"""研究者仪表盘（只读）：净值曲线、持仓、决策时间线、快照回放、日记流、成本。"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .. import config, datafeed, db, exchange, nav

log = logging.getLogger(__name__)
app = FastAPI(title="LeekOrbit", docs_url=None, redoc_url=None)

STATIC = Path(__file__).parent / "static"


def _agent() -> str:
    import os

    return os.environ.get("LEEK_AGENT", "leek-01")


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/summary")
def summary():
    agent = _agent()
    cash = exchange.cash(agent)
    positions = []
    mv = 0.0
    for sym, p in exchange.positions(agent).items():
        q = datafeed.quote(sym)
        last = q["last"] if q else None
        avg = p["cost"] / p["qty"] if p["qty"] else 0
        value = p["qty"] * last if last else None
        mv += value or 0
        positions.append({
            "symbol": sym, "name": p["name"], "qty": p["qty"], "avg_cost": round(avg, 3),
            "last": last, "pct": q["pct"] if q else None,
            "pnl": round((last - avg) * p["qty"], 2) if last else None,
            "pnl_pct": round((last / avg - 1) * 100, 2) if last and avg else None,
        })
    return {"agent": agent, "cash": round(cash, 2), "market_value": round(mv, 2),
            "total": round(cash + mv, 2), "positions": positions}


@app.get("/api/nav")
def nav_series():
    return nav.series(_agent())


@app.get("/api/wakes")
def wakes(limit: int = 200):
    rows = db.rows(
        "SELECT w.id, w.ts, w.scene, w.status, w.detail, s.id AS snapshot_id "
        "FROM wakes w LEFT JOIN snapshots s ON s.wake_id = w.id "
        "WHERE w.agent=? ORDER BY w.ts DESC LIMIT ?", _agent(), limit)
    return [dict(r) for r in rows]


@app.get("/api/snapshot/{snapshot_id}")
def snapshot(snapshot_id: int):
    r = db.one("SELECT * FROM snapshots WHERE id=?", snapshot_id)
    if not r:
        raise HTTPException(404)
    return {"id": r["id"], "ts": r["ts"], "scene": r["scene"], "feed": r["feed"],
            "transcript": json.loads(r["transcript"]), "actions": json.loads(r["actions"]),
            "cost": json.loads(r["cost"] or "{}")}


@app.get("/api/diary")
def diary(limit: int = 100):
    rows = db.rows("SELECT ts, scene, content FROM diary WHERE agent=? ORDER BY id DESC LIMIT ?",
                   _agent(), limit)
    return [dict(r) for r in rows]


@app.get("/api/costs")
def costs():
    rows = db.rows(
        "SELECT substr(ts,1,10) AS date, COUNT(*) AS calls, SUM(prompt_tokens) AS pt, "
        "SUM(completion_tokens) AS ct, ROUND(SUM(cost_est),4) AS cost_cny "
        "FROM llm_calls WHERE agent=? GROUP BY date ORDER BY date DESC LIMIT 62", _agent())
    return [dict(r) for r in rows]


@app.get("/api/events")
def events():
    rows = db.rows("SELECT ts, type, payload, notified FROM events WHERE agent=? ORDER BY id DESC",
                   _agent())
    return [dict(r) for r in rows]


def serve_in_thread() -> threading.Thread:
    import uvicorn

    cfg = config.load_platform().get("dashboard", {})
    host, port = cfg.get("host", "127.0.0.1"), int(cfg.get("port", 8798))
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True, name="dashboard")
    t.start()
    log.info("dashboard on http://%s:%d", host, port)
    return t
