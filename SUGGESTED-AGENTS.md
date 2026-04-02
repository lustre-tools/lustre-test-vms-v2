# Suggested Agent Instructions for ltvm-based Lustre Development

Copy/adapt these sections into your project's CLAUDE.md
or AGENTS.md. Replace paths like `~/lustre-release` and
`~/lustre-test-vms-v2` with your actual locations.

## Building

### Kernel Build

The ltvm repo builds a kernel with the full build tree
needed for Lustre module compilation. The build tree
lives at `<ltvm-repo>/output/<target>/kernel/build-tree/`.

```bash
# One-time: build kernel + VM image for rocky9
cd ~/lustre-test-vms-v2
./ltvm init rocky9

# Check what's built
./ltvm status
```

### Lustre Build

Point fullbuild at the ltvm kernel build tree:

```bash
cd ~/lustre-release
fullbuild --with-linux=~/lustre-test-vms-v2/output/rocky9/kernel/build-tree
```

Incremental build: `make` (aliased to `sudo make -j16`
with ccache). Only needs `--with-linux` on the initial
configure; subsequent `make` runs remember it.

To build RPMs: `make rpms`.

### Deploy Workflow (QEMU microVMs)

QEMU microVMs provide persistent test environments.
Uses KVM acceleration with virtio-mmio devices.
Supports kdump for crash analysis. VMs survive
stop/start -- overlays and disks persist until
explicitly destroyed.

`ltvm` is the single entry point for VM lifecycle,
deployment, and execution. It replaces the older
`vm.sh` and `deploy-lustre.sh` tools.

#### Single-node VMs

```bash
# Create a test VM (also starts it)
sudo ltvm vm create --name co1-single \
    --vcpus 2 --mem 2048

# Create a VM with Lustre backing disks
sudo ltvm vm create --name co1-single \
    --vcpus 2 --mem 4096 --mdt-disks 1 --ost-disks 3

# Idempotent: create if missing, start if stopped
sudo ltvm vm ensure co1-single \
    --vcpus 2 --mem 4096 --mdt-disks 1 --ost-disks 3

# List VMs (status, disk usage, resource totals)
sudo ltvm vm list
sudo ltvm vm list --json

# Stop / start / restart (preserves overlay + disks)
sudo ltvm vm stop co1-single
sudo ltvm vm start co1-single
sudo ltvm vm restart co1-single

# Health check
sudo ltvm vm status co1-single

# Destroy the VM (deletes overlay + disks)
sudo ltvm vm destroy co1-single
```

#### Execution and file transfer

```bash
# Interactive SSH (passwordless by name)
ssh co1-single

# Non-interactive exec with timeout + exit codes
# Exit codes: 0=ok, 1=error, 2=not-found,
#             3=timeout, 4=unreachable
sudo ltvm exec --timeout 30 co1-single 'lctl dl'

# Copy files to/from VM
sudo ltvm cp-to co1-single local.txt /tmp/
sudo ltvm cp-from co1-single /tmp/out.txt .

# Kernel log
sudo ltvm dmesg --tail 100 co1-single
```

#### Deploy and Mount

`ltvm deploy` is idempotent -- safe to redeploy over
existing mounts (unmounts, unloads, cleans dm devices
first). When run from a Lustre build tree, `--build`
defaults to cwd.

```bash
# Deploy built Lustre to VM (from Lustre tree)
cd ~/lustre-release
sudo ltvm deploy co1-single

# Deploy and mount a single-node filesystem
sudo ltvm deploy co1-single --mount

# Explicit build path (from anywhere)
sudo ltvm deploy co1-single \
    --build ~/lustre-release --mount

# Deploy server-only (no client)
sudo ltvm deploy co1-single --mount --server-only
```

#### Multi-node Clusters

Clusters create multiple VMs with Lustre role
assignments. Node specs: `roles:vmname[:disks]`.
Roles: `mgs`, `mds`, `oss`, `client` (join with `+`).

```bash
# Combined MGS/MDS + OSS
sudo ltvm cluster create co2 \
    mgs+mds:co2-mds:1 oss:co2-oss:3

# Deploy + mount
sudo ltvm cluster deploy co2 \
    --build ~/lustre-release --mount

# Cluster info
sudo ltvm cluster list
sudo ltvm cluster status co2

# Exec on a node by role
sudo ltvm cluster exec co2 oss 'lctl dl'

# Destroy cluster + all VMs
sudo ltvm cluster destroy co2
```

**Cluster deploy details:** rsyncs the full build
tree to the same absolute path on all VMs. Installs
modules via depmod, libraries via ldconfig, key
binaries to /usr/sbin. Generates cfg/local.sh with
correct host, device, NID, and PDSH configuration
for the test framework's multi-node support.

#### VM Naming Convention

VM names MUST include the checkout number to avoid
conflicts between parallel sessions.
Format: `co<N>-<role>`.

Never use bare names like `testvm` or `myvm` --
these collide when multiple checkouts are in use.

#### VM Networking and Discovery

- VMs resolve each other by name via dnsmasq on
  the host bridge (`192.168.100.1`)
- `/etc/hosts` entries added on create, removed on
  destroy; dnsmasq SIGHUPed automatically
- Shared inter-VM SSH key baked into base image --
  `ssh othervmname` works from inside any VM
- Host SSH config: `ServerAliveInterval=1`,
  `ServerAliveCountMax=2`, `ConnectTimeout=5` --
  dead VMs detected in seconds

#### VM Internals

- QEMU binary: `/opt/qemu/bin/qemu-system-x86_64`
- Base image: `<ltvm-repo>/output/<target>/image/base.ext4`
- Kernel: `<ltvm-repo>/output/<target>/kernel/vmlinux`
- Kernel build tree:
  `<ltvm-repo>/output/<target>/kernel/build-tree/`
- qcow2 overlays in `/opt/qemu-vms/overlays/`
- TAP devices on `fcbr0` bridge, `192.168.100.0/24`
- IPs derived from VM name (md5 hash -> last octet)
- After host reboot: `sudo ltvm vm start-all`
  (no auto-start)
- Requires root/sudo for TAP and QEMU operations

#### Base Image Tools

Pre-installed in the base image:

- **Profiling:** perf, bcc-tools, bpftrace, trace-cmd,
  systemtap, valgrind, sysstat, kernel-tools
- **I/O:** fio, blktrace, ioprofile, iotop
- **Benchmarks:** IOR, mdtest, iozone, dbench, bonnie++,
  pjdfstest
- **MPI:** openmpi, openmpi-devel
- **Tracing:** strace, ltrace
- **Flamegraphs:** `/usr/local/FlameGraph/`
- **Dev:** vim, git, gcc, gdb
- **Test deps:** attr, acl, bc, rsync, perl, pdsh,
  pdsh-rcmd-ssh, nfs-utils, sg3_utils

## Testing

### Test Environment

Testing is done inside QEMU microVMs. Building is done
on the host; loading/unloading Lustre and running tests
happens inside the VM.

Single-node (checkout 1):
```bash
sudo ltvm vm ensure co1-single \
    --vcpus 2 --mem 4096 --mdt-disks 1 --ost-disks 3
sudo ltvm deploy co1-single \
    --build ~/lustre-release --mount
```

Multi-node cluster (checkout 2):
```bash
sudo ltvm cluster create co2 \
    mgs+mds:co2-mds:1 oss:co2-oss:3
sudo ltvm cluster deploy co2 \
    --build ~/lustre-release --mount
```

### Loading and Unloading Lustre

**Inside the VM** (client mounts at `/mnt/lustre`):
```bash
sudo bash lustre/tests/llmount.sh
sudo bash lustre/tests/llmountcleanup.sh && \
    sudo lustre_rmmod
```

If `llmount.sh` mkfs fails, try
`sudo dmsetup remove_all` first.

**Destroy/recreate is often faster than cleanup.**
`llmountcleanup.sh` can be slow or hang. If you
don't need to preserve logs or other VM state:
```bash
sudo ltvm vm destroy co1-single
sudo ltvm vm ensure co1-single \
    --vcpus 2 --mem 4096 --mdt-disks 1 --ost-disks 3
sudo ltvm deploy co1-single \
    --build ~/lustre-release --mount
```

### Running Tests

**Inside the VM:**
```bash
sudo -E ONLY=42a bash \
    lustre/tests/sanity.sh
```
**Auster:** `sudo -E auster -s sanity --only 42a`

Auster logs: `/tmp/test_logs/YYYY-MM-DD/HHMMSS/`

### Testing Notes

- When running something that might hang, use
  `timeout <seconds> <command>` or run in the
  background with a timeout.
- If a test VM is stuck (hung mount, blocked cleanup),
  just destroy and recreate it -- VMs are ephemeral.
- Print output directly / save to bash variables
  rather than redirecting to temp files.
- Use stack_trap to reset configuration after test.

## Debugging

### Debug Log Quick Reference

Lustre uses an in-kernel ring buffer (per-CPU,
default ~5 MB/CPU). All CDEBUG/CERROR output goes
here.

**Basic capture workflow:**
```bash
lctl set_param debug=-1
lctl set_param debug_mb=10000
lctl clear
lctl mark "before repro"
# ... reproduce ...
lctl dk /tmp/dk.log
```

Set debug_mb extremely large -- kernel clamps to max.

**From the host:**
```bash
sudo ltvm exec co1-single \
    'lctl set_param debug=-1 && lctl clear'
# ... reproduce ...
sudo ltvm exec co1-single 'lctl dk /tmp/dk.log'
sudo ltvm cp-from co1-single /tmp/dk.log .
```

### kdump / Crash Analysis

VMs boot with `crashkernel=512M`. The base image has
kexec-tools, crash, drgn, and vmlinux with debug
symbols. After a kernel panic, kdump saves a vmcore
to `/var/crash/` and reboots.

**Triggering a crash:**
```bash
sudo ltvm exec co1-single \
    'echo c > /proc/sysrq-trigger'
```
VM reboots after kdump completes (~15s).

**Collecting and analyzing:**
```bash
sudo ltvm cp-from co1-single \
    /var/crash/latest/vmcore /tmp/

crash-tool recipes lustre \
    --vmcore /tmp/vmcore \
    --vmlinux <ltvm-repo>/output/rocky9/kernel/vmlinux \
    --mod-dir ~/lustre-release
```

**Use drgn, not crash.** drgn provides typed Lustre
struct traversal, local variable extraction, dk log,
kernel log, LDLM lock enumeration, OBD device listing,
and structured JSON output.

**Key notes:**
- `--mod-dir <build-tree>` loads Lustre module debug
  symbols. Build tree .ko files have debug_info.
- drgn runs from the host -- copy vmcore out with
  `ltvm cp-from`.
- vmlinux is at
  `<ltvm-repo>/output/<target>/kernel/vmlinux`

**Parallel testing:** use separate numbered checkouts
and separate VMs (or separate clusters).
