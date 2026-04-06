#!/usr/bin/env python3
"""QEMU/KVM microVM manager for Lustre test environments.

This is the entry point. The implementation lives in:
  qemu/models.py    - VMInfo, ClusterInfo, constants
  qemu/process.py   - QEMU launch/kill, subprocess helpers
  qemu/net.py       - networking, DNS, SSH registry
  qemu/commands.py  - single-VM CLI commands
  qemu/cluster.py   - multi-node cluster commands
  qemu/cli.py       - argparse and dispatch
"""

import sys
from pathlib import Path

# Allow running as: python3 qemu/vm.py  (from repo root)
# or as: /usr/local/bin/vm.py  (installed symlink)
_here = Path(__file__).resolve().parent
if str(_here.parent) not in sys.path:
    sys.path.insert(0, str(_here.parent))

from qemu.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
