CC ?= cc
CFLAGS ?= -O2
PYTHON ?= python3

.PHONY: all clean test test-boot-inputs test-boot-splash

# OE recipes install files directly; their default configure runs `make clean`.
all:

clean:

test: test-boot-inputs test-boot-splash

test-boot-inputs:
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py'

test-boot-splash:
	@work=$$(mktemp -d); trap 'rm -rf "$$work"' EXIT; \
	$(CC) $(CPPFLAGS) $(CFLAGS) -std=c11 -Wall -Wextra -Werror -DONEKVM_SPLASH_GO \
	    tests/boot-splash-test.c $(LDFLAGS) -o "$$work/test-go" && "$$work/test-go"
