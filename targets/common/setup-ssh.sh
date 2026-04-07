#!/usr/bin/env bash
# Configure SSH for passwordless root access and inter-VM connectivity.
#
# - Enables sshd
# - Allows root login with empty password
# - Generates persistent SSH host keys
# - Creates a shared ed25519 key so VMs can SSH to each other without prompts
set -euo pipefail

# Enable sshd (service name differs: sshd on RHEL, ssh on Debian)
if systemctl list-unit-files sshd.service &>/dev/null; then
	systemctl enable sshd
else
	systemctl enable ssh
fi

# Allow root login with empty password
# Use sshd_config.d if available (Ubuntu 24.04+), else append to main config
if [[ -d /etc/ssh/sshd_config.d ]]; then
	cat > /etc/ssh/sshd_config.d/99-ltvm.conf <<'SSHEOF'
PermitRootLogin yes
PermitEmptyPasswords yes
SSHEOF
else
	echo "PermitRootLogin yes"      >> /etc/ssh/sshd_config
	echo "PermitEmptyPasswords yes" >> /etc/ssh/sshd_config
fi

# Clear root password
passwd -d root

# Generate host keys so they persist across boots
ssh-keygen -A

# Shared inter-VM key: all VMs share the same ed25519 keypair so any
# VM can SSH to any other without needing to exchange keys at runtime.
mkdir -p /root/.ssh
chmod 700 /root/.ssh
ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N "" -q
cp /root/.ssh/id_ed25519.pub /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

cat > /root/.ssh/config <<'SSHCFG'
Host *
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
SSHCFG
chmod 600 /root/.ssh/config
