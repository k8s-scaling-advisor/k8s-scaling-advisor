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
- Grype vulnerability scan (fails on HIGH/CRITICAL findings)
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

The image does NOT bundle `kubectl` or `curl`. Discovery and (when needed)
port-forwarding go through the `kubernetes` Python client, so the runtime
talks directly to the API server.

## 2) Deploy with Helm

Two equally-valid sources for the chart. Pick whichever fits your flow:

| Source | Use when |
|---|---|
| `oci://ghcr.io/<owner>/charts/k8s-scaling-advisor` (pinned) | Production / CI / Argo / Flux / shipping a release tag without cloning the repo |
| `./charts/k8s-scaling-advisor` (local source) | You've cloned the repo for development, you're hacking on values, or you want to pin to `main` |

Each release publishes the chart as an OCI artifact alongside the image; the
exact `--version` is printed on the GitHub Release page. The local source
always reflects the current branch.

The examples below show both invocations. Anywhere you see `./charts/...`
works, the equivalent `oci://...` works too, and vice versa.

### Cluster-wide deployment (default)

The advisor is a cluster-admin tool — its job is to look at *other* workloads
to recommend resource changes. Scanning only its own namespace is rarely
useful, so the chart defaults to cluster-wide RBAC + `--all-namespaces`.

From the OCI artifact (pinned release):

```bash
helm upgrade --install k8s-scaling-advisor \
  oci://ghcr.io/<owner>/charts/k8s-scaling-advisor \
  --version <chart-version> \
  --namespace platform-observability \
  --create-namespace \
  --set image.digest=sha256:<published-digest>
```

From a local checkout (development / unreleased changes on `main`):

```bash
helm upgrade --install k8s-scaling-advisor ./charts/k8s-scaling-advisor \
  --namespace platform-observability \
  --create-namespace \
  --set image.digest=sha256:<published-digest>
```

This creates:
- `ServiceAccount`
- `ClusterRole` + `ClusterRoleBinding` (read-only across the cluster)
- `CronJob` running `report --all-namespaces --format md,json --graphs` daily

### Namespace-scoped deployment

For paranoid / single-tenant environments, flip BOTH `rbac.clusterWide=false`
AND the args to a specific namespace:

```bash
# OCI (pinned release)
helm upgrade --install k8s-scaling-advisor \
  oci://ghcr.io/<owner>/charts/k8s-scaling-advisor \
  --version <chart-version> \
  --namespace platform-observability \
  --create-namespace \
  --set image.digest=sha256:<published-digest> \
  --set rbac.clusterWide=false \
  --set-string 'args[0]=report' \
  --set-string 'args[1]=-n' \
  --set-string 'args[2]={{ .Release.Namespace }}' \
  --set-string 'args[3]=--format' \
  --set-string 'args[4]=md,json' \
  --set-string 'args[5]=--graphs'
```

This swaps the `ClusterRole` for a namespace-scoped `Role`/`RoleBinding`.

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

Once the Job pod terminates, the `emptyDir` is gone. Three ways to
keep the reports:

- **Production:** enable the [uploader sidecar](#6-uploader-sidecar-s3-slack-http)
  (next section) — ships files to S3 / Slack / HTTP before the pod exits.
- **Ad-hoc:** [debug mode](#7-debug-mode-fetch-reports-from-a-finished-pod)
  keeps the pod alive for 30 min so you can `kubectl debug` + `kubectl cp`.
- **Quick check:** `kubectl logs <job-pod>` shows summary stats and report
  paths but not the full markdown content.

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

## 7) Debug mode: fetch reports from a finished pod

The runtime image is distroless (no shell), so a `Completed` pod can't
be `kubectl exec`'d into. For ad-hoc inspection, set
`debug.keepAlive=true` and the main container will sleep for 30 minutes
(configurable via `debug.keepAliveSeconds`) after writing the reports.

```bash
helm upgrade --install k8s-scaling-advisor ./charts/k8s-scaling-advisor \
  -n platform-observability --create-namespace \
  --set image.digest=sha256:<published-digest> \
  --set debug.keepAlive=true
```

While the pod is in the sleep phase, attach a busybox debug container
and copy the reports out via the shared process namespace:

```bash
# 1. Find the job's pod
POD=$(kubectl get pods -n platform-observability \
  -l batch.kubernetes.io/job-name=<your-job> \
  -o jsonpath='{.items[0].metadata.name}')

# 2. Inject a debugger that copies reports to its own /tmp/out
kubectl debug -n platform-observability "$POD" \
  --image=busybox --share-processes --target=advisor \
  --container=debugger --quiet \
  -- sh -c 'cp -r /proc/$(pidof python3)/root/app/reports /tmp/out && \
            ls /tmp/out && sleep 600' &

# 3. Pull the files out
kubectl cp -n platform-observability \
  "$POD":/tmp/out ./local-reports -c debugger
```

This is intentionally manual — production should use the
[uploader sidecar](#6-uploader-sidecar-s3-slack-http), which never
needs human intervention. Debug mode is for "I want to look at one
specific run on this cluster, right now."

## 8) Verify published image signature (optional, recommended)

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

Reports are written to an ephemeral `emptyDir` inside the Job pod and
disappear when the pod terminates. To retain them, enable the uploader
sidecar (S3 / Slack / HTTP — see "Uploader sidecar" above).

## RBAC guidance

- Prefer **namespace-scoped** mode whenever possible.
- Use **cluster-wide** only for central platform operations.
- If running namespace-scoped, pass explicit `-n <namespace>` in chart args.
- Avoid broad permissions unless your workflow requires cross-namespace collection.
