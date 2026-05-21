"""Data models for deployment analysis and recommendations."""

from dataclasses import asdict, dataclass, field
from enum import Enum


class Priority(Enum):
    """Issue priority levels for resource optimization.

    P0: Blockers - must fix before HPA rollout (missing requests, confirmed OOM, CPU throttling)
    P1: High - fix during Phase 1 (OOM risk, unstable workloads)
    P2: Medium - fix during Phase 2-3 (under-requested, over-requested, RWO PVCs)
    P3: Low - optimize after rollout (minor inefficiencies)
    """

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class ScalingApproach(Enum):
    """Recommended scaling approach for each workload.

    HPA: Horizontal Pod Autoscaler (multi-replica, high usage)
    VPA: Vertical Pod Autoscaler (single replica, over-requested, or K8s 1.33+ with RWO PVCs)
    MANUAL: Manual resource updates only (no autoscaling)
    NONE: No scaling recommendations (excluded workload)
    HPA_AFTER_FIX: HPA after fixing P0/stability issues
    """

    HPA = "HPA"
    VPA = "VPA"
    MANUAL = "MANUAL"
    NONE = "NONE"
    HPA_AFTER_FIX = "HPA_AFTER_FIX"


class IssueType(Enum):
    """Types of resource issues detected.

    These issue flags drive priority classification and scaling decisions.
    """

    REQUESTS_NOT_SET = "REQUESTS_NOT_SET"  # P0: Missing resource requests
    CPU_THROTTLED = "CPU_THROTTLED"  # P0: CPU throttling detected
    RWO_PVC = "RWO_PVC"  # P2: ReadWriteOnce PVC (HPA conflict)
    UNSTABLE = "UNSTABLE"  # P0/P1: Frequent restarts
    OOM_KILLED = "OOM_KILLED"  # P0: Confirmed OOM kill
    MEM_SATURATION = "MEM_SATURATION"  # P1: High memory usage approaching limit
    CPU_OVER_REQUESTED = "CPU_OVER_REQUESTED"  # P2: <50% CPU usage (wasteful)
    CPU_UNDER_REQUESTED = "CPU_UNDER_REQUESTED"  # P2: >85% CPU usage (needs more)
    MEM_OVER_REQUESTED = "MEM_OVER_REQUESTED"  # P2: <50% memory usage (wasteful)
    MEM_UNDER_REQUESTED = "MEM_UNDER_REQUESTED"  # P2: >85% memory usage (needs more)
    NO_METRICS = "NO_METRICS"  # Missing metrics data
    SINGLE_REPLICA = "SINGLE_REPLICA"  # Info: Single replica deployment
    MISSING_CPU_LIMITS = "MISSING_CPU_LIMITS"  # P2: No CPU limits set
    MEMORY_HPA_CANDIDATE = "MEMORY_HPA_CANDIDATE"  # Rare: May benefit from memory-based HPA


@dataclass
class ResourceRecommendation:
    """Resource request and limit recommendations.

    Attributes:
        cpu_request: Recommended CPU request (e.g., "125m")
        cpu_limit: Recommended CPU limit (e.g., "250m")
        memory_request: Recommended memory request (e.g., "256Mi")
        memory_limit: Recommended memory limit (e.g., "512Mi")
        rationale: Human-readable explanation of recommendations
        requires_manual_action: Flag for immediate manual resource updates
    """

    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    rationale: str = ""
    requires_manual_action: bool = False


@dataclass
class DeploymentAnalysis:
    """Complete analysis for a single deployment or statefulset.

    This dataclass represents the full analysis pipeline:
    1. Raw metrics from CSV (kubectl + optional Prometheus)
    2. Detected issues
    3. Priority classification
    4. Scaling approach recommendation
    5. Resource recommendations

    Fields with defaults (at the end) are optional Prometheus metrics.
    """

    # ─────────────────────────────────────────────────────────────────────────
    # Workload identification (required)
    # ─────────────────────────────────────────────────────────────────────────
    cluster: str
    namespace: str
    workload_type: str  # "Deployment" or "StatefulSet"
    deployment: str
    replicas: int

    # ─────────────────────────────────────────────────────────────────────────
    # Current CPU state (required - from kubectl)
    # ─────────────────────────────────────────────────────────────────────────
    avg_cpu_usage_m: float  # Average CPU usage in millicores
    cpu_request_m: float  # Current CPU request in millicores
    cpu_limit_m: float  # Current CPU limit in millicores (0 if not set)
    cpu_usage_percent: float  # CPU usage as % of request

    # ─────────────────────────────────────────────────────────────────────────
    # Current memory state (required - from kubectl)
    # ─────────────────────────────────────────────────────────────────────────
    avg_mem_usage_mi: float  # Average memory usage in MiB
    mem_request_mi: float  # Current memory request in MiB
    mem_limit_mi: float  # Current memory limit in MiB (0 if not set)
    mem_usage_percent: float  # Memory usage as % of request

    # ─────────────────────────────────────────────────────────────────────────
    # Stability metrics (required - from kubectl)
    # ─────────────────────────────────────────────────────────────────────────
    total_restarts: int  # Total restarts across all pods
    max_pod_restarts: int  # Maximum restarts for any single pod
    pods_restarting: int  # Number of pods with restarts
    restart_reason: str  # Last restart reason (e.g., "OOMKilled", "Error")

    # ─────────────────────────────────────────────────────────────────────────
    # Storage information (required - from kubectl)
    # ─────────────────────────────────────────────────────────────────────────
    rwo_pvc: bool  # Has ReadWriteOnce PVC (HPA conflict)
    rwo_pvc_names: str  # Comma-separated list of RWO PVC names

    # ─────────────────────────────────────────────────────────────────────────
    # Analysis results (required)
    # ─────────────────────────────────────────────────────────────────────────
    priority: Priority

    # ─────────────────────────────────────────────────────────────────────────
    # Prometheus metrics (optional - defaults to N/A if unavailable)
    # ─────────────────────────────────────────────────────────────────────────
    cpu_p95_m: float = 0.0  # P95 CPU usage for dynamic limit calculation
    cpu_max_m: float = 0.0  # Max CPU usage for dynamic limit calculation
    mem_p95_mi: float = 0.0  # P95 memory usage for dynamic limit calculation
    mem_6h_stddev_mi: float = 0.0  # 6-hour memory standard deviation
    mem_coeff_var_percent: float = 0.0  # Coefficient of variation (%)
    mem_6h_min_mi: float = 0.0  # 6-hour memory minimum
    mem_6h_max_mi: float = 0.0  # 6-hour memory maximum
    mem_volatility: str = "N/A"  # HIGH/MODERATE/LOW/N/A (memory leak indicator)
    restart_rate_per_day: float = 0.0  # Restart rate (restarts/day)
    cluster_cpu_pressure_percent: float = 0.0  # Cluster-wide CPU pressure
    cluster_mem_pressure_percent: float = 0.0  # Cluster-wide memory pressure
    collection_window_hours: int = 6  # Time window for metrics collection

    # ─────────────────────────────────────────────────────────────────────────
    # Analysis outputs (populated by analyzer)
    # ─────────────────────────────────────────────────────────────────────────
    issues: list[IssueType] = field(default_factory=list)
    scaling_approach: ScalingApproach = ScalingApproach.MANUAL
    recommended_resources: ResourceRecommendation | None = None
    action_required: str = ""
    rationale: str = ""
    owner_team: str = "Unknown"  # Populated from namespace ownership data

    # ─────────────────────────────────────────────────────────────────────────
    # HPA predictions (calculated for HPA candidates)
    # ─────────────────────────────────────────────────────────────────────────
    predicted_replicas_cpu: int = 0  # Predicted replicas based on CPU
    predicted_replicas_mem: int = 0  # Predicted replicas based on memory
    final_predicted_replicas: int = 0  # Final replica prediction (max of above)

    # ─────────────────────────────────────────────────────────────────────────
    # Rollout planning (for phased HPA rollout)
    # ─────────────────────────────────────────────────────────────────────────
    rollout_phase: str = ""  # Phase 1, 2, or 3

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization.

        Enums are converted to their string values for JSON compatibility.
        """
        d = asdict(self)
        d["priority"] = self.priority.value
        d["issues"] = [issue.value for issue in self.issues]
        d["scaling_approach"] = self.scaling_approach.value
        return d

    @property
    def has_prometheus_metrics(self) -> bool:
        """Check if Prometheus metrics are available (not just defaults)."""
        return (
            self.cpu_p95_m > 0.0
            or self.mem_p95_mi > 0.0
            or self.restart_rate_per_day > 0.0
            or self.mem_volatility != "N/A"
        )

    @property
    def is_statefulset(self) -> bool:
        """Check if this is a StatefulSet."""
        return self.workload_type.lower() == "statefulset"

    @property
    def is_multi_replica(self) -> bool:
        """Check if this workload has multiple replicas."""
        return self.replicas > 1

    @property
    def has_cpu_throttling(self) -> bool:
        """Check if CPU throttling is detected."""
        return IssueType.CPU_THROTTLED in self.issues

    @property
    def has_oom_killed(self) -> bool:
        """Check if OOM kills are confirmed."""
        return IssueType.OOM_KILLED in self.issues

    @property
    def is_unstable(self) -> bool:
        """Check if workload is unstable (frequent restarts)."""
        return IssueType.UNSTABLE in self.issues

    @property
    def has_p0_issues(self) -> bool:
        """Check if workload has P0 (blocker) issues."""
        return self.priority == Priority.P0
