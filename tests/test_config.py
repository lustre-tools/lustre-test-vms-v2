"""Tests for lib/config.py -- TargetConfig and helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import _make_config


class TestTargetConfigProperties:
    def test_os_family(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.os_family == "rhel"

    def test_os_name(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.os_name == "rocky"

    def test_os_version(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.os_version == "9"

    def test_server(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.server is True

    def test_arch(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.arch == "x86_64"

    def test_container_image(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.container_image == "rockylinux:9"

    def test_lustre_target(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.lustre_target == "5.14-rhel9.7"

    def test_default_kernel(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.default_kernel == tc.lustre_target

    def test_kernel_config_overrides(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        overrides = tc.kernel_config_overrides
        assert overrides == {"CONFIG_XEN_PVH": "y"}


class TestTargetConfigUnknown:
    def test_unknown_target_raises(self, tmp_targets: Path) -> None:
        import lib.config as cfg

        with (
            patch.object(cfg, "TARGETS_DIR", tmp_targets / "targets"),
            patch.object(cfg, "OUTPUT_DIR", tmp_targets / "output"),
            pytest.raises(ValueError, match="Unknown target"),
        ):
            cfg.TargetConfig("nonexistent")


class TestResolveKernel:
    def test_explicit_kernel(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.resolve_kernel("custom-kernel") == "custom-kernel"

    def test_default_kernel(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.resolve_kernel(None) == "5.14-rhel9.7"


class TestKernelOutputDir:
    def test_default_path(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        expected = (
            tmp_targets / "output" / "rocky9" / "kernels" / "5.14-rhel9.7"
        )
        assert tc.kernel_output_dir() == expected

    def test_custom_kernel_path(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        expected = tmp_targets / "output" / "rocky9" / "kernels" / "custom"
        assert tc.kernel_output_dir("custom") == expected


class TestAvailableKernels:
    def test_no_kernels_dir(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.available_kernels() == []

    def test_with_kernels(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        kernels = tmp_targets / "output" / "rocky9" / "kernels"
        (kernels / "5.14-rhel9.7").mkdir(parents=True)
        (kernels / "5.14-rhel9.6").mkdir(parents=True)
        result = tc.available_kernels()
        assert result == ["5.14-rhel9.6", "5.14-rhel9.7"]

    def test_ignores_files(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        kernels = tmp_targets / "output" / "rocky9" / "kernels"
        kernels.mkdir(parents=True)
        (kernels / "stray-file.txt").write_text("ignore me")
        (kernels / "real-kernel").mkdir()
        assert tc.available_kernels() == ["real-kernel"]


class TestOutputDirs:
    def test_image_output_dir(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert (
            tc.image_output_dir() == tmp_targets / "output" / "rocky9" / "image"
        )

    def test_container_output_dir(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.container_output_dir() == (
            tmp_targets / "output" / "rocky9" / "container"
        )


class TestInputHash:
    def test_container_hash_deterministic(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        h1 = tc.input_hash("container")
        h2 = tc.input_hash("container")
        assert h1 == h2
        assert len(h1) == 16

    def test_kernel_hash_deterministic(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        h1 = tc.input_hash("kernel")
        h2 = tc.input_hash("kernel")
        assert h1 == h2

    def test_image_hash_deterministic(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        h1 = tc.input_hash("image")
        h2 = tc.input_hash("image")
        assert h1 == h2

    def test_different_artifacts_different_hash(
        self, tmp_targets: Path
    ) -> None:
        tc = _make_config(tmp_targets)
        hashes = {
            tc.input_hash("container"),
            tc.input_hash("kernel"),
            tc.input_hash("image"),
        }
        assert len(hashes) == 3

    def test_container_hash_changes_with_dockerfile(
        self, tmp_targets: Path
    ) -> None:
        tc = _make_config(tmp_targets)
        h1 = tc.input_hash("container")
        df = tmp_targets / "targets" / "rocky9" / "container.Dockerfile"
        df.write_text("FROM rockylinux:9\nRUN echo changed\n")
        h2 = tc.input_hash("container")
        assert h1 != h2

    def test_kernel_hash_changes_with_override(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        h1 = tc.input_hash("kernel")
        # Modify kernel.conf to add an override
        kc = tmp_targets / "targets" / "rocky9" / "kernel.conf"
        kc.write_text(
            "[kernel]\nlustre_target = 5.14-rhel9.7\n\n"
            "[config]\nCONFIG_XEN_PVH=y\nCONFIG_NEW=m\n"
        )
        # Need fresh TargetConfig to pick up changes
        tc2 = _make_config(tmp_targets)
        h2 = tc2.input_hash("kernel")
        assert h1 != h2

    def test_image_hash_changes_with_packages(self, tmp_targets: Path) -> None:
        import lib.config as cfg

        with (
            patch.object(cfg, "TARGETS_DIR", tmp_targets / "targets"),
            patch.object(cfg, "OUTPUT_DIR", tmp_targets / "output"),
        ):
            tc = cfg.TargetConfig("rocky9")
            h1 = tc.input_hash("image")
            pkg = tmp_targets / "targets" / "common" / "packages-base.txt"
            pkg.write_text("bash\ncoreutils\nnew-package\n")
            h2 = tc.input_hash("image")
        assert h1 != h2

    def test_image_hash_includes_server_packages(
        self, tmp_targets: Path
    ) -> None:
        """Image hash for a server target includes server packages."""
        import lib.config as cfg

        with (
            patch.object(cfg, "TARGETS_DIR", tmp_targets / "targets"),
            patch.object(cfg, "OUTPUT_DIR", tmp_targets / "output"),
        ):
            tc = cfg.TargetConfig("rocky9")
            h1 = tc.input_hash("image")
            # Modify server packages
            pkg = tmp_targets / "targets" / "common" / "packages-server.txt"
            pkg.write_text("nfs-utils\nextra-server-pkg\n")
            h2 = tc.input_hash("image")
        assert h1 != h2

    def test_image_hash_excludes_server_for_non_server(
        self, tmp_targets: Path
    ) -> None:
        """Image hash for non-server target ignores server packages."""
        # Make target non-server
        tc_conf = tmp_targets / "targets" / "rocky9" / "target.conf"
        tc_conf.write_text(
            "[target]\nos_family = rhel\nos_name = rocky\n"
            "os_version = 9\nserver = no\narch = x86_64\n"
            "container_image = rockylinux:9\n"
        )
        tc = _make_config(tmp_targets)
        h1 = tc.input_hash("image")
        # Modify server packages -- should NOT change hash
        pkg = tmp_targets / "targets" / "common" / "packages-server.txt"
        pkg.write_text("nfs-utils\nextra-server-pkg\n")
        h2 = tc.input_hash("image")
        assert h1 == h2


class TestStaleness:
    def test_stale_when_no_meta(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.is_stale("container") is True

    def test_not_stale_after_write_meta(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        tc.write_meta("container")
        assert tc.is_stale("container") is False

    def test_stale_after_input_change(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        tc.write_meta("container")
        assert tc.is_stale("container") is False
        # Change input
        df = tmp_targets / "targets" / "rocky9" / "container.Dockerfile"
        df.write_text("FROM rockylinux:9\nRUN echo changed\n")
        assert tc.is_stale("container") is True

    def test_kernel_staleness(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        assert tc.is_stale("kernel") is True
        tc.write_meta("kernel")
        assert tc.is_stale("kernel") is False

    def test_kernel_staleness_with_explicit_name(
        self, tmp_targets: Path
    ) -> None:
        tc = _make_config(tmp_targets)
        tc.write_meta("kernel", kernel="custom-kernel")
        assert tc.is_stale("kernel", kernel="custom-kernel") is False
        # Different kernel name should still be stale
        assert tc.is_stale("kernel", kernel="other-kernel") is True


class TestWriteMeta:
    def test_writes_json(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        tc.write_meta("container", build_date="2024-01-01")
        meta_path = (
            tmp_targets / "output" / "rocky9" / "container" / "meta.json"
        )
        assert meta_path.exists()
        data = json.loads(meta_path.read_text())
        assert data["target"] == "rocky9"
        assert data["build_date"] == "2024-01-01"
        assert "input_hash" in data

    def test_kernel_meta_under_kernel_dir(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        tc.write_meta("kernel", kernel="5.14-rhel9.7", version="5.14.0")
        meta_path = (
            tmp_targets
            / "output"
            / "rocky9"
            / "kernels"
            / "5.14-rhel9.7"
            / "meta.json"
        )
        assert meta_path.exists()
        data = json.loads(meta_path.read_text())
        assert data["version"] == "5.14.0"

    def test_creates_dirs(self, tmp_targets: Path) -> None:
        tc = _make_config(tmp_targets)
        tc.write_meta("image")
        meta_path = tmp_targets / "output" / "rocky9" / "image" / "meta.json"
        assert meta_path.exists()


class TestListTargets:
    def test_finds_targets(self, tmp_targets: Path) -> None:
        import lib.config as cfg

        with patch.object(cfg, "TARGETS_DIR", tmp_targets / "targets"):
            targets = cfg.list_targets()
        assert "rocky9" in targets

    def test_ignores_common(self, tmp_targets: Path) -> None:
        """common/ has no target.conf, so it's not a target."""
        import lib.config as cfg

        with patch.object(cfg, "TARGETS_DIR", tmp_targets / "targets"):
            targets = cfg.list_targets()
        assert "common" not in targets

    def test_ignores_dirs_without_target_conf(self, tmp_targets: Path) -> None:
        import lib.config as cfg

        (tmp_targets / "targets" / "bogus").mkdir()
        with patch.object(cfg, "TARGETS_DIR", tmp_targets / "targets"):
            targets = cfg.list_targets()
        assert "bogus" not in targets
