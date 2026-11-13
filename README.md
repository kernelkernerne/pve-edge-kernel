# pve-edge-kernel

A bleeding-edge, hardened kernel for Proxmox VE 9 (Debian trixie, amd64):

- vanilla [kernel.org](https://kernel.org) stable instead of the Ubuntu kernel Proxmox VE uses,
- the fixes of the [Proxmox VE kernel](https://git.proxmox.com/?p=pve-kernel.git) that still apply,
  and its ZFS,
- [linux-hardened](https://github.com/anthraxx/linux-hardened), with a config that merges the
  Proxmox VE and the Arch Linux linux-hardened configs,
- built with Clang/LLVM, ThinLTO and kCFI, optimized for Sunny Cove (Ice Lake server) by default.

It is not affiliated with or supported by Proxmox. It is built for one server first: test it
before you rely on it, and keep a stock kernel installed to fall back to.

Struct layout randomization uses a random seed per build, which the headers package ships for
out-of-tree modules. For the packages published as releases, that seed is public, so
randomization only protects your own builds.

## Layout

| path | |
|---|---|
| `patches/kernel/` | kernel patches, applied in order to the kernel.org tarball |
| `patches/hardened/` | the linux-hardened release patch, unmodified, with its signature |
| `patches/zfs/` | patches on top of Proxmox zfsonlinux (`submodules/zfsonlinux`) |
| `config/` | the Proxmox VE and linux-hardened configs, and `edge-amd64.policy` merging them |
| `upstream/` | the checksum of the kernel.org tarball |
| `keys/` | keys verifying the kernel.org tarballs and the linux-hardened releases |
| `ci/` | scripts used by the workflows, also usable locally |

The config policy lists every option taken from linux-hardened or set explicitly; the build
fails if the resulting config does not match it. `README.proxmox` is the README of the Proxmox
VE kernel this is forked from.

## Installing from the APT repository

Released packages are published as an APT repository in the
[`apt` release](https://github.com/kernelkernerne/pve-edge-kernel/releases/tag/apt), signed with
`keys/apt.asc` (`021E C800 04BF 191B 38EC  C151 CDF8 D794 BD00 530D`). It keeps the newest three
versions. They are built with `EDGE_MARCH=icelake-server` and only boot on Ice Lake server or
newer Intel CPUs; build the packages yourself for other CPUs.

```
curl -fsSL https://github.com/kernelkernerne/pve-edge-kernel/releases/download/apt/pve-edge-kernel.gpg \
    -o /usr/share/keyrings/pve-edge-kernel.gpg
echo "deb [signed-by=/usr/share/keyrings/pve-edge-kernel.gpg] https://github.com/kernelkernerne/pve-edge-kernel/releases/download/apt ./" \
    > /etc/apt/sources.list.d/pve-edge-kernel.list
apt update
apt install proxmox-kernel-edge-7.3 proxmox-headers-edge-7.3
```

Pin the old kernel with `proxmox-boot-tool kernel pin` until the new one has booted fine. The
repository does not contain `proxmox-kernel-libc-dev`, so the one of Proxmox VE stays installed.

## Building

On Debian trixie with the build dependencies installed (see `ci/deps.sh`), or in a container
with `ci/container.sh`:

```
git submodule update --init --recursive
make deb EDGE_LINTIAN=0
```

| variable | default | |
|---|---|---|
| `EDGE_MARCH` | `icelake-server` | `icelake-server`, `native` (the build host) or `generic` |
| `EDGE_TOOLCHAIN` | `llvm` | `llvm` or `gcc` |
| `EDGE_LLVM_VERSION` | `23` | LLVM version, from [apt.llvm.org](https://apt.llvm.org) |
| `EDGE_LTO` | `thin` | `thin`, `full` or `none`; LTO disables Rust support |
| `EDGE_LINTIAN` | `1` | `0` skips lintian, which takes 15+ minutes |

Out-of-tree modules for this kernel, for example DKMS ones, are built with the same LLVM
version, which the headers package recommends. ZFS is built in; to keep `zfs-dkms` from failing
on this kernel, restrict it to official kernels in `/etc/dkms/zfs.conf`:

```
BUILD_EXCLUSIVE_KERNEL="^[0-9.]+-[0-9]+-pve$"
```

## CI

| workflow | when | what |
|---|---|---|
| `check` | push, pull request | signatures and checksums, patch queue (`ci/patchq.py`), package version, config policy |
| `build` | manually, tags `v*` | full build; tags matching the package version publish a release and add it to the APT repository |
| `apt` | after a release, manually | adds the packages of a release to the APT repository (`ci/apt-publish.sh`) |
| `watch` | daily | upstream updates, see below |

`ci/watch.py` puts updates that can be applied mechanically into one pull request: a newer
stable release of the current series (dropping patches it already contains), a newer
linux-hardened release, a changed Arch Linux linux-hardened config, and new Proxmox zfsonlinux
commits. A newer kernel series and new Proxmox pve-kernel commits, triaged against the patch
queue, are reported in issues.

To release, merge, then tag the package version: `git tag v7.3.3-2 && git push --tags`.
