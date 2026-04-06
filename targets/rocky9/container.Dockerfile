FROM rockylinux:9

# Rocky 9 build container for kernel and Lustre builds.
# GCC 11 matches the EL9 5.14.0 kernel build environment.

# Enable CRB + EPEL repos
RUN dnf -y install dnf-plugins-core epel-release \
    && dnf config-manager --set-enabled crb

# Kernel build dependencies
RUN dnf -y install \
        rpm-build \
        gcc gcc-c++ make bc bison flex \
        elfutils-libelf-devel elfutils-devel \
        openssl-devel \
        perl-interpreter perl-Carp perl-devel \
        perl-generators \
        ncurses-devel dwarves \
        net-tools hostname diffutils findutils \
        python3 python3-devel \
        rsync tar gzip xz bzip2 \
        kmod \
    && dnf clean all

# Lustre build dependencies (autogen + configure + make)
RUN dnf -y install \
        autoconf automake libtool git patch \
        libyaml-devel libnl3-devel libmount-devel \
        libselinux-devel zlib-devel \
        kernel-rpm-macros texinfo \
    && dnf -y install nasm 2>/dev/null || true \
    && dnf clean all

# Whamcloud-patched e2fsprogs (required for server builds).
# ldiskfs/mkfs.lustre needs ext2fs >= 1.47.3-wc2.
# Auto-discovers the latest v*-wc* release tag and builds
# from source so the container always has a released version.
RUN E2FS_REPO=https://review.whamcloud.com/tools/e2fsprogs \
    && TAG=$(git ls-remote --tags "$E2FS_REPO" 'refs/tags/v*wc*' \
        | grep -v '\^{}' \
        | awk '{print $2}' \
        | sed 's|refs/tags/||' \
        | sort -V \
        | tail -1) \
    && echo "e2fsprogs: using tag $TAG" \
    && git clone --depth 1 --branch "$TAG" \
        "$E2FS_REPO" /tmp/e2fsprogs \
    && cd /tmp/e2fsprogs \
    && ./configure --prefix=/usr --with-root-prefix="" \
        --enable-elf-shlibs --disable-uuidd \
    && make -j$(nproc) \
    && make install \
    && make install-libs \
    && ldconfig \
    && echo "e2fsprogs: $(pkg-config --modversion ext2fs)" \
    && cd / && rm -rf /tmp/e2fsprogs

# ccache for faster rebuilds
RUN dnf -y install ccache \
    && dnf clean all
ENV PATH="/usr/lib64/ccache:${PATH}"
ENV CCACHE_DIR="/ccache"

WORKDIR /build
ENTRYPOINT ["/bin/bash"]
