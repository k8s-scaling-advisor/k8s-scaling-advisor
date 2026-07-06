"""Shared constants for K8s Scaling Advisor.

This module consolidates all thresholds, patterns, and guardrails used across
data collection, analysis, and visualization.

CRITICAL: This is the single source of truth for all constants. Any changes here
must be intentional as they affect scaling decisions, priority classification,
and resource recommendations.
"""

# ══════════════════════════════════════════════════════════════════════════════
# Efficiency Thresholds
# ══════════════════════════════════════════════════════════════════════════════

# CPU efficiency thresholds (percentage of requested resources)
CPU_OVER_REQUESTED_THRESHOLD = 50  # < 50% usage = over-requested (wasteful)
CPU_UNDER_REQUESTED_THRESHOLD = 85  # > 85% usage = under-requested (needs more)

# Memory efficiency thresholds
MEM_OVER_REQUESTED_THRESHOLD = 50  # < 50% usage = over-requested (wasteful)
MEM_UNDER_REQUESTED_THRESHOLD = 85  # > 85% usage = under-requested (needs more)

# ══════════════════════════════════════════════════════════════════════════════
# Stability & Risk Thresholds
# ══════════════════════════════════════════════════════════════════════════════

# Restart thresholds for detecting unstable workloads
UNSTABLE_RESTART_THRESHOLD = 5  # >5 total restarts = unstable (fallback if no rate data)
UNSTABLE_RESTART_RATE_THRESHOLD = 2.0  # >2 restarts/day = unstable (primary indicator)

# Memory saturation thresholds
OOM_RISK_THRESHOLD = 200  # >200% memory usage = approaching OOM kill
MEM_SATURATION_LIMIT_THRESHOLD = 90  # >90% of limit = high OOM risk
EXTREME_USAGE_THRESHOLD = 200  # >200% = extreme under-provisioning

# Memory volatility thresholds (coefficient of variation)
MEMORY_VOLATILITY_LOW_THRESHOLD = 10  # <10% CV = stable memory usage
MEMORY_VOLATILITY_HIGH_THRESHOLD = 20  # >20% CV = potential memory leak

# ══════════════════════════════════════════════════════════════════════════════
# Resource Recommendation Parameters
# ══════════════════════════════════════════════════════════════════════════════

# Headroom multiplier applied to actual usage for recommendations.
# Example: 100m avg usage → 125m recommended request.
HEADROOM_MULTIPLIER = 1.25

# Volatility-aware memory headroom.
#
# Workloads with high coefficient-of-variation (CV) memory profiles need
# wider headroom than the steady-state default. A 50%-CV workload sized at
# P95 × 1.25 will OOM during the next swing; a 5%-CV workload sized that
# tightly is fine.
#
# CV is `Mem_Volatility_CV` in the CSV (collected via Prometheus only).
HEADROOM_MULTIPLIER_MID_VOLATILITY = 1.5  # 10% <= CV < 20%
HEADROOM_MULTIPLIER_HIGH_VOLATILITY = 1.8  # CV >= 20%

# Burst headroom for limits (minimum limit/request ratio)
BURST_HEADROOM_MULTIPLIER = 1.5  # Limits should be ≥1.5x requests

# ══════════════════════════════════════════════════════════════════════════════
# CPU Throttling
# ══════════════════════════════════════════════════════════════════════════════
#
# CPU throttling above this percentage is a P0 live-performance issue. A CPU
# *limit* is the cause of throttling: the kernel CFS quota parks the container
# once it exceeds the limit within a period, adding latency even when the node
# has spare CPU. Industry guidance (Google SRE, Kubernetes SIG-Node, Fairwinds,
# and Ballast's explicit design stance — "CPU limits cause throttling rather
# than reclaiming waste") is that removing/widening the CPU limit stops the
# throttling. BUT the opposite failure mode is real too: on a multi-tenant node
# an unlimited "use-all-you-can" container becomes a noisy neighbor and starves
# co-tenants. There is no universally correct default — it depends on who owns
# the app and how the cluster is shared. See CPU_LIMIT_POLICY_* below.
CPU_THROTTLE_P0_THRESHOLD_PCT = 5

# CPU-limit stance under throttling. Because the "right" answer is a cluster
# policy decision (burst-friendly single-tenant vs. protect-the-neighbors
# multi-tenant), the analyzer does not pick a direction by default — it presents
# BOTH the remove/widen fix and the keep-the-limit stance with the multi-tenant
# tradeoff spelled out. Operators can override per-namespace (profiles.py
# `cpu_limit_policy:`) or globally (`--cpu-limit-policy`):
#   - "neutral": present both options, recommend no direction (default)
#   - "burst":   recommend removing/widening the limit to stop throttling
#   - "protect": recommend keeping (and widening) the limit to protect co-tenants
CPU_LIMIT_POLICY_NEUTRAL = "neutral"
CPU_LIMIT_POLICY_BURST = "burst"
CPU_LIMIT_POLICY_PROTECT = "protect"
CPU_LIMIT_POLICY_DEFAULT = CPU_LIMIT_POLICY_NEUTRAL
CPU_LIMIT_POLICY_VALUES = frozenset({CPU_LIMIT_POLICY_NEUTRAL, CPU_LIMIT_POLICY_BURST, CPU_LIMIT_POLICY_PROTECT})

# ══════════════════════════════════════════════════════════════════════════════
# CPU Request Guardrails
# ══════════════════════════════════════════════════════════════════════════════

# These guardrails prevent noise and meaningless recommendations.
#
# WHY THESE VALUES:
# - Runtime overhead (containerd, health probes, log collectors) consumes 5-15m per pod
# - HPA CPU-based scaling becomes jittery at very low values (<50m)
# - Changes like 100m → 85m (15m saving) are operationally meaningless toil
# - A single GC event can look like a traffic spike when request is <50m

CPU_MIN_RECOMMENDED_M = 50  # Absolute floor for any CPU request recommendation
CPU_REDUCTION_BASELINE_M = 100  # Don't suggest reduction if current request ≤ this
CPU_REDUCTION_MIN_SAVING_M = 50  # Don't suggest reduction if saving < this amount
MEM_MIN_RECOMMENDED_MI = 16  # Absolute floor for any memory request recommendation

# Kubernetes validity constraints
CPU_ABSOLUTE_FLOOR_M = 100  # Container runtime overhead minimum
MEMORY_ABSOLUTE_FLOOR_MI = 256  # Practical minimum for container operation

# ══════════════════════════════════════════════════════════════════════════════
# Recommendation Deadband
# ══════════════════════════════════════════════════════════════════════════════
#
# Skip *raise* recommendations whose delta is smaller than this fraction of the
# current request. Rationale: VPA and Ballast both use a drift/deadband (Ballast
# ships a 10% threshold) so trivial deltas don't churn run-over-run. A rec that
# nudges a request by 3% is noise — it costs a rollout and a review for no real
# benefit, and it makes every report look "always changing".
#
# NOTE: this deadband applies to *raises*. CPU/memory *reductions* already have
# their own absolute guardrails (CPU_REDUCTION_MIN_SAVING_M, the over-request
# percentage gate), so the deadband complements rather than replaces those.
# Reductions are additionally deadbanded by percentage so a marginal
# over-request (e.g. 49% usage vs a 50% threshold) doesn't produce a 1% trim.
RECOMMENDATION_DEADBAND_PCT = 10  # skip raises/reductions under 10% delta

# ══════════════════════════════════════════════════════════════════════════════
# Statistical Readiness Gates
# ══════════════════════════════════════════════════════════════════════════════
#
# Best practice (VPA, Ballast): do not emit a numeric right-sizing until enough
# stable samples have accrued. A recommendation off a handful of noisy samples
# is worse than none — it reads as authoritative but is statistically hollow.
#
# We can only gate on what the CSV carries. Two signals:
#   1. CV (coefficient of variation) — too spiky to size reliably. Ballast skips
#      workloads above a CV cut; we suppress the numeric *reduction* (raises for
#      safety still fire) and say so, rather than trimming toward a mean that the
#      workload routinely blows past.
#   2. (Prometheus-only) CV is only meaningful with historical data, so this gate
#      is a no-op in kubectl-only mode, where INSUFFICIENT_DATA already guards.
#
# CV here is a fraction-of-mean expressed as a percent (same units as
# Mem_Volatility_CV in the CSV). 100 = std-dev equals the mean (extremely
# spiky). We suppress numeric *reductions* above this — sizing a workload whose
# usage swings ±100% of its mean down toward that mean is how you cause OOM /
# throttle regressions.
READINESS_MAX_CV_FOR_REDUCTION = 100

# ══════════════════════════════════════════════════════════════════════════════
# HPA Target Utilization
# ══════════════════════════════════════════════════════════════════════════════
#
# averageUtilization on an HPA target should sit comfortably under 100. Values
# >100 make the HPA *reactive* — pods saturate before a scale-up triggers.
# Industry guidance: 70-80%. We default to 75 and cap suggestions at 80.

HPA_TARGET_UTILIZATION_DEFAULT = 75
HPA_TARGET_UTILIZATION_MAX = 80

# ══════════════════════════════════════════════════════════════════════════════
# VPA Input Signal
# ══════════════════════════════════════════════════════════════════════════════
#
# When a VerticalPodAutoscaler recommendation exists it is preferred over our
# own P95/avg estimate (it is a controller-computed, headroom-inclusive target).
# But if VPA and our Prometheus P95 disagree by more than this ratio, we flag it
# for human review rather than silently trusting VPA — a large gap usually means
# the two observation windows differ or the workload's profile recently shifted.
# 2.0 = "one is more than 2x the other".
VPA_DISAGREEMENT_RATIO = 2.0

# ══════════════════════════════════════════════════════════════════════════════
# Confidence / Sample-Size Gates
# ══════════════════════════════════════════════════════════════════════════════
#
# In kubectl-only mode, metrics-server only exposes the most recent ~60s
# rolling sample. A workload that just started, is crashlooping, or sat idle
# during the collection window will have effectively zero usage signal — and
# should NOT receive prescriptive numeric recommendations.
#
# These gates produce a "INSUFFICIENT_DATA" / "LOW_CONFIDENCE" marker rather
# than a misleading number.

# Avg usage at or below this is treated as "no signal" in kubectl-only mode.
LOW_SIGNAL_CPU_M = 1.0  # millicores
LOW_SIGNAL_MEM_MI = 1.0  # MiB

# Restart-aware low-confidence gate.
#
# A workload that restarts frequently is in CrashLoop or has a memory leak —
# its `avg_*` and even P95 are not safe sizing signals. Suppress numeric
# recommendations entirely and surface "investigate restart cause first."
#
# These are intentionally conservative (low) thresholds because the cost of
# a wrong rec on a restart-zombie is high — a CrashLooping pod with thousands
# of restarts will report `avg_mem` reflecting only its early lifecycle, and
# trimming its memory request based on that signal can deepen the outage.
LOW_CONFIDENCE_RESTART_RATE_PER_DAY = 1.0
LOW_CONFIDENCE_MAX_RESTARTS_PER_POD = 5
LOW_CONFIDENCE_TOTAL_RESTARTS = 50  # absolute fallback (covers no-rate-data case)

# REQUESTS_NOT_SET priority split.
#
# Auto-promoting every workload with no requests to P0 inflates the priority
# count beyond usefulness — on a multi-tenant cluster this can easily put
# 40%+ of workloads in P0 and drown the actually-broken ones in noise.
# Demote to P2 when the workload looks idle and stable: low usage AND no
# restart history.
REQUESTS_NOT_SET_P0_CPU_M = 50  # active CPU usage threshold
REQUESTS_NOT_SET_P0_MEM_MI = 64  # active memory usage threshold

# Owner attribution.
#
# We pick the first label key from this allowlist that resolves to a
# non-empty value on a workload. Order matters — earlier keys win.
# Edit to match your environment's labelling conventions.
OWNER_LABEL_KEYS = (
    "app.kubernetes.io/part-of",
    "owner",
    "team",
    "owner-team",
    "app.kubernetes.io/managed-by",
)

# ══════════════════════════════════════════════════════════════════════════════
# Workload Patterns & Exclusions
# ══════════════════════════════════════════════════════════════════════════════

# Deployments explicitly excluded from all scaling recommendations.
#
# Use sparingly — most workloads are better handled by GC_RUNTIME_PATTERNS
# (skip memory reductions) or BURSTY_WORKLOAD_PATTERNS (manual review).
# Add a substring here only when the workload class is fundamentally
# unsuitable for HPA/VPA recommendations.
EXCLUDED_DEPLOYMENTS = {
    "logstash",  # JVM + plugin-constrained scaling; resize manually
}

# GC runtimes where memory-based HPA is UNRELIABLE.
#
# WHY: JVM (Java, Scala, Kotlin) retains heap pages after GC (MADV_FREE not
# used by default until JDK 12+). Node.js/V8 retains old-generation heap.
# Both cause monotonically increasing memory metrics that trigger runaway
# scale-up loops.
#
# IMPLICATION: For these patterns, memory HPA target will show
# "N/A (GC runtime)" and memory request reductions are skipped.
#
# Edit this set to match your environment's naming conventions.
GC_RUNTIME_PATTERNS = {
    # JVM ecosystem
    "java",
    "jvm",
    "logstash",
    "airflow",
    "kafka",
    "zookeeper",
    "elasticsearch",
    "opensearch",
    "cassandra",
    "spark",
    "flink",
    "tomcat",
    "jetty",
    # Node.js / V8
    "nodejs",
    "-node-",
    "node-server",
}

# Bursty workloads where avg/P95 are NOT representative of working set.
#
# WHY: WAL replay, ingest spikes, query bursts (Prometheus); compaction
# bursts (Cassandra, Kafka); shard rebalance (Elasticsearch). These have
# heavy-tailed memory profiles that 1.25× avg / 1.25× P95 will under-size,
# leading to OOM under load.
#
# IMPLICATION: Tagged BURSTY_WORKLOAD; memory request reductions are skipped
# (same handling as GC_RUNTIME) and an explicit "manual review required"
# note replaces the numeric recommendation. These also drop off the Top-N
# Memory Savers leaderboard so they don't become misleading easy wins.
BURSTY_WORKLOAD_PATTERNS = {
    "prometheus",
    "alertmanager",
    "thanos",
    "cortex",
    "mimir",
    "cassandra",
    "kafka-broker",
    "kafka-connect",
    "elasticsearch",
    "opensearch",
    "redis-cluster",
    "mongodb",
    "clickhouse",
    "victoriametrics",
}

# Memory-based HPA IS appropriate for these patterns.
#
# WHY: Stateless, non-GC runtimes (Go, C, Rust) where memory usage scales
# linearly with active connections/requests. Memory is released promptly.
#
# EXAMPLES: Caches, proxies, connection poolers, monitoring agents
MEMORY_SCALABLE_PATTERNS = {
    # Caches / proxies
    "redis",
    "memcached",
    "varnish",
    "nginx",
    "envoy",
    "haproxy",
    # Non-JVM message queues
    "rabbitmq",
    "pulsar",
    # DB sidecars (connection poolers)
    "postgres",
    "mysql",
    # Go-based monitoring
    "telegraf",
    "prometheus",
    "alertmanager",
    # Go tooling
    "oauth2-proxy",
    "kube-state-metrics",
    "metrics-server",
}

# ══════════════════════════════════════════════════════════════════════════════
# Visualization Colors
# ══════════════════════════════════════════════════════════════════════════════

COLOR_SCHEME = {
    "critical": "#d62728",  # Red
    "warning": "#ff7f0e",  # Orange
    "good": "#2ca02c",  # Green
    "info": "#1f77b4",  # Blue
    "neutral": "#aec7e8",  # Light blue/grey
    "highlight": "#ffbb78",  # Yellow
    "secondary": "#9467bd",  # Purple
}

# ══════════════════════════════════════════════════════════════════════════════
# CSV Column Definitions
# ══════════════════════════════════════════════════════════════════════════════

# CSV output columns (39 columns total)
# This defines the schema for data collection output
CSV_COLUMNS = [
    # Cluster identification (3 columns)
    "Cluster",
    "Namespace",
    "Workload_Type",  # "Deployment" or "StatefulSet"
    "Deployment",
    # Replica information (2 columns)
    "Replicas",
    "Pod_Count",
    # CPU metrics (10 columns)
    "Avg_CPU_Usage(m)",
    "CPU_Request(m)",
    "CPU_Limit(m)",
    "CPU_Usage_Pct_Of_Request",
    "CPU_Usage_Pct_Of_Limit",
    "CPU_Throttle_Pct",
    "CPU_P50(m)",
    "CPU_P95(m)",
    "CPU_Max(m)",
    "CPU_StdDev(m)",
    # Memory metrics (10 columns)
    "Avg_Mem_Usage(Mi)",
    "Mem_Request(Mi)",
    "Mem_Limit(Mi)",
    "Mem_Usage_Pct_Of_Request",
    "Mem_Usage_Pct_Of_Limit",
    "Mem_P50(Mi)",
    "Mem_P95(Mi)",
    "Mem_Max(Mi)",
    "Mem_StdDev(Mi)",
    "Mem_Volatility_CV",  # Coefficient of variation (%)
    # Stability metrics (7 columns)
    "OOMKilled_Count",
    "LastRestart_Reason",
    "LastRestart_ExitCode",  # numeric exit code or 'N/A'
    "Total_Restarts",
    "Max_Restarts_Per_Pod",
    "Restart_Rate_Per_Day",
    "Days_Since_Last_Restart",
    # HPA information (3 columns)
    "Has_HPA",
    "HPA_Min_Replicas",
    "HPA_Max_Replicas",
    # VPA recommendation, if a VerticalPodAutoscaler targets this workload
    # (recommender/"Off" mode counts). Optional input signal — "N/A" when VPA
    # is not installed or has produced no recommendation. Summed across
    # containers so it is comparable to CPU_Request(m) / Mem_Request(Mi).
    # (4 columns)
    "VPA_Present",
    "VPA_CPU_Target(m)",
    "VPA_Mem_Target(Mi)",
    "VPA_Mem_Upper(Mi)",
    # Storage (2 columns)
    "PVC_Access_Mode",
    "PVC_Count",
    # Container count (1 column)
    "Container_Count",
    # Labels and tags (2 columns)
    "Key_Labels",
    "Detected_Issues",  # Comma-separated issue flags
]

# Total: 44 columns (40 + 4 VPA columns; appended, so existing CSV readers
# that key by column name are unaffected).

# ══════════════════════════════════════════════════════════════════════════════
# Kubernetes Version Support
# ══════════════════════════════════════════════════════════════════════════════

# Kubernetes version where in-place VPA becomes available
K8S_VPA_IN_PLACE_MIN_VERSION = "1.33"

# ══════════════════════════════════════════════════════════════════════════════
# Time Range Defaults
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_PROMETHEUS_TIME_RANGE = "7d"  # 7 days of historical data
DEFAULT_PROMETHEUS_PORT = 9091  # Local port-forward port

# ══════════════════════════════════════════════════════════════════════════════
# Recommendation Confidence Scoring
# ══════════════════════════════════════════════════════════════════════════════
#
# These tune `_confidence_score()` in simple_analyzer.py. The score is a
# heuristic 0.0–1.0 rollup of the analyzer's data-quality signals so
# downstream tooling can gate ("only auto-act on >= HIGH_CONF_THRESHOLD").
# Pick a base by Prometheus availability, subtract penalties for noisy
# signals, add a small bonus when limits are set, clamp into [0.0, 1.0],
# then bucket into bands.

# Hard floor when INSUFFICIENT_DATA is set — no other heuristic can
# salvage a recommendation built on missing/misleading data.
INSUFFICIENT_DATA_SCORE = 0.10

# Base score by data source.
BASE_SCORE_PROMETHEUS = 0.85
BASE_SCORE_NO_PROM = 0.55

# Penalties applied to the base score (subtracted).
BURSTY_PENALTY = 0.20  # peaks not captured (Cassandra/Kafka/etc.)
GC_PENALTY = 0.15  # JVM/Node — memory shape distorted
RESTART_PENALTY = 0.15  # crash-loop noise contaminates metrics
SINGLE_REPLICA_PENALTY = 0.05  # no peer averaging

# Threshold above which restart_rate is treated as crash-loop noise
# (per day). Bound to UNSTABLE_RESTART_RATE_THRESHOLD so the two stay
# in sync — they encode the same "this is too unstable to trust the
# metrics" judgment and would silently disagree if someone bumped one
# without the other.
RESTART_RATE_THRESHOLD = UNSTABLE_RESTART_RATE_THRESHOLD

# Bonus when both CPU and memory limits are set — saturation is then
# observable through metrics-server, so the data we have is more
# meaningful even without Prometheus.
LIMITS_BONUS = 0.05

# Bonus when a VerticalPodAutoscaler recommendation drives the numbers — a
# controller-computed target built from long-horizon usage + OOM history is a
# stronger right-sizing signal than our own single-window estimate.
VPA_CONFIDENCE_BONUS = 0.10

# Band boundaries on the final clamped score.
HIGH_CONF_THRESHOLD = 0.75
MEDIUM_CONF_THRESHOLD = 0.50
