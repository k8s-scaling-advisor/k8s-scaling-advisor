# Unit Tests

This directory contains unit tests for the k8s-advisor analyzer modules.

## Test Structure

```
tests/
├── __init__.py
├── test_models.py                # Data model tests (DeploymentAnalysis, enums)
├── test_detector.py              # Issue detection logic
├── test_classifier.py            # Priority & scaling approach classification
├── test_recommender.py           # Resource recommendation formulas
├── test_loader.py                # CSV → DeploymentAnalysis loader
├── test_reporting_markdown.py    # Markdown report rendering
├── test_simple_analyzer.py       # Production simple_analyzer pipeline
├── test_analyze_api.py           # k8s_advisor.analyze public API
├── test_cli_entrypoint.py        # k8s-advisor console script wiring
├── test_cli_analyze.py           # `analyze` subcommand behavior
└── test_cli_collect_help.py      # `collect --help` surface
```

## Running Tests

### Run all unit tests
```bash
pytest tests/
```

### Run specific test file
```bash
pytest tests/test_detector.py
pytest tests/test_classifier.py
pytest tests/test_models.py
```

### Run specific test class or function
```bash
pytest tests/test_detector.py::TestDetectIssues::test_oom_killed_detection
pytest tests/test_classifier.py::TestDeterminePriority
```

### Run with verbose output
```bash
pytest tests/ -v
```

### Run with coverage (if pytest-cov installed)
```bash
pytest tests/ --cov=k8s_advisor --cov-report=term-missing
```

## Test Categories

### `test_models.py`
- Enum value validation
- Dataclass creation and defaults
- Property methods (`is_statefulset`, `has_prometheus_metrics`, etc.)
- `to_dict()` JSON serialization

### `test_detector.py`
- Issue detection for all IssueType variants
- Edge cases (zero restarts with garbage rate, OOM vs MEM_SATURATION)
- Multi-issue detection
- Defensive logic validation

### `test_classifier.py`
- Priority classification (P0, P1, P2, P3)
- Scaling approach decision tree
- K8s version support checks
- Precedence rules (StatefulSet > RWO > Single replica > P0 > etc.)

### `test_recommender.py`
- CPU/Memory request and limit recommendation formulas
- Guardrails (50m floor, 100m baseline, 50m min saving)
- Headroom math against P95 / Max / averages

### `test_loader.py`
- CSV row → `DeploymentAnalysis` parsing
- N/A handling for Prometheus columns
- Type coercion and defensive defaults

### `test_reporting_markdown.py`
- Markdown rendering of priority sections, executive summary, per-workload blocks
- Stable formatting (column widths, bullet ordering)

### `test_simple_analyzer.py`
- End-to-end coverage of the production analyzer (`k8s_advisor.simple_analyzer`)
- Both Prometheus-enabled and kubectl-only paths

### `test_analyze_api.py`
- Public `k8s_advisor.analyze` API behavior

### `test_cli_*.py`
- `k8s-advisor` console script entrypoint wiring
- `analyze` subcommand behavior on a sample CSV
- `collect --help` surface

## Writing New Tests

### Test Naming
- Test files: `test_<module>.py`
- Test classes: `Test<Functionality>`
- Test functions: `test_<scenario>`

### Example Test Pattern
```python
def test_specific_scenario(self):
    """Test description explaining what is being validated."""
    # Arrange
    analysis = self.create_baseline_analysis()
    analysis.some_field = test_value

    # Act
    result = function_under_test(analysis)

    # Assert
    assert result == expected_value
```

### Baseline Helper
Most test classes provide a `create_baseline_analysis()` method that returns a healthy DeploymentAnalysis. Modify specific fields to test different scenarios.

## CI/CD Integration

These unit tests are **fast** and should be run:
- After every code change (locally)
- In pre-commit hooks
- In CI/CD pipeline (before e2e tests)

Expected runtime: < 5 seconds for full suite

## Difference from E2E Tests

**Unit tests (`tests/`):**
- Test individual functions in isolation
- No K8s cluster required
- Fast (milliseconds per test)
- Mock/stub external dependencies

**E2E tests (`e2e/`):**
- Test full pipeline (CLI → collection → analysis)
- Require K8s cluster (real or mocked)
- Slower (seconds to minutes)
- Validate end-to-end behavior

## Requirements

Install test dependencies:
```bash
pip install pytest pytest-cov
```

Or use the virtual environment:
```bash
venv/bin/python -m pytest tests/
```
