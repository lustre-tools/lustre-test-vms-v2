#!/bin/bash
# cross-compile-env.sh -- source this to populate cross-compile env vars.
#
# Input (env):
#   TARGET_ARCH -- x86_64 or aarch64.  Defaults to $(uname -m).
#
# Exports:
#   HOST_ARCH            -- $(uname -m)
#   CROSSING             -- 1 if host != target, 0 otherwise
#   KBUILD_ARCH          -- kernel ARCH=<value> (arm64, x86_64)
#   KERNEL_IMAGE         -- relative path to the bootable image
#   MAKE_TARGETS         -- kbuild targets for the bootable image
#   CROSS_TRIPLE         -- GNU triple (aarch64-linux-gnu, x86_64-linux-gnu)
#   CROSS_COMPILE        -- CROSS_COMPILE=<triple>- when crossing, else empty
#   MAKE_ARCH_FLAGS      -- bash array: ARCH=... [CROSS_COMPILE=...] [KCFLAGS=...]
#   CONFIGURE_HOST       -- --host=<triple> when crossing, else empty
#   DEB_ARCH             -- amd64 or arm64 (for apt multiarch)
#   IOZONE_TARGET        -- make target for iozone (linux-AMD64 or linux-arm)
#
# This script is sourced (not executed).  It must be safe to source
# multiple times.  It assumes `set -u` but not `set -e`.

TARGET_ARCH="${TARGET_ARCH:-$(uname -m)}"
HOST_ARCH="$(uname -m)"

if [[ "$TARGET_ARCH" != "$HOST_ARCH" ]]; then
	CROSSING=1
else
	CROSSING=0
fi

case "$TARGET_ARCH" in
	aarch64)
		KBUILD_ARCH="arm64"
		KERNEL_IMAGE="arch/arm64/boot/Image.gz"
		MAKE_TARGETS="vmlinux Image.gz"
		CROSS_TRIPLE="aarch64-linux-gnu"
		DEB_ARCH="arm64"
		IOZONE_TARGET="linux-arm"
		;;
	x86_64)
		KBUILD_ARCH="x86_64"
		KERNEL_IMAGE="arch/x86/boot/bzImage"
		MAKE_TARGETS="vmlinux bzImage"
		CROSS_TRIPLE="x86_64-linux-gnu"
		DEB_ARCH="amd64"
		IOZONE_TARGET="linux-AMD64"
		;;
	*)
		echo "cross-compile-env.sh: unsupported TARGET_ARCH=$TARGET_ARCH" >&2
		return 1 2>/dev/null || exit 1
		;;
esac

MAKE_ARCH_FLAGS=(ARCH="$KBUILD_ARCH")
if [[ "$CROSSING" == "1" ]]; then
	CROSS_COMPILE="${CROSS_TRIPLE}-"
	MAKE_ARCH_FLAGS+=(CROSS_COMPILE="$CROSS_COMPILE")
	# Cross-compiler may be stricter than native; demote -Werror
	# variants to warnings so a clean native kernel build doesn't
	# fail cross.  Applied for either direction.
	MAKE_ARCH_FLAGS+=("KCFLAGS=-Wno-error -Wno-error=incompatible-pointer-types -Wno-error=missing-prototypes -Wno-error=enum-int-mismatch")
	CONFIGURE_HOST="--host=${CROSS_TRIPLE}"
else
	CROSS_COMPILE=""
	CONFIGURE_HOST=""
fi

# Install the cross toolchain in the running container if we're going
# to use it and it isn't already present.  Best-effort: a container
# Dockerfile that pre-installed the toolchain skips the install here;
# an older container that didn't falls through to dnf/apt.
cross_ensure_toolchain() {
	[[ "$CROSSING" == "1" ]] || return 0
	local cc="${CROSS_TRIPLE}-gcc"
	if command -v "$cc" >/dev/null 2>&1; then
		return 0
	fi
	echo "--- Installing ${CROSS_TRIPLE} cross-compiler..."
	if command -v dnf >/dev/null 2>&1; then
		dnf -y install "gcc-${CROSS_TRIPLE}" "binutils-${CROSS_TRIPLE}" 2>&1 | tail -3 || true
	elif command -v apt-get >/dev/null 2>&1; then
		# Debian uses "x86-64-linux-gnu" (hyphen) instead of the "x86_64-linux-gnu"
		# triple RHEL packages use.  Python side maps this for us via
		# cross_compile.apt_package_name, but the shell path has to
		# translate inline.
		local apt_triple="$CROSS_TRIPLE"
		[[ "$apt_triple" == "x86_64-linux-gnu" ]] && apt_triple="x86-64-linux-gnu"
		apt-get update -qq 2>&1 | tail -3
		apt-get install -y "gcc-${apt_triple}" 2>&1 | tail -3 || true
	fi
	if ! command -v "$cc" >/dev/null 2>&1; then
		echo "WARNING: ${cc} not available after install; build may fail" >&2
	fi
}
