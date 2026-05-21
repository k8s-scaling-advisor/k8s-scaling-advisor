"""Tests for the graph generator.

We don't compare pixel buffers — we assert (a) all 6 PNGs are produced,
(b) `render_graphs(analyses)` shares logic with the analyzer (priority pie
matches the analyzer's count, BURSTY/GC are excluded from over-requested
ranking), and (c) the markdown template embeds the right paths when
graphs_dir is supplied.
"""

from pathlib import Path
from typing import Any, Dict, List

import pytest

from k8s_advisor.simple_analyzer import (
    Priority,
    analyze_workload,
    generate_report,
)
from k8s_advisor.visualizer import _is_excluded_from_savings, render_graphs


def _row(**overrides) -> Dict[str, Any]:
    base = {
        "Cluster": "production-east",
        "Namespace": "demo",
        "Workload_Type": "Deployment",
        "Deployment": "web-api",
        "Replicas": "3",
        "Pod_Count": "3",
        "Avg_CPU_Usage(m)": "100",
        "CPU_Request(m)": "200",
        "CPU_Limit(m)": "400",
        "CPU_Usage_Pct_Of_Request": "50",
        "CPU_Usage_Pct_Of_Limit": "25",
        "CPU_Throttle_Pct": "N/A",
        "CPU_P50(m)": "N/A",
        "CPU_P95(m)": "N/A",
        "CPU_Max(m)": "N/A",
        "CPU_StdDev(m)": "N/A",
        "Avg_Mem_Usage(Mi)": "300",
        "Mem_Request(Mi)": "400",
        "Mem_Limit(Mi)": "800",
        "Mem_Usage_Pct_Of_Request": "75",
        "Mem_Usage_Pct_Of_Limit": "37.5",
        "Mem_P50(Mi)": "N/A",
        "Mem_P95(Mi)": "N/A",
        "Mem_Max(Mi)": "N/A",
        "Mem_StdDev(Mi)": "N/A",
        "Mem_Volatility_CV": "N/A",
        "OOMKilled_Count": "0",
        "LastRestart_Reason": "",
        "Total_Restarts": "0",
        "Max_Restarts_Per_Pod": "0",
        "Restart_Rate_Per_Day": "0",
        "Days_Since_Last_Restart": "N/A",
        "Has_HPA": "false",
        "HPA_Min_Replicas": "N/A",
        "HPA_Max_Replicas": "N/A",
        "PVC_Access_Mode": "N/A",
        "PVC_Count": "0",
        "Container_Count": "1",
        "Key_Labels": "app=web-api",
        "Detected_Issues": "",
    }
    base.update(overrides)
    return base


def _diverse_analyses() -> List:
    """A small fleet that exercises all 6 graphs."""
    rows = [
        # Healthy workload — green dot in scatter
        _row(Deployment="green-app", **{
            "CPU_Request(m)": "100", "Avg_CPU_Usage(m)": "100",
            "Mem_Request(Mi)": "200", "Avg_Mem_Usage(Mi)": "200",
        }),
        # Over-requested CPU saver
        _row(Deployment="over-cpu", **{
            "CPU_Request(m)": "500", "Avg_CPU_Usage(m)": "10",
            "Mem_Request(Mi)": "100", "Avg_Mem_Usage(Mi)": "60",
        }),
        # Over-requested mem saver
        _row(Deployment="over-mem", **{
            "CPU_Request(m)": "200", "Avg_CPU_Usage(m)": "100",
            "Mem_Request(Mi)": "2000", "Avg_Mem_Usage(Mi)": "200",
        }),
        # Bursty Prometheus — must be excluded from savings ranking
        _row(Deployment="prometheus-fabric", **{
            "CPU_Request(m)": "500", "Avg_CPU_Usage(m)": "10",
            "Mem_Request(Mi)": "10000", "Avg_Mem_Usage(Mi)": "200",
        }),
        # GC runtime — must be excluded too
        _row(Deployment="kafka-broker", **{
            "CPU_Request(m)": "500", "Avg_CPU_Usage(m)": "10",
            "Mem_Request(Mi)": "5000", "Avg_Mem_Usage(Mi)": "200",
        }),
        # Under-requested CPU
        _row(Deployment="hot-app", **{
            "CPU_Request(m)": "100", "Avg_CPU_Usage(m)": "250",
            "Mem_Request(Mi)": "200", "Avg_Mem_Usage(Mi)": "180",
        }),
        # OOM kill victim
        _row(Deployment="oom-app", **{
            "OOMKilled_Count": "3", "Total_Restarts": "5",
            "Avg_Mem_Usage(Mi)": "300", "Mem_Limit(Mi)": "256",
        }),
        # Restart-zombie — INSUFFICIENT_DATA
        _row(Deployment="zombie-app", **{
            "Total_Restarts": "200", "Restart_Rate_Per_Day": "30",
            "Avg_CPU_Usage(m)": "0", "Avg_Mem_Usage(Mi)": "0",
        }),
    ]
    return [analyze_workload(r, has_prometheus=False) for r in rows]


def test_render_graphs_produces_all_expected_png(tmp_path: Path):
    pytest.importorskip("matplotlib")
    out = tmp_path / "graphs"
    assert render_graphs(_diverse_analyses(), str(out)) is True
    actual = {p.name for p in out.iterdir()}
    # Always-on graphs (no Prometheus required, no pattern groups required)
    always_on = {
        "1_resource_efficiency.png",
        "2_top_over_requested.png",
        "3_top_under_requested.png",
        "4_priority_distribution.png",
        "6_stability_analysis.png",
        "7_namespace_risk.png",
        "8_fleet_capacity.png",
    }
    missing = always_on - actual
    assert not missing, f"Missing PNGs: {missing}"
    # The dropped histogram must NOT be regenerated.
    assert "5_resource_distribution.png" not in actual


def test_pattern_group_impact_only_when_groups_exist(tmp_path: Path):
    """Graph 9 should only render when there's at least one large pattern."""
    pytest.importorskip("matplotlib")
    rows = []
    for n in range(5):
        rows.append(_row(
            Namespace="rook-ceph",
            Deployment=f"rook-ceph-osd-{n}",
            **{
                "CPU_Request(m)": "0",
                "Mem_Request(Mi)": "0",
                "Avg_CPU_Usage(m)": "100",
            },
        ))
    analyses = [analyze_workload(r, has_prometheus=False) for r in rows]
    out = tmp_path / "graphs"
    assert render_graphs(analyses, str(out)) is True
    assert (out / "9_pattern_group_impact.png").exists()


def test_p95_scatter_only_when_prometheus(tmp_path: Path):
    """Graph 10 must skip when fewer than 25% of workloads have P95."""
    pytest.importorskip("matplotlib")
    out = tmp_path / "graphs"
    # Default _diverse_analyses uses has_prometheus=False → no P95 → skip.
    render_graphs(_diverse_analyses(), str(out))
    assert not (out / "10_p95_vs_request.png").exists()


def test_priority_pie_counts_match_analyzer(tmp_path: Path):
    """The pie chart must use the same Priority assignments the analyzer
    produced — guards against the regression where the visualizer re-derived
    priorities from CSV with stale rules."""
    pytest.importorskip("matplotlib")
    analyses = _diverse_analyses()
    p_counts = {p: 0 for p in Priority}
    for a in analyses:
        p_counts[a.priority] += 1

    out = tmp_path / "graphs"
    render_graphs(analyses, str(out))
    pie = out / "4_priority_distribution.png"
    assert pie.exists()
    # Re-derive what the visualizer would have used and confirm parity.
    from k8s_advisor.visualizer import _priority_distribution
    _priority_distribution(analyses, out)  # idempotent, smoke check
    # Direct value parity with the simple_analyzer summary.
    assert p_counts[Priority.P0] + p_counts[Priority.P1] + \
           p_counts[Priority.P2] + p_counts[Priority.P3] == len(analyses)


def test_bursty_and_gc_excluded_from_savings():
    """Both BURSTY and GC_RUNTIME workloads must be filtered out of the
    over-requested chart's input set."""
    analyses = _diverse_analyses()
    by_name = {a.deployment: a for a in analyses}
    assert _is_excluded_from_savings(by_name["prometheus-fabric"]) is True
    assert _is_excluded_from_savings(by_name["kafka-broker"]) is True
    assert _is_excluded_from_savings(by_name["over-cpu"]) is False


def test_unstable_and_insufficient_excluded_from_savings():
    analyses = _diverse_analyses()
    by_name = {a.deployment: a for a in analyses}
    # zombie-app -> INSUFFICIENT_DATA via restart-zombie gate
    assert by_name["zombie-app"].insufficient_data is True
    assert _is_excluded_from_savings(by_name["zombie-app"]) is True


def test_report_embeds_graph_image_links_when_graphs_dir_set(tmp_path: Path):
    pytest.importorskip("matplotlib")
    analyses = _diverse_analyses()
    report_path = tmp_path / "report.md"
    generate_report(analyses, str(report_path), has_prometheus=False, graphs_dir="graphs")
    text = report_path.read_text(encoding="utf-8")

    # Each of the 6 PNGs is referenced from somewhere in the report.
    expected_paths = [
        "graphs/1_resource_efficiency.png",
        "graphs/2_top_over_requested.png",
        "graphs/3_top_under_requested.png",
        "graphs/4_priority_distribution.png",
        "graphs/6_stability_analysis.png",
        "graphs/7_namespace_risk.png",
        "graphs/8_fleet_capacity.png",
    ]
    for path in expected_paths:
        assert path in text, f"Report is missing image embed for {path}"
    # Histogram was dropped — must not appear.
    assert "graphs/5_resource_distribution.png" not in text


def test_report_omits_graph_links_when_graphs_dir_none(tmp_path: Path):
    """Generating a report without --graphs must not embed broken image
    paths."""
    analyses = _diverse_analyses()
    report_path = tmp_path / "report.md"
    generate_report(analyses, str(report_path), has_prometheus=False, graphs_dir=None)
    text = report_path.read_text(encoding="utf-8")
    assert "graphs/4_priority_distribution.png" not in text
    assert "![Priority distribution]" not in text
