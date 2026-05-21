# Deployment Guide (Container + Helm)

This guide covers running K8s Scaling Advisor as a self-contained container in Kubernetes.

## Secure image pipeline (recommended)

Use the release workflow to build, scan, sign, and publish:

- Workflow: `.github/workflows/release-image.yml`
- Trigger: push a Git tag like `v3.0.1` (or run `workflow_dispatch`)
- Registry: `ghcr.io/<owner>/<repo>`
- Output: immutable image digest + Helm install snippet in workflow artifact (`image-release.txt`)

Security controls in the workflow:
- SBOM + SLSA provenance attestation via BuildKit (`sbom: true`, `provenance: mode=max`)
- Trivy vulnerability scan (fails on HIGH/CRITICAL findings)
- Keyless Cosign signing with GitHub OIDC identity
- Signature verification step in CI

## 1) Build and push the image (manual fallback)

From repository root:

```bash
docker build -f Containerfile -t ghcr.io/<your-org>/k8s-scaling-advisor:3.0.0 .
docker push ghcr.io/<your-org>/k8s-scaling-advisor:3.0.0
```

The image includes:
- Python runtime
- project package + `k8s-advisor` CLI
- `kubectl` (used by Prometheus auto-detection/port-forward flow)

## 2) Deploy with Helm

Chart path:

```text
charts/k8s-scaling-advisor
```

### Cluster-wide deployment (default)

```bash
helm upgrade --install k8s-scaling-advisor ./charts/k8s-scaling-advisor \
  --namespace platform-observability \
  --create-namespace \
  --set image.digest=sha256:<published-digest>
```

This creates:
- `ServiceAccount`
- `ClusterRole` + `ClusterRoleBinding`
- `CronJob` running `report --all-namespaces --format md,json` daily

### Namespace-scoped deployment

When you want the advisor to only scan its own namespace (lower blast
radius for the credential):

```bash
helm upgrade --install k8s-scaling-advisor ./charts/k8s-scaling-advisor \
  --namespace platform-observability \
  --create-namespace \
  --set image.digest=sha256:<published-digest> \
  --set rbac.clusterWide=false \
  --set-string 'args[0]=report' \
  --set-string 'args[1]=-n' \
  --set-string 'args[2]={{ .Release.Namespace }}' \
  --set-string 'args[3]=--format' \
  --set-string 'args[4]=md,json'
```

This uses a namespace-scoped `Role` + `RoleBinding` instead.

## 3) Prefer immutable digests over tags

The chart supports both tag and digest:

- `image.tag`: mutable, useful for dev
- `image.digest`: immutable, recommended for prod

If both are provided, digest is used.

## 4) Configure schedule

Example:

```bash
# Every 6 hours
--set cronjob.schedule="0 */6 * * *"
```

Reports are written inside the pod to `/app/reports` (`emptyDir`, ephemeral).
Capture them via `kubectl logs` or by adding a sidecar that ships
markdown/JSON to your destination of choice (S3, Slack, ticketing system, etc.)
before the Job pod terminates.

## 5) Verify published image signature (optional, recommended)

```bash
cosign verify \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  --certificate-identity-regexp "https://github.com/<owner>/<repo>/.github/workflows/release-image.yml@refs/tags/v.*" \
  ghcr.io/<owner>/<repo>@sha256:<published-digest>
```

## 6) Trigger an immediate run

```bash
kubectl create job \
  --from=cronjob/k8s-scaling-advisor-k8s-scaling-advisor \
  k8s-scaling-advisor-manual-$(date +%s) \
  -n platform-observability
```

## 7) Inspect results

```bash
# Recent jobs
kubectl get jobs -n platform-observability --sort-by=.metadata.creationTimestamp

# Logs from a job
kubectl logs -n platform-observability job/<job-name>
```

If persistence is enabled, reports are also available in the mounted PVC.

## RBAC guidance

- Prefer **namespace-scoped** mode whenever possible.
- Use **cluster-wide** only for central platform operations.
- If running namespace-scoped, pass explicit `-n <namespace>` in chart args.
- Avoid broad permissions unless your workflow requires cross-namespace collection.
