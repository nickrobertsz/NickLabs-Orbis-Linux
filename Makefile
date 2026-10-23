# NickLabs PS4 Orbis Linux — top-level build entry points. See docs/BUILD-AND-DEPLOY.md.
SNAPSHOT ?= 2026/10/20
TARGET   ?= aeolia-belize-6.15
ENGINE   ?= docker
VERSION  := $(shell python3 -c "import sys; sys.path.insert(0,'platform'); import orbis; print(orbis.__version__)")

.PHONY: test probe kernel container packages rootfs initramfs image sbom dist clean container-lock

test:                     ## unit + integration tests (any Linux, python3 >= 3.11, gcc)
	python3 -m unittest discover -s tests/unit -t tests/unit
	python3 -m unittest discover -s tests/integration -t tests/integration

probe:                    ## build orbis-gpu-probe (needs Vulkan headers)
	$(MAKE) -C tools/gpu-probe

kernel:                   ## PS4 kernel (bzImage + modules + manifest) into out/kernel/$(TARGET)
	kernel/build-kernel.sh $(TARGET)

container:
	$(ENGINE) build --build-arg SNAPSHOT=$(SNAPSHOT) -t orbis-build:$(VERSION) -f packaging/Containerfile packaging

container-lock:
	$(ENGINE) image inspect --format '{{index .RepoDigests 0}}' docker.io/archlinux/archlinux:base-devel > packaging/container.lock

packages: container       ## all Orbis packages into out/repo (Arch container)
	$(ENGINE) run --rm -v $(CURDIR):/src orbis-build:$(VERSION) packaging/build-packages.sh

rootfs: container         ## root filesystem from one Arch snapshot + out/repo
	$(ENGINE) run --rm --privileged -v $(CURDIR):/src orbis-build:$(VERSION) \
	  packaging/rootfs/build-rootfs.sh --snapshot $(SNAPSHOT) --repo out/repo --out out/rootfs

initramfs:                ## needs FIRMWARE_FROM=<your current initramfs.cpio.gz>
	@test -n "$(FIRMWARE_FROM)" || (echo "set FIRMWARE_FROM=path/to/working/initramfs.cpio.gz"; exit 2)
	git -C out/src/initramfs-ps4 rev-parse HEAD 2>/dev/null || git clone https://github.com/GayCorp/initramfs-ps4 out/src/initramfs-ps4
	git -C out/src/initramfs-ps4 checkout -q c21a72044adf6c13b773c40f16bae2a9cb83523e
	python3 packaging/initramfs/build_initramfs.py --src out/src/initramfs-ps4 \
	  --firmware-from $(FIRMWARE_FROM) --manifest manifests/firmware.json -o out/initramfs.cpio.gz

image:                    ## USB image (needs out/rootfs extracted to out/rootfs/dir, kernel, initramfs)
	python3 packaging/image/make_usb_image.py image --rootfs-dir out/rootfs/dir \
	  --kernel out/kernel/$(TARGET)/bzImage --initramfs out/initramfs.cpio.gz -o out/orbis-$(VERSION).img

sbom:
	python3 packaging/sbom.py > out/sbom.spdx.json

dist:                     ## source release tarball
	python3 packaging/dist.py --version $(VERSION)

clean:
	rm -rf out tools/gpu-probe/orbis-gpu-probe
