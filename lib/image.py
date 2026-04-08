"""VM base image builder.

Builds a QEMU microvm root filesystem image for a given target
by building a container image (Dockerfile) and exporting it to
raw ext4.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import TARGETS_DIR, arch_deb

if TYPE_CHECKING:
    from .config import TargetConfig

log = logging.getLogger(__name__)

# Image sizing:
#   _IMAGE_SIZE_MB: initial mke2fs allocation (must fit all packages + build outputs)
#   _IMAGE_HEADROOM_MB: free space added back after resize2fs -M shrink,
#     so the running VM has room for Lustre modules (~500 MB debug) + runtime data.
_IMAGE_SIZE_MB = 8192
_IMAGE_HEADROOM_MB = 4096  # 4 GiB headroom in running VM


def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run a command, logging it and raising on failure."""
    log.info("Running: %s", " ".join(str(c) for c in cmd))
    return subprocess.run(
        cmd,
        check=True,
        capture_output=kwargs.pop("capture_output", True),
        text=kwargs.pop("text", True),
        **kwargs,
    )


def _check_mke2fs() -> None:
    """Verify mke2fs supports -d (populate from directory)."""
    result = subprocess.run(["mke2fs", "-V"], capture_output=True, text=True)
    # -d support was added in e2fsprogs 1.43 (2016)
    version_str = result.stderr + result.stdout
    if "mke2fs" not in version_str:
        raise RuntimeError("mke2fs not found; install e2fsprogs")


def _container_image_tag(target_config: TargetConfig) -> str:
    if target_config.arch != "x86_64":
        return f"ltvm-image-{target_config.name}-{target_config.arch}"
    return f"ltvm-image-{target_config.name}"


def _is_cross_build(target_config: TargetConfig) -> bool:
    """True if the target arch differs from the host."""
    import platform
    return target_config.arch != platform.machine()


def _podman_platform(target_config: TargetConfig) -> list[str]:
    """Return --platform flag for podman if cross-arch build needed."""
    if not _is_cross_build(target_config):
        return []
    _PLAT = {"aarch64": "linux/arm64", "x86_64": "linux/amd64"}
    plat = _PLAT.get(target_config.arch)
    return ["--platform", plat] if plat else []


def _prebuild_tools_native(
    target_config: TargetConfig,
    output_dir: Path,
) -> None:
    """Cross-compile IOR, iozone, pjdfstest, e2fsprogs using the
    host-native build container + cross-compiler.

    This avoids compiling under QEMU user-mode emulation by running
    the cross-compiler natively. The output goes to *output_dir* and
    is injected into the emulated image build via COPY.
    """
    # Use the existing (native) build container for this target
    build_tag = f"ltvm-build-{target_config.name}"
    arch = target_config.arch

    output_dir.mkdir(parents=True, exist_ok=True)

    # Ensure the native build container exists (may need to build it
    # if we're running as root but the container was built as a user)
    r = subprocess.run(
        ["podman", "image", "exists", build_tag], capture_output=True
    )
    if r.returncode != 0:
        log.info("Building native container %s for cross-compile...", build_tag)
        build_dockerfile = target_config.target_dir / "container.Dockerfile"
        subprocess.run(
            [
                "podman", "build",
                "-t", build_tag,
                "--build-arg", f"BASE_IMAGE={target_config.container_image}",
                "-f", str(build_dockerfile),
                str(TARGETS_DIR),
            ],
            check=True,
        )

    log.info(
        "Pre-building tools natively for %s (cross-compile in %s)...",
        arch, build_tag,
    )

    script = (
        f"export TARGET_ARCH={arch} DESTDIR=/output\n"
        "bash /input/build-tools.sh\n"
        "bash /input/build-e2fsprogs.sh\n"
    )

    common_dir = TARGETS_DIR / "common"
    cmd = [
        "podman", "run", "--rm",
        "-v", f"{common_dir}:/input:ro,Z",
        "-v", f"{output_dir}:/output:Z",
        build_tag,
        "-c", script,
    ]
    subprocess.run(cmd, check=True)
    log.info("Pre-built tools at %s", output_dir)


def build_image(target_config: TargetConfig, force: bool = False) -> Path:
    """Build a VM base image for the given target.

    Steps:
      1. Build container image via podman build
      2. Export container filesystem to raw ext4
      3. Write meta.json

    Args:
        target_config: TargetConfig instance
        force: rebuild even if inputs unchanged
    """
    _check_mke2fs()

    if not force and not target_config.is_stale("image"):
        log.info(
            "Image for %s is up to date, skipping (use force=True to rebuild)",
            target_config.name,
        )
        return target_config.image_output_dir() / "base.ext4"

    out_dir = target_config.image_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    image_path = out_dir / "base.ext4"

    tag = _container_image_tag(target_config)
    dockerfile = target_config.target_dir / "image.Dockerfile"
    if not dockerfile.exists():
        raise FileNotFoundError(
            f"No image.Dockerfile for target {target_config.name}"
        )

    t0 = time.monotonic()

    # ── Step 1: Build container image ──
    cross = _is_cross_build(target_config)
    platform_args = _podman_platform(target_config)
    effective_dockerfile = dockerfile
    prebuilt_dir: Path | None = None

    if cross:
        # Cross-build: compile tools natively, then inject into the
        # emulated image build (avoids slow compilation under QEMU).
        prebuilt_dir = out_dir / "_prebuilt"
        _prebuild_tools_native(target_config, prebuilt_dir)

        # Patch the Dockerfile: replace the RUN build-tools.sh and
        # build-e2fsprogs.sh steps with COPY from pre-built output.
        original = dockerfile.read_text()
        patched = original
        patched = patched.replace(
            "RUN bash /tmp/build-tools.sh",
            "COPY _prebuilt/usr/local/ /usr/local/",
        )
        patched = patched.replace(
            "RUN bash /tmp/build-e2fsprogs.sh v1.47.3-wc2",
            "COPY _prebuilt/usr/ /usr/",
        )
        patched = patched.replace(
            "RUN bash /tmp/build-e2fsprogs.sh",
            "COPY _prebuilt/usr/ /usr/",
        )

        # Write patched Dockerfile next to the original
        effective_dockerfile = out_dir / "image.Dockerfile.cross"
        effective_dockerfile.write_text(patched)
        log.info("Using patched Dockerfile for cross-build: %s", effective_dockerfile)

        # Build context needs to include the prebuilt dir.
        # We use out_dir as context and symlink targets/ content in.
        build_context = out_dir
        # Symlink the targets dirs into the build context
        for name in ("common", target_config.name):
            link = build_context / name
            link.unlink(missing_ok=True)
            try:
                link.symlink_to(TARGETS_DIR / name)
            except FileExistsError:
                pass
    else:
        build_context = TARGETS_DIR

    log.info("Building container image %s ...", tag)
    _run(
        [
            "podman", "build",
            *platform_args,
            "--build-arg", f"BASE_IMAGE={target_config.container_image}",
            "-t", tag,
            "-f", str(effective_dockerfile),
            str(build_context),
        ],
        capture_output=False,
    )

    # Find kernel modules and Lustre staging tree to bake into the image
    kernel_modules_dir = None
    lustre_staging_dir = None
    try:
        kernel_name = target_config.resolve_kernel(None)
        kdir = target_config.output_dir / "kernels" / kernel_name
        lib_mods = kdir / "modules" / "lib" / "modules"
        if lib_mods.is_dir():
            kernel_modules_dir = lib_mods
            log.info("Including kernel modules from %s", kdir / "modules")
        else:
            log.warning("No kernel modules found -- build kernel first for full image")
        staging = kdir / "lustre" / ".staging"
        if staging.is_dir():
            lustre_staging_dir = staging
            log.info("Including pre-built Lustre from %s", staging)
    except (ValueError, FileNotFoundError):
        log.warning("Could not resolve kernel -- image will not include kernel modules")

    # ── Step 2: Export to ext4 ──
    log.info("Exporting container to ext4 ...")
    image_path = _export_to_ext4(
        tag, image_path,
        kernel_modules_dir=kernel_modules_dir,
        lustre_staging_dir=lustre_staging_dir,
    )

    elapsed = time.monotonic() - t0

    # ── Step 3: Collect metadata ──
    size_mb = image_path.stat().st_size / (1024 * 1024)
    pkg_manifest = _get_package_manifest(tag, target_config.os_family)

    target_config.write_meta(
        "image",
        build_date=datetime.now(timezone.utc).isoformat(),
        build_seconds=round(elapsed, 1),
        image_size_mb=round(size_mb, 1),
        packages=pkg_manifest,
    )

    log.info("Image built: %s (%.0f MiB, %.0fs)", image_path, size_mb, elapsed)
    return image_path


def _export_to_ext4(
    container_tag: str,
    image_path: Path,
    kernel_modules_dir: Path | None = None,
    lustre_staging_dir: Path | None = None,
) -> Path:
    """Create a raw ext4 image from a container's filesystem.

    1. podman create from the image tag
    2. podman cp kernel modules + Lustre into the container
    3. podman export | fakeroot mke2fs -d → ext4
    4. resize2fs -M to shrink
    """
    tmpdir = None
    tmpfile = None
    container_id = None

    try:
        result = _run(["podman", "create", container_tag])
        container_id = result.stdout.strip()
        log.info("Container: %s", container_id[:12])

        # Inject kernel modules into the container
        if kernel_modules_dir and kernel_modules_dir.is_dir():
            for kver_dir in kernel_modules_dir.iterdir():
                if kver_dir.is_dir():
                    log.info("  Injecting modules: %s", kver_dir.name)
                    # Remove dangling symlinks before copy
                    for name in ("build", "source"):
                        link = kver_dir / name
                        if link.is_symlink():
                            link.unlink()
                    _run(["podman", "cp", str(kver_dir),
                          f"{container_id}:/lib/modules/{kver_dir.name}"])

        # Inject Lustre staging tree into the container
        # Only copy /usr/ and /lib/modules/ — not /sbin/ or /lib/
        # (those are FHS symlinks on EL that would be clobbered)
        if lustre_staging_dir and lustre_staging_dir.is_dir():
            staging_usr = lustre_staging_dir / "usr"
            staging_mods = lustre_staging_dir / "lib" / "modules"
            if staging_usr.is_dir():
                log.info("  Injecting Lustre userspace")
                _run(["podman", "cp", str(staging_usr) + "/.",
                      f"{container_id}:/usr/"])
            if staging_mods.is_dir():
                log.info("  Injecting Lustre kernel modules")
                _run(["podman", "cp", str(staging_mods) + "/.",
                      f"{container_id}:/lib/modules/"])
            # Create mount.lustre_tgt symlink
            subprocess.run(
                ["podman", "start", container_id], capture_output=True
            )
            subprocess.run(
                ["podman", "exec", container_id,
                 "ln", "-sf", "mount.lustre", "/usr/sbin/mount.lustre_tgt"],
                capture_output=True,
            )
            subprocess.run(
                ["podman", "exec", container_id, "ldconfig"],
                capture_output=True,
            )
            subprocess.run(
                ["podman", "stop", container_id], capture_output=True
            )

        # Export container to ext4
        tmpdir = tempfile.mkdtemp(prefix="ltvm-rootfs-")
        tmp_f = tempfile.NamedTemporaryFile(
            suffix=".ext4", prefix="ltvm-image-", delete=False
        )
        tmpfile = tmp_f.name
        tmp_f.close()

        qcid = shlex.quote(container_id)
        qtmp = shlex.quote(tmpdir)

        log.info("Exporting to ext4...")
        _run(
            [
                "bash", "-c",
                f"podman export {qcid} "
                f"| fakeroot bash -c '"
                f"tar -C {qtmp} -xf - --exclude=dev/* "
                f"&& mkdir -p {qtmp}/dev/pts {qtmp}/dev/shm {qtmp}/dev/mqueue "
                f"&& find {qtmp} ! -readable -exec chmod u+r {{}} + 2>/dev/null; "
                f"depmod -a -b {qtmp} $(ls {qtmp}/lib/modules/ 2>/dev/null | head -1) 2>/dev/null; "
                f"mke2fs -t ext4 -d {qtmp} -b 4096 "
                f"-L rootfs {shlex.quote(tmpfile)} {_IMAGE_SIZE_MB}M'",
            ],
            capture_output=False,
        )

        # Shrink to minimum. qcow2 overlay resizes to 8G at VM
        # creation, rc.local auto-expands the ext4 on boot.
        subprocess.run(["e2fsck", "-fy", tmpfile], capture_output=True)
        _run(["resize2fs", "-M", tmpfile])

        # Remove the podman container
        subprocess.run(
            ["podman", "rm", "-f", container_id], capture_output=True
        )
        container_id = None

        # Shrink to minimum + headroom. The qcow2 overlay is resized
        # to 8G at VM creation, and rc.local auto-expands on boot.
        # First shrink to minimum, then add headroom for first-boot writes.
        subprocess.run(["e2fsck", "-fy", tmpfile], capture_output=True)
        _run(["resize2fs", "-M", tmpfile])

        # Move to final location
        if image_path.exists():
            image_path.unlink()
        shutil.move(tmpfile, str(image_path))
        tmpfile = None

        return image_path

    finally:
        if container_id:
            subprocess.run(
                ["podman", "rm", "-f", container_id], capture_output=True
            )
        if tmpdir and os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)
        if tmpfile and os.path.exists(tmpfile):
            os.unlink(tmpfile)


def _get_package_manifest(
    container_tag: str, os_family: str = "rhel"
) -> list[str]:
    """Get installed package list from the container image."""
    try:
        if os_family == "debian":
            result = _run(
                [
                    "podman",
                    "run",
                    "--rm",
                    container_tag,
                    "dpkg-query",
                    "-W",
                    "-f",
                    "${Package} ${Version} ${Architecture}\n",
                ]
            )
        else:
            result = _run(
                [
                    "podman",
                    "run",
                    "--rm",
                    container_tag,
                    "rpm",
                    "-qa",
                    "--queryformat",
                    "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\\n",
                ]
            )
        packages = sorted(result.stdout.strip().splitlines())
        return packages
    except subprocess.CalledProcessError:
        log.warning("Failed to extract package manifest")
        return []


def image_status(
    target_config: TargetConfig,
) -> dict[str, bool | str | float | None]:
    """Return status dict for the target's image artifact.

    Keys:
        built: bool -- whether an image exists
        build_date: str or None -- ISO timestamp
        stale: bool -- whether inputs have changed
        size_mb: float or None -- image file size
        path: str or None -- path to base.ext4
    """
    out_dir = target_config.image_output_dir()
    image_path = out_dir / "base.ext4"
    meta_path = out_dir / "meta.json"

    if not image_path.exists():
        return {
            "built": False,
            "build_date": None,
            "stale": True,
            "size_mb": None,
            "path": None,
        }

    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())

    size_mb = image_path.stat().st_size / (1024 * 1024)
    stale = target_config.is_stale("image")

    return {
        "built": True,
        "build_date": meta.get("build_date"),
        "stale": stale,
        "size_mb": round(size_mb, 1),
        "path": str(image_path),
    }
