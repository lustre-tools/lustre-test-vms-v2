#!/usr/bin/env bash
# Configure kdump for near-complete crash dumps.
#
# Writes dumps to /var/crash. Use vm.py crash-collect to retrieve.
# crashkernel=512M must be in QEMU kernel cmdline (set by vm.py).
set -euo pipefail

mkdir -p /var/crash

cat > /etc/kdump.conf <<'EOF'
path /var/crash
core_collector makedumpfile -l --message-level 7 -d 1
EOF

cat > /etc/sysconfig/kdump <<'EOF'
KDUMP_KERNELVER=""
KDUMP_COMMANDLINE_REMOVE="hugepages hugepagesz slub_debug quiet log_buf_len swiotlb"
KDUMP_COMMANDLINE_APPEND="irqpoll nr_cpus=1 reset_devices cgroup_disable=memory mce=off numa=off udev.children-max=2 panic=10 rootflags=nofail acpi_no_memhotplug transparent_hugepage=never nokaslr"
KEXEC_ARGS=""
KDUMP_IMG=vmlinuz
EOF

systemctl enable kdump
