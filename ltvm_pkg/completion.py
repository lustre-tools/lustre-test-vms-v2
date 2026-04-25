"""argcomplete dynamic completers for the ltvm CLI.

Each completer returns a list of candidate strings given the current
prefix.  argcomplete filters by prefix itself, so we just return every
candidate we know about.  All of these are called during tab completion,
where an unhandled exception just produces no completions -- so we wrap
everything defensively.  A broken target registry shouldn't make tab
hang or dump a traceback into the user's prompt.
"""

from __future__ import annotations

import argparse
from typing import Any


def _safe(fn):  # type: ignore[no-untyped-def]
    """Wrap a completer so any exception degrades to 'no completions'."""

    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return list(fn(*args, **kwargs))
        except Exception:
            return []

    return wrapper


@_safe
def complete_targets(
    prefix: str = "",
    parsed_args: argparse.Namespace | None = None,
    **kwargs: Any,
) -> list[str]:
    from .target_config import list_targets

    return list_targets()


@_safe
def complete_vms(
    prefix: str = "",
    parsed_args: argparse.Namespace | None = None,
    **kwargs: Any,
) -> list[str]:
    from .vm_state import VMInfo

    return VMInfo.all_names()


@_safe
def complete_clusters(
    prefix: str = "",
    parsed_args: argparse.Namespace | None = None,
    **kwargs: Any,
) -> list[str]:
    from .vm_state import ClusterInfo

    return ClusterInfo.all_names()


@_safe
def complete_kernels(
    prefix: str = "",
    parsed_args: argparse.Namespace | None = None,
    **kwargs: Any,
) -> list[str]:
    """Complete --kernel values.  Scopes to the target if one is parsed."""
    from .target_config import TargetConfig, list_targets

    target = getattr(parsed_args, "target", None) if parsed_args else None
    if target:
        return TargetConfig(target).declared_kernels()
    # No target yet -- union across all known targets so the user still
    # sees sensible completions when --kernel is typed before the target.
    seen: set[str] = set()
    out: list[str] = []
    for name in list_targets():
        try:
            for k in TargetConfig(name).declared_kernels():
                if k not in seen:
                    seen.add(k)
                    out.append(k)
        except Exception:
            continue
    return out


@_safe
def complete_variants(
    prefix: str = "",
    parsed_args: argparse.Namespace | None = None,
    **kwargs: Any,
) -> list[str]:
    from .target_config import TargetConfig, list_targets

    target = getattr(parsed_args, "target", None) if parsed_args else None
    names: set[str] = {"base"}
    targets = [target] if target else list_targets()
    for t in targets:
        try:
            for v in TargetConfig(t).variants().keys():
                names.add(v)
        except Exception:
            continue
    return sorted(names)


@_safe
def complete_cluster_remainder(
    prefix: str = "",
    parsed_args: argparse.Namespace | None = None,
    **kwargs: Any,
) -> list[str]:
    """Completer for the cluster-command REMAINDER action.

    The REMAINDER captures everything after `cluster <action>`.  For most
    actions the next positional is a cluster name, so returning cluster
    names is the useful default.  For `cluster create` it's slightly
    off (the user is typing a *new* name), but showing taken names is
    mild noise at worst.
    """
    from .vm_state import ClusterInfo

    return ClusterInfo.all_names()
