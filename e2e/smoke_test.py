#!/usr/bin/env python3
"""Smoke test - Fast validation that basic functionality works.

Run this after any code changes to quickly verify nothing is broken.
"""

import subprocess
import sys
from pathlib import Path

# Colors for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
RESET = '\033[0m'

def print_test(name):
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print('='*60)

def run_command(cmd, description):
    """Run command and return True if successful."""
    print(f"{YELLOW}→{RESET} {description}")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
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
    except Exception as e:
        print(f"{RED}✗{RESET} Exception: {e}")
        return False

def main():
    """Run smoke tests."""
    print(f"{GREEN}K8s Scaling Advisor - Smoke Test{RESET}")
    print("Fast validation after code changes\n")

    project_root = Path(__file__).parent.parent
    main_py = project_root / "main.py"
    venv_python = project_root / "venv" / "bin" / "python3"

    tests_passed = 0
    tests_failed = 0
    tests_skipped = 0

    # Test 1: CLI Help
    print_test("CLI Help")
    if run_command(f"{venv_python} {main_py} --help", "Show main help"):
        tests_passed += 1
    else:
        tests_failed += 1

    if run_command(f"{venv_python} {main_py} collect --help", "Show collect help"):
        tests_passed += 1
    else:
        tests_failed += 1

    if run_command(f"{venv_python} {main_py} analyze --help", "Show analyze help"):
        tests_passed += 1
    else:
        tests_failed += 1

    # Test 2: Python Imports
    print_test("Python Imports")
    import_test = f"""
import sys
sys.path.insert(0, '{project_root}')
from k8s_advisor.collector import kubernetes as k8s
from k8s_advisor.collector import prometheus as prom
print('Imports successful')
"""
    if run_command(f"{venv_python} -c \"{import_test}\"", "Import collector modules"):
        tests_passed += 1
    else:
        tests_failed += 1

    # Test 3: Kubernetes Connectivity
    print_test("Kubernetes Connectivity")
    has_cluster = run_command("kubectl cluster-info", "Check kubectl connection")
    if has_cluster:
        tests_passed += 1
    else:
        tests_skipped += 1
        print(f"{YELLOW}⚠{RESET}  Kubernetes not available - skipping cluster-dependent checks")

    k8s_test = f"""
import sys
sys.path.insert(0, '{project_root}')
from k8s_advisor.collector import kubernetes as k8s
if k8s.load_kube_config():
    cluster = k8s.get_cluster_name()
    print(f'Cluster: {{cluster}}')
else:
    sys.exit(1)
"""
    if has_cluster:
        if run_command(f"{venv_python} -c \"{k8s_test}\"", "Load kubeconfig and get cluster name"):
            tests_passed += 1
        else:
            tests_failed += 1
    else:
        tests_skipped += 1

    # Test 4: Prometheus Detection
    print_test("Prometheus Detection")
    prom_test = f"""
import sys
sys.path.insert(0, '{project_root}')
from k8s_advisor.collector import prometheus as prom
result = prom.auto_detect_prometheus()
print(f'Prometheus available: {{result[\'available\']}}')
if result['available']:
    print(f'Method: {{result[\'method\']}}')
    print(f'Service: {{result[\'service_name\']}}')
"""
    if has_cluster:
        if run_command(f"{venv_python} -c \"{prom_test}\"", "Detect Prometheus"):
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
    print(f"\n{'='*60}")
    print("SUMMARY")
    print('='*60)
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

if __name__ == '__main__':
    sys.exit(main())
