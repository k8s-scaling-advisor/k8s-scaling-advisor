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

## 5) Where the reports live

Each Job pod writes reports to **`/app/reports`** inside its `emptyDir`
volume. The advisor produces:

- `k8s-advisor_<cluster>_<timestamp>.csv` — raw 40-column collection
- `k8s-advisor_<cluster>_<timestamp>.md` — markdown summary
- `k8s-advisor_<cluster>_<timestamp>.json` — same data, machine-readable

Once the Job pod terminates, the `emptyDir` is gone. To retain the
reports, enable the **uploader sidecar** (next section) — or copy them
out manually before `ttlSecondsAfterFinished` expires (default 24h):

```bash
kubectl cp -n platform-observability \
  <job-pod>:/app/reports/. ./local-reports/
```

## 6) Uploader sidecar (S3, Slack, HTTP)

Opt-in via `uploader.enabled=true`. Uses a Kubernetes 1.29+ native
sidecar (initContainer with `restartPolicy: Always`) so the pod
terminates cleanly when the main container finishes.

The advisor writes a `.done` sentinel file when its analysis is
complete. The sidecar polls for that sentinel, ships the reports, and
exits.

### S3

```bash
helm upgrade --install k8s-scaling-advisor ./charts/k8s-scaling-advisor \
  -n platform-observability --create-namespace \
  --set image.digest=sha256:<published-digest> \
  --set uploader.enabled=true \
  --set uploader.kind=s3 \
  --set uploader.s3.bucket=my-reports-bucket \
  --set uploader.s3.prefix=k8s-advisor \
  --set uploader.s3.region=us-east-1
```

Credentials come from the pod's ServiceAccount via IRSA / Workload
Identity / Pod Identity. For static credentials, mount a Secret via
`uploader.extraEnv`:

```yaml
uploader:
  extraEnv:
    - name: AWS_ACCESS_KEY_ID
      valueFrom: { secretKeyRef: { name: aws-creds, key: access_key } }
    - name: AWS_SECRET_ACCESS_KEY
      valueFrom: { secretKeyRef: { name: aws-creds, key: secret_key } }
```

S3-compatible (MinIO, R2, GCS via HMAC):

```bash
--set uploader.s3.endpointUrl=https://s3.example.com
```

### Slack

Create a Slack Bot/User token with `files:write`. Store it in a Secret:

```bash
kubectl create secret generic slack-token \
  -n platform-observability \
  --from-literal=token=xoxb-...
```

Install:

```bash
helm upgrade --install k8s-scaling-advisor ./charts/k8s-scaling-advisor \
  -n platform-observability --create-namespace \
  --set image.digest=sha256:<published-digest> \
  --set uploader.enabled=true \
  --set uploader.kind=slack \
  --set uploader.slack.channel=C0123456789 \
  --set uploader.slack.tokenSecret.name=slack-token \
  --set uploader.slack.markdownOnly=true
```

`channel` must be a Channel ID (starts with `C`), not a name. The bot
must be a member of the channel.

### Generic HTTP (webhooks, ticketing, custom collectors)

The sidecar POSTs each report file as `multipart/form-data` with the
field name `file`:

```bash
helm upgrade --install k8s-scaling-advisor ./charts/k8s-scaling-advisor \
  -n platform-observability --create-namespace \
  --set image.digest=sha256:<published-digest> \
  --set uploader.enabled=true \
  --set uploader.kind=http \
  --set uploader.http.url=https://collector.example.com/upload \
  --set 'uploader.http.headers.Authorization=Bearer <token>'
```

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
