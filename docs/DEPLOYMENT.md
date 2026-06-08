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

## Image variants

Each release publishes two image tags:

| Variant | Tag | Size | Use case |
|---------|-----|------|----------|
| **slim** _(default)_ | `<version>` | ~170 MB | `collect`, `analyze`, `report` — no graph support |
| **full** | `<version>-full` | ~404 MB | Adds `--graphs` support (matplotlib / pandas / numpy) |

Both variants are built on [Chainguard's distroless Python](https://images.chainguard.dev/directory/image/python/overview) and carry zero OS-level CVEs. The `latest` and `latest-full` floating tags always point to the most recent release.

```bash
# Slim (default) — collect, analyze, report
docker pull ghcr.io/<owner>/k8s-scaling-advisor:<version>

# Full — use this if you need --graphs
docker pull ghcr.io/<owner>/k8s-scaling-advisor:<version>-full
```

The Helm chart defaults to the slim image. To use the full image, override the digest at install time (see [Deploy with Helm](#2-deploy-with-helm) below).

## 1) Build and push the image (manual fallback)

From repository root:

```bash
# Slim image
docker build -f Containerfile.chainguard --target slim \
  -t ghcr.io/<your-org>/k8s-scaling-advisor:3.0.0 .

# Full image (with --graphs support)
docker build -f Containerfile.chainguard --target full \
  -t ghcr.io/<your-org>/k8s-scaling-advisor:3.0.0-full .

docker push ghcr.io/<your-org>/k8s-scaling-advisor:3.0.0
docker push ghcr.io/<your-org>/k8s-scaling-advisor:3.0.0-full
```

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

- **Production:** enable the [uploader sidecar](#6-uploader-sidecar-s3-slack-http-teams)
  (next section) — ships files to S3 / Slack / HTTP / Teams before the pod exits.
- **Ad-hoc:** [debug mode](#7-debug-mode-fetch-reports-from-a-finished-pod)
  keeps the pod alive for 30 min so you can `kubectl debug` + `kubectl cp`.
- **Quick check:** `kubectl logs <job-pod>` shows summary stats and report
  paths but not the full markdown content.

## 6) Uploader sidecar (S3, Slack, HTTP, Teams)

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

### Microsoft Teams (with SharePoint upload)

The Teams uploader uses the **Microsoft Graph API** to upload reports
to a channel's **Files** tab (which is a SharePoint document library
behind the scenes), then posts a chat message in that channel linking
to the freshly-uploaded files.

This is heavier to set up than Slack — Teams doesn't expose a simple
incoming webhook for file delivery, so we go through Azure AD
client-credentials. One-time setup, then it works on every release.

#### Azure AD app setup (one-time, requires tenant admin)

1. Go to `https://portal.azure.com` → **Microsoft Entra ID** →
   **App registrations** → **New registration**.
   - Name: `k8s-scaling-advisor` (any).
   - Supported account types: **Accounts in this organizational
     directory only** (single tenant).
   - Click **Register**.
2. Note the **Application (client) ID** and **Directory (tenant) ID**
   from the Overview page.
3. **API permissions** → **Add a permission** → **Microsoft Graph**
   → **Application permissions**:
   - `Files.ReadWrite.All`
   - `ChannelMessage.Send`
4. Click **Grant admin consent for <tenant>**. Both permissions must
   show **Granted** (green checkmark).
5. **Certificates & secrets** → **New client secret**.
   - Description: anything; Expires: pick the longest acceptable for
     your security policy (24 months max).
   - **Copy the Value** immediately. You won't see it again.

#### Resolve team and channel IDs

In Teams, click the channel → **More options** (⋯) → **Get link to
channel**. The link looks like:

```text
https://teams.microsoft.com/l/channel/19%3a<CHANNEL_ID>%40thread.tacv2/
General?groupId=<TEAM_ID>&tenantId=<TENANT_ID>
```

URL-decode `<CHANNEL_ID>` — the colons (`%3a`) become `:`, the at-sign
(`%40`) becomes `@`. The result starts with `19:` and ends with
`@thread.tacv2`.

#### Create the Kubernetes Secret

```bash
kubectl create secret generic teams-graph-secret \
  -n platform-observability \
  --from-literal=clientSecret='<the-secret-Value-from-step-5>'
```

#### Install with Teams uploader enabled

```bash
helm upgrade --install k8s-scaling-advisor ./charts/k8s-scaling-advisor \
  -n platform-observability --create-namespace \
  --set image.digest=sha256:<published-digest> \
  --set uploader.enabled=true \
  --set uploader.kind=teams \
  --set uploader.teams.tenantId=<DIRECTORY_TENANT_ID> \
  --set uploader.teams.clientId=<APPLICATION_CLIENT_ID> \
  --set uploader.teams.teamId=<TEAM_ID> \
  --set uploader.teams.channelId='<CHANNEL_ID>' \
  --set uploader.teams.clientSecretSecret.name=teams-graph-secret
```

Default `uploader.teams.markdownOnly=true` — only the `.md` report
gets uploaded to keep the channel readable. Set to `false` to also
ship CSV / JSON / PNGs.

The default sidecar image for `kind: teams` is
`mcr.microsoft.com/azure-cli` (it ships `jq` and `python3` which the
upload script needs). Override with `uploader.image.repository` /
`.tag` if your environment requires an internal mirror.

## 7) Debug mode: fetch reports from a finished pod

A Job pod that finishes its work is reaped quickly and its `emptyDir`
(where reports live) goes with it. For ad-hoc inspection, set
`debug.keepAlive=true` and the main container will sleep for 30 minutes
(configurable via `debug.keepAliveSeconds`) after writing the reports,
so you have a window to `kubectl exec` / `kubectl cp` them out.

This is intentionally manual — production should use the
[uploader sidecar](#6-uploader-sidecar-s3-slack-http-teams), which never
needs human intervention. Debug mode is for "I want to look at one
specific run on this cluster, right now."

### Step 1: install with debug.keepAlive enabled

```bash
helm upgrade --install k8s-scaling-advisor ./charts/k8s-scaling-advisor \
  -n platform-observability --create-namespace \
  --set image.digest=sha256:<published-digest> \
  --set debug.keepAlive=true
```

### Step 2: trigger a run (don't wait for the schedule)

```bash
JOB="k8s-advisor-debug-$(date +%s)"
kubectl create job --from=cronjob/k8s-scaling-advisor "$JOB" \
  -n platform-observability
echo "JOB=$JOB"
```

### Step 3: wait until the analysis finishes and the pod enters its sleep window

Poll the log until you see the `[debug] sleeping ...` banner:

```bash
# Tail logs as the job runs; ^C once you see "sleeping NNNs":
kubectl logs -n platform-observability \
  -l "batch.kubernetes.io/job-name=$JOB" -f

# Last lines you'll see:
#   ✅ Markdown report: /app/reports/k8s-advisor_<cluster>_<ts>.md
#   ✅ JSON report: /app/reports/k8s-advisor_<cluster>_<ts>.json
#   📊 Graphs: /app/reports/graphs/
#   [debug] advisor exited rc=0; sleeping 1800s for kubectl exec / kubectl cp
#   [debug] kubectl exec -n platform-observability <pod> -- ls /app/reports
#   [debug] kubectl cp -n platform-observability <pod>:/app/reports ./local-reports
```

The two `[debug]` lines have the resolved pod name baked in via the
chart's downward-API env vars — copy/paste them rather than typing.

### Step 4: copy the reports to your laptop

```bash
POD="$(kubectl get pods -n platform-observability \
  -l "batch.kubernetes.io/job-name=$JOB" \
  -o jsonpath='{.items[0].metadata.name}')"

# Optional: list before copying
kubectl exec -n platform-observability "$POD" -- ls -la /app/reports

# Copy the whole reports dir down
kubectl cp -n platform-observability \
  "$POD":/app/reports ./reports-from-debug
```

You'll get the full set: CSV, markdown, JSON, and the graphs/ subdir.

### Step 5: clean up

The pod will exit on its own when `debug.keepAliveSeconds` runs out.
To stop it sooner:

```bash
kubectl delete job "$JOB" -n platform-observability
```

To turn debug mode off for the next run:

```bash
helm upgrade k8s-scaling-advisor ./charts/k8s-scaling-advisor \
  -n platform-observability \
  --reuse-values \
  --set debug.keepAlive=false
```

### Notes on what debug mode changes

When `debug.keepAlive=true`:
- The main container's `command` is wrapped in `/bin/sh -c '... && sleep N'`,
  so the pod stays in `Running` after the advisor finishes instead of
  immediately going to `Completed`.
- `POD_NAME` and `POD_NAMESPACE` are injected via the downward API so the
  printed `kubectl` commands have real values, not placeholders.
- Nothing else changes: same image, same RBAC, same args. The reports
  are written to the same `/app/reports` `emptyDir`.

## 8) Verify published image signature (optional, recommended)

```bash
cosign verify \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  --certificate-identity-regexp "https://github.com/<owner>/<repo>/.github/workflows/release-image.yml@refs/tags/v.*" \
  ghcr.io/<owner>/<repo>@sha256:<published-digest>
```

## 9) Trigger an immediate run

```bash
kubectl create job \
  --from=cronjob/k8s-scaling-advisor-k8s-scaling-advisor \
  k8s-scaling-advisor-manual-$(date +%s) \
  -n platform-observability
```

## 10) Inspect results

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
