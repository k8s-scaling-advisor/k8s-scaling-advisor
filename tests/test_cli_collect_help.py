"""Tests for collect CLI help options."""

import subprocess
import sys
from pathlib import Path


def test_collect_help_includes_prometheus_auth_flags():
    project_root = Path(__file__).resolve().parent.parent
    main_py = project_root / "main.py"

    result = subprocess.run(
        [sys.executable, str(main_py), "collect", "--help"],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert "--prometheus-user" in result.stdout
    assert "--prometheus-password" in result.stdout
    assert "--prometheus-token" in result.stdout
