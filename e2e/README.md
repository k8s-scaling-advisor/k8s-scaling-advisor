# End-to-End Smoke Scripts

This directory contains operational smoke checks for the K8s Scaling Advisor.
Unlike the unit tests in `tests/`, these scripts exercise the real CLI and may
require cluster connectivity.

> These scripts are **not** discovered by pytest. Run them directly with the
> repo's virtualenv Python.

## Available Scripts

| Script | Purpose | Cluster required? |
|--------|---------|-------------------|
| `smoke_test.py` | Fast end-to-end smoke check of CLI surface | No (uses fixtures) |
| `test_cli.py`   | Argparse / CLI behavior validation | No |
| `run_all_tests.py` | Convenience runner that invokes the scripts above | Inherits from above |

## Running

```bash
# Single check
./venv/bin/python e2e/smoke_test.py
./venv/bin/python e2e/test_cli.py

# All of them
./venv/bin/python e2e/run_all_tests.py
```

## When to use these vs `tests/`

- `tests/` — fast unit tests against the analyzer modules. Run on every change.
- `e2e/`   — slower CLI-level validation. Run before releases or after changes
  to `main.py`, the collectors, or the report writer.

For a full live-cluster validation, use `scripts/setup-example.sh` to deploy
Online Boutique to a local cluster (OrbStack / minikube / kind) and run the
real `collect` → `analyze` pipeline against it.
