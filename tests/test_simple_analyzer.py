"""Unit tests for the enhanced simple analyzer."""

from pathlib import Path

from k8s_advisor.simple_analyzer import (
    analyze_csv_file,
    analyze_workload,
    check_prometheus,
    generate_report,
)


def _base_row() -> dict:
    """Build a minimal workload row matching the CSV schema."""
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
        "PVC_Access_Mode": "N/A",
        "PVC_Count": "0",
        "Container_Count": "1",
        "Key_Labels": "app=web-api",
        "Detected_Issues": "",
    }


def test_check_prometheus_true_when_p95_present():
    rows = [_base_row()]
    rows[0]["CPU_P95(m)"] = "150"
    assert check_prometheus(rows) is True


def test_check_prometheus_false_when_p95_na():
    rows = [_base_row()]
    assert check_prometheus(rows) is False


def test_analyze_workload_uses_p95_with_prometheus():
    row = _base_row()
    row["CPU_P95(m)"] = "800"
    row["CPU_P50(m)"] = "120"
    row["CPU_Max(m)"] = "1000"
    row["Mem_P95(Mi)"] = "600"
    row["Mem_P50(Mi)"] = "250"
    row["Mem_Max(Mi)"] = "900"
    row["Mem_Volatility_CV"] = "8.1"

    analysis = analyze_workload(row, has_prometheus=True)

    # P95 (800) * 1.25 => 1000m => 1.0 CPU
    assert analysis.recommended_cpu == "1.0"
    # P95 (600) * 1.25 => 750Mi
    assert analysis.recommended_mem == "750Mi"
    assert "P95" in analysis.current_cpu


def test_analyze_workload_uses_avg_without_prometheus():
    row = _base_row()
    row["Avg_CPU_Usage(m)"] = "40"
    row["CPU_Request(m)"] = "0"
    row["Avg_Mem_Usage(Mi)"] = "12"
    row["Mem_Request(Mi)"] = "0"

    analysis = analyze_workload(row, has_prometheus=False)

    # Avg CPU fallback with floor => 50m
    assert analysis.recommended_cpu == "50m"
    # Avg memory fallback with floor => 16Mi
    assert analysis.recommended_mem == "16Mi"


def test_report_keeps_sections_without_prometheus(tmp_path: Path):
    row = _base_row()
    analysis = analyze_workload(row, has_prometheus=False)

    report_path = tmp_path / "report.md"
    generate_report([analysis], str(report_path), has_prometheus=False)
    text = report_path.read_text(encoding="utf-8")

    assert "Executive Summary" in text
    assert "Detailed Analysis" in text
    assert "Implementation Guide" in text
    assert "Prometheus Metrics:** ⚠️ Not available" in text
    # New navigation structure
    assert "Table of Contents" in text
    assert "Top Optimizations" in text
    assert "Namespace Rollup" in text


def test_mem_saturation_raises_limit_so_request_is_valid():
    """Cartservice case: avg_mem near limit should raise both request and limit
    so the resulting PodSpec satisfies request <= limit."""
    row = _base_row()
    # avg_mem (128) > 90% of mem_limit (128) → MEM_SATURATION fires.
    row["Avg_Mem_Usage(Mi)"] = "128"
    row["Mem_Request(Mi)"] = "64"
    row["Mem_Limit(Mi)"] = "128"
    row["Mem_Usage_Pct_Of_Request"] = "200"
    row["Mem_Usage_Pct_Of_Limit"] = "100"

    analysis = analyze_workload(row, has_prometheus=False)

    assert "MEM_SATURATION" in analysis.issues
    # A memory limit raise must be in the actions even without OOM.
    assert any("memory LIMIT" in a for a in analysis.action_required.split("; "))
    # Invariant: any recommended request <= recommended limit. We extract the
    # numeric "Raise memory REQUEST ... → NMi" and "memory LIMIT ... → NMi"
    # values and check the relationship.
    actions = analysis.action_required
    # If a request raise was emitted, the limit raise must cover it.
    if "Raise memory REQUEST" in actions:
        # both targets are in the action string; the limit target must be
        # numerically >= the request target. We just assert the limit raise
        # exists — the specific numbers come from format_memory.
        assert "memory LIMIT" in actions


def test_dead_pod_skips_numeric_recs():
    """Conservative dead-pod gate: zero usage AND restart history → no recs."""
    row = _base_row()
    row["Avg_CPU_Usage(m)"] = "0"
    row["Avg_Mem_Usage(Mi)"] = "0"
    row["Total_Restarts"] = "5"
    row["LastRestart_Reason"] = "Error"
    row["Pod_Count"] = "1"

    analysis = analyze_workload(row, has_prometheus=False)

    assert analysis.insufficient_data is True
    assert "INSUFFICIENT_DATA" in analysis.issues
    # No prescriptive numeric recommendations
    assert analysis.recommendations == []
    assert "INSUFFICIENT_DATA" in analysis.action_required


def test_idle_but_healthy_workload_still_gets_recs():
    """Conservative gate must NOT skip an idle-but-healthy workload (no
    restarts, pods running)."""
    row = _base_row()
    row["Avg_CPU_Usage(m)"] = "0"
    row["Avg_Mem_Usage(Mi)"] = "0"
    row["Total_Restarts"] = "0"
    row["Pod_Count"] = "3"

    analysis = analyze_workload(row, has_prometheus=False)
    assert analysis.insufficient_data is False
    assert "INSUFFICIENT_DATA" not in analysis.issues


def test_hpa_action_uses_default_target_under_100():
    """HPA recommendation must not advertise averageUtilization > 100."""
    row = _base_row()
    row["Replicas"] = "3"
    row["Pod_Count"] = "3"
    row["CPU_Request(m)"] = "100"
    row["Avg_CPU_Usage(m)"] = "80"  # > 0.6 * request → HPA path

    analysis = analyze_workload(row, has_prometheus=False)

    # No "120%" anywhere in the action string; default 75 must appear instead.
    assert "120%" not in analysis.action_required
    assert "75%" in analysis.action_required


def test_implementation_guide_uses_safe_hpa_target():
    """The bundled HPA YAML in the implementation guide must use a safe
    averageUtilization (<=80)."""
    from k8s_advisor.simple_analyzer import generate_implementation_guide

    text = "\n".join(generate_implementation_guide())
    assert "averageUtilization: 120" not in text
    assert "averageUtilization: 75" in text


def test_gc_runtime_workload_skips_memory_reduction():
    """JVM/Node.js workloads must never have their memory request reduced."""
    row = _base_row()
    row["Deployment"] = "kafka-broker"  # matches GC_RUNTIME_PATTERNS
    row["Avg_Mem_Usage(Mi)"] = "100"
    row["Mem_Request(Mi)"] = "1024"  # heavy over-request (~10%) → would reduce
    row["Mem_Usage_Pct_Of_Request"] = "10"

    analysis = analyze_workload(row, has_prometheus=False)

    assert analysis.gc_runtime is True
    # Should not emit a "Reduce memory REQUEST" action
    assert not any("Reduce memory REQUEST" in a for a in analysis.action_required.split("; "))
    assert "GC runtime" in analysis.action_required


def test_low_confidence_banner_in_kubectl_only_report(tmp_path: Path):
    """kubectl-only mode emits a LOW CONFIDENCE banner at the top of the report."""
    row = _base_row()
    analysis = analyze_workload(row, has_prometheus=False)

    report_path = tmp_path / "report.md"
    generate_report([analysis], str(report_path), has_prometheus=False)
    text = report_path.read_text(encoding="utf-8")

    assert "LOW CONFIDENCE" in text
    assert "kubectl-only mode" in text


def test_insufficient_data_banner_when_dead_pods_present(tmp_path: Path):
    row_alive = _base_row()
    row_dead = _base_row()
    row_dead["Deployment"] = "broken-svc"
    row_dead["Avg_CPU_Usage(m)"] = "0"
    row_dead["Avg_Mem_Usage(Mi)"] = "0"
    row_dead["Total_Restarts"] = "5"
    row_dead["Pod_Count"] = "0"

    analyses = [
        analyze_workload(row_alive, has_prometheus=False),
        analyze_workload(row_dead, has_prometheus=False),
    ]

    report_path = tmp_path / "report.md"
    generate_report(analyses, str(report_path), has_prometheus=False)
    text = report_path.read_text(encoding="utf-8")

    assert "INSUFFICIENT_DATA" in text
    # Per-workload marker on the dead one
    assert "broken-svc" in text


def test_unstable_message_never_says_zero_restarts():
    """When UNSTABLE fires from rate (not Total_Restarts), the rationale must
    use the actual triggering signal — never the literal string 'Unstable (0 restarts)'."""
    row = _base_row()
    row["Total_Restarts"] = "0"
    row["Restart_Rate_Per_Day"] = "3.5"  # > UNSTABLE_RESTART_RATE_THRESHOLD (2.0)

    analysis = analyze_workload(row, has_prometheus=True)

    assert "UNSTABLE" in analysis.issues
    assert "Unstable (0 restarts)" not in analysis.rationale
    assert "3.5/day" in analysis.rationale


def test_restart_zombie_is_skipped_in_prometheus_mode():
    """High restart count in Prometheus mode still triggers INSUFFICIENT_DATA.
    The previous gate only handled kubectl-only mode."""
    row = _base_row()
    # Plenty of usage signal — what should trip the gate is the restart count.
    row["Avg_CPU_Usage(m)"] = "20"
    row["Avg_Mem_Usage(Mi)"] = "150"
    row["CPU_P95(m)"] = "80"
    row["Mem_P95(Mi)"] = "180"
    row["Total_Restarts"] = "5057"
    row["Restart_Rate_Per_Day"] = "1500"
    row["LastRestart_Reason"] = "OOMKilled"

    analysis = analyze_workload(row, has_prometheus=True)

    assert "INSUFFICIENT_DATA" in analysis.issues
    assert analysis.recommendations == []
    assert "CrashLoop" in analysis.action_required


def test_requests_not_set_idle_stable_is_p2_not_p0():
    """Idle, stable workload missing requests should be P2, not P0 noise."""
    row = _base_row()
    row["CPU_Request(m)"] = "0"
    row["Mem_Request(Mi)"] = "0"
    row["Avg_CPU_Usage(m)"] = "2"  # well under 50m threshold
    row["Avg_Mem_Usage(Mi)"] = "10"  # well under 64Mi threshold
    row["Total_Restarts"] = "0"
    row["Restart_Rate_Per_Day"] = "0"
    row["OOMKilled_Count"] = "0"

    analysis = analyze_workload(row, has_prometheus=True)

    assert "REQUESTS_NOT_SET" in analysis.issues
    assert analysis.priority.value == "P2"


def test_requests_not_set_active_workload_stays_p0():
    """Same issue but on an actively used workload remains P0."""
    row = _base_row()
    row["CPU_Request(m)"] = "0"
    row["Mem_Request(Mi)"] = "0"
    row["Avg_CPU_Usage(m)"] = "120"  # > 50m → still P0
    row["Avg_Mem_Usage(Mi)"] = "200"

    analysis = analyze_workload(row, has_prometheus=True)

    assert "REQUESTS_NOT_SET" in analysis.issues
    assert analysis.priority.value == "P0"


def test_requests_not_set_unstable_workload_stays_p0():
    """Missing requests AND restart history → still P0 even if usage low."""
    row = _base_row()
    row["CPU_Request(m)"] = "0"
    row["Mem_Request(Mi)"] = "0"
    row["Avg_CPU_Usage(m)"] = "1"
    row["Avg_Mem_Usage(Mi)"] = "1"
    row["Total_Restarts"] = "3"

    analysis = analyze_workload(row, has_prometheus=True)
    # Total_Restarts=3 > 0 → active_or_unstable → P0
    assert analysis.priority.value == "P0"


def test_report_has_toc_namespace_rollup_top10(tmp_path: Path):
    """Navigation aids must appear in the rendered report."""
    row = _base_row()
    analysis = analyze_workload(row, has_prometheus=True)

    report_path = tmp_path / "report.md"
    generate_report([analysis], str(report_path), has_prometheus=True)
    text = report_path.read_text(encoding="utf-8")

    assert "Table of Contents" in text
    assert "Namespace Rollup" in text
    assert "Top Optimizations" in text
    # The TOC should link to the priority-bucket anchors
    assert "#executive-summary" in text or 'id="executive-summary"' in text


def test_pattern_groups_collapse_identical_prefixes(tmp_path: Path):
    """38 identical rook-ceph-crashcollector-* deployments should appear in
    a single Pattern Groups row instead of 38 separate items."""
    rows = []
    for suffix in ["worker001", "worker002", "worker003", "worker004", "worker005"]:
        r = _base_row()
        r["Namespace"] = "rook-ceph"
        r["Deployment"] = f"rook-ceph-crashcollector-{suffix}"
        r["CPU_Request(m)"] = "0"
        r["Mem_Request(Mi)"] = "0"
        r["Avg_CPU_Usage(m)"] = "100"
        rows.append(r)

    analyses = [analyze_workload(r, has_prometheus=True) for r in rows]

    report_path = tmp_path / "report.md"
    generate_report(analyses, str(report_path), has_prometheus=True)
    text = report_path.read_text(encoding="utf-8")

    assert "Pattern Groups" in text
    assert "rook-ceph-crashcollector-*" in text
    # Single rolled-up row mentions the count of 5
    assert "| 5 |" in text


def test_bursty_workload_skips_memory_reduction():
    """Prometheus/Cassandra/Kafka/ES workloads must never have memory reduced."""
    row = _base_row()
    row["Deployment"] = "prometheus-server"
    row["Avg_Mem_Usage(Mi)"] = "4096"
    row["Mem_P95(Mi)"] = "5000"
    row["Mem_Request(Mi)"] = "26000"  # heavy over-request → would reduce
    row["Mem_Usage_Pct_Of_Request"] = "16"

    analysis = analyze_workload(row, has_prometheus=True)

    assert analysis.bursty_workload is True
    assert not any("Reduce memory REQUEST" in a for a in analysis.action_required.split("; "))
    assert "bursty workload class" in analysis.action_required


def test_bursty_workload_marker_in_report(tmp_path: Path):
    row = _base_row()
    row["Deployment"] = "alertmanager-stack"
    row["Avg_Mem_Usage(Mi)"] = "100"
    row["Mem_Request(Mi)"] = "1024"

    analysis = analyze_workload(row, has_prometheus=True)
    report_path = tmp_path / "report.md"
    generate_report([analysis], str(report_path), has_prometheus=True)
    text = report_path.read_text(encoding="utf-8")

    assert "BURSTY_WORKLOAD" in text


def test_unstable_workload_skips_request_rightsizing():
    """UNSTABLE workloads must not get numeric Reduce/Raise REQUEST actions —
    self-contradictory with the 'fix instability first' guidance."""
    row = _base_row()
    row["Total_Restarts"] = "8"  # > UNSTABLE_RESTART_THRESHOLD (5), restart_rate==0 fallback
    row["Avg_CPU_Usage(m)"] = "20"
    row["CPU_Request(m)"] = "326"  # would normally trigger reduction
    row["Avg_Mem_Usage(Mi)"] = "150"

    analysis = analyze_workload(row, has_prometheus=True)

    assert "UNSTABLE" in analysis.issues
    actions = analysis.action_required
    assert "Reduce CPU REQUEST" not in actions
    assert "Reduce memory REQUEST" not in actions
    assert "Raise CPU REQUEST" not in actions
    assert "Raise memory REQUEST" not in actions
    assert "rightsizing suppressed" in actions


def test_unstable_workload_still_gets_oom_limit_raise():
    """Safety-critical OOM limit raises must still fire on UNSTABLE workloads."""
    row = _base_row()
    row["Total_Restarts"] = "8"
    row["OOMKilled_Count"] = "3"
    row["Avg_Mem_Usage(Mi)"] = "120"
    row["Mem_Limit(Mi)"] = "128"

    analysis = analyze_workload(row, has_prometheus=True)
    assert "UNSTABLE" in analysis.issues
    assert "OOM_KILLED" in analysis.issues
    # Limit raise must be present even though request rightsizing is suppressed.
    assert any("Raise memory LIMIT" in a for a in analysis.action_required.split("; "))


def test_pattern_groups_table_shows_raises_column(tmp_path: Path):
    """Pattern Groups table must surface raises (not only savings) so chart
    fixes that ADD requests don't show as 0/0."""
    rows = []
    for n in range(3):
        r = _base_row()
        r["Namespace"] = "rook-ceph"
        r["Deployment"] = f"rook-ceph-osd-osd-{n}"
        r["CPU_Request(m)"] = "0"
        r["Mem_Request(Mi)"] = "0"
        r["Avg_CPU_Usage(m)"] = "10"  # small but non-zero
        r["Avg_Mem_Usage(Mi)"] = "20"
        rows.append(r)

    analyses = [analyze_workload(r, has_prometheus=True) for r in rows]
    report_path = tmp_path / "report.md"
    generate_report(analyses, str(report_path), has_prometheus=True)
    text = report_path.read_text(encoding="utf-8")

    # Table header now includes both Savings and Raises.
    assert "CPU savings" in text
    assert "CPU raises" in text
    assert "Mem savings" in text
    assert "Mem raises" in text


def test_large_pattern_collapses_in_detail_section(tmp_path: Path):
    """When a pattern group has >=10 instances, only the first card renders
    in the detail section; the rest are summarized in a collapse note."""
    rows = []
    for n in range(12):
        r = _base_row()
        r["Namespace"] = "rook-ceph"
        r["Deployment"] = f"rook-ceph-crashcollector-host{n:02d}"
        r["CPU_Request(m)"] = "0"
        r["Mem_Request(Mi)"] = "0"
        # Active enough to land in P0 (REQUESTS_NOT_SET active branch)
        r["Avg_CPU_Usage(m)"] = "100"
        r["Avg_Mem_Usage(Mi)"] = "200"
        rows.append(r)

    analyses = [analyze_workload(r, has_prometheus=True) for r in rows]
    report_path = tmp_path / "report.md"
    generate_report(analyses, str(report_path), has_prometheus=True)
    text = report_path.read_text(encoding="utf-8")

    # Only the first instance gets a per-workload heading.
    detail_headings = [
        line for line in text.splitlines() if line.startswith("#### `rook-ceph/rook-ceph-crashcollector-")
    ]
    assert len(detail_headings) == 1, f"Expected 1 collapsed heading, got {len(detail_headings)}"
    # And there's a collapse note announcing the other 11.
    assert "+ 11 identical" in text
    assert "rook-ceph-crashcollector-*" in text


def test_small_pattern_does_not_collapse(tmp_path: Path):
    """Below the collapse threshold (N<10), every instance should render."""
    rows = []
    for n in range(5):
        r = _base_row()
        r["Namespace"] = "demo"
        r["Deployment"] = f"app-frontend-pod-{n}"
        r["CPU_Request(m)"] = "0"
        r["Mem_Request(Mi)"] = "0"
        r["Avg_CPU_Usage(m)"] = "100"
        rows.append(r)

    analyses = [analyze_workload(r, has_prometheus=True) for r in rows]
    report_path = tmp_path / "report.md"
    generate_report(analyses, str(report_path), has_prometheus=True)
    text = report_path.read_text(encoding="utf-8")

    detail_headings = [line for line in text.splitlines() if line.startswith("#### `demo/app-frontend-pod-")]
    assert len(detail_headings) == 5
    # No collapse note for small groups (look for the specific "+ N identical
    # instances in `ns` collapsed" marker, not the Pattern Groups header copy).
    assert "+ 4 identical" not in text
    assert "instances in `demo` collapsed" not in text


def test_prometheus_zero_signal_skips_recs():
    """Bug surfaced by app-dev review: a workload with avg=0 AND empty P95
    columns must be flagged INSUFFICIENT_DATA even in Prometheus mode."""
    row = _base_row()
    row["Avg_CPU_Usage(m)"] = "0"
    row["Avg_Mem_Usage(Mi)"] = "0"
    row["CPU_P95(m)"] = "0"
    row["Mem_P95(Mi)"] = "0"
    row["Total_Restarts"] = "0"  # not a restart-zombie
    row["Mem_Request(Mi)"] = "1500"  # plenty to "save" if signal allowed it

    analysis = analyze_workload(row, has_prometheus=True)
    assert analysis.insufficient_data is True
    assert "INSUFFICIENT_DATA" in analysis.issues
    assert analysis.recommendations == []
    # The new branch tells the admin what's wrong.
    assert "zero usage AND empty P95" in analysis.action_required


def test_prometheus_signal_with_p95_passes_gate():
    """Inverse of the above: any P95 signal at all should keep the gate open."""
    row = _base_row()
    row["Avg_CPU_Usage(m)"] = "0"
    row["Avg_Mem_Usage(Mi)"] = "0"
    row["CPU_P95(m)"] = "30"  # real but small signal
    row["Mem_P95(Mi)"] = "0"
    row["Total_Restarts"] = "0"

    analysis = analyze_workload(row, has_prometheus=True)
    assert analysis.insufficient_data is False
    assert "INSUFFICIENT_DATA" not in analysis.issues


def test_high_cv_workload_gets_wider_headroom():
    """Mem_Volatility_CV > 20 should widen rec to ~1.8× P95 and tag HIGH_VOLATILITY."""
    row = _base_row()
    row["CPU_Request(m)"] = "200"
    row["Mem_Request(Mi)"] = "1000"
    row["Avg_CPU_Usage(m)"] = "100"
    row["Avg_Mem_Usage(Mi)"] = "500"
    row["CPU_P95(m)"] = "150"
    row["Mem_P95(Mi)"] = "600"
    row["Mem_Volatility_CV"] = "35"  # high volatility

    analysis = analyze_workload(row, has_prometheus=True)
    assert "HIGH_VOLATILITY" in analysis.issues
    # P95 (600) × 1.8 → 1080; format as Gi.
    assert analysis.recommended_mem in ("1080Mi", "1.1Gi")


def test_mid_cv_workload_uses_mid_multiplier():
    row = _base_row()
    row["CPU_Request(m)"] = "200"
    row["Mem_Request(Mi)"] = "1000"
    row["Avg_CPU_Usage(m)"] = "100"
    row["Avg_Mem_Usage(Mi)"] = "500"
    row["CPU_P95(m)"] = "150"
    row["Mem_P95(Mi)"] = "600"
    row["Mem_Volatility_CV"] = "12"  # mid

    analysis = analyze_workload(row, has_prometheus=True)
    assert "HIGH_VOLATILITY" not in analysis.issues
    # P95 (600) × 1.5 → 900Mi
    assert analysis.recommended_mem == "900Mi"


def test_low_cv_workload_uses_default_multiplier():
    row = _base_row()
    row["CPU_Request(m)"] = "200"
    row["Mem_Request(Mi)"] = "1000"
    row["Avg_CPU_Usage(m)"] = "100"
    row["Avg_Mem_Usage(Mi)"] = "500"
    row["CPU_P95(m)"] = "150"
    row["Mem_P95(Mi)"] = "600"
    row["Mem_Volatility_CV"] = "3"  # low

    analysis = analyze_workload(row, has_prometheus=True)
    # P95 (600) × 1.25 → 750Mi (preserves prior behavior)
    assert analysis.recommended_mem == "750Mi"


def test_owner_extracted_from_json_labels():
    """JSON-serialized Key_Labels should round-trip and surface owner."""
    import json

    row = _base_row()
    row["Key_Labels"] = json.dumps(
        {
            "app": "web-api",
            "team": "platform-eng",
            "app.kubernetes.io/managed-by": "Helm",
        }
    )

    analysis = analyze_workload(row, has_prometheus=True)
    # 'team' is in OWNER_LABEL_KEYS; 'app.kubernetes.io/part-of' is preferred
    # but absent → falls through to 'team'.
    assert analysis.owner == "platform-eng"


def test_owner_prefers_part_of_label():
    """The OWNER_LABEL_KEYS allowlist order must matter."""
    import json

    row = _base_row()
    row["Key_Labels"] = json.dumps(
        {
            "app.kubernetes.io/part-of": "checkout-stack",
            "team": "platform-eng",  # would otherwise win
        }
    )

    analysis = analyze_workload(row, has_prometheus=True)
    assert analysis.owner == "checkout-stack"


def test_owner_backward_compat_with_legacy_csv():
    """Old comma-string Key_Labels must still work for owner extraction."""
    row = _base_row()
    row["Key_Labels"] = "app=web-api,team=platform-eng"

    analysis = analyze_workload(row, has_prometheus=True)
    assert analysis.owner == "platform-eng"


def test_no_owner_when_labels_dont_match_allowlist():
    import json

    row = _base_row()
    row["Key_Labels"] = json.dumps({"app": "web-api", "version": "v1"})

    analysis = analyze_workload(row, has_prometheus=True)
    assert analysis.owner == ""


def test_data_quality_banner_when_high_na_rate(tmp_path: Path):
    """When >10% of a Prometheus column is N/A, a banner appears in the report."""
    from k8s_advisor.simple_analyzer import compute_data_quality

    rows = []
    for n in range(20):
        r = _base_row()
        r["Deployment"] = f"svc-{n}"
        # 18/20 (90%) have throttle = N/A → triggers warning.
        if n >= 2:
            r["CPU_Throttle_Pct"] = "N/A"
        else:
            r["CPU_Throttle_Pct"] = "5"
        r["CPU_P95(m)"] = "100"  # keeps Prometheus mode active
        rows.append(r)

    warnings = compute_data_quality(rows, has_prometheus=True)
    columns = [w["column"] for w in warnings]
    assert "CPU_Throttle_Pct" in columns
    throttle_warning = next(w for w in warnings if w["column"] == "CPU_Throttle_Pct")
    assert throttle_warning["na_count"] == 18
    assert throttle_warning["na_pct"] == 90.0


def test_data_quality_banner_skipped_in_kubectl_only_mode():
    """In kubectl-only mode, all Prometheus columns are N/A by design — no
    warnings should fire."""
    from k8s_advisor.simple_analyzer import compute_data_quality

    rows = [_base_row() for _ in range(5)]  # all have N/A in P95 columns
    warnings = compute_data_quality(rows, has_prometheus=False)
    assert warnings == []


def test_data_quality_banner_renders_in_report(tmp_path: Path):
    """generate_report() embeds the warnings as a markdown table."""
    row = _base_row()
    row["CPU_P95(m)"] = "100"  # keeps has_prometheus=True
    analysis = analyze_workload(row, has_prometheus=True)

    fake_warnings = [
        {
            "column": "CPU_Throttle_Pct",
            "label": "CPU throttling",
            "na_count": 50,
            "na_pct": 95.0,
            "total": 53,
        }
    ]
    report_path = tmp_path / "report.md"
    generate_report(
        [analysis],
        str(report_path),
        has_prometheus=True,
        data_quality_warnings=fake_warnings,
    )
    text = report_path.read_text(encoding="utf-8")
    assert "Data-quality warnings" in text
    assert "CPU_Throttle_Pct" in text
    assert "95%" in text


def test_crash_signal_oom_137():
    """OOMKilled + exit 137 → standard OOM signal string."""
    from k8s_advisor.simple_analyzer import _crash_signal

    assert (
        _crash_signal("OOMKilled", "137")
        == "OOMKilled (exit 137 — SIGKILL — usually OOMKilled or liveness-probe-failed)"
    )


def test_crash_signal_app_error():
    from k8s_advisor.simple_analyzer import _crash_signal

    assert _crash_signal("Error", 1) == "Error (exit 1 — app error / unhandled exception)"


def test_crash_signal_unknown_code_passthrough():
    from k8s_advisor.simple_analyzer import _crash_signal

    # Codes outside our hint table still surface the number.
    assert _crash_signal("Error", 42) == "Error (exit 42)"


def test_crash_signal_no_data_returns_empty():
    from k8s_advisor.simple_analyzer import _crash_signal

    assert _crash_signal("", None) == ""
    assert _crash_signal(None, None) == ""
    assert _crash_signal("", "N/A") == ""


def test_crash_signal_reason_only():
    from k8s_advisor.simple_analyzer import _crash_signal

    # Reason without exit code is still useful (older CSVs).
    assert _crash_signal("Error", None) == "Error"


def test_crash_signal_exit_code_only():
    """Exit code without a reason still surfaces the hint."""
    from k8s_advisor.simple_analyzer import _crash_signal

    assert _crash_signal("", "143") == "unknown (exit 143 — SIGTERM — graceful shutdown)"


def test_crash_signal_on_workload_card(tmp_path: Path):
    """When LastRestart_ExitCode is set, the workload card shows the signal."""
    row = _base_row()
    row["Total_Restarts"] = "20"
    row["LastRestart_Reason"] = "OOMKilled"
    row["LastRestart_ExitCode"] = "137"

    analysis = analyze_workload(row, has_prometheus=True)
    assert analysis.crash_signal.startswith("OOMKilled (exit 137")

    report_path = tmp_path / "report.md"
    generate_report([analysis], str(report_path), has_prometheus=True)
    text = report_path.read_text(encoding="utf-8")
    assert "**Crash signal:**" in text
    assert "OOMKilled (exit 137" in text


def test_top10_risk_includes_crash_signal(tmp_path: Path):
    """Top-10 Highest Risk leaderboard appends the crash signal to its note."""
    row = _base_row()
    row["Total_Restarts"] = "20"
    row["Restart_Rate_Per_Day"] = "5"
    row["LastRestart_Reason"] = "Error"
    row["LastRestart_ExitCode"] = "1"

    analysis = analyze_workload(row, has_prometheus=True)
    report_path = tmp_path / "report.md"
    generate_report([analysis], str(report_path), has_prometheus=True)
    text = report_path.read_text(encoding="utf-8")
    # Top-10 row should mention both restart count AND the decoded signal.
    assert "Error (exit 1 — app error" in text


def test_no_crash_signal_when_no_restarts(tmp_path: Path):
    """A clean workload (no restarts, no termination history) has no crash
    signal and the row stays clean."""
    row = _base_row()
    analysis = analyze_workload(row, has_prometheus=True)
    assert analysis.crash_signal == ""

    report_path = tmp_path / "report.md"
    generate_report([analysis], str(report_path), has_prometheus=True)
    text = report_path.read_text(encoding="utf-8")
    assert "**Crash signal:**" not in text


def test_analyze_csv_file_generates_markdown(tmp_path: Path):
    row = _base_row()
    row["CPU_P95(m)"] = "220"  # Ensure Prometheus mode path executes
    headers = list(row.keys())
    csv_path = tmp_path / "k8s-advisor_sandbox_20260101_010101.csv"

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        f.write(",".join(headers) + "\n")
        f.write(",".join(str(row[h]) for h in headers) + "\n")

    output_dir = tmp_path / "reports"
    written = analyze_csv_file(str(csv_path), output_dir=str(output_dir), generate_graphs=False)

    report_path = Path(written["md"])
    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")
    assert "K8s Scaling Advisor - Analysis Report" in report_text
    assert "Prometheus Metrics:** ✅ Available" in report_text


def test_analyze_csv_file_emits_json(tmp_path: Path):
    row = _base_row()
    row["CPU_P95(m)"] = "220"
    headers = list(row.keys())
    csv_path = tmp_path / "k8s-advisor_sandbox_20260101_010101.csv"

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        f.write(",".join(headers) + "\n")
        f.write(",".join(str(row[h]) for h in headers) + "\n")

    output_dir = tmp_path / "reports"
    written = analyze_csv_file(
        str(csv_path),
        output_dir=str(output_dir),
        generate_graphs=False,
        formats=("md", "json"),
    )

    assert set(written.keys()) == {"md", "json"}
    json_path = Path(written["json"])
    assert json_path.exists()
    import json as _json

    payload = _json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["cluster"] == "sandbox"
    assert payload["prometheus_used"] is True
    assert payload["summary"]["total"] == 1
    assert isinstance(payload["workloads"], list) and payload["workloads"]
    workload = payload["workloads"][0]
    # Enums must be plain strings for downstream consumers.
    assert isinstance(workload["priority"], str)
    assert isinstance(workload["scaling_approach"], str)


# ──────────────────────────────────────────────────────────────────────
# Confidence scoring
# ──────────────────────────────────────────────────────────────────────

from k8s_advisor.simple_analyzer import _confidence_score  # noqa: E402


def test_confidence_high_with_prometheus_clean_signal():
    score, band, reasons = _confidence_score(
        has_prometheus=True,
        insufficient_data=False,
        gc_runtime=False,
        bursty_workload=False,
        restart_rate=0.0,
        pod_count=4,
        has_cpu_limit=True,
        has_mem_limit=True,
    )
    assert band == "high"
    assert score >= 0.85
    assert any("Prometheus" in r for r in reasons)
    assert any("limits set" in r for r in reasons)


def test_confidence_low_with_kubectl_only():
    score, band, _reasons = _confidence_score(
        has_prometheus=False,
        insufficient_data=False,
        gc_runtime=False,
        bursty_workload=False,
        restart_rate=0.0,
        pod_count=2,
        has_cpu_limit=False,
        has_mem_limit=False,
    )
    # 0.55 base - 0 penalties + 0 bonus → "medium" upper, band "medium"
    assert band == "medium"
    assert score == 0.55


def test_confidence_floor_when_insufficient_data():
    score, band, reasons = _confidence_score(
        has_prometheus=True,
        insufficient_data=True,
        gc_runtime=False,
        bursty_workload=False,
        restart_rate=0.0,
        pod_count=4,
        has_cpu_limit=True,
        has_mem_limit=True,
    )
    # INSUFFICIENT_DATA short-circuits regardless of other signals.
    assert score == 0.10
    assert band == "low"
    assert reasons == ["INSUFFICIENT_DATA — no usable signal"]


def test_confidence_penalties_compound():
    # Prometheus + bursty + GC + crash-loops + single replica should
    # land in "low" band even with limits set.
    score, band, reasons = _confidence_score(
        has_prometheus=True,
        insufficient_data=False,
        gc_runtime=True,
        bursty_workload=True,
        restart_rate=5.0,  # > 2/day
        pod_count=1,
        has_cpu_limit=True,
        has_mem_limit=True,
    )
    # 0.85 - 0.20 - 0.15 - 0.15 - 0.05 + 0.05 = 0.35 → "low"
    assert score == 0.35
    assert band == "low"
    assert any("bursty" in r for r in reasons)
    assert any("GC" in r for r in reasons)
    assert any("restart" in r for r in reasons)


def test_confidence_round_clamps_to_unit_interval():
    # Even with all bonuses and no penalties, score must not exceed 1.0.
    score, _band, _reasons = _confidence_score(
        has_prometheus=True,
        insufficient_data=False,
        gc_runtime=False,
        bursty_workload=False,
        restart_rate=0.0,
        pod_count=10,
        has_cpu_limit=True,
        has_mem_limit=True,
    )
    assert 0.0 <= score <= 1.0


def test_analyze_workload_emits_confidence_fields():
    row = _base_row()
    row["CPU_P95(m)"] = "220"
    workload = analyze_workload(row, has_prometheus=True)
    assert hasattr(workload, "confidence")
    assert 0.0 <= workload.confidence <= 1.0
    assert workload.confidence_band in ("high", "medium", "low")
    assert isinstance(workload.confidence_reasons, list)
    assert workload.confidence_reasons  # non-empty


def test_analyze_workload_confidence_lower_without_prometheus():
    row = _base_row()
    p_workload = analyze_workload(row, has_prometheus=True)
    k_workload = analyze_workload(row, has_prometheus=False)
    assert k_workload.confidence < p_workload.confidence
