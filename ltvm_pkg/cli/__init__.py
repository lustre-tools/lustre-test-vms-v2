"""Command implementations for ltvm CLI.

Each cmd_* function takes an argparse.Namespace and returns an int
exit code.  Implementation now lives in per-concern submodules
(util, build, targets, fetch, deploy, cluster, vm, setup); this
package's __init__ re-exports every public name those submodules
expose so ``from ltvm_pkg.cli import cmd_build_all`` keeps working
and attribute-patching tests (``patch.object(cli_mod, "X")``) still
find every name they expect.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from ltvm_pkg import host_setup
from ltvm_pkg.deploy import deploy_to_vm, lustre_mount_vm
from ltvm_pkg.image_build import build_image, image_status
from ltvm_pkg.kernel_build import build_kernel, kernel_status
from ltvm_pkg.lustre_build import (
    build_lustre,
    read_staging_meta,
    staging_path,
)
from ltvm_pkg.lustre_compat import ValidationResult, validate_target
from ltvm_pkg.paths import load_meta_safe
from ltvm_pkg.release_package import (
    fetch_target,
    package_target,
    snapshot_lustre,
)
from ltvm_pkg.target_config import LustreMode, TargetConfig, list_targets

from ltvm_pkg.cli.util import (
    EXIT_ERROR,
    EXIT_NOT_FOUND,
    EXIT_OK,
    _artifact_label,
    _container_status,
    _emit_error,
    _error,
    _load_target,
    _load_target_args,
    _maybe_print_traceback,
    _output,
    _qemu_ns,
    _require_root,
)
from ltvm_pkg.cli.build import (
    _do_build_container,
    _gate_lustre_validation,
    _resolve_lustre_tree,
    cmd_build_all,
    cmd_build_container,
    cmd_build_image,
    cmd_build_kernel,
    cmd_build_lustre,
    cmd_build_mofed_kmods,
    cmd_build_shell,
    cmd_clean,
    cmd_status,
)

# GitHub repo for release downloads.  Override with LTVM_GITHUB_REPO
# so a fork can use `ltvm fetch` / `ltvm publish` without editing
# source.
GITHUB_REPO = os.environ.get("LTVM_GITHUB_REPO", "lustre-tools/lustre-test-vms")

# Imported AFTER GITHUB_REPO so fetch.py's module-level _gh_api reads
# the live value (tests flip it via monkeypatch).  fetch uses the
# _cli_attr indirection at call time, so order here is mostly a
# style issue -- but keep it consistent with the "constants first,
# submodule re-exports second" pattern.
from ltvm_pkg.cli.fetch import (  # noqa: E402
    _KVER_PREFIX_RE,
    _RHEL_RE,
    _find_release_url,
    _gh_api,
    _gh_next_link,
    _gh_release_upload,
    _kernel_release_signature,
    _list_releases,
    _release_matches_kernel,
    cmd_fetch,
    cmd_package,
    cmd_publish,
)
from ltvm_pkg.cli.targets import (  # noqa: E402
    _VALIDATE_EXIT,
    _release_status,
    _validation_result_to_dict,
    _variant_suffix_in_tag,
    cmd_target_export,
    cmd_target_show,
    cmd_targets,
    cmd_validate,
)
from ltvm_pkg.cli.vm import (  # noqa: E402
    _vm_call,
    cmd_console_log,
    cmd_crash_collect,
    cmd_list,
    cmd_nmi,
    cmd_restore,
    cmd_snapshot,
    cmd_vm_start,
    cmd_vm_stop,
)
from ltvm_pkg.cli.deploy import (  # noqa: E402
    cmd_deploy,
    cmd_llmount,
)
from ltvm_pkg.cli.cluster import cmd_cluster  # noqa: E402


def cmd_create(args: argparse.Namespace) -> int:
    use_json = args.json
    err = _require_root(use_json)
    if err is not None:
        return err
    from ltvm_pkg.vm_commands import cmd_create as _create

    return _vm_call(_create, args, use_json)


def cmd_destroy(args: argparse.Namespace) -> int:
    use_json = args.json
    err = _require_root(use_json)
    if err is not None:
        return err
    from ltvm_pkg.vm_commands import cmd_destroy as _destroy

    return _vm_call(_destroy, args, use_json)


def cmd_doctor(args: argparse.Namespace) -> int:
    use_json = args.json
    err = _require_root(use_json)
    if err is not None:
        return err
    from ltvm_pkg.vm_commands import cmd_doctor as _doctor

    return _vm_call(_doctor, args, use_json)


# ------------------------------------------------------------------
# Subcommand: setup
# ------------------------------------------------------------------


def cmd_setup(args: argparse.Namespace) -> int:
    """Run host setup (QEMU, network, scripts, SSH)."""
    use_json = args.json

    # Collect requested steps
    explicit = []
    if args.qemu:
        explicit.append("qemu")
    if args.network:
        explicit.append("network")
    if args.install:
        explicit.append("install")
    if args.ssh:
        explicit.append("ssh")
    steps = explicit or None  # None = all

    if args.verify:
        try:
            results = host_setup.verify(subnet=args.subnet)
        except Exception as e:
            return _error(str(e), use_json)
        if use_json:
            print(json.dumps(results, indent=2))
        else:
            host_setup.print_verify(results)
        return EXIT_OK if results["all_ok"] else EXIT_ERROR

    try:
        host_setup.run_setup(
            steps=steps,
            subnet=args.subnet,
            force=getattr(args, "force", False),
        )
    except RuntimeError as e:
        return _error(str(e), use_json)
    except Exception as e:
        return _error(f"Setup failed: {e}", use_json)

    return EXIT_OK


# ------------------------------------------------------------------
# Subcommand: update
# ------------------------------------------------------------------


def _ltvm_repo_root() -> Path:
    """Return the on-disk repo root for this ltvm checkout.

    `ltvm install` symlinks the entry-point script into ``/usr/local/bin``
    and then resolves that symlink at startup, so when the user runs
    ``ltvm update`` from an installed copy we still load ``ltvm_pkg``
    from the real checkout.  ``Path(__file__).resolve()`` follows any
    intermediate symlink and lands us in the real ``<repo>/ltvm_pkg/``,
    so the parent of the parent is the real repo root regardless of how
    ltvm was invoked.
    """
    # cli.py lives at <repo>/ltvm_pkg/cli.py
    return Path(__file__).resolve().parent.parent


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess:
    """Run a git command against ``repo`` and return the CompletedProcess."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=60,
    )


def _current_version() -> str:
    """Return the version string, recomputing fresh from disk.

    ``ltvm_pkg.__version__`` is captured at import time, so after a
    successful update we recompute via ``_compute_version`` to pick up
    the new git hash without forcing a reload.
    """
    from ltvm_pkg import _compute_version

    return _compute_version()


def cmd_update(args: argparse.Namespace) -> int:
    """Pull the latest ltvm from the upstream git remote.

    Refuses to act on a dirty working tree unless --force is given.
    Uses --ff-only so we never silently create a merge commit on the
    user's checkout.  Reports the old and new version on success.
    """
    use_json = args.json
    # git pull writes into the checkout (.git/FETCH_HEAD, refs, ...).
    # In shared-install deployments the ltvm repo is owned by one user
    # (e.g. admin) and everyone else runs ltvm through PATH, so letting
    # the unprivileged caller hit this leaks a git permission error
    # mid-command.  Require root up front so sudo is the obvious fix.
    err = _require_root(use_json)
    if err is not None:
        return err
    repo = _ltvm_repo_root()

    if not (repo / ".git").exists():
        return _error(
            f"{repo} is not a git checkout -- cannot update",
            use_json,
            hint="Reinstall ltvm by cloning "
            "https://github.com/lustre-tools/lustre-test-vms",
        )

    old_version = _current_version()

    # --check: just report whether an update is available
    if getattr(args, "check", False):
        try:
            _git(repo, "fetch", "--quiet")
        except subprocess.CalledProcessError as e:
            return _error(
                f"git fetch failed: {e.stderr.strip() or e}", use_json
            )
        try:
            behind = _git(
                repo, "rev-list", "--count", "HEAD..@{u}"
            ).stdout.strip()
        except subprocess.CalledProcessError as e:
            return _error(
                f"git rev-list failed: {e.stderr.strip() or e}",
                use_json,
                hint="Is the current branch tracking an upstream?",
            )
        n = int(behind or "0")
        result = {
            "version": old_version,
            "behind": n,
            "update_available": n > 0,
        }
        _output(result, use_json)
        return EXIT_OK

    # Refuse on dirty working tree unless forced
    if not getattr(args, "force", False):
        status = _git(repo, "status", "--porcelain").stdout
        if status.strip():
            return _error(
                "working tree has local changes -- refusing to update",
                use_json,
                hint="Commit or stash your changes, or pass --force",
            )

    try:
        _git(repo, "fetch", "--quiet")
    except subprocess.CalledProcessError as e:
        return _error(f"git fetch failed: {e.stderr.strip() or e}", use_json)

    try:
        pull = _git(repo, "pull", "--ff-only")
    except subprocess.CalledProcessError as e:
        return _error(
            f"git pull --ff-only failed: {e.stderr.strip() or e}",
            use_json,
            hint="The local branch has diverged from upstream. "
            "Resolve manually with git.",
        )

    # Refresh _build_info.py so the new short hash takes effect
    # immediately, even if the post-commit hook isn't installed.
    try:
        new_hash = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
        if new_hash:
            (repo / "ltvm_pkg" / "_build_info.py").write_text(
                '"""Auto-generated by ltvm update. Do not edit or commit."""\n\n'
                f'BUILD_HASH = "{new_hash}"\n'
            )
    except (subprocess.CalledProcessError, OSError):
        # Non-fatal: version reporting will fall back to the runtime
        # git rev-parse path.
        pass

    new_version = _current_version()

    result = {
        "old_version": old_version,
        "new_version": new_version,
        "changed": old_version != new_version,
        "git": pull.stdout.strip(),
    }
    if not use_json:
        if old_version == new_version:
            print(f"Already up to date at {new_version}")
        else:
            print(f"Updated ltvm: {old_version} -> {new_version}")
        if pull.stdout.strip():
            print(pull.stdout.strip())
    else:
        print(json.dumps(result, indent=2))
    return EXIT_OK
