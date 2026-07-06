#!/usr/bin/env python3
"""Stop hook: catch the CI lint failure before the session ends.

Mirrors the fast half of the ``lint`` CI job (``ruff format --check .`` and
``ruff check .``) so a session can't wrap up leaving the exact failure that
started this work — CI red on formatting. Runs on the Stop event.

Full pytest stays in the pre-push git hook (.pre-commit-config.yaml): running
~260 tests on every Stop would be slow and annoying, and the git hook already
blocks the push that triggers CI. This hook is the cheap, always-on tripwire.

Behavior:
  - ruff missing        -> skip silently (exit 0); not every contributor has it
  - format/lint clean    -> exit 0
  - drift found          -> exit 2 with a fixable message on stderr, so the
                            model is nudged to run ``ruff format .`` before it
                            actually stops.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    # Drain stdin so Claude Code doesn't see a broken pipe; payload is unused.
    with contextlib.suppress(json.JSONDecodeError, ValueError):
        json.load(sys.stdin)

    if shutil.which("ruff") is None:
        return 0  # ruff not installed locally — don't block, CI still covers it

    repo_root = Path(__file__).resolve().parents[2]

    problems: list[str] = []

    fmt = _run(["ruff", "format", "--check", "."], repo_root)
    if fmt.returncode != 0:
        problems.append(
            "ruff format --check failed. Run `ruff format .` to fix:\n" + (fmt.stdout or fmt.stderr).strip()
        )

    lint = _run(["ruff", "check", "."], repo_root)
    if lint.returncode != 0:
        problems.append(
            "ruff check failed. Run `ruff check --fix .` (or fix by hand):\n" + (lint.stdout or lint.stderr).strip()
        )

    if not problems:
        return 0

    print(
        "CI lint parity check failed before stopping — CI would go red:\n\n" + "\n\n".join(problems),
        file=sys.stderr,
    )
    return 2  # blocking; the model sees this and can fix before stopping


if __name__ == "__main__":
    sys.exit(main())
