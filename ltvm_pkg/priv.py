"""Privileged-operation helpers.

ltvm runs as the invoking user and elevates only the specific
operations that require root (bridge/tap setup, /etc/hosts edits,
qemu launch, losetup/mount, etc.).  This module exposes the two
helpers that make that uniform across host_setup, vm_commands,
vm_net, qemu_run, image_export, and vm_cluster: ``sudo_run()``
prefixes a command with ``sudo`` when not already root, and
``sudo_prime()`` warms the sudo timestamp upfront so later
``sudo_run()`` calls don't surprise the user with a mid-flow
password prompt.

These helpers are deliberately dependency-free (stdlib only) so
any module can import them without risking a circular import.
"""

from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger(__name__)


def _run(
    cmd: list[str],
    *,
    check: bool = True,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command, optionally capturing output, raising on non-zero."""
    log.debug("run: %s", " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, capture_output=quiet, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(
            f"Command failed (rc={r.returncode}): "
            f"{' '.join(str(c) for c in cmd)}"
        )
    return r


def sudo_run(
    cmd: list[str],
    *,
    check: bool = True,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command under sudo (no-op prefix if already root)."""
    if os.geteuid() == 0:
        return _run(cmd, check=check, quiet=quiet)
    return _run(["sudo", *cmd], check=check, quiet=quiet)


def sudo_prime(reason: str) -> None:
    """Prompt for sudo credentials up front so later ``sudo_run()``
    calls don't interrupt with a surprise password prompt mid-flow.

    Skips the prompt entirely when ``sudo -n true`` succeeds, which
    covers both an unexpired sudo timestamp and ``NOPASSWD`` rules --
    in those cases ``sudo -v`` would still try to authenticate and
    fail in non-tty contexts (subshells, hooks, CI), aborting even
    though every later ``sudo`` would have worked.
    """
    if os.geteuid() == 0:
        return
    if _run(
        ["sudo", "-n", "true"], check=False, quiet=True
    ).returncode == 0:
        return
    log.info("%s -- prompting for sudo credentials now.", reason)
    _run(["sudo", "-v"])
