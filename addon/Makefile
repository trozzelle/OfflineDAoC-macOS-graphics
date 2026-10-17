# Builds daoc_gfx/daoc-world.addon32, the 32-bit ReShade add-on (see LAYOUT.md for the ABI it hard-codes).
# Needs mingw-w64 (brew install mingw-w64). The built add-on is committed, so daoc-gfx users need no compiler;
# rebuild and commit it after changing any file in SOURCES. The add-on embeds SOURCE_HASH, the sha256 of SOURCES in
# this order, and the tests check it against the files. `make test` builds and runs the rules' host tests with cc.

CC = i686-w64-mingw32-gcc
CC_HOST = cc
SOURCES = world_core.h world_core.c reshade_abi.h daoc_world.c Makefile
OUT = ../daoc_gfx/daoc-world.addon32
SOURCE_HASH := $(shell cat $(SOURCES) | shasum -a 256 | cut -d' ' -f1)

CFLAGS_HOST = -O2 -std=c11 -Wall -Wextra -Werror
CFLAGS = -O2 -std=gnu11 -Wall -Wextra -Wno-unused-parameter -Wno-cast-function-type \
         -DSOURCE_HASH='"$(SOURCE_HASH)"'
LDFLAGS = -shared -s -static-libgcc -Wl,--no-insert-timestamp -Wl,--build-id=none

$(OUT): $(SOURCES)
	$(CC) $(CFLAGS) -o $@ daoc_world.c world_core.c $(LDFLAGS)

.PHONY: test clean
test: test_world
	./test_world

test_world: test_world.c world_core.c world_core.h
	$(CC_HOST) $(CFLAGS_HOST) -o $@ test_world.c world_core.c

clean:
	rm -f $(OUT) test_world
