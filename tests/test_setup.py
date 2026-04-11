"""Tests for ltvm_pkg/host_setup.py -- host detection, package mapping, SSH."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ltvm_pkg.host_setup import (
    SSH_BLOCK_MARKER,
    HostInfo,
    _qemu_installed_version,
    _translate_pkgs,
    check_kvm,
    check_prerequisites,
    print_verify,
    setup_ssh,
    verify,
)


class TestHostInfo:
    def test_detects_current_host(self) -> None:
        """Should not raise on this machine."""
        info = HostInfo()
        assert info.id != "unknown"
        assert info.pkg_mgr in ("dnf", "apt")
        assert info.pretty_name != "unknown"

    def test_real_os_release_parses(self, tmp_path: Path) -> None:
        """Smoke check: HostInfo() reads the real /etc/os-release on this
        host and produces a non-None id.  The genuinely-missing-file case
        is hard to mock without breaking HostInfo's internal Path usage,
        so we don't cover it here."""
        info = HostInfo()
        assert info.id is not None

    def test_str_representation(self) -> None:
        info = HostInfo()
        s = str(info)
        assert "(" in s  # "(dnf)" or "(apt)"


class TestTranslatePkgs:
    def test_dnf_passthrough(self) -> None:
        host = HostInfo()
        if host.pkg_mgr != "dnf":
            pytest.skip("need dnf host")
        result = _translate_pkgs(("glib2-devel", "make"), host)
        assert result == ["glib2-devel", "make"]

    def test_apt_translation(self) -> None:
        """Test package name mapping for apt-based hosts."""
        # Create a mock host with apt
        host = HostInfo.__new__(HostInfo)
        host.id = "ubuntu"
        host.version = "22.04"
        host.pretty_name = "Ubuntu 22.04"
        host.pkg_mgr = "apt"

        result = _translate_pkgs(("glib2-devel", "pixman-devel"), host)
        assert result == ["libglib2.0-dev", "libpixman-1-dev"]

    def test_apt_unknown_pkg_passthrough(self) -> None:
        """Unmapped packages pass through unchanged."""
        host = HostInfo.__new__(HostInfo)
        host.pkg_mgr = "apt"
        result = _translate_pkgs(("curl", "tar"), host)
        assert result == ["curl", "tar"]


class TestSetupSsh:
    """Tests that call the real setup_ssh() against a temp directory."""

    def _call_setup_ssh(
        self, tmp_path: Path, subnet: str = "192.168.100"
    ) -> Path:
        """Run setup_ssh() with /root/.ssh redirected to tmp_path."""
        ssh_dir = tmp_path / ".ssh"
        with patch(
            "ltvm_pkg.host_setup.Path",
            side_effect=lambda p: ssh_dir if p == "/root/.ssh" else Path(p),
        ):
            setup_ssh(subnet)
        return ssh_dir / "config"

    def test_creates_ssh_config(self, tmp_path: Path) -> None:
        config = self._call_setup_ssh(tmp_path)
        text = config.read_text()
        assert SSH_BLOCK_MARKER in text
        assert "StrictHostKeyChecking no" in text
        assert "Host 192.168.100.*" in text
        assert "User root" in text

    def test_marker_format(self) -> None:
        assert SSH_BLOCK_MARKER == "# lustre-test-vms"

    def test_ssh_block_contains_fast_timeouts(self, tmp_path: Path) -> None:
        """SSH config should have aggressive timeouts for VMs."""
        config = self._call_setup_ssh(tmp_path)
        text = config.read_text()
        assert "ServerAliveInterval 5" in text
        assert "ServerAliveCountMax 3" in text
        assert "ConnectTimeout 5" in text

    def test_custom_subnet(self, tmp_path: Path) -> None:
        """Different subnet produces the correct Host pattern."""
        config = self._call_setup_ssh(tmp_path, subnet="10.0.0")
        text = config.read_text()
        assert "Host 10.0.0.*" in text

    def test_idempotent(self, tmp_path: Path) -> None:
        """Calling setup_ssh twice does not duplicate the block."""
        self._call_setup_ssh(tmp_path)
        config = self._call_setup_ssh(tmp_path)
        text = config.read_text()
        assert text.count(SSH_BLOCK_MARKER) == 1

    def test_subnet_change_replaces_block(self, tmp_path: Path) -> None:
        """Changing subnet replaces the old block."""
        self._call_setup_ssh(tmp_path, subnet="192.168.100")
        config = self._call_setup_ssh(tmp_path, subnet="10.0.0")
        text = config.read_text()
        assert "Host 10.0.0.*" in text
        assert "192.168.100" not in text
        assert text.count(SSH_BLOCK_MARKER) == 1


# ------------------------------------------------------------------
# TestHostInfoNoPkgMgr
# ------------------------------------------------------------------


class TestHostInfoNoPkgMgr:
    def test_raises_when_no_package_manager(self) -> None:
        """RuntimeError raised when neither dnf nor apt-get found."""
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="package manager"):
                HostInfo()


# ------------------------------------------------------------------
# TestCheckPrerequisites
# ------------------------------------------------------------------


class TestCheckPrerequisites:
    def _make_host(self, pkg_mgr: str) -> HostInfo:
        host = HostInfo.__new__(HostInfo)
        host.id = "rocky"
        host.version = "9"
        host.pretty_name = "Rocky Linux 9"
        host.pkg_mgr = pkg_mgr
        return host

    def test_no_missing_no_install(self) -> None:
        """When all cmds present, _pkg_install is not called."""
        host = self._make_host("dnf")
        with patch("shutil.which", return_value="/usr/bin/cmd"):
            with patch("ltvm_pkg.host_setup._pkg_install") as mock_install:
                check_prerequisites(host)
        mock_install.assert_not_called()

    def test_missing_cmds_calls_install(self) -> None:
        """Missing cmds trigger _pkg_install with the right packages."""
        host = self._make_host("dnf")

        def _which(cmd: str) -> str | None:
            # curl and tar are missing; everything else present
            if cmd in ("curl", "tar"):
                return None
            return f"/usr/bin/{cmd}"

        with patch("shutil.which", side_effect=_which):
            with patch("ltvm_pkg.host_setup._pkg_install") as mock_install:
                check_prerequisites(host)

        # Should be called once with the two missing packages
        mock_install.assert_called_once()
        args = mock_install.call_args[0]
        assert args[0] is host
        assert "curl" in args
        assert "tar" in args

    def test_iproute_pkg_name_dnf(self) -> None:
        """dnf host uses 'iproute' for the 'ip' command."""
        host = self._make_host("dnf")

        def _which(cmd: str) -> str | None:
            if cmd == "ip":
                return None
            return f"/usr/bin/{cmd}"

        with patch("shutil.which", side_effect=_which):
            with patch("ltvm_pkg.host_setup._pkg_install") as mock_install:
                check_prerequisites(host)

        args = mock_install.call_args[0]
        assert "iproute" in args
        assert "iproute2" not in args

    def test_iproute2_pkg_name_apt(self) -> None:
        """apt host uses 'iproute2' for the 'ip' command."""
        host = self._make_host("apt")

        def _which(cmd: str) -> str | None:
            if cmd == "ip":
                return None
            return f"/usr/bin/{cmd}"

        with patch("shutil.which", side_effect=_which):
            with patch("ltvm_pkg.host_setup._pkg_install") as mock_install:
                check_prerequisites(host)

        args = mock_install.call_args[0]
        assert "iproute2" in args
        assert "iproute" not in args

    def test_podman_missing_no_exception(self) -> None:
        """Missing podman only logs a warning -- no exception raised."""
        host = self._make_host("dnf")

        def _which(cmd: str) -> str | None:
            if cmd == "podman":
                return None
            return f"/usr/bin/{cmd}"

        # Should not raise
        with patch("shutil.which", side_effect=_which):
            with patch("ltvm_pkg.host_setup._pkg_install"):
                check_prerequisites(host)


# ------------------------------------------------------------------
# TestCheckKvm
# ------------------------------------------------------------------


class TestCheckKvm:
    def test_kvm_exists_returns_true(self) -> None:
        """Returns True when /dev/kvm is present."""
        with patch("ltvm_pkg.host_setup.Path") as mock_path_cls:
            mock_kvm = MagicMock()
            mock_kvm.exists.return_value = True
            mock_path_cls.return_value = mock_kvm
            result = check_kvm()
        assert result is True

    def test_kvm_missing_require_true_raises(self) -> None:
        """Raises RuntimeError when /dev/kvm absent and require=True."""
        with patch("ltvm_pkg.host_setup.Path") as mock_path_cls:
            mock_kvm = MagicMock()
            mock_kvm.exists.return_value = False
            mock_path_cls.return_value = mock_kvm
            with pytest.raises(RuntimeError, match="/dev/kvm"):
                check_kvm(require=True)

    def test_kvm_missing_require_false_returns_false(self) -> None:
        """Returns False without raising when require=False."""
        with patch("ltvm_pkg.host_setup.Path") as mock_path_cls:
            mock_kvm = MagicMock()
            mock_kvm.exists.return_value = False
            mock_path_cls.return_value = mock_kvm
            result = check_kvm(require=False)
        assert result is False


# ------------------------------------------------------------------
# TestQemuInstalledVersion
# ------------------------------------------------------------------


class TestQemuInstalledVersion:
    def test_qemu_binary_missing_returns_none(self, tmp_path: Path) -> None:
        """Returns None when the qemu binary does not exist."""
        fake_prefix = tmp_path / "qemu"
        # Do NOT create the binary -- it should not exist.
        with patch("ltvm_pkg.host_setup.QEMU_PREFIX", fake_prefix):
            result = _qemu_installed_version()
        assert result is None

    def test_qemu_version_parsed(self, tmp_path: Path) -> None:
        """Returns version string when output contains 'version X.Y.Z'."""
        fake_bin = tmp_path / "bin" / "qemu-system-x86_64"
        fake_bin.parent.mkdir(parents=True)
        fake_bin.touch()

        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="QEMU emulator version 9.2.2\n"
        )
        with patch("ltvm_pkg.host_setup.QEMU_PREFIX", tmp_path):
            with patch(
                "ltvm_pkg.host_setup._run_quiet", return_value=completed
            ):
                result = _qemu_installed_version()
        assert result == "9.2.2"

    def test_qemu_no_version_in_output(self, tmp_path: Path) -> None:
        """Returns 'unknown' when output has no recognisable version."""
        fake_bin = tmp_path / "bin" / "qemu-system-x86_64"
        fake_bin.parent.mkdir(parents=True)
        fake_bin.touch()

        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="something completely different\n"
        )
        with patch("ltvm_pkg.host_setup.QEMU_PREFIX", tmp_path):
            with patch(
                "ltvm_pkg.host_setup._run_quiet", return_value=completed
            ):
                result = _qemu_installed_version()
        assert result == "unknown"

    def test_qemu_exception_returns_none(self, tmp_path: Path) -> None:
        """Returns None when running the binary raises an exception."""
        fake_bin = tmp_path / "bin" / "qemu-system-x86_64"
        fake_bin.parent.mkdir(parents=True)
        fake_bin.touch()

        with patch("ltvm_pkg.host_setup.QEMU_PREFIX", tmp_path):
            with patch(
                "ltvm_pkg.host_setup._run_quiet", side_effect=OSError("oops")
            ):
                result = _qemu_installed_version()
        assert result is None


# ------------------------------------------------------------------
# TestVerify
# ------------------------------------------------------------------


def _mock_completed(returncode: int = 0, stdout: str = "") -> MagicMock:
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.returncode = returncode
    r.stdout = stdout
    return r


class TestVerify:
    def _patch_all_ok(self) -> dict:
        """Return a dict of patch kwargs representing a fully healthy host."""
        return {
            "ltvm_pkg.host_setup._qemu_installed_version": "9.2.2",
            # /dev/kvm exists
            # bridge ip cmd succeeds with an inet address
            # dnsmasq is active
            # scripts are in PATH
            # podman is present
            # SSH config exists and has SSH_BLOCK_MARKER
        }

    def test_all_ok(self) -> None:
        """all_ok=True when every component is healthy."""
        ssh_mock = MagicMock()
        ssh_mock.exists.return_value = True
        ssh_mock.read_text.return_value = f"{SSH_BLOCK_MARKER}\n"

        def _run_quiet_side(cmd: list, **kw: object) -> MagicMock:
            # ip addr show fcbr0
            if "fcbr0" in cmd:
                return _mock_completed(0, "inet 192.168.100.1/24")
            # systemctl is-active dnsmasq
            if "dnsmasq" in cmd:
                return _mock_completed(0)
            # podman --version
            if "podman" in cmd:
                return _mock_completed(0, "podman version 4.9.0")
            return _mock_completed(0)

        with (
            patch(
                "ltvm_pkg.host_setup._qemu_installed_version",
                return_value="9.2.2",
            ),
            patch(
                "ltvm_pkg.host_setup.Path",
                side_effect=lambda p: (
                    ssh_mock
                    if p == "/root/.ssh/config"
                    else MagicMock(exists=MagicMock(return_value=True))
                ),
            ),
            patch(
                "ltvm_pkg.host_setup._run_quiet", side_effect=_run_quiet_side
            ),
            patch(
                "shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
        ):
            result = verify()

        assert result["all_ok"] is True
        assert result["qemu"]["installed"] is True
        assert result["qemu"]["version"] == "9.2.2"
        assert result["kvm"]["available"] is True
        assert result["bridge"]["up"] is True
        assert result["dnsmasq"]["running"] is True
        assert result["ltvm"]["installed"] is True
        assert result["podman"]["installed"] is True
        assert result["ssh"]["configured"] is True

    def test_qemu_missing(self) -> None:
        """all_ok=False and qemu.installed=False when QEMU absent."""
        ssh_mock = MagicMock()
        ssh_mock.exists.return_value = True
        ssh_mock.read_text.return_value = f"{SSH_BLOCK_MARKER}\n"

        def _run_quiet_side(cmd: list, **kw: object) -> MagicMock:
            if "fcbr0" in cmd:
                return _mock_completed(0, "inet 192.168.100.1/24")
            if "dnsmasq" in cmd:
                return _mock_completed(0)
            if "podman" in cmd:
                return _mock_completed(0, "podman version 4.9.0")
            return _mock_completed(0)

        with (
            patch(
                "ltvm_pkg.host_setup._qemu_installed_version", return_value=None
            ),
            patch(
                "ltvm_pkg.host_setup.Path",
                side_effect=lambda p: (
                    ssh_mock
                    if p == "/root/.ssh/config"
                    else MagicMock(exists=MagicMock(return_value=True))
                ),
            ),
            patch(
                "ltvm_pkg.host_setup._run_quiet", side_effect=_run_quiet_side
            ),
            patch("shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}"),
        ):
            result = verify()

        assert result["qemu"]["installed"] is False
        assert result["all_ok"] is False

    def test_kvm_missing(self) -> None:
        """all_ok=False when /dev/kvm is absent."""
        ssh_mock = MagicMock()
        ssh_mock.exists.return_value = True
        ssh_mock.read_text.return_value = f"{SSH_BLOCK_MARKER}\n"

        def _path_side(p: str) -> MagicMock:
            m = MagicMock()
            if p == "/root/.ssh/config":
                return ssh_mock
            if p == "/dev/kvm":
                m.exists.return_value = False
                return m
            m.exists.return_value = True
            return m

        def _run_quiet_side(cmd: list, **kw: object) -> MagicMock:
            if "fcbr0" in cmd:
                return _mock_completed(0, "inet 192.168.100.1/24")
            if "dnsmasq" in cmd:
                return _mock_completed(0)
            if "podman" in cmd:
                return _mock_completed(0, "podman version 4.9.0")
            return _mock_completed(0)

        with (
            patch(
                "ltvm_pkg.host_setup._qemu_installed_version",
                return_value="9.2.2",
            ),
            patch("ltvm_pkg.host_setup.Path", side_effect=_path_side),
            patch(
                "ltvm_pkg.host_setup._run_quiet", side_effect=_run_quiet_side
            ),
            patch("shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}"),
        ):
            result = verify()

        assert result["kvm"]["available"] is False
        assert result["all_ok"] is False

    def test_result_structure(self) -> None:
        """Result dict contains all expected keys."""
        ssh_mock = MagicMock()
        ssh_mock.exists.return_value = False
        ssh_mock.read_text.return_value = ""

        def _run_quiet_side(cmd: list, **kw: object) -> MagicMock:
            return _mock_completed(1)

        with (
            patch(
                "ltvm_pkg.host_setup._qemu_installed_version", return_value=None
            ),
            patch(
                "ltvm_pkg.host_setup.Path",
                side_effect=lambda p: (
                    ssh_mock
                    if p == "/root/.ssh/config"
                    else MagicMock(exists=MagicMock(return_value=False))
                ),
            ),
            patch(
                "ltvm_pkg.host_setup._run_quiet", side_effect=_run_quiet_side
            ),
            patch("shutil.which", return_value=None),
        ):
            result = verify()

        for key in (
            "qemu",
            "kvm",
            "bridge",
            "dnsmasq",
            "ltvm",
            "podman",
            "ssh",
            "all_ok",
        ):
            assert key in result, f"Missing key: {key}"


# ------------------------------------------------------------------
# TestPrintVerify
# ------------------------------------------------------------------


def _all_ok_result() -> dict:
    return {
        "qemu": {"installed": True, "version": "9.2.2", "path": "/opt/qemu"},
        "qemu_aarch64": {"installed": False, "version": None},
        "kvm": {"available": True},
        "bridge": {"up": True, "address": "192.168.100.1/24"},
        "dnsmasq": {"running": True},
        "ltvm": {"installed": True, "path": "/usr/local/bin/ltvm"},
        "podman": {"installed": True, "version": "4.9.0"},
        "ssh": {"configured": True},
        "all_ok": True,
    }


def _failing_result() -> dict:
    r = _all_ok_result()
    r["qemu"]["installed"] = False
    r["qemu"]["version"] = None
    r["kvm"]["available"] = False
    r["all_ok"] = False
    return r


class TestPrintVerify:
    def test_all_ok_message(self, capsys: pytest.CaptureFixture) -> None:
        """Prints 'All checks passed.' when all_ok=True."""
        print_verify(_all_ok_result())
        captured = capsys.readouterr()
        assert "All checks passed." in captured.out

    def test_some_failing_message(self, capsys: pytest.CaptureFixture) -> None:
        """Prints failure summary when all_ok=False."""
        print_verify(_failing_result())
        captured = capsys.readouterr()
        assert "Some checks failed" in captured.out

    def test_qemu_installed_prints_version_and_path(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """When QEMU is installed, prints its version and path."""
        print_verify(_all_ok_result())
        captured = capsys.readouterr()
        assert "9.2.2" in captured.out
        assert "/opt/qemu" in captured.out

    def test_qemu_missing_prints_warning(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """When QEMU is missing, prints 'WARNING: QEMU x86_64: not installed'."""
        result = _all_ok_result()
        result["qemu"]["installed"] = False
        result["qemu"]["version"] = None
        result["all_ok"] = False
        print_verify(result)
        captured = capsys.readouterr()
        assert "WARNING: QEMU x86_64: not installed" in captured.out

    def test_kvm_missing_prints_warning(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """KVM absent produces a warning line."""
        result = _all_ok_result()
        result["kvm"]["available"] = False
        result["all_ok"] = False
        print_verify(result)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "kvm" in captured.out.lower()
