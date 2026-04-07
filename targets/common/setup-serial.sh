#!/usr/bin/env bash
# Configure serial console for automatic root login on ttyS0.
# Required for QEMU microvm direct kernel boot (no display, no VNC).
set -euo pipefail

mkdir -p /etc/systemd/system/serial-getty@ttyS0.service.d

cat > /etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root -o '-p -f root' --keep-baud 115200,38400,9600 %I $TERM
EOF
