#!/usr/bin/env bash
# Build and install Whamcloud-patched e2fsprogs from source.
#
# Used in both build containers and VM images.
# Build containers: pass TAG to pin to a specific version.
# VM images: omit TAG to auto-discover the latest v*-wc* release.
#
# Usage: build-e2fsprogs.sh [TAG]
#   TAG  Git tag to build (e.g. v1.47.3-wc2). Defaults to latest v*-wc*.
set -euo pipefail

E2FS_REPO=https://review.whamcloud.com/tools/e2fsprogs

if [[ -n "${1:-}" ]]; then
    TAG="$1"
else
    TAG=$(git ls-remote --tags "$E2FS_REPO" 'refs/tags/v*wc*' \
        | grep -v '\^{}' \
        | awk '{print $2}' \
        | sed 's|refs/tags/||' \
        | sort -V \
        | tail -1)
fi

echo "e2fsprogs: building tag $TAG"
git clone --depth 1 --branch "$TAG" "$E2FS_REPO" /tmp/e2fsprogs
cd /tmp/e2fsprogs
./configure --prefix=/usr --with-root-prefix="" \
    --enable-elf-shlibs --disable-uuidd
make -j"$(nproc)"
make install
make install-libs
ldconfig
echo "e2fsprogs: $(pkg-config --modversion ext2fs)"
cd /
rm -rf /tmp/e2fsprogs
