"""Tests for the robustness enhancements:

  #1 CPU-limit stance under throttling — NEUTRAL by default (present both the
     remove/widen and keep-to-protect-co-tenants options, recommend no
     direction), policy-driven via cpu_limit_policy (neutral/burst/protect)
  #2 Statistical readiness gate — suppress memory *reductions* on too-spiky data
  #4 Recommendation deadband — skip sub-threshold churny changes
"""

from dataclasses import replace

from k8s_advisor.profiles import DEFAULT_PROFILE
from k8s_advisor.simple_analyzer import analyze_workload


def _base_row() -> dict:
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


# ── #1: CPU limit stance under throttling (neutral default + policy) ─────────


def _throttled_row() -> dict:
    row = _base_row()
    row["CPU_P95(m)"] = "380"
    row["CPU_Max(m)"] = "410"
    row["CPU_Throttle_Pct"] = "25"
    row["CPU_Limit(m)"] = "400"
    return row


def test_throttling_neutral_presents_both_no_direction():
    """Default (neutral): show remove/widen AND keep-to-protect, recommend
    neither — the multi-tenant tradeoff is the user's call."""
    analysis = analyze_workload(_throttled_row(), has_prometheus=True)
    joined = analysis.action_required.lower()
    assert "CPU_THROTTLED" in analysis.issues
    # Both stances are surfaced.
    assert "option a" in joined and "option b" in joined
    assert "remove cpu limit" in joined  # burst-friendly option
    assert "protect" in joined or "co-tenant" in joined or "neighbor" in joined
    # Neutral must NOT emit a directional recommendation line.
    assert not any("remove cpu limit" in r.lower() for r in analysis.recommendations)
    assert not any("widen cpu limit" in r.lower() for r in analysis.recommendations)
    assert "no default direction" in joined


def test_throttling_burst_policy_recommends_removal():
    profile = replace(DEFAULT_PROFILE, cpu_limit_policy="burst")
    analysis = analyze_workload(_throttled_row(), has_prometheus=True, profile=profile)
    joined = analysis.action_required.lower()
    assert "remove cpu limit" in joined
    assert "policy 'burst'" in joined
    # Directional recommendation is issued.
    assert any("remove cpu limit" in r.lower() for r in analysis.recommendations)
    # Widen still offered as the conservative alternative.
    assert "alternative" in joined


def test_throttling_protect_policy_recommends_keeping_limit():
    profile = replace(DEFAULT_PROFILE, cpu_limit_policy="protect")
    analysis = analyze_workload(_throttled_row(), has_prometheus=True, profile=profile)
    joined = analysis.action_required.lower()
    assert "policy 'protect'" in joined
    # Recommends widening (keeping the ceiling), not removal.
    assert any("widen cpu limit" in r.lower() for r in analysis.recommendations)
    assert not any("remove cpu limit" in r.lower() for r in analysis.recommendations)
    # Removal still offered as the single-tenant alternative.
    assert "alternative" in joined


def test_throttling_no_limit_points_at_limitrange():
    """Throttling observed with no CPU limit → blame LimitRange/parent cgroup,
    do NOT invent a CPU limit."""
    row = _base_row()
    row["CPU_P95(m)"] = "180"
    row["CPU_Throttle_Pct"] = "15"
    row["CPU_Limit(m)"] = "0"  # no limit set
    analysis = analyze_workload(row, has_prometheus=True)
    joined = analysis.action_required.lower()
    assert "limitrange" in joined or "parent cgroup" in joined
    assert "remove cpu limit" not in joined


def test_no_throttling_no_cpu_limit_action():
    row = _base_row()
    row["CPU_P95(m)"] = "150"
    row["CPU_Throttle_Pct"] = "1"  # below threshold
    analysis = analyze_workload(row, has_prometheus=True)
    assert "remove cpu limit" not in analysis.action_required.lower()


# ── #2: readiness / CV gate on reductions ───────────────────────────────────


def test_high_cv_suppresses_memory_reduction():
    row = _base_row()
    row["Mem_Request(Mi)"] = "1000"
    row["Avg_Mem_Usage(Mi)"] = "200"
    row["Mem_P95(Mi)"] = "300"  # over-requested → would normally reduce
    row["Mem_Volatility_CV"] = "150"  # far above readiness cut
    analysis = analyze_workload(row, has_prometheus=True)
    joined = analysis.action_required.lower()
    assert "too volatile" in joined
    # No numeric reduction was issued.
    assert not any("reduce memory request" in r.lower() for r in analysis.recommendations)


def test_moderate_cv_still_reduces():
    row = _base_row()
    row["Mem_Request(Mi)"] = "1000"
    row["Avg_Mem_Usage(Mi)"] = "200"
    row["Mem_P95(Mi)"] = "300"
    row["Mem_Volatility_CV"] = "15"  # below readiness cut
    analysis = analyze_workload(row, has_prometheus=True)
    assert any("reduce memory request" in r.lower() for r in analysis.recommendations)


def test_vpa_bypasses_cv_reduction_gate():
    """VPA already accounts for volatility → its target is honored even at high CV."""
    row = _base_row()
    row["Mem_Request(Mi)"] = "1000"
    row["Mem_P95(Mi)"] = "300"
    row["Mem_Volatility_CV"] = "150"
    row["VPA_Present"] = "true"
    row["VPA_CPU_Target(m)"] = "180"
    row["VPA_Mem_Target(Mi)"] = "350"
    analysis = analyze_workload(row, has_prometheus=True)
    assert "too volatile" not in analysis.action_required.lower()
    assert any("reduce memory request" in r.lower() for r in analysis.recommendations)


# ── #4: deadband ────────────────────────────────────────────────────────────


def test_deadband_skips_tiny_cpu_raise():
    row = _base_row()
    # under-requested → wants a raise, but only ~5% larger than current.
    row["CPU_Request(m)"] = "1000"
    row["Avg_CPU_Usage(m)"] = "840"  # * 1.25 = 1050 → +5% delta
    row["CPU_P95(m)"] = "840"
    analysis = analyze_workload(row, has_prometheus=True)
    assert not any("increase cpu request" in r.lower() for r in analysis.recommendations)


def test_deadband_allows_large_cpu_raise():
    row = _base_row()
    row["CPU_Request(m)"] = "200"
    row["Avg_CPU_Usage(m)"] = "400"
    row["CPU_P95(m)"] = "400"  # * 1.25 = 500 → +150% delta
    analysis = analyze_workload(row, has_prometheus=True)
    assert any("increase cpu request" in r.lower() for r in analysis.recommendations)


def test_deadband_skips_tiny_memory_reduction():
    row = _base_row()
    # Over-requested but the reduction is only ~6%.
    row["Mem_Request(Mi)"] = "1000"
    row["Avg_Mem_Usage(Mi)"] = "752"  # * 1.25 = 940 → 6% reduction
    row["Mem_P95(Mi)"] = "752"
    analysis = analyze_workload(row, has_prometheus=True)
    assert not any("reduce memory request" in r.lower() for r in analysis.recommendations)


def test_vpa_bypasses_deadband():
    """A VPA target is authoritative even for a small move."""
    row = _base_row()
    row["CPU_Request(m)"] = "1000"
    row["Avg_CPU_Usage(m)"] = "840"
    row["CPU_P95(m)"] = "840"
    row["VPA_Present"] = "true"
    row["VPA_CPU_Target(m)"] = "1050"  # +5%, would be deadbanded without VPA
    row["VPA_Mem_Target(Mi)"] = "400"
    analysis = analyze_workload(row, has_prometheus=True)
    assert any("increase cpu request" in r.lower() for r in analysis.recommendations)
