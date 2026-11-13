include /usr/share/dpkg/pkg-info.mk

# also bump proxmox-kernel-meta if the default MAJ.MIN version changes!
KERNEL_MAJ=7
KERNEL_MIN=3
KERNEL_PATCHLEVEL=4
# increment KREL for every published package release!
# rebuild packages with new KREL and run 'make abiupdate'
KREL=1

# Use to create a separate package for the same version, like -bpoXY for backport or test-$foo.
# This way the package can be co-installed with the original, a requirement for major dist updates.
KREL_EXTRA=-edge
# Normally empty, but allows adding a part just for the debian package revision, like ~bpoXY+Z.
# For the kernel pkg itself it wouldn't matter, but for the meta pkgs it allows major dist upgrades.
PKG_REV_EXTRA=

# x86-64 CPU optimization target (amd64 only):
#   icelake-server - Sunny Cove / 3rd gen Xeon Scalable (CONFIG_X86_MARCH_ICELAKE_SERVER)
#   native         - CPU of the build host (CONFIG_X86_NATIVE_CPU)
#   generic        - baseline x86-64, runs on any machine
EDGE_MARCH ?= icelake-server

# Compiler for the kernel and the ZFS modules:
#   llvm - clang/ld.lld version EDGE_LLVM_VERSION (e.g. from apt.llvm.org)
#   gcc  - the distribution GCC toolchain
EDGE_TOOLCHAIN ?= llvm
EDGE_LLVM_VERSION ?= 23
# Clang link time optimization: thin, full or none. Ignored with gcc.
# LTO together with BTF rules out Rust, so Rust support is disabled with LTO.
EDGE_LTO ?= thin

KERNEL_MAJMIN=$(KERNEL_MAJ).$(KERNEL_MIN)
KERNEL_VER=$(KERNEL_MAJMIN).$(KERNEL_PATCHLEVEL)
# name of the series meta-packages, kept distinct from the official proxmox-kernel-X.Y
KERNEL_SERIES=edge-$(KERNEL_MAJMIN)

DEB_VERSION=$(KERNEL_VER)-$(KREL)$(PKG_REV_EXTRA)
EXTRAVERSION=-$(KREL)$(KREL_EXTRA)-pve
KVNAME=$(KERNEL_VER)$(EXTRAVERSION)
PACKAGE=proxmox-kernel-$(KVNAME)
HDRPACKAGE=proxmox-headers-$(KVNAME)

ARCH=$(shell dpkg-architecture -qDEB_HOST_ARCH)

SUPPORTED_ARCHS = amd64 arm64
ifeq ($(filter $(ARCH),$(SUPPORTED_ARCHS)),)
$(error Unsupported architecture: $(ARCH). Supported: $(SUPPORTED_ARCHS))
endif

# map Debian arch to kernel source arch directory name
KERNEL_ARCH_amd64 = x86
KERNEL_ARCH_arm64 = arm64
KERNEL_ARCH = $(KERNEL_ARCH_$(ARCH))

SKIPABI=0

BUILD_DIR=proxmox-kernel-$(KERNEL_VER)

KERNEL_SRC=linux
KERNEL_TARBALL=linux-$(KERNEL_VER).tar.xz
KERNEL_TARBALL_URL=https://cdn.kernel.org/pub/linux/kernel/v$(KERNEL_MAJ).x/$(KERNEL_TARBALL)
KERNEL_TARBALL_SHA256=upstream/$(KERNEL_TARBALL).sha256
# kernel configs: the Proxmox VE base, the linux-hardened config and the policy merging both,
# see config/edge-$(ARCH).policy; merged by debian/scripts/edge-config during the build
KERNEL_CFG_FILES=config/proxmox-$(ARCH).config config/linux-hardened-$(ARCH).config config/edge-$(ARCH).policy

# linux-hardened release patch, kept unmodified with its upstream signature and applied after
# patches/kernel; its signature is checked against keys/linux-hardened.gpg
HARDENED_PATCHES=$(sort $(wildcard patches/hardened/*.patch))

ZFSONLINUX_SUBMODULE=submodules/zfsonlinux
ZFSDIR=pkg-zfs

MODULES=modules
MODULE_DIRS=$(ZFSDIR)

# exported to debian/rules via debian/rules.d/dirs.mk
DIRS=KERNEL_SRC ZFSDIR MODULES EDGE_MARCH EDGE_TOOLCHAIN EDGE_LLVM_VERSION EDGE_LTO

DSC=proxmox-kernel-$(KERNEL_SERIES)_$(DEB_VERSION).dsc
DST_DEB=$(PACKAGE)_$(DEB_VERSION)_$(ARCH).deb
SIGNED_TEMPLATE_DEB=$(PACKAGE)-signed-template_$(DEB_VERSION)_$(ARCH).deb
META_DEB=proxmox-kernel-$(KERNEL_SERIES)_$(DEB_VERSION)_$(ARCH).deb
HDR_DEB=$(HDRPACKAGE)_$(DEB_VERSION)_$(ARCH).deb
META_HDR_DEB=proxmox-headers-$(KERNEL_SERIES)_$(DEB_VERSION)_$(ARCH).deb
USR_HDR_DEB=proxmox-kernel-libc-dev_$(DEB_VERSION)_$(ARCH).deb
LINUX_TOOLS_DEB=linux-tools-$(KERNEL_MAJMIN)_$(DEB_VERSION)_$(ARCH).deb
LINUX_TOOLS_DBG_DEB=linux-tools-$(KERNEL_MAJMIN)-dbgsym_$(DEB_VERSION)_$(ARCH).deb

DEBS=$(DST_DEB) $(META_DEB) $(HDR_DEB) $(META_HDR_DEB) $(LINUX_TOOLS_DEB) $(LINUX_TOOLS_DBG_DEB) $(SIGNED_TEMPLATE_DEB) # $(USR_HDR_DEB)

all: deb
# lintian takes 15+ minutes on the kernel package; set EDGE_LINTIAN=0 to skip it
EDGE_LINTIAN ?= 1
deb: $(DEBS)
ifneq ($(EDGE_LINTIAN),0)
	lintian $(DST_DEB)
	#lintian $(HDR_DEB)
	lintian $(LINUX_TOOLS_DEB)
endif

$(META_DEB) $(META_HDR_DEB) $(LINUX_TOOLS_DEB) $(HDR_DEB) $(DST_DEB) &: $(BUILD_DIR).prepared
	cd $(BUILD_DIR); dpkg-buildpackage --jobs=auto -b -uc -us

dsc:
	$(MAKE) $(DSC)
	lintian $(DSC)

$(DSC): $(BUILD_DIR).prepared
	cd $(BUILD_DIR); dpkg-buildpackage -S -uc -us -d

sbuild: $(DSC)
	sbuild $(DSC)

$(BUILD_DIR).prepared: $(addsuffix .prepared,$(KERNEL_SRC) $(MODULES) debian)
	test -f fwlist-previous-$(ARCH) && cp -a fwlist-previous-$(ARCH) $(BUILD_DIR)/ || touch $(BUILD_DIR)/fwlist-previous-$(ARCH)
	cp -a abi-prev-*-$(ARCH) $(BUILD_DIR)/ 2>/dev/null || true
	cp -a abi-blacklist $(BUILD_DIR)/
	touch $@

.PHONY: build-dir-fresh
build-dir-fresh:
	$(MAKE) clean
	$(MAKE) $(BUILD_DIR).prepared
	echo "created build-directory: $(BUILD_DIR).prepared/"

debian.prepared: debian
	rm -rf $(BUILD_DIR)/debian
	mkdir -p $(BUILD_DIR)
	cp -a debian $(BUILD_DIR)/debian
	echo "git clone git://git.proxmox.com/git/pve-kernel.git\\ngit checkout $(shell git rev-parse HEAD)" \
	    >$(BUILD_DIR)/debian/SOURCE
	@$(foreach dir, $(DIRS),echo "$(dir)=$($(dir))" >> $(BUILD_DIR)/debian/rules.d/env.mk;)
	echo "KVNAME=$(KVNAME)" >> $(BUILD_DIR)/debian/rules.d/env.mk
	echo "KERNEL_MAJMIN=$(KERNEL_MAJMIN)" >> $(BUILD_DIR)/debian/rules.d/env.mk
	echo "KERNEL_SERIES=$(KERNEL_SERIES)" >> $(BUILD_DIR)/debian/rules.d/env.mk
	cd $(BUILD_DIR); debian/rules debian/control
	touch $@

upstream/$(KERNEL_TARBALL):
	mkdir -p upstream
	curl -fL --retry 3 -o $@.tmp $(KERNEL_TARBALL_URL)
	mv $@.tmp $@

.PHONY: download
download: upstream/$(KERNEL_TARBALL)
	cd upstream; sha256sum -c $(KERNEL_TARBALL).sha256

.PHONY: verify-hardened
verify-hardened:
	set -e; for patch in $(HARDENED_PATCHES); do \
	    gpgv --keyring ./keys/linux-hardened.gpg "$$patch.sig" "$$patch"; \
	  done

# print a variable, e.g. 'make -s print-KERNEL_VER', for scripts and CI
print-%:
	@echo '$($*)'

$(KERNEL_SRC).prepared: upstream/$(KERNEL_TARBALL) $(KERNEL_CFG_FILES) $(HARDENED_PATCHES) | verify-hardened
	cd upstream; sha256sum -c $(KERNEL_TARBALL).sha256
	rm -rf $(BUILD_DIR)/$(KERNEL_SRC) $(BUILD_DIR)/config $@
	mkdir -p $(BUILD_DIR)/$(KERNEL_SRC) $(BUILD_DIR)/config
	tar -C $(BUILD_DIR)/$(KERNEL_SRC) --strip-components=1 -xf upstream/$(KERNEL_TARBALL)
	cp $(KERNEL_CFG_FILES) $(BUILD_DIR)/config/
	set -e; cd $(BUILD_DIR)/$(KERNEL_SRC); \
	  for patch in ../../patches/kernel/*.patch; do \
	    echo "applying patch '$$patch'"; \
	    patch --batch --forward --no-backup-if-mismatch -p1 < "$${patch}"; \
	  done
	# the release patch also sets EXTRAVERSION, in a hunk that only applies to its exact base
	set -e; cd $(BUILD_DIR)/$(KERNEL_SRC); \
	  for patch in $(addprefix $(CURDIR)/,$(HARDENED_PATCHES)); do \
	    echo "applying patch '$$patch'"; \
	    filterdiff -p1 -x Makefile "$${patch}" | patch --batch --forward --no-backup-if-mismatch -p1; \
	  done
	sed -i $(BUILD_DIR)/$(KERNEL_SRC)/Makefile -e 's/^EXTRAVERSION.*$$/EXTRAVERSION=$(EXTRAVERSION)/'
	touch $@

$(MODULES).prepared: $(addsuffix .prepared,$(MODULE_DIRS))
	touch $@

$(ZFSDIR).prepared: $(ZFSONLINUX_SUBMODULE) $(wildcard patches/zfs/*.patch) | submodule
	rm -rf $(BUILD_DIR)/$(MODULES)/$(ZFSDIR) $(BUILD_DIR)/$(MODULES)/tmp $@
	mkdir -p $(BUILD_DIR)/$(MODULES)/tmp
	cp -a $(ZFSONLINUX_SUBMODULE)/* $(BUILD_DIR)/$(MODULES)/tmp
	cd $(BUILD_DIR)/$(MODULES)/tmp; make kernel
	rm -rf $(BUILD_DIR)/$(MODULES)/tmp
	# edge specific ZFS patches, on top of the zfs-linux ones
	set -e; cd $(BUILD_DIR)/$(MODULES)/$(ZFSDIR); \
	  for patch in $(addprefix $(CURDIR)/,$(sort $(wildcard patches/zfs/*.patch))); do \
	    echo "applying patch '$$patch'"; \
	    patch --batch --forward --no-backup-if-mismatch -p1 < "$${patch}"; \
	  done
	touch $(ZFSDIR).prepared

.PHONY: distclean
distclean: clean
	git submodule deinit --all

# upgrade to current master
.PHONY: update_modules
update_modules: submodule
	cd $(ZFSONLINUX_SUBMODULE); git pull --ff-only origin master

# make sure submodules were initialized
.PHONY: submodule
submodule:
	test -f "$(ZFSONLINUX_SUBMODULE)/Makefile" || git submodule update --init --recursive $(ZFSONLINUX_SUBMODULE)

# call after ABI bump with header deb in working directory
.PHONY: abiupdate
abiupdate: abi-prev-$(KVNAME)-$(ARCH)
abi-prev-$(KVNAME)-$(ARCH): abi-tmp-$(KVNAME)-$(ARCH)
ifneq ($(strip $(shell git status --untracked-files=no --porcelain -z)),)
	@echo "working directory unclean, aborting!"
	@false
else
	git rm "abi-prev-*-$(ARCH)"
	mv $< $@
	git add $@
	git commit -s -m "update ABI file for $(KVNAME) ($(ARCH))" -m "(generated with debian/scripts/abi-generate)"
	@echo "update abi-prev-$(KVNAME)-$(ARCH) committed!"
endif

abi-tmp-$(KVNAME)-$(ARCH):
	@ test -e $(HDR_DEB) || (echo "need $(HDR_DEB) to extract ABI data!" && false)
	debian/scripts/abi-generate $(HDR_DEB) $@ $(KVNAME) 1

.PHONY: clean
clean:
	rm -rf *~ proxmox-kernel-[0-9]*/ *.prepared
	rm -f *.deb *.dsc *.changes *.buildinfo *.build proxmox-kernel*.tar.*
