"""End-to-end tests for Lustre deployment and mounting.

Two dependency levels:

* ``TestLustreMount`` -- deploy Lustre from a build tree, then verify
  mount and basic I/O.  Requires a built Lustre tree whose modules
  match the VM's running kernel.

* ``TestLustreDeploy`` -- focuses on the deploy step itself (sync,
  depmod, module install).  Same dependency.

Set ``LTVM_LUSTRE_TREE`` to the path of a built Lustre tree that
matches the VM kernel.  Tests are skipped when the variable is unset
or the tree has no .ko files.

Run with::

    LTVM_LUSTRE_TREE=~/code_shared/master_checkouts/1 make test-e2e

or directly::

    LTVM_LUSTRE_TREE=~/code_shared/master_checkouts/1 \\
        uv run pytest tests/e2e/test_lustre_deploy.py -v --no-cov -m e2e
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lib.runtime import deploy, lustre_mount, vm_destroy, vm_ensure, vm_exec

# ---------------------------------------------------------------------------
# Build tree resolution
# ---------------------------------------------------------------------------


def _find_lustre_tree() -> Path | None:
    """Return the Lustre build tree from LTVM_LUSTRE_TREE, or None."""
    override = os.environ.get("LTVM_LUSTRE_TREE")
    if not override:
        return None
    p = Path(override).expanduser().resolve()
    if not p.is_dir():
        return None
    # Must have built .ko files
    if not any(f for f in p.rglob("*.ko") if "kconftest" not in str(f)):
        return None
    return p


LUSTRE_TREE = _find_lustre_tree()

skip_no_tree = pytest.mark.skipif(
    LUSTRE_TREE is None,
    reason=(
        "LTVM_LUSTRE_TREE not set or tree has no .ko files. "
        "Set it to a built Lustre tree matching the VM kernel, e.g.: "
        "LTVM_LUSTRE_TREE=~/code_shared/master_checkouts/1"
    ),
)

# ---------------------------------------------------------------------------
# VM naming
# ---------------------------------------------------------------------------

VM_BASE = f"ltvm-e2e-lustre-{os.getpid()}"


def _vm_name(suffix: str) -> str:
    safe = suffix.replace("[", "").replace("]", "").replace(" ", "_")
    return f"{VM_BASE}-{safe[:16]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def deployed_vm(request: pytest.FixtureRequest) -> str:  # type: ignore[return]
    """VM with Lustre deployed (not yet mounted). Destroys on teardown."""
    assert LUSTRE_TREE is not None
    name = _vm_name(request.node.name)
    vm_ensure(name, vcpus=2, mem=4096, mdt_disks=1, ost_disks=3)
    result = deploy(name, build_path=LUSTRE_TREE, mount=False)
    if not result["ok"]:
        vm_destroy(name)
        pytest.fail(
            f"deploy() failed (rc={result['returncode']}):\n{result['output']}"
        )
    yield name
    vm_destroy(name)


@pytest.fixture
def mounted_vm(request: pytest.FixtureRequest) -> str:  # type: ignore[return]
    """VM with Lustre fully deployed and mounted. Destroys on teardown."""
    assert LUSTRE_TREE is not None
    name = _vm_name(request.node.name)
    vm_ensure(name, vcpus=2, mem=4096, mdt_disks=1, ost_disks=3)
    result = deploy(name, build_path=LUSTRE_TREE, mount=True)
    if not result["ok"]:
        vm_destroy(name)
        pytest.fail(
            f"deploy --mount failed (rc={result['returncode']}):\n"
            f"{result['output']}"
        )
    yield name
    vm_destroy(name)


# ---------------------------------------------------------------------------
# TestLustreDeploy -- verify the deploy step
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@skip_no_tree
class TestLustreDeploy:
    """Verify deploy-lustre.sh syncs modules and binaries correctly."""

    def test_deploy_ok(self, deployed_vm: str) -> None:
        """deploy() without mount exits ok."""
        # If we reach here the fixture already succeeded; just sanity-check
        # that lctl is present in the VM.
        result = vm_exec(deployed_vm, "lctl --version", timeout=15)
        assert result["ok"], (
            f"lctl not found after deploy (rc={result['returncode']}): "
            f"{result['output']}"
        )

    def test_modules_present(self, deployed_vm: str) -> None:
        """Lustre .ko files are present at the expected path after deploy."""
        assert LUSTRE_TREE is not None
        result = vm_exec(
            deployed_vm,
            f"find {LUSTRE_TREE}/lustre -name '*.ko' | head -5",
            timeout=15,
        )
        assert result["ok"] and result["output"].strip(), (
            f"No .ko files found under {LUSTRE_TREE}/lustre in VM: "
            f"{result['output']}"
        )


# ---------------------------------------------------------------------------
# TestLustreMount -- verify mount + I/O
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@skip_no_tree
class TestLustreMount:
    """Verify Lustre mounts and provides a working filesystem."""

    def test_mount_via_lustre_mount(self, deployed_vm: str) -> None:
        """lustre_mount() starts Lustre after a deploy-only step."""
        assert LUSTRE_TREE is not None
        result = lustre_mount(deployed_vm, build_path=LUSTRE_TREE)
        assert result["ok"], (
            f"lustre_mount() failed (rc={result['returncode']}):\n"
            f"{result['output']}"
        )
        mount_check = vm_exec(
            deployed_vm, "mountpoint -q /mnt/lustre", timeout=10
        )
        assert mount_check["ok"], "/mnt/lustre not mounted after lustre_mount()"

    def test_lustre_mounted(self, mounted_vm: str) -> None:
        """/mnt/lustre is a mountpoint after deploy --mount."""
        result = vm_exec(mounted_vm, "mountpoint -q /mnt/lustre", timeout=15)
        assert result["ok"], (
            f"/mnt/lustre is not mounted "
            f"(rc={result['returncode']}): {result['output']}"
        )

    def test_modules_loaded(self, mounted_vm: str) -> None:
        """lctl dl shows llite and ldlm modules."""
        result = vm_exec(mounted_vm, "lctl dl", timeout=15)
        assert result["ok"], f"lctl dl failed: {result['output']}"
        out = result["output"]
        assert "llite" in out, f"llite not in lctl dl:\n{out}"
        assert "ldlm" in out, f"ldlm not in lctl dl:\n{out}"

    def test_lustre_version(self, mounted_vm: str) -> None:
        """lctl get_param version returns a Lustre version string."""
        result = vm_exec(mounted_vm, "lctl get_param version", timeout=15)
        assert result["ok"], (
            f"lctl get_param version failed: {result['output']}"
        )
        assert "lustre" in result["output"].lower(), (
            f"Unexpected version output: {result['output']}"
        )

    def test_basic_io(self, mounted_vm: str) -> None:
        """Write and read back a file on /mnt/lustre."""
        payload = "ltvm-e2e-io-test-12345"
        result = vm_exec(
            mounted_vm,
            f"echo '{payload}' > /mnt/lustre/e2e_test && "
            f"cat /mnt/lustre/e2e_test",
            timeout=30,
        )
        assert result["ok"], f"write/read failed: {result['output']}"
        assert payload in result["output"], (
            f"Readback mismatch: {result['output']!r}"
        )
        vm_exec(mounted_vm, "rm -f /mnt/lustre/e2e_test", timeout=10)

    def test_lfs_df(self, mounted_vm: str) -> None:
        """lfs df shows filesystem summary."""
        result = vm_exec(mounted_vm, "lfs df /mnt/lustre", timeout=15)
        assert result["ok"], f"lfs df failed: {result['output']}"
        assert "lustre" in result["output"].lower(), (
            f"Unexpected lfs df output:\n{result['output']}"
        )

    def test_stripe_info(self, mounted_vm: str) -> None:
        """lfs getstripe works on a new file."""
        result = vm_exec(
            mounted_vm,
            "touch /mnt/lustre/e2e_stripe && "
            "lfs getstripe /mnt/lustre/e2e_stripe && "
            "rm /mnt/lustre/e2e_stripe",
            timeout=20,
        )
        assert result["ok"], f"lfs getstripe failed: {result['output']}"
        assert "stripe" in result["output"].lower(), (
            f"Expected stripe info, got: {result['output']}"
        )
