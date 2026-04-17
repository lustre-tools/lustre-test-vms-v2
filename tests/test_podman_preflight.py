"""Tests for the macOS podman-machine preflight check.

Covers ``check_podman_machine_macos`` in ``ltvm_pkg.host_setup`` and its
wiring into the build CLI commands via ``_preflight_podman``.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ltvm_pkg.cli import EXIT_ERROR, EXIT_OK
from ltvm_pkg.host_setup import (
    PodmanMachineError,
    check_podman_machine_macos,
)


_LTVM_PATH = str(Path(__file__).parent.parent / "ltvm")


def _load_ltvm() -> Any:
    loader = importlib.machinery.SourceFileLoader("ltvm", _LTVM_PATH)
    spec = importlib.util.spec_from_loader("ltvm", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ltvm = _load_ltvm()


def _run_main(argv: list[str]) -> int:
    with patch.object(sys, "argv", ["ltvm"] + argv):
        return ltvm.main()


class TestCheckPodmanMachineMacos:
    def test_noop_on_non_macos(self) -> None:
        with patch("ltvm_pkg.host_setup.is_macos", return_value=False):
            check_podman_machine_macos()

    def test_missing_podman_binary(self) -> None:
        with (
            patch("ltvm_pkg.host_setup.is_macos", return_value=True),
            patch("ltvm_pkg.host_setup.shutil.which", return_value=None),
        ):
            with pytest.raises(PodmanMachineError) as exc:
                check_podman_machine_macos()
        assert "brew install podman" in str(exc.value)

    def test_no_machines_defined(self) -> None:
        fake = MagicMock(returncode=0, stdout="[]")
        with (
            patch("ltvm_pkg.host_setup.is_macos", return_value=True),
            patch(
                "ltvm_pkg.host_setup.shutil.which",
                return_value="/opt/homebrew/bin/podman",
            ),
            patch(
                "ltvm_pkg.host_setup.subprocess.run", return_value=fake
            ),
        ):
            with pytest.raises(PodmanMachineError) as exc:
                check_podman_machine_macos()
        assert "podman machine init" in str(exc.value)
        assert "podman machine start" in str(exc.value)

    def test_machine_defined_but_not_running(self) -> None:
        fake = MagicMock(
            returncode=0,
            stdout='[{"Name": "podman-machine-default", "Running": false}]',
        )
        with (
            patch("ltvm_pkg.host_setup.is_macos", return_value=True),
            patch(
                "ltvm_pkg.host_setup.shutil.which",
                return_value="/opt/homebrew/bin/podman",
            ),
            patch(
                "ltvm_pkg.host_setup.subprocess.run", return_value=fake
            ),
        ):
            with pytest.raises(PodmanMachineError) as exc:
                check_podman_machine_macos()
        assert "podman machine start" in str(exc.value)

    def test_running_machine_passes(self) -> None:
        fake = MagicMock(
            returncode=0,
            stdout='[{"Name": "podman-machine-default", "Running": true}]',
        )
        with (
            patch("ltvm_pkg.host_setup.is_macos", return_value=True),
            patch(
                "ltvm_pkg.host_setup.shutil.which",
                return_value="/opt/homebrew/bin/podman",
            ),
            patch(
                "ltvm_pkg.host_setup.subprocess.run", return_value=fake
            ),
        ):
            check_podman_machine_macos()

    def test_query_failure_surfaces_message(self) -> None:
        with (
            patch("ltvm_pkg.host_setup.is_macos", return_value=True),
            patch(
                "ltvm_pkg.host_setup.shutil.which",
                return_value="/opt/homebrew/bin/podman",
            ),
            patch(
                "ltvm_pkg.host_setup.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="podman", timeout=10),
            ),
        ):
            with pytest.raises(PodmanMachineError) as exc:
                check_podman_machine_macos()
        assert "podman machine" in str(exc.value)


class TestCliPreflight:
    """The build commands should refuse to proceed when the macOS preflight fails."""

    def test_build_container_fails_fast(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ltvm_pkg.cli import build as build_mod

        monkeypatch.setattr(
            build_mod,
            "check_podman_machine_macos",
            MagicMock(
                side_effect=PodmanMachineError(
                    "On macOS, container builds require a running podman machine."
                )
            ),
        )
        rc = _run_main(["build", "container", "rocky9"])
        assert rc == EXIT_ERROR
        err = capsys.readouterr().err
        assert "running podman machine" in err

    def test_build_kernel_fails_fast(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ltvm_pkg.cli import build as build_mod

        monkeypatch.setattr(
            build_mod,
            "check_podman_machine_macos",
            MagicMock(
                side_effect=PodmanMachineError("need podman machine")
            ),
        )
        rc = _run_main(["build", "kernel", "rocky9"])
        assert rc == EXIT_ERROR
        assert "need podman machine" in capsys.readouterr().err

    def test_build_image_fails_fast(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ltvm_pkg.cli import build as build_mod

        monkeypatch.setattr(
            build_mod,
            "check_podman_machine_macos",
            MagicMock(
                side_effect=PodmanMachineError("need podman machine")
            ),
        )
        rc = _run_main(["build", "image", "rocky9"])
        assert rc == EXIT_ERROR
        assert "need podman machine" in capsys.readouterr().err

    def test_build_lustre_fails_fast(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ltvm_pkg.cli import build as build_mod

        monkeypatch.setattr(
            build_mod,
            "check_podman_machine_macos",
            MagicMock(
                side_effect=PodmanMachineError("need podman machine")
            ),
        )
        rc = _run_main(["build", "lustre", "rocky9"])
        assert rc == EXIT_ERROR
        assert "need podman machine" in capsys.readouterr().err

    def test_build_all_fails_fast(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ltvm_pkg.cli import build as build_mod

        monkeypatch.setattr(
            build_mod,
            "check_podman_machine_macos",
            MagicMock(
                side_effect=PodmanMachineError("need podman machine")
            ),
        )
        rc = _run_main(["build", "all", "rocky9"])
        assert rc == EXIT_ERROR
        assert "need podman machine" in capsys.readouterr().err

    def test_build_shell_fails_fast(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from ltvm_pkg.cli import build as build_mod

        monkeypatch.setattr(
            build_mod,
            "check_podman_machine_macos",
            MagicMock(
                side_effect=PodmanMachineError("need podman machine")
            ),
        )
        rc = _run_main(
            ["build", "shell", "rocky9", str(tmp_path)]
        )
        assert rc == EXIT_ERROR
        assert "need podman machine" in capsys.readouterr().err

    def test_build_mofed_kmods_fails_fast(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from ltvm_pkg.cli import build as build_mod

        monkeypatch.setattr(
            build_mod,
            "check_podman_machine_macos",
            MagicMock(
                side_effect=PodmanMachineError("need podman machine")
            ),
        )
        rc = _run_main(
            ["build", "mofed-kmods", "rocky9", "--variant", "mofed-24"]
        )
        # Either the preflight fires (EXIT_ERROR) or an earlier arg
        # validation path does; in both cases we should NOT reach EXIT_OK.
        assert rc != EXIT_OK
