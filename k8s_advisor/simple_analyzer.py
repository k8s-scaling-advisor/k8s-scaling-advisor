#!/usr/bin/env python3
"""
Enhanced self-contained analyzer for K8s resource optimization.
Handles both Prometheus and non-Prometheus data collection.
Works with namespace-scoped permissions (no cluster-admin required).
"""

import csv
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

# Import constants
try:
    from k8s_advisor.constants import (
        BASE_SCORE_NO_PROM,
        BASE_SCORE_PROMETHEUS,
        BURSTY_PENALTY,
        BURSTY_WORKLOAD_PATTERNS,
        CPU_LIMIT_POLICY_BURST,
        CPU_LIMIT_POLICY_PROTECT,
        CPU_MIN_RECOMMENDED_M,
        CPU_OVER_REQUESTED_THRESHOLD,
        CPU_REDUCTION_BASELINE_M,
        CPU_REDUCTION_MIN_SAVING_M,
        CPU_THROTTLE_P0_THRESHOLD_PCT,
        CPU_UNDER_REQUESTED_THRESHOLD,
        GC_PENALTY,
        GC_RUNTIME_PATTERNS,
        HEADROOM_MULTIPLIER,
        HEADROOM_MULTIPLIER_HIGH_VOLATILITY,
        HEADROOM_MULTIPLIER_MID_VOLATILITY,
        HIGH_CONF_THRESHOLD,
        HPA_TARGET_UTILIZATION_DEFAULT,
        INSUFFICIENT_DATA_SCORE,
        LIMITS_BONUS,
        LOW_CONFIDENCE_MAX_RESTARTS_PER_POD,
        LOW_CONFIDENCE_RESTART_RATE_PER_DAY,
        LOW_CONFIDENCE_TOTAL_RESTARTS,
        LOW_SIGNAL_CPU_M,
        LOW_SIGNAL_MEM_MI,
        MEDIUM_CONF_THRESHOLD,
        MEM_OVER_REQUESTED_THRESHOLD,
        MEM_SATURATION_LIMIT_THRESHOLD,
        MEM_UNDER_REQUESTED_THRESHOLD,
        MEMORY_VOLATILITY_HIGH_THRESHOLD,
        MEMORY_VOLATILITY_LOW_THRESHOLD,
        OWNER_LABEL_KEYS,
        READINESS_MAX_CV_FOR_REDUCTION,
        RECOMMENDATION_DEADBAND_PCT,
        REQUESTS_NOT_SET_P0_CPU_M,
        REQUESTS_NOT_SET_P0_MEM_MI,
        RESTART_PENALTY,
        RESTART_RATE_THRESHOLD,
        SINGLE_REPLICA_PENALTY,
        UNSTABLE_RESTART_RATE_THRESHOLD,
        UNSTABLE_RESTART_THRESHOLD,
        VPA_CONFIDENCE_BONUS,
        VPA_DISAGREEMENT_RATIO,
    )
except ImportError:
    # Fallback if constants not available
    CPU_OVER_REQUESTED_THRESHOLD = 50
    CPU_UNDER_REQUESTED_THRESHOLD = 85
    MEM_OVER_REQUESTED_THRESHOLD = 50
    MEM_UNDER_REQUESTED_THRESHOLD = 85
    UNSTABLE_RESTART_THRESHOLD = 5
    UNSTABLE_RESTART_RATE_THRESHOLD = 2.0
    MEM_SATURATION_LIMIT_THRESHOLD = 90
    HEADROOM_MULTIPLIER = 1.25
    HEADROOM_MULTIPLIER_MID_VOLATILITY = 1.5
    HEADROOM_MULTIPLIER_HIGH_VOLATILITY = 1.8
    CPU_MIN_RECOMMENDED_M = 50
    CPU_REDUCTION_BASELINE_M = 100
    CPU_REDUCTION_MIN_SAVING_M = 50
    CPU_THROTTLE_P0_THRESHOLD_PCT = 5
    CPU_LIMIT_POLICY_BURST = "burst"
    CPU_LIMIT_POLICY_PROTECT = "protect"
    RECOMMENDATION_DEADBAND_PCT = 10
    READINESS_MAX_CV_FOR_REDUCTION = 100
    MEMORY_VOLATILITY_LOW_THRESHOLD = 10
    MEMORY_VOLATILITY_HIGH_THRESHOLD = 20
    HPA_TARGET_UTILIZATION_DEFAULT = 75
    LOW_SIGNAL_CPU_M = 1.0
    LOW_SIGNAL_MEM_MI = 1.0
    LOW_CONFIDENCE_RESTART_RATE_PER_DAY = 1.0
    LOW_CONFIDENCE_MAX_RESTARTS_PER_POD = 5
    LOW_CONFIDENCE_TOTAL_RESTARTS = 50
    REQUESTS_NOT_SET_P0_CPU_M = 50
    REQUESTS_NOT_SET_P0_MEM_MI = 64
    OWNER_LABEL_KEYS = (
        "app.kubernetes.io/part-of",
        "owner",
        "team",
        "owner-team",
        "app.kubernetes.io/managed-by",
    )
    GC_RUNTIME_PATTERNS = set()
    BURSTY_WORKLOAD_PATTERNS = set()
    # Confidence-scoring fallbacks (must mirror constants.py defaults).
    INSUFFICIENT_DATA_SCORE = 0.10
    BASE_SCORE_PROMETHEUS = 0.85
    BASE_SCORE_NO_PROM = 0.55
    BURSTY_PENALTY = 0.20
    GC_PENALTY = 0.15
    RESTART_PENALTY = 0.15
    SINGLE_REPLICA_PENALTY = 0.05
    RESTART_RATE_THRESHOLD = 2.0
    LIMITS_BONUS = 0.05
    HIGH_CONF_THRESHOLD = 0.75
    MEDIUM_CONF_THRESHOLD = 0.50
    VPA_CONFIDENCE_BONUS = 0.10
    VPA_DISAGREEMENT_RATIO = 2.0

# Profile imports live below the constants fallback so the analyzer
# still loads if a partial install is missing constants.py — profiles.py
# imports from constants.py too, so it'd fail in that scenario anyway.
from k8s_advisor.profiles import DEFAULT_PROFILE, DEFAULT_PROFILE_SET, Profile, ProfileSet


class Priority(Enum):
    """Workload priority bucket. P0 = act now, P3 = no action needed."""

    P0 = "P0"  # Blockers
    P1 = "P1"  # High
    P2 = "P2"  # Medium
    P3 = "P3"  # Low


class ScalingApproach(Enum):
    """Recommended autoscaling strategy for a workload.

    HPA / VPA / MANUAL / NONE / HPA_AFTER_FIX (HPA candidate but blocked
    on a P0 issue or instability that must be fixed first).
    """

    HPA = "HPA"
    VPA = "VPA"
    MANUAL = "MANUAL"
    NONE = "NONE"
    HPA_AFTER_FIX = "HPA_AFTER_FIX"


@dataclass
class WorkloadAnalysis:
    """Analysis result for a single workload with comprehensive Prometheus metrics."""

    namespace: str
    deployment: str
    workload_type: str
    cluster: str
    priority: Priority
    scaling_approach: ScalingApproach
    issues: list[str]
    recommendations: list[str]
    current_cpu: str
    current_mem: str
    recommended_cpu: str
    recommended_mem: str
    rationale: str = ""
    action_required: str = ""

    # Prometheus metrics for rich analysis
    cpu_p50: float = 0.0
    cpu_p95: float = 0.0
    cpu_max: float = 0.0
    cpu_stddev: float = 0.0
    cpu_throttle_pct: float = 0.0

    mem_p50: float = 0.0
    mem_p95: float = 0.0
    mem_max: float = 0.0
    mem_stddev: float = 0.0
    mem_volatility_cv: float = 0.0

    replicas: int = 1
    oom_kills: int = 0
    total_restarts: int = 0
    restart_rate: float = 0.0
    max_restarts_per_pod: int = 0

    # Calculated values
    avg_cpu: float = 0.0
    avg_mem: float = 0.0
    cpu_request: float = 0.0
    cpu_limit: float = 0.0
    mem_request: float = 0.0
    mem_limit: float = 0.0

    requires_manual_action: bool = False

    pod_count: int = 0
    last_restart_reason: str = ""
    last_restart_exit_code: str = ""
    crash_signal: str = ""
    key_labels: str = ""
    owner: str = ""

    # VPA recommendation, when a VerticalPodAutoscaler targets this workload.
    # Summed across containers (comparable to cpu_request / mem_request).
    # Zero when no VPA signal is present.
    vpa_present: bool = False
    vpa_cpu_target: float = 0.0
    vpa_mem_target: float = 0.0
    vpa_mem_upper: float = 0.0
    # Which signal drove the numeric recommendation base: "vpa" | "prometheus"
    # | "metrics-server". Surfaced in the rationale so the number is auditable.
    rec_basis: str = "metrics-server"

    # Confidence / data-quality flags
    low_confidence: bool = False
    insufficient_data: bool = False
    gc_runtime: bool = False
    bursty_workload: bool = False

    # Numeric confidence score in [0.0, 1.0] for the recommendation
    # itself. Computed from the observable-evidence signals (Prometheus
    # availability, restart noise, runtime shape, etc.) — see
    # _confidence_score(). Useful for downstream gating ("only act on
    # recommendations >= 0.7").
    confidence: float = 0.0
    # Human-readable rollup ("high" / "medium" / "low") derived from
    # `confidence`. Convenience for the markdown report.
    confidence_band: str = "low"
    # Short human-readable strings describing what dragged confidence
    # down (or up). Surfaces in the markdown rationale.
    confidence_reasons: list = field(default_factory=list)

    # Quantified per-workload deltas, signed in millicores / Mi.
    # Positive = recommended request is below current (savings).
    # Negative = recommended request raises the floor (more capacity needed).
    # Zero    = no recommendation issued.
    cpu_delta_m: float = 0.0
    mem_delta_mi: float = 0.0

    # Idempotency / "have we seen this recommendation before" tracking.
    # Populated only when the analyzer is invoked with `--state-dir`;
    # otherwise stays at the dataclass defaults so behavior is unchanged.
    fingerprint: str = ""  # stable hash over (ns, deployment, priority, scaling, rec_cpu, rec_mem)
    previously_seen: bool = False  # True iff fingerprint was in prior state
    times_seen: int = 1  # how many runs have produced this exact recommendation
    first_seen: str = ""  # ISO-8601 timestamp of first observation

    # Name of the per-namespace profile that drove this row's numbers.
    # "default" when no --profiles flag was passed, or when the namespace
    # has no override in the policy file.
    policy_name: str = "default"


def safe_float(value, default=0.0):
    """Safely convert to float."""
    if not value or value in ("N/A", "", "-"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    """Safely convert to int."""
    if not value or value in ("N/A", "", "-"):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _confidence_score(
    *,
    has_prometheus: bool,
    insufficient_data: bool,
    gc_runtime: bool,
    bursty_workload: bool,
    restart_rate: float,
    pod_count: int,
    has_cpu_limit: bool,
    has_mem_limit: bool,
    vpa_present: bool = False,
) -> tuple:
    """Score the recommendation's data-quality from 0.0 to 1.0.

    The score is heuristic, not statistical — it rolls up the same
    boolean signals the analyzer already computes into a single number
    plus a list of human-readable reasons. Downstream tooling can use
    the number for gating ("auto-act on >= 0.8") and the reasons for
    UI / report rendering.

    Returns ``(score, band, reasons)`` where ``band`` is one of
    "high" / "medium" / "low".
    """
    reasons: list = []
    if insufficient_data:
        # Floor: when the data itself is missing/misleading no scoring
        # heuristic recovers it.
        return (INSUFFICIENT_DATA_SCORE, "low", ["INSUFFICIENT_DATA — no usable signal"])

    score = BASE_SCORE_PROMETHEUS if has_prometheus else BASE_SCORE_NO_PROM
    reasons.append("Prometheus metrics available" if has_prometheus else "kubectl-only metrics-server data")

    if bursty_workload:
        score -= BURSTY_PENALTY
        reasons.append("bursty runtime — peaks under-represented")
    if gc_runtime:
        score -= GC_PENALTY
        reasons.append("GC-managed runtime — memory shape distorted")
    if restart_rate > RESTART_RATE_THRESHOLD:
        score -= RESTART_PENALTY
        reasons.append(f"restart rate {restart_rate:.1f}/day — metrics include crash-loop noise")
    if pod_count <= 1:
        score -= SINGLE_REPLICA_PENALTY
        reasons.append("single replica — no peer averaging")
    if has_cpu_limit and has_mem_limit:
        score += LIMITS_BONUS
        reasons.append("CPU + memory limits set — saturation observable")
    if vpa_present:
        # A live VPA recommendation is a controller-computed target built from
        # long-horizon usage + OOM history — a strong right-sizing signal.
        score += VPA_CONFIDENCE_BONUS
        reasons.append("VPA recommendation available — controller-computed target")

    score = max(0.0, min(1.0, score))
    if score >= HIGH_CONF_THRESHOLD:
        band = "high"
    elif score >= MEDIUM_CONF_THRESHOLD:
        band = "medium"
    else:
        band = "low"
    return (round(score, 2), band, reasons)


def build_rationale(analysis: WorkloadAnalysis, has_prometheus: bool) -> str:
    """Build comprehensive rationale explaining recommendations using Prometheus data."""
    parts = []

    # State the sizing basis up front so the number is auditable. VPA is a
    # controller-computed target; the other two are our own estimates.
    if analysis.rec_basis == "vpa":
        parts.append(
            f"Sizing basis: VerticalPodAutoscaler target "
            f"(CPU {analysis.vpa_cpu_target:.0f}m, mem {analysis.vpa_mem_target:.0f}Mi) "
            f"— preferred over local estimate"
        )
    elif analysis.rec_basis == "prometheus":
        parts.append("Sizing basis: Prometheus P95 + headroom")

    # P0 Issues
    if "REQUESTS_NOT_SET" in analysis.issues:
        parts.append("Resource requests not set - HPA cannot function, scheduler impact")

    if "OOM_KILLED" in analysis.issues:
        parts.append(f"Confirmed OOM kills ({analysis.oom_kills}x) - increase limits immediately")

    if "CPU_THROTTLED" in analysis.issues and has_prometheus:
        parts.append(f"Active CPU throttling ({analysis.cpu_throttle_pct:.1f}%) - causing latency")

    # P1 Issues
    if "UNSTABLE" in analysis.issues:
        # Pick the signal that actually triggered the flag — never claim
        # "0 restarts" when something else (rate, max-per-pod) tripped it.
        if analysis.total_restarts > 0:
            parts.append(f"Unstable ({analysis.total_restarts} restarts) - fix before HPA")
        elif analysis.restart_rate > 0:
            parts.append(f"Unstable ({analysis.restart_rate:.1f}/day restart rate) - fix before HPA")
        elif analysis.max_restarts_per_pod > 0:
            parts.append(f"Unstable ({analysis.max_restarts_per_pod} restarts on a single pod) - fix before HPA")
        else:
            parts.append("Unstable workload signal - fix before HPA")

    if "MEM_SATURATION" in analysis.issues:
        parts.append("High memory saturation - approaching OOM")

    # Resource efficiency with Prometheus context
    if "CPU_OVER_REQUESTED" in analysis.issues:
        if has_prometheus and analysis.cpu_p95 > 0:
            parts.append(
                f"CPU over-requested (avg: {analysis.avg_cpu:.0f}m, P95: {analysis.cpu_p95:.0f}m, request: {analysis.cpu_request:.0f}m)"
            )
        else:
            parts.append(f"CPU over-requested ({(analysis.avg_cpu / analysis.cpu_request * 100):.0f}% usage)")

    if "CPU_UNDER_REQUESTED" in analysis.issues:
        if has_prometheus and analysis.cpu_p95 > 0:
            parts.append(
                f"CPU under-requested (avg: {analysis.avg_cpu:.0f}m, P95: {analysis.cpu_p95:.0f}m, request: {analysis.cpu_request:.0f}m)"
            )
        else:
            parts.append(f"CPU under-requested ({(analysis.avg_cpu / analysis.cpu_request * 100):.0f}% usage)")

    if "MEM_OVER_REQUESTED" in analysis.issues:
        if has_prometheus and analysis.mem_p95 > 0:
            parts.append(
                f"Memory over-requested (avg: {analysis.avg_mem:.0f}Mi, P95: {analysis.mem_p95:.0f}Mi, request: {analysis.mem_request:.0f}Mi)"
            )
        else:
            parts.append(f"Memory over-requested ({(analysis.avg_mem / analysis.mem_request * 100):.0f}% usage)")

    if "MEM_UNDER_REQUESTED" in analysis.issues:
        if has_prometheus and analysis.mem_p95 > 0:
            parts.append(
                f"Memory under-requested (avg: {analysis.avg_mem:.0f}Mi, P95: {analysis.mem_p95:.0f}Mi, request: {analysis.mem_request:.0f}Mi)"
            )
        else:
            parts.append(f"Memory under-requested ({(analysis.avg_mem / analysis.mem_request * 100):.0f}% usage)")

    # Scaling approach context
    if analysis.scaling_approach == ScalingApproach.HPA:
        parts.append("Good HPA candidate - multi-replica, variable load")
    elif analysis.scaling_approach == ScalingApproach.VPA:
        if analysis.workload_type == "StatefulSet":
            parts.append("StatefulSet - VPA recommended over HPA")
        else:
            parts.append("VPA recommended - single replica or stable load")
    elif analysis.scaling_approach == ScalingApproach.HPA_AFTER_FIX:
        parts.append("HPA candidate after resolving blockers")

    # Memory volatility insights (Prometheus only)
    if has_prometheus and analysis.mem_volatility_cv > 0:
        if analysis.mem_volatility_cv > MEMORY_VOLATILITY_HIGH_THRESHOLD:
            parts.append(f"HIGH memory volatility (CV={analysis.mem_volatility_cv:.1f}%) - investigate memory leak")
        elif analysis.mem_volatility_cv > MEMORY_VOLATILITY_LOW_THRESHOLD:
            parts.append(f"Moderate memory volatility (CV={analysis.mem_volatility_cv:.1f}%)")
        else:
            parts.append(f"Memory stable (CV={analysis.mem_volatility_cv:.1f}%) - confirms CPU-based HPA")

    # RWO PVC
    if "RWO_PVC" in analysis.issues:
        parts.append("RWO PVCs prevent horizontal scaling - VPA only")

    return "; ".join(parts) if parts else "No significant issues detected"


def _parse_labels(key_labels_field: str) -> dict[str, str]:
    """Parse the Key_Labels CSV column into a {key: value} dict.

    Supports both the new JSON-serialized format ({"team":"foo"}) and the
    legacy comma-separated format (team=foo,app=bar). The JSON form is
    warehouse-loadable; the legacy form is preserved so older CSVs still
    analyze.
    """
    import json as _json

    if not key_labels_field:
        return {}
    s = key_labels_field.strip()
    if s.startswith("{") and s.endswith("}"):
        try:
            parsed = _json.loads(s)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except (ValueError, TypeError):
            pass
    # Legacy: a=b,c=d
    out: dict[str, str] = {}
    for chunk in s.split(","):
        if "=" in chunk:
            k, _, v = chunk.partition("=")
            k = k.strip()
            if k:
                out[k] = v.strip()
    return out


def _extract_owner(labels: dict[str, str]) -> str:
    """Pick the first non-empty value from the OWNER_LABEL_KEYS allowlist."""
    for key in OWNER_LABEL_KEYS:
        val = labels.get(key)
        if val:
            return val
    return ""


# Exit-code interpretation table. Sources:
#   - POSIX: 128+N for signal N (137=SIGKILL, 139=SIGSEGV, 143=SIGTERM, etc.)
#   - kubelet: 137 is the canonical OOMKilled signal
#   - Docker/runc: 125, 126, 127 are container start failures
#   - General: 0 clean, 1/2 app error
_EXIT_CODE_HINTS = {
    0: "clean exit",
    1: "app error / unhandled exception",
    2: "app error",
    125: "container failed to start (image / runtime error)",
    126: "container command not executable",
    127: "container command not found",
    130: "SIGINT (Ctrl+C)",
    134: "SIGABRT — assertion failure / abort()",
    137: "SIGKILL — usually OOMKilled or liveness-probe-failed",
    139: "SIGSEGV — segfault / native crash",
    143: "SIGTERM — graceful shutdown",
    255: "fatal exit / unhandled panic",
}


def _diverges(a: float, b: float, ratio: float) -> bool:
    """True if a and b differ by more than `ratio`x in either direction.

    Used to flag VPA-vs-Prometheus disagreement. Guards against zero so a
    missing signal never reports a spurious divergence.
    """
    if a <= 0 or b <= 0:
        return False
    hi, lo = (a, b) if a >= b else (b, a)
    return hi > lo * ratio


def _crash_signal(reason: str, exit_code) -> str:
    """Return a short human-readable crash signal, or '' when nothing useful.

    Combines K8s `last_state.terminated.reason` with the container's exit
    code to give the developer something to grep for. Examples:
      reason='OOMKilled', exit_code=137 → 'OOMKilled (exit 137)'
      reason='Error', exit_code=1       → 'Error (exit 1 — app error / unhandled exception)'
      reason='Completed', exit_code=0   → 'Completed (exit 0 — clean exit)'
      reason='', exit_code=None         → ''
    """
    reason = (reason or "").strip()
    # Normalize exit_code (CSV stores 'N/A' or stringified int).
    if exit_code in (None, "", "N/A"):
        ec_int = None
    else:
        try:
            ec_int = int(exit_code)
        except (TypeError, ValueError):
            ec_int = None

    if not reason and ec_int is None:
        return ""
    if not reason:
        reason = "unknown"

    if ec_int is None:
        return reason
    hint = _EXIT_CODE_HINTS.get(ec_int)
    if hint:
        return f"{reason} (exit {ec_int} — {hint})"
    return f"{reason} (exit {ec_int})"


def _is_gc_runtime(deployment: str, key_labels: str) -> bool:
    """Detect GC-bound runtimes (JVM, Node.js) where memory rightsizing is unsafe.

    Memory request must never be reduced for these workloads — GC runtimes
    retain heap pages and `avg_mem` underestimates working-set size.
    """
    haystack = f"{deployment} {key_labels}".lower()
    return any(pattern.lower() in haystack for pattern in GC_RUNTIME_PATTERNS)


def _is_bursty_workload(deployment: str, key_labels: str) -> bool:
    """Detect bursty workload classes (Prometheus, Cassandra, Kafka, ES, etc.)
    where avg/P95 do not represent peak working-set memory.

    These workloads have heavy-tailed memory profiles — WAL replay, query
    bursts, compactions, shard rebalances — that any avg-based or even
    P95-based rec will under-size. Treat the same as GC_RUNTIME for memory
    reductions: skip with an explicit manual-review note.
    """
    haystack = f"{deployment} {key_labels}".lower()
    return any(pattern.lower() in haystack for pattern in BURSTY_WORKLOAD_PATTERNS)


def _is_restart_zombie(restart_rate: float, max_restarts_per_pod: int, total_restarts: int) -> bool:
    """Detect workloads in CrashLoop / restart-leak state.

    These pods produce misleading metrics — a service crashing every 30s
    reports an `avg_mem` that reflects only the early lifecycle of the
    container, not its steady-state working set. Numeric recommendations
    against this can deepen the outage (e.g. trimming memory request on a
    pod whose true working set we never observed).
    """
    return (
        restart_rate > LOW_CONFIDENCE_RESTART_RATE_PER_DAY
        or max_restarts_per_pod > LOW_CONFIDENCE_MAX_RESTARTS_PER_POD
        or total_restarts > LOW_CONFIDENCE_TOTAL_RESTARTS
    )


def _has_signal(avg_cpu: float, avg_mem: float, pod_count: int, total_restarts: int) -> bool:
    """Conservative signal check for kubectl-only mode.

    Returns False (no signal) when:
      - Avg CPU and memory are effectively zero, AND
      - The pod is restarting OR no pods are reporting metrics.

    A workload that has been running stably with extremely low usage
    (idle but healthy) still passes this check as long as pod_count > 0
    and there are no active restarts.
    """
    no_usage = avg_cpu <= LOW_SIGNAL_CPU_M and avg_mem <= LOW_SIGNAL_MEM_MI
    looks_dead = total_restarts > 0 or pod_count == 0
    return not (no_usage and looks_dead)


def _has_prometheus_signal(avg_cpu: float, avg_mem: float, cpu_p95: float, mem_p95: float) -> bool:
    """Signal check for Prometheus mode.

    Catches workloads that returned `avg=0` AND empty P95 columns —
    typically event-driven services that were idle for the entire
    7-day window. In this state the analyzer has nothing to size
    against; numeric recommendations are dangerous.

    Returns False (no signal) when both avg metrics are at-or-below the
    LOW_SIGNAL floor AND both P95 columns are also zero/missing.
    """
    no_avg = avg_cpu <= LOW_SIGNAL_CPU_M and avg_mem <= LOW_SIGNAL_MEM_MI
    no_p95 = cpu_p95 <= 0 and mem_p95 <= 0
    return not (no_avg and no_p95)


def analyze_workload(
    row: dict,
    has_prometheus: bool,
    *,
    profile: Profile = DEFAULT_PROFILE,
) -> WorkloadAnalysis:
    """Analyze a single workload from CSV row with comprehensive Prometheus metrics.

    Args:
        row: Parsed CSV row from the collect phase.
        has_prometheus: Whether the source CSV has Prometheus columns populated.
        profile: Per-namespace policy knobs (headroom, guardrail floors,
            efficiency thresholds). Defaults to ``DEFAULT_PROFILE`` which
            mirrors ``constants.py`` so existing callers see no behavior
            change.
    """

    # Parse basic data
    namespace = row.get("Namespace", "")
    deployment = row.get("Deployment", "")
    workload_type = row.get("Workload_Type", "Deployment")
    cluster = row.get("Cluster", "")

    # Parse metrics
    avg_cpu = safe_float(row.get("Avg_CPU_Usage(m)", 0))
    cpu_request = safe_float(row.get("CPU_Request(m)", 0))
    cpu_limit = safe_float(row.get("CPU_Limit(m)", 0))

    avg_mem = safe_float(row.get("Avg_Mem_Usage(Mi)", 0))
    mem_request = safe_float(row.get("Mem_Request(Mi)", 0))
    mem_limit = safe_float(row.get("Mem_Limit(Mi)", 0))

    # Parse Prometheus metrics
    cpu_p50 = safe_float(row.get("CPU_P50(m)", 0))
    cpu_p95 = safe_float(row.get("CPU_P95(m)", 0))
    cpu_max = safe_float(row.get("CPU_Max(m)", 0))
    cpu_stddev = safe_float(row.get("CPU_StdDev(m)", 0))
    cpu_throttle = safe_float(row.get("CPU_Throttle_Pct", 0))

    mem_p50 = safe_float(row.get("Mem_P50(Mi)", 0))
    mem_p95 = safe_float(row.get("Mem_P95(Mi)", 0))
    mem_max = safe_float(row.get("Mem_Max(Mi)", 0))
    mem_stddev = safe_float(row.get("Mem_StdDev(Mi)", 0))
    mem_cv = safe_float(row.get("Mem_Volatility_CV", 0))

    # VPA recommendation (optional input). Present iff a VerticalPodAutoscaler
    # targets this workload and has produced a recommendation. VPA_Present
    # gates use of the numbers so a zero target (no signal) can't be mistaken
    # for a legitimate "size to zero".
    vpa_present = (row.get("VPA_Present") or "").strip().lower() == "true"
    vpa_cpu_target = safe_float(row.get("VPA_CPU_Target(m)", 0))
    vpa_mem_target = safe_float(row.get("VPA_Mem_Target(Mi)", 0))
    vpa_mem_upper = safe_float(row.get("VPA_Mem_Upper(Mi)", 0))
    has_vpa_cpu = vpa_present and vpa_cpu_target > 0
    has_vpa_mem = vpa_present and vpa_mem_target > 0

    oom_kills = safe_int(row.get("OOMKilled_Count", 0))
    total_restarts = safe_int(row.get("Total_Restarts", 0))
    restart_rate = safe_float(row.get("Restart_Rate_Per_Day", 0))
    max_restarts_per_pod = safe_int(row.get("Max_Restarts_Per_Pod", 0))

    replicas = safe_int(row.get("Replicas", 1))
    pod_count = safe_int(row.get("Pod_Count", 0))
    last_restart_reason = (row.get("LastRestart_Reason") or "").strip()
    last_restart_exit_code = (row.get("LastRestart_ExitCode") or "").strip()
    crash_signal = _crash_signal(last_restart_reason, last_restart_exit_code)
    key_labels = row.get("Key_Labels", "") or ""
    parsed_labels = _parse_labels(key_labels)
    owner = _extract_owner(parsed_labels)
    pvc_mode = row.get("PVC_Access_Mode", "")
    is_statefulset = workload_type == "StatefulSet"

    # Confidence flags.
    #   - dead_pod_signal:    kubectl-only run + zero usage + dead/idle pod
    #   - prom_zero_signal:   Prometheus mode but both avg AND P95 are zero
    #                         (event-driven workload idle for the full window)
    #   - restart_zombie:     high restart count regardless of mode
    #                         (Prometheus can't see through CrashLoop either;
    #                         metrics are misleading)
    dead_pod_signal = not has_prometheus and not _has_signal(avg_cpu, avg_mem, pod_count, total_restarts)
    prom_zero_signal = has_prometheus and not _has_prometheus_signal(avg_cpu, avg_mem, cpu_p95, mem_p95)
    restart_zombie = _is_restart_zombie(restart_rate, max_restarts_per_pod, total_restarts)
    insufficient_data = dead_pod_signal or prom_zero_signal or restart_zombie
    gc_runtime = _is_gc_runtime(deployment, key_labels)
    bursty_workload = _is_bursty_workload(deployment, key_labels)
    low_confidence = insufficient_data or (not has_prometheus)

    # Detect issues
    issues = []
    priority = Priority.P3

    if insufficient_data:
        issues.append("INSUFFICIENT_DATA")

    # P0 issues
    if cpu_request == 0 or mem_request == 0:
        issues.append("REQUESTS_NOT_SET")
        # REQUESTS_NOT_SET is only P0 when the workload looks active or
        # unstable. An idle, stable workload missing requests is a hygiene
        # issue (P2), not a fire-now blocker — auto-promoting these to P0
        # buries real OOMs/throttles under noise.
        active_or_unstable = (
            avg_cpu > REQUESTS_NOT_SET_P0_CPU_M
            or avg_mem > REQUESTS_NOT_SET_P0_MEM_MI
            or total_restarts > 0
            or restart_rate > 0
            or oom_kills > 0
        )
        if active_or_unstable:
            priority = Priority.P0
        elif priority.value > Priority.P2.value:
            priority = Priority.P2

    if oom_kills > 0:
        issues.append("OOM_KILLED")
        priority = Priority.P0

    if has_prometheus and cpu_throttle > CPU_THROTTLE_P0_THRESHOLD_PCT:
        issues.append("CPU_THROTTLED")
        priority = Priority.P0

    # P1 issues
    if restart_rate > UNSTABLE_RESTART_RATE_THRESHOLD or (
        restart_rate == 0 and total_restarts > UNSTABLE_RESTART_THRESHOLD
    ):
        issues.append("UNSTABLE")
        if priority.value > Priority.P1.value:
            priority = Priority.P1

    if mem_limit > 0 and avg_mem > mem_limit * (MEM_SATURATION_LIMIT_THRESHOLD / 100):
        issues.append("MEM_SATURATION")
        if priority.value > Priority.P1.value:
            priority = Priority.P1

    # P2 issues - use P95 for better assessment if available
    effective_cpu = cpu_p95 if has_prometheus and cpu_p95 > 0 else avg_cpu
    effective_mem = mem_p95 if has_prometheus and mem_p95 > 0 else avg_mem

    if cpu_request > 0:
        if effective_cpu > cpu_request * (profile.cpu_under_pct / 100) and effective_cpu < cpu_request * 2.0:
            issues.append("CPU_UNDER_REQUESTED")
            if priority.value > Priority.P2.value:
                priority = Priority.P2
        elif effective_cpu < cpu_request * (profile.cpu_over_pct / 100):
            issues.append("CPU_OVER_REQUESTED")
            if priority.value > Priority.P2.value:
                priority = Priority.P2

    if mem_request > 0:
        if effective_mem > mem_request * (profile.mem_under_pct / 100) and effective_mem < mem_request * 2.0:
            issues.append("MEM_UNDER_REQUESTED")
            if priority.value > Priority.P2.value:
                priority = Priority.P2
        elif effective_mem < mem_request * (profile.mem_over_pct / 100):
            issues.append("MEM_OVER_REQUESTED")
            if priority.value > Priority.P2.value:
                priority = Priority.P2

    if "ReadWriteOnce" in pvc_mode:
        issues.append("RWO_PVC")
        if priority.value > Priority.P2.value:
            priority = Priority.P2

    # Generate recommendations
    recommendations = []
    actions = []

    # Signed deltas — positive means savings, negative means raise required.
    # Used by the Top-10 leaderboard and namespace rollups.
    cpu_delta_m = 0.0
    mem_delta_mi = 0.0

    # Recommendation basis, in precedence order:
    #   1. VPA target  — a controller-computed right-sizing number. Preferred
    #      because it is purpose-built for this and already accounts for
    #      historical usage + OOM. A VPA *target* is headroom-inclusive, so we
    #      do NOT re-apply our cpu_headroom/mem_headroom on top (that would
    #      double-count headroom); we use it directly, floored by guardrails.
    #   2. Prometheus P95 — our own estimate; gets the headroom multiplier.
    #   3. metrics-server avg — coarsest fallback; gets the headroom multiplier.
    if has_vpa_cpu:
        rec_cpu_req = max(profile.min_cpu_request_m, vpa_cpu_target)
        rec_basis = "vpa"
    elif has_prometheus and cpu_p95 > 0:
        rec_cpu_req = max(profile.min_cpu_request_m, cpu_p95 * profile.cpu_headroom)
        rec_basis = "prometheus"
    else:
        rec_cpu_req = max(profile.min_cpu_request_m, avg_cpu * profile.cpu_headroom)
        rec_basis = "metrics-server"

    # `rec_mem_base` retained for the P95/avg path below; VPA overrides it.
    rec_mem_base = mem_p95 if (has_prometheus and mem_p95 > 0) else avg_mem

    # Memory headroom is volatility-aware: a 50%-CV workload sized at
    # P95 × 1.25 will OOM during the next swing. CV is only meaningful in
    # Prometheus mode; kubectl-only mode uses the profile baseline. For
    # the volatility step-ups we take max(profile, hardcoded floor) so a
    # tight profile (e.g. mem_headroom=1.10 for batch) still snaps up to
    # the safe absolute multiplier when CV is high — under-sizing a
    # leaky workload is the costlier failure mode.
    high_volatility = False
    if has_prometheus and mem_cv >= MEMORY_VOLATILITY_HIGH_THRESHOLD:
        mem_headroom = max(profile.mem_headroom, HEADROOM_MULTIPLIER_HIGH_VOLATILITY)
        high_volatility = True
    elif has_prometheus and mem_cv >= MEMORY_VOLATILITY_LOW_THRESHOLD:
        mem_headroom = max(profile.mem_headroom, HEADROOM_MULTIPLIER_MID_VOLATILITY)
    else:
        mem_headroom = profile.mem_headroom

    if has_vpa_mem:
        # VPA memory target is headroom-inclusive → use directly. But never let
        # it size a high-volatility workload below what our volatility-aware
        # estimate would demand — under-sizing a leaky/bursty workload OOMs it,
        # the costlier failure. The volatility floor is the guardrail; VPA
        # otherwise wins even when it is *lower* than our P95 estimate (that is
        # the whole point of trusting the controller).
        volatility_floor = rec_mem_base * mem_headroom if high_volatility else 0.0
        rec_mem_req = max(profile.min_mem_request_mi, vpa_mem_target, volatility_floor)
    else:
        rec_mem_req = max(profile.min_mem_request_mi, rec_mem_base * mem_headroom)

    if high_volatility:
        issues.append("HIGH_VOLATILITY")

    if insufficient_data:
        # Tool refuses to issue numeric advice when metrics are unreliable.
        if restart_zombie:
            # Build the most specific signal string we can.
            signals = []
            if total_restarts > 0:
                signals.append(f"{total_restarts} restarts")
            if max_restarts_per_pod > 0:
                signals.append(f"{max_restarts_per_pod}/pod")
            if restart_rate > 0:
                signals.append(f"{restart_rate:.1f}/day")
            signal_str = ", ".join(signals) if signals else "high restart count"
            actions.append(
                f"INSUFFICIENT_DATA: workload is in CrashLoop / restart-leak "
                f"state ({signal_str}) — avg/P95 metrics reflect lifecycle, "
                f"not working set. Investigate restart cause "
                f"(LastRestart_Reason: {last_restart_reason or 'unknown'}) "
                f"before any rightsizing"
            )
        elif prom_zero_signal:
            actions.append(
                "INSUFFICIENT_DATA: workload returned zero usage AND empty "
                "P95 across the full 7-day window — likely event-driven, "
                "idle, or scaled-to-zero. No reliable signal to size "
                "against. Re-run after observing real traffic, or annotate "
                "with a workload-class hint if this is intentional"
            )
        else:
            actions.append(
                "INSUFFICIENT_DATA: pod reported zero usage during collection "
                "— re-run after the workload has been steady for ≥15min, or "
                "use Prometheus mode for historical data"
            )
    else:
        # Suppress *request rightsizing* on UNSTABLE workloads — telling an
        # admin to trim resources on a flapping pod is self-contradictory.
        # Safety-critical *limit* raises (OOM, throttle) still run below,
        # because those are root-cause fixes, not optimization.
        suppress_request_rightsizing = "UNSTABLE" in issues
        if suppress_request_rightsizing:
            actions.append(
                "UNSTABLE: numeric request rightsizing suppressed — fix "
                "instability first (see Issues), then re-run advisor for "
                "validated request numbers. Limit raises below remain in "
                "effect (root-cause fixes for OOM / throttling)."
            )

        # Readiness gate (#2): a workload whose memory usage swings wildly
        # (CV above the readiness cut) is too spiky to size *down* reliably —
        # trimming toward a mean it routinely blows past causes OOM. Suppress
        # numeric *reductions* only; safety raises still fire. CV is
        # Prometheus-only and a VPA target already accounts for volatility, so
        # the gate is skipped when VPA drove the numbers.
        too_spiky_to_reduce = has_prometheus and rec_basis != "vpa" and mem_cv > READINESS_MAX_CV_FOR_REDUCTION
        if too_spiky_to_reduce:
            actions.append(
                f"Memory reduction suppressed — usage is too volatile to size "
                f"down reliably (CV {mem_cv:.0f}% > {READINESS_MAX_CV_FOR_REDUCTION}%). "
                f"Sizing toward the mean would risk OOM on the next swing. "
                f"Collect a longer window or run VPA for a controller-computed "
                f"target before trimming."
            )

        def _below_deadband(current: float, proposed: float) -> bool:
            """Deadband (#4): True when the change is too small to be worth a
            rollout. VPA-driven numbers bypass the deadband — the controller's
            target is authoritative even for small moves."""
            if rec_basis == "vpa" or current <= 0:
                return False
            return abs(proposed - current) < current * (RECOMMENDATION_DEADBAND_PCT / 100)

        # CPU recommendations (request)
        if not suppress_request_rightsizing:
            if cpu_request == 0:
                recommendations.append(f"Set CPU request: {format_cpu(rec_cpu_req)}")
                actions.append(f"Set CPU REQUEST to {format_cpu(rec_cpu_req)} (currently unset)")
                # Track as a raise so Pattern Groups & namespace rollups
                # surface the additional capacity needed.
                cpu_delta_m = -rec_cpu_req
            elif rec_basis == "vpa":
                # VPA is authoritative: follow its target directly rather than
                # our P95-vs-request classification, which can land a workload
                # in the dead zone between the over/under gates and silently
                # drop VPA's recommendation. Deadband still applies (bypassed
                # inside _below_deadband when rec_basis == "vpa", so any real
                # VPA delta is surfaced).
                if rec_cpu_req > cpu_request and not _below_deadband(cpu_request, rec_cpu_req):
                    recommendations.append(f"Increase CPU request to {format_cpu(rec_cpu_req)}")
                    actions.append(
                        f"Raise CPU REQUEST from {format_cpu(cpu_request)} → {format_cpu(rec_cpu_req)} (VPA target)"
                    )
                    cpu_delta_m = cpu_request - rec_cpu_req  # negative — needs more
                elif rec_cpu_req < cpu_request and not _below_deadband(cpu_request, rec_cpu_req):
                    recommendations.append(f"Reduce CPU request to {format_cpu(rec_cpu_req)}")
                    actions.append(
                        f"Reduce CPU REQUEST from {format_cpu(cpu_request)} → {format_cpu(rec_cpu_req)} "
                        f"(VPA target, saves {format_cpu(cpu_request - rec_cpu_req)})"
                    )
                    cpu_delta_m = cpu_request - rec_cpu_req  # positive — savings
            elif effective_cpu > cpu_request * (profile.cpu_under_pct / 100):
                if rec_cpu_req > cpu_request and not _below_deadband(cpu_request, rec_cpu_req):
                    recommendations.append(f"Increase CPU request to {format_cpu(rec_cpu_req)}")
                    actions.append(f"Raise CPU REQUEST from {format_cpu(cpu_request)} → {format_cpu(rec_cpu_req)}")
                    cpu_delta_m = cpu_request - rec_cpu_req  # negative — needs more
            elif effective_cpu < cpu_request * (profile.cpu_over_pct / 100) and cpu_request > CPU_REDUCTION_BASELINE_M:
                savings = cpu_request - rec_cpu_req
                # Reduce only when it clears BOTH the absolute min-saving
                # guardrail AND the percentage deadband — a 51m saving on a
                # 2000m request is real millicores but a 2.5% move not worth
                # a rollout.
                if savings >= profile.min_cpu_saving_m and not _below_deadband(cpu_request, rec_cpu_req):
                    recommendations.append(f"Reduce CPU request to {format_cpu(rec_cpu_req)}")
                    actions.append(
                        f"Reduce CPU REQUEST from {format_cpu(cpu_request)} → {format_cpu(rec_cpu_req)} (saves {format_cpu(savings)})"
                    )
                    cpu_delta_m = savings  # positive — savings

            # Memory recommendations (request)
            if mem_request == 0:
                recommendations.append(f"Set memory request: {format_memory(rec_mem_req)}")
                actions.append(f"Set memory REQUEST to {format_memory(rec_mem_req)} (currently unset)")
                mem_delta_mi = -rec_mem_req
            elif rec_basis == "vpa":
                # VPA authoritative (see CPU note). A raise is always safe. A
                # *reduction* still honors the GC-runtime / bursty guards —
                # those are working-set-shape safety guards independent of the
                # signal source; even a VPA target shouldn't trim a known
                # heap-retaining runtime. (The CV readiness gate is bypassed
                # for VPA, since the controller already models volatility.)
                if rec_mem_req > mem_request and not _below_deadband(mem_request, rec_mem_req):
                    recommendations.append(f"Increase memory request to {format_memory(rec_mem_req)}")
                    actions.append(
                        f"Raise memory REQUEST from {format_memory(mem_request)} → {format_memory(rec_mem_req)} (VPA target)"
                    )
                    mem_delta_mi = mem_request - rec_mem_req  # negative
                elif rec_mem_req < mem_request and not _below_deadband(mem_request, rec_mem_req):
                    if gc_runtime:
                        actions.append(
                            f"Skip memory reduction — {deployment} matches GC runtime "
                            f"pattern; heap retention makes a reduction unsafe even "
                            f"against the VPA target"
                        )
                    elif bursty_workload:
                        actions.append(
                            f"Skip memory reduction — {deployment} matches bursty "
                            f"workload class; manual review required even against "
                            f"the VPA target"
                        )
                    else:
                        recommendations.append(f"Reduce memory request to {format_memory(rec_mem_req)}")
                        actions.append(
                            f"Reduce memory REQUEST from {format_memory(mem_request)} → "
                            f"{format_memory(rec_mem_req)} (VPA target)"
                        )
                        mem_delta_mi = mem_request - rec_mem_req  # positive
            elif effective_mem > mem_request * (profile.mem_under_pct / 100):
                if rec_mem_req > mem_request and not _below_deadband(mem_request, rec_mem_req):
                    recommendations.append(f"Increase memory request to {format_memory(rec_mem_req)}")
                    actions.append(
                        f"Raise memory REQUEST from {format_memory(mem_request)} → {format_memory(rec_mem_req)}"
                    )
                    mem_delta_mi = mem_request - rec_mem_req  # negative
            elif effective_mem < mem_request * (profile.mem_over_pct / 100):
                # GC runtimes (JVM, Node.js) retain heap pages — `avg_mem` and even
                # P95 underestimate working set. Never reduce mem request for them.
                if too_spiky_to_reduce:
                    # Readiness gate already emitted the explanation above;
                    # just don't issue the numeric reduction.
                    pass
                elif gc_runtime:
                    actions.append(
                        f"Skip memory reduction — {deployment} matches GC runtime "
                        f"pattern (JVM/Node.js); heap retention makes avg_mem "
                        f"unsafe to use as a sizing signal"
                    )
                elif bursty_workload:
                    # Prometheus/Cassandra/Kafka/ES — bursty memory profile.
                    # avg/P95 over a 7d window misses ingestion spikes, WAL
                    # replay, query-time memory, compactions, etc.
                    actions.append(
                        f"Skip memory reduction — {deployment} matches bursty "
                        f"workload class (Prometheus/Cassandra/Kafka/ES). "
                        f"Manual review required: avg/P95 do not represent "
                        f"working set under load"
                    )
                elif not _below_deadband(mem_request, rec_mem_req):
                    recommendations.append(f"Reduce memory request to {format_memory(rec_mem_req)}")
                    actions.append(
                        f"Reduce memory REQUEST from {format_memory(mem_request)} → {format_memory(rec_mem_req)}"
                    )
                    mem_delta_mi = mem_request - rec_mem_req  # positive

        # Limit recommendations — run regardless of UNSTABLE because OOM /
        # throttling fixes are root-cause, not optimization.
        # Memory limit: raise on (a) confirmed OOM kills, OR (b) MEM_SATURATION
        # without OOM (the cartservice case — request rising toward an
        # already-tight limit produces an invalid PodSpec if we don't also
        # raise the limit).
        needs_mem_limit_raise = oom_kills > 0 or "MEM_SATURATION" in issues

        if needs_mem_limit_raise:
            if has_prometheus and mem_p95 > 0:
                rec_mem_limit = max(mem_p95 * 1.3, mem_max * 1.2, mem_request * 1.5)
            else:
                rec_mem_limit = max(avg_mem * 1.5, mem_request * 1.5)

            if mem_limit > 0:
                rec_mem_limit = max(rec_mem_limit, mem_limit * 1.25)

            # Invariant: request <= limit. If we just recommended a request
            # raise, ensure the limit covers it (with burst headroom).
            if rec_mem_req > 0:
                rec_mem_limit = max(rec_mem_limit, rec_mem_req * 1.25)

            reason = (
                "P0: confirmed OOM kills"
                if oom_kills > 0
                else "P1: memory saturation — request would exceed current limit"
            )
            recommendations.append(f"Increase memory limit to {format_memory(rec_mem_limit)}")
            actions.append(
                f"Raise memory LIMIT from {format_memory(mem_limit) if mem_limit > 0 else 'none'} "
                f"→ {format_memory(rec_mem_limit)} ({reason})"
            )

        if has_prometheus and cpu_throttle > CPU_THROTTLE_P0_THRESHOLD_PCT:
            # Throttling is caused by the CPU *limit* (CFS quota), not by the
            # node being busy. There are two legitimate, opposing fixes and the
            # "right" one is a cluster-policy call, not something the analyzer
            # can decide from metrics alone:
            #
            #   - REMOVE/WIDEN the limit → the container bursts freely, throttling
            #     stops, and the request still governs scheduling fairness. Best
            #     for single-tenant / burst-friendly workloads owned by teams that
            #     manage their own CPU behavior.
            #   - KEEP (and widen) the limit → on a shared, multi-tenant node an
            #     unlimited "use-all-you-can" container becomes a noisy neighbor
            #     and starves co-tenants. A hard ceiling protects the rest of the
            #     node at the cost of some throttling for this workload.
            #
            # `profile.cpu_limit_policy` selects the stance (neutral/burst/protect;
            # set per-namespace via --profiles or globally via --cpu-limit-policy).
            # Default "neutral" presents BOTH options with the tradeoff spelled
            # out and recommends no direction. If no CPU limit is set, throttling
            # comes from a namespace LimitRange / parent cgroup regardless of
            # policy — surface that rather than inventing a limit.
            policy = profile.cpu_limit_policy
            if cpu_limit > 0:
                widen_to = max(CPU_REDUCTION_BASELINE_M, cpu_p95 * 1.2, cpu_max * 1.1, rec_cpu_req * 1.5)
                remove_note = (
                    f"REMOVE CPU LIMIT ({format_cpu(cpu_limit)}) — throttling "
                    f"({cpu_throttle:.1f}%) is caused by the CFS quota, not node "
                    f"pressure. Removing it lets the container burst; the request "
                    f"({format_cpu(rec_cpu_req)}) still governs scheduling."
                )
                widen_note = (
                    f"Widen CPU LIMIT {format_cpu(cpu_limit)} → {format_cpu(widen_to)} "
                    f"(keeps a hard ceiling but a limit set at peak still throttles "
                    f"the next spike)."
                )
                keep_note = (
                    f"KEEP the CPU LIMIT to protect co-tenants — on a shared node an "
                    f"unlimited container can starve neighbors. Widen it "
                    f"{format_cpu(cpu_limit)} → {format_cpu(widen_to)} to reduce "
                    f"throttling while retaining the ceiling."
                )
                if policy == CPU_LIMIT_POLICY_BURST:
                    recommendations.append(f"Remove CPU limit (currently {format_cpu(cpu_limit)}) to stop throttling")
                    actions.append(f"{remove_note} Preferred fix under policy 'burst' (P0: active throttling).")
                    actions.append(f"Alternative (if a hard ceiling is required): {widen_note}")
                elif policy == CPU_LIMIT_POLICY_PROTECT:
                    recommendations.append(
                        f"Widen CPU limit {format_cpu(cpu_limit)} → {format_cpu(widen_to)} to reduce throttling"
                    )
                    actions.append(f"{keep_note} Preferred fix under policy 'protect' (P0: active throttling).")
                    actions.append(f"Alternative (single-tenant / burst-friendly): {remove_note}")
                else:  # neutral — present both, recommend no direction
                    actions.append(
                        f"CPU throttling ({cpu_throttle:.1f}%) is caused by the CFS "
                        f"quota (the limit), not node pressure. There is a tradeoff — "
                        f"pick one based on how this node is shared (P0: active throttling):"
                    )
                    actions.append(f"Option A (burst-friendly / single-tenant): {remove_note} Or {widen_note}")
                    actions.append(f"Option B (multi-tenant / protect neighbors): {keep_note}")
                    actions.append(
                        "No default direction: removing the limit helps this workload "
                        "but risks noisy-neighbor starvation; keeping it protects "
                        "co-tenants but tolerates some throttling. Set a per-namespace "
                        "`cpu_limit_policy` (burst/protect) or `--cpu-limit-policy` to "
                        "make this decision automatically."
                    )
            else:
                actions.append(
                    f"CPU throttling ({cpu_throttle:.1f}%) observed with NO CPU "
                    f"limit on the workload — the quota is coming from a namespace "
                    f"LimitRange or a parent cgroup. Investigate/remove that limit; "
                    f"do not add a CPU limit here (P0: active throttling)."
                )

        # Final invariant check: if we recommend a request raise but no limit
        # change, and the proposed request would exceed the existing limit,
        # auto-raise the limit so the resulting PodSpec is valid.
        if (
            rec_mem_req > mem_request
            and mem_limit > 0
            and rec_mem_req > mem_limit
            and not needs_mem_limit_raise
            and not suppress_request_rightsizing
        ):
            safe_limit = max(mem_limit * 1.25, rec_mem_req * 1.25)
            recommendations.append(f"Increase memory limit to {format_memory(safe_limit)}")
            actions.append(
                f"Raise memory LIMIT from {format_memory(mem_limit)} → "
                f"{format_memory(safe_limit)} (request would otherwise exceed "
                f"limit, producing an invalid PodSpec)"
            )

    # Determine scaling approach
    if is_statefulset:
        scaling_approach = ScalingApproach.VPA
    elif priority == Priority.P0 or "UNSTABLE" in issues:
        scaling_approach = ScalingApproach.HPA_AFTER_FIX
    elif "ReadWriteOnce" in pvc_mode or replicas == 1:
        scaling_approach = ScalingApproach.VPA
    elif replicas > 1 and avg_cpu > cpu_request * 0.6:
        scaling_approach = ScalingApproach.HPA
    else:
        scaling_approach = ScalingApproach.VPA

    # Add scaling recommendations to actions
    if scaling_approach == ScalingApproach.HPA:
        actions.append(f"Enable HPA with fleet-wide policy ({HPA_TARGET_UTILIZATION_DEFAULT}% CPU target)")
        actions.append("Set minReplicas: 2 for HA")
    elif scaling_approach == ScalingApproach.VPA:
        if is_statefulset:
            actions.append("⚠️ StatefulSet - use VPA, not HPA")
        actions.append("Enable VPA in 'Off' mode to validate recommendations")
    elif scaling_approach == ScalingApproach.HPA_AFTER_FIX:
        actions.append("Fix issues above BEFORE enabling HPA")

    # Cross-check VPA against our own Prometheus estimate. If both signals
    # exist and disagree by more than VPA_DISAGREEMENT_RATIO, surface it rather
    # than silently trusting VPA — a large gap usually means the observation
    # windows differ or the workload's profile recently shifted, and a human
    # should look before applying. Advisory only; VPA still drove the number.
    if has_prometheus:
        if has_vpa_cpu and cpu_p95 > 0 and _diverges(vpa_cpu_target, cpu_p95, VPA_DISAGREEMENT_RATIO):
            actions.append(
                f"VPA/Prometheus CPU disagree — VPA target {format_cpu(vpa_cpu_target)} "
                f"vs P95 {format_cpu(cpu_p95)}. Recommendation uses VPA; verify the "
                f"observation window before applying."
            )
        if has_vpa_mem and mem_p95 > 0 and _diverges(vpa_mem_target, mem_p95, VPA_DISAGREEMENT_RATIO):
            actions.append(
                f"VPA/Prometheus memory disagree — VPA target {format_memory(vpa_mem_target)} "
                f"vs P95 {format_memory(mem_p95)}. Recommendation uses VPA; verify the "
                f"observation window before applying."
            )

    # Determine if manual action required
    requires_manual = any(issue in ["REQUESTS_NOT_SET", "OOM_KILLED", "CPU_THROTTLED", "UNSTABLE"] for issue in issues)

    # Build current state strings
    current_cpu = f"{cpu_request:.0f}m req"
    if cpu_limit > 0:
        current_cpu += f", {cpu_limit:.0f}m limit"
    current_cpu += f" (avg: {avg_cpu:.0f}m"
    if has_prometheus and cpu_p95 > 0:
        current_cpu += f", P95: {cpu_p95:.0f}m"
    current_cpu += ")"

    current_mem = f"{mem_request:.0f}Mi req"
    if mem_limit > 0:
        current_mem += f", {mem_limit:.0f}Mi limit"
    current_mem += f" (avg: {avg_mem:.0f}Mi"
    if has_prometheus and mem_p95 > 0:
        current_mem += f", P95: {mem_p95:.0f}Mi"
    current_mem += ")"

    analysis = WorkloadAnalysis(
        namespace=namespace,
        deployment=deployment,
        workload_type=workload_type,
        cluster=cluster,
        priority=priority,
        scaling_approach=scaling_approach,
        issues=issues,
        recommendations=recommendations,
        current_cpu=current_cpu,
        current_mem=current_mem,
        recommended_cpu=format_cpu(rec_cpu_req),
        recommended_mem=format_memory(rec_mem_req),
        action_required="; ".join(actions) if actions else "No immediate action required",
        cpu_p50=cpu_p50,
        cpu_p95=cpu_p95,
        cpu_max=cpu_max,
        cpu_stddev=cpu_stddev,
        cpu_throttle_pct=cpu_throttle,
        mem_p50=mem_p50,
        mem_p95=mem_p95,
        mem_max=mem_max,
        mem_stddev=mem_stddev,
        mem_volatility_cv=mem_cv,
        replicas=replicas,
        oom_kills=oom_kills,
        total_restarts=total_restarts,
        restart_rate=restart_rate,
        max_restarts_per_pod=max_restarts_per_pod,
        avg_cpu=avg_cpu,
        avg_mem=avg_mem,
        cpu_request=cpu_request,
        cpu_limit=cpu_limit,
        mem_request=mem_request,
        mem_limit=mem_limit,
        requires_manual_action=requires_manual,
        pod_count=pod_count,
        last_restart_reason=last_restart_reason,
        last_restart_exit_code=last_restart_exit_code,
        crash_signal=crash_signal,
        key_labels=key_labels,
        owner=owner,
        low_confidence=low_confidence,
        insufficient_data=insufficient_data,
        gc_runtime=gc_runtime,
        bursty_workload=bursty_workload,
        vpa_present=vpa_present,
        vpa_cpu_target=vpa_cpu_target,
        vpa_mem_target=vpa_mem_target,
        vpa_mem_upper=vpa_mem_upper,
        rec_basis=rec_basis,
        cpu_delta_m=cpu_delta_m,
        mem_delta_mi=mem_delta_mi,
        policy_name=profile.name,
    )

    # Confidence score (used for downstream gating + report rendering).
    score, band, reasons = _confidence_score(
        has_prometheus=has_prometheus,
        insufficient_data=insufficient_data,
        gc_runtime=gc_runtime,
        bursty_workload=bursty_workload,
        restart_rate=restart_rate,
        pod_count=pod_count,
        has_cpu_limit=cpu_limit > 0,
        has_mem_limit=mem_limit > 0,
        vpa_present=vpa_present,
    )
    analysis.confidence = score
    analysis.confidence_band = band
    analysis.confidence_reasons = reasons

    # Build rationale
    analysis.rationale = build_rationale(analysis, has_prometheus)

    return analysis


def format_cpu(millicores: float) -> str:
    """Format CPU to string."""
    if millicores < 1000:
        return f"{int(millicores)}m"
    return f"{millicores / 1000:.1f}"


def format_memory(mebibytes: float) -> str:
    """Format memory to string."""
    if mebibytes < 1024:
        return f"{int(mebibytes)}Mi"
    return f"{mebibytes / 1024:.1f}Gi"


def compute_data_quality(data: list[dict], has_prometheus: bool) -> list[dict]:
    """Scan raw CSV rows for N/A rates on Prometheus-derived columns.

    Returns a list of dicts describing columns where coverage is suspect.
    The markdown header surfaces these as a banner so an admin can tell
    "tool says no throttling" from "tool couldn't measure throttling."

    A column with >10% N/A is flagged. We only check Prometheus columns
    in Prometheus mode — kubectl-only mode has them all N/A by design.
    """
    if not data or not has_prometheus:
        return []
    columns = [
        ("CPU_Throttle_Pct", "CPU throttling"),
        ("CPU_P95(m)", "CPU P95"),
        ("Mem_P95(Mi)", "Memory P95"),
        ("Mem_Volatility_CV", "Memory volatility CV"),
        ("Restart_Rate_Per_Day", "Restart rate per day"),
    ]
    total = len(data)
    warnings = []
    for col, label in columns:
        na = sum(1 for row in data if (row.get(col) or "").strip() in ("N/A", "", "-"))
        pct = (na / total) * 100 if total else 0
        if pct > 10:
            warnings.append(
                {
                    "column": col,
                    "label": label,
                    "na_count": na,
                    "na_pct": pct,
                    "total": total,
                }
            )
    return warnings


def check_prometheus(data: list[dict]) -> bool:
    """Check if Prometheus metrics are available."""
    if not data:
        return False

    for row in data[:5]:
        cpu_p95 = row.get("CPU_P95(m)", "N/A")
        if cpu_p95 not in ("N/A", "", "0", "0.0"):
            return True
    return False


def _build_render_context(analyses: list["WorkloadAnalysis"], has_prometheus: bool, cluster: str) -> dict:
    """Assemble the full Jinja render context.

    Pure data — no template logic. Aggregations live in k8s_advisor.aggregations.
    """
    from k8s_advisor.aggregations import (
        build_namespace_rollups,
        build_pattern_groups,
        build_top_savers,
        fleet_totals,
    )

    rollups = build_namespace_rollups(analyses)
    totals = fleet_totals(rollups)
    top = build_top_savers(analyses)
    pattern_groups = build_pattern_groups(analyses)

    counts = {
        "p0": sum(1 for a in analyses if a.priority == Priority.P0),
        "p1": sum(1 for a in analyses if a.priority == Priority.P1),
        "p2": sum(1 for a in analyses if a.priority == Priority.P2),
        "p3": sum(1 for a in analyses if a.priority == Priority.P3),
        "hpa": sum(1 for a in analyses if a.scaling_approach == ScalingApproach.HPA),
        "vpa": sum(1 for a in analyses if a.scaling_approach == ScalingApproach.VPA),
        "hpa_after_fix": sum(1 for a in analyses if a.scaling_approach == ScalingApproach.HPA_AFTER_FIX),
        "manual": sum(1 for a in analyses if a.scaling_approach == ScalingApproach.MANUAL),
        "none_": sum(1 for a in analyses if a.scaling_approach == ScalingApproach.NONE),
    }
    issue_counts_dict: dict[str, int] = {}
    for a in analyses:
        for issue in a.issues:
            issue_counts_dict[issue] = issue_counts_dict.get(issue, 0) + 1
    issue_counts = sorted(issue_counts_dict.items(), key=lambda kv: -kv[1])

    # Determine which workloads are part of large pattern groups (N >= 10).
    # We render only the first instance per group in the detail section, then
    # a single collapse-marker line summarizing the rest. Cuts the P0 detail
    # section from ~50 cards to ~10 on big multi-tenant clusters.
    LARGE_PATTERN_THRESHOLD = 10
    pattern_first: dict = {}  # (ns, prefix, priority) -> first deployment name
    pattern_size: dict = {}  # (ns, prefix, priority) -> count
    for g in pattern_groups:
        if len(g.workloads) >= LARGE_PATTERN_THRESHOLD:
            sorted_members = sorted(g.workloads)
            pattern_first[(g.namespace, g.prefix, g.priority)] = sorted_members[0]
            pattern_size[(g.namespace, g.prefix, g.priority)] = len(g.workloads)

    from k8s_advisor.aggregations import _name_prefix as pattern_prefix

    priority_buckets = []
    for p, anchor, label in [
        (Priority.P0, "p0", "P0"),
        (Priority.P1, "p1", "P1"),
        (Priority.P2, "p2", "P2"),
        (Priority.P3, "p3", "P3"),
    ]:
        workloads = [a for a in analyses if a.priority == p]
        workloads.sort(key=lambda a: (a.namespace, a.deployment))

        rendered = []
        seen_collapsed: dict = {}  # (ns, prefix, priority) -> True after first
        for a in workloads:
            prefix = pattern_prefix(a.deployment)
            key = (a.namespace, prefix, p.value)
            group_size = pattern_size.get(key)
            if group_size:
                # Member of a large pattern group.
                if key not in seen_collapsed:
                    # Render the first one + a collapse note.
                    rendered.append(
                        {
                            "kind": "workload",
                            "analysis": a,
                            "collapse_note": (
                                f"+ {group_size - 1} identical `{prefix}-*` "
                                f"instances in `{a.namespace}` collapsed "
                                f"(see Pattern Groups)"
                            )
                            if group_size > 1
                            else None,
                        }
                    )
                    seen_collapsed[key] = True
                # else: silently drop subsequent members.
            else:
                rendered.append({"kind": "workload", "analysis": a, "collapse_note": None})

        priority_buckets.append(
            {
                "label": label,
                "anchor": anchor,
                "workloads": workloads,  # raw count for headers / TOC
                "rendered": rendered,  # what the template iterates
            }
        )

    return {
        "analyses": analyses,
        "cluster": cluster or "unknown",
        "has_prometheus": has_prometheus,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "counts": counts,
        "manual_count": sum(1 for a in analyses if a.requires_manual_action),
        "insufficient_count": sum(1 for a in analyses if a.insufficient_data),
        "issue_counts": issue_counts,
        "namespace_rollups": rollups,
        "fleet_totals": totals,
        "top": top,
        "pattern_groups": pattern_groups,
        "priority_buckets": priority_buckets,
        "hpa_target_default": HPA_TARGET_UTILIZATION_DEFAULT,
        "format_cpu": format_cpu,
        "format_mem": format_memory,
    }


def _jinja_env():
    """Build (and cache) the Jinja environment that renders the report."""
    global _JINJA_ENV
    if _JINJA_ENV is None:
        from jinja2 import Environment, PackageLoader, select_autoescape

        _JINJA_ENV = Environment(
            loader=PackageLoader("k8s_advisor", "templates"),
            autoescape=select_autoescape(disabled_extensions=("md", "j2", "md.j2")),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
    return _JINJA_ENV


_JINJA_ENV = None


def generate_executive_summary(analyses: list[WorkloadAnalysis], has_prometheus: bool) -> list[str]:
    """Generate executive summary section."""
    lines = [
        "## Executive Summary",
        "",
    ]

    manual_count = sum(1 for a in analyses if a.requires_manual_action)
    p0_count = sum(1 for a in analyses if a.priority == Priority.P0)

    if manual_count > 0:
        lines.extend(
            [
                "### ⚠️ Immediate Action Required",
                "",
                f"**{manual_count} workloads require immediate manual resource updates** before HPA/VPA can be enabled.",
                f"**{p0_count} P0 workloads** require immediate action: missing resource requests (scheduler impact + HPA blocked), active CPU throttling (live performance degradation), or confirmed OOM kills (apps actively crashing).",
                "",
                "**👉 [Jump to P0 Priority Section →](#p0-priority)**",
                "",
            ]
        )

    lines.extend(
        [
            "### Priority Distribution",
            "",
            "| Priority | Count | Description |",
            "|----------|-------|-------------|",
        ]
    )

    for priority in Priority:
        count = sum(1 for a in analyses if a.priority == priority)
        desc = {
            Priority.P0: "Must fix now — missing requests, CPU throttling, or confirmed OOM kills",
            Priority.P1: "High - frequent restarts, memory saturation, or severe under-provisioning",
            Priority.P2: "Medium - resource optimization opportunities",
            Priority.P3: "Low - no significant issues",
        }.get(priority, "")
        lines.append(f"| **{priority.value}** | {count} | {desc} |")

    lines.extend(
        [
            "",
            "### Scaling Approach Recommendations",
            "",
            "| Approach | Count | Description |",
            "|----------|-------|-------------|",
        ]
    )

    for approach in ScalingApproach:
        count = sum(1 for a in analyses if a.scaling_approach == approach)
        desc = {
            ScalingApproach.HPA: "Ready for Horizontal Pod Autoscaler",
            ScalingApproach.VPA: "Recommended for Vertical Pod Autoscaler",
            ScalingApproach.MANUAL: "Manual resource updates only",
            ScalingApproach.HPA_AFTER_FIX: "HPA after fixing blockers",
            ScalingApproach.NONE: "Excluded from autoscaling",
        }.get(approach, "")
        lines.append(f"| **{approach.value}** | {count} | {desc} |")

    lines.extend(
        [
            "",
            "_* HPA workloads can optionally run VPA in `updateMode: Off` for continuous right-sizing insights._",
            "",
        ]
    )

    # Common issues
    issue_counts = {}
    for analysis in analyses:
        for issue in analysis.issues:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1

    if issue_counts:
        lines.extend(
            [
                "### Common Issues",
                "",
                "| Issue | Count |",
                "|-------|-------|",
            ]
        )
        for issue, count in sorted(issue_counts.items(), key=lambda x: -x[1]):
            lines.append(f"| {issue} | {count} |")
        lines.append("")

    lines.extend(["---", ""])

    return lines


def generate_implementation_guide() -> list[str]:
    """Generate implementation guide section."""
    return [
        "## Implementation Guide",
        "",
        "### HPA Fleet-Wide Protection Policy",
        "",
        "Apply this standard `behavior` block to **all** HPAs to prevent thundering herd:",
        "",
        "```yaml",
        "apiVersion: autoscaling/v2",
        "kind: HorizontalPodAutoscaler",
        "metadata:",
        "  name: <deployment-name>",
        "spec:",
        "  scaleTargetRef:",
        "    apiVersion: apps/v1",
        "    kind: Deployment",
        "    name: <deployment-name>",
        "  minReplicas: 2",
        "  maxReplicas: 10",
        "  metrics:",
        "  - type: Resource",
        "    resource:",
        "      name: cpu",
        "      target:",
        "        type: Utilization",
        f"        averageUtilization: {HPA_TARGET_UTILIZATION_DEFAULT}    # 70-80 keeps HPA proactive (>100 is reactive)",
        "  behavior:",
        "    scaleUp:",
        "      stabilizationWindowSeconds: 0",
        "      policies:",
        "      - type: Pods",
        "        value: 10                # Max 10 new pods per minute",
        "        periodSeconds: 60",
        "      - type: Percent",
        "        value: 50                # Or max 50% increase per minute",
        "        periodSeconds: 60",
        "      selectPolicy: Min          # Use more conservative policy",
        "    scaleDown:",
        "      stabilizationWindowSeconds: 300",
        "      policies:",
        "      - type: Pods",
        "        value: 5                 # Remove max 5 pods per 3 minutes",
        "        periodSeconds: 180",
        "      - type: Percent",
        "        value: 25                # Or remove max 25% per 3 minutes",
        "        periodSeconds: 180",
        "      selectPolicy: Min",
        "```",
        "",
        "**Why this matters:**",
        "- Prevents API server overload from simultaneous pod creation",
        "- Avoids database connection pool exhaustion",
        "- Provides controlled, predictable scaling behavior",
        "",
        "### VPA Configuration",
        "",
        "For workloads recommended for VPA, use this pattern:",
        "",
        "```yaml",
        "apiVersion: autoscaling.k8s.io/v1",
        "kind: VerticalPodAutoscaler",
        "metadata:",
        "  name: <deployment-name>-vpa",
        "spec:",
        "  targetRef:",
        "    apiVersion: apps/v1",
        "    kind: Deployment",
        "    name: <deployment-name>",
        "  updatePolicy:",
        '    updateMode: "Off"           # Start with Off, validate for 24-48h',
        "  resourcePolicy:",
        "    containerPolicies:",
        "    - containerName: '*'",
        "      minAllowed:",
        "        cpu: 50m",
        "        memory: 16Mi",
        "      maxAllowed:",
        "        cpu: 4",
        "        memory: 8Gi",
        "```",
        "",
        "**VPA Modes:**",
        "- `Off`: Recommendations only (safe for validation)",
        "- `Auto`: Restart pods with new recommendations",
        "- `Recreate`: Like Auto (restart required)",
        "- `InPlaceOrRecreate`: In-place updates if supported (K8s 1.33+)",
        "",
        "### HPA + VPA Coexistence",
        "",
        "You can run both on the same deployment:",
        "",
        "```yaml",
        "# VPA in Off mode for continuous recommendations",
        "updatePolicy:",
        '  updateMode: "Off"',
        "",
        "# HPA handles replica scaling",
        "# VPA provides guidance for request tuning",
        "```",
        "",
        "**Benefits:**",
        "- HPA scales replicas based on load",
        "- VPA provides data-driven request recommendations",
        "- Manually apply VPA suggestions periodically",
        "",
        "### Rollout Strategy",
        "",
        "**Phase 1: P0 Fixes (Week 1)**",
        "- Fix missing requests",
        "- Address OOM kills",
        "- Resolve CPU throttling",
        "- Fix unstable workloads",
        "",
        "**Phase 2: HPA Rollout (Week 2-3)**",
        "- Start with low-risk workloads (P2/P3)",
        "- Apply HPA behavior blocks",
        "- Monitor for 48h before proceeding",
        "- Gradually enable more workloads",
        "",
        "**Phase 3: VPA & Optimization (Week 4+)**",
        "- Enable VPA for single-replica workloads",
        "- Optimize over-requested resources",
        "- Fine-tune based on observed behavior",
        "",
        "---",
        "",
    ]


def generate_report(
    analyses: list[WorkloadAnalysis],
    output_path: str,
    has_prometheus: bool,
    graphs_dir: str | None = None,
    data_quality_warnings: list[dict] | None = None,
):
    """Render the markdown report from k8s_advisor/templates/report.md.j2.

    graphs_dir: relative path the template uses to embed graph PNGs (e.g.
    'graphs'). When None, no image embeds are produced.
    data_quality_warnings: list of dicts from compute_data_quality(); when
    non-empty, a banner is rendered at the top of the report.
    """
    cluster = analyses[0].cluster if analyses else "unknown"
    context = _build_render_context(analyses, has_prometheus, cluster)
    context["graphs_dir"] = graphs_dir
    context["data_quality_warnings"] = data_quality_warnings or []
    template = _jinja_env().get_template("report.md.j2")
    rendered = template.render(**context)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(rendered)


SUPPORTED_FORMATS = ("md", "json")


def _serialize_analyses(
    analyses: list,
    has_prometheus: bool,
    data_quality_warnings: list,
) -> dict:
    """Build the JSON-serializable dict. Enums become their string values
    so the payload is consumable from non-Python tooling without lookup
    tables."""
    workloads = []
    for a in analyses:
        record = asdict(a)
        record["priority"] = a.priority.value
        record["scaling_approach"] = a.scaling_approach.value
        workloads.append(record)

    summary = {
        "total": len(analyses),
        "p0": sum(1 for a in analyses if a.priority == Priority.P0),
        "p1": sum(1 for a in analyses if a.priority == Priority.P1),
        "p2": sum(1 for a in analyses if a.priority == Priority.P2),
        "p3": sum(1 for a in analyses if a.priority == Priority.P3),
        "manual_action_required": sum(1 for a in analyses if a.requires_manual_action),
    }

    return {
        "cluster": analyses[0].cluster if analyses else "unknown",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "prometheus_used": has_prometheus,
        "data_quality_warnings": list(data_quality_warnings or []),
        "summary": summary,
        "workloads": workloads,
    }


def _write_json_report(
    analyses: list,
    output_path: Path,
    has_prometheus: bool,
    data_quality_warnings: list,
) -> None:
    """Write the JSON report next to the markdown one."""
    payload = _serialize_analyses(analyses, has_prometheus, data_quality_warnings)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)


def analyze_csv_file(
    csv_path: str,
    output_dir: str = "reports",
    generate_graphs: bool = False,
    formats: tuple = ("md",),
    state_dir: str | None = None,
    profiles_path: str | None = None,
    cpu_limit_policy: str | None = None,
) -> dict:
    """Main analysis function.

    Args:
        csv_path: Input CSV from `collect`.
        output_dir: Where to write the markdown / JSON / graphs.
        generate_graphs: Render PNGs alongside the markdown.
        formats: Tuple of output formats to write. See SUPPORTED_FORMATS.
        state_dir: Optional. When set, the analyzer reads/writes
            ``<state_dir>/seen.json`` to detect recommendations that
            haven't changed since a previous run. Each WorkloadAnalysis
            gets ``fingerprint``, ``previously_seen``, ``times_seen``,
            and ``first_seen`` populated. Downstream uploaders can use
            this to suppress noise (e.g. "skip a Slack post if the
            recommendation is unchanged from last week").
        profiles_path: Optional. Path to a YAML policy file with per-namespace
            overrides for headroom multipliers, guardrail floors, and
            efficiency thresholds. See ``k8s_advisor/profiles.py`` for the
            schema. When omitted, every namespace uses the constants.py
            defaults — analyzer behavior is byte-identical to before this
            feature.
        cpu_limit_policy: Optional. Global default stance for the CPU-limit
            recommendation under throttling ("neutral"/"burst"/"protect"). Acts
            as the fallback baseline; a per-namespace ``cpu_limit_policy`` in the
            ``--profiles`` file still overrides it. When omitted, the neutral
            (present-both, no-direction) default applies.

    Returns a dict mapping each requested format ("md", "json") to its
    output path. Backwards-compatible: if a single-format tuple is passed,
    callers can still grab paths by key.
    """

    if not formats:
        raise ValueError(f"At least one output format is required. Supported: {SUPPORTED_FORMATS}")
    unknown = [f for f in formats if f not in SUPPORTED_FORMATS]
    if unknown:
        raise ValueError(f"Unsupported format(s): {unknown}. Supported: {SUPPORTED_FORMATS}")

    print(f"🔍 Analyzing: {csv_path}")

    # Load CSV
    data = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        data = list(reader)

    print(f"✅ Loaded {len(data)} workloads")

    # Check Prometheus availability
    has_prometheus = check_prometheus(data)
    if has_prometheus:
        print("✅ Prometheus metrics detected - using enhanced analysis")
    else:
        print("⚠️  No Prometheus metrics - using basic kubectl-only analysis")

    # Compute data-quality warnings BEFORE analyze_workload converts N/A → 0.
    data_quality_warnings = compute_data_quality(data, has_prometheus)
    if data_quality_warnings:
        print(f"⚠️  Data-quality warnings: {len(data_quality_warnings)} columns with >10% N/A")

    # Resolve the policy file once, then look up per-namespace overrides
    # in the analyze loop. Loading is intentionally eager so a malformed
    # policy file fails before we do any analysis work.
    #
    # The global --cpu-limit-policy (if any) seeds the base profile so it
    # becomes the fallback default; a per-namespace/`default:` block in the
    # profiles file can still override it. Validated here so a bad flag value
    # fails loudly rather than being silently ignored.
    from k8s_advisor.profiles import load_profiles

    base_profile = DEFAULT_PROFILE
    if cpu_limit_policy is not None:
        from dataclasses import replace as _replace

        from k8s_advisor.constants import CPU_LIMIT_POLICY_VALUES

        if cpu_limit_policy not in CPU_LIMIT_POLICY_VALUES:
            raise ValueError(
                f"--cpu-limit-policy must be one of {sorted(CPU_LIMIT_POLICY_VALUES)}, got {cpu_limit_policy!r}"
            )
        base_profile = _replace(DEFAULT_PROFILE, cpu_limit_policy=cpu_limit_policy)

    if profiles_path:
        profile_set = load_profiles(profiles_path, base=base_profile)
        ns_overrides = sorted(profile_set.namespaces.keys())
        if ns_overrides:
            print(f"📋 Loaded profiles: default + overrides for {ns_overrides}")
        else:
            print("📋 Loaded profiles: default only")
    elif cpu_limit_policy is not None:
        # No profiles file, but a global policy was requested — apply it to
        # every namespace via the base default.
        profile_set = ProfileSet(default=base_profile)
    else:
        profile_set = DEFAULT_PROFILE_SET

    # Analyze each workload
    analyses = []
    for row in data:
        ns = row.get("Namespace", "")
        analysis = analyze_workload(row, has_prometheus, profile=profile_set.for_namespace(ns))
        analyses.append(analysis)

    print(f"✅ Analyzed {len(analyses)} workloads")

    # Idempotency: if a state directory was provided, fingerprint each
    # recommendation and tag whether it appeared in any previous run.
    # No-op when state_dir is None — keeps the legacy code path identical.
    if state_dir:
        from k8s_advisor.idempotency import fingerprint as _fp
        from k8s_advisor.idempotency import load_state, merge_run, save_state

        prior = load_state(state_dir)
        prior_fps = prior.get("fingerprints") or {}
        run_fps: list = []
        for a in analyses:
            fp = _fp(
                namespace=a.namespace,
                deployment=a.deployment,
                priority=a.priority.value,
                scaling_approach=a.scaling_approach.value,
                recommended_cpu=a.recommended_cpu,
                recommended_mem=a.recommended_mem,
            )
            a.fingerprint = fp
            run_fps.append(fp)
            prior_entry = prior_fps.get(fp)
            if prior_entry is not None:
                a.previously_seen = True
                a.first_seen = prior_entry.get("first_seen", "")
                # `times_seen` reflects the count *including* this run,
                # which is what an operator wants in the report ("seen 3
                # times"). After merge_run the entry's count will be N+1
                # for an N-prior workload; we set times_seen to that.
                a.times_seen = int(prior_entry.get("count", 0)) + 1
        new_state = merge_run(prior, run_fps)
        path = save_state(state_dir, new_state)
        repeats = sum(1 for a in analyses if a.previously_seen)
        print(f"🔁 Idempotency: {repeats} of {len(analyses)} workloads repeat prior run; state at {path}")

    # Generate graphs if requested. We render from the in-memory analyses
    # (not from the CSV) so the visualizer shares the analyzer's source of
    # truth — see visualizer.py docstring for context.
    graphs_subdir = None
    if generate_graphs:
        try:
            from k8s_advisor.visualizer import render_graphs

            graphs_dir = Path(output_dir) / "graphs"
            if render_graphs(analyses, str(graphs_dir)):
                # Path the markdown template should reference, relative to
                # the report file (which sits in `output_dir`).
                graphs_subdir = "graphs"
                print(f"✅ Graphs generated in {graphs_dir}")
        except ImportError:
            print("⚠️  Graph generation requires matplotlib")
            print("   Install with: pip install -e .[viz]")
        except Exception as e:
            print(f"⚠️  Graph generation failed: {e}")

    # Generate report
    Path(output_dir).mkdir(exist_ok=True)

    csv_name = Path(csv_path).stem
    if "_" in csv_name:
        parts = csv_name.split("_")
        timestamp = "_".join(parts[-2:]) if len(parts) >= 3 else parts[-1]
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    cluster = analyses[0].cluster if analyses else "unknown"
    base = Path(output_dir) / f"k8s-advisor_{cluster}_{timestamp}"

    written: dict = {}
    if "md" in formats:
        md_path = base.with_suffix(".md")
        generate_report(
            analyses,
            str(md_path),
            has_prometheus,
            graphs_dir=graphs_subdir,
            data_quality_warnings=data_quality_warnings,
        )
        print(f"✅ Markdown report: {md_path}")
        written["md"] = str(md_path)
    if "json" in formats:
        json_path = base.with_suffix(".json")
        _write_json_report(analyses, json_path, has_prometheus, data_quality_warnings)
        print(f"✅ JSON report: {json_path}")
        written["json"] = str(json_path)

    # Print summary
    p0 = sum(1 for a in analyses if a.priority == Priority.P0)
    p1 = sum(1 for a in analyses if a.priority == Priority.P1)
    p2 = sum(1 for a in analyses if a.priority == Priority.P2)
    manual = sum(1 for a in analyses if a.requires_manual_action)

    print("\n📊 Summary:")
    if p0 > 0:
        print(f"  ⚠️  P0 Blockers: {p0}")
    if p1 > 0:
        print(f"  🔴 P1 High: {p1}")
    if p2 > 0:
        print(f"  🟡 P2 Medium: {p2}")
    if manual > 0:
        print(f"  🔧 Manual action required: {manual}")

    return written


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python simple_analyzer.py <csv_file> [--graphs]")
        sys.exit(1)

    csv_file = sys.argv[1]
    generate_graphs = "--graphs" in sys.argv

    analyze_csv_file(csv_file, generate_graphs=generate_graphs)
