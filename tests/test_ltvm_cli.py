"""Tests for the ltvm CLI entry point.

Covers argument parsing, output formatting, subcommand dispatch,
and error handling -- all without real build infra.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Load the ltvm module (no .py extension -- must use SourceFileLoader)
# ---------------------------------------------------------------------------

_LTVM_PATH = str(Path(__file__).parent.parent / "ltvm")


def _load_ltvm() -> Any:
    loader = importlib.machinery.SourceFileLoader("ltvm", _LTVM_PATH)
    spec = importlib.util.spec_from_loader("ltvm", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ltvm = _load_ltvm()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_main(argv: list[str], capsys: pytest.CaptureFixture[str]) -> int:
    """Patch sys.argv and call ltvm.main(); return exit code."""
    with patch.object(sys, "argv", ["ltvm"] + argv):
        return ltvm.main()


# ---------------------------------------------------------------------------
# Parser structure tests (no side effects)
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_returns_parser(self) -> None:
        p = ltvm.build_parser()
        assert p is not None
        assert p.prog == "ltvm"

    def test_json_flag_default_false(self) -> None:
        p = ltvm.build_parser()
        args = p.parse_args(["status"])
        assert args.json is False

    def test_json_flag_true_when_passed(self) -> None:
        # --json is defined on each subparser (via parents=[common]),
        # so it must appear after the subcommand name.
        p = ltvm.build_parser()
        args = p.parse_args(["status", "--json"])
        assert args.json is True

    def test_verbose_flag(self) -> None:
        p = ltvm.build_parser()
        args = p.parse_args(["status", "-v"])
        assert args.verbose is True

    def test_status_subcommand_sets_func(self) -> None:
        p = ltvm.build_parser()
        args = p.parse_args(["status"])
        assert args.func is ltvm.cmd_status

    def test_build_all_target_positional(self) -> None:
        p = ltvm.build_parser()
        args = p.parse_args(["build-all", "rocky9"])
        assert args.target == "rocky9"
        assert args.force is False

    def test_build_all_force_flag(self) -> None:
        p = ltvm.build_parser()
        args = p.parse_args(["build-all", "rocky9", "--force"])
        assert args.force is True

    def test_vm_action_parsed(self) -> None:
        p = ltvm.build_parser()
        args = p.parse_args(["vm", "list"])
        assert args.action == "list"

    def test_deploy_subcommand(self) -> None:
        p = ltvm.build_parser()
        args = p.parse_args(["deploy", "myvm", "--mount"])
        assert args.vm == "myvm"
        assert args.mount is True


# ---------------------------------------------------------------------------
# --help exits 0
# ---------------------------------------------------------------------------


class TestHelp:
    def test_help_exits_zero(self) -> None:
        p = ltvm.build_parser()
        with pytest.raises(SystemExit) as exc_info:
            p.parse_args(["--help"])
        assert exc_info.value.code == 0

    def test_subcommand_help_exits_zero(self) -> None:
        p = ltvm.build_parser()
        with pytest.raises(SystemExit) as exc_info:
            p.parse_args(["status", "--help"])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# No subcommand: main() prints help and returns EXIT_ERROR
# ---------------------------------------------------------------------------


class TestNoSubcommand:
    def test_no_command_returns_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = _run_main([], capsys)
        assert rc == ltvm.EXIT_ERROR

    def test_no_command_prints_usage(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run_main([], capsys)
        out = capsys.readouterr().out
        assert "ltvm" in out


# ---------------------------------------------------------------------------
# _output helper: JSON vs human-readable
# ---------------------------------------------------------------------------


class TestOutputHelper:
    def test_string_human(self, capsys: pytest.CaptureFixture[str]) -> None:
        ltvm._output("hello world", use_json=False)
        assert capsys.readouterr().out.strip() == "hello world"

    def test_string_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        ltvm._output("hello world", use_json=True)
        out = capsys.readouterr().out
        assert json.loads(out) == "hello world"

    def test_dict_human(self, capsys: pytest.CaptureFixture[str]) -> None:
        ltvm._output({"key": "val"}, use_json=False)
        assert "key: val" in capsys.readouterr().out

    def test_dict_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        ltvm._output({"key": "val"}, use_json=True)
        out = capsys.readouterr().out
        assert json.loads(out) == {"key": "val"}

    def test_list_human(self, capsys: pytest.CaptureFixture[str]) -> None:
        ltvm._output(["a", "b"], use_json=False)
        out = capsys.readouterr().out
        assert "a" in out
        assert "b" in out

    def test_list_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        ltvm._output(["a", "b"], use_json=True)
        out = capsys.readouterr().out
        assert json.loads(out) == ["a", "b"]


# ---------------------------------------------------------------------------
# _error and _not_found helpers
# ---------------------------------------------------------------------------


class TestErrorHelpers:
    def test_error_human(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = ltvm._error("something went wrong", use_json=False)
        assert rc == ltvm.EXIT_ERROR
        assert "something went wrong" in capsys.readouterr().err

    def test_error_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = ltvm._error("bad thing", use_json=True)
        assert rc == ltvm.EXIT_ERROR
        err = capsys.readouterr().err
        payload = json.loads(err)
        assert payload["error"] == "bad thing"

    def test_error_with_hint_json(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ltvm._error("oops", use_json=True, hint="try this")
        payload = json.loads(capsys.readouterr().err)
        assert payload["hint"] == "try this"

    def test_not_found_returns_exit_not_found(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = ltvm._not_found("no such target", use_json=False)
        assert rc == ltvm.EXIT_NOT_FOUND

    def test_not_found_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        ltvm._not_found("missing", use_json=True)
        payload = json.loads(capsys.readouterr().err)
        assert "error" in payload


# ---------------------------------------------------------------------------
# _load_target: unknown target returns EXIT_NOT_FOUND
# ---------------------------------------------------------------------------


class TestLoadTarget:
    def test_unknown_target_returns_not_found(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # TargetConfig raises ValueError for unknown target names;
        # _load_target must catch it and return EXIT_NOT_FOUND.
        def _raise(name: str) -> None:
            raise ValueError(f"Unknown target: {name}")

        with patch.object(ltvm, "TargetConfig", side_effect=_raise):
            tc, code = ltvm._load_target("no_such_target", use_json=False)
        assert tc is None
        assert code == ltvm.EXIT_NOT_FOUND


# ---------------------------------------------------------------------------
# cmd_status: mocked list_targets + TargetConfig
# ---------------------------------------------------------------------------


class TestCmdStatus:
    def test_status_no_targets_human(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch.object(ltvm, "list_targets", return_value=[]):
            rc = _run_main(["status"], capsys)
        assert rc == ltvm.EXIT_OK
        assert "No targets" in capsys.readouterr().out

    def test_status_no_targets_json(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # --json must follow the subcommand name
        with patch.object(ltvm, "list_targets", return_value=[]):
            rc = _run_main(["status", "--json"], capsys)
        assert rc == ltvm.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload == {"targets": []}

    def test_status_with_target(
        self, capsys: pytest.CaptureFixture[str], tmp_targets: Path
    ) -> None:
        """With one configured target, status table includes its name."""
        import lib.config as cfg

        # Build a real TargetConfig against tmp_targets so it won't raise
        with (
            patch.object(cfg, "TARGETS_DIR", tmp_targets / "targets"),
            patch.object(cfg, "OUTPUT_DIR", tmp_targets / "output"),
        ):
            tc = cfg.TargetConfig("rocky9")

        with (
            patch.object(ltvm, "list_targets", return_value=["rocky9"]),
            patch.object(ltvm, "TargetConfig", return_value=tc),
            patch.object(
                ltvm,
                "kernel_status",
                return_value={"built": False, "stale": True},
            ),
            patch.object(
                ltvm,
                "image_status",
                return_value={"built": False, "stale": True},
            ),
        ):
            rc = _run_main(["status"], capsys)

        assert rc == ltvm.EXIT_OK
        out = capsys.readouterr().out
        assert "rocky9" in out


# ---------------------------------------------------------------------------
# cmd_status JSON output format
# ---------------------------------------------------------------------------


class TestCmdStatusJson:
    def test_json_output_is_valid(
        self, capsys: pytest.CaptureFixture[str], tmp_targets: Path
    ) -> None:
        import lib.config as cfg

        with (
            patch.object(cfg, "TARGETS_DIR", tmp_targets / "targets"),
            patch.object(cfg, "OUTPUT_DIR", tmp_targets / "output"),
        ):
            tc = cfg.TargetConfig("rocky9")

        with (
            patch.object(ltvm, "list_targets", return_value=["rocky9"]),
            patch.object(ltvm, "TargetConfig", return_value=tc),
            patch.object(
                ltvm,
                "kernel_status",
                return_value={"built": False, "stale": True},
            ),
            patch.object(
                ltvm,
                "image_status",
                return_value={"built": False, "stale": True},
            ),
        ):
            rc = _run_main(["status", "--json"], capsys)

        assert rc == ltvm.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert "rocky9" in payload
        assert "container" in payload["rocky9"]
        assert "kernel" in payload["rocky9"]
        assert "image" in payload["rocky9"]


# ---------------------------------------------------------------------------
# _artifact_label helper
# ---------------------------------------------------------------------------


class TestArtifactLabel:
    def test_not_built(self) -> None:
        assert ltvm._artifact_label({"built": False}) == "not built"

    def test_stale(self) -> None:
        assert ltvm._artifact_label({"built": True, "stale": True}) == "stale"

    def test_current(self) -> None:
        assert (
            ltvm._artifact_label({"built": True, "stale": False}) == "current"
        )


# ---------------------------------------------------------------------------
# update: missing target and --all
# ---------------------------------------------------------------------------


class TestCmdUpdate:
    def test_update_no_target_no_all_returns_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = _run_main(["update"], capsys)
        assert rc == ltvm.EXIT_ERROR
        assert "update requires" in capsys.readouterr().err

    def test_update_no_target_no_all_json_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # --json must follow the subcommand to be picked up by
        # the subparser's copy of the flag.
        rc = _run_main(["update", "--json"], capsys)
        assert rc == ltvm.EXIT_ERROR
        err = capsys.readouterr().err
        payload = json.loads(err)
        assert "error" in payload


# ---------------------------------------------------------------------------
# _parse_vm_kwargs
# ---------------------------------------------------------------------------


class TestParseVmKwargs:
    def test_empty(self) -> None:
        assert ltvm._parse_vm_kwargs([]) == {}

    def test_vcpus(self) -> None:
        result = ltvm._parse_vm_kwargs(["--vcpus", "4"])
        assert result == {"vcpus": 4}

    def test_mem(self) -> None:
        result = ltvm._parse_vm_kwargs(["--mem", "2048"])
        assert result == {"mem": 2048}

    def test_disks(self) -> None:
        result = ltvm._parse_vm_kwargs(["--mdt-disks", "1", "--ost-disks", "3"])
        assert result == {"mdt_disks": 1, "ost_disks": 3}

    def test_combined(self) -> None:
        result = ltvm._parse_vm_kwargs(
            ["--vcpus", "2", "--mem", "4096", "--ost-disks", "2"]
        )
        assert result["vcpus"] == 2
        assert result["mem"] == 4096
        assert result["ost_disks"] == 2

    def test_unknown_flags_ignored(self) -> None:
        result = ltvm._parse_vm_kwargs(["--unknown", "foo"])
        assert result == {}


# ---------------------------------------------------------------------------
# _resolve_lustre_tree
# ---------------------------------------------------------------------------


class TestResolveLustreTree:
    def test_valid_tree(self, lustre_tree: Path) -> None:
        path, err = ltvm._resolve_lustre_tree(str(lustre_tree))
        assert err is None
        assert path == lustre_tree.resolve()

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        missing = str(tmp_path / "no_such_dir")
        path, err = ltvm._resolve_lustre_tree(missing)
        assert path is None
        assert err is not None
        assert "Not a directory" in err

    def test_dir_without_kernel_patches(self, tmp_path: Path) -> None:
        path, err = ltvm._resolve_lustre_tree(str(tmp_path))
        assert path is None
        assert err is not None
        assert "lustre/kernel_patches" in err

    def test_none_uses_cwd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When arg is None, cwd is used; if cwd lacks kernel_patches, error."""
        monkeypatch.chdir("/tmp")
        path, err = ltvm._resolve_lustre_tree(None)
        # /tmp won't have lustre/kernel_patches, so we expect an error
        assert err is not None


# ---------------------------------------------------------------------------
# vm subcommand: missing name for status/destroy/start/stop
# ---------------------------------------------------------------------------


class TestCmdVm:
    def _make_args(self, action: str, vm_args: list[str]) -> Any:
        p = ltvm.build_parser()
        return p.parse_args(["vm", action] + vm_args)

    def test_status_no_name_returns_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = self._make_args("status", [])
        rc = ltvm.cmd_vm(args)
        assert rc == ltvm.EXIT_ERROR
        assert "requires a VM name" in capsys.readouterr().err

    def test_destroy_no_name_returns_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = self._make_args("destroy", [])
        rc = ltvm.cmd_vm(args)
        assert rc == ltvm.EXIT_ERROR

    def test_unknown_action_returns_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Build args manually since "bogus" isn't in choices
        import argparse

        args = argparse.Namespace(json=False, action="bogus", vm_args=[])
        rc = ltvm.cmd_vm(args)
        assert rc == ltvm.EXIT_ERROR
        assert "Unknown vm action" in capsys.readouterr().err
