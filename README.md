# K8s Scaling Advisor

K8s Scaling Advisor collects workload metrics from Kubernetes (and optionally Prometheus), then generates actionable scaling and resource recommendations.

It supports two analysis modes:

- **Prometheus-enabled mode**: richer recommendations using P50/P95/Max, restart rate, and memory volatility.
- **kubectl-only mode**: basic, conservative analysis using metrics-server data.

The report structure stays consistent in both modes.

## What It Does

- Collects per-workload CPU/memory requests, limits, usage, restart, HPA, PVC, and metadata into CSV
- Detects P0/P1/P2/P3 issues (missing requests, OOM risk, instability, waste, etc.)
- Recommends scaling approach (`HPA`, `VPA`, `HPA_AFTER_FIX`, `MANUAL`, `NONE`)
- Produces a markdown report with:
  - executive summary
  - detailed workload analysis
  - implementation guidance (HPA behavior policy, VPA patterns)
- Optionally generates PNG graphs

## Permission Model

- **Collect/report commands** need Kubernetes API read access in target namespaces.
- **Analyze command** is offline and only needs a CSV file.
- The analyzer is designed to work with **namespace-scoped RBAC** (no cluster-admin requirement).

## Installation

### 1) Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 2) Install package

```bash
pip install -e .
```

### 3) Optional: install visualization dependencies

```bash
pip install -e ".[viz]"
```

### 4) Optional: install development dependencies

```bash
pip install -e ".[dev]"
```

## CLI Usage

The project exposes one CLI: `k8s-advisor`.

### Collect data

```bash
# Specific namespaces
k8s-advisor collect -n ns1 -n ns2

# Glob pattern (matches against discovered namespaces)
k8s-advisor collect --namespace-pattern 'app-*'

# All namespaces (default when -n is not provided)
k8s-advisor collect
```

### Prometheus authentication (optional)

If your Prometheus endpoint requires authentication, pass credentials to `collect`
or `report`:

```bash
# Basic Auth
k8s-advisor collect -n ns1 \
  --prometheus-user "$PROM_USER" \
  --prometheus-password "$PROM_PASSWORD"

# Bearer token auth
k8s-advisor report -n ns1 --graphs \
  --prometheus-token "$PROM_TOKEN"
```

Notes:
- `kubectl port-forward` authenticates to Kubernetes, but Prometheus itself may still require auth.
- If both token and basic auth are provided, token is used first.

### Analyze existing CSV

```bash
k8s-advisor analyze reports/k8s-advisor_<cluster>_<timestamp>.csv

# with graphs
k8s-advisor analyze reports/k8s-advisor_<cluster>_<timestamp>.csv --graphs

# offline against the committed example (no cluster needed)
k8s-advisor analyze examples/online-boutique.csv
```

### Full pipeline

```bash
k8s-advisor report -n ns1 -n ns2 --graphs

# or with pattern
k8s-advisor report --namespace-pattern 'app-*' --graphs
```

## Output Files

- CSV: `reports/k8s-advisor_<cluster>_<timestamp>.csv`
- Markdown: `reports/k8s-advisor_<cluster>_<timestamp>.md`
- Graphs (optional): `reports/graphs/*.png`

## Report Priorities

- **P0**: blockers (missing requests, confirmed OOM, CPU throttling)
- **P1**: high risk (instability, memory saturation)
- **P2**: optimization opportunities
- **P3**: low/no issues

## Project Layout

```text
k8s-scaling-advisor/
├── main.py                         # CLI command orchestration
├── k8s_advisor/
│   ├── cli.py                      # console entry point
│   ├── constants.py                # thresholds/guardrails
│   ├── simple_analyzer.py          # report generator (dual-mode)
│   ├── visualizer.py               # optional graph generation
│   ├── collector/
│   │   ├── kubernetes.py           # Kubernetes API wrappers
│   │   └── prometheus.py           # Prometheus detection/queries
│   └── analyzer/                   # modular analyzer components + models
├── tests/                          # pytest unit/integration-like tests
└── e2e/                            # manual smoke scripts (not pytest-discovered)
```

## Testing

### Run unit tests (recommended)

```bash
./venv/bin/python -m pytest tests -q
```

### Run with coverage

```bash
./venv/bin/python -m pytest tests --cov=k8s_advisor --cov-report=term-missing
```

### Manual smoke scripts

```bash
./venv/bin/python e2e/smoke_test.py
./venv/bin/python e2e/test_cli.py
```

> Note: `e2e/` scripts are operational smoke checks and may require cluster connectivity.

## Local Validation Checklist

```bash
# 1) CLI help
./venv/bin/python main.py --help

# 2) Analyze sample CSV (offline)
./venv/bin/python main.py analyze examples/online-boutique.csv

# 3) Unit tests
./venv/bin/python -m pytest tests -q
```

## Troubleshooting

### `No module named kubernetes`

Install runtime dependencies:

```bash
pip install -e .
```

### `Graph generation requires matplotlib`

Install visualization extras:

```bash
pip install -e ".[viz]"
```

### `kubeconfig` / permission errors during collect

- Verify context: `kubectl config current-context`
- Verify access in namespace: `kubectl auth can-i list pods -n <namespace>`
- Analyzer still works offline with CSV:
  `k8s-advisor analyze <csv>`

## Report Example (Prometheus vs Basic)

Below is a condensed before/after showing how Prometheus enriches recommendations for the same workload.

**Without Prometheus (basic mode):**

```text
Issues: CPU_OVER_REQUESTED
Current: CPU: 160m req (avg: 2m) | Memory: 286Mi req, 768Mi limit (avg: 149Mi)
Recommended: CPU: 160m | Memory: 286Mi
Scaling: VPA
Actions:
  - Reduce CPU request to 50m
```

**With Prometheus (enhanced mode):**

```text
Issues: CPU_OVER_REQUESTED
Current: CPU: 160m req (avg: 2m, P95: 5m) | Memory: 286Mi req, 768Mi limit (avg: 148Mi, P95: 254Mi)
Recommended: CPU: 50m | Memory: 286Mi
Scaling: VPA
Actions:
  - Reduce CPU REQUEST from 160m → 50m (saves 110m)
  - Enable VPA in 'Off' mode to validate recommendations
Rationale: CPU over-requested (avg: 2m, P95: 5m, request: 160m); Memory stable (CV=2.2%)
Prometheus Metrics:
  - CPU: Avg=2m, P50=2m, P95=5m, Max=13m
  - Memory: Avg=148Mi, P50=148Mi, P95=254Mi, Max=286Mi
  - Memory Volatility: 2.2% CV
```

Prometheus mode uses P95-based sizing, detects memory leaks via CV%, and identifies CPU throttling.

## Test Coverage Goals

| Module | Target |
|--------|--------|
| `k8s_advisor/simple_analyzer.py` (production analyzer) | 80% |
| `k8s_advisor/analyzer/` (test fixtures: models, detector, classifier, loader, recommender) | 100% |
| `k8s_advisor/reporting/markdown.py` | 80% |
| `k8s_advisor/collector/kubernetes.py` | 80% |
| `k8s_advisor/collector/prometheus.py` | 80% |
| `k8s_advisor/visualizer.py` | 80% |

Unit tests run in < 5 seconds with no cluster dependency. E2E tests require a live cluster.

## FAQ

**Do I need Prometheus?**
No. The tool works without Prometheus using metrics-server only. Prometheus provides percentiles, volatility, and throttle data for better recommendations.

**How long does collection take?**
5-10 minutes for ~50 workloads with Prometheus. Without Prometheus, ~1-2 minutes.

**Can I analyze multiple clusters?**
Yes. Collect from each cluster separately (`kubectl config use-context <ctx>` then `k8s-advisor collect`), then analyze each CSV independently.

**Can I customize thresholds?**
Yes. Edit `k8s_advisor/constants.py` to change CPU/memory efficiency thresholds, guardrails, and stability parameters.

## Contributing

### Adding New Metrics

1. Add the query to `k8s_advisor/collector/kubernetes.py` or `prometheus.py`
2. Add the column to `collect_workload_data()` in `main.py`
3. Update `k8s_advisor/simple_analyzer.py` to consume the new column
4. Add constants to `k8s_advisor/constants.py`

### Adding New Detection Logic

Production detection lives in `k8s_advisor/simple_analyzer.py` — that is what
the CLI runs. Add the new check there. The parallel `k8s_advisor/analyzer/`
package (used only by unit tests) should be updated alongside so the
test-suite stays representative.

### Code Style

- Linting: `ruff`
- Formatting: Black-compatible
- Type hints encouraged
- Docstrings: Google style

## Public Repo Notes

- No hardcoded local paths are required for normal usage.
- Generated artifacts are ignored via root `.gitignore`.
- Reports can be shared safely after reviewing for environment-specific labels/names.

## License

Apache 2.0. See `LICENSE`.
