#!/usr/bin/env bash
# Build and install source-built tools used in VM images:
#   IOR + mdtest, iozone, pjdfstest, FlameGraph, drgn
#
# Expects gcc, make, autoconf, automake, libtool, curl, pip3
# to already be installed (via the package list install step).
# git is installed here as a build-time dep (not in the VM image packages).
#
# Cross-compilation support:
#   Set TARGET_ARCH to cross-compile (e.g. TARGET_ARCH=aarch64).
#   The script detects the host arch and sets up CC/--host
#   accordingly. When not cross-compiling, builds natively.
#
# Output:
#   By default, installs to /usr/local/bin.
#   Set DESTDIR to redirect (e.g. DESTDIR=/output for staged builds).
set -euo pipefail

# Pinned versions of source-built tools.  Bump in one place.
IOR_VERSION="${IOR_VERSION:-4.0.0}"
IOZONE_VERSION="${IOZONE_VERSION:-3_506}"

TARGET_ARCH="${TARGET_ARCH:-$(uname -m)}"
HOST_ARCH="$(uname -m)"
DESTDIR="${DESTDIR:-}"
PREFIX="${DESTDIR}/usr/local"

# Cross-compilation setup (only set CC/CXX when cross-compiling;
# leave them unset for native builds so mpicc wrappers work)
CONFIGURE_HOST=""

CROSS_TRIPLE=""
if [[ "$TARGET_ARCH" == "aarch64" && "$HOST_ARCH" != "aarch64" ]]; then
	CROSS_TRIPLE="aarch64-linux-gnu"
elif [[ "$TARGET_ARCH" == "x86_64" && "$HOST_ARCH" != "x86_64" ]]; then
	CROSS_TRIPLE="x86_64-linux-gnu"
fi
if [[ -n "$CROSS_TRIPLE" ]]; then
	CONFIGURE_HOST="--host=$CROSS_TRIPLE"
	# RHEL cross gccs ship without a sysroot or default include path;
	# point them at one if the caller provides it.  The Debian path
	# uses multiarch, so SYSROOT stays unset there.
	if [[ -n "${SYSROOT:-}" ]]; then
		export CC="${CROSS_TRIPLE}-gcc --sysroot=${SYSROOT} -isystem ${SYSROOT}/usr/include"
		export CXX="${CROSS_TRIPLE}-g++ --sysroot=${SYSROOT} -isystem ${SYSROOT}/usr/include"
	else
		export CC="${CROSS_TRIPLE}-gcc"
		export CXX="${CROSS_TRIPLE}-g++"
	fi
	echo "--- Cross-compiling tools: ${HOST_ARCH} -> ${TARGET_ARCH}"
fi

# Ensure build deps are present (may have been skipped by --skip-broken)
if command -v dnf &>/dev/null; then
	dnf -y install gcc gcc-c++ make autoconf automake libtool git curl \
		python3-pip 2>/dev/null || true
elif command -v apt-get &>/dev/null; then
	apt-get update && apt-get install -y gcc g++ make autoconf automake \
		libtool git curl python3-pip 2>/dev/null || true
fi

# Install cross-compiler if cross-compiling.  Cross direction is
# implied by TARGET_ARCH vs HOST_ARCH (already captured in
# CONFIGURE_HOST above).  The triple in CONFIGURE_HOST is
# --host=<triple>; derive the package-name stem from it so we support
# either direction (aarch64 target from x86 host, x86 target from
# aarch64 host).
if [[ -n "$CONFIGURE_HOST" ]]; then
	CROSS_TRIPLE="${CONFIGURE_HOST#--host=}"
	if command -v dnf &>/dev/null; then
		dnf -y install "gcc-${CROSS_TRIPLE}" "binutils-${CROSS_TRIPLE}" 2>/dev/null || true
	elif command -v apt-get &>/dev/null; then
		# Debian's cross package naming uses x86-64-linux-gnu (hyphen)
		# rather than the RHEL x86_64-linux-gnu (underscore).
		APT_TRIPLE="${CROSS_TRIPLE//x86_64/x86-64}"
		apt-get install -y "gcc-${APT_TRIPLE}" "g++-${APT_TRIPLE}" 2>/dev/null || true
	fi
fi

mkdir -p "$PREFIX/bin"
cd /tmp

# IOR + mdtest
#
# IOR's configure auto-detects MPI via mpicc.  We have no cross-arch
# MPI in the build container (cross-building openmpi means cross-building
# libfabric + ucx + ...), so skip IOR/mdtest on cross builds; install
# them inside the VM via dnf if they're needed at test time.
#
# When the previous form of this section failed during `./configure`,
# `set -e` did *not* abort because a failing middle clause in a
# `cd && configure && make` chain is shielded by &&.  The chain has
# been split into separate commands so any failure is caught.
if [[ -z "$CROSS_TRIPLE" ]]; then
	# Add openmpi to PATH if available (EL installs to /usr/lib64/openmpi/bin)
	if [[ -d /usr/lib64/openmpi/bin ]]; then
		export PATH=/usr/lib64/openmpi/bin:$PATH
		export LD_LIBRARY_PATH="/usr/lib64/openmpi/lib:${LD_LIBRARY_PATH:-}"
	fi
	curl -fsSL "https://github.com/hpc/ior/releases/download/${IOR_VERSION}/ior-${IOR_VERSION}.tar.gz" | tar xz
	cd "ior-${IOR_VERSION}"
	./configure
	make -j"$(nproc)"
	cp src/ior src/mdtest "$PREFIX/bin/"
	cd /tmp && rm -rf "ior-${IOR_VERSION}"
else
	echo "--- Skipping IOR/mdtest (cross-compile; no cross-arch MPI toolchain)"
fi

# iozone (needs -Wno-error=implicit-* for GCC 14+)
#
# Skipped on cross builds: the linux-AMD64 target's Makefile has a
# literal `x86_64` token (TARGET-substitution) that the cross gcc
# treats as an input filename and aborts; the linux-arm target works
# native but we'd need to special-case the cross side anyway.  Drop
# iozone in cross images; install via dnf inside the VM.
if [[ -z "$CROSS_TRIPLE" ]]; then
	curl -fsSL "http://www.iozone.org/src/current/iozone${IOZONE_VERSION}.tar" | tar xf -
	cd "iozone${IOZONE_VERSION}/src/current"
	EXTRA_CFLAGS="-Wno-error=implicit-int -Wno-error=implicit-function-declaration"
	# iozone uses arch-specific make targets
	case "$TARGET_ARCH" in
		aarch64) IOZONE_TARGET="linux-arm" ;;
		*)       IOZONE_TARGET="linux-AMD64" ;;
	esac
	make -j"$(nproc)" "$IOZONE_TARGET" \
		CC="${CC:-cc}" \
		CFLAGS="-O3 $EXTRA_CFLAGS" \
		C_OPT="-O3 $EXTRA_CFLAGS"
	cp iozone "$PREFIX/bin/"
	cd /tmp && rm -rf "iozone${IOZONE_VERSION}"
else
	echo "--- Skipping iozone (cross-compile; linux-AMD64 Makefile mishandles cross gcc)"
fi

# pjdfstest
git clone https://github.com/pjd/pjdfstest.git
cd pjdfstest
autoreconf -ifs
./configure ${CONFIGURE_HOST:+"$CONFIGURE_HOST"}
make -j"$(nproc)"
cp pjdfstest "$PREFIX/bin/"
cd /tmp && rm -rf pjdfstest

# FlameGraph (pure perl scripts -- no compilation needed)
git clone --depth 1 https://github.com/brendangregg/FlameGraph.git \
    "$PREFIX/FlameGraph"
for f in flamegraph.pl stackcollapse-perf.pl stackcollapse.pl difffolded.pl; do
    ln -sf "$PREFIX/FlameGraph/$f" "$PREFIX/bin/$f"
done

# drgn (Python crash analysis) -- skip when cross-compiling
# (needs target-arch Python headers + C extensions)
if [[ -z "$CONFIGURE_HOST" ]]; then
	pip3 install --break-system-packages drgn 2>/dev/null \
	    || pip3 install drgn 2>/dev/null \
	    || echo "WARNING: drgn install failed (non-fatal)"
	rm -rf /root/.cache/pip
else
	echo "--- Skipping drgn (cross-compile; install on target instead)"
fi
