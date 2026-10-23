# orbis-gpu-probe — builds with any C11 compiler; needs only Vulkan headers.
# Jaguar-safe: -march=x86-64 baseline (override ARCHFLAGS for btver2 tuning).
CC        ?= cc
ARCHFLAGS ?= -march=x86-64 -mtune=btver2
CFLAGS    ?= -O2 -g -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers
VK_INCLUDE ?= /usr/include
PREFIX    ?= /usr
LIBEXEC   ?= $(PREFIX)/lib/orbis

orbis-gpu-probe: orbis-gpu-probe.c shaders_spv.h
	$(CC) $(ARCHFLAGS) $(CFLAGS) -std=gnu11 -I$(VK_INCLUDE) -o $@ $< -ldl

# The generated header is committed; regenerate explicitly (needs glslc):
shaders:
	python3 gen_spv_header.py

check-shaders:
	python3 gen_spv_header.py --check

install: orbis-gpu-probe
	install -Dm0755 orbis-gpu-probe $(DESTDIR)$(LIBEXEC)/orbis-gpu-probe

clean:
	rm -f orbis-gpu-probe

.PHONY: install clean shaders check-shaders
