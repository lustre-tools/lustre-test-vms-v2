ARG BASE_IMAGE=rockylinux:8.9
FROM ${BASE_IMAGE}

# Rocky 8 build container for kernel and Lustre builds.
# GCC 8 matches the EL8 4.18.0 kernel build environment.

# Enable PowerTools + EPEL repos (PowerTools = CRB on EL8).
# findutils is not in the minimal rocky:8.9 image but the bulk
# package-install below uses xargs, so pull it in up front.
RUN dnf -y install dnf-plugins-core epel-release findutils \
    && dnf config-manager --set-enabled powertools

# Install build packages from the canonical shared list.
# pkgconf-pkg-config is named pkgconfig on EL8 -- skip-broken handles
# the rename without us needing a per-target package list yet.
COPY common/packages-dev.txt /tmp/packages-dev.txt
RUN cat /tmp/packages-dev.txt \
        | grep -v '^\s*#' | grep -v '^\s*$' \
        | sort -u \
        | xargs dnf -y --allowerasing --skip-broken install \
    && dnf -y install pkgconfig 2>/dev/null || true \
    && dnf clean all && rm -f /tmp/packages-dev.txt

# Whamcloud-patched e2fsprogs (required for server builds).
COPY common/build-e2fsprogs.sh /tmp/build-e2fsprogs.sh
RUN bash /tmp/build-e2fsprogs.sh && rm /tmp/build-e2fsprogs.sh

# Cross-compilers for the opposite arch (best-effort; see rocky9).
# EL8 EPEL coverage is thinner than el9/el10, so expect either or both
# of these to be no-ops on el8 -- the inner build script falls back to
# a runtime install if needed.
RUN dnf -y install gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu 2>/dev/null || true \
    && dnf -y install gcc-x86_64-linux-gnu binutils-x86_64-linux-gnu 2>/dev/null || true \
    && dnf clean all

ENV PATH="/usr/lib64/ccache:${PATH}"
ENV CCACHE_DIR="/ccache"

WORKDIR /build
ENTRYPOINT ["/bin/bash"]
