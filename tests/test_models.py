"""Tests for qemu/models.py -- VMInfo metadata fields."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ltvm_pkg.vm_state import VMInfo, VMNotFound


@pytest.fixture
def tmp_sockets(tmp_path: Path) -> Path:
    """Redirect SOCKETS to a temp directory for isolation."""
    with patch("ltvm_pkg.vm_state.SOCKETS", tmp_path):
        yield tmp_path


class TestVMInfoMetadata:
    """VMInfo save/load round-trip with new metadata fields."""

    def test_save_load_all_fields(self, tmp_sockets: Path) -> None:
        """All metadata fields survive a save/load cycle."""
        vm = VMInfo(
            name="test-vm",
            ip="192.168.100.50",
            pid=12345,
            tap="tap-test-vm",
            mac="AA:BB:CC:DD:EE:FF",
            vcpus=4,
            mem=8192,
            mdt_disks=2,
            ost_disks=3,
            created=1700000000,
            last_boot=1700001000,
            last_deploy=1700002000,
            build_path="/home/admin/lustre-release",
            kver="5.14.0-test.x86_64",
            base_image="rocky9-base.ext4",
            os_id="rocky9",
        )
        vm.save()
        loaded = VMInfo.load("test-vm")

        assert loaded.created == 1700000000
        assert loaded.last_boot == 1700001000
        assert loaded.last_deploy == 1700002000
        assert loaded.build_path == "/home/admin/lustre-release"
        assert loaded.kver == "5.14.0-test.x86_64"
        assert loaded.base_image == "rocky9-base.ext4"
        assert loaded.os_id == "rocky9"

    def test_load_missing_metadata_defaults(self, tmp_sockets: Path) -> None:
        """Loading an old info file (no metadata) yields zero/empty defaults."""
        info = tmp_sockets / "old-vm.info"
        info.write_text(
            "NAME=old-vm\n"
            "IP=192.168.100.10\n"
            "PID=999\n"
            "TAP=tap-old\n"
            "MAC=AA:00:00:00:00:01\n"
            "VCPUS=2\n"
            "MEM=4096\n"
            "MDT_DISKS=1\n"
            "OST_DISKS=3\n"
            "IMAGE=\n"
            "KERNEL=\n"
        )
        vm = VMInfo.load("old-vm")
        assert vm.created == 0
        assert vm.last_boot == 0
        assert vm.last_deploy == 0
        assert vm.build_path == ""
        assert vm.kver == ""
        assert vm.base_image == ""
        assert vm.os_id == ""

    def test_update_field_adds_missing(self, tmp_sockets: Path) -> None:
        """_update_field adds a field that doesn't exist yet."""
        vm = VMInfo(name="add-test", ip="192.168.100.20")
        vm.save()
        vm._update_field("KVER", "5.14.0-new")
        text = vm.info_path.read_text()
        assert "KVER=5.14.0-new" in text

    def test_update_field_replaces_existing(self, tmp_sockets: Path) -> None:
        """_update_field replaces an existing field value."""
        vm = VMInfo(
            name="replace-test",
            ip="192.168.100.21",
            kver="old-version",
        )
        vm.save()
        assert "KVER=old-version" in vm.info_path.read_text()
        vm._update_field("KVER", "new-version")
        text = vm.info_path.read_text()
        assert "KVER=new-version" in text
        assert "KVER=old-version" not in text

    def test_update_pid(self, tmp_sockets: Path) -> None:
        """update_pid uses _update_field correctly."""
        vm = VMInfo(name="pid-test", ip="192.168.100.22", pid=100)
        vm.save()
        vm.update_pid(200)
        loaded = VMInfo.load("pid-test")
        assert loaded.pid == 200

    def test_update_last_boot(self, tmp_sockets: Path) -> None:
        """update_last_boot persists the timestamp."""
        vm = VMInfo(name="boot-test", ip="192.168.100.23")
        vm.save()
        vm.update_last_boot(1700005000)
        loaded = VMInfo.load("boot-test")
        assert loaded.last_boot == 1700005000

    def test_update_deploy(self, tmp_sockets: Path) -> None:
        """update_deploy sets all three deploy-related fields."""
        vm = VMInfo(name="deploy-test", ip="192.168.100.24")
        vm.save()
        vm.update_deploy(1700006000, "/builds/lustre", "5.14.0-x")
        loaded = VMInfo.load("deploy-test")
        assert loaded.last_deploy == 1700006000
        assert loaded.build_path == "/builds/lustre"
        assert loaded.kver == "5.14.0-x"


class TestVMInfoLoadNotFound:
    """VMNotFound raised for missing VMs."""

    def test_load_nonexistent(self, tmp_sockets: Path) -> None:
        with pytest.raises(VMNotFound):
            VMInfo.load("does-not-exist")


class TestUpdateFieldsAtomic:
    """_update_fields writes all fields in a single atomic operation."""

    def test_all_fields_persisted(self, tmp_sockets: Path) -> None:
        """Calling _update_fields with multiple keys persists every key."""
        vm = VMInfo(name="atomic-test", ip="192.168.100.30")
        vm.save()
        vm._update_fields(
            {"LAST_DEPLOY": 123, "BUILD_PATH": "/x", "KVER": "5.14"}
        )
        text = vm.info_path.read_text()
        assert "LAST_DEPLOY=123" in text
        assert "BUILD_PATH=/x" in text
        assert "KVER=5.14" in text

    def test_file_reloads_correctly(self, tmp_sockets: Path) -> None:
        """Round-trip: fields written by _update_fields survive VMInfo.load."""
        vm = VMInfo(name="roundtrip-test", ip="192.168.100.31")
        vm.save()
        vm._update_fields(
            {"LAST_DEPLOY": 999, "BUILD_PATH": "/lustre", "KVER": "6.1"}
        )
        loaded = VMInfo.load("roundtrip-test")
        assert loaded.last_deploy == 999
        assert loaded.build_path == "/lustre"
        assert loaded.kver == "6.1"

    def test_raises_vmnotfound_on_missing_file(
        self, tmp_sockets: Path
    ) -> None:
        """_update_fields raises when the .info file is gone.

        The previous silent no-op caused in-memory VMInfo state to
        diverge from disk with no signal -- a concurrent destroy or
        partially rolled-back create would silently lose updates.
        """
        from ltvm_pkg.vm_state import VMNotFound
        vm = VMInfo(name="no-file-test", ip="192.168.100.32")
        # Do NOT call vm.save() -- info file does not exist
        with pytest.raises(VMNotFound):
            vm._update_fields({"KVER": "5.14"})
        assert not vm.info_path.exists()


# ---------------------------------------------------------------------------
# vm_net: wait_for_ssh and deploy_ssh_key
# ---------------------------------------------------------------------------


class TestWaitForSsh:
    """wait_for_ssh raises SystemExit (via die()) when SSH never becomes ready."""

    def test_raises_on_timeout(self, tmp_sockets: Path) -> None:
        """When run_ssh always fails, wait_for_ssh exhausts retries and dies."""
        from ltvm_pkg.vm_net import wait_for_ssh

        fail_result = MagicMock()
        fail_result.returncode = 1

        with patch(
            "ltvm_pkg.vm_net.run_ssh", return_value=fail_result
        ), patch("ltvm_pkg.vm_net.time.sleep"):
            with pytest.raises(SystemExit):
                wait_for_ssh("192.168.100.99", max_wait=3)

    def test_returns_on_success(self, tmp_sockets: Path) -> None:
        """wait_for_ssh returns normally when SSH succeeds on the first attempt."""
        from ltvm_pkg.vm_net import wait_for_ssh

        ok_result = MagicMock()
        ok_result.returncode = 0

        with patch("ltvm_pkg.vm_net.run_ssh", return_value=ok_result):
            wait_for_ssh("192.168.100.50", max_wait=5)  # should not raise


class TestDeploySshKey:
    """deploy_ssh_key raises SystemExit (via die()) on SSH timeout."""

    def test_raises_on_timeout_expired(self, tmp_path: Path) -> None:
        """When run_ssh raises TimeoutExpired, deploy_ssh_key calls die()."""
        from ltvm_pkg.vm_net import deploy_ssh_key

        fake_ssh_dir = tmp_path / ".ssh"
        fake_ssh_dir.mkdir()
        (fake_ssh_dir / "id_rsa.pub").write_text("ssh-rsa AAAA fake-key user@host")

        with (
            patch(
                "ltvm_pkg.vm_net._real_user_ssh_dir",
                return_value=("testuser", fake_ssh_dir),
            ),
            patch(
                "ltvm_pkg.vm_net.run_ssh",
                side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10),
            ),
        ):
            with pytest.raises(SystemExit):
                deploy_ssh_key("192.168.100.99")
