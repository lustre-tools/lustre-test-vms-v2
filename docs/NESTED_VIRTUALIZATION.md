# Enabling Nested Virtualization

ltvm runs QEMU/KVM virtual machines. If your host is itself a VM
(cloud instance, Hyper-V, VMware, etc.), you need **nested
virtualization** enabled so the inner VMs can use hardware
acceleration.

Without KVM, QEMU falls back to software emulation which is
~100x slower and not supported by ltvm.

## Quick Diagnosis

```bash
ls /dev/kvm          # should exist
lscpu | grep -i virt # should show VT-x or AMD-V
```

If `/dev/kvm` is missing, follow the instructions for your
platform below.

---

## Linux on Bare Metal

KVM should work out of the box. If `/dev/kvm` is missing:

```bash
# Check BIOS/UEFI settings
grep -E 'vmx|svm' /proc/cpuinfo

# Load the KVM module
sudo modprobe kvm_intel   # Intel
sudo modprobe kvm_amd     # AMD

# If modprobe fails, enable VT-x/AMD-V in BIOS
```

## WSL2 (Windows)

From an **elevated PowerShell** on the Windows host:

```powershell
# Find your WSL VM
wsl -l -v

# Enable nested virt (requires Hyper-V)
Set-VMProcessor -VMName "WSL" -ExposeVirtualizationExtensions $true

# Restart WSL
wsl --shutdown
```

Then verify inside WSL: `ls /dev/kvm`

**Note:** WSL2 nested virt requires Windows 11 or Windows 10
21H2+ with Hyper-V enabled. Older Windows versions do not
support this.

## VMware (ESXi / Workstation / Fusion)

### ESXi (vSphere)

Edit the VM settings:
1. VM > Edit Settings > CPU
2. Check "Expose hardware-assisted virtualization to the guest OS"
3. Power cycle the VM (not just reboot)

Or via CLI:
```
vhv.enable = "TRUE"
```

### VMware Workstation / Fusion

VM Settings > Processors > "Virtualize Intel VT-x/EPT or AMD-V/RVI"

## KVM (libvirt / virsh)

Nested virt must be enabled in the host's KVM module:

```bash
# Check current setting on the HOST
cat /sys/module/kvm_intel/parameters/nested    # Intel
cat /sys/module/kvm_amd/parameters/nested      # AMD

# Enable (temporary)
sudo modprobe -r kvm_intel
sudo modprobe kvm_intel nested=1

# Enable (permanent)
echo 'options kvm_intel nested=1' | sudo tee /etc/modprobe.d/kvm-nested.conf
```

Then ensure the guest VM's XML has:
```xml
<cpu mode='host-passthrough'/>
```

Or via virsh:
```bash
virsh edit <vm-name>
# Change <cpu> to: <cpu mode='host-passthrough'/>
# Then: virsh destroy <vm> && virsh start <vm>
```

## Hyper-V (Azure / on-premises)

### Azure VMs

Use a **v5 or newer** VM size that supports nested virt:
- Dv5, Dsv5, Ev5, etc.
- **Not supported:** Dv2, Dv3, Dv4, Bv1, Fsv2

Check: `az vm list-skus --location <region> --query "[?capabilities[?name=='HyperVGenerations' && value=='V2']]"`

Nested virt is automatic on supported sizes -- no config needed.

### On-premises Hyper-V

From **elevated PowerShell** on the Hyper-V host:

```powershell
Set-VMProcessor -VMName <name> -ExposeVirtualizationExtensions $true
# Power cycle the VM
```

## AWS EC2

Use a **metal** instance type (e.g. `m5.metal`, `c5.metal`).
Regular EC2 instances do not support nested KVM.

Alternatively, `.metal` instances have direct hardware access
and KVM works natively (not technically "nested").

## Google Cloud (GCE)

Nested virt is supported on Haswell+ CPUs. Enable it when
creating the VM:

```bash
gcloud compute instances create <name> \
    --enable-nested-virtualization \
    --min-cpu-platform="Intel Haswell"
```

Or for an existing instance (must be stopped):
```bash
gcloud compute instances update <name> \
    --enable-nested-virtualization
```

## macOS (Apple Silicon / Intel)

### Apple Silicon (M1/M2/M3/M4) -- aarch64

Apple Silicon Macs don't have x86 hardware, so x86 KVM is not
possible. Two options:

1. **Run ltvm natively on aarch64.** ltvm supports aarch64 targets
   (rocky9, ubuntu2404). Use a Linux VM on your Mac
   (UTM, Parallels, VMware Fusion) with an **aarch64 Linux guest**,
   then run ltvm inside that. KVM works because the guest arch
   matches the host (ARM virtualization).

2. **Use a remote x86 machine.** SSH to a cloud instance or
   lab server and run ltvm there.

The key: the **guest architecture must match the host**. An aarch64
Mac can run aarch64 KVM guests but not x86_64 KVM guests.

VM frameworks for aarch64 Linux on Apple Silicon:
- **UTM** (free, QEMU-based): enable "Use Apple Virtualization"
  for KVM-equivalent performance
- **Parallels Desktop**: full nested virt support
- **VMware Fusion**: supports nested virt on ARM

### Intel Mac

Use VMware Fusion, Parallels, or UTM with an x86_64 Linux guest.
Enable nested virtualization in the VM settings (same as VMware
Workstation instructions above).

## Troubleshooting

### `/dev/kvm` exists but permission denied

```bash
sudo chmod 666 /dev/kvm
# Or add your user to the kvm group:
sudo usermod -aG kvm $USER
# Then log out and back in
```

### Module loads but `/dev/kvm` not created

```bash
# Check for errors
dmesg | grep -i kvm
# Common: BIOS has VT-x disabled even though CPU supports it
```

### Everything looks right but VMs won't start

```bash
# Test KVM directly
/opt/qemu/bin/qemu-system-x86_64 -machine accel=kvm -nographic -no-reboot \
    -kernel /boot/vmlinuz-$(uname -r) -append "console=ttyS0 panic=1" &
# Should boot briefly and panic (no rootfs) -- proves KVM works
# Kill it: kill %1
```

If this fails with "Could not access KVM kernel module", the
issue is in the hypervisor configuration, not the guest.
