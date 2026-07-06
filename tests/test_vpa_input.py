"""Tests for VPA-as-input: precedence, headroom handling, cross-check, and
Kubernetes quantity parsing.

VPA is an optional input signal (like Prometheus). When a VerticalPodAutoscaler
recommendation exists it is preferred over our own P95/avg estimate — and,
crucially, a VPA *target* is already headroom-inclusive so we must NOT re-apply
the profile headroom on top of it.
"""

from k8s_advisor.collector.kubernetes import (
    _parse_cpu_quantity_to_m,
    _parse_mem_quantity_to_mi,
)
from k8s_advisor.simple_analyzer import _diverges, analyze_workload


def _base_row() -> dict:
    """Minimal row; VPA columns default to 'N/A' (no VPA present)."""
    return {
        "Cluster": "sandbox",
        "Namespace": "default",
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
        "VPA_Present": "N/A",
        "VPA_CPU_Target(m)": "N/A",
        "VPA_Mem_Target(Mi)": "N/A",
        "VPA_Mem_Upper(Mi)": "N/A",
        "PVC_Access_Mode": "N/A",
        "PVC_Count": "0",
        "Container_Count": "1",
        "Key_Labels": "app=web-api",
        "Detected_Issues": "",
    }


# ── Precedence ────────────────────────────────────────────────────────────


def test_no_vpa_falls_back_to_prometheus():
    row = _base_row()
    row["CPU_P95(m)"] = "150"
    row["Mem_P95(Mi)"] = "500"
    analysis = analyze_workload(row, has_prometheus=True)
    assert analysis.rec_basis == "prometheus"
    assert analysis.vpa_present is False


def test_no_vpa_no_prometheus_falls_back_to_metrics_server():
    analysis = analyze_workload(_base_row(), has_prometheus=False)
    assert analysis.rec_basis == "metrics-server"


def test_vpa_wins_over_prometheus():
    row = _base_row()
    row["CPU_P95(m)"] = "150"
    row["Mem_P95(Mi)"] = "500"
    row["VPA_Present"] = "true"
    row["VPA_CPU_Target(m)"] = "260"
    row["VPA_Mem_Target(Mi)"] = "512"
    analysis = analyze_workload(row, has_prometheus=True)
    assert analysis.rec_basis == "vpa"
    assert analysis.vpa_present is True


def test_vpa_target_used_verbatim_no_double_headroom():
    """A VPA target is headroom-inclusive → used directly, not × cpu_headroom."""
    row = _base_row()
    row["VPA_Present"] = "true"
    row["VPA_CPU_Target(m)"] = "260"
    row["VPA_Mem_Target(Mi)"] = "512"
    analysis = analyze_workload(row, has_prometheus=True)
    # 260m verbatim — NOT 260 * 1.25.
    assert analysis.recommended_cpu == "260m"
    assert "512Mi" in analysis.recommended_mem


def test_vpa_present_but_zero_target_falls_back():
    """VPA_Present true but a zero CPU target is 'no signal', not size-to-zero."""
    row = _base_row()
    row["CPU_P95(m)"] = "150"
    row["VPA_Present"] = "true"
    row["VPA_CPU_Target(m)"] = "0"
    row["VPA_Mem_Target(Mi)"] = "0"
    analysis = analyze_workload(row, has_prometheus=True)
    assert analysis.rec_basis == "prometheus"


def test_vpa_respects_min_cpu_floor():
    row = _base_row()
    row["VPA_Present"] = "true"
    row["VPA_CPU_Target(m)"] = "5"  # below the 50m guardrail floor
    row["VPA_Mem_Target(Mi)"] = "512"
    analysis = analyze_workload(row, has_prometheus=True)
    assert analysis.recommended_cpu == "50m"


def test_vpa_confidence_bonus():
    row = _base_row()
    row["CPU_P95(m)"] = "150"
    row["Mem_P95(Mi)"] = "500"
    base = analyze_workload(_base_row() | {"CPU_P95(m)": "150", "Mem_P95(Mi)": "500"}, has_prometheus=True)
    row["VPA_Present"] = "true"
    row["VPA_CPU_Target(m)"] = "180"
    row["VPA_Mem_Target(Mi)"] = "520"
    withvpa = analyze_workload(row, has_prometheus=True)
    assert withvpa.confidence > base.confidence


# ── Cross-check flag ────────────────────────────────────────────────────────


def test_vpa_prometheus_disagreement_flagged():
    row = _base_row()
    row["CPU_P95(m)"] = "100"
    row["Mem_P95(Mi)"] = "400"
    row["VPA_Present"] = "true"
    row["VPA_CPU_Target(m)"] = "500"  # 5x the P95 → diverges
    row["VPA_Mem_Target(Mi)"] = "420"
    analysis = analyze_workload(row, has_prometheus=True)
    assert any("disagree" in a.lower() for a in analysis.action_required.split(";"))


def test_vpa_prometheus_agreement_not_flagged():
    row = _base_row()
    row["CPU_P95(m)"] = "200"
    row["Mem_P95(Mi)"] = "400"
    row["VPA_Present"] = "true"
    row["VPA_CPU_Target(m)"] = "220"  # close to P95
    row["VPA_Mem_Target(Mi)"] = "420"
    analysis = analyze_workload(row, has_prometheus=True)
    assert "disagree" not in analysis.action_required.lower()


def test_diverges_helper():
    assert _diverges(500, 100, 2.0) is True
    assert _diverges(100, 500, 2.0) is True
    assert _diverges(220, 200, 2.0) is False
    # Zero-guard: a missing signal never reports divergence.
    assert _diverges(0, 100, 2.0) is False
    assert _diverges(100, 0, 2.0) is False


# ── Quantity parsing ─────────────────────────────────────────────────────────


def test_parse_cpu_quantity():
    assert _parse_cpu_quantity_to_m("250m") == 250.0
    assert _parse_cpu_quantity_to_m("1") == 1000.0
    assert _parse_cpu_quantity_to_m("1.5") == 1500.0
    assert _parse_cpu_quantity_to_m("500000000n") == 500.0  # nanocores
    assert _parse_cpu_quantity_to_m("250000u") == 250.0  # microcores
    assert _parse_cpu_quantity_to_m(None) == 0.0
    assert _parse_cpu_quantity_to_m("") == 0.0
    assert _parse_cpu_quantity_to_m("garbage") == 0.0


def test_parse_mem_quantity():
    assert _parse_mem_quantity_to_mi("512Mi") == 512.0
    assert _parse_mem_quantity_to_mi("1Gi") == 1024.0
    assert _parse_mem_quantity_to_mi("1024Ki") == 1.0
    assert _parse_mem_quantity_to_mi(str(1024 * 1024)) == 1.0  # bare bytes → 1Mi
    assert round(_parse_mem_quantity_to_mi("1M"), 2) == round(1000**2 / (1024**2), 2)
    assert _parse_mem_quantity_to_mi(None) == 0.0
    assert _parse_mem_quantity_to_mi("garbage") == 0.0


def test_high_volatility_floor_protects_against_low_vpa():
    """A high-CV workload must not be sized below the volatility floor even if
    VPA's memory target is lower — under-sizing a leaky workload OOMs it."""
    row = _base_row()
    row["Mem_P95(Mi)"] = "1000"
    row["Mem_Volatility_CV"] = "50"  # high volatility
    row["VPA_Present"] = "true"
    row["VPA_CPU_Target(m)"] = "180"
    row["VPA_Mem_Target(Mi)"] = "300"  # well below P95 * high-vol headroom
    analysis = analyze_workload(row, has_prometheus=True)
    # Floor = 1000 * 1.8 = 1800Mi should win over the 300Mi VPA target.
    assert "1800Mi" in analysis.recommended_mem or "1.8" in analysis.recommended_mem
