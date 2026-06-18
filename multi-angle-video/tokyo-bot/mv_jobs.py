"""Tiny durable job queue backed by a Google Sheet tab (`tokyo_jobs`).

Decouples long render jobs from the Slack bot process so a bot redeploy doesn't kill
in-flight renders: the bot ENQUEUEs a job (one row), a separate worker service CLAIMs and
runs it. Survives restarts — a worker that dies mid-job leaves the row 'running' with a
stale heartbeat; `reclaim_stale` puts it back to 'pending' so another worker re-runs it.

Pure functions over a passed gspread worksheet (no import of slack_bot — avoids circular
import and the bot's module-level side effects). The caller supplies the worksheet via the
bot's existing `_mv_get_worksheet(JOBS_TAB, JOBS_HEADER)`.

Row schema (JOBS_HEADER): id | kind | payload_json | status | worker | heartbeat | created | error
  status ∈ pending | running | done | failed
"""
from __future__ import annotations
import json, time, hashlib

JOBS_TAB = "tokyo_jobs"
JOBS_HEADER = ["id", "kind", "payload_json", "status", "worker", "heartbeat", "created", "error"]
_C = {k: i for i, k in enumerate(JOBS_HEADER)}  # column index by name (0-based)


def _now() -> float:
    return time.time()


def new_job_id(kind: str, payload: dict) -> str:
    # deterministic-ish unique id (no Math.random/Date needs); good enough for a row key
    h = hashlib.sha1(f"{kind}|{json.dumps(payload, sort_keys=True)}|{_now()}".encode()).hexdigest()[:12]
    return f"{kind}-{h}"


def enqueue(ws, kind: str, payload: dict) -> str:
    """Append a pending job. Returns the job id."""
    jid = new_job_id(kind, payload)
    ws.append_row([jid, kind, json.dumps(payload), "pending", "", "", f"{_now():.0f}", ""],
                  value_input_option="RAW")
    return jid


def _rows(ws):
    """All data rows as (row_number, list[str]). Row 1 is the header."""
    vals = ws.get_all_values()
    return [(i, r) for i, r in enumerate(vals[1:], start=2)]


def claim_next(ws, worker_id: str):
    """Claim the oldest pending job for this worker (status->running). Returns
    {id, kind, payload, row} or None. Single-writer-ish: with few workers + stale-reclaim
    the occasional race just means a job runs twice, which renders tolerate (idempotent post)."""
    for rownum, r in _rows(ws):
        if len(r) > _C["status"] and r[_C["status"]] == "pending":
            ws.update(f"D{rownum}:F{rownum}", [["running", worker_id, f"{_now():.0f}"]],
                      value_input_option="RAW")
            try:
                payload = json.loads(r[_C["payload_json"]] or "{}")
            except Exception:
                payload = {}
            return {"id": r[_C["id"]], "kind": r[_C["kind"]], "payload": payload, "row": rownum}
    return None


def _find_row(ws, job_id: str):
    for rownum, r in _rows(ws):
        if r and r[_C["id"]] == job_id:
            return rownum
    return None


def heartbeat(ws, job_id: str):
    rn = _find_row(ws, job_id)
    if rn:
        ws.update(f"F{rn}", [[f"{_now():.0f}"]], value_input_option="RAW")


def complete(ws, job_id: str):
    rn = _find_row(ws, job_id)
    if rn:
        ws.update(f"D{rn}", [["done"]], value_input_option="RAW")


def fail(ws, job_id: str, err: str):
    rn = _find_row(ws, job_id)
    if rn:
        ws.update(f"D{rn}", [["failed"]], value_input_option="RAW")          # status
        ws.update(f"H{rn}", [[(err or "")[:300]]], value_input_option="RAW")  # error


def reclaim_stale(ws, timeout_s: float = 1800):
    """Return any 'running' job whose heartbeat is older than timeout_s back to 'pending'
    (its worker died — e.g. a redeploy). Call periodically from the worker loop."""
    now = _now()
    n = 0
    for rownum, r in _rows(ws):
        if len(r) > _C["heartbeat"] and r[_C["status"]] == "running":
            try:
                hb = float(r[_C["heartbeat"]] or 0)
            except Exception:
                hb = 0
            if now - hb > timeout_s:
                ws.update(f"D{rownum}:F{rownum}", [["pending", "", ""]], value_input_option="RAW")
                n += 1
    return n
