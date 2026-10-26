# NickLabs PS4 Orbis Linux — top-level build entry points. See docs/BUILD-AND-DEPLOY.md.
SNAPSHOT ?= 2026/10/20
TARGET   ?= aeolia-belize-6.15
PROFILE  ?= cuh-1215a
ENGINE   ?= $(shell command -v podman >/dev/null 2>&1 && echo podman || echo docker)
FIRMWARE_FROM ?= out/references/initramfs.cpio.gz
VERSION  := $(shell python3 -c "import sys; sys.path.insert(0,'platform'); import orbis; print(orbis.__version__)")

.PHONY: test probe kernel container packages rootfs initramfs image sbom dist clean container-lock \
        references initramfs-src stage0 release release-stage0 repo-key serve-repo loader-payload

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
	  packaging/rootfs/build-rootfs.sh --snapshot $(SNAPSHOT) --repo out/repo --out out/rootfs --model CUH-1215A

references:               ## fetch + verify community inputs (manifests/references.json) into out/references
	python3 packaging/scripts/fetch_references.py --out out/references

initramfs-src:            ## pinned GayCorp/initramfs-ps4 source into out/src/initramfs-ps4
	git -C out/src/initramfs-ps4 rev-parse HEAD >/dev/null 2>&1 || git clone https://github.com/GayCorp/initramfs-ps4 out/src/initramfs-ps4
	git -C out/src/initramfs-ps4 checkout -q $$(python3 -c "import json; print(json.load(open('manifests/sources.json'))['initramfs']['commit'])")

initramfs: initramfs-src  ## PROFILE=cuh-1215a (firmware verified from FIRMWARE_FROM); PROFILE=firmware-free needs none
	python3 packaging/initramfs/build_initramfs.py --src out/src/initramfs-ps4 --profile $(PROFILE) \
	  $(if $(filter firmware-free,$(PROFILE)),,--firmware-from $(FIRMWARE_FROM)) -o out/initramfs/initramfs-$(PROFILE).cpio.gz

stage0:                   ## hardware-report USB image (kernel + initramfs, no rootfs)
	python3 packaging/image/make_usb_image.py image --stage0 --kernel out/kernel/$(TARGET)/bzImage \
	  --initramfs out/initramfs/initramfs-$(PROFILE).cpio.gz --size 1G -o out/release/orbis-$(VERSION)-stage0-$(PROFILE).img

image:                    ## full USB image (as root/in the container: needs out/rootfs/dir from `make rootfs`)
	python3 packaging/image/make_usb_image.py image --rootfs-dir out/rootfs/dir \
	  --kernel out/kernel/$(TARGET)/bzImage --initramfs out/initramfs/initramfs-$(PROFILE).cpio.gz \
	  -o out/release/orbis-$(VERSION)-$(PROFILE).img

release:                  ## everything, one command (KERNEL_TAR=... to reuse the released kernel build)
	packaging/release.sh --profile $(PROFILE) $(if $(KERNEL_TAR),--kernel-tar $(KERNEL_TAR),) --engine $(ENGINE)

release-stage0:           ## steps 1-5 only: references, initramfs, stage-0 image
	packaging/release.sh --profile $(PROFILE) --stage0-only $(if $(KERNEL_TAR),--kernel-tar $(KERNEL_TAR),)

repo-key:                 ## create the local package-signing key (out/keys, never committed)
	packaging/scripts/orbis-repo-key.sh

serve-repo:               ## serve out/repo to consoles on the LAN (port 8780)
	packaging/scripts/orbis-serve-repo.sh

loader-payload:           ## build the ps4-linux-loader payload from the pinned commit
	packaging/loader/build-loader-payload.sh

sbom:
	python3 packaging/sbom.py > out/sbom.spdx.json

dist:                     ## source release tarball
	python3 packaging/dist.py --version $(VERSION)

clean:
	rm -rf out tools/gpu-probe/orbis-gpu-probe
