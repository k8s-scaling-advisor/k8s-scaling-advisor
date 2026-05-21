# K8s Scaling Advisor - Analysis Report

**Generated:** 2026-05-20 15:24:43

**Cluster:** `orbstack`

**Total Workloads:** 12

**Prometheus Metrics:** ⚠️ Not available (Basic kubectl metrics-server analysis only)

> **⚠️ LOW CONFIDENCE — kubectl-only mode.** metrics-server only exposes a ~60s rolling sample, so numeric recommendations below are based on a tiny data window. For production rightsizing, run with Prometheus enabled and **at least 7 days** of history. Workloads marked `INSUFFICIENT_DATA` were restarting or idle during collection — re-run when steady.
> **1 workload(s) flagged INSUFFICIENT_DATA.** No numeric recommendations were issued for those workloads (CrashLoop, restart-leak, or zero-signal). Investigate restart cause first.

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [Top Optimizations](#top-10)
- [Namespace Rollup](#namespace-rollup)
- [Scaling Summary](#scaling-summary)
- [Detailed Analysis](#detailed-analysis)
- [P1 (1)](#p1)
- [P2 (11)](#p2)
- [Implementation Guide](#implementation-guide)

---

## <a id="executive-summary"></a>Executive Summary

### ⚠️ Immediate Action Required

**1 workload(s) require immediate manual resource updates** before HPA/VPA can be enabled.
**0 P0 workload(s)** require immediate action: missing resource requests on active workloads, active CPU throttling, or confirmed OOM kills.

**👉 [Jump to P0 Priority Section →](#p0)**

### Priority Distribution

| Priority | Count | Description |
|----------|------:|-------------|
| **P0** | 0 | Must fix now — OOM, throttling, or missing requests on active workloads |
| **P1** | 1 | High — frequent restarts, memory saturation, severe under-provisioning |
| **P2** | 11 | Medium — resource optimization opportunities |
| **P3** | 0 | Low — no significant issues |

### Scaling Approach

| Approach | Count | Description |
|----------|------:|-------------|
| **HPA** | 0 | Ready for Horizontal Pod Autoscaler |
| **VPA** | 11 | Recommended for Vertical Pod Autoscaler |
| **HPA_AFTER_FIX** | 1 | HPA after fixing blockers |
| **MANUAL** | 0 | Manual resource updates only |
| **NONE** | 0 | Excluded from autoscaling |

_HPA workloads can optionally run VPA in `updateMode: Off` for continuous right-sizing insights._

### Fleet-Wide Impact (if all recommendations applied)

| Resource | Savings | Raises Required |
|----------|--------:|----------------:|
| CPU | 400m | 0m |
| Memory | 566Mi | 232Mi |

### Common Issues

| Issue | Count |
|-------|------:|
| CPU_OVER_REQUESTED | 12 |
| MEM_OVER_REQUESTED | 6 |
| MEM_UNDER_REQUESTED | 5 |
| INSUFFICIENT_DATA | 1 |
| UNSTABLE | 1 |

---

## <a id="top-10"></a>Top Optimizations

### Top 2 CPU Savers

| Rank | Namespace | Deployment | Savings |
|-----:|-----------|------------|--------:|
| 1 | `demo` | `loadgenerator` | 250m |
| 2 | `demo` | `adservice` | 150m |
### Top 6 Memory Savers

| Rank | Namespace | Deployment | Savings |
|-----:|-----------|------------|--------:|
| 1 | `demo` | `redis-cart` | 184Mi |
| 2 | `demo` | `loadgenerator` | 145Mi |
| 3 | `demo` | `recommendationservice` | 142Mi |
| 4 | `demo` | `shippingservice` | 36Mi |
| 5 | `demo` | `checkoutservice` | 30Mi |
| 6 | `demo` | `productcatalogservice` | 28Mi |

### Top 1 Highest Risk

These are the workloads most likely to wake someone up at 2am. Triage these first.

| Rank | Namespace | Deployment | Signals |
|-----:|-----------|------------|---------|
| 1 | `demo` | `cartservice` | 20 restarts |


---

## <a id="namespace-rollup"></a>Namespace Rollup

| Namespace | Owner(s) | Workloads | P0 | P1 | P2 | INSUFF | CPU savings | Mem savings |
|-----------|----------|----------:|---:|---:|---:|-------:|------------:|------------:|
| `demo` | _unattributed_ | 12 | 0 | 1 | 11 | 1 | 400m | 566Mi |


---


## <a id="scaling-summary"></a>Scaling Approach Summary

- **HPA Ready:** 0 workloads
- **VPA Recommended:** 11 workloads
- **HPA After Fixes:** 1 workloads

---

## <a id="detailed-analysis"></a>Detailed Analysis

### <a id="p1"></a>P1 Priority (1 workloads)

🟠 **High Priority:** Significant operational risk — frequent restarts, memory saturation.

#### `demo/cartservice` (Deployment) — INSUFFICIENT_DATA

**Issues:**
- INSUFFICIENT_DATA
- UNSTABLE
- CPU_OVER_REQUESTED
- MEM_UNDER_REQUESTED

**Current:** CPU: 200m req, 300m limit (avg: 17m) | Memory: 64Mi req, 128Mi limit (avg: 83Mi)

**Recommended:** _N/A — see INSUFFICIENT_DATA action below_

**Scaling:** HPA_AFTER_FIX

**Actions:**
- INSUFFICIENT_DATA: workload is in CrashLoop / restart-leak state (20 restarts, 20/pod) — avg/P95 metrics reflect lifecycle, not working set. Investigate restart cause (LastRestart_Reason: Completed) before any rightsizing
- Fix issues above BEFORE enabling HPA

**Rationale:** Unstable (20 restarts) - fix before HPA; CPU over-requested (9% usage); Memory under-requested (130% usage); HPA candidate after resolving blockers

---

### <a id="p2"></a>P2 Priority (11 workloads)

#### `demo/adservice` (Deployment) — LOW_CONFIDENCE

**Issues:**
- CPU_OVER_REQUESTED
- MEM_UNDER_REQUESTED

**Current:** CPU: 200m req, 300m limit (avg: 1m) | Memory: 180Mi req, 300Mi limit (avg: 243Mi)

**Recommended:** CPU: 50m | Memory: 303Mi

**Scaling:** VPA

**Actions:**
- Reduce CPU REQUEST from 200m → 50m (saves 150m)
- Raise memory REQUEST from 180Mi → 303Mi
- Raise memory LIMIT from 300Mi → 379Mi (request would otherwise exceed limit, producing an invalid PodSpec)
- Enable VPA in 'Off' mode to validate recommendations

**Rationale:** CPU over-requested (0% usage); Memory under-requested (135% usage); VPA recommended - single replica or stable load

---

#### `demo/checkoutservice` (Deployment) — LOW_CONFIDENCE

**Issues:**
- CPU_OVER_REQUESTED
- MEM_OVER_REQUESTED

**Current:** CPU: 100m req, 200m limit (avg: 0m) | Memory: 64Mi req, 128Mi limit (avg: 27Mi)

**Recommended:** CPU: 50m | Memory: 33Mi

**Scaling:** VPA

**Actions:**
- Reduce memory REQUEST from 64Mi → 33Mi
- Enable VPA in 'Off' mode to validate recommendations

**Rationale:** CPU over-requested (0% usage); Memory over-requested (42% usage); VPA recommended - single replica or stable load

---

#### `demo/currencyservice` (Deployment) — LOW_CONFIDENCE

**Issues:**
- CPU_OVER_REQUESTED
- MEM_UNDER_REQUESTED

**Current:** CPU: 100m req, 200m limit (avg: 5m) | Memory: 64Mi req, 128Mi limit (avg: 95Mi)

**Recommended:** CPU: 50m | Memory: 118Mi

**Scaling:** VPA

**Actions:**
- Raise memory REQUEST from 64Mi → 118Mi
- Enable VPA in 'Off' mode to validate recommendations

**Rationale:** CPU over-requested (5% usage); Memory under-requested (148% usage); VPA recommended - single replica or stable load

---

#### `demo/emailservice` (Deployment) — LOW_CONFIDENCE

**Issues:**
- CPU_OVER_REQUESTED
- MEM_UNDER_REQUESTED

**Current:** CPU: 100m req, 200m limit (avg: 5m) | Memory: 64Mi req, 128Mi limit (avg: 60Mi)

**Recommended:** CPU: 50m | Memory: 75Mi

**Scaling:** VPA

**Actions:**
- Raise memory REQUEST from 64Mi → 75Mi
- Enable VPA in 'Off' mode to validate recommendations

**Rationale:** CPU over-requested (5% usage); Memory under-requested (94% usage); VPA recommended - single replica or stable load

---

#### `demo/frontend` (Deployment) — LOW_CONFIDENCE

**Issues:**
- CPU_OVER_REQUESTED

**Current:** CPU: 100m req, 200m limit (avg: 6m) | Memory: 64Mi req, 128Mi limit (avg: 36Mi)

**Recommended:** CPU: 50m | Memory: 44Mi

**Scaling:** VPA

**Actions:**
- Enable VPA in 'Off' mode to validate recommendations

**Rationale:** CPU over-requested (6% usage); VPA recommended - single replica or stable load

---

#### `demo/loadgenerator` (Deployment) — LOW_CONFIDENCE

**Issues:**
- CPU_OVER_REQUESTED
- MEM_OVER_REQUESTED

**Current:** CPU: 300m req, 500m limit (avg: 11m) | Memory: 256Mi req, 512Mi limit (avg: 88Mi)

**Recommended:** CPU: 50m | Memory: 110Mi

**Scaling:** VPA

**Actions:**
- Reduce CPU REQUEST from 300m → 50m (saves 250m)
- Reduce memory REQUEST from 256Mi → 110Mi
- Enable VPA in 'Off' mode to validate recommendations

**Rationale:** CPU over-requested (4% usage); Memory over-requested (35% usage); VPA recommended - single replica or stable load

---

#### `demo/paymentservice` (Deployment) — LOW_CONFIDENCE

**Issues:**
- CPU_OVER_REQUESTED
- MEM_UNDER_REQUESTED

**Current:** CPU: 100m req, 200m limit (avg: 2m) | Memory: 64Mi req, 128Mi limit (avg: 86Mi)

**Recommended:** CPU: 50m | Memory: 107Mi

**Scaling:** VPA

**Actions:**
- Raise memory REQUEST from 64Mi → 107Mi
- Enable VPA in 'Off' mode to validate recommendations

**Rationale:** CPU over-requested (2% usage); Memory under-requested (135% usage); VPA recommended - single replica or stable load

---

#### `demo/productcatalogservice` (Deployment) — LOW_CONFIDENCE

**Issues:**
- CPU_OVER_REQUESTED
- MEM_OVER_REQUESTED

**Current:** CPU: 100m req, 200m limit (avg: 1m) | Memory: 64Mi req, 128Mi limit (avg: 29Mi)

**Recommended:** CPU: 50m | Memory: 35Mi

**Scaling:** VPA

**Actions:**
- Reduce memory REQUEST from 64Mi → 35Mi
- Enable VPA in 'Off' mode to validate recommendations

**Rationale:** CPU over-requested (1% usage); Memory over-requested (45% usage); VPA recommended - single replica or stable load

---

#### `demo/recommendationservice` (Deployment) — LOW_CONFIDENCE

**Issues:**
- CPU_OVER_REQUESTED
- MEM_OVER_REQUESTED

**Current:** CPU: 100m req, 200m limit (avg: 7m) | Memory: 220Mi req, 450Mi limit (avg: 62Mi)

**Recommended:** CPU: 50m | Memory: 77Mi

**Scaling:** VPA

**Actions:**
- Reduce memory REQUEST from 220Mi → 77Mi
- Enable VPA in 'Off' mode to validate recommendations

**Rationale:** CPU over-requested (7% usage); Memory over-requested (28% usage); VPA recommended - single replica or stable load

---

#### `demo/redis-cart` (Deployment) — LOW_CONFIDENCE

**Issues:**
- CPU_OVER_REQUESTED
- MEM_OVER_REQUESTED

**Current:** CPU: 70m req, 125m limit (avg: 2m) | Memory: 200Mi req, 256Mi limit (avg: 9Mi)

**Recommended:** CPU: 50m | Memory: 16Mi

**Scaling:** VPA

**Actions:**
- Reduce memory REQUEST from 200Mi → 16Mi
- Enable VPA in 'Off' mode to validate recommendations

**Rationale:** CPU over-requested (3% usage); Memory over-requested (4% usage); VPA recommended - single replica or stable load

---

#### `demo/shippingservice` (Deployment) — LOW_CONFIDENCE

**Issues:**
- CPU_OVER_REQUESTED
- MEM_OVER_REQUESTED

**Current:** CPU: 100m req, 200m limit (avg: 0m) | Memory: 64Mi req, 128Mi limit (avg: 22Mi)

**Recommended:** CPU: 50m | Memory: 27Mi

**Scaling:** VPA

**Actions:**
- Reduce memory REQUEST from 64Mi → 27Mi
- Enable VPA in 'Off' mode to validate recommendations

**Rationale:** CPU over-requested (0% usage); Memory over-requested (34% usage); VPA recommended - single replica or stable load

---


## <a id="implementation-guide"></a>Implementation Guide

### HPA Fleet-Wide Protection Policy

Apply this standard `behavior` block to **all** HPAs to prevent thundering herd:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: <deployment-name>
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: <deployment-name>
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 75    # 70-80 keeps HPA proactive (>100 is reactive)
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 0
      policies:
      - type: Pods
        value: 10                # Max 10 new pods per minute
        periodSeconds: 60
      - type: Percent
        value: 50                # Or max 50% increase per minute
        periodSeconds: 60
      selectPolicy: Min
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
      - type: Pods
        value: 5
        periodSeconds: 180
      - type: Percent
        value: 25
        periodSeconds: 180
      selectPolicy: Min
```

**Why this matters:**
- Prevents API server overload from simultaneous pod creation
- Avoids database connection pool exhaustion
- Provides controlled, predictable scaling behavior

### VPA Configuration

For workloads recommended for VPA:

```yaml
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: <deployment-name>-vpa
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: <deployment-name>
  updatePolicy:
    updateMode: "Off"           # Start with Off, validate for 24-48h
  resourcePolicy:
    containerPolicies:
    - containerName: '*'
      minAllowed:
        cpu: 50m
        memory: 16Mi
      maxAllowed:
        cpu: 4
        memory: 8Gi
```

**VPA Modes:**
- `Off`: Recommendations only (safe for validation)
- `Auto`: Restart pods with new recommendations
- `Recreate`: Like Auto (restart required)
- `InPlaceOrRecreate`: In-place updates if supported (K8s 1.33+)

### Rollout Strategy

**Phase 1: P0 Fixes (Week 1)** — fix missing requests on active workloads, OOM kills, CPU throttling, unstable workloads.

**Phase 2: HPA Rollout (Week 2-3)** — start with low-risk P2/P3 workloads, apply HPA behavior blocks, monitor 48h, expand gradually.

**Phase 3: VPA & Optimization (Week 4+)** — enable VPA for single-replica workloads, harvest the savings from the Top 10 lists.
