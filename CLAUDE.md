# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

K8s Scaling Advisor is a self-contained Kubernetes resource optimization toolkit that analyzes workload metrics and generates HPA/VPA/resource recommendations. The tool has **dual-mode operation**: it works with enhanced Prometheus metrics when available, but gracefully degrades to kubectl metrics-server-only mode when Prometheus is unavailable.

## Core Architecture

### Two-Phase Design

1. **Collection Phase** (`main.py` → `k8s_advisor/collector/`)
   - Gathers 45 metrics per workload from Kubernetes API and optionally Prometheus (incl. optional VPA recommendation)
   - Auto-detects Prometheus availability (5 methods: CRDs, service grep, labels, operator, namespaces)
   - Outputs CSV with all data to `reports/` directory

2. **Analysis Phase** (`k8s_advisor/simple_analyzer.py`)
   - Self-contained analyzer that detects Prometheus availability from CSV data
   - Adapts recommendations based on available metrics
   - Generates markdown reports and optional PNG graphs

### Critical: Dual-Mode Analysis

The analyzer must handle **two distinct data modes**:

**With Prometheus:**
- Enhanced metrics: P95, P50, Max, StdDev, throttle %, volatility CV
- Can detect CPU throttling (P0 if >5%)
- Can identify memory leaks via coefficient of variation
- P95-based recommendations (more accurate than averages)
- Rich rationale with actual metric values
- Memory volatility classification (LOW/MODERATE/HIGH)
- Comprehensive implementation guides

**Without Prometheus (kubectl only):**
- Basic metrics: averages from metrics-server
- Average-based recommendations with 1.25x headroom
- Cannot detect throttling or memory volatility
- Still provides all sections (executive summary, implementation guide)
- Still functional for OOM detection and resource sizing
- Clear indicator in report

**Code Location:** Check CSV for `CPU_P95(m)` != 'N/A' to determine mode.

### Recommendation Basis Precedence

The numeric right-sizing base is selected in this order (see `rec_basis` on
`WorkloadAnalysis`):

1. **VPA target** (`VPA_Present` = true, target > 0) — a controller-computed,
   *headroom-inclusive* right-sizing number. Used **verbatim** (floored by
   guardrails); the profile headroom multiplier is NOT re-applied, to avoid
   double-counting headroom. Preferred because VPA already accounts for
   long-horizon usage + OOM history.
2. **Prometheus P95** × profile headroom.
3. **metrics-server avg** × profile headroom.

VPA is an *optional input signal* exactly like Prometheus — absent on most
clusters, and the analyzer degrades transparently. When VPA and Prometheus P95
disagree by more than `VPA_DISAGREEMENT_RATIO` (2x), the recommendation still
uses VPA but flags the gap for human review. A high-CV workload is never sized
below its volatility floor, even when the VPA memory target is lower. A live
VPA recommendation adds `VPA_CONFIDENCE_BONUS` to the confidence score.

## Key Commands

### Development Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .                    # Core only
pip install -e .[viz]               # With graph support
```

### CLI Commands
```bash
# Three commands only - do not add more
k8s-advisor collect -n namespace1 -n namespace2
k8s-advisor analyze reports/k8s-advisor_*.csv --graphs
k8s-advisor report -n namespace1 --graphs

# Optional flags on analyze + report:
#   --state-dir <dir>   Persist recommendation fingerprints across runs
#                       so downstream tooling can suppress duplicate
#                       Slack/Teams posts. Stored as <dir>/seen.json.
#   --profiles <yaml>   Per-namespace policy overrides (headroom,
#                       guardrail floors, efficiency thresholds,
#                       cpu_limit_policy). See k8s_advisor/profiles.py
#                       for the schema.
#   --cpu-limit-policy {neutral,burst,protect}
#                       Global CPU-limit stance under throttling.
#                       neutral (default): present both remove/widen and
#                       keep-to-protect-co-tenants, recommend no direction.
#                       A per-namespace cpu_limit_policy overrides this.
```

### Testing
```bash
# Quick validation (must pass before commits)
python3 main.py collect -n kube-system
python3 main.py analyze reports/k8s-advisor_*.csv
python3 main.py report -n kube-system --graphs

# Verify outputs
ls reports/                         # CSV and MD files
ls reports/graphs/                  # PNG files if --graphs used
```

## Critical Files & Their Roles

### `main.py` (~810 lines)
**Purpose:** CLI orchestrator - NOT the analyzer
- Three commands: collect, analyze, report
- Collection logic with Prometheus auto-detection
- Calls `k8s_advisor/simple_analyzer.py` for analysis
- **Never modify to call external scripts** - everything must be self-contained

### `k8s_advisor/simple_analyzer.py` (~1700 lines)
**Purpose:** Enhanced self-contained analysis engine
- **This is the actual analyzer** - handles both Prometheus/non-Prometheus modes
- Priority classification (P0-P3) with comprehensive rationale
- Scaling approach determination (HPA/VPA/Manual/HPA_AFTER_FIX)
- Resource recommendations with P95-based calculations
- Rich Prometheus metrics usage (P50, P95, Max, StdDev, CV%)
- Per-recommendation **confidence score** (0.0–1.0) with band (high/medium/low)
  and reasons — see `_confidence_score()`. Surfaced on every `WorkloadAnalysis`
  and rendered in the markdown report.
- Executive summary with actionable insights
- Implementation guides (HPA behavior blocks, VPA patterns)
- Works with namespace-scoped permissions (no cluster-admin required)
- Threads a `Profile` through to `analyze_workload(..., profile=...)` so
  per-namespace policy overrides take effect at the per-row level.
- Calls `visualizer.py` for optional graphs

### `k8s_advisor/idempotency.py` (~165 lines)
**Purpose:** Recommendation fingerprinting and "have we seen this before" tracking
- Activated only when `--state-dir` is passed; otherwise the analyzer's
  output is byte-identical to the no-state path.
- SHA-256 fingerprint over (namespace, deployment, priority, scaling_approach,
  recommended_cpu, recommended_mem) — truncated to 16 hex chars.
- Persists `<state-dir>/seen.json` between runs with a schema version
  (`STATE_SCHEMA_VERSION`). Bumping invalidates stale state.
- GC: entries unobserved for `STATE_GC_DAYS` (365) are pruned on merge.
- Hardened `load_state()` drops malformed entries (non-dict, missing
  timestamps, bad counts) instead of crashing — pathological JSON in
  the state file must not propagate to `merge_run()`.

### `k8s_advisor/profiles.py` (~280 lines)
**Purpose:** Per-namespace policy profiles loaded from `--profiles policies.yaml`
- `Profile` dataclass holds the knobs the analyzer reads:
  `cpu_headroom`, `mem_headroom`, `min_cpu_request_m`, `min_mem_request_mi`,
  `min_cpu_saving_m`, `cpu_over_pct`, `cpu_under_pct`, `mem_over_pct`,
  `mem_under_pct`, `cpu_limit_policy`. Defaults mirror `constants.py` so
  behavior is byte-identical without `--profiles`.
- `cpu_limit_policy` (`neutral`/`burst`/`protect`) selects the CPU-limit stance
  under throttling. `neutral` (default) presents both remove/widen and
  keep-to-protect-co-tenants with no directional recommendation. The global
  `--cpu-limit-policy` flag seeds the base profile via `load_profiles(..., base=)`
  so a per-namespace value still wins.
- `ProfileSet.for_namespace(ns)` resolves: namespace override → `default:` block → constants.py.
- Strict YAML loader: unknown top-level keys, unknown knob names,
  non-positive values, and out-of-range percentages all raise with
  the offending field named. Falsy non-mapping shapes (`default: false`,
  `namespaces: 0`) are explicitly rejected — the loader's "fail loud
  on typos" promise must hold for those cases too.
- Volatility memory step-ups take `max(profile.mem_headroom, hardcoded_floor)`
  so a tight profile can't OOM a high-CV workload.

### `k8s_advisor/analyzer/` (modular detection — test fixtures only)
**Purpose:** A clean, modular reimplementation of the detection/classification/recommendation logic that lives inline in `simple_analyzer.py`. Files: `models.py`, `detector.py`, `classifier.py`, `loader.py`, `recommender.py`.
- **Not wired into the CLI.** `main.py` and `simple_analyzer.py` do not import from this package.
- Used by `tests/test_{models,detector,classifier,loader,recommender}.py` to exercise the logic in isolation.
- Treat this as a refactor target. **All production behavior changes must still go in `simple_analyzer.py`.** Updating `analyzer/*` alone will not change the CLI output.

### `k8s_advisor/visualizer.py` (~790 lines)
**Purpose:** Graph generation (6 charts)
- Requires matplotlib/numpy/pandas
- Graceful degradation if not installed
- Outputs to `reports/graphs/`

### `k8s_advisor/constants.py` (~380 lines)
**Purpose:** Single source of truth for all thresholds
- CPU/Memory efficiency thresholds (50% over, 85% under)
- CPU guardrails (min 50m, baseline 100m, min saving 50m)
- Memory guardrail floor (`MEM_MIN_RECOMMENDED_MI = 16`)
- Stability thresholds (>2 restarts/day = unstable)
- Confidence-scoring weights and band cutoffs
- **IMPORTANT:** All analyzer logic must use these constants. New magic
  numbers belong here, not inline.

### `k8s_advisor/collector/kubernetes.py` (~705 lines)
**Purpose:** K8s API wrapper
- Uses official `kubernetes` Python client
- Functions: get_deployments(), get_statefulsets(), get_pod_metrics()
- Handles metrics-server API for current resource usage

### `k8s_advisor/collector/prometheus.py` (~1160 lines)
**Purpose:** Prometheus detection and queries
- 5 auto-detection methods
- Port-forward management
- Queries: CPU percentiles, memory volatility, restart rates
- **Critical:** Always includes `container!="",container!="POD"` filters

## Priority Classification Logic

**P0 (Blocker):**
- Missing CPU/Memory requests (request = 0)
- OOM kills detected
- CPU throttling >5% (Prometheus only)

**P1 (High):**
- Restart rate >2/day OR total restarts >5
- Memory saturation >90% of limit

**P2 (Medium):**
- Under-requested: 85-200% usage
- Over-requested: <50% usage
- RWO PVCs (architectural constraint for HPA)

**P3 (Low):**
- No issues detected

## Scaling Approach Logic

```
IF workload in EXCLUDED_DEPLOYMENTS → NONE
IF StatefulSet → VPA
IF P0 issues OR UNSTABLE → HPA_AFTER_FIX
IF RWO PVC → VPA
IF single replica → VPA
IF multi-replica AND avg_cpu > request * 0.6 → HPA
ELSE → VPA
```

## Resource Recommendation Formulas

**CPU Request:**
```python
if not set: max(50m, avg_cpu * 1.25)
if under-requested: max(50m, avg_cpu * 1.25)
if over-requested: reduce if saving >= 50m and current > 100m
```

**Memory Request:**
```python
if not set: max(16Mi, avg_mem * 1.25)
if under-requested: max(16Mi, avg_mem * 1.25)
if over-requested: max(16Mi, avg_mem * 1.25)
```

**Limits:**
```python
# CPU: throttling is caused by the LIMIT (CFS quota), not node pressure. There
# is no universally-correct fix — removing/widening the limit stops throttling
# but risks noisy-neighbor starvation on multi-tenant nodes, while keeping it
# protects co-tenants at the cost of some throttling. The stance is policy-
# driven via profile.cpu_limit_policy (neutral/burst/protect; global default
# --cpu-limit-policy). neutral (default) presents BOTH options and recommends no
# direction. If throttling is seen with NO limit on the workload, blame the
# namespace LimitRange / parent cgroup — do NOT invent a CPU limit.
# (Threshold: CPU_THROTTLE_P0_THRESHOLD_PCT.)
if wasteful (>3x request): reduce to max(request*2.0, P95*1.2)

# Memory: Increase if OOM or near limit
if OOM: max(request*1.5, P95*1.3, Max*1.2)
if >90% limit: increase by 30%
```

**Recommendation gating (best-practice robustness):**
- **Deadband** (`RECOMMENDATION_DEADBAND_PCT`, 10%): skip raises/reductions whose
  delta is under 10% of the current request — trivial moves cost a rollout for
  no real benefit and cause run-over-run churn. Complements (does not replace)
  the absolute CPU guardrails.
- **Readiness / CV gate** (`READINESS_MAX_CV_FOR_REDUCTION`): suppress numeric
  memory *reductions* when `Mem_Volatility_CV` exceeds the cut — sizing a spiky
  workload toward its mean risks OOM. Safety *raises* still fire. Prometheus-only.
- **VPA precedence:** when a VPA target drove the number (`rec_basis == "vpa"`),
  it is authoritative — it follows VPA's direction directly (bypassing the
  P95-vs-request classification), and bypasses both the deadband and the CV gate
  (the controller already models volatility). Reductions still honor the
  GC-runtime / bursty working-set guards even against a VPA target.

## WorkloadAnalysis Fields (recently added)

Beyond the core priority/scaling/recommendation fields, the dataclass
now carries:

- `confidence: float` (0.0–1.0), `confidence_band: str` ("high"/"medium"/"low"),
  `confidence_reasons: list[str]` — populated by `_confidence_score()` for
  every analysis. Surfaces in markdown when `confidence_reasons` is
  non-empty (using emptiness as the "did the scorer run" signal, since
  `confidence == 0.0` is also the dataclass default for hand-built test
  fixtures).
- `fingerprint`, `previously_seen`, `times_seen`, `first_seen` — populated
  only when `--state-dir` was passed. Drives Slack/Teams noise suppression.
- `policy_name: str` — name of the per-namespace profile that drove this
  row. `"default"` when no `--profiles` was passed; the markdown template
  hides the `[Policy: <name>]` tag for the default case to avoid noise.

## Output Formats

### CSV (45 columns)
**Location:** `reports/k8s-advisor_<cluster>_<timestamp>.csv`

Columns include: identity (4), replicas (2), CPU metrics (10), memory metrics (10), stability/restart metrics (7), HPA info (3), VPA info (4), PVC info (2), container count (1), metadata (2). See `k8s_advisor/constants.py` → `CSV_COLUMNS` for the authoritative order.

**VPA columns** (`VPA_Present`, `VPA_CPU_Target(m)`, `VPA_Mem_Target(Mi)`,
`VPA_Mem_Upper(Mi)`): populated only when a `VerticalPodAutoscaler` targets the
workload and has produced a recommendation (recommender/`Off` mode counts).
`N/A` otherwise. Appended to the schema, so existing name-keyed CSV readers are
unaffected.

### Markdown Report
**Location:** `reports/k8s-advisor_<cluster>_<timestamp>.md`

Sections:
1. Header with Prometheus status indicator
2. Priority summary (P0/P1/P2/P3 counts)
3. Scaling approach summary
4. Detailed per-workload analysis with rationale

### Graphs (6 PNG files)
**Location:** `reports/graphs/*.png`

1. Resource efficiency scatter plot
2. Top 10 over-requested workloads
3. Top 10 under-requested workloads
4. Priority distribution pie chart
5. Resource distribution histograms
6. Stability analysis (OOM + restarts)

## Important Constraints

### Self-Contained Requirement
**CRITICAL:** No external dependencies on other projects or directories.
- Never reference `~/workspaces/k8s-scaling-advisor/` or any external path
- Never call external scripts
- All analysis must be in `k8s_advisor/` package

### Prometheus Query Format
**CRITICAL:** Must use exact format for compatibility:
```promql
quantile(0.95, quantile_over_time(0.95,
    rate(container_cpu_usage_seconds_total{
        namespace="...",
        pod=~"...",
        container!="",          # REQUIRED
        container!="POD"        # REQUIRED
    }[5m])[7d:1m]))
```

Never skip the `container!=""` and `container!="POD"` filters.

### Namespace Filtering
Always use the `-n/--namespace` flag pattern:
```bash
k8s-advisor collect -n ns1 -n ns2    # Multiple -n flags
# NOT: --namespaces ns1,ns2           # This pattern removed
```

### Guardrails (IMPORTANT)
CPU reduction recommendations respect three guardrails:
1. **Minimum:** Never recommend <50m
2. **Baseline:** Don't reduce if current ≤100m (overhead + HPA jitter)
3. **Savings:** Don't reduce if saving <50m (meaningless toil)

**Why:** Container overhead (5-15m), HPA instability at low values, operational cost vs benefit.

## Common Pitfalls

1. **Don't create separate collection scripts** - Use `main.py collect` only
2. **Don't call legacy analyzer** - Use `simple_analyzer.py` (self-contained)
3. **Don't break Prometheus queries** - Keep exact format with double aggregation
4. **Don't ignore guardrails** - Validate against constants.py thresholds
5. **Don't assume Prometheus** - Always check for N/A values in CSV

## Cluster Name Detection
```python
# Uses kubeconfig context name - works for any cluster
contexts, active = config.list_kube_config_contexts()
cluster_name = active['name']  # "minikube", "sandbox", "production", etc.
```

## Report Naming Convention
**Pattern:** `k8s-advisor_<cluster>_<timestamp>`
- `<cluster>`: From kubeconfig context
- `<timestamp>`: YYYYMMDD_HHMMSS format

**Examples:**
- `k8s-advisor_sandbox_20260311_010804.csv`
- `k8s-advisor_production_20260311_010804.md`

## When Adding Features

**Do:**
- Add constants to `constants.py`
- Update `simple_analyzer.py` for analysis logic
- Handle both Prometheus/non-Prometheus modes
- Write to `reports/` directory only
- Test with real cluster data

**Don't:**
- Add new CLI commands (keep to collect/analyze/report)
- Create files outside `k8s_advisor/` package
- Hard-code cluster-specific logic
- Break backwards compatibility with CSV format
- Add external script dependencies
