"""Tests for resource recommendation engine."""

from k8s_advisor.analyzer.recommender import (
    format_cpu,
    format_memory,
    recommend_resources,
)


def _workload() -> dict:
    return {
        "avg_cpu_usage_m": 100.0,
        "cpu_request_m": 200.0,
        "cpu_limit_m": 400.0,
        "cpu_p95_m": 150.0,
        "cpu_max_m": 180.0,
        "cpu_throttle_pct": 0.0,
        "avg_mem_usage_mi": 256.0,
        "mem_request_mi": 512.0,
        "mem_limit_mi": 1024.0,
        "mem_p95_mi": 300.0,
        "mem_max_mi": 380.0,
        "oom_killed_count": 0,
    }


def test_format_helpers():
    assert format_cpu(500) == "500m"
    assert format_cpu(1000) == "1"
    assert format_cpu(1500) == "1.5"
    assert format_memory(512) == "512Mi"
    assert format_memory(1024) == "1Gi"
    assert format_memory(1536) == "1.5Gi"


def test_recommend_missing_requests_requires_manual():
    workload = _workload()
    workload["cpu_request_m"] = 0.0
    workload["mem_request_mi"] = 0.0

    rec = recommend_resources(workload, has_prometheus=True)

    assert rec.cpu_request is not None
    assert rec.memory_request is not None
    assert rec.requires_manual_action is True
    assert "not set" in rec.rationale


def test_recommend_cpu_throttling_increases_limit():
    workload = _workload()
    workload["cpu_throttle_pct"] = 8.0
    rec = recommend_resources(workload, has_prometheus=True)

    assert rec.cpu_limit is not None
    assert "throttled" in rec.rationale


def test_recommend_oom_increases_memory_limit():
    workload = _workload()
    workload["oom_killed_count"] = 2
    rec = recommend_resources(workload, has_prometheus=True)

    assert rec.memory_limit is not None
    assert rec.requires_manual_action is True
    assert "OOM killed" in rec.rationale
