#!/usr/bin/env python3
"""Run all e2e tests in sequence."""

import subprocess
import sys
from pathlib import Path

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"


def run_test_suite(script_path):
    """Run a test suite and return pass/fail."""
    script_name = script_path.name
    print(f"\n{BLUE}{'=' * 60}{RESET}")
    print(f"{BLUE}Running: {script_name}{RESET}")
    print(f"{BLUE}{'=' * 60}{RESET}")

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            timeout=300,  # 5 minute timeout per suite
        )
        if result.returncode == 0:
            print(f"{GREEN}✓ {script_name} PASSED{RESET}")
            return True
        else:
            print(f"{RED}✗ {script_name} FAILED{RESET}")
            return False
    except subprocess.TimeoutExpired:
        print(f"{RED}✗ {script_name} TIMEOUT{RESET}")
        return False
    except Exception as e:
        print(f"{RED}✗ {script_name} ERROR: {e}{RESET}")
        return False


def main():
    """Run all test suites."""
    print(f"{GREEN}{'=' * 60}{RESET}")
    print(f"{GREEN}K8s Scaling Advisor - E2E Test Suite{RESET}")
    print(f"{GREEN}{'=' * 60}{RESET}")

    e2e_dir = Path(__file__).parent

    # Test suites in order
    test_suites = [
        e2e_dir / "smoke_test.py",  # Fast smoke test
        e2e_dir / "test_cli.py",  # CLI tests
        # Add more as they're created:
        # e2e_dir / 'test_collection.py',
        # e2e_dir / 'test_analysis.py',
        # e2e_dir / 'test_integration.py',
    ]

    results = {}
    for suite in test_suites:
        if suite.exists():
            results[suite.name] = run_test_suite(suite)
        else:
            print(f"{YELLOW}⚠ Skipping {suite.name} (not found){RESET}")

    # Summary
    print(f"\n{BLUE}{'=' * 60}{RESET}")
    print(f"{BLUE}FINAL SUMMARY{RESET}")
    print(f"{BLUE}{'=' * 60}{RESET}")

    passed = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    total = len(results)

    for suite_name, passed_test in results.items():
        status = f"{GREEN}✓ PASS{RESET}" if passed_test else f"{RED}✗ FAIL{RESET}"
        print(f"  {suite_name:30} {status}")

    print(f"\n  Total: {total} suites")
    print(f"  Passed: {GREEN}{passed}{RESET}")
    print(f"  Failed: {RED}{failed}{RESET}")

    if failed == 0:
        print(f"\n{GREEN}✓ All test suites passed!{RESET}\n")
        return 0
    else:
        print(f"\n{RED}✗ Some test suites failed{RESET}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
