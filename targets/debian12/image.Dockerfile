ARG BASE_IMAGE=ubuntu:24.04
FROM debian:12

# Ubuntu 24.04 VM base image.
# Built via podman, exported to raw ext4 for QEMU microvm use.
# No kernel installed -- QEMU passes it externally via -kernel.

ENV DEBIAN_FRONTEND=noninteractive

# Copy package lists
COPY debian12/packages-os.txt /tmp/packages-os.txt

# Install Ubuntu packages.
# The common package lists use RHEL names; translate to Ubuntu equivalents.
RUN apt-get update \
    && apt-get install -y \
        bash coreutils systemd passwd hostname kmod util-linux \
        less findutils procps tar gzip xz-utils sudo \
        network-manager iproute2 iputils-ping \
        openssh-server openssh-client \
        vim-tiny vim \
        rsync e2fsprogs dmsetup \
        kexec-tools crash kdump-tools \
        python3 python3-pip python3-dev \
        jq lsof psmisc \
        openmpi-bin libopenmpi-dev \
        fio bonnie++ dbench \
        attr acl bc perl pdsh \
        nfs-common sg3-utils quota \
        iperf3 \
        sysstat valgrind \
        bpfcc-tools bpftrace \
        trace-cmd systemtap strace ltrace \
        ethtool tcpdump conntrack \
        numactl hwloc \
        blktrace iproute2 iotop \
        htop tmux \
        gcc g++ make autoconf automake libtool git curl \
    && rm -rf /var/lib/apt/lists/*

# Copy shared setup scripts
COPY common/build-e2fsprogs.sh /tmp/build-e2fsprogs.sh
COPY common/build-tools.sh     /tmp/build-tools.sh
COPY common/setup-ssh.sh       /tmp/setup-ssh.sh
COPY common/setup-serial.sh    /tmp/setup-serial.sh
COPY common/rc.local           /etc/rc.d/rc.local
COPY common/setup-network.sh   /tmp/setup-network.sh
COPY common/setup-kdump.sh     /tmp/setup-kdump.sh
COPY common/setup-services.sh  /tmp/setup-services.sh

# Source-built tools: IOR, mdtest, iozone, pjdfstest, FlameGraph, drgn
RUN bash /tmp/build-tools.sh

# Lustre-patched e2fsprogs (pinned release)
RUN bash /tmp/build-e2fsprogs.sh v1.47.3-wc2

# System configuration
RUN bash /tmp/setup-ssh.sh
RUN bash /tmp/setup-serial.sh
RUN bash /tmp/setup-network.sh
RUN bash /tmp/setup-kdump.sh
RUN bash /tmp/setup-services.sh unattended-upgrades.service snapd.service snapd.socket

# Clean up
RUN apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/*
