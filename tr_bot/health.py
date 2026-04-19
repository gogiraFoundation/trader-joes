"""FastAPI health and readiness (Section 6)."""

from __future__ import annotations

from typing import Any

from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse, Response

from fastapi.staticfiles import StaticFiles

from tr_bot import config
from tr_bot.dashboard_api import router as dashboard_router
from tr_bot.health_checks import check_postgres, check_redis, overall_readiness_ok

app = FastAPI(title="tr_bot health", version="0.1.0")

_STATIC_DIR = Path(__file__).resolve().parent / "static" / "dashboard"
_DASHBOARD_INDEX = _STATIC_DIR / "index.html"


@app.get("/", response_model=None)
def root() -> FileResponse | RedirectResponse:
    """
    Serve the dashboard HTML at / so browsers work without a redirect chain.
    (Use http://127.0.0.1:PORT or http://localhost:PORT — many browsers show a blank page for http://0.0.0.0:PORT.)
    """
    if _DASHBOARD_INDEX.is_file():
        return FileResponse(_DASHBOARD_INDEX, media_type="text/html")
    return RedirectResponse(url="/dashboard/", status_code=302)


@app.get("/favicon.ico", response_model=None)
def favicon() -> Response:
    """Avoid noisy 404s in access logs when browsers request a favicon."""
    return Response(status_code=204)


@app.get("/dashboard/edit", response_model=None)
def dashboard_edit_alias() -> RedirectResponse:
    """Common typo / bookmark; there is no separate editor UI yet."""
    return RedirectResponse(url="/dashboard/", status_code=302)


app.include_router(dashboard_router)


def _check_redis() -> dict[str, Any]:
    return check_redis(config.REDIS_URL)


def _check_postgres() -> dict[str, Any]:
    return check_postgres(config.DATABASE_URL)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", response_class=Response)
def metrics() -> Response:
    """Prometheus scrape endpoint when prometheus_client is installed (CoinDesk + other counters)."""
    if not config.COINDESK_METRICS_ENABLED:
        return Response(status_code=404)
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    except ImportError:
        return Response(status_code=503, content=b"prometheus_client not installed")
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


def _managers_status() -> dict[str, Any]:
    out: dict[str, Any] = {"enabled": config.MANAGERS_ENABLED}
    if not config.MANAGERS_ENABLED:
        return out
    try:
        from tr_bot.managers.fund import FundManager

        fund = FundManager()
        st = fund.get_state()
        out["fund"] = {
            "halt_file": fund.manual_halt_file_active(),
            "state": asdict(st) if st else None,
        }
        out["portfolio"] = {
            "mode": config.PORTFOLIO_MODE,
            "symbols": __import__(
                "tr_bot.managers.portfolio", fromlist=["active_symbols"]
            ).active_symbols(),
        }
        out["analysis"] = {
            "use_extended_ta": config.USE_EXTENDED_TA,
            "use_ta_filter": config.USE_TA_FILTER,
            "use_fundamentals_filter": config.USE_FUNDAMENTALS_FILTER,
        }
        out["notifications"] = {
            "enabled": config.NOTIFY_ENABLED,
            "channels": config.NOTIFY_CHANNELS,
            "webhook_configured": bool((config.NOTIFY_WEBHOOK_URL or "").strip()),
        }
    except Exception as e:
        out["error"] = str(e)
    return out


@app.get("/ready")
def ready() -> dict[str, Any]:
    redis_info = _check_redis()
    pg_info = _check_postgres()
    mgr = _managers_status()
    req_pg = config.readiness_require_postgres()
    ok = overall_readiness_ok(redis_info, pg_info, require_postgres=req_pg)
    coindesk_reference: dict[str, Any] | None = None
    if config.COINDESK_REFERENCE_CHECK_ENABLED and config.COINDESK_API_KEY.strip():
        try:
            from tr_bot.coindesk.reference import coindesk_reference_ready_snapshot

            coindesk_reference = coindesk_reference_ready_snapshot()
        except Exception as e:
            coindesk_reference = {"skipped": False, "error": str(e)}
    out: dict[str, Any] = {
        "status": "ready" if ok else "degraded",
        "redis": redis_info,
        "postgres": pg_info,
        "readiness": {
            "postgres_required_for_ready": req_pg,
            "mode": config.READINESS_MODE,
        },
        "managers": mgr,
        "artifacts_dir": str(Path(config.ARTIFACTS_DIR).resolve()),
    }
    if coindesk_reference is not None:
        out["coindesk_reference"] = coindesk_reference
    cve = config.coindesk_runtime_validation_errors()
    if cve:
        out["coindesk_config_issues"] = cve
    return out


# Mount static dashboard assets last so explicit routes (e.g. /dashboard/edit) win.
if _STATIC_DIR.is_dir():
    app.mount(
        "/dashboard",
        StaticFiles(directory=str(_STATIC_DIR), html=True),
        name="dashboard",
    )
