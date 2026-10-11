# Builds daoc_gfx/d3d9.dll, the 32-bit proxy the `anisotropy` patch installs.
# Needs mingw-w64 (brew install mingw-w64). The built DLL is committed, so
# daoc-gfx users need no compiler; rebuild and commit it after changing any
# file in SOURCES. The DLL embeds SOURCE_HASH, the sha256 of SOURCES in this
# order, and the tests check it against the files.

CC = i686-w64-mingw32-gcc
SOURCES = sampler.h sampler.c d3d9_proxy.c d3d9.def Makefile
OUT = ../daoc_gfx/d3d9.dll
SOURCE_HASH := $(shell cat $(SOURCES) | shasum -a 256 | cut -d' ' -f1)

CFLAGS = -O2 -std=gnu11 -Wall -Wextra -Wno-unused-parameter -Wno-cast-function-type \
         -DSOURCE_HASH='"$(SOURCE_HASH)"'
LDFLAGS = -shared -s -static-libgcc -Wl,--no-insert-timestamp -Wl,--enable-stdcall-fixup \
          -Wl,--build-id=none

$(OUT): $(SOURCES)
	$(CC) $(CFLAGS) -o $@ d3d9_proxy.c sampler.c d3d9.def $(LDFLAGS)

.PHONY: clean
clean:
	rm -f $(OUT)
