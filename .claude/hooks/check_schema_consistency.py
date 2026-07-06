#!/usr/bin/env python3
"""PostToolUse hook: guard CSV schema/doc drift.

The project's CSV schema (``k8s_advisor/constants.py`` -> ``CSV_COLUMNS``) is
stated as a column count in several docs (README, CLAUDE.md, docs/, examples/).
Those counts have silently drifted from the real schema before (a VPA change
made the real schema 45 columns while docs still said 40/44). This hook makes
that drift a hard error instead of something a human has to notice.

Wiring: registered as a PostToolUse hook (matcher ``Edit|Write|MultiEdit``) in
``.claude/settings.json``. Claude Code feeds the tool-call payload on stdin as
JSON; we only act when the edited file is one that could affect the schema or a
doc that quotes it. Exit code 2 signals a blocking error whose stderr is shown
back to the model so it can fix the mismatch.

Deliberately dependency-free (stdlib only) and fast (<50ms): it re-derives the
authoritative count by parsing the ``CSV_COLUMNS`` list literally, without
importing the package (importing would drag in optional deps and slow the
hook).
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

# Files whose edits could change the schema or the counts that describe it. If
# a tool call touches none of these, the hook is a no-op (fast path).
CONSTANTS_REL = "k8s_advisor/constants.py"
DOC_GLOBS = ("README.md", "CLAUDE.md", "docs/*.md", "examples/*.md")

# Doc phrases that state a column count, e.g. "45-column", "45 columns",
# "CSV (45 columns)", "raw 45-column collection", "45 metrics per workload".
# We capture the integer and compare it to the authoritative count. Only
# counts adjacent to a schema keyword are checked, so unrelated numbers in the
# docs are ignored.
_COUNT_RE = re.compile(
    r"(\d{2,3})[\s-]*(?:column|columns|metrics)\b",
    re.IGNORECASE,
)

# A line may intentionally cite an OLD/legacy/sample column count (e.g. the note
# that the committed sample CSV predates newer columns). Such lines carry one of
# these markers and are exempt — they are describing history, not the current
# schema.
_LEGACY_MARKERS = ("older", "predate", "legacy", "sample", "previous", "old ")


def _authoritative_count(repo_root: Path) -> int | None:
    """Parse ``CSV_COLUMNS`` from constants.py and return its length.

    Uses ``ast`` rather than importing the module so the hook stays free of the
    package's runtime dependencies. Returns None if the list can't be found
    (in which case we skip rather than block — a parse failure is not the
    contributor's bug to fix).
    """
    src = (repo_root / CONSTANTS_REL).read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "CSV_COLUMNS" not in targets:
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            return len(node.value.elts)
    return None


def _stale_docs(repo_root: Path, expected: int) -> list[tuple[str, int, str]]:
    """Return (path, count_found, line) for docs stating a wrong column count."""
    findings: list[tuple[str, int, str]] = []
    seen: set[Path] = set()
    for pattern in DOC_GLOBS:
        for path in sorted(repo_root.glob(pattern)):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            for line in path.read_text(encoding="utf-8").splitlines():
                lowered = line.lower()
                if any(marker in lowered for marker in _LEGACY_MARKERS):
                    continue  # line intentionally cites a historical count
                for m in _COUNT_RE.finditer(line):
                    count = int(m.group(1))
                    # Only flag counts in a plausible schema range so we don't
                    # trip on unrelated numbers (ports, years, percentages that
                    # happen to sit next to the word "metrics").
                    if 30 <= count <= 60 and count != expected:
                        findings.append((str(path.relative_to(repo_root)), count, line.strip()))
    return findings


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # No/garbled payload — nothing to check. Never block on hook plumbing.
        return 0

    tool_input = payload.get("tool_input") or {}
    edited = tool_input.get("file_path") or ""
    if not edited:
        return 0

    # Resolve the repo root from this hook's own location: .claude/hooks/ ->
    # repo root is two parents up. Robust regardless of the CWD Claude runs in.
    repo_root = Path(__file__).resolve().parents[2]

    edited_path = Path(edited).resolve()
    # Only run when the edit touched constants.py or a doc that quotes counts.
    touched_schema = edited_path == (repo_root / CONSTANTS_REL).resolve()
    touched_doc = False
    for pattern in DOC_GLOBS:
        if edited_path in {p.resolve() for p in repo_root.glob(pattern)}:
            touched_doc = True
            break
    if not (touched_schema or touched_doc):
        return 0

    expected = _authoritative_count(repo_root)
    if expected is None:
        return 0

    stale = _stale_docs(repo_root, expected)
    if not stale:
        return 0

    lines = [
        f"CSV schema drift detected. constants.py CSV_COLUMNS has "
        f"{expected} columns, but these docs state a different count:",
    ]
    for path, count, text in stale:
        lines.append(f"  - {path}: says {count} — {text!r}")
    lines.append(
        f"Update the stated count to {expected} (or fix CSV_COLUMNS if the "
        f"schema itself is wrong). See k8s_advisor/constants.py -> CSV_COLUMNS."
    )
    print("\n".join(lines), file=sys.stderr)
    return 2  # blocking; stderr is surfaced to the model


if __name__ == "__main__":
    sys.exit(main())
