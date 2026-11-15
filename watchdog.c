// SPDX-License-Identifier: MIT
/* Small initramfs feeder, bounded boot grace and explicit hardware reset. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/watchdog.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define RUN_DIR "/run/onekvm"
#define PID_FILE "boot-watchdog.pid"
#define BOOT_TIMEOUT_SEC 60
#define RESET_TIMEOUT_SEC 5
#define BOOT_GRACE_SEC 240
#define RELEASE_TIMEOUT_MS 5000

static int arm(int seconds)
{
    int fd = open("/dev/watchdog0", O_WRONLY | O_CLOEXEC);
    if (fd < 0) { perror("watchdog open"); return -1; }
    if (ioctl(fd, WDIOC_SETTIMEOUT, &seconds) ||
        ioctl(fd, WDIOC_KEEPALIVE, 0)) {
        perror("watchdog arm");
        return -1;
    }
    fprintf(stderr, "OneKVM Go: hardware watchdog armed (%d s)\n", seconds);
    return fd;
}

static int publish_pid(int run, pid_t pid)
{
    int fd = openat(run, PID_FILE, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) return -1;
    FILE *file = fdopen(fd, "w");
    if (!file) { close(fd); return -1; }
    int failed = fprintf(file, "%ld\n", (long)pid) < 0;
    return fclose(file) || failed ? -1 : 0;
}

/*
 * Arm, then return once the feeder is forked and its PID published, so init
 * needs no wait. The feeder keeps running across switch_root: it holds /run
 * as a directory, which init moves below the real root.
 */
static int boot(void)
{
    sigset_t release;
    sigemptyset(&release);
    sigaddset(&release, SIGUSR2);
    /* Blocked before the PID is published: --release may signal at once. */
    if (sigprocmask(SIG_BLOCK, &release, NULL)) { perror("watchdog signals"); return 1; }
    int run = open(RUN_DIR, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (run < 0) { perror("watchdog run directory"); return 1; }
    int fd = arm(BOOT_TIMEOUT_SEC);
    if (fd < 0) return 1;
    pid_t feeder = fork();
    if (feeder < 0) { perror("watchdog fork"); return 1; }
    if (feeder > 0) {
        if (publish_pid(run, feeder) == 0) return 0;
        perror("watchdog pid");
        kill(feeder, SIGKILL);
        return 1;
    }
    struct timespec start, now, step = { 2, 0 };
    clock_gettime(CLOCK_MONOTONIC, &start);
    for (;;) {
        clock_gettime(CLOCK_MONOTONIC, &now);
        if (now.tv_sec - start.tv_sec >= BOOT_GRACE_SEC) {
            /* The PID file stays: --release then refuses the takeover. */
            fprintf(stderr, "OneKVM Go: boot grace expired; hardware reset pending\n");
            close(fd);
            return 1;
        }
        if (ioctl(fd, WDIOC_KEEPALIVE, 0)) { perror("watchdog feed"); return 1; }
        if (sigtimedwait(&release, NULL, &step) == SIGUSR2) {
            /* No 'V': keep hardware armed while the runtime owner opens it. */
            close(fd);
            unlinkat(run, PID_FILE, 0);
            return 0;
        }
    }
}

/* Stop the boot feeder and return once it has exited and closed the timer. */
static int release(void)
{
    char line[32], *end;
    FILE *file = fopen(RUN_DIR "/" PID_FILE, "re");
    if (!file) {
        if (errno == ENOENT) return 0;
        perror("boot watchdog pid");
        return 1;
    }
    int got = fgets(line, sizeof(line), file) != NULL;
    fclose(file);
    long pid = got ? strtol(line, &end, 10) : 0;
    if (pid <= 1 || *end != '\n') {
        fprintf(stderr, "invalid boot watchdog pid\n");
        return 1;
    }
    /* A pidfd names this exact process and becomes readable when it exits. */
    int feeder = syscall(SYS_pidfd_open, (pid_t)pid, 0);
    if (feeder < 0 || syscall(SYS_pidfd_send_signal, feeder, SIGUSR2, NULL, 0)) {
        perror("boot watchdog feeder");
        return 1;
    }
    struct pollfd exited = { .fd = feeder, .events = POLLIN };
    int ready;
    do ready = poll(&exited, 1, RELEASE_TIMEOUT_MS);
    while (ready < 0 && errno == EINTR);
    /* A feeder that exited after its grace, or was killed, kept the PID file. */
    if (ready == 1 && access(RUN_DIR "/" PID_FILE, F_OK) && errno == ENOENT) return 0;
    fprintf(stderr, "Go boot watchdog did not release ownership\n");
    return 1;
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--boot")) return boot();
    if (argc == 2 && !strcmp(argv[1], "--release")) return release();
    if (argc != 2 || strcmp(argv[1], "--reset")) {
        fprintf(stderr, "usage: %s --boot|--release|--reset\n", argv[0]);
        return 2;
    }
    if (arm(RESET_TIMEOUT_SEC) < 0) return 1;
    /* Keep the descriptor open and never feed it again. No reboot syscall. */
    /* Flush writable SD/NFS data after arming; a hung sync is bounded by WDT. */
    sync();
    for (;;) pause();
}
