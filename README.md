# lustre-test-vms-v2

Build infrastructure for Lustre development and testing using QEMU microVMs.

Produces three independent, cacheable artifacts per target OS:

1. **Build container** -- cross-compilation environment (GCC, rpm-build, etc.)
2. **Kernel** -- custom-built kernel + full build tree for Lustre module builds
3. **VM base image** -- minimal root filesystem for QEMU microvm boot

Lustre itself is built by the developer (on the host or in a container)
against the kernel build tree.

## Quick start

```bash
./ltvm init rocky9          # build container, kernel, image
./ltvm status               # show what's built

# Then use vm.sh / deploy-lustre.sh as usual
```

## Target OS support

| Target | Server | Client | Status |
|--------|--------|--------|--------|
| Rocky 8 | yes | yes | planned |
| Rocky 9 | yes | yes | planned |
| Rocky 10 | yes | yes | planned |
| Ubuntu 24.04 | no | yes | planned |

## Repository layout

```
targets/
  common/                   # shared package lists + config
    packages-base.txt       # packages for all targets
    packages-server.txt     # server-only packages (ZFS, ldiskfs tools)
    packages-dev.txt        # build-time deps (in container)
    packages-test.txt       # test runtime deps (IOR, dbench, etc.)
    kernel-config.fragment  # config overrides applied to all kernels
    image-setup.sh          # post-install setup common to all images
  rocky9/
    target.conf             # OS metadata, capabilities, versions
    kernel.conf             # kernel version + config overrides
    container.Dockerfile    # build container definition
    packages-os.txt         # OS-specific packages (beyond common)
    image.Dockerfile        # VM image definition
  rocky8/
    ...
  ubuntu2404/
    ...

output/                     # persistent build artifacts (gitignored)
  rocky9/
    container.tag
    kernel/
      vmlinux
      vmlinuz
      build-tree/
      meta.json
    image/
      base.ext4
      meta.json

ltvm                        # main entry point
```
