"""Combine Yahoo + CMC (+ optional ccxt) OHLCV, train RegimeModel, persist."""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd

from tr_bot import config
from tr_bot.managers.security import (
    collapse_artifact_dir_duplication,
    is_valid_ccxt_spot_symbol,
    resolve_path_under_artifact_dir,
)
from tr_bot.indicators import compute_indicators
from tr_bot.logger import setup_logger
from tr_bot.market_data import fetch_ohlcv
from tr_bot.regime import RegimeModel
from tr_bot.training.cmc import fetch_cmc_training, resolve_train_symbols_to_ids
from tr_bot.training.cmc_macro import merge_cmc_macro_training_features
from tr_bot.training.regime_dataset import sort_training_frame
from tr_bot.training.regime_validation import (
    compact_validation_report,
    validate_regime_frame,
    validation_gates_pass,
)
from tr_bot.training.dataset import (
    cache_exists,
    cache_is_stale,
    load_training_dataset,
    save_training_dataset,
)
from tr_bot.training.exchange_history import fetch_exchange_ohlcv_range
from tr_bot.training.last_run import write_training_last_run
from tr_bot.training.microstructure import add_ohlcv_microstructure_proxies, add_vwap_columns
from tr_bot.training.yahoo import fetch_yahoo_training

logger = setup_logger("tr_bot.training")


def _parse_sources(s: str) -> set[str]:
    """Normalize TRAIN_SOURCES tokens; `binance` is an alias for `exchange` (ccxt Binance spot)."""
    out: set[str] = set()
    for x in (x.strip().lower() for x in s.split(",") if x.strip()):
        out.add("exchange" if x == "binance" else x)
    return out


def resolve_train_exchange_symbols(
    raw: str | None = None,
    *,
    fallback_symbol: str | None = None,
) -> list[str]:
    """
    Parse comma-separated TRAIN_EXCHANGE_SYMBOL into CCXT pairs.

    Empty input falls back to ``SYMBOL``. Dedupes while preserving order.
    Rejects tokens without ``/`` or that fail ``is_valid_ccxt_spot_symbol``.
    """
    text = (raw if raw is not None else config.TRAIN_EXCHANGE_SYMBOL).strip()
    fallback = (fallback_symbol if fallback_symbol is not None else config.SYMBOL).strip()
    tokens = [t.strip() for t in text.split(",") if t.strip()] if text else []
    if not tokens and fallback:
        tokens = [fallback]

    out: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        if "/" not in tok or not is_valid_ccxt_spot_symbol(tok):
            raise ValueError(
                f"Invalid exchange training symbol {tok!r}; expected CCXT pair like BTC/USDT"
            )
        key = tok.upper()
        if key in seen:
            continue
        seen.add(key)
        # Preserve original slash casing style but normalize base/quote letters.
        base, quote = tok.split("/", 1)
        normalized = f"{base.strip().upper()}/{quote.strip().upper()}"
        out.append(normalized)
    if not out:
        raise ValueError("No exchange training symbols resolved from TRAIN_EXCHANGE_SYMBOL / SYMBOL")
    return out


def _exchange_range_ms() -> tuple[int | None, int | None]:
    """If set, use paginated ccxt fetch for exchange source."""
    lookback = config.TRAIN_EXCHANGE_LOOKBACK_DAYS
    if lookback > 0:
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - lookback * 24 * 3600 * 1000
        return start_ms, now_ms
    start_s = config.TRAIN_EXCHANGE_START_MS
    end_s = config.TRAIN_EXCHANGE_END_MS
    if start_s and end_s:
        return int(start_s), int(end_s)
    if start_s or end_s:
        raise ValueError(
            "Set both TRAIN_EXCHANGE_START_MS and TRAIN_EXCHANGE_END_MS, "
            "or set TRAIN_EXCHANGE_LOOKBACK_DAYS > 0 for paginated Binance history"
        )
    return None, None


def _apply_train_max_rows(df: pd.DataFrame) -> pd.DataFrame:
    if not config.TRAIN_MAX_ROWS or len(df) <= config.TRAIN_MAX_ROWS:
        return df
    sort_cols = ["timestamp"]
    for c in ("source", "asset"):
        if c in df.columns:
            sort_cols.append(c)
    s = df.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)
    out = s.tail(config.TRAIN_MAX_ROWS).reset_index(drop=True)
    logger.info(
        "Subsampled to TRAIN_MAX_ROWS=%s (time-ordered tail, preserves chronology)",
        config.TRAIN_MAX_ROWS,
    )
    return out


def _regime_model_from_config() -> RegimeModel:
    return RegimeModel(
        n_estimators=config.TRAIN_RF_N_ESTIMATORS,
        random_state=config.TRAIN_RF_RANDOM_STATE,
        max_depth=config.TRAIN_RF_MAX_DEPTH,
        min_samples_leaf=config.TRAIN_RF_MIN_SAMPLES_LEAF,
        max_features=config.TRAIN_RF_MAX_FEATURES,
    )


def collect_training_frames() -> pd.DataFrame:
    sources = _parse_sources(config.TRAIN_SOURCES)
    frames: list[pd.DataFrame] = []

    if "yahoo" in sources:
        tickers = [t.strip() for t in config.YAHOO_TRAIN_TICKERS.split(",") if t.strip()]
        for t in tickers:
            logger.info(
                "Fetching Yahoo Finance: %s period=%s timeframe=%s",
                t,
                config.YAHOO_TRAIN_PERIOD,
                config.TRAIN_TIMEFRAME,
            )
            try:
                df = fetch_yahoo_training(
                    t,
                    timeframe=config.TRAIN_TIMEFRAME,
                    period=config.YAHOO_TRAIN_PERIOD,
                )
            except Exception as e:
                logger.warning("[yahoo_symbol_skipped] ticker=%s: %s", t, e)
                if config.YAHOO_TRAIN_FAIL_ON_SKIP:
                    raise RuntimeError(
                        f"Yahoo ticker {t!r} failed and YAHOO_TRAIN_FAIL_ON_SKIP is set: {e}"
                    ) from e
                continue
            frames.append(df)

    if "coinmarketcap" in sources:
        if not config.CMC_API_KEY:
            logger.warning("TRAIN_SOURCES includes coinmarketcap but CMC_API_KEY is empty; skipping")
        else:
            id_tokens = [x.strip() for x in config.CMC_TRAIN_IDS.split(",") if x.strip()]
            slug_tokens = [x.strip() for x in config.CMC_TRAIN_SLUGS.split(",") if x.strip()]
            sym_tokens = [s.strip() for s in config.CMC_TRAIN_SYMBOLS.split(",") if s.strip()]
            targets: list[tuple[str, dict[str, Any]]] = []

            if id_tokens:
                for x in id_tokens:
                    try:
                        cid = int(x)
                    except ValueError:
                        logger.warning("Invalid CMC_TRAIN_IDS entry %r; skip", x)
                        continue
                    targets.append((str(cid), {"cmc_id": cid}))
            elif slug_tokens:
                for sl in slug_tokens:
                    targets.append((sl, {"slug": sl}))
            elif sym_tokens and config.CMC_MAP_RESOLVE_SYMBOLS:
                resolved = resolve_train_symbols_to_ids(sym_tokens, config.CMC_API_KEY)
                for sym in sym_tokens:
                    su = sym.upper()
                    cid = resolved.get(su)
                    if cid is not None:
                        logger.info("CMC map resolved %s -> id=%s", su, cid)
                        targets.append((str(cid), {"cmc_id": cid}))
                    else:
                        logger.warning("CMC map missing %s; using symbol param (ambiguous)", su)
                        targets.append((su, {"symbol": sym}))
            else:
                for sym in sym_tokens:
                    targets.append((sym.upper(), {"symbol": sym}))

            for label, kwa in targets:
                logger.info(
                    "Fetching CoinMarketCap: %s interval=%s count=%s",
                    label,
                    config.CMC_INTERVAL,
                    config.CMC_COUNT,
                )
                df = fetch_cmc_training(
                    interval=config.CMC_INTERVAL,
                    count=config.CMC_COUNT,
                    api_key=config.CMC_API_KEY,
                    convert=config.CMC_CONVERT,
                    **kwa,
                )
                frames.append(df)

    if "coindesk" in sources:
        if not config.COINDESK_API_KEY:
            logger.warning("TRAIN_SOURCES includes coindesk but COINDESK_API_KEY is empty; skipping")
        else:
            from tr_bot.coindesk.endpoints import fetch_training_ohlcv

            inst_list = [x.strip() for x in config.COINDESK_INSTRUMENTS.split(",") if x.strip()]
            from tr_bot.db import coindesk_repo

            for inst in inst_list:
                logger.info(
                    "Fetching CoinDesk OHLCV: %s timeframe=%s limit=%s market=%s",
                    inst,
                    config.TRAIN_TIMEFRAME,
                    config.COINDESK_TRAIN_LIMIT,
                    config.COINDESK_DEFAULT_MARKET,
                )
                try:
                    cdf = fetch_training_ohlcv(
                        inst,
                        timeframe=config.TRAIN_TIMEFRAME,
                        limit=config.COINDESK_TRAIN_LIMIT,
                        market=config.COINDESK_DEFAULT_MARKET,
                    )
                except Exception as e:
                    logger.warning("CoinDesk fetch failed for %s: %s", inst, e)
                    continue
                if cdf.empty:
                    continue
                frames.append(cdf)
                if config.COINDESK_STORE_ENABLED:
                    # Materialize rows before opening DB: iterrows left the socket idle before we fixed ordering.
                    ohlcv_rows = coindesk_repo.training_ohlcv_rows_from_dataframe(cdf)
                    store_conn = None
                    _coindesk_db_attempts = 3
                    _coindesk_db_backoff_s = 0.5
                    try:
                        for attempt in range(_coindesk_db_attempts):
                            try:
                                store_conn = coindesk_repo.connect()
                                coindesk_repo.ping(store_conn)
                                coindesk_repo.upsert_ohlcv_rows(store_conn, ohlcv_rows)
                                break
                            except Exception as e:
                                coindesk_repo.safe_close(store_conn)
                                store_conn = None
                                recoverable = coindesk_repo.is_recoverable_connection_error(e)
                                last_attempt = attempt == _coindesk_db_attempts - 1
                                if recoverable and not last_attempt:
                                    logger.warning(
                                        "CoinDesk OHLCV store transient DB error for %s "
                                        "(attempt %s/%s): %s",
                                        inst,
                                        attempt + 1,
                                        _coindesk_db_attempts,
                                        e,
                                    )
                                    time.sleep(_coindesk_db_backoff_s)
                                    continue
                                if recoverable and last_attempt:
                                    logger.warning(
                                        "CoinDesk OHLCV store failed for %s after %s attempts: %s",
                                        inst,
                                        _coindesk_db_attempts,
                                        e,
                                    )
                                else:
                                    logger.warning("CoinDesk OHLCV store failed for %s: %s", inst, e)
                                break
                    finally:
                        coindesk_repo.safe_close(store_conn)
            if config.COINDESK_STORE_ENABLED and config.COINDESK_EXPORT_PARQUET_PATH.strip():
                try:
                    from pathlib import Path

                    base = Path(config.COINDESK_EXPORT_PARQUET_PATH.strip())
                    for inst in inst_list:
                        pq_conn = None
                        try:
                            pq_conn = coindesk_repo.connect()
                            coindesk_repo.ping(pq_conn)
                            out_path = base.with_name(f"{base.stem}_{inst.replace('-', '_')}{base.suffix}")
                            coindesk_repo.export_ohlcv_parquet(
                                pq_conn,
                                str(out_path),
                                instrument=inst,
                                timeframe=config.TRAIN_TIMEFRAME,
                                market=config.COINDESK_DEFAULT_MARKET,
                                limit=max(config.COINDESK_TRAIN_LIMIT, 10_000),
                            )
                        except Exception as e:
                            logger.warning("CoinDesk parquet export failed for %s: %s", inst, e)
                        finally:
                            coindesk_repo.safe_close(pq_conn)
                except Exception as e:
                    logger.warning("CoinDesk DB parquet export skipped: %s", e)

    if "exchange" in sources:
        symbols = resolve_train_exchange_symbols()
        rng = _exchange_range_ms()
        delay = max(0.0, float(config.TRAIN_EXCHANGE_FETCH_SLEEP_S))
        for i, sym in enumerate(symbols):
            try:
                if rng[0] is not None and rng[1] is not None:
                    logger.info(
                        "Fetching exchange OHLCV (paginated): %s %s range_ms=%s..%s",
                        sym,
                        config.TRAIN_TIMEFRAME,
                        rng[0],
                        rng[1],
                    )
                    df = fetch_exchange_ohlcv_range(
                        sym,
                        config.TRAIN_TIMEFRAME,
                        rng[0],
                        rng[1],
                    )
                else:
                    logger.info(
                        "Fetching exchange OHLCV: %s %s limit=%s",
                        sym,
                        config.TRAIN_TIMEFRAME,
                        config.TRAIN_EXCHANGE_LIMIT,
                    )
                    df = fetch_ohlcv(sym, config.TRAIN_TIMEFRAME, limit=config.TRAIN_EXCHANGE_LIMIT)
            except Exception as e:
                logger.warning("[exchange_symbol_skipped] symbol=%s: %s", sym, e)
                if config.TRAIN_EXCHANGE_FAIL_ON_SKIP:
                    raise RuntimeError(
                        f"Exchange symbol {sym!r} failed and TRAIN_EXCHANGE_FAIL_ON_SKIP is set: {e}"
                    ) from e
                continue
            if df is None or df.empty:
                logger.warning("[exchange_symbol_skipped] symbol=%s: empty OHLCV", sym)
                if config.TRAIN_EXCHANGE_FAIL_ON_SKIP:
                    raise RuntimeError(
                        f"Exchange symbol {sym!r} returned empty OHLCV and "
                        "TRAIN_EXCHANGE_FAIL_ON_SKIP is set"
                    )
                continue
            df = df.copy()
            df["source"] = "exchange"
            df["asset"] = sym.replace("/", "_")
            frames.append(df)
            if delay > 0 and i + 1 < len(symbols):
                time.sleep(delay)

    if not frames:
        raise RuntimeError(
            "No training data: enable TRAIN_SOURCES (yahoo, coinmarketcap, exchange or binance, coindesk) "
            "and configure tickers/symbols."
        )

    return pd.concat(frames, ignore_index=True)


def _exchange_manifest_fields() -> dict[str, Any]:
    """Include resolved exchange symbols in dataset manifest when exchange is enabled."""
    sources = _parse_sources(config.TRAIN_SOURCES)
    if "exchange" not in sources:
        return {}
    try:
        symbols = resolve_train_exchange_symbols()
    except ValueError:
        return {}
    return {
        "exchange_symbols": symbols,
        "exchange_symbol_count": len(symbols),
    }


def _dataset_coverage_fields(df: pd.DataFrame) -> dict[str, Any]:
    """Primary asset tag + per-asset row counts for training observability."""
    from tr_bot.training.regime_dataset import resolve_primary_asset_tag

    out: dict[str, Any] = {
        "primary_asset": resolve_primary_asset_tag() or None,
        "primary_sample_weight": float(config.TRAIN_PRIMARY_SAMPLE_WEIGHT or 1.0),
    }
    if "asset" in df.columns and len(df) > 0:
        counts = df["asset"].astype(str).value_counts()
        out["rows_by_asset"] = {str(k): int(v) for k, v in counts.items()}
    return out


def _normalize_dataset_mode(mode: str | None) -> str:
    m = (mode or config.TRAIN_DATASET_MODE or "fetch_only").strip().lower()
    if m not in ("fetch_only", "load_or_fetch", "load_only"):
        raise ValueError(
            f"Invalid TRAIN_DATASET_MODE: {m!r}; use fetch_only, load_or_fetch, or load_only"
        )
    return m


def run_training_pipeline(
    output_path: Path | None = None,
    *,
    dataset_mode: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    mode = _normalize_dataset_mode(dataset_mode)
    dataset_from_cache = False
    manifest: dict[str, Any] = {}

    if mode == "load_only":
        raw, manifest = load_training_dataset()
        dataset_from_cache = True
        logger.info("Loaded training dataset from cache (%s rows)", len(raw))
    elif mode == "load_or_fetch" and not force_refresh and cache_exists() and not cache_is_stale():
        raw, manifest = load_training_dataset()
        # Corrupt or partial cache (e.g. bad prior run) must not skip a full fetch.
        min_cache = max(100, int(config.MIN_TRAIN_ROWS_VALIDATE) // 5)
        if len(raw) < min_cache:
            logger.warning(
                "Cached training dataset too small (%s rows < %s); refetching from network",
                len(raw),
                min_cache,
            )
            raw = collect_training_frames()
            manifest = save_training_dataset(
                raw,
                extra_manifest={
                    "from_network": True,
                    "dataset_mode": mode,
                    "replaced_small_cache": True,
                    **_exchange_manifest_fields(),
                    **_dataset_coverage_fields(raw),
                },
            )["manifest"]
        else:
            dataset_from_cache = True
            logger.info("Using cached training dataset (%s rows)", len(raw))
    else:
        raw = collect_training_frames()
        manifest = save_training_dataset(
            raw,
            extra_manifest={
                "from_network": True,
                "dataset_mode": mode,
                **_exchange_manifest_fields(),
                **_dataset_coverage_fields(raw),
            },
        )["manifest"]

    if config.CMC_TRAIN_MACRO_MERGE_ENABLED and (config.CMC_API_KEY or "").strip():
        logger.info("Merging CMC macro history (global / fear-greed / optional altcoin) onto training frame")
        raw = merge_cmc_macro_training_features(raw, config.CMC_API_KEY)

    raw = _apply_train_max_rows(raw)

    feat = compute_indicators(raw)
    if config.TRAIN_MICROSTRUCTURE_FEATURES:
        feat = add_vwap_columns(feat)
        feat = add_ohlcv_microstructure_proxies(feat)
    feat = feat.dropna(subset=["rsi", "ema20", "ema50", "atr", "close"])
    if len(feat) < 50:
        raise ValueError(f"Not enough rows after indicators: {len(feat)}")
    feat = sort_training_frame(feat)

    validation_gates_passed = True
    validation_gate_reasons: list[str] = []
    validation_summary: dict[str, Any] | None = None

    if config.TRAIN_RUN_VALIDATION:

        def _rf_builder():
            return _regime_model_from_config().model

        try:
            vreport = validate_regime_frame(
                feat,
                _rf_builder,
                n_splits=config.TRAIN_TS_SPLITS,
                min_rows_per_fold=config.TRAIN_MIN_ROWS_PER_FOLD,
                per_group=config.TRAIN_VALIDATION_PER_GROUP,
            )
            validation_summary = compact_validation_report(vreport)
            validation_gates_passed, validation_gate_reasons = validation_gates_pass(
                vreport,
                min_oof_f1=config.TRAIN_MIN_OOF_F1,
                max_train_val_gap=config.TRAIN_MAX_TRAIN_VAL_GAP,
            )
            logger.info(
                "Regime validation: oof_f1=%s mean_train_val_gap_accuracy=%s gates_ok=%s",
                validation_summary.get("oof_f1"),
                validation_summary.get("mean_train_val_gap_accuracy"),
                validation_gates_passed,
            )
            if not validation_gates_passed:
                logger.warning("Validation gates failed: %s", validation_gate_reasons)
            if config.TRAIN_VALIDATION_STRICT and not validation_gates_passed:
                raise RuntimeError(
                    "TRAIN_VALIDATION_STRICT: " + "; ".join(validation_gate_reasons)
                )
        except RuntimeError:
            raise
        except Exception as e:
            if config.TRAIN_VALIDATION_STRICT:
                raise
            logger.warning("Regime validation failed (non-strict; model still saved): %s", e)
            validation_gates_passed = False
            validation_summary = {"error": str(e)[:500]}

    regime = _regime_model_from_config()
    regime.train(feat)
    logger.info("Trained RegimeModel on %s rows (sources=%s)", len(feat), raw["source"].unique().tolist())

    if validation_summary is None:
        validation_summary = {}

    if config.TRAIN_BACKTEST_GATE:
        from tr_bot.training.backtest_gate import (
            evaluate_train_backtest_gate,
            run_train_backtest_gate,
        )

        try:
            bt = run_train_backtest_gate(regime)
            validation_summary["backtest"] = {
                "ok": bt.get("ok"),
                "symbol": bt.get("symbol"),
                "timeframe": bt.get("timeframe"),
                "total_return_pct": bt.get("total_return_pct"),
                "max_drawdown_pct": bt.get("max_drawdown_pct"),
                "trades": bt.get("trades"),
                "bars_simulated": bt.get("bars_simulated"),
            }
            bt_ok, bt_reasons = evaluate_train_backtest_gate(bt)
            validation_gates_passed = validation_gates_passed and bt_ok
            validation_gate_reasons.extend(bt_reasons)
            logger.info(
                "Train backtest gate: total_return_pct=%s max_drawdown_pct=%s gates_ok=%s",
                bt.get("total_return_pct"),
                bt.get("max_drawdown_pct"),
                bt_ok,
            )
            if not bt_ok:
                logger.warning("Train backtest gate failed: %s", bt_reasons)
            if config.TRAIN_VALIDATION_STRICT and not bt_ok:
                raise RuntimeError(
                    "TRAIN_VALIDATION_STRICT (backtest): " + "; ".join(bt_reasons)
                )
        except RuntimeError:
            raise
        except Exception as e:
            if config.TRAIN_VALIDATION_STRICT:
                raise RuntimeError(f"TRAIN_BACKTEST_GATE failed: {e}") from e
            logger.warning("Train backtest gate error (non-strict): %s", e)
            validation_gates_passed = False
            validation_gate_reasons.append(f"train_backtest_gate: {str(e)[:500]}")
            validation_summary["backtest"] = {"error": str(e)[:500]}

    root = Path(config.ARTIFACTS_DIR).resolve()
    out = output_path or Path(config.TRAIN_OUTPUT_PATH)
    if not out.is_absolute():
        out = resolve_path_under_artifact_dir(root, str(out))
    else:
        out = collapse_artifact_dir_duplication(root, out)
    out.parent.mkdir(parents=True, exist_ok=True)
    regime.save(out)
    logger.info("Saved model to %s", out.resolve())
    return {
        "path": out.resolve(),
        "rows": len(feat),
        "sources": raw["source"].unique().tolist(),
        "dataset_from_cache": dataset_from_cache,
        "manifest": manifest,
        "validation": validation_summary,
        "validation_gates_passed": validation_gates_passed,
        "validation_gate_reasons": validation_gate_reasons,
    }


def main() -> None:
    logging.getLogger("tr_bot.training").setLevel(logging.INFO)
    p = argparse.ArgumentParser(description="Train regime model on Yahoo, CMC, and/or exchange data")
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Override TRAIN_OUTPUT_PATH / .env",
    )
    p.add_argument(
        "--dataset-mode",
        type=str,
        default=None,
        choices=("fetch_only", "load_or_fetch", "load_only"),
        help="Override TRAIN_DATASET_MODE for this run",
    )
    p.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore cache (for load_or_fetch): fetch and overwrite dataset",
    )
    args = p.parse_args()
    try:
        result = run_training_pipeline(
            output_path=args.output,
            dataset_mode=args.dataset_mode,
            force_refresh=args.force_refresh,
        )
    except Exception as e:
        write_training_last_run(
            {
                "ok": False,
                "stage": "pipeline",
                "error": str(e)[:800],
            }
        )
        raise
    artifact = Path(result["path"]).resolve()
    write_training_last_run(
        {
            "ok": True,
            "stage": "complete",
            "feature_rows": int(result["rows"]),
            "artifact": str(artifact),
            "sources": result.get("sources"),
            "dataset_from_cache": result.get("dataset_from_cache"),
            "manifest": result.get("manifest"),
            "validation": result.get("validation"),
            "validation_gates_passed": result.get("validation_gates_passed"),
            "validation_gate_reasons": result.get("validation_gate_reasons"),
        }
    )


if __name__ == "__main__":
    main()
