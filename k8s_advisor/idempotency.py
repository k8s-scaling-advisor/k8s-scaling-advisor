"""Recommendation idempotency tracking.

When the analyzer is invoked with `--state-dir`, every workload analysis
gets a stable fingerprint over its identity + the actual recommended
numbers. Fingerprints from prior runs are persisted to
``<state-dir>/seen.json`` so we can answer "have we already told the
operator about this exact recommendation?".

This is the foundation for noise suppression in delivery channels
(Slack/Teams) — operators stop seeing the same advice every week unless
something materially changed.

The state file is small (one entry per workload, ~80 bytes each) and
the analyzer keeps it in-memory only for the duration of a run; there
is no concurrency story because every advisor invocation is a discrete
batch.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Bumped if the fingerprint shape ever changes — old state files become
# unreadable and the next run treats every workload as new. Better than
# silently mis-matching after a schema change.
#
# Versions:
#   1 — initial schema, used SHA-1 fingerprint (never released).
#   2 — switched to SHA-256 to clear security-scanner flags (current).
STATE_SCHEMA_VERSION = 2

STATE_FILENAME = "seen.json"


def fingerprint(
    *,
    namespace: str,
    deployment: str,
    priority: str,
    scaling_approach: str,
    recommended_cpu: str,
    recommended_mem: str,
) -> str:
    """Stable hash over the identity + the actual recommendation.

    Inputs are joined with a delimiter that can't appear in any of them
    (a NUL byte) so distinct field combinations can't collide. We use
    SHA-256 (truncated to 16 hex chars) — not because the threat model
    needs cryptographic strength, but because security scanners flag
    SHA-1/MD5 even for non-crypto uses and the cost of using SHA-256
    here is negligible. Collision space of 2^64 is plenty for a
    per-cluster recommendation set.
    """
    payload = "\x00".join([namespace, deployment, priority, scaling_approach, recommended_cpu, recommended_mem])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_state(state_dir: str | Path) -> dict[str, Any]:
    """Read ``<state-dir>/seen.json``; return an empty state on miss/parse-fail.

    A missing file, an unreadable file, or a schema-version mismatch all
    yield a fresh empty state. The advisor is intentionally tolerant:
    losing this file means at most one run of duplicated notifications,
    not a crashed pipeline.
    """
    path = Path(state_dir) / STATE_FILENAME
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(data, dict) or data.get("schema_version") != STATE_SCHEMA_VERSION:
        return _empty_state()
    raw_fps = data.get("fingerprints")
    if not isinstance(raw_fps, dict):
        return _empty_state()
    # Sanitize each entry. The contract is "load never raises", so we
    # keep only entries that match the expected shape and silently drop
    # the rest. A malformed file thus degrades to a smaller-but-correct
    # state rather than crashing merge_run() downstream.
    sanitized: dict[str, dict[str, Any]] = {}
    for fp, entry in raw_fps.items():
        if not isinstance(fp, str) or not isinstance(entry, dict):
            continue
        first_seen = entry.get("first_seen")
        last_seen = entry.get("last_seen")
        if not isinstance(first_seen, str) or not isinstance(last_seen, str):
            continue
        try:
            count = int(entry.get("count", 1))
        except (TypeError, ValueError):
            count = 1
        sanitized[fp] = {
            "first_seen": first_seen,
            "last_seen": last_seen,
            "count": max(1, count),
        }
    return {"schema_version": STATE_SCHEMA_VERSION, "fingerprints": sanitized}


def save_state(state_dir: str | Path, state: dict[str, Any]) -> Path:
    """Write the state atomically (temp-file + rename)."""
    base = Path(state_dir)
    base.mkdir(parents=True, exist_ok=True)
    final = base / STATE_FILENAME
    tmp = base / f".{STATE_FILENAME}.tmp"
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(final)  # atomic on POSIX, near-atomic on Windows
    return final


def merge_run(prior: dict[str, Any], current_fingerprints: list[str]) -> dict[str, Any]:
    """Fold this run's fingerprints into the prior state and return the new one.

    For each fingerprint produced by the current run:
      - if seen before → bump count, refresh ``last_seen``
      - if new → record with count=1, set first_seen=last_seen=now

    Fingerprints in ``prior`` that the current run did NOT produce are
    kept (a workload disappearing from one run shouldn't reset its
    history; the operator may have suspended its CronJob). After
    365 days of no observation entries are dropped to keep the file
    bounded.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fps = dict(prior.get("fingerprints") or {})

    for fp in current_fingerprints:
        if fp in fps:
            entry = fps[fp]
            entry["last_seen"] = now
            entry["count"] = int(entry.get("count", 1)) + 1
        else:
            fps[fp] = {"first_seen": now, "last_seen": now, "count": 1}

    # GC entries we haven't seen in a year. Cheap to evaluate and keeps
    # the state file from growing forever after workload churn.
    cutoff = _ts_minus_days(now, 365)
    fps = {fp: e for fp, e in fps.items() if (e.get("last_seen") or now) >= cutoff}

    return {"schema_version": STATE_SCHEMA_VERSION, "fingerprints": fps}


def _empty_state() -> dict[str, Any]:
    """Default state: no fingerprints, current schema version."""
    return {"schema_version": STATE_SCHEMA_VERSION, "fingerprints": {}}


def _ts_minus_days(now_iso: str, days: int) -> str:
    """Subtract `days` from an ISO-8601 UTC timestamp, return ISO-8601 string."""
    from datetime import timedelta

    now = datetime.fromisoformat(now_iso)
    return (now - timedelta(days=days)).isoformat(timespec="seconds")
