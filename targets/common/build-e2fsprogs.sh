#!/usr/bin/env bash
# Build and install Whamcloud-patched e2fsprogs from source.
#
# Used in both build containers and VM images.
#
# The default tag is pinned at the top of this script -- bump it
# in one place to update all targets.  Callers may override by
# passing a tag as $1, or pass the literal string "latest" to
# auto-discover the most recent v*-wc* tag.
#
# Cross-compilation support:
#   Set TARGET_ARCH to cross-compile (e.g. TARGET_ARCH=aarch64).
#   Set DESTDIR to redirect installation (e.g. DESTDIR=/output).
#   Set SYSROOT to point the cross compiler at a sysroot for headers
#     and libs (required on RHEL where the cross-toolchain ships no
#     glibc/headers; install via dnf --forcearch --installroot).
#
# Usage: build-e2fsprogs.sh [TAG|latest]
set -euo pipefail

# Single source of truth for the e2fsprogs version across all targets.
DEFAULT_E2FS_TAG="v1.47.3-wc2"

TARGET_ARCH="${TARGET_ARCH:-$(uname -m)}"
HOST_ARCH="$(uname -m)"
DESTDIR="${DESTDIR:-}"
SYSROOT="${SYSROOT:-}"

E2FS_REPO=https://review.whamcloud.com/tools/e2fsprogs

ARG_TAG="${1:-}"
if [[ -z "$ARG_TAG" ]]; then
    TAG="$DEFAULT_E2FS_TAG"
elif [[ "$ARG_TAG" == "latest" ]]; then
    TAG=$(git ls-remote --tags "$E2FS_REPO" 'refs/tags/v*wc*' \
        | grep -v '\^{}' \
        | awk '{print $2}' \
        | sed 's|refs/tags/||' \
        | sort -V \
        | tail -1)
else
    TAG="$ARG_TAG"
fi

# Cross-compilation setup
CONFIGURE_HOST=""
CROSS_TRIPLE=""
if [[ "$TARGET_ARCH" == "aarch64" && "$HOST_ARCH" != "aarch64" ]]; then
	CROSS_TRIPLE="aarch64-linux-gnu"
elif [[ "$TARGET_ARCH" == "x86_64" && "$HOST_ARCH" != "x86_64" ]]; then
	CROSS_TRIPLE="x86_64-linux-gnu"
fi
if [[ -n "$CROSS_TRIPLE" ]]; then
	CONFIGURE_HOST="--host=$CROSS_TRIPLE"
	if [[ -n "$SYSROOT" ]]; then
		# RHEL cross-toolchains have no built-in sysroot; configure's
		# "C compiler can create executables" probe fails without one.
		# -isystem is also needed: the EPEL gcc-x86_64-linux-gnu ships
		# an empty default include search list, so without it the
		# preprocessor never finds glibc's headers (PATH_MAX, etc.).
		export CC="${CROSS_TRIPLE}-gcc --sysroot=${SYSROOT} -isystem ${SYSROOT}/usr/include"
	else
		export CC="${CROSS_TRIPLE}-gcc"
	fi
fi

echo "e2fsprogs: building tag $TAG"
git clone --depth 1 --branch "$TAG" "$E2FS_REPO" /tmp/e2fsprogs
cd /tmp/e2fsprogs

if [[ -n "$DESTDIR" ]]; then
	./configure --prefix=/usr --with-root-prefix="" \
	    --enable-elf-shlibs --disable-uuidd $CONFIGURE_HOST \
	    CFLAGS="-fPIC -O2"
	make -j"$(nproc)"
	make install DESTDIR="$DESTDIR"
	make install-libs DESTDIR="$DESTDIR"
	echo "e2fsprogs: installed to $DESTDIR"
else
	./configure --prefix=/usr --with-root-prefix="" \
	    --enable-elf-shlibs --disable-uuidd $CONFIGURE_HOST \
	    CFLAGS="-fPIC -O2"
	make -j"$(nproc)"
	make install
	make install-libs
	ldconfig
	echo "e2fsprogs: $(pkg-config --modversion ext2fs)"
fi

cd /
rm -rf /tmp/e2fsprogs
