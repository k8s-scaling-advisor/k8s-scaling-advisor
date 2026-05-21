"""Priority and scaling approach classification logic."""

from ..constants import EXCLUDED_DEPLOYMENTS
from .models import DeploymentAnalysis, IssueType, Priority, ScalingApproach


def determine_priority(analysis: DeploymentAnalysis) -> Priority:
    """Determine priority level based on detected issues.

    Priority Classification Logic:
    ─────────────────────────────────────────────────────────────────────────
    P0 (Blocker) - Operational emergencies that must be fixed before ANY rollout:
        - REQUESTS_NOT_SET: Scheduler cannot place pods; HPA has no baseline
        - CPU_THROTTLED: App is actively performance-degraded right now
        - OOM_KILLED: App is actively crashing (confirmed OOMKilled events)

    P1 (High) - High operational risk, not crashing today but at significant
                risk of imminent failure:
        - UNSTABLE: Frequent restarts (>2/day or >5 total if no rate data)
        - MEM_SATURATION: Approaching memory limits (>90% limit or >200% request,
                          no kills yet)

    P2 (Medium) - Optimization opportunities, no active operational risk:
        - CPU_UNDER_REQUESTED: Suboptimal but stable (85-200% usage, no throttling)
        - MEM_UNDER_REQUESTED: Suboptimal but stable (85-200% usage, no kills)
        - CPU_OVER_REQUESTED: Wasteful but functional
        - MEM_OVER_REQUESTED: Wasteful but functional
        - MISSING_CPU_LIMITS: Best practice violation
        - RWO_PVC: Informational (HPA architecturally impossible, but app healthy)

    P3 (Low) - Healthy workloads, no action needed soon

    IMPORTANT: Under-requested resources (85-200% usage) are P2, NOT P1.
    WHY: If no OOM kills or CPU throttling, the app is functionally stable.
    Limits provide burst capacity, and HPA can function even if not perfectly tuned.
    Request optimization improves efficiency but doesn't fix breakage.

    Args:
        analysis: DeploymentAnalysis with issues already detected

    Returns:
        Priority enum (P0, P1, P2, or P3)
    """
    # ─────────────────────────────────────────────────────────────────────────
    # P0: Operational emergencies (fix before ANY rollout)
    # ─────────────────────────────────────────────────────────────────────────
    p0_issues = {
        IssueType.REQUESTS_NOT_SET,  # Scheduler impact + HPA fully blocked
        IssueType.CPU_THROTTLED,  # Active performance degradation
        IssueType.OOM_KILLED,  # App is actively crashing
    }

    if any(issue in p0_issues for issue in analysis.issues):
        return Priority.P0

    # ─────────────────────────────────────────────────────────────────────────
    # P1: High operational risk (imminent failure risk)
    # ─────────────────────────────────────────────────────────────────────────
    p1_issues = {
        IssueType.UNSTABLE,  # Frequent restarts (>2/day or >5 total)
        IssueType.MEM_SATURATION,  # Approaching memory limits (no kills yet)
    }

    if any(issue in p1_issues for issue in analysis.issues):
        return Priority.P1

    # ─────────────────────────────────────────────────────────────────────────
    # P2: Medium priority (optimization, no operational risk)
    # ─────────────────────────────────────────────────────────────────────────
    p2_issues = {
        IssueType.CPU_UNDER_REQUESTED,  # 85-200% usage (stable but suboptimal)
        IssueType.MEM_UNDER_REQUESTED,  # 85-200% usage (stable but suboptimal)
        IssueType.CPU_OVER_REQUESTED,  # <50% usage (wasteful)
        IssueType.MEM_OVER_REQUESTED,  # <50% usage (wasteful)
        IssueType.MISSING_CPU_LIMITS,  # Best practice
        IssueType.RWO_PVC,  # Architectural constraint (informational)
    }

    if any(issue in p2_issues for issue in analysis.issues):
        return Priority.P2

    # ─────────────────────────────────────────────────────────────────────────
    # P3: Low priority (healthy, no issues)
    # ─────────────────────────────────────────────────────────────────────────
    return Priority.P3


def determine_scaling_approach(analysis: DeploymentAnalysis, k8s_version: str = "1.33") -> ScalingApproach:
    """Determine appropriate scaling approach (HPA, VPA, Manual, or None).

    Scaling Approach Decision Tree:
    ─────────────────────────────────────────────────────────────────────────
    1. Excluded workload (e.g., Logstash) → NONE
    2. StatefulSet → VPA (K8s 1.33+) or MANUAL (older versions)
    3. RWO PVC → VPA (K8s 1.33+) or MANUAL (HPA architecturally impossible)
    4. Single replica → VPA (K8s 1.33+) or MANUAL
    5. Has P0 issues → HPA_AFTER_FIX (fix blockers first)
    6. Unstable (P1) → HPA_AFTER_FIX (fix restarts first)
    7. High usage (>85% CPU or memory) + multi-replica → HPA
    8. Multi-replica (≥3 replicas) → HPA (variable load assumption)
    9. Over-requested resources → VPA (K8s 1.33+) or MANUAL
    10. Default → MANUAL

    IMPORTANT NOTES:
    ──────────────────
    - StatefulSets NEVER use HPA (ordered startup conflicts with horizontal scaling)
    - RWO PVCs permanently block HPA (cannot mount on multiple nodes)
    - Single-replica workloads cannot be horizontally scaled
    - HPA and HPA_AFTER_FIX deployments can optionally run VPA in updateMode: Off
      for continuous right-sizing insights (see HPA+VPA Coexistence section)

    HPA+VPA Coexistence Patterns:
    ──────────────────────────────
    ✅ SAFE Pattern 1: HPA (active) + VPA (updateMode: Off) on any metrics
       - VPA provides continuous insights without control loop conflicts
       - Perfectly safe for all HPA deployments

    ✅ SAFE Pattern 2: HPA (active) + VPA (active) on DIFFERENT metrics
       - Example: HPA on CPU, VPA on Memory
       - No control loop conflicts when metrics don't overlap

    ❌ DANGEROUS Anti-Pattern: HPA (active) + VPA (active) on SAME metric
       - Example: Both scaling based on CPU
       - Causes thrashing (both controllers fight over pod count/size)

    Args:
        analysis: DeploymentAnalysis with issues and priority set
        k8s_version: Kubernetes version (default: "1.33")

    Returns:
        ScalingApproach enum (HPA, VPA, MANUAL, NONE, or HPA_AFTER_FIX)
    """
    # Check if K8s version supports in-place VPA
    supports_in_place_vpa = _check_vpa_support(k8s_version)

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Excluded workloads → NONE
    # ─────────────────────────────────────────────────────────────────────────
    # Example: Logstash (JVM-based, plugin-constrained scaling)
    if any(excluded in analysis.deployment.lower() for excluded in EXCLUDED_DEPLOYMENTS):
        return ScalingApproach.NONE

    # ─────────────────────────────────────────────────────────────────────────
    # 2. StatefulSets → VPA or MANUAL (NEVER HPA)
    # ─────────────────────────────────────────────────────────────────────────
    # WHY: StatefulSets require ordered startup and stable network identities.
    # Horizontal scaling with HPA conflicts with these guarantees.
    # VPA (vertical scaling) is safe because it preserves pod identity.
    if analysis.workload_type.lower() == "statefulset":
        if supports_in_place_vpa:
            return ScalingApproach.VPA
        else:
            return ScalingApproach.MANUAL

    # ─────────────────────────────────────────────────────────────────────────
    # 3. RWO PVC → VPA or MANUAL (HPA architecturally impossible)
    # ─────────────────────────────────────────────────────────────────────────
    # WHY: ReadWriteOnce PVCs cannot be mounted by multiple pods on different
    # nodes. HPA horizontal scaling would try to create new pods on different
    # nodes, which will fail to mount the PVC.
    if analysis.rwo_pvc:
        if supports_in_place_vpa:
            return ScalingApproach.VPA
        else:
            return ScalingApproach.MANUAL

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Single replica → VPA or MANUAL
    # ─────────────────────────────────────────────────────────────────────────
    # WHY: Cannot horizontally scale a single-replica workload.
    # Even if there are P0 blockers (e.g., missing requests), after fixing them
    # the correct path is VPA (in-place resize) or MANUAL, not HPA.
    if analysis.replicas == 1:
        if supports_in_place_vpa:
            return ScalingApproach.VPA
        else:
            return ScalingApproach.MANUAL

    # ─────────────────────────────────────────────────────────────────────────
    # 5. P0 blockers → HPA_AFTER_FIX
    # ─────────────────────────────────────────────────────────────────────────
    # WHY: Must fix operational emergencies before enabling HPA:
    # - REQUESTS_NOT_SET: HPA has no baseline to scale from
    # - CPU_THROTTLED: App is actively degraded
    # - OOM_KILLED: App is crashing
    if analysis.priority == Priority.P0:
        return ScalingApproach.HPA_AFTER_FIX

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Unstable → HPA_AFTER_FIX
    # ─────────────────────────────────────────────────────────────────────────
    # WHY: Frequent restarts indicate instability. HPA will amplify the problem
    # by scaling up/down based on unstable metrics. Fix stability first.
    if IssueType.UNSTABLE in analysis.issues:
        return ScalingApproach.HPA_AFTER_FIX

    # ─────────────────────────────────────────────────────────────────────────
    # 7. High utilization + multi-replica → HPA
    # ─────────────────────────────────────────────────────────────────────────
    # >85% CPU or memory usage indicates need for horizontal scaling
    if (analysis.cpu_usage_percent > 85 or analysis.mem_usage_percent > 85) and analysis.replicas > 1:
        return ScalingApproach.HPA

    # ─────────────────────────────────────────────────────────────────────────
    # 8. Multi-replica (≥3) → HPA
    # ─────────────────────────────────────────────────────────────────────────
    # WHY: Multi-replica deployments typically have variable load and benefit
    # from HPA's dynamic scaling.
    if analysis.replicas >= 3:
        return ScalingApproach.HPA

    # ─────────────────────────────────────────────────────────────────────────
    # 9. Over-requested resources → VPA or MANUAL
    # ─────────────────────────────────────────────────────────────────────────
    # VPA can right-size over-provisioned resources automatically
    if IssueType.CPU_OVER_REQUESTED in analysis.issues or IssueType.MEM_OVER_REQUESTED in analysis.issues:
        if supports_in_place_vpa:
            return ScalingApproach.VPA
        else:
            return ScalingApproach.MANUAL

    # ─────────────────────────────────────────────────────────────────────────
    # 10. Default → MANUAL
    # ─────────────────────────────────────────────────────────────────────────
    return ScalingApproach.MANUAL


def _check_vpa_support(version: str) -> bool:
    """Check if Kubernetes version supports in-place VPA.

    In-place VPA (updateMode: Auto without pod restart) is available in K8s 1.33+.

    Args:
        version: Kubernetes version string (e.g., "1.33", "1.32.0")

    Returns:
        True if version >= 1.33, False otherwise
    """
    try:
        # Extract major.minor version
        # Handle formats like "1.33", "1.33.0", "v1.33.2"
        version_clean = version.strip().lower().replace("v", "")
        parts = version_clean.split(".")
        if len(parts) >= 2:
            major = int(parts[0])
            minor = int(parts[1])
            # In-place VPA available in 1.33+
            return major > 1 or (major == 1 and minor >= 33)
    except (ValueError, IndexError):
        # If parsing fails, assume no VPA support (conservative)
        pass
    return False
