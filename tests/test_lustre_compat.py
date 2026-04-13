"""Tests for ltvm_pkg/lustre_compat.py parsers."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from ltvm_pkg.lustre_compat import (
    ChangeLogEntry,
    TargetIn,
    parse_changelog,
    parse_target_in,
    parse_which_patch,
)

FIXTURES = Path(__file__).parent / "fixtures" / "lustre_compat"


def _mktree(root: Path) -> Path:
    (root / "lustre/kernel_patches/targets").mkdir(parents=True)
    return root


class TestFixtureTree:
    """Smoke tests against the checked-in fixture tree."""

    def test_which_patch(self) -> None:
        got = parse_which_patch(FIXTURES / "good_tree")
        assert got["5.14-rhel9.7.series"] == "5.14.0-611.13.1.el9"
        assert got["6.12-rhel10.0.series"] == "6.12.0-55.43.1.el10"

    def test_changelog(self) -> None:
        entry = parse_changelog(FIXTURES / "good_tree")
        assert "5.14.0-611.13.1.el9" in entry.server_primary
        assert "5.4.0" in entry.server_best_effort
        assert "6.12.0-55.43.1.el10" in entry.client_primary

    def test_target_in(self) -> None:
        ti = parse_target_in(FIXTURES / "good_tree", "5.14-rhel9.7")
        assert ti.KERNEL_SRPM == "kernel-5.14.0-611.13.1.el9_7.src.rpm"
        fc = parse_target_in(FIXTURES / "good_tree", "3.x-fc18")
        assert fc.SERIES == "3.x-fc18.series"


# ------------------------------------------------------------------
# parse_which_patch
# ------------------------------------------------------------------


WHICH_PATCH_SAMPLE = """\
Note that Lustre server kernels do not REQUIRE patches.

PATCH SERIES FOR SERVER KERNELS:
4.18-rhel8.10.series    4.18.0-553.89.1.el8  (RHEL 8.10)
5.14-rhel9.7.series     5.14.0-611.13.1.el9  (RHEL 9.7)
6.12-rhel10.0.series    6.12.0-55.43.1.el10  (RHEL 10.0)

See lustre/ChangeLog for supported client kernel versions.
"""


class TestParseWhichPatch:
    def test_well_formed(self, tmp_path: Path) -> None:
        tree = _mktree(tmp_path)
        (tree / "lustre/kernel_patches/which_patch").write_text(
            WHICH_PATCH_SAMPLE
        )
        got = parse_which_patch(tree)
        assert got == {
            "4.18-rhel8.10.series": "4.18.0-553.89.1.el8",
            "5.14-rhel9.7.series": "5.14.0-611.13.1.el9",
            "6.12-rhel10.0.series": "6.12.0-55.43.1.el10",
        }

    def test_missing_file(self, tmp_path: Path) -> None:
        tree = _mktree(tmp_path)
        with pytest.raises(FileNotFoundError, match="which_patch"):
            parse_which_patch(tree)

    def test_missing_header(self, tmp_path: Path) -> None:
        tree = _mktree(tmp_path)
        (tree / "lustre/kernel_patches/which_patch").write_text(
            "no table here\n"
        )
        with pytest.raises(ValueError, match="patch series table"):
            parse_which_patch(tree)


# ------------------------------------------------------------------
# parse_changelog
# ------------------------------------------------------------------


CHANGELOG_SAMPLE = """\
TBD Whamcloud
\t* version 2.18.0
\t* See https://wiki.whamcloud.com/ for support matrix.
\t* Server primary kernels built and tested during release cycle:
\t  5.14.0-611.13.1.el9  (RHEL9.7)
\t  4.18.0-553.89.1.el8  (RHEL8.10)
\t* Other server kernels known to build and work at some point (others may also work):
\t  4.18.0-425.10.1.el8  (RHEL8.7)
\t  5.14.21-150500.55.65 (SLES15 SP5)
\t  vanilla linux 5.4.0  (ZFS + ldiskfs)
\t* ldiskfs needs an ldiskfs patch series for that kernel
\t* Client primary kernels built and tested during release cycle:
\t  6.12.0-55.43.1.el10  (RHEL10.0)
\t  5.14.0-611.13.1.el9  (RHEL9.7)
\t* Other clients known to build on these kernels at some point (others may also work):
\t  4.18.0-348.23.1.el8  (RHEL8.5)
\t  5.3.18-24.96         (SLES15 SP2)

2025-01-01 Whamcloud
\t* version 2.17.0
"""


class TestParseChangelog:
    def test_well_formed(self, tmp_path: Path) -> None:
        tree = tmp_path
        (tree / "lustre").mkdir()
        (tree / "lustre/ChangeLog").write_text(CHANGELOG_SAMPLE)
        entry = parse_changelog(tree)
        assert isinstance(entry, ChangeLogEntry)
        assert entry.server_primary == [
            "5.14.0-611.13.1.el9",
            "4.18.0-553.89.1.el8",
        ]
        assert entry.server_best_effort == [
            "4.18.0-425.10.1.el8",
            "5.14.21-150500.55.65",
            "5.4.0",
        ]
        assert entry.client_primary == [
            "6.12.0-55.43.1.el10",
            "5.14.0-611.13.1.el9",
        ]
        assert entry.client_best_effort == [
            "4.18.0-348.23.1.el8",
            "5.3.18-24.96",
        ]

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="ChangeLog"):
            parse_changelog(tmp_path)

    def test_malformed_no_headers(self, tmp_path: Path) -> None:
        (tmp_path / "lustre").mkdir()
        (tmp_path / "lustre/ChangeLog").write_text(
            "TBD Whamcloud\n\t* version 2.18.0\n\t* some unrelated note\n"
        )
        with pytest.raises(ValueError, match="no recognized"):
            parse_changelog(tmp_path)

    def test_only_top_entry_consumed(self, tmp_path: Path) -> None:
        """Kernels below the first release header must not leak in."""
        (tmp_path / "lustre").mkdir()
        (tmp_path / "lustre/ChangeLog").write_text(
            textwrap.dedent("""\
                TBD Whamcloud
                \t* version 2.18.0
                \t* Server primary kernels built and tested during release cycle:
                \t  5.14.0-611.13.1.el9  (RHEL9.7)

                2024-01-01 Whamcloud
                \t* version 2.17.0
                \t* Server primary kernels built and tested during release cycle:
                \t  9.9.9-old.el9  (should not appear)
                """)
        )
        entry = parse_changelog(tmp_path)
        assert entry.server_primary == ["5.14.0-611.13.1.el9"]

    def test_frozen(self, tmp_path: Path) -> None:
        (tmp_path / "lustre").mkdir()
        (tmp_path / "lustre/ChangeLog").write_text(CHANGELOG_SAMPLE)
        entry = parse_changelog(tmp_path)
        with pytest.raises(Exception):
            entry.server_primary = []  # type: ignore[misc]


# ------------------------------------------------------------------
# parse_target_in
# ------------------------------------------------------------------


TARGET_IN_RHEL97 = """\
lnxmaj="5.14.0"
lnxrel="611.13.1.el9_7"

KERNEL_SRPM=kernel-${lnxmaj}-${lnxrel}.src.rpm
SERIES=5.14-rhel9.7.series
EXTRA_VERSION=${lnxrel}_lustre.@VERSION@
LUSTRE_VERSION=@VERSION@
"""

TARGET_IN_NO_LUSTRE_VER = """\
lnxmaj=3.6.10
lnxrel=4.fc18

KERNEL_SRPM=kernel-${lnxmaj}-${lnxrel}.src.rpm
SERIES=""
"""


class TestParseTargetIn:
    def test_resolves_expansions(self, tmp_path: Path) -> None:
        tree = _mktree(tmp_path)
        (
            tree / "lustre/kernel_patches/targets/5.14-rhel9.7.target.in"
        ).write_text(TARGET_IN_RHEL97)
        ti = parse_target_in(tree, "5.14-rhel9.7")
        assert isinstance(ti, TargetIn)
        assert ti.lnxmaj == "5.14.0"
        assert ti.lnxrel == "611.13.1.el9_7"
        assert ti.KERNEL_SRPM == "kernel-5.14.0-611.13.1.el9_7.src.rpm"
        assert ti.SERIES == "5.14-rhel9.7.series"

    def test_empty_series_defaults(self, tmp_path: Path) -> None:
        tree = _mktree(tmp_path)
        (tree / "lustre/kernel_patches/targets/3.x-fc18.target.in").write_text(
            TARGET_IN_NO_LUSTRE_VER
        )
        ti = parse_target_in(tree, "3.x-fc18")
        assert ti.SERIES == "3.x-fc18.series"
        assert ti.KERNEL_SRPM == "kernel-3.6.10-4.fc18.src.rpm"

    def test_fallback_to_plain_target(self, tmp_path: Path) -> None:
        tree = _mktree(tmp_path)
        (tree / "lustre/kernel_patches/targets/plain.target").write_text(
            "lnxmaj=5.14.0\nlnxrel=100.el9\nSERIES=plain.series\n"
            "KERNEL_SRPM=kernel-5.14.0-100.el9.src.rpm\n"
        )
        ti = parse_target_in(tree, "plain")
        assert ti.lnxmaj == "5.14.0"
        assert ti.SERIES == "plain.series"

    def test_missing_file(self, tmp_path: Path) -> None:
        tree = _mktree(tmp_path)
        with pytest.raises(FileNotFoundError, match="target file not found"):
            parse_target_in(tree, "nope")

    def test_missing_lnxmaj(self, tmp_path: Path) -> None:
        tree = _mktree(tmp_path)
        (tree / "lustre/kernel_patches/targets/bad.target.in").write_text(
            "SERIES=bad.series\n"
        )
        with pytest.raises(ValueError, match="Cannot parse"):
            parse_target_in(tree, "bad")

    def test_frozen(self, tmp_path: Path) -> None:
        tree = _mktree(tmp_path)
        (
            tree / "lustre/kernel_patches/targets/5.14-rhel9.7.target.in"
        ).write_text(TARGET_IN_RHEL97)
        ti = parse_target_in(tree, "5.14-rhel9.7")
        with pytest.raises(Exception):
            ti.lnxmaj = "x"  # type: ignore[misc]
