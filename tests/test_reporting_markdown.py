"""Tests for markdown report generation module."""

from pathlib import Path

from k8s_advisor.analyzer.models import (
    DeploymentAnalysis,
    IssueType,
    Priority,
    ResourceRecommendation,
    ScalingApproach,
)
from k8s_advisor.reporting.markdown import generate_markdown_report


def _analysis() -> DeploymentAnalysis:
    return DeploymentAnalysis(
        cluster="sandbox",
        namespace="default",
        workload_type="Deployment",
        deployment="api",
        replicas=2,
        avg_cpu_usage_m=80.0,
        cpu_request_m=100.0,
        cpu_limit_m=200.0,
        cpu_usage_percent=80.0,
        avg_mem_usage_mi=150.0,
        mem_request_mi=200.0,
        mem_limit_mi=400.0,
        mem_usage_percent=75.0,
        total_restarts=0,
        max_pod_restarts=0,
        pods_restarting=0,
        restart_reason="",
        rwo_pvc=False,
        rwo_pvc_names="",
        priority=Priority.P2,
        issues=[IssueType.CPU_UNDER_REQUESTED],
        scaling_approach=ScalingApproach.HPA,
        recommended_resources=ResourceRecommendation(
            cpu_request="100m",
            cpu_limit="200m",
            memory_request="200Mi",
            memory_limit="400Mi",
            rationale="Right-sized",
            requires_manual_action=False,
        ),
        rationale="Good HPA candidate",
    )


def test_generate_markdown_report_writes_expected_sections(tmp_path: Path):
    report_path = tmp_path / "report.md"
    generate_markdown_report([_analysis()], str(report_path), has_prometheus=True)
    text = report_path.read_text(encoding="utf-8")

    assert "K8s Scaling Advisor - Analysis Report" in text
    assert "## Executive Summary" in text
    assert "## Scaling Approach Summary" in text
    assert "## Implementation Guide" in text
    assert "Good HPA candidate" in text
