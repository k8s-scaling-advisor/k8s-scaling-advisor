# Getting Started

This guide helps you run K8s Scaling Advisor in under 10 minutes.

It covers:
- Quick offline run (no cluster required)
- Live cluster run (metrics-server required, Prometheus optional)
- How to read generated outputs
- Common first-run issues

## Prerequisites

- Python 3.10+
- `kubectl` configured (for live cluster mode)
- Access to at least one namespace in your cluster

Optional:
- Prometheus (for richer P50/P95/volatility/throttle insights)

## 1) Clone and install

```bash
git clone https://github.com/<your-org>/k8s-scaling-advisor.git
cd k8s-scaling-advisor

python3 -m venv venv
source venv/bin/activate
pip install -e .
```

If you want graphs:

```bash
pip install -e ".[viz]"
```

## 2) Sanity check CLI

```bash
./venv/bin/python main.py --help
```

You should see three commands only:
- `collect`
- `analyze`
- `report`

## 3) Fastest first run (offline, no cluster)

Use the committed sample CSV:

```bash
./venv/bin/python main.py analyze examples/online-boutique.csv
```

Expected output:
- A markdown report written to `reports/`
- Filename pattern: `reports/k8s-advisor_<cluster>_<timestamp>.md`

Optional graphs:

```bash
./venv/bin/python main.py analyze examples/online-boutique.csv --graphs
```

Graph files are written to `reports/graphs/`.

## 4) Live cluster run (namespace-scoped)

Start with explicit namespaces:

```bash
./venv/bin/python main.py report -n my-namespace
```

Multiple namespaces:

```bash
./venv/bin/python main.py report -n team-a -n team-b --graphs
```

Pattern match namespaces (requires permission to list namespaces):

```bash
./venv/bin/python main.py report --namespace-pattern 'app-*'
```

## 5) Prometheus-authenticated run (optional)

If Prometheus requires auth, pass credentials on `collect` or `report`.

Basic auth:

```bash
./venv/bin/python main.py report -n my-namespace \
  --prometheus-user "$PROM_USER" \
  --prometheus-password "$PROM_PASSWORD"
```

Bearer token:

```bash
./venv/bin/python main.py report -n my-namespace \
  --prometheus-token "$PROM_TOKEN"
```

## 6) Understand the outputs

After `collect`:
- CSV: `reports/k8s-advisor_<cluster>_<timestamp>.csv`

After `analyze`/`report`:
- Markdown: `reports/k8s-advisor_<cluster>_<timestamp>.md` (default)
- JSON (machine-readable, opt-in via `--format json` or `--format md,json`):
  `reports/k8s-advisor_<cluster>_<timestamp>.json`
- Optional PNG charts: `reports/graphs/*.png`

Priority interpretation:
- `P0`: urgent blockers (missing active requests, OOM, throttling)
- `P1`: high risk (instability, memory saturation)
- `P2`: optimization opportunity
- `P3`: low/no issues

## 7) Recommended first production workflow

1. Run on one non-critical namespace.
2. Review P0/P1 findings with service owners.
3. Apply request/limit changes gradually.
4. Re-run after 24-72 hours and compare.
5. For best accuracy, enable Prometheus and collect ~7 days of signal.

## Troubleshooting

### `No module named kubernetes`

Install project dependencies:

```bash
pip install -e .
```

### `No permission to list namespaces`

Use explicit namespace flags instead of pattern/all-namespace mode:

```bash
./venv/bin/python main.py report -n my-namespace
```

### Graph generation fails

Install visualization extras:

```bash
pip install -e ".[viz]"
```

### Prometheus metrics show as `N/A`

This is expected when Prometheus is unavailable or inaccessible. The tool still works in kubectl-only mode with conservative recommendations.

## Next steps

- Review `README.md` for full architecture and design notes.
- Use `docs/DEPLOYMENT.md` to run as a container/CronJob in Kubernetes.
- Review `examples/README.md` for reproducible sample data flow.
- Review `CONTRIBUTING.md` if you plan to extend metrics or detection logic.
