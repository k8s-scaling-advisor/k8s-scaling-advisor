#!/usr/bin/env bash
#
# setup-example.sh — deploy Google's Online Boutique microservices demo to a
# local Kubernetes cluster (OrbStack / minikube / kind) and run k8s-advisor
# against it to produce sample CSV + markdown outputs in examples/.
#
# Prerequisites:
#   - kubectl pointing at a local cluster (e.g. `kubectl config use-context orbstack`)
#   - metrics-server installed in the cluster
#   - Python venv set up: `python3 -m venv venv && venv/bin/pip install -e .`
#
# Usage:
#   scripts/setup-example.sh [namespace]
#
# Defaults to namespace "demo".

set -euo pipefail

NAMESPACE="${1:-demo}"
MANIFEST_URL="https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/main/release/kubernetes-manifests.yaml"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${REPO_ROOT}/venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

echo "==> kubectl context: $(kubectl config current-context)"
echo "==> Target namespace: ${NAMESPACE}"

if ! kubectl get apiservice v1beta1.metrics.k8s.io >/dev/null 2>&1; then
  echo "ERROR: metrics-server is not installed. Install it first:" >&2
  echo "  kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml" >&2
  echo "  kubectl -n kube-system patch deployment metrics-server --type='json' \\" >&2
  echo "    -p='[{\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/args/-\", \"value\": \"--kubelet-insecure-tls\"}]'" >&2
  exit 1
fi

echo "==> Creating namespace ${NAMESPACE} (if missing)"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo "==> Deploying Online Boutique"
kubectl -n "${NAMESPACE}" apply -f "${MANIFEST_URL}"

echo "==> Waiting for deployments to become available (10 min timeout)"
kubectl -n "${NAMESPACE}" wait --for=condition=available --timeout=600s deployment --all

echo "==> Letting workloads warm up for 30s"
sleep 30

echo "==> Running k8s-advisor collect"
cd "${REPO_ROOT}"
"$PYTHON" main.py collect -n "${NAMESPACE}"

LATEST_CSV="$(ls -t reports/k8s-advisor_*.csv | head -1)"
echo "==> Running k8s-advisor analyze on ${LATEST_CSV}"
"$PYTHON" main.py analyze "${LATEST_CSV}"

LATEST_MD="${LATEST_CSV%.csv}.md"
echo "==> Updating examples/online-boutique.{csv,md}"
cp "${LATEST_CSV}" examples/online-boutique.csv
cp "${LATEST_MD}" examples/online-boutique.md

echo
echo "Done. Sample outputs:"
echo "  examples/online-boutique.csv"
echo "  examples/online-boutique.md"
echo
echo "To remove the demo workload:"
echo "  kubectl delete namespace ${NAMESPACE}"
