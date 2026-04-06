"""Package, fetch, and install target artifacts.

One package per target containing everything needed to boot
VMs and build Lustre:
  - vmlinux / vmlinuz (kernel for QEMU direct boot + kdump)
  - modules/ (kernel modules deployed into VMs)
  - build-tree/ (kernel source for compiling Lustre)
  - image.ext4 (VM rootfs)
  - meta.json (version, build info)

Users can replace the kernel later with `ltvm build-kernel`
if they need custom patches or a different version.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path


def _find_artifacts(output_dir):
    """Verify all expected artifacts exist in output_dir.

    Returns dict of paths or raises ValueError.
    """
    output_dir = Path(output_dir)
    kernel_dir = output_dir / "kernel"
    image_dir = output_dir / "image"

    required = {
        "vmlinux": kernel_dir / "vmlinux",
        "vmlinuz": kernel_dir / "vmlinuz",
        "build-tree": kernel_dir / "build-tree",
        "modules": kernel_dir / "modules",
    }

    # Image can be .ext4 or .img
    image_files = list(image_dir.glob("*.ext4")) + list(image_dir.glob("*.img"))
    if image_files:
        required["image"] = image_files[0]

    missing = []
    for name, path in required.items():
        if not path.exists():
            missing.append(name)

    if "image" not in required:
        missing.append("image (*.ext4 or *.img)")

    if missing:
        raise ValueError(
            f"Missing artifacts in {output_dir}: "
            f"{', '.join(missing)}\n"
            f"Run 'ltvm build-all <target>' to build them"
        )

    return required


def package_target(target_name, output_dir, dest_dir=None):
    """Create a compressed tarball of all target artifacts.

    Returns the path to the created tarball.
    """
    output_dir = Path(output_dir)
    artifacts = _find_artifacts(output_dir)

    if dest_dir is None:
        dest_dir = output_dir.parent
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Read version from kernel meta
    kernel_meta = output_dir / "kernel" / "meta.json"
    version = "unknown"
    if kernel_meta.exists():
        meta = json.loads(kernel_meta.read_text())
        version = meta.get("kernel_version", "unknown")

    tarball = dest_dir / f"{target_name}-{version}.tar.zst"

    print(f"  Packaging {target_name} -> {tarball.name}")
    print(f"    Kernel: {artifacts['vmlinux']}")
    print(f"    Image:  {artifacts['image']}")

    # Use tar + zstd via subprocess for streaming compression
    # (Python's tarfile doesn't support zstd natively on
    # older Python)
    cmd = [
        "tar",
        "--zstd",
        "-cf",
        str(tarball),
        "-C",
        str(output_dir.parent),
        target_name,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        # Fallback: try gzip if zstd not available
        tarball = dest_dir / f"{target_name}-{version}.tar.gz"
        cmd = [
            "tar",
            "-czf",
            str(tarball),
            "-C",
            str(output_dir.parent),
            target_name,
        ]
        subprocess.run(cmd, check=True)

    size_mb = tarball.stat().st_size / (1024 * 1024)
    print(f"    Size: {size_mb:.0f} MB")

    # Write a manifest alongside the tarball
    manifest = {
        "target": target_name,
        "kernel_version": version,
        "contents": list(artifacts.keys()),
        "size_bytes": tarball.stat().st_size,
    }
    manifest_path = tarball.with_suffix(tarball.suffix + ".json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    return tarball


def fetch_target(target_name, url, output_base):
    """Download and extract a target package.

    url: direct URL to the tarball
    output_base: parent of output/<target>/ (usually the
                 ltvm repo root's output/ directory)
    """
    output_base = Path(output_base)
    output_base.mkdir(parents=True, exist_ok=True)

    print(f"  Fetching {target_name} from {url}...")

    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # Download with curl (shows progress)
        r = subprocess.run(
            ["curl", "-fSL", "--progress-bar", "-o", tmp_path, url], check=False
        )
        if r.returncode != 0:
            raise RuntimeError(f"Download failed (rc={r.returncode}): {url}")

        size_mb = os.path.getsize(tmp_path) / (1024 * 1024)
        print(f"    Downloaded: {size_mb:.0f} MB")

        # Extract -- auto-detects compression
        print(f"    Extracting to {output_base}/...")
        subprocess.run(
            ["tar", "-xf", tmp_path, "-C", str(output_base)], check=True
        )

    finally:
        os.unlink(tmp_path)

    target_dir = output_base / target_name
    if not target_dir.is_dir():
        raise RuntimeError(
            f"Expected {target_dir} after extraction but not found"
        )

    # Verify the artifacts are there
    _find_artifacts(target_dir)
    print(f"    Artifacts verified in {target_dir}")

    return target_dir


# Default install paths (matching vm.sh conventions)
DEFAULT_KERNEL_DIR = Path("/opt/qemu-vms/kernel")
DEFAULT_IMAGE_DIR = Path("/opt/qemu-vms/images")


def install_target(target_name, output_dir, *, kernel_dir=None, image_dir=None):
    """Install kernel + image to system paths for vm.sh.

    This requires sudo (writes to /opt/qemu-vms/).
    Returns dict of installed paths.
    """
    output_dir = Path(output_dir)
    artifacts = _find_artifacts(output_dir)

    kernel_dir = Path(kernel_dir or DEFAULT_KERNEL_DIR)
    image_dir = Path(image_dir or DEFAULT_IMAGE_DIR)

    installed = {}

    # Install kernel
    vmlinux_dest = kernel_dir / f"vmlinux-{target_name}"
    vmlinuz_dest = kernel_dir / f"vmlinuz-{target_name}"

    print(f"  Installing kernel to {kernel_dir}/")
    subprocess.run(["sudo", "mkdir", "-p", str(kernel_dir)], check=True)
    subprocess.run(
        ["sudo", "cp", str(artifacts["vmlinux"]), str(vmlinux_dest)], check=True
    )
    subprocess.run(
        ["sudo", "cp", str(artifacts["vmlinuz"]), str(vmlinuz_dest)], check=True
    )

    # Also install as the default vmlinux if no default exists
    # or if this is the only target
    default_vmlinux = kernel_dir / "vmlinux"
    if not default_vmlinux.exists():
        subprocess.run(
            ["sudo", "ln", "-sf", str(vmlinux_dest), str(default_vmlinux)],
            check=True,
        )
        print(f"    Default kernel -> vmlinux-{target_name}")

    installed["vmlinux"] = str(vmlinux_dest)
    installed["vmlinuz"] = str(vmlinuz_dest)

    # Install image
    image_src = artifacts["image"]
    image_dest = image_dir / f"{target_name}-ltvm.ext4"

    print(f"  Installing image to {image_dir}/")
    subprocess.run(["sudo", "mkdir", "-p", str(image_dir)], check=True)
    subprocess.run(["sudo", "cp", str(image_src), str(image_dest)], check=True)

    installed["image"] = str(image_dest)

    print("  Installed:")
    for k, v in installed.items():
        print(f"    {k}: {v}")

    return installed
