"""Tests for the recommendation-fingerprint idempotency module."""

from __future__ import annotations

import json
from pathlib import Path

from k8s_advisor.idempotency import (
    STATE_FILENAME,
    STATE_SCHEMA_VERSION,
    fingerprint,
    load_state,
    merge_run,
    save_state,
)

# ──────────────────────────────────────────────────────────────────────
# fingerprint()
# ──────────────────────────────────────────────────────────────────────


def _fp(**overrides):
    """Build a fingerprint with sane defaults; override one field at a time."""
    base = dict(
        namespace="ns",
        deployment="dep",
        priority="P2",
        scaling_approach="VPA",
        recommended_cpu="500m",
        recommended_mem="256Mi",
    )
    base.update(overrides)
    return fingerprint(**base)


def test_fingerprint_is_stable_for_identical_inputs():
    assert _fp() == _fp()


def test_fingerprint_changes_when_namespace_changes():
    assert _fp() != _fp(namespace="other")


def test_fingerprint_changes_when_recommendation_changes():
    assert _fp() != _fp(recommended_cpu="600m")
    assert _fp() != _fp(recommended_mem="512Mi")


def test_fingerprint_changes_when_priority_or_scaling_changes():
    assert _fp() != _fp(priority="P0")
    assert _fp() != _fp(scaling_approach="HPA")


def test_fingerprint_uses_nul_delimiter_so_fields_cant_collide():
    # `name + '' + space` should not collide with `name + 'space'`.
    a = fingerprint(
        namespace="a", deployment="b", priority="P", scaling_approach="X", recommended_cpu="1m", recommended_mem="1Mi"
    )
    b = fingerprint(
        namespace="ab",
        deployment="",
        priority="P",
        scaling_approach="X",
        recommended_cpu="1m",
        recommended_mem="1Mi",
    )
    assert a != b


def test_fingerprint_returns_short_hex():
    fp = _fp()
    assert len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)


# ──────────────────────────────────────────────────────────────────────
# load_state / save_state
# ──────────────────────────────────────────────────────────────────────


def test_load_state_returns_empty_when_dir_missing(tmp_path: Path):
    state = load_state(tmp_path / "doesnt-exist")
    assert state == {"schema_version": STATE_SCHEMA_VERSION, "fingerprints": {}}


def test_load_state_returns_empty_when_file_missing(tmp_path: Path):
    # Directory exists but file doesn't.
    state = load_state(tmp_path)
    assert state["fingerprints"] == {}


def test_load_state_returns_empty_on_corrupt_json(tmp_path: Path):
    (tmp_path / STATE_FILENAME).write_text("not-json{", encoding="utf-8")
    state = load_state(tmp_path)
    assert state["fingerprints"] == {}


def test_load_state_returns_empty_on_schema_mismatch(tmp_path: Path):
    (tmp_path / STATE_FILENAME).write_text(
        json.dumps({"schema_version": 999, "fingerprints": {"abc": {"count": 1}}}),
        encoding="utf-8",
    )
    state = load_state(tmp_path)
    assert state["fingerprints"] == {}


def test_save_state_round_trips(tmp_path: Path):
    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "fingerprints": {
            "abc123": {"first_seen": "2026-01-01T00:00:00+00:00", "last_seen": "2026-01-01T00:00:00+00:00", "count": 1}
        },
    }
    save_state(tmp_path, state)
    again = load_state(tmp_path)
    assert again == state


def test_save_state_creates_directory(tmp_path: Path):
    nested = tmp_path / "a" / "b"
    save_state(nested, {"schema_version": STATE_SCHEMA_VERSION, "fingerprints": {}})
    assert (nested / STATE_FILENAME).exists()


# ──────────────────────────────────────────────────────────────────────
# merge_run()
# ──────────────────────────────────────────────────────────────────────


def test_merge_run_adds_new_fingerprints():
    prior = {"schema_version": STATE_SCHEMA_VERSION, "fingerprints": {}}
    out = merge_run(prior, ["abc", "def"])
    assert set(out["fingerprints"].keys()) == {"abc", "def"}
    for entry in out["fingerprints"].values():
        assert entry["count"] == 1
        assert entry["first_seen"] == entry["last_seen"]


def test_merge_run_bumps_count_for_existing_fingerprint():
    prior = {
        "schema_version": STATE_SCHEMA_VERSION,
        "fingerprints": {
            "abc": {"first_seen": "2026-01-01T00:00:00+00:00", "last_seen": "2026-01-01T00:00:00+00:00", "count": 1}
        },
    }
    out = merge_run(prior, ["abc"])
    entry = out["fingerprints"]["abc"]
    assert entry["count"] == 2
    assert entry["first_seen"] == "2026-01-01T00:00:00+00:00"  # preserved
    assert entry["last_seen"] != "2026-01-01T00:00:00+00:00"  # refreshed


def test_merge_run_preserves_unobserved_fingerprints():
    # A fingerprint in prior but not in this run should stay (workload may
    # have been temporarily skipped).
    prior = {
        "schema_version": STATE_SCHEMA_VERSION,
        "fingerprints": {
            "old": {"first_seen": "2026-04-01T00:00:00+00:00", "last_seen": "2026-04-01T00:00:00+00:00", "count": 5}
        },
    }
    out = merge_run(prior, ["new"])
    assert "old" in out["fingerprints"]
    assert "new" in out["fingerprints"]
    assert out["fingerprints"]["old"]["count"] == 5  # untouched


def test_merge_run_drops_entries_older_than_one_year():
    # last_seen well over a year ago → GC'd on next merge.
    prior = {
        "schema_version": STATE_SCHEMA_VERSION,
        "fingerprints": {
            "ancient": {
                "first_seen": "2020-01-01T00:00:00+00:00",
                "last_seen": "2020-01-01T00:00:00+00:00",
                "count": 99,
            }
        },
    }
    out = merge_run(prior, ["fresh"])
    assert "ancient" not in out["fingerprints"]
    assert "fresh" in out["fingerprints"]


# ──────────────────────────────────────────────────────────────────────
# load_state() malformed-input hardening
# ──────────────────────────────────────────────────────────────────────


def test_load_state_drops_non_dict_entries(tmp_path: Path):
    # An entry that's a bare string (not a dict) must be dropped, not
    # propagated to merge_run() where it would crash on `.get(...)`.
    (tmp_path / STATE_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "fingerprints": {
                    "good": {
                        "first_seen": "2026-01-01T00:00:00+00:00",
                        "last_seen": "2026-01-01T00:00:00+00:00",
                        "count": 3,
                    },
                    "bad": "not-a-dict",
                    "also-bad": 42,
                },
            }
        ),
        encoding="utf-8",
    )
    state = load_state(tmp_path)
    assert "good" in state["fingerprints"]
    assert "bad" not in state["fingerprints"]
    assert "also-bad" not in state["fingerprints"]


def test_load_state_drops_entries_missing_timestamps(tmp_path: Path):
    (tmp_path / STATE_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "fingerprints": {
                    "no-first": {"last_seen": "2026-01-01T00:00:00+00:00", "count": 1},
                    "no-last": {"first_seen": "2026-01-01T00:00:00+00:00", "count": 1},
                    "non-string-ts": {"first_seen": 123, "last_seen": 456, "count": 1},
                    "ok": {
                        "first_seen": "2026-01-01T00:00:00+00:00",
                        "last_seen": "2026-01-01T00:00:00+00:00",
                        "count": 1,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    state = load_state(tmp_path)
    assert set(state["fingerprints"].keys()) == {"ok"}


def test_load_state_coerces_bad_count_to_one(tmp_path: Path):
    (tmp_path / STATE_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "fingerprints": {
                    "bad-count": {
                        "first_seen": "2026-01-01T00:00:00+00:00",
                        "last_seen": "2026-01-01T00:00:00+00:00",
                        "count": "not-a-number",
                    },
                    "negative-count": {
                        "first_seen": "2026-01-01T00:00:00+00:00",
                        "last_seen": "2026-01-01T00:00:00+00:00",
                        "count": -5,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    state = load_state(tmp_path)
    # Both entries kept; counts clamped to >= 1.
    assert state["fingerprints"]["bad-count"]["count"] == 1
    assert state["fingerprints"]["negative-count"]["count"] == 1


def test_load_then_merge_does_not_crash_on_corrupt_entries(tmp_path: Path):
    """End-to-end: pathological input must not propagate to merge_run."""
    (tmp_path / STATE_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "fingerprints": {
                    "garbage": "totally not a dict",
                    "good": {
                        "first_seen": "2026-01-01T00:00:00+00:00",
                        "last_seen": "2026-01-01T00:00:00+00:00",
                        "count": 1,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    state = load_state(tmp_path)
    # If merge_run() ever sees the unsanitized "garbage" entry it will
    # call .get("count", 1) on a string and crash with AttributeError.
    out = merge_run(state, ["good"])
    assert out["fingerprints"]["good"]["count"] == 2
