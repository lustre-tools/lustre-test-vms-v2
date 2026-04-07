#!/usr/bin/env bash
# Disable slow or unnecessary services and kernel modules.
#
# Pass additional service names to mask as arguments, e.g.:
#   setup-services.sh dnf-makecache.timer
set -euo pipefail

# Always mask these in every VM image
MASK_ALWAYS=(
    systemd-hwdb-update
    firewalld
)

# Caller can pass extra services to mask (distro-specific timers, etc.)
MASK_EXTRA=("$@")

systemctl mask "${MASK_ALWAYS[@]}" "${MASK_EXTRA[@]}" 2>/dev/null || true

# Blacklist DRM -- no display hardware in microvm
mkdir -p /etc/modprobe.d
cat > /etc/modprobe.d/no-drm.conf <<'EOF'
blacklist drm
EOF
