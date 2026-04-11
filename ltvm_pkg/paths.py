"""Single source of truth for the ltvm repo root.

target_config.py and vm_state.py both need to resolve where outputs
live.  Round 19 found the helper duplicated in both modules with a
comment literally saying "must agree with the other one" -- a
maintenance bomb waiting to go off.  This module is the canonical
implementation; both consumers re-export the same function so any
future changes happen in exactly one place.

We deliberately avoid imports beyond pathlib/os so this can be pulled
in by every other module without circular-import risk.
"""

from __future__ import annotations

import os
from pathlib import Path


def find_ltvm_root() -> Path:
    """Resolve the ltvm repo root.

    Resolution order:
      1. LTVM_ROOT environment variable, if set.
      2. /usr/local/bin/ltvm symlink target's parent (the install path).
      3. This file's grandparent (the source-tree fallback).

    Build outputs land under <root>/output/, and the runtime later reads
    them from the same path -- so target_config.py (build side) and
    vm_state.py (runtime side) MUST agree on this resolution.
    """
    env = os.environ.get("LTVM_ROOT")
    if env:
        return Path(env)
    ltvm_link = Path("/usr/local/bin/ltvm")
    if ltvm_link.is_symlink():
        return ltvm_link.resolve().parent
    return Path(__file__).resolve().parent.parent
