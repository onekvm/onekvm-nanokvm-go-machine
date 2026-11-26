// SPDX-License-Identifier: MIT
#define _POSIX_C_SOURCE 200809L
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#ifdef ONEKVM_SPLASH_GO
/* NanoKVM Go: landscape canvas on a 240x284 ST7789P3 framebuffer. */
enum { WIDTH = 284, HEIGHT = 240, PANEL_WIDTH = 240, PANEL_HEIGHT = 284 };
#define PANEL_FBTFT_ID "fb_st7789p3"
#else
/* Pro Desk: landscape canvas on a 172x320 JD9853 framebuffer. */
enum { WIDTH = 320, HEIGHT = 172, PANEL_WIDTH = 172, PANEL_HEIGHT = 320 };
#define PANEL_FBTFT_ID "fb_jd9853"
#endif
/* Layouts are drawn for 172 rows and centered vertically on taller canvases. */
enum { LAYOUT_HEIGHT = 172, TOP_PAD = (HEIGHT - LAYOUT_HEIGHT) / 2 };
enum { BAR_HEIGHT = 4, INSTALL_BAR_WIDTH = 232, INSTALL_BAR_X = (WIDTH - INSTALL_BAR_WIDTH) / 2,
       INSTALL_BAR_Y = TOP_PAD + 130 };
enum { BOOT_BAR_WIDTH = 160, BOOT_BAR_X = (WIDTH - BOOT_BAR_WIDTH) / 2, BOOT_BAR_Y = TOP_PAD + 150 };

struct splash_asset {
    unsigned width, height;
    const uint16_t *runs;
    size_t words;
};

struct splash_labels {
    const struct splash_asset *starting, *installing, *warning, *paused, *error_detail, *saved_detail;
    const struct splash_asset *stages[7];
};

#include "assets.h"
#include "log-font.h"

static const struct splash_labels *language_labels(const char *language)
{
    if (!strcmp(language, "zh") || !strcmp(language, "zh-CN"))
        return &labels_zh;
    if (!strcmp(language, "zh_tw") || !strcmp(language, "zh-TW"))
        return &labels_zh_tw;
    return &labels_en;
}

static uint16_t rgb565(unsigned r, unsigned g, unsigned b)
{
    return (uint16_t)(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3));
}

static void draw_asset(uint16_t *frame, const struct splash_asset *asset, unsigned y)
{
    size_t pixel = 0;
    /* Wider artwork is clipped at the right edge instead of wrapping. */
    unsigned left = asset->width < WIDTH ? (WIDTH - asset->width) / 2 : 0;

    for (size_t i = 0; i + 1 < asset->words; i += 2) {
        for (unsigned n = 0; n < asset->runs[i]; n++, pixel++) {
            unsigned row = (unsigned)(pixel / asset->width) + y;
            unsigned col = (unsigned)(pixel % asset->width) + left;
            if (row < HEIGHT && col < WIDTH && pixel < asset->width * asset->height)
                frame[row * WIDTH + col] = asset->runs[i + 1];
        }
    }
}

static uint16_t background(void) { return rgb565(8, 11, 17); }
static uint16_t bar_track(void) { return rgb565(31, 41, 59); }
static uint16_t bar_fill(void) { return rgb565(22, 119, 255); }

static void draw_bar(uint16_t *frame, unsigned left, unsigned top, unsigned width, uint16_t color)
{
    for (unsigned y = 0; y < BAR_HEIGHT; y++) {
        unsigned inset = (y == 0 || y == BAR_HEIGHT - 1) ? 2 : 0;
        for (unsigned x = inset; x + inset < width; x++)
            frame[(top + y) * WIDTH + left + x] = color;
    }
}

/* Pixel x of boot bar row y when `fill` pixels are filled; ends are rounded. */
static uint16_t boot_bar_pixel(unsigned x, unsigned y, unsigned fill)
{
    unsigned inset = (y == 0 || y == BAR_HEIGHT - 1) ? 2 : 0;
    if (x >= inset && x + inset < fill)
        return bar_fill();
    if (x >= inset && x + inset < BOOT_BAR_WIDTH)
        return bar_track();
    return background();
}

static unsigned percent_width(unsigned percent)
{
    return BOOT_BAR_WIDTH * percent / 100;
}

static void draw_boot_bar(uint16_t *frame, unsigned percent)
{
    for (unsigned y = 0; y < BAR_HEIGHT; y++)
        for (unsigned x = 0; x < BOOT_BAR_WIDTH; x++)
            frame[(BOOT_BAR_Y + y) * WIDTH + BOOT_BAR_X + x] =
                boot_bar_pixel(x, y, percent_width(percent));
}

/* `row` is the bar's middle row, indexed by screen x. Only a boot screen drawn
 * by this program carries the bar. */
static int boot_bar_row_valid(const uint16_t *row)
{
    uint16_t first = row[BOOT_BAR_X], last = row[BOOT_BAR_X + BOOT_BAR_WIDTH - 1];
    return row[BOOT_BAR_X - 1] == background() &&
        row[BOOT_BAR_X + BOOT_BAR_WIDTH] == background() &&
        (first == bar_track() || first == bar_fill()) &&
        (last == bar_track() || last == bar_fill());
}

static unsigned boot_bar_row_fill(const uint16_t *row)
{
    unsigned fill = 0;
    while (fill < BOOT_BAR_WIDTH && row[BOOT_BAR_X + fill] == bar_fill())
        fill++;
    return fill;
}

static int has_boot_bar(const uint16_t *frame)
{
    return boot_bar_row_valid(frame + (BOOT_BAR_Y + 1) * WIDTH);
}

/*
 * Named checkpoints draw the bar; the screen service's first frame replaces
 * it. Between checkpoints `animate` moves steadily toward the next one over
 * its usual duration, then slows and never reaches it. Percentages follow a
 * normal boot from the U-Boot logo (0%) to the first screen-service frame.
 */
static const struct {
    const char *name;
    unsigned char percent;
    unsigned short next_ms;
} boot_stages[] = {
#ifdef ONEKVM_SPLASH_GO
    /*
     * U-Boot fills 2% and 4% while loading the slot (onekvm_go.c), then the
     * initramfs draws `initramfs`, starts the animator and marks `storage`
     * before switch_root; root-system units mark early, sysinit and basic, and
     * the screen service marks screen when it opens the panel.
     * Measured over three cold boots of a NanoKVM Go from an SD slot: Linux
     * starts 4.0 s after the logo and the first screen frame follows 15.7 s
     * later. Percentages share that span evenly above U-Boot's 4%; each
     * duration is the mean time to the next checkpoint.
     */
    { "initramfs", 17, 2200 }, { "storage", 30, 2300 }, { "early", 45, 3400 },
    { "sysinit", 65, 2200 }, { "basic", 79, 2000 }, { "screen", 91, 1500 },
    { "done", 100, 0 },
#else
    /* Firstboot marks storage; the root system and screen service the rest. */
    { "storage", 10, 4400 }, { "early", 37, 3100 }, { "sysinit", 56, 1500 },
    { "basic", 65, 2100 }, { "screen", 78, 4000 }, { "done", 100, 0 },
#endif
};
enum { BOOT_STAGES = sizeof(boot_stages) / sizeof(boot_stages[0]) };

/* Index of the checkpoint the bar has reached, or -1 before the first. */
static int boot_stage(unsigned fill)
{
    int stage = -1;
    for (int i = 0; i < BOOT_STAGES; i++)
        if (fill >= percent_width(boot_stages[i].percent))
            stage = i;
    return stage;
}

/* Fill width `elapsed_ms` after reaching checkpoint `stage`: 90% of the way at
 * the usual time, then ever slower, never past 98%. */
static unsigned boot_stage_target(int stage, unsigned long elapsed_ms)
{
    unsigned base = percent_width(boot_stages[stage].percent);
    unsigned span = percent_width(boot_stages[stage + 1].percent) - base;
    unsigned long long usual = boot_stages[stage].next_ms, permille;

    if (elapsed_ms <= usual) {
        permille = 900 * elapsed_ms / usual;
    } else {
        unsigned long long late = elapsed_ms - usual;
        permille = 900 + 80 * late / (late + usual);
    }
    return base + (unsigned)(span * permille / 1000);
}

static void draw_log_message(uint16_t *frame, const char *message)
{
    enum { LEFT = 12, TOP = TOP_PAD + 6, COLUMNS = (WIDTH - 2 * LEFT) / LOG_FONT_WIDTH, ROWS = 3 };
    char text[COLUMNS * ROWS];
    size_t length = 0, offset = 0;
    while (message[length] && length < sizeof(text)) {
        unsigned char c = (unsigned char)message[length];
        text[length++] = c >= 32 && c <= 126 ? (char)c : (c < 32 ? ' ' : '?');
    }
    for (unsigned row = 0; row < ROWS && offset < length; row++) {
        size_t count = length - offset;
        int shortened = row == ROWS - 1 && (count > COLUMNS || message[length]);
        if (count > COLUMNS)
            count = COLUMNS;
        if (row < ROWS - 1 && offset + count < length) {
            size_t word_end = count;
            while (word_end && text[offset + word_end] != ' ')
                word_end--;
            if (word_end)
                count = word_end;
        }
        for (size_t col = 0; col < count; col++) {
            unsigned char c = shortened && col + 3 >= count ? '.' : (unsigned char)text[offset + col];
            for (unsigned y = 0; y < LOG_FONT_HEIGHT; y++) {
                for (unsigned x = 0; x < LOG_FONT_WIDTH; x++) {
                    if (log_font[c - 32][y] & (1u << x))
                        frame[(TOP + row * (LOG_FONT_HEIGHT + 1) + y) * WIDTH +
                              LEFT + col * LOG_FONT_WIDTH + x] = rgb565(234, 238, 245);
                }
            }
        }
        offset += count;
        while (offset < length && text[offset] == ' ')
            offset++;
    }
}

/* Storage stages advance the install bar; it is not a time estimate. */
static void render(uint16_t *frame, unsigned stage, int failed, unsigned progress,
                   const struct splash_labels *labels, const char *message)
{
    for (unsigned i = 0; i < WIDTH * HEIGHT; i++)
        frame[i] = background();
    if (failed < 0) {
        /* Recovery: its instructions above the logo, without a progress bar. */
        draw_log_message(frame, message && *message ? message : "Recovery");
        draw_asset(frame, &logo_install, TOP_PAD + 51);
    } else if (failed) {
        int has_message = message && *message;
        unsigned shift = TOP_PAD + (has_message ? 28 : 0);
        if (has_message)
            draw_log_message(frame, message);
        draw_asset(frame, &logo_install, 23 + shift);
        draw_asset(frame, labels->paused, 86 + shift);
        draw_asset(frame, failed > 1 ? labels->saved_detail : labels->error_detail, 114 + shift);
    } else if (stage) {
        draw_asset(frame, &logo_install, TOP_PAD + 23);
        draw_asset(frame, labels->installing, TOP_PAD + 84);
        draw_asset(frame, labels->stages[stage - 1], TOP_PAD + 108);
        draw_bar(frame, INSTALL_BAR_X, INSTALL_BAR_Y, INSTALL_BAR_WIDTH, bar_track());
        draw_bar(frame, INSTALL_BAR_X, INSTALL_BAR_Y, INSTALL_BAR_WIDTH * stage / 7, bar_fill());
        draw_asset(frame, labels->warning, TOP_PAD + 148);
    } else {
        draw_asset(frame, &logo_boot, TOP_PAD + 48);
        draw_asset(frame, labels->starting, TOP_PAD + 125);
        draw_boot_bar(frame, progress);
    }
}

static int valid_panel(const struct fb_var_screeninfo *var,
                       const struct fb_fix_screeninfo *fix)
{
    return var->xres == PANEL_WIDTH && var->yres == PANEL_HEIGHT &&
        var->xres_virtual >= PANEL_WIDTH && var->yres_virtual >= PANEL_HEIGHT &&
        var->xoffset == 0 && var->yoffset == 0 && var->bits_per_pixel == 16 &&
        var->red.offset == 11 && var->red.length == 5 && !var->red.msb_right &&
        var->green.offset == 5 && var->green.length == 6 && !var->green.msb_right &&
        var->blue.offset == 0 && var->blue.length == 5 && !var->blue.msb_right &&
        /* fbtft sets nonstd=1 even though these bitfields describe RGB565. */
        var->transp.length == 0 && !var->grayscale &&
        (!var->nonstd || (var->nonstd == 1 &&
         !strncmp(fix->id, PANEL_FBTFT_ID, sizeof(fix->id)))) &&
        fix->type == FB_TYPE_PACKED_PIXELS && fix->visual == FB_VISUAL_TRUECOLOR &&
        fix->line_length >= PANEL_WIDTH * 2 && fix->line_length <= 4096 &&
        fix->smem_len >= fix->line_length * PANEL_HEIGHT;
}

/*
 * Byte offset of canvas pixel (x, y) in the native framebuffer. Either way a
 * canvas column is one native row, so a bar column is one short run there.
 */
static size_t panel_offset(unsigned x, unsigned y, size_t stride)
{
#ifdef ONEKVM_SPLASH_GO
    /* Go panel is mounted 90 degrees from the framebuffer's native orientation. */
    return (size_t)x * stride + (size_t)(PANEL_WIDTH - 1 - y) * 2;
#else
    /* Desk panel is mounted 270 degrees from the framebuffer's native orientation. */
    return (size_t)(PANEL_HEIGHT - 1 - x) * stride + (size_t)y * 2;
#endif
}

static void put_pixel(uint8_t *pixels, size_t offset, uint16_t color)
{
    pixels[offset] = (uint8_t)color;
    pixels[offset + 1] = (uint8_t)(color >> 8);
}

static uint16_t get_pixel(const uint8_t *pixels, size_t offset)
{
    return (uint16_t)(pixels[offset] | pixels[offset + 1] << 8);
}

static void rotate_panel(uint8_t *pixels, size_t stride, const uint16_t *frame)
{
    for (unsigned y = 0; y < HEIGHT; y++)
        for (unsigned x = 0; x < WIDTH; x++)
            put_pixel(pixels, panel_offset(x, y, stride), frame[y * WIDTH + x]);
}

static void unrotate_panel(uint16_t *frame, const uint8_t *pixels, size_t stride)
{
    for (unsigned y = 0; y < HEIGHT; y++)
        for (unsigned x = 0; x < WIDTH; x++)
            frame[y * WIDTH + x] = get_pixel(pixels, panel_offset(x, y, stride));
}

/* Native panel rows holding canvas columns [left, right). */
static size_t column_band(unsigned left, unsigned right, size_t stride, size_t *size)
{
    size_t a = panel_offset(left, 0, stride) / stride;
    size_t b = panel_offset(right - 1, 0, stride) / stride;
    size_t first = a < b ? a : b;

    *size = (size_t)(right - left) * stride;
    return first * stride;
}

/* Bar columns map to a contiguous band of native panel rows. */
static size_t boot_bar_band(size_t stride, size_t *size)
{
    return column_band(BOOT_BAR_X, BOOT_BAR_X + BOOT_BAR_WIDTH, stride, size);
}

/* Returns 0 when the panel shows no boot bar or the bar is already further. */
static int update_boot_bar(uint8_t *pixels, size_t stride, uint16_t *frame, unsigned progress)
{
    unrotate_panel(frame, pixels, stride);
    if (!has_boot_bar(frame) ||
        percent_width(progress) <= boot_bar_row_fill(frame + (BOOT_BAR_Y + 1) * WIDTH))
        return 0;
    draw_boot_bar(frame, progress);
    rotate_panel(pixels, stride, frame);
    return 1;
}

/* Rebuild the bar's middle row (screen x) from the native panel. */
static void panel_bar_row(uint16_t *row, const uint8_t *panel, size_t stride)
{
    for (unsigned x = BOOT_BAR_X - 1; x <= BOOT_BAR_X + BOOT_BAR_WIDTH; x++)
        row[x] = get_pixel(panel, panel_offset(x, BOOT_BAR_Y + 1, stride));
}

/* Paint bar column x on the native panel for a bar `fill` pixels wide. */
static void paint_bar_column(uint8_t *panel, size_t stride, unsigned x, unsigned fill)
{
    for (unsigned y = 0; y < BAR_HEIGHT; y++)
        put_pixel(panel, panel_offset(BOOT_BAR_X + x, BOOT_BAR_Y + y, stride),
                  boot_bar_pixel(x, y, fill));
}

static int open_panel(const char *path, struct fb_fix_screeninfo *fix)
{
    struct fb_var_screeninfo var;
    int fd = open(path, O_RDWR | O_CLOEXEC);

    if (fd < 0)
        return -1;
    if (ioctl(fd, FBIOGET_VSCREENINFO, &var) < 0 ||
        ioctl(fd, FBIOGET_FSCREENINFO, fix) < 0)
        goto failed;
    if (!valid_panel(&var, fix)) {
        errno = ENOTSUP;
        goto failed;
    }
    return fd;
failed:
    {
        int saved = errno;
        close(fd);
        errno = saved;
    }
    return -1;
}

/* Write a whole frame; the panel driver resends the full panel afterwards. */
static int write_panel(int fd, const uint8_t *pixels, size_t size)
{
    size_t done = 0;
    while (done < size) {
        ssize_t n = pwrite(fd, pixels + done, size - done, (off_t)done);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0) {
            if (!n)
                errno = EIO;
            return -1;
        }
        done += (size_t)n;
    }
    return 0;
}

/*
 * Bar updates go through a shared mapping: the panel driver then sends only the
 * touched pages, while write() always resends the whole panel over SPI.
 */
static uint8_t *map_panel(int fd, size_t stride)
{
    void *map = mmap(NULL, stride * PANEL_HEIGHT, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    return map == MAP_FAILED ? NULL : map;
}

/* Copy the rows of [offset, offset + size) that differ from the panel. */
static void copy_changed_rows(uint8_t *panel, const uint8_t *pixels, size_t offset, size_t size,
                              size_t stride)
{
    for (size_t at = offset; at < offset + size; at += stride)
        if (memcmp(panel + at, pixels + at, stride))
            memcpy(panel + at, pixels + at, stride);
}

/* Bar writers serialize their read-modify-write cycles on the framebuffer. */
static int lock_panel(int fd)
{
    struct flock lock = { .l_type = F_WRLCK, .l_whence = SEEK_SET };
    while (fcntl(fd, F_SETLKW, &lock) < 0)
        if (errno != EINTR)
            return -1;
    return 0;
}

/* progress < 0 draws the whole frame; otherwise only an existing boot bar changes. */
static int show_panel(const char *path, uint16_t *frame, int progress)
{
    struct fb_fix_screeninfo fix;
    uint8_t *pixels;
    size_t size;
    int result = -1;
    int fd = open_panel(path, &fix);

    if (fd < 0)
        return -1;
    size = (size_t)fix.line_length * PANEL_HEIGHT;
    pixels = calloc(1, size);
    if (!pixels)
        goto close_panel;
    if (progress < 0) {
        rotate_panel(pixels, fix.line_length, frame);
        /* The lock keeps the animator from painting over this frame. */
        if (lock_panel(fd) < 0 || write_panel(fd, pixels, size) < 0)
            goto free_pixels;
        /* Leave the last frame visible; the normal screen service takes over. */
        (void)ioctl(fd, FBIOBLANK, FB_BLANK_UNBLANK);
    } else {
        size_t band, first = boot_bar_band(fix.line_length, &band);
        uint8_t *panel = map_panel(fd, fix.line_length);
        if (!panel)
            goto free_pixels;
        if (lock_panel(fd) < 0) {
            munmap(panel, size);
            goto free_pixels;
        }
        memcpy(pixels, panel, size);
        if (update_boot_bar(pixels, fix.line_length, frame, (unsigned)progress))
            copy_changed_rows(panel, pixels, first, band, fix.line_length);
        munmap(panel, size);
    }
    result = 0;
free_pixels:
    free(pixels);
close_panel:
    {
        int saved = errno;
        close(fd);
        errno = saved;
    }
    return result;
}

static int write_preview(const char *path, const uint16_t *frame)
{
    FILE *file = fopen(path, "wb");
    if (!file)
        return -1;
    if (fprintf(file, "P6\n%d %d\n255\n", WIDTH, HEIGHT) < 0)
        goto failed;
    for (unsigned i = 0; i < WIDTH * HEIGHT; i++) {
        unsigned r = (frame[i] >> 11) & 31, g = (frame[i] >> 5) & 63;
        unsigned b = frame[i] & 31;
        uint8_t rgb[] = {
            (uint8_t)((r << 3) | (r >> 2)), (uint8_t)((g << 2) | (g >> 4)),
            (uint8_t)((b << 3) | (b >> 2)),
        };
        if (fwrite(rgb, 1, sizeof(rgb), file) != sizeof(rgb))
            goto failed;
    }
    return fclose(file);
failed:
    fclose(file);
    return -1;
}

static volatile sig_atomic_t stop_animation;

static void request_stop(int signal)
{
    (void)signal;
    stop_animation = 1;
}

static unsigned long monotonic_ms(void)
{
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (unsigned long)now.tv_sec * 1000 + (unsigned long)now.tv_nsec / 1000000;
}

/* Paint columns [from, fill) of a bar `fill` pixels wide on the native panel. */
static void paint_bar(uint8_t *panel, size_t stride, unsigned from, unsigned fill)
{
    for (unsigned x = from; x < fill; x++)
        paint_bar_column(panel, stride, x, fill);
}

/* Keep lengthening the bar on `fd` (a native panel with `stride`) until told to stop. */
static int animate_fd(int fd, size_t stride, unsigned long lifetime_ms)
{
    enum { TICK_MS = 80 };
    uint16_t row[WIDTH];
    uint8_t *panel = map_panel(fd, stride);
    unsigned long started = monotonic_ms(), stage_started = started;
    int stage = -2;

    if (!panel)
        return 1;
    while (!stop_animation && monotonic_ms() - started < lifetime_ms) {
        struct timespec tick = { 0, TICK_MS * 1000000L };
        struct flock unlock = { .l_type = F_UNLCK, .l_whence = SEEK_SET };
        unsigned fill, target;
        int current;

        if (lock_panel(fd) < 0)
            break;
        panel_bar_row(row, panel, stride);
        if (!boot_bar_row_valid(row))
            break;
        fill = boot_bar_row_fill(row);
        current = boot_stage(fill);
        if (current != stage) {
            stage = current;
            stage_started = monotonic_ms();
        }
        if (stage >= BOOT_STAGES - 1)
            break;
        target = stage < 0 ? fill : boot_stage_target(stage, monotonic_ms() - stage_started);
        /* Repaint the old rounded end too, then the newly filled columns. */
        if (target > fill)
            paint_bar(panel, stride, fill > 2 ? fill - 2 : 0, target);
        (void)fcntl(fd, F_SETLK, &unlock);
        nanosleep(&tick, NULL);
    }
    munmap(panel, stride * PANEL_HEIGHT);
    return 0;
}

/*
 * Started by the initramfs once its boot screen is up; it survives switch_root
 * and keeps the bar moving until `finish`, until the bar disappears, or for at
 * most two minutes. It only ever lengthens the fill.
 */
static int animate(const char *device)
{
    struct fb_fix_screeninfo fix;
    struct sigaction action = { .sa_handler = request_stop };
    int fd, result;

    (void)setsid();
    signal(SIGHUP, SIG_IGN);
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGINT, &action, NULL);
    fd = open_panel(device, &fix);
    if (fd < 0)
        return 1;
    result = animate_fd(fd, fix.line_length, 120000);
    close(fd);
    return result;
}

static int animation_process(pid_t pid)
{
    char path[64], args[256];
    ssize_t length;
    int fd;
    const char *name;

    snprintf(path, sizeof(path), "/proc/%d/cmdline", (int)pid);
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return 0;
    length = read(fd, args, sizeof(args) - 1);
    close(fd);
    if (length <= 0)
        return 0;
    args[length] = '\0';
    name = strrchr(args, '/') ? strrchr(args, '/') + 1 : args;
    if (strcmp(name, "onekvm-boot-splash"))
        return 0;
    for (ssize_t i = (ssize_t)strlen(args) + 1; i < length; i += (ssize_t)strlen(args + i) + 1)
        if (!strcmp(args + i, "animate"))
            return 1;
    return 0;
}

static int process_running(pid_t pid)
{
    char path[64], stat[128];
    ssize_t length;
    const char *state;
    int fd;

    snprintf(path, sizeof(path), "/proc/%d/stat", (int)pid);
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return 0;
    length = read(fd, stat, sizeof(stat) - 1);
    close(fd);
    if (length <= 0)
        return 0;
    stat[length] = '\0';
    state = strrchr(stat, ')');
    return state && state[1] == ' ' && state[2] != 'Z' && state[2] != 'X';
}

/* Stop `animate` before the caller draws its own first frame. */
static int finish(void)
{
    enum { MAX = 8 };
    pid_t pids[MAX];
    unsigned count = 0;
    struct dirent *entry;
    DIR *proc = opendir("/proc");

    if (!proc)
        return 1;
    while ((entry = readdir(proc)) && count < MAX) {
        char *end;
        long pid = strtol(entry->d_name, &end, 10);
        if (*end || pid <= 0 || pid == (long)getpid() || !animation_process((pid_t)pid))
            continue;
        if (!kill((pid_t)pid, SIGTERM))
            pids[count++] = (pid_t)pid;
    }
    closedir(proc);
    /* Wait up to half a second so no bar write can follow the caller's frame. */
    for (unsigned tries = 0; count && tries < 50; tries++) {
        struct timespec pause = { 0, 10 * 1000000L };
        unsigned running = 0;
        for (unsigned i = 0; i < count; i++)
            if (process_running(pids[i]))
                pids[running++] = pids[i];
        count = running;
        if (count)
            nanosleep(&pause, NULL);
    }
    return count ? 1 : 0;
}

/* A percentage, or the name of a boot checkpoint. */
static int parse_percent(const char *text)
{
    int value = 0;
    for (int i = 0; i < BOOT_STAGES; i++)
        if (!strcmp(text, boot_stages[i].name))
            return boot_stages[i].percent;
    if (!*text || strlen(text) > 3)
        return -1;
    for (; *text; text++) {
        if (*text < '0' || *text > '9')
            return -1;
        value = value * 10 + (*text - '0');
    }
    return value <= 100 ? value : -1;
}

int main(int argc, char **argv)
{
    uint16_t frame[WIDTH * HEIGHT];
    const char *preview = NULL, *device = "/dev/fb0", *message = NULL;
    unsigned stage = 0;
    int failed = 0, progress = 0, bar_only = 0;
    const struct splash_labels *labels = &labels_en;

    while (argc >= 3) {
        if (!strcmp(argv[1], "--language"))
            labels = language_labels(argv[2]);
        else if (!strcmp(argv[1], "--message"))
            message = argv[2];
        else if (!strcmp(argv[1], "--output"))
            preview = argv[2];
        else if (!strcmp(argv[1], "--framebuffer"))
            device = argv[2];
        else if (!strcmp(argv[1], "--progress") && (progress = parse_percent(argv[2])) >= 0)
            ;
        else
            break;
        argc -= 2;
        argv += 2;
    }
    if (argc == 2 && !strcmp(argv[1], "animate") && !preview)
        return animate(device);
    if (argc == 2 && !strcmp(argv[1], "finish"))
        return finish();
    if (argc == 2 && !strcmp(argv[1], "boot")) {
        stage = 0;
    } else if (argc == 2 && !strcmp(argv[1], "error")) {
        failed = 1;
    } else if (argc == 2 && !strcmp(argv[1], "error-saved")) {
        failed = 2;
    } else if (argc == 2 && !strcmp(argv[1], "recovery")) {
        failed = -1;
    } else if (argc == 3 && !strcmp(argv[1], "progress") && !preview &&
               (progress = parse_percent(argv[2])) >= 0) {
        bar_only = 1;
    } else if (argc == 3 && !strcmp(argv[1], "install") &&
               strlen(argv[2]) == 1 && argv[2][0] >= '1' && argv[2][0] <= '7') {
        stage = (unsigned)(argv[2][0] - '0');
    } else {
        fprintf(stderr, "Usage: onekvm-boot-splash [--language en|zh|zh_tw] [--message TEXT] [--progress PERCENT] "
                "[--output FILE.ppm | --framebuffer /dev/fb0] {boot | install STAGE(1-7) | error | error-saved | recovery}\n"
                "       onekvm-boot-splash [--framebuffer /dev/fb0] {progress PERCENT|CHECKPOINT | animate}\n"
                "       onekvm-boot-splash finish\n");
        return 2;
    }
    if (!bar_only)
        render(frame, stage, failed, (unsigned)progress, labels, message);
    if ((preview ? write_preview(preview, frame) : show_panel(device, frame, bar_only ? progress : -1)) < 0) {
        perror("onekvm-boot-splash");
        return 1;
    }
    return 0;
}
