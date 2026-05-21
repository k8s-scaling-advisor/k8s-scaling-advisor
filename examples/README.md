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

The CSV follows a 40-column schema:

```text
1.  Cluster
2.  Namespace
3.  Workload_Type (Deployment|StatefulSet)
4.  Deployment
5.  Replicas
6.  Pod_Count
7-16.  CPU metrics (avg, request, limit, usage%, throttle%, P50, P95, max, stddev)
17-26. Memory metrics (avg, request, limit, usage%, P50, P95, max, stddev, volatility CV)
27-32. Stability metrics (OOM count, restart reason, total restarts, max/pod, rate/day, days since)
33-35. HPA information (has_hpa, min_replicas, max_replicas)
36-37. Storage (PVC access mode, PVC count)
38. Container_Count
39. Key_Labels
40. Detected_Issues (comma-separated)
```

Prometheus columns (`CPU_P50`, `CPU_P95`, `Mem_Volatility_CV`, etc.) show `N/A`
when Prometheus was not available during collection — as is the case with the
committed sample.
