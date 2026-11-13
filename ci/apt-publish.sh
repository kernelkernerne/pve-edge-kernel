#!/bin/sh
# Add the packages of a release to the APT repository and prune old versions.
#
# The repository is a flat one ('deb URL ./') made of the assets of the GitHub release 'apt':
# the packages, Packages*, Release, InRelease and Release.gpg, signed with APT_SIGNING_KEY, and
# the public key as pve-edge-kernel.gpg. apt follows the redirects of the asset downloads.
#
# Usage: ci/apt-publish.sh TAG
# Needs GH_TOKEN, GITHUB_REPOSITORY, APT_SIGNING_KEY, and gh, dpkg-dev, apt-utils, gpg, xz.
set -eu
tag=$1
keep=${APT_KEEP:-3}
apt_tag=apt
export GH_REPO="$GITHUB_REPOSITORY"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"

gh release download "$tag" -p '*.deb' -D new
# not published: the libc headers would replace the ones of Proxmox VE on upgrades, the
# signing template and debug symbols are of no use without the Proxmox signing service
rm -f new/proxmox-kernel-libc-dev_*.deb new/*-signed-template_*.deb new/*-dbgsym_*.deb

if ! gh release view "$apt_tag" > /dev/null 2>&1; then
    gh release create "$apt_tag" --latest=false --title "APT repository" \
        --notes "Packages of the latest releases, as an APT repository; see the README."
fi
mkdir pool
gh release download "$apt_tag" -p '*.deb' -D pool 2> /dev/null || true
cp new/*.deb pool/

# keep the packages of the newest $keep versions
versions=$(for f in pool/*.deb; do dpkg-deb -f "$f" Version; done | sort -u | sort -V -r)
for v in $(echo "$versions" | tail -n +$((keep + 1))); do
    for f in pool/*_"$v"_*.deb; do
        echo "removing $(basename "$f")"
        gh release delete-asset "$apt_tag" "$(basename "$f")" --yes || true
        rm -f "$f"
    done
done

cd pool
apt-ftparchive packages . > Packages
gzip -9kf Packages
xz -kf Packages
apt-ftparchive \
    -o APT::FTPArchive::Release::Origin=pve-edge-kernel \
    -o APT::FTPArchive::Release::Label=pve-edge-kernel \
    -o APT::FTPArchive::Release::Suite=trixie \
    -o APT::FTPArchive::Release::Codename=trixie \
    -o APT::FTPArchive::Release::Architectures=amd64 \
    -o "APT::FTPArchive::Release::Description=pve-edge-kernel for Proxmox VE 9" \
    release . > Release

export GNUPGHOME="$work/gnupg"
mkdir -m 700 "$GNUPGHOME"
printf '%s\n' "$APT_SIGNING_KEY" | gpg --batch --quiet --import
gpg --batch --yes --clearsign -o InRelease Release
gpg --batch --yes --detach-sign -o Release.gpg Release
gpg --export > pve-edge-kernel.gpg

# packages first, so the index never references a missing file
gh release upload "$apt_tag" ../new/*.deb --clobber
gh release upload "$apt_tag" Packages Packages.gz Packages.xz Release InRelease Release.gpg \
    pve-edge-kernel.gpg --clobber
echo "published $tag; versions: $(echo "$versions" | head -n "$keep" | tr '\n' ' ')"
