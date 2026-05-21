#!/usr/bin/env python3
"""Smoke test - Fast validation that basic functionality works.

Run this after any code changes to quickly verify nothing is broken.

Commands are passed to subprocess.run as argument lists with shell=False
(the default), and any path needed by an inline `python -c` snippet is
passed via env var rather than interpolated into the source string.
This eliminates a class of shell-injection findings flagged by Semgrep
and CodeQL.
"""

import os
import subprocess
import sys
from pathlib import Path

# Colors for output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def print_test(name):
    print(f"\n{'=' * 60}")
    print(f"TEST: {name}")
    print("=" * 60)


def run_command(cmd, description, *, env=None):
    """Run command and return True if successful.

    cmd: list of arguments. NEVER pass a shell string here.
    env: optional dict of additional environment variables (merged with
         os.environ).
    """
    print(f"{YELLOW}→{RESET} {description}")
    try:
        merged_env = None
        if env is not None:
            merged_env = {**os.environ, **env}
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=merged_env,
        )
        if result.returncode == 0:
            print(f"{GREEN}✓{RESET} Success")
            return True
        else:
            print(f"{RED}✗{RESET} Failed (exit code {result.returncode})")
            if result.stderr:
                print(f"  Error: {result.stderr[:200]}")
            return False
    except subprocess.TimeoutExpired:
        print(f"{RED}✗{RESET} Timeout")
        return False
    except FileNotFoundError as e:
        print(f"{RED}✗{RESET} Executable not found: {e}")
        return False
    except OSError as e:
        print(f"{RED}✗{RESET} OS error: {e}")
        return False


# Inline snippets that the harness runs via `python -c`. Each reads
# PROJECT_ROOT from os.environ (passed in by run_command's env=...) so we
# never have to inject paths via string concatenation.
IMPORT_SNIPPET = """
import os, sys
sys.path.insert(0, os.environ['PROJECT_ROOT'])
from k8s_advisor.collector import kubernetes as k8s  # noqa: F401
from k8s_advisor.collector import prometheus as prom  # noqa: F401
print('Imports successful')
"""

K8S_SNIPPET = """
import os, sys
sys.path.insert(0, os.environ['PROJECT_ROOT'])
from k8s_advisor.collector import kubernetes as k8s
if k8s.load_kube_config():
    cluster = k8s.get_cluster_name()
    print(f'Cluster: {cluster}')
else:
    sys.exit(1)
"""

PROM_SNIPPET = """
import os, sys
sys.path.insert(0, os.environ['PROJECT_ROOT'])
from k8s_advisor.collector import prometheus as prom
result = prom.auto_detect_prometheus()
print(f"Prometheus available: {result['available']}")
if result['available']:
    print(f"Method: {result['method']}")
    print(f"Service: {result['service_name']}")
"""


def main():
    """Run smoke tests."""
    print(f"{GREEN}K8s Scaling Advisor - Smoke Test{RESET}")
    print("Fast validation after code changes\n")

    project_root = Path(__file__).parent.parent
    main_py = str(project_root / "main.py")
    venv_python = os.environ.get("K8S_ADVISOR_PYTHON", sys.executable)
    snippet_env = {"PROJECT_ROOT": str(project_root)}

    tests_passed = 0
    tests_failed = 0
    tests_skipped = 0

    # Test 1: CLI Help
    print_test("CLI Help")
    if run_command([venv_python, main_py, "--help"], "Show main help"):
        tests_passed += 1
    else:
        tests_failed += 1

    if run_command([venv_python, main_py, "collect", "--help"], "Show collect help"):
        tests_passed += 1
    else:
        tests_failed += 1

    if run_command([venv_python, main_py, "analyze", "--help"], "Show analyze help"):
        tests_passed += 1
    else:
        tests_failed += 1

    # Test 2: Python Imports
    print_test("Python Imports")
    if run_command(
        [venv_python, "-c", IMPORT_SNIPPET],
        "Import collector modules",
        env=snippet_env,
    ):
        tests_passed += 1
    else:
        tests_failed += 1

    # Test 3: Kubernetes Connectivity
    print_test("Kubernetes Connectivity")
    has_cluster = run_command(["kubectl", "cluster-info"], "Check kubectl connection")
    if has_cluster:
        tests_passed += 1
    else:
        tests_skipped += 1
        print(f"{YELLOW}⚠{RESET}  Kubernetes not available - skipping cluster-dependent checks")

    if has_cluster:
        if run_command(
            [venv_python, "-c", K8S_SNIPPET],
            "Load kubeconfig and get cluster name",
            env=snippet_env,
        ):
            tests_passed += 1
        else:
            tests_failed += 1
    else:
        tests_skipped += 1

    # Test 4: Prometheus Detection
    print_test("Prometheus Detection")
    if has_cluster:
        if run_command(
            [venv_python, "-c", PROM_SNIPPET],
            "Detect Prometheus",
            env=snippet_env,
        ):
            tests_passed += 1
        else:
            tests_failed += 1
    else:
        tests_skipped += 1

    # Test 5: Reports Directory
    print_test("Reports Directory")
    reports_dir = project_root / "reports"
    if reports_dir.exists():
        print(f"{GREEN}✓{RESET} reports/ directory exists")
        tests_passed += 1
    else:
        print(f"{RED}✗{RESET} reports/ directory missing")
        tests_failed += 1

    if (reports_dir / ".gitignore").exists():
        print(f"{GREEN}✓{RESET} reports/.gitignore exists")
        tests_passed += 1
    else:
        print(f"{RED}✗{RESET} reports/.gitignore missing")
        tests_failed += 1

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print("=" * 60)
    total = tests_passed + tests_failed + tests_skipped
    print(f"Passed: {GREEN}{tests_passed}/{total}{RESET}")
    print(f"Failed: {RED}{tests_failed}/{total}{RESET}")
    print(f"Skipped: {YELLOW}{tests_skipped}/{total}{RESET}")

    if tests_failed == 0:
        print(f"\n{GREEN}✓ All smoke tests passed!{RESET}")
        return 0
    else:
        print(f"\n{RED}✗ Some tests failed{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
