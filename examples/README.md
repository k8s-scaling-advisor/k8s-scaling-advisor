# Examples

This directory contains sample CSV and markdown outputs you can analyze offline
without a live cluster.

## Files

| File | Description |
|------|-------------|
| `online-boutique.csv` | Collected metrics from Google's Online Boutique microservices demo (12 deployments, no Prometheus, kubectl-only mode) |
| `online-boutique.md` | Analyzer output for the CSV above — priority breakdown, scaling recommendations, per-workload analysis |

## Try the offline analyzer

```bash
k8s-advisor analyze examples/online-boutique.csv
```

This produces a fresh markdown report next to the CSV. Add `--graphs` to also
generate PNG charts (requires `pip install -e ".[viz]"`).

## Reproduce the example

The committed sample was produced with `scripts/setup-example.sh`, which
deploys Google's [microservices-demo](https://github.com/GoogleCloudPlatform/microservices-demo)
(Online Boutique) into a local OrbStack / minikube / kind cluster and runs the
full `collect` → `analyze` pipeline.

```bash
# 1) Point kubectl at a local cluster (OrbStack shown)
kubectl config use-context orbstack

# 2) Install metrics-server if missing (one-time setup)
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl -n kube-system patch deployment metrics-server --type='json' \
  -p='[{"op": "add", "path": "/spec/template/spec/containers/0/args/-", "value": "--kubelet-insecure-tls"}]'

# 3) Deploy demo + run analyzer
scripts/setup-example.sh

# 4) Clean up
kubectl delete namespace demo
```

The script writes outputs back to `examples/online-boutique.{csv,md}` so a new
run refreshes the committed samples.

## CSV Schema

The CSV follows a 45-column schema (see `k8s_advisor/constants.py` →
`CSV_COLUMNS` for the authoritative order):

```text
1-4.   Identity (Cluster, Namespace, Workload_Type, Deployment)
5-6.   Replicas (Replicas, Pod_Count)
7-16.  CPU metrics (avg, request, limit, usage%-of-request, usage%-of-limit,
       throttle%, P50, P95, max, stddev)
17-26. Memory metrics (avg, request, limit, usage%-of-request, usage%-of-limit,
       P50, P95, max, stddev, volatility CV)
27-33. Stability metrics (OOM count, last-restart reason, last-restart exit
       code, total restarts, max/pod, rate/day, days since)
34-36. HPA information (has_hpa, min_replicas, max_replicas)
37-40. VPA recommendation (VPA_Present, VPA_CPU_Target(m), VPA_Mem_Target(Mi),
       VPA_Mem_Upper(Mi))
41-42. Storage (PVC access mode, PVC count)
43.    Container_Count
44-45. Key_Labels, Detected_Issues (comma-separated)
```

Prometheus columns (`CPU_P50`, `CPU_P95`, `Mem_Volatility_CV`, etc.) show `N/A`
when Prometheus was not available during collection — as is the case with the
committed sample.

VPA columns (`VPA_Present`, `VPA_CPU_Target(m)`, …) show `N/A` unless a
`VerticalPodAutoscaler` targets the workload and has produced a recommendation.
They were appended to the schema, so name-keyed CSV readers are unaffected.

> **Note:** the committed `online-boutique.csv` predates the VPA columns (and
> the `LastRestart_ExitCode` column) and still has the older 40-column layout.
> Re-run `scripts/setup-example.sh` against a cluster to regenerate it with the
> full 45-column schema.
