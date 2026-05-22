# Contributing to K8s Scaling Advisor

Thank you for your interest in contributing! This document covers the process and guidelines.

## Getting Started

```bash
# Fork and clone the repo
git clone https://github.com/<your-fork>/k8s-scaling-advisor.git
cd k8s-scaling-advisor

# Set up development environment
python3 -m venv venv
source venv/bin/activate
pip install -e ".[all]"

# Install pre-commit hooks (one-time, runs ruff + actionlint + shellcheck
# + structural YAML/whitespace checks on every `git commit`)
pip install pre-commit
pre-commit install
```

The pre-commit hooks are fast (<2s on typical diffs) and catch the
classes of bugs we've actually shipped before (workflow-injection
patterns, ruff-format drift, broken YAML in chart templates). The
heavier security scans (Semgrep, CodeQL) run in CI on push.

To run the chart-lint hook (it's manual-stage so commits without Helm
installed still work), use:

```bash
pre-commit run --hook-stage manual --all-files
```

## Development Workflow

1. Create a branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. Make your changes.
3. Run tests and lint:
   ```bash
   python -m pytest tests -q
   ruff check .
   ```
4. Commit with a clear message (see conventions below).
5. Push and open a Pull Request.

## Commit Message Convention

Use the format: `<type>: <short summary>`

Types:
- `feat` -- new feature
- `fix` -- bug fix
- `docs` -- documentation only
- `refactor` -- code restructuring without behavior change
- `test` -- adding or updating tests
- `chore` -- build, CI, or tooling changes

Examples:
```
feat: add memory leak severity scoring
fix: handle N/A values in CPU throttle column
docs: update CLI usage examples
test: add edge case for StatefulSet with RWO PVC
```

## Code Style

- **Linter:** `ruff check .`
- **Formatter:** `ruff format .`
- **Type hints:** encouraged on all public functions
- **Docstrings:** Google style
- **Constants:** add to `k8s_advisor/constants.py` (single source of truth for thresholds)

## Project Structure

```text
main.py                    # CLI orchestration
k8s_advisor/
  simple_analyzer.py       # Report generator (dual-mode: Prometheus / kubectl-only)
  constants.py             # All thresholds and guardrails
  visualizer.py            # Optional graph generation
  collector/
    kubernetes.py           # Kubernetes API wrappers
    prometheus.py           # Prometheus detection and queries
  analyzer/                 # Modular analyzer components and data models
tests/                      # Unit tests (pytest)
e2e/                        # End-to-end smoke tests (require live cluster)
```

## Adding New Features

### New Metric

1. Add the collector function to `k8s_advisor/collector/kubernetes.py` or `prometheus.py`.
2. Wire it into `collect_workload_data()` in `main.py` to add the CSV column.
3. Update `k8s_advisor/simple_analyzer.py` to consume the new column.
4. Add any thresholds to `k8s_advisor/constants.py`.
5. Write tests.

### New Issue Detection

The production analyzer is `k8s_advisor/simple_analyzer.py`. The
`k8s_advisor/analyzer/` package is a parallel modular implementation kept in
sync for unit-test coverage but is **not wired into the CLI**. Update both:

1. Add the detection branch to `k8s_advisor/simple_analyzer.py` (this is what
   actually runs).
2. Mirror the change in `k8s_advisor/analyzer/detector.py` and add the
   `IssueType` to `k8s_advisor/analyzer/models.py` so the unit tests stay
   meaningful.
3. Write tests in `tests/test_detector.py`.

### New CLI Flag

The project has exactly three subcommands: `collect`, `analyze`, `report`. Do not add new subcommands. You can add flags to existing subcommands in `main.py`.

## Testing

### Unit Tests

```bash
python -m pytest tests -q                 # quick run
python -m pytest tests -v --tb=short      # verbose
python -m pytest tests --cov=k8s_advisor  # with coverage
```

### E2E Smoke Tests (require cluster access)

```bash
python e2e/smoke_test.py
python e2e/test_cli.py
```

### Manual Prometheus Check

```bash
python scripts/test_prometheus_collector.py -n default
```

## Important Constraints

- **Dual-mode:** all analysis must handle both Prometheus-enabled and kubectl-only data.
- **Namespace-scoped:** never assume cluster-admin; respect RBAC boundaries.
- **Self-contained:** no external scripts or hardcoded paths.
- **Guardrails:** CPU recommendations must respect min 50m, baseline 100m, min saving 50m.
- **Prometheus queries:** always include `container!=""` and `container!="POD"` filters.

## Pull Request Checklist

- [ ] Tests pass (`python -m pytest tests -q`)
- [ ] Lint passes (`ruff check .`)
- [ ] New code has type hints and docstrings
- [ ] Both Prometheus and non-Prometheus paths are handled
- [ ] Constants are in `constants.py`, not hardcoded
- [ ] No hardcoded paths, usernames, or environment-specific values
