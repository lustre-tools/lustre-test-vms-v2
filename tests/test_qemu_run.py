"""Tests for ltvm_pkg/qemu_run.py: command construction, is_running, kill."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ltvm_pkg import qemu_run
from ltvm_pkg.vm_state import VMInfo


@pytest.fixture
def tmp_vmdir(tmp_path: Path) -> Path:
    """Patch VM_DIR, SOCKETS, OVERLAYS so VMInfo paths land in tmp_path."""
    sockets = tmp_path / "sockets"
    overlays = tmp_path / "overlays"
    sockets.mkdir()
    overlays.mkdir()
    with (
        patch("ltvm_pkg.vm_state.VM_DIR", tmp_path),
        patch("ltvm_pkg.vm_state.SOCKETS", sockets),
        patch("ltvm_pkg.vm_state.OVERLAYS", overlays),
    ):
        yield tmp_path


def _make_vm(
    tmp_vmdir: Path,
    *,
    name: str = "co1-single",
    arch: str = "x86_64",
    mem: int = 2048,
    mdt_disks: int = 0,
    ost_disks: int = 0,
    kernel: str = "",
) -> VMInfo:
    """Build a VMInfo and materialise overlay + data disk files."""
    if not kernel:
        kernel = str(tmp_vmdir / "vmlinuz")
        Path(kernel).write_text("")  # real file so Path(vm.kernel) resolves
    vm = VMInfo(
        name=name,
        ip="192.168.100.50",
        tap=f"tap-{name}",
        mac="AA:FC:00:01:02:03",
        vcpus=2,
        mem=mem,
        mdt_disks=mdt_disks,
        ost_disks=ost_disks,
        kernel=kernel,
        arch=arch,
    )
    vm.overlay_path.write_text("")
    for n in range(1, mdt_disks + ost_disks + 1):
        vm.disk_path(n).write_text("")
    return vm


# ── is_running ───────────────────────────────────────────


class TestIsRunning:
    """is_running probes the kernel for the saved PID."""

    def test_zero_pid_is_not_running(self) -> None:
        vm = VMInfo(name="x", ip="1.2.3.4", pid=0)
        assert qemu_run.is_running(vm) is False

    def test_negative_pid_is_not_running(self) -> None:
        vm = VMInfo(name="x", ip="1.2.3.4", pid=-1)
        assert qemu_run.is_running(vm) is False

    def test_live_pid_is_running(self) -> None:
        """A PID that kill(0) accepts is considered running."""
        vm = VMInfo(name="x", ip="1.2.3.4", pid=42)
        with patch("ltvm_pkg.qemu_run.os.kill", return_value=None):
            assert qemu_run.is_running(vm) is True

    def test_dead_pid_is_not_running(self) -> None:
        """ProcessLookupError from kill(0) means the process is gone."""
        vm = VMInfo(name="x", ip="1.2.3.4", pid=42)
        with patch(
            "ltvm_pkg.qemu_run.os.kill",
            side_effect=ProcessLookupError,
        ):
            assert qemu_run.is_running(vm) is False

    def test_oserror_is_not_running(self) -> None:
        """OSError (EPERM et al) is treated the same as 'not running'."""
        vm = VMInfo(name="x", ip="1.2.3.4", pid=42)
        with patch(
            "ltvm_pkg.qemu_run.os.kill",
            side_effect=OSError("eperm"),
        ):
            assert qemu_run.is_running(vm) is False


# ── launch_qemu: command construction ────────────────────


class _LaunchHarness:
    """Intercept subprocess.run / open during launch_qemu.

    Captures the QEMU invocation (the final `subprocess.run(qemu_args, ...)`
    call) so tests can assert on the arg list and the console log path.
    """

    def __init__(self) -> None:
        self.qemu_args: list[str] | None = None
        self.run_calls: list[list[str]] = []

    def run(self, cmd, **kwargs):
        """Stand-in for qemu_run.run used by launch_qemu."""
        if isinstance(cmd, list):
            self.run_calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    def subprocess_run(self, qemu_args, **kwargs):
        """The direct subprocess.run call that actually launches QEMU."""
        self.qemu_args = list(qemu_args)
        r = MagicMock()
        r.returncode = 0
        return r


def _run_launch(vm: VMInfo, harness: _LaunchHarness) -> None:
    """Run launch_qemu with all side effects mocked."""
    # Ensure the pidfile write/read round-trip works.
    vm.pid_path.write_text("12345\n")
    with (
        patch("ltvm_pkg.qemu_run.run", side_effect=harness.run),
        patch("ltvm_pkg.qemu_run.subprocess.run",
              side_effect=harness.subprocess_run),
        patch("ltvm_pkg.qemu_run.is_running", return_value=False),
        patch("ltvm_pkg.qemu_run.time.time", return_value=1700000000),
        patch.object(VMInfo, "update_pid"),
        patch.object(VMInfo, "update_last_boot"),
    ):
        qemu_run.launch_qemu(vm)


class TestLaunchQemuCommand:
    """launch_qemu builds the expected QEMU args for each arch."""

    def test_x86_64_microvm_with_virtio_mmio(
        self, tmp_vmdir: Path
    ) -> None:
        """x86_64 uses our custom binary + microvm machine + MMIO devices."""
        vm = _make_vm(tmp_vmdir, arch="x86_64", mem=2048)
        h = _LaunchHarness()
        _run_launch(vm, h)
        args = h.qemu_args
        assert args is not None
        assert args[0].endswith("qemu-system-x86_64")
        # microvm machine with KVM on x86 host
        assert "-machine" in args
        machine = args[args.index("-machine") + 1]
        assert machine.startswith("microvm")
        # virtio-*-device (MMIO bus)
        joined = " ".join(args)
        assert "virtio-blk-device" in joined
        assert "virtio-net-device" in joined
        assert "virtio-rng-device" in joined
        assert "virtio-blk-pci" not in joined

    def test_aarch64_virt_with_pci(self, tmp_vmdir: Path) -> None:
        """aarch64 uses virt + virtio-*-pci devices."""
        vm = _make_vm(tmp_vmdir, arch="aarch64", mem=4096)
        h = _LaunchHarness()
        _run_launch(vm, h)
        args = h.qemu_args
        assert args is not None
        assert args[0].endswith("qemu-system-aarch64")
        machine = args[args.index("-machine") + 1]
        assert machine.startswith("virt")
        joined = " ".join(args)
        assert "virtio-blk-pci" in joined
        assert "virtio-net-pci" in joined
        assert "virtio-rng-pci" in joined
        assert "virtio-blk-device" not in joined

    def test_boot_args_contains_ip_name_gateway(
        self, tmp_vmdir: Path
    ) -> None:
        """The -append arg wires IP/gateway/name into the kernel cmdline."""
        vm = _make_vm(tmp_vmdir)
        vm.ip = "192.168.100.77"
        h = _LaunchHarness()
        _run_launch(vm, h)
        args = h.qemu_args
        append = args[args.index("-append") + 1]
        assert "fc_ip=192.168.100.77" in append
        assert "fc_name=co1-single" in append
        assert "fc_gw=" in append
        assert "root=/dev/vda" in append

    def test_crashkernel_scales_with_memory(self, tmp_vmdir: Path) -> None:
        """Low-memory VMs reserve 256M; normal VMs reserve 512M."""
        small = _make_vm(tmp_vmdir, name="small", mem=1024)
        h1 = _LaunchHarness()
        _run_launch(small, h1)
        append_small = h1.qemu_args[h1.qemu_args.index("-append") + 1]
        assert "crashkernel=256M" in append_small

        big = _make_vm(tmp_vmdir, name="big", mem=4096)
        h2 = _LaunchHarness()
        _run_launch(big, h2)
        append_big = h2.qemu_args[h2.qemu_args.index("-append") + 1]
        assert "crashkernel=512M" in append_big

    def test_console_ttyAMA0_on_aarch64(self, tmp_vmdir: Path) -> None:
        """aarch64 uses ttyAMA0; x86 uses ttyS0."""
        arm = _make_vm(tmp_vmdir, name="arm", arch="aarch64")
        h = _LaunchHarness()
        _run_launch(arm, h)
        append = h.qemu_args[h.qemu_args.index("-append") + 1]
        assert "console=ttyAMA0" in append

        x86 = _make_vm(tmp_vmdir, name="x86", arch="x86_64")
        h2 = _LaunchHarness()
        _run_launch(x86, h2)
        append2 = h2.qemu_args[h2.qemu_args.index("-append") + 1]
        assert "console=ttyS0" in append2

    def test_data_disks_emit_device_and_drive(
        self, tmp_vmdir: Path
    ) -> None:
        """mdt+ost disks produce paired -device/-drive args, 1-indexed."""
        vm = _make_vm(tmp_vmdir, mdt_disks=1, ost_disks=2)
        h = _LaunchHarness()
        _run_launch(vm, h)
        joined = " ".join(h.qemu_args)
        for n in (1, 2, 3):
            assert f"drive=disk{n}" in joined
            assert f"id=disk{n}" in joined
        # Exactly 3 data disks plus the rootfs drive
        drive_count = joined.count("-drive")
        assert drive_count == 4

    def test_missing_data_disk_errors(self, tmp_vmdir: Path) -> None:
        """launch_qemu die()s (SystemExit) if a backing disk is missing."""
        vm = _make_vm(tmp_vmdir, mdt_disks=1)
        vm.disk_path(1).unlink()
        h = _LaunchHarness()
        with (
            patch("ltvm_pkg.qemu_run.run", side_effect=h.run),
            patch("ltvm_pkg.qemu_run.subprocess.run",
                  side_effect=h.subprocess_run),
            patch("ltvm_pkg.qemu_run.is_running", return_value=False),
        ):
            with pytest.raises(SystemExit):
                qemu_run.launch_qemu(vm)

    def test_missing_overlay_errors(self, tmp_vmdir: Path) -> None:
        """No overlay -> die() before spawning qemu."""
        vm = _make_vm(tmp_vmdir)
        vm.overlay_path.unlink()
        with patch("ltvm_pkg.qemu_run.is_running", return_value=False):
            with pytest.raises(SystemExit):
                qemu_run.launch_qemu(vm)

    def test_missing_kernel_errors(self, tmp_vmdir: Path) -> None:
        """Empty vm.kernel -> die() with a helpful message."""
        vm = _make_vm(tmp_vmdir)
        vm.kernel = ""
        h = _LaunchHarness()
        with (
            patch("ltvm_pkg.qemu_run.run", side_effect=h.run),
            patch("ltvm_pkg.qemu_run.subprocess.run",
                  side_effect=h.subprocess_run),
            patch("ltvm_pkg.qemu_run.is_running", return_value=False),
        ):
            with pytest.raises(SystemExit):
                qemu_run.launch_qemu(vm)

    def test_running_vm_short_circuits(self, tmp_vmdir: Path) -> None:
        """launch_qemu on an already-running VM is a no-op (no qemu spawn)."""
        vm = _make_vm(tmp_vmdir)
        h = _LaunchHarness()
        with (
            patch("ltvm_pkg.qemu_run.run", side_effect=h.run),
            patch("ltvm_pkg.qemu_run.subprocess.run",
                  side_effect=h.subprocess_run),
            patch("ltvm_pkg.qemu_run.is_running", return_value=True),
        ):
            qemu_run.launch_qemu(vm)
        assert h.qemu_args is None

    def test_tap_setup_sequence(self, tmp_vmdir: Path) -> None:
        """Before qemu launch: del-old-tap, flush-arp, add-tap, master, up."""
        vm = _make_vm(tmp_vmdir)
        h = _LaunchHarness()
        _run_launch(vm, h)
        # Each element is the full argv; look for the expected verbs in order
        verbs = [c[1:4] for c in h.run_calls if len(c) >= 4]
        # del + neigh flush are the first two
        assert ["link", "del", vm.tap] in verbs
        # tuntap add + link set master + link set up all present
        flat = [tuple(c) for c in h.run_calls]
        assert any(
            "tuntap" in c and "add" in c for c in flat
        )
        assert any(
            "master" in c for c in flat
        )

    def test_tap_rollback_on_failure(self, tmp_vmdir: Path) -> None:
        """If launch fails after TAP creation, the TAP is torn back down."""
        vm = _make_vm(tmp_vmdir)
        h = _LaunchHarness()
        # Simulate qemu-system-x86_64 failing (returncode != 0 -> die()).
        def bad_qemu(args, **kwargs):
            r = MagicMock()
            r.returncode = 1
            return r
        with (
            patch("ltvm_pkg.qemu_run.run", side_effect=h.run),
            patch("ltvm_pkg.qemu_run.subprocess.run", side_effect=bad_qemu),
            patch("ltvm_pkg.qemu_run.is_running", return_value=False),
        ):
            with pytest.raises(SystemExit):
                qemu_run.launch_qemu(vm)
        # Two "link del <tap>" calls: initial stale cleanup + rollback
        del_calls = [
            c for c in h.run_calls
            if len(c) >= 3 and c[:3] == ["ip", "link", "del"]
            and c[3] == vm.tap
        ]
        assert len(del_calls) >= 2, (
            "expected rollback to tear down TAP, saw: "
            f"{h.run_calls}"
        )


# ── kill_qemu ────────────────────────────────────────────


class TestKillQemu:
    """kill_qemu sends SIGTERM, escalates to SIGKILL, cleans up TAP."""

    def test_no_pid_just_clears_tap(self, tmp_vmdir: Path) -> None:
        """pid=0 skips the signalling path but still tears down TAP."""
        vm = _make_vm(tmp_vmdir)
        vm.pid = 0
        run_calls: list[list[str]] = []
        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list):
                run_calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            return r
        kill_calls: list[int] = []
        def fake_kill(pid, sig):
            kill_calls.append(sig)
        with (
            patch("ltvm_pkg.qemu_run.run", side_effect=fake_run),
            patch("ltvm_pkg.qemu_run.os.kill", side_effect=fake_kill),
            patch.object(VMInfo, "update_pid"),
        ):
            qemu_run.kill_qemu(vm)
        # No signals sent
        assert kill_calls == []
        # TAP teardown + ARP flush still happen
        assert any(
            len(c) >= 4 and c[1:4] == ["link", "del", vm.tap]
            for c in run_calls
        )
        assert any("neigh" in c for c in run_calls)

    def test_clean_sigterm_shutdown(self, tmp_vmdir: Path) -> None:
        """A process that exits after SIGTERM is not escalated to SIGKILL."""
        import signal as _signal
        vm = _make_vm(tmp_vmdir)
        vm.pid = 555
        sent: list[int] = []
        # First kill (SIGTERM) succeeds; probe kill(0) raises OSError
        # on the first poll -> process considered gone.
        def fake_kill(pid, sig):
            sent.append(sig)
            if sig == 0:
                raise OSError("gone")
        with (
            patch("ltvm_pkg.qemu_run.run"),
            patch("ltvm_pkg.qemu_run.os.kill", side_effect=fake_kill),
            patch("ltvm_pkg.qemu_run.time.sleep"),
            patch.object(VMInfo, "update_pid") as mock_update,
        ):
            qemu_run.kill_qemu(vm)
        assert _signal.SIGTERM in sent
        assert _signal.SIGKILL not in sent
        mock_update.assert_called_once_with(0)

    def test_sigkill_escalation(self, tmp_vmdir: Path) -> None:
        """A process that ignores SIGTERM gets SIGKILL after the grace period."""
        import signal as _signal
        vm = _make_vm(tmp_vmdir)
        vm.pid = 555
        sent: list[int] = []
        def fake_kill(pid, sig):
            sent.append(sig)
            # kill(0) always succeeds -> process stays alive through the loop
            return None
        with (
            patch("ltvm_pkg.qemu_run.run"),
            patch("ltvm_pkg.qemu_run.os.kill", side_effect=fake_kill),
            patch("ltvm_pkg.qemu_run.time.sleep"),
            patch.object(VMInfo, "update_pid"),
        ):
            qemu_run.kill_qemu(vm)
        assert _signal.SIGTERM in sent
        assert _signal.SIGKILL in sent
