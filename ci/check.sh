#!/bin/sh
# Everything a change has to pass short of a full build: signatures and checksums of the
# upstream sources, the patch queue, the package version and the kernel config policy.
# Run in Debian trixie with the build dependencies installed, see ci/container.sh.
set -eu
cd "$(dirname "$0")/.."
report=$(pwd)/${PATCHQ_REPORT:-check-report.md}
: > "$report"

echo "== package version"
mk_version=$(make -s print-DEB_VERSION)
mk_source=proxmox-kernel-$(make -s print-KERNEL_SERIES)
cl_version=$(dpkg-parsechangelog -S Version)
cl_source=$(dpkg-parsechangelog -S Source)
if [ "$mk_version" != "$cl_version" ] || [ "$mk_source" != "$cl_source" ]; then
    echo "Makefile has $mk_source $mk_version, debian/changelog has $cl_source $cl_version" >&2
    exit 1
fi
echo "$cl_source $cl_version"

echo "== upstream sources"
make download
make verify-hardened

echo "== patch queue"
PATCHQ_REPORT=$report python3 ci/patchq.py check

echo "== kernel config"
make build-dir-fresh
cd "proxmox-kernel-$(make -s print-KERNEL_VER)"
debian/rules .config_mark
{
    echo '### Kernel config'
    echo
    echo '```'
    grep -E '^CONFIG_(CC_VERSION_TEXT|LTO_CLANG_THIN|LTO_CLANG_FULL|CFI|X86_KERNEL_IBT|RANDSTRUCT_FULL|X86_MARCH_ICELAKE_SERVER|X86_NATIVE_CPU|LSM|DEBUG_INFO_BTF)=' linux/.config
    echo '```'
} >> "$report"
