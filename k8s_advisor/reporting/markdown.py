"""Markdown report generation."""

from datetime import datetime

from ..analyzer.models import DeploymentAnalysis, Priority, ScalingApproach


def generate_markdown_report(analyses: list[DeploymentAnalysis], output_path: str, has_prometheus: bool = True) -> None:
    """Generate comprehensive markdown report.

    Args:
        analyses: List of deployment analyses
        output_path: Path to write markdown file
        has_prometheus: Whether Prometheus metrics were available
    """
    with open(output_path, "w") as f:
        _write_header(f, analyses, has_prometheus)
        _write_executive_summary(f, analyses)
        _write_priority_breakdown(f, analyses)
        _write_scaling_approach_summary(f, analyses)
        _write_detailed_recommendations(f, analyses)
        _write_implementation_guide(f)


def _write_header(f, analyses: list[DeploymentAnalysis], has_prometheus: bool):
    """Write report header."""
    f.write("# K8s Scaling Advisor - Analysis Report\n\n")
    f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    f.write(f"**Total Workloads Analyzed:** {len(analyses)}\n\n")

    if has_prometheus:
        f.write("**Prometheus Metrics:** ✅ Available (enhanced analysis)\n\n")
    else:
        f.write("**Prometheus Metrics:** ⚠️ Not available (basic analysis using kubectl metrics-server only)\n\n")

    # Cluster summary
    clusters = set(a.cluster for a in analyses)
    f.write(f"**Clusters:** {', '.join(sorted(clusters))}\n\n")

    f.write("---\n\n")


def _write_executive_summary(f, analyses: list[DeploymentAnalysis]):
    """Write executive summary section."""
    f.write("## Executive Summary\n\n")

    p0_count = sum(1 for a in analyses if a.priority == Priority.P0)
    p1_count = sum(1 for a in analyses if a.priority == Priority.P1)
    p2_count = sum(1 for a in analyses if a.priority == Priority.P2)
    p3_count = sum(1 for a in analyses if a.priority == Priority.P3)

    manual_count = sum(
        1 for a in analyses if a.recommended_resources and a.recommended_resources.requires_manual_action
    )

    f.write("### Priority Breakdown\n\n")
    if p0_count > 0:
        f.write(f"⚠️ **P0 Blockers:** {p0_count} workloads require immediate attention\n\n")
    if p1_count > 0:
        f.write(f"🔴 **P1 High Priority:** {p1_count} workloads\n\n")
    if p2_count > 0:
        f.write(f"🟡 **P2 Medium Priority:** {p2_count} workloads\n\n")
    if p3_count > 0:
        f.write(f"🟢 **P3 Low Priority:** {p3_count} workloads\n\n")

    if manual_count > 0:
        f.write(f"**Manual Resource Adjustments Needed:** {manual_count} workloads\n\n")

    f.write("---\n\n")


def _write_priority_breakdown(f, analyses: list[DeploymentAnalysis]):
    """Write detailed priority breakdown."""
    f.write("## Workloads by Priority\n\n")

    for priority in [Priority.P0, Priority.P1, Priority.P2, Priority.P3]:
        workloads = [a for a in analyses if a.priority == priority]
        if not workloads:
            continue

        f.write(f"### {priority.value} Priority ({len(workloads)} workloads)\n\n")

        for analysis in workloads:
            f.write(f"#### `{analysis.namespace}/{analysis.deployment}` ({analysis.workload_type})\n\n")

            # Issues
            if analysis.issues:
                f.write("**Issues:**\n")
                for issue in analysis.issues:
                    f.write(f"- {issue.value}\n")
                f.write("\n")

            # Current state
            f.write("**Current Resources:**\n")
            f.write(f"- CPU: {analysis.cpu_request_m:.0f}m request")
            if analysis.cpu_limit_m > 0:
                f.write(f", {analysis.cpu_limit_m:.0f}m limit")
            f.write(f" (avg usage: {analysis.avg_cpu_usage_m:.0f}m)\n")

            f.write(f"- Memory: {analysis.mem_request_mi:.0f}Mi request")
            if analysis.mem_limit_mi > 0:
                f.write(f", {analysis.mem_limit_mi:.0f}Mi limit")
            f.write(f" (avg usage: {analysis.avg_mem_usage_mi:.0f}Mi)\n")
            f.write("\n")

            # Recommendations
            if analysis.recommended_resources:
                rec = analysis.recommended_resources
                f.write("**Recommended Resources:**\n")
                if rec.cpu_request:
                    f.write(f"- CPU Request: {rec.cpu_request}\n")
                if rec.cpu_limit:
                    f.write(f"- CPU Limit: {rec.cpu_limit}\n")
                if rec.memory_request:
                    f.write(f"- Memory Request: {rec.memory_request}\n")
                if rec.memory_limit:
                    f.write(f"- Memory Limit: {rec.memory_limit}\n")
                if rec.rationale:
                    f.write(f"\n**Rationale:** {rec.rationale}\n")
                f.write("\n")

            # Scaling approach
            f.write(f"**Scaling Approach:** {analysis.scaling_approach.value}\n\n")

            scaling_rationale = getattr(analysis, "scaling_rationale", "") or analysis.rationale
            if scaling_rationale:
                f.write(f"**Scaling Rationale:** {scaling_rationale}\n\n")

            f.write("---\n\n")


def _write_scaling_approach_summary(f, analyses: list[DeploymentAnalysis]):
    """Write scaling approach summary."""
    f.write("## Scaling Approach Summary\n\n")

    hpa_ready = [a for a in analyses if a.scaling_approach == ScalingApproach.HPA]
    vpa_ready = [a for a in analyses if a.scaling_approach == ScalingApproach.VPA]
    hpa_after_fix = [a for a in analyses if a.scaling_approach == ScalingApproach.HPA_AFTER_FIX]
    manual_only = [a for a in analyses if a.scaling_approach == ScalingApproach.MANUAL]
    excluded = [a for a in analyses if a.scaling_approach == ScalingApproach.NONE]

    f.write(f"- **HPA Ready:** {len(hpa_ready)} workloads\n")
    f.write(f"- **VPA Recommended:** {len(vpa_ready)} workloads\n")
    f.write(f"- **HPA After Fixes:** {len(hpa_after_fix)} workloads\n")
    f.write(f"- **Manual Only:** {len(manual_only)} workloads\n")
    f.write(f"- **Excluded:** {len(excluded)} workloads\n\n")

    f.write("---\n\n")


def _write_detailed_recommendations(f, analyses: list[DeploymentAnalysis]):
    """Write detailed recommendations section."""
    f.write("## Detailed Recommendations\n\n")

    # Group by namespace
    by_namespace = {}
    for analysis in analyses:
        if analysis.namespace not in by_namespace:
            by_namespace[analysis.namespace] = []
        by_namespace[analysis.namespace].append(analysis)

    for namespace in sorted(by_namespace.keys()):
        f.write(f"### Namespace: `{namespace}`\n\n")

        workloads = by_namespace[namespace]
        for analysis in sorted(workloads, key=lambda a: (a.priority.value, a.deployment)):
            f.write(f"- **{analysis.deployment}** ({analysis.workload_type}): ")
            f.write(f"{analysis.priority.value} | {analysis.scaling_approach.value}")

            if analysis.issues:
                f.write(f" | Issues: {', '.join(i.value for i in analysis.issues[:3])}")

            f.write("\n")

        f.write("\n")

    f.write("---\n\n")


def _write_implementation_guide(f):
    """Write implementation guide."""
    f.write("## Implementation Guide\n\n")

    f.write("### Phase 1: Fix P0 Blockers\n\n")
    f.write("1. Fix workloads with missing resource requests\n")
    f.write("2. Address OOM kills and CPU throttling\n")
    f.write("3. Stabilize workloads with high restart rates\n\n")

    f.write("### Phase 2: Deploy HPA\n\n")
    f.write('1. Start with "HPA Ready" workloads\n')
    f.write("2. Monitor for 1-2 weeks\n")
    f.write("3. Fix any issues that arise\n\n")

    f.write("### Phase 3: Optimize with VPA\n\n")
    f.write('1. Deploy VPA in "Off" mode for recommendations\n')
    f.write("2. Review and apply VPA suggestions\n")
    f.write('3. Consider VPA "Auto" mode for single-replica workloads\n\n')

    f.write("---\n\n")

    f.write("**Report generated by K8s Scaling Advisor**\n")
