#!/usr/bin/env bash
# Build and install Whamcloud-patched e2fsprogs from source.
#
# Used in both build containers and VM images.
# Build containers: pass TAG to pin to a specific version.
# VM images: omit TAG to auto-discover the latest v*-wc* release.
#
# Cross-compilation support:
#   Set TARGET_ARCH to cross-compile (e.g. TARGET_ARCH=aarch64).
#   Set DESTDIR to redirect installation (e.g. DESTDIR=/output).
#
# Usage: build-e2fsprogs.sh [TAG]
#   TAG  Git tag to build (e.g. v1.47.3-wc2). Defaults to latest v*-wc*.
set -euo pipefail

TARGET_ARCH="${TARGET_ARCH:-$(uname -m)}"
HOST_ARCH="$(uname -m)"
DESTDIR="${DESTDIR:-}"

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

# Cross-compilation setup
CONFIGURE_HOST=""
if [[ "$TARGET_ARCH" == "aarch64" && "$HOST_ARCH" != "aarch64" ]]; then
	CONFIGURE_HOST="--host=aarch64-linux-gnu"
	export CC=aarch64-linux-gnu-gcc
elif [[ "$TARGET_ARCH" == "x86_64" && "$HOST_ARCH" != "x86_64" ]]; then
	CONFIGURE_HOST="--host=x86_64-linux-gnu"
	export CC=x86_64-linux-gnu-gcc
fi

echo "e2fsprogs: building tag $TAG"
git clone --depth 1 --branch "$TAG" "$E2FS_REPO" /tmp/e2fsprogs
cd /tmp/e2fsprogs

if [[ -n "$DESTDIR" ]]; then
	./configure --prefix=/usr --with-root-prefix="" \
	    --enable-elf-shlibs --disable-uuidd $CONFIGURE_HOST
	make -j"$(nproc)"
	make install DESTDIR="$DESTDIR"
	make install-libs DESTDIR="$DESTDIR"
	echo "e2fsprogs: installed to $DESTDIR"
else
	./configure --prefix=/usr --with-root-prefix="" \
	    --enable-elf-shlibs --disable-uuidd $CONFIGURE_HOST
	make -j"$(nproc)"
	make install
	make install-libs
	ldconfig
	echo "e2fsprogs: $(pkg-config --modversion ext2fs)"
fi

cd /
rm -rf /tmp/e2fsprogs
