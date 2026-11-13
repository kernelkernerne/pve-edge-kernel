#!/bin/sh
# Install everything needed to check and build pve-edge-kernel on Debian trixie, as root.
set -eu
cd "$(dirname "$0")/.."
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates curl gpg dpkg-dev make python3 >/dev/null

llvm=$(sed -n 's/^EDGE_LLVM_VERSION ?= *//p' Makefile)
curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/apt.llvm.org.gpg
cat > /etc/apt/sources.list.d/llvm.sources <<EOF
Types: deb
URIs: https://apt.llvm.org/trixie/
Suites: llvm-toolchain-trixie-$llvm
Components: main
Signed-By: /usr/share/keyrings/apt.llvm.org.gpg
EOF
apt-get update -qq

# Build-Depends of the kernel package, without versions and alternatives
deps=$(python3 - <<'EOF'
import re
text = open('debian/control.in').read()
block = re.search(r'^Build-Depends:(.*?)(?=^\S)', text, re.S | re.M).group(1)
print(' '.join(re.sub(r'\(.*?\)', '', d).split('|')[0].strip()
               for d in block.split(',') if d.strip()))
EOF
)
# plus what the ZFS source package preparation and the CI scripts need
# shellcheck disable=SC2086
apt-get install -y -qq --no-install-recommends $deps \
    clang-"$llvm" lld-"$llvm" llvm-"$llvm" \
    build-essential debhelper dh-python fakeroot xz-utils patch >/dev/null

git config --global --add safe.directory '*'
