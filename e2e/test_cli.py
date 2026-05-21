#!/usr/bin/env python3
"""CLI Tests - Verify command-line interface works correctly.

Commands are passed to subprocess.run as lists (shell=False, the default)
so the shell never sees them. This avoids the class of injection bugs
where an interpolated path containing whitespace or shell metacharacters
gets parsed as multiple arguments.
"""

import subprocess
import sys
from pathlib import Path


def run_test(name, cmd, expected_in_output=None, should_fail=False):
    """Run a test and check output.

    cmd: list of arguments (NOT a shell string).
    """
    print(f"  Testing: {name}...", end=" ")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)

        # Check exit code
        if should_fail:
            if result.returncode != 0:
                print("✓ (expected failure)")
                return True
            else:
                print("✗ (should have failed)")
                return False
        else:
            if result.returncode != 0:
                print(f"✗ (exit code {result.returncode})")
                return False

        # Check output content
        if expected_in_output:
            output = result.stdout + result.stderr
            if expected_in_output in output:
                print("✓")
                return True
            else:
                print(f"✗ (expected '{expected_in_output}' in output)")
                return False

        print("✓")
        return True

    except subprocess.TimeoutExpired:
        print("✗ (timeout)")
        return False
    except FileNotFoundError as e:
        print(f"✗ (executable not found: {e})")
        return False
    except OSError as e:
        print(f"✗ (OS error: {e})")
        return False


def main():
    """Run CLI tests."""
    print("=" * 60)
    print("CLI Tests")
    print("=" * 60)

    project_root = Path(__file__).parent.parent
    main_py = str(project_root / "main.py")
    venv_python = str(project_root / "venv" / "bin" / "python3")

    tests_passed = 0
    tests_failed = 0

    # Test 1: Help commands
    print("\n1. Help Commands")
    tests = [
        ("Main help", [venv_python, main_py, "--help"], "K8s Scaling Advisor"),
        ("Collect help", [venv_python, main_py, "collect", "--help"], "Namespace to collect"),
        ("Analyze help", [venv_python, main_py, "analyze", "--help"], "CSV file"),
        ("Report help", [venv_python, main_py, "report", "--help"], "Generate graphs"),
    ]

    for name, cmd, expected in tests:
        if run_test(name, cmd, expected):
            tests_passed += 1
        else:
            tests_failed += 1

    # Test 2: Error handling
    print("\n2. Error Handling")
    tests = [
        ("No arguments", [venv_python, main_py], None, True),
        ("Invalid command", [venv_python, main_py, "invalid"], None, True),
        ("Analyze without CSV", [venv_python, main_py, "analyze"], None, True),
    ]

    for name, cmd, expected, should_fail in tests:
        if run_test(name, cmd, expected, should_fail):
            tests_passed += 1
        else:
            tests_failed += 1

    # Test 3: Version/info
    print("\n3. Script Info")
    tests = [
        ("Syntax check", ["python3", "-m", "py_compile", main_py], None),
    ]

    for name, cmd, expected in tests:
        if run_test(name, cmd, expected):
            tests_passed += 1
        else:
            tests_failed += 1

    # Summary
    total = tests_passed + tests_failed
    print(f"\n{'=' * 60}")
    print(f"Results: {tests_passed}/{total} passed")
    print("=" * 60)

    return 0 if tests_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
