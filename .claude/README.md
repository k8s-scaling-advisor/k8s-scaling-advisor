# Project Claude Code assets

These are **project-scoped** Claude Code agents and hooks, checked into the
repo. Anyone who clones this project and opens it in Claude Code gets them
automatically — they are unique to this repository and are not installed
globally on anyone's machine. Nothing needs to be fetched from the internet.

## Agents (`.claude/agents/`)

Claude auto-delegates to these based on their `description`; you can also invoke
one explicitly (e.g. `@advisor-reviewer`).

| Agent | Use it for | Access |
| --- | --- | --- |
| `advisor-reviewer` | Reviewing a diff against this project's invariants (constants-only thresholds, dual-mode Prometheus/kubectl, VPA-as-input precedence, guardrails, self-contained rule, CSV backward-compat) plus general Python correctness. | read-only |
| `ci-parity` | "Will CI pass?" — reproduces `.github/workflows/ci.yml` (ruff format/check, pytest, helm lint/template) locally before you push. | read-only + Bash |
| `collector-analyzer-helper` | Implementing a new metric/CSV column/recommendation rule in the right place with the project's rules enforced. | read/write |

## Hooks (`.claude/settings.json` → `.claude/hooks/`)

Stdlib-only Python, no dependencies. Both are advisory tripwires, not gates you
can't clear — they print a fixable message and exit non-zero so Claude corrects
before proceeding.

| Hook | Event | What it catches |
| --- | --- | --- |
| `check_schema_consistency.py` | PostToolUse (Edit/Write) | The stated CSV column count in README/CLAUDE/docs/examples drifting from `constants.py` → `CSV_COLUMNS`. This exact drift has shipped before. |
| `ci_parity_check.py` | Stop | Ending a session with `ruff format`/`ruff check` failures — i.e. leaving CI red on lint. Skips silently if `ruff` isn't installed. |

Full `pytest` stays in the git `pre-push` hook (`.pre-commit-config.yaml`), not
here, so it doesn't run on every session Stop.

## What's NOT committed

`.claude/settings.local.json` holds each developer's personal overrides
(permission grants, etc.) and is git-ignored. Keep shared configuration in
`settings.json`; keep personal configuration in `settings.local.json`.
