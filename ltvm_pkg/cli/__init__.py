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


def cmd_cluster(args: argparse.Namespace) -> int:
    use_json = args.json
    action = args.action
    cargs = args.cluster_args

    from ltvm_pkg.vm_cluster import (
        cmd_cluster_create as _qc_create,
    )
    from ltvm_pkg.vm_cluster import (
        cmd_cluster_deploy as _qc_deploy,
    )
    from ltvm_pkg.vm_cluster import (
        cmd_cluster_destroy as _qc_destroy,
    )
    from ltvm_pkg.vm_cluster import (
        cmd_cluster_exec as _qc_exec,
    )
    from ltvm_pkg.vm_cluster import (
        cmd_cluster_list as _qc_list,
    )
    from ltvm_pkg.vm_cluster import (
        cmd_cluster_ssh as _qc_ssh,
    )
    from ltvm_pkg.vm_cluster import (
        cmd_cluster_status as _qc_status,
    )

    def _call(fn: Any, ns: argparse.Namespace) -> int:
        try:
            fn(ns)
            return EXIT_OK
        except SystemExit as e:
            return int(e.code) if e.code is not None else EXIT_ERROR

    if action == "create":
        err = _require_root(use_json)
        if err is not None:
            return err
        if len(cargs) < 2:
            return _error(
                "cluster create requires a name and at least one node spec",
                use_json,
                hint="ltvm cluster create <name> [--target TARGET] "
                "[--arch ARCH] [--vcpus N] [--mem MB] "
                "<role:vm[:disks]> ...",
            )
        # Parse optional flags out of cargs; remaining positionals are
        # name + node specs.
        vcpus = 2
        # mem=None means "let cmd_create resolve from os_arts.default_mem"
        # so cluster nodes inherit the per-target default (e.g. rocky10
        # needs 4096) instead of being silently overridden.
        mem: int | None = None
        os_target: str | None = None
        arch: str | None = None
        disk_size: str | None = None
        nics: list[str] = []
        positional: list[str] = []
        i = 0
        while i < len(cargs):
            if cargs[i] == "--vcpus" and i + 1 < len(cargs):
                vcpus = int(cargs[i + 1])
                i += 2
            elif cargs[i] == "--mem" and i + 1 < len(cargs):
                mem = int(cargs[i + 1])
                i += 2
            elif cargs[i] == "--target" and i + 1 < len(cargs):
                os_target = cargs[i + 1]
                i += 2
            elif cargs[i] == "--arch" and i + 1 < len(cargs):
                arch = cargs[i + 1]
                i += 2
            elif cargs[i] == "--disk-size" and i + 1 < len(cargs):
                disk_size = cargs[i + 1]
                i += 2
            elif cargs[i] == "--nic" and i + 1 < len(cargs):
                # --nic is repeatable and applies uniformly to every
                # node in the cluster.  Validation happens inside each
                # node's `ltvm create`, so a bad value (e.g. softroce)
                # surfaces per-node with the usual follow-up-issue hint.
                nics.append(cargs[i + 1])
                i += 2
            elif cargs[i].startswith("--"):
                return _error(
                    f"cluster create: unknown argument '{cargs[i]}'",
                    use_json,
                    hint="valid: --vcpus, --mem, --target, --arch, "
                    "--disk-size, --nic",
                )
            else:
                positional.append(cargs[i])
                i += 1
        if len(positional) < 2:
            return _error(
                "cluster create requires a name and at least one node spec",
                use_json,
                hint="ltvm cluster create <name> [TARGET | --target TARGET] "
                "[--arch ARCH] [--vcpus N] [--mem MB] "
                "<role:vm[:disks]> ...",
            )
        # Accept a positional target after the cluster name: any
        # bare token (no ':') between the name and the first node
        # spec is treated as the OS target.  Node specs always
        # contain ':' (role:vm[:disks]) so this is unambiguous.  If
        # both the positional and --target are given, they must agree.
        pos_target: str | None = None
        if len(positional) >= 2 and ":" not in positional[1]:
            pos_target = positional[1]
            positional = [positional[0]] + positional[2:]
            if len(positional) < 2:
                return _error(
                    "cluster create requires at least one node spec",
                    use_json,
                    hint="ltvm cluster create <name> "
                    "[TARGET | --target TARGET] <role:vm[:disks]> ...",
                )
        if pos_target is not None and os_target is not None \
                and pos_target != os_target:
            return _error(
                f"--target {os_target!r} conflicts with positional "
                f"target {pos_target!r}; pass only one",
                use_json,
            )
        final_target = pos_target if pos_target is not None else os_target
        return _call(
            _qc_create,
            _qemu_ns(
                name=positional[0],
                nodes=positional[1:],
                vcpus=vcpus,
                mem=mem,
                os=final_target,
                arch=arch,
                disk_size=disk_size,
                nic=nics,
            ),
        )

    if action == "destroy":
        err = _require_root(use_json)
        if err is not None:
            return err
        if not cargs:
            return _error("cluster destroy requires a name", use_json)
        return _call(_qc_destroy, _qemu_ns(name=cargs[0]))

    if action == "deploy":
        if not cargs:
            return _error("cluster deploy requires a name", use_json)
        name = cargs[0]
        build_path = "."
        mount = False
        server_only = False
        force_compat = False
        i = 1
        while i < len(cargs):
            if cargs[i] == "--build" and i + 1 < len(cargs):
                build_path = cargs[i + 1]
                i += 2
            elif cargs[i] == "--mount":
                mount = True
                i += 1
            elif cargs[i] == "--server-only":
                server_only = True
                i += 1
            elif cargs[i] == "--force-compat":
                force_compat = True
                i += 1
            else:
                return _error(
                    f"cluster deploy: unknown argument '{cargs[i]}'",
                    use_json,
                    hint="valid: --build PATH, --mount, --server-only, "
                    "--force-compat",
                )
        return _call(
            _qc_deploy,
            _qemu_ns(
                name=name,
                lustre_source=build_path,
                mount=mount,
                server_only=server_only,
                force_compat=force_compat,
            ),
        )

    if action == "status":
        if not cargs:
            return _error("cluster status requires a name", use_json)
        return _call(_qc_status, _qemu_ns(name=cargs[0]))

    if action == "exec":
        if len(cargs) < 3:
            return _error(
                "cluster exec requires a name, role, and command",
                use_json,
                hint="ltvm cluster exec <name> <role> '<cmd>'",
            )
        return _call(
            _qc_exec,
            _qemu_ns(
                name=cargs[0],
                target=cargs[1],
                command=cargs[2:],
                timeout=120,
                json=use_json,
            ),
        )

    if action == "list":
        return _call(_qc_list, _qemu_ns())

    if action == "ssh":
        if len(cargs) < 2:
            return _error(
                "cluster ssh requires a name and a target (role or vm name)",
                use_json,
                hint="ltvm cluster ssh <name> <role> [cmd...]",
            )
        return _call(
            _qc_ssh,
            _qemu_ns(name=cargs[0], target=cargs[1], command=cargs[2:]),
        )

    return _error(f"Unknown cluster action: {action}", use_json)


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
