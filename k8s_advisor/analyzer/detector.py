"""Issue detection logic for deployments and statefulsets."""

from ..constants import (
    CPU_OVER_REQUESTED_THRESHOLD,
    CPU_UNDER_REQUESTED_THRESHOLD,
    MEM_OVER_REQUESTED_THRESHOLD,
    MEM_SATURATION_LIMIT_THRESHOLD,
    MEM_UNDER_REQUESTED_THRESHOLD,
    MEMORY_SCALABLE_PATTERNS,
    OOM_RISK_THRESHOLD,
    UNSTABLE_RESTART_RATE_THRESHOLD,
    UNSTABLE_RESTART_THRESHOLD,
)
from .models import DeploymentAnalysis, IssueType


def detect_issues(analysis: DeploymentAnalysis) -> list[IssueType]:
    """Detect all resource and stability issues with a workload.

    This function performs comprehensive issue detection including:
    - Missing resource requests (P0 blocker)
    - CPU throttling (P0 blocker)
    - Confirmed OOM kills (P0 blocker)
    - Memory saturation risk (P1 high priority)
    - Unstable workloads (P1 high priority)
    - Over/under-requested resources (P2 optimization)
    - RWO PVC conflicts (P2 architectural)
    - Missing CPU limits (P2 best practice)
    - Memory HPA candidates (rare pattern)

    Args:
        analysis: DeploymentAnalysis object with metrics populated

    Returns:
        List of detected IssueType enums

    Critical Logic:
        - For restart detection, ALWAYS validate total_restarts > 0 before trusting
          restart_rate_per_day (Prometheus can return garbage like 92.87/day when total=0)
        - Memory saturation uses TWO thresholds: >200% of request OR >90% of limit
        - OOM_KILLED takes precedence over MEM_SATURATION (don't double-flag)
    """
    issues: list[IssueType] = []

    # ─────────────────────────────────────────────────────────────────────────
    # Check for missing metrics (no data at all)
    # ─────────────────────────────────────────────────────────────────────────
    if analysis.avg_cpu_usage_m == 0 and analysis.avg_mem_usage_mi == 0:
        issues.append(IssueType.NO_METRICS)
        return issues  # Cannot detect other issues without metrics

    # ─────────────────────────────────────────────────────────────────────────
    # P0 Issue: Missing resource requests
    # ─────────────────────────────────────────────────────────────────────────
    # IMPACT: Scheduler cannot place pods correctly, HPA has no baseline
    if analysis.cpu_request_m == 0 or analysis.mem_request_mi == 0:
        issues.append(IssueType.REQUESTS_NOT_SET)

    # ─────────────────────────────────────────────────────────────────────────
    # P0 Issue: CPU throttling
    # ─────────────────────────────────────────────────────────────────────────
    # DETECTION: Two scenarios:
    # 1. Usage exceeds limit (direct throttling)
    # 2. Usage >300% of request with low limit (<2x request) - indirect throttling
    if analysis.cpu_limit_m > 0:
        if analysis.avg_cpu_usage_m > analysis.cpu_limit_m or (
            analysis.cpu_usage_percent > 300 and analysis.cpu_limit_m < analysis.cpu_request_m * 2
        ):
            issues.append(IssueType.CPU_THROTTLED)

    # ─────────────────────────────────────────────────────────────────────────
    # P2 Issue: Missing CPU limits
    # ─────────────────────────────────────────────────────────────────────────
    # Best practice: Always set limits to prevent resource hoarding
    if analysis.cpu_limit_m == 0:
        issues.append(IssueType.MISSING_CPU_LIMITS)

    # ─────────────────────────────────────────────────────────────────────────
    # P2 Issue: RWO PVC (architectural HPA blocker)
    # ─────────────────────────────────────────────────────────────────────────
    # ReadWriteOnce PVCs cannot be mounted by multiple pods on different nodes
    # This permanently blocks HPA horizontal scaling
    if analysis.rwo_pvc:
        issues.append(IssueType.RWO_PVC)

    # ─────────────────────────────────────────────────────────────────────────
    # P0 Issue: Confirmed OOM kill (pod actually died)
    # ─────────────────────────────────────────────────────────────────────────
    if "OOMKilled" in analysis.restart_reason:
        issues.append(IssueType.OOM_KILLED)

    # ─────────────────────────────────────────────────────────────────────────
    # P1 Issue: Memory saturation (approaching limit, no kill YET)
    # ─────────────────────────────────────────────────────────────────────────
    # TWO thresholds (OR condition):
    # 1. >200% of request (very high usage relative to request)
    # 2. >90% of limit (if limit is set, approaching hard ceiling)
    #
    # IMPORTANT: Only flag if NOT already flagged as OOM_KILLED
    if IssueType.OOM_KILLED not in issues:
        if analysis.mem_usage_percent > OOM_RISK_THRESHOLD or (
            analysis.mem_limit_mi > 0
            and analysis.avg_mem_usage_mi > (analysis.mem_limit_mi * (MEM_SATURATION_LIMIT_THRESHOLD / 100))
        ):
            issues.append(IssueType.MEM_SATURATION)

    # ─────────────────────────────────────────────────────────────────────────
    # P1 Issue: Unstable workload (frequent restarts)
    # ─────────────────────────────────────────────────────────────────────────
    # CRITICAL DEFENSIVE LOGIC:
    # Prometheus rate() function can return garbage values (e.g., 92.87 restarts/day)
    # when actual total_restarts == 0. This is a known Prometheus behavior with
    # extrapolation on sparse data.
    #
    # SOLUTION: Always validate total_restarts > 0 before trusting rate data
    #
    # DETECTION: Two-tier approach:
    # 1. Primary: restart_rate_per_day > 2.0 (if total_restarts > 0)
    # 2. Fallback: total_restarts > 5 (if rate data is unavailable)
    if analysis.total_restarts == 0:
        # No restarts at all - definitely not unstable
        # Ignore restart_rate_per_day even if it's non-zero (Prometheus garbage)
        pass
    elif analysis.restart_rate_per_day > UNSTABLE_RESTART_RATE_THRESHOLD:
        # Rate-based threshold: >2 restarts/day is concerning
        # (only evaluated if total_restarts > 0 per above check)
        issues.append(IssueType.UNSTABLE)
    elif analysis.restart_rate_per_day == 0 and analysis.total_restarts > UNSTABLE_RESTART_THRESHOLD:
        # Fallback: Use count-based threshold when rate is unavailable
        # This handles missing Prometheus data with high kubectl restart counts
        issues.append(IssueType.UNSTABLE)

    # ─────────────────────────────────────────────────────────────────────────
    # P2 Issue: Over-requested CPU
    # ─────────────────────────────────────────────────────────────────────────
    # <50% CPU usage = wasteful over-provisioning
    if analysis.cpu_request_m > 0 and analysis.cpu_usage_percent < CPU_OVER_REQUESTED_THRESHOLD:
        issues.append(IssueType.CPU_OVER_REQUESTED)

    # ─────────────────────────────────────────────────────────────────────────
    # P2 Issue: Over-requested memory
    # ─────────────────────────────────────────────────────────────────────────
    # <50% memory usage = wasteful over-provisioning
    if analysis.mem_request_mi > 0 and analysis.mem_usage_percent < MEM_OVER_REQUESTED_THRESHOLD:
        issues.append(IssueType.MEM_OVER_REQUESTED)

    # ─────────────────────────────────────────────────────────────────────────
    # P2 Issue: Under-requested CPU
    # ─────────────────────────────────────────────────────────────────────────
    # >85% CPU usage = needs more resources
    # NOTE: This is P2 not P1 because if no throttling, app is functionally stable
    if analysis.cpu_request_m > 0 and analysis.cpu_usage_percent > CPU_UNDER_REQUESTED_THRESHOLD:
        issues.append(IssueType.CPU_UNDER_REQUESTED)

    # ─────────────────────────────────────────────────────────────────────────
    # P2 Issue: Under-requested memory
    # ─────────────────────────────────────────────────────────────────────────
    # >85% memory usage = needs more resources
    # NOTE: This is P2 not P1 because if no OOM kills, app is functionally stable
    if analysis.mem_request_mi > 0 and analysis.mem_usage_percent > MEM_UNDER_REQUESTED_THRESHOLD:
        issues.append(IssueType.MEM_UNDER_REQUESTED)

    # ─────────────────────────────────────────────────────────────────────────
    # Info: Single replica (availability risk, HPA requires multiple replicas)
    # ─────────────────────────────────────────────────────────────────────────
    if analysis.replicas == 1:
        issues.append(IssueType.SINGLE_REPLICA)

    # ─────────────────────────────────────────────────────────────────────────
    # Rare: Memory HPA candidate (memory-bound workload with high volatility)
    # ─────────────────────────────────────────────────────────────────────────
    # CONTEXT: Memory-based HPA is generally UNRELIABLE for GC runtimes (JVM, Node.js)
    # but CAN work for:
    # - Stateless caches (Redis, Memcached)
    # - Proxies/load balancers (nginx, envoy)
    # - Connection poolers (postgres, mysql sidecars)
    #
    # DETECTION: HIGH memory volatility + matches known memory-scalable pattern
    if analysis.mem_volatility == "HIGH":
        deployment_lower = analysis.deployment.lower()
        is_memory_bound = any(pattern in deployment_lower for pattern in MEMORY_SCALABLE_PATTERNS)
        if is_memory_bound:
            issues.append(IssueType.MEMORY_HPA_CANDIDATE)
        # Non-memory-bound apps with HIGH volatility are likely memory leaks
        # We don't flag them specially - the volatility will be noted in recommendations

    return issues
