"""Tests for the update-check gate logic.

We don't exercise the network or the actual `git pull` / `sudo
install` side-effects -- the interesting logic is the schedule gate,
config persistence, and ancestry comparison.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def _config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point update_check at an isolated XDG_CONFIG_HOME."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    import importlib

    import ltvm_pkg.update_check as uc

    importlib.reload(uc)
    return tmp_path / "ltvm"


def test_default_mode_is_prompt(_config_dir: Path) -> None:
    import ltvm_pkg.update_check as uc

    cfg = uc._load_config()
    assert cfg["update_check"]["mode"] == "prompt"
    assert cfg["update_check"]["last_check_iso"] is None


def test_due_for_check_fresh(_config_dir: Path) -> None:
    import ltvm_pkg.update_check as uc

    cfg = uc._load_config()
    assert uc._due_for_check(cfg) is True


def test_due_for_check_recent(_config_dir: Path) -> None:
    import ltvm_pkg.update_check as uc

    cfg = uc._load_config()
    cfg["update_check"]["last_check_iso"] = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat()
    assert uc._due_for_check(cfg) is False


def test_due_for_check_stale(_config_dir: Path) -> None:
    import ltvm_pkg.update_check as uc

    cfg = uc._load_config()
    cfg["update_check"]["last_check_iso"] = (
        datetime.now(timezone.utc) - timedelta(hours=48)
    ).isoformat()
    assert uc._due_for_check(cfg) is True


def test_never_mode_skips(_config_dir: Path) -> None:
    import ltvm_pkg.update_check as uc

    _config_dir.mkdir(parents=True, exist_ok=True)
    (_config_dir / "config.json").write_text(
        json.dumps({"update_check": {"mode": "never"}})
    )
    with (
        patch.object(uc, "_is_interactive", return_value=True),
        patch.object(uc, "_remote_hash") as mock_remote,
    ):
        uc.maybe_check_for_updates()
    mock_remote.assert_not_called()


def test_force_bypasses_schedule_and_never(_config_dir: Path) -> None:
    import ltvm_pkg.update_check as uc

    _config_dir.mkdir(parents=True, exist_ok=True)
    (_config_dir / "config.json").write_text(
        json.dumps(
            {
                "update_check": {
                    "mode": "never",
                    "last_check_iso": datetime.now(timezone.utc).isoformat(),
                }
            }
        )
    )
    # force=True should still be suppressed by "never" -- the user
    # explicitly opted out.
    with (
        patch.object(uc, "_is_interactive", return_value=True),
        patch.object(uc, "_remote_hash") as mock_remote,
    ):
        uc.maybe_check_for_updates(force=True)
    mock_remote.assert_not_called()


def test_json_mode_skips(_config_dir: Path) -> None:
    import ltvm_pkg.update_check as uc

    with (
        patch.object(uc, "_is_interactive", return_value=True),
        patch.object(uc, "_remote_hash") as mock_remote,
    ):
        uc.maybe_check_for_updates(use_json=True)
    mock_remote.assert_not_called()


def test_non_tty_skips(_config_dir: Path) -> None:
    import ltvm_pkg.update_check as uc

    with (
        patch.object(uc, "_is_interactive", return_value=False),
        patch.object(uc, "_remote_hash") as mock_remote,
    ):
        uc.maybe_check_for_updates()
    mock_remote.assert_not_called()


def test_prompt_yes_triggers_update(_config_dir: Path) -> None:
    import ltvm_pkg.update_check as uc

    with (
        patch.object(uc, "_is_interactive", return_value=True),
        patch.object(uc, "_local_hash", return_value="aaaaaaa"),
        patch.object(uc, "_remote_hash", return_value="bbbbbbb"),
        patch.object(uc, "_is_newer", return_value=True),
        patch.object(uc, "_prompt_choice", return_value="y"),
        patch.object(uc, "_apply_update") as mock_apply,
    ):
        uc.maybe_check_for_updates()
    mock_apply.assert_called_once()


def test_prompt_auto_flips_config(_config_dir: Path) -> None:
    import ltvm_pkg.update_check as uc

    with (
        patch.object(uc, "_is_interactive", return_value=True),
        patch.object(uc, "_local_hash", return_value="aaaaaaa"),
        patch.object(uc, "_remote_hash", return_value="bbbbbbb"),
        patch.object(uc, "_is_newer", return_value=True),
        patch.object(uc, "_prompt_choice", return_value="a"),
        patch.object(uc, "_apply_update") as mock_apply,
    ):
        uc.maybe_check_for_updates()
    mock_apply.assert_called_once()
    cfg = uc._load_config()
    assert cfg["update_check"]["mode"] == "auto"


def test_prompt_never_persists(_config_dir: Path) -> None:
    import ltvm_pkg.update_check as uc

    with (
        patch.object(uc, "_is_interactive", return_value=True),
        patch.object(uc, "_local_hash", return_value="aaaaaaa"),
        patch.object(uc, "_remote_hash", return_value="bbbbbbb"),
        patch.object(uc, "_is_newer", return_value=True),
        patch.object(uc, "_prompt_choice", return_value="x"),
        patch.object(uc, "_apply_update") as mock_apply,
    ):
        uc.maybe_check_for_updates()
    mock_apply.assert_not_called()
    cfg = uc._load_config()
    assert cfg["update_check"]["mode"] == "never"


def test_auto_mode_no_prompt(_config_dir: Path) -> None:
    import ltvm_pkg.update_check as uc

    _config_dir.mkdir(parents=True, exist_ok=True)
    (_config_dir / "config.json").write_text(
        json.dumps({"update_check": {"mode": "auto"}})
    )
    with (
        patch.object(uc, "_is_interactive", return_value=True),
        patch.object(uc, "_local_hash", return_value="aaaaaaa"),
        patch.object(uc, "_remote_hash", return_value="bbbbbbb"),
        patch.object(uc, "_is_newer", return_value=True),
        patch.object(uc, "_prompt_choice") as mock_prompt,
        patch.object(uc, "_apply_update") as mock_apply,
    ):
        uc.maybe_check_for_updates()
    mock_prompt.assert_not_called()
    mock_apply.assert_called_once()
