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

TARGET_ARCH="${TARGET_ARCH:-$(uname -m)}"
HOST_ARCH="$(uname -m)"
DESTDIR="${DESTDIR:-}"
PREFIX="${DESTDIR}/usr/local"

# Cross-compilation setup (only set CC/CXX when cross-compiling;
# leave them unset for native builds so mpicc wrappers work)
CONFIGURE_HOST=""

if [[ "$TARGET_ARCH" == "aarch64" && "$HOST_ARCH" != "aarch64" ]]; then
	CONFIGURE_HOST="--host=aarch64-linux-gnu"
	export CC="aarch64-linux-gnu-gcc" CXX="aarch64-linux-gnu-g++"
	echo "--- Cross-compiling tools: ${HOST_ARCH} -> aarch64"
elif [[ "$TARGET_ARCH" == "x86_64" && "$HOST_ARCH" != "x86_64" ]]; then
	CONFIGURE_HOST="--host=x86_64-linux-gnu"
	export CC="x86_64-linux-gnu-gcc" CXX="x86_64-linux-gnu-g++"
	echo "--- Cross-compiling tools: ${HOST_ARCH} -> x86_64"
fi

# Ensure build deps are present (may have been skipped by --skip-broken)
if command -v dnf &>/dev/null; then
	dnf -y install gcc gcc-c++ make autoconf automake libtool git curl \
		python3-pip 2>/dev/null || true
elif command -v apt-get &>/dev/null; then
	apt-get update && apt-get install -y gcc g++ make autoconf automake \
		libtool git curl python3-pip 2>/dev/null || true
fi

# Install cross-compiler if cross-compiling
if [[ -n "$CONFIGURE_HOST" ]]; then
	if command -v dnf &>/dev/null; then
		dnf -y install gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu 2>/dev/null || true
	elif command -v apt-get &>/dev/null; then
		apt-get install -y gcc-aarch64-linux-gnu g++-aarch64-linux-gnu 2>/dev/null || true
	fi
fi

mkdir -p "$PREFIX/bin"
cd /tmp

# IOR + mdtest
# Add openmpi to PATH if available (EL installs to /usr/lib64/openmpi/bin)
if [[ -d /usr/lib64/openmpi/bin ]]; then
	export PATH=/usr/lib64/openmpi/bin:$PATH
	export LD_LIBRARY_PATH="/usr/lib64/openmpi/lib:${LD_LIBRARY_PATH:-}"
fi
curl -sL https://github.com/hpc/ior/releases/download/4.0.0/ior-4.0.0.tar.gz | tar xz
cd ior-4.0.0 && ./configure $CONFIGURE_HOST && make -j"$(nproc)"
cp src/ior src/mdtest "$PREFIX/bin/"
cd /tmp && rm -rf ior-4.0.0

# iozone (needs -Wno-error=implicit-* for GCC 14+)
curl -sL http://www.iozone.org/src/current/iozone3_506.tar | tar xf -
cd iozone3_506/src/current
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
cd /tmp && rm -rf iozone3_506

# pjdfstest
git clone https://github.com/pjd/pjdfstest.git
cd pjdfstest && autoreconf -ifs && ./configure $CONFIGURE_HOST && make -j"$(nproc)"
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
