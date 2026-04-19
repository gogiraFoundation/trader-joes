"""Security helpers."""

from pathlib import Path

import pytest

from tr_bot.managers.security import (
    collapse_artifact_dir_duplication,
    is_valid_ccxt_spot_symbol,
    resolve_path_under_artifact_dir,
    safe_artifact_path,
)


def test_valid_ccxt_pair() -> None:
    assert is_valid_ccxt_spot_symbol("BTC/USDT") is True


def test_valid_yahoo_ticker() -> None:
    assert is_valid_ccxt_spot_symbol("SPY") is True


def test_resolve_path_strips_redundant_artifact_segment(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    assert resolve_path_under_artifact_dir(root, "artifacts/regime_model.joblib") == (
        root / "regime_model.joblib"
    ).resolve()
    assert resolve_path_under_artifact_dir(root, "regime_model.joblib") == (
        root / "regime_model.joblib"
    ).resolve()


def test_collapse_absolute_doubled_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    doubled = root / "artifacts" / "regime_model.joblib"
    doubled.parent.mkdir(parents=True)
    doubled.write_text("x", encoding="utf-8")
    fixed = collapse_artifact_dir_duplication(root, doubled)
    assert fixed == (root / "regime_model.joblib").resolve()
    assert resolve_path_under_artifact_dir(root, str(doubled.resolve())) == fixed


def test_safe_artifact_rejects_traversal(tmp_path: Path) -> None:
    base = tmp_path / "art"
    base.mkdir()
    with pytest.raises(ValueError):
        safe_artifact_path(base, "../etc/passwd")
