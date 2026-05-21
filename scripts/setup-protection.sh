#!/usr/bin/env bash
#
# setup-protection.sh — apply strict branch-protection rules to `main`.
#
# Run this AFTER the first `git push -u origin main` so the branch exists
# on GitHub. Requires:
#   - gh CLI installed and authenticated (`gh auth status`)
#   - admin permission on the repository
#
# Usage:
#   scripts/setup-protection.sh                 # auto-detect repo from `git remote`
#   scripts/setup-protection.sh owner/repo      # explicit
#
# Idempotent — re-running updates the rules in place.

set -euo pipefail

# ─── Resolve the target repo ────────────────────────────────────────────
if [[ $# -ge 1 ]]; then
    REPO="$1"
else
    if ! command -v gh >/dev/null 2>&1; then
        echo "ERROR: gh CLI not installed. https://cli.github.com" >&2
        exit 1
    fi
    REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)"
    if [[ -z "$REPO" ]]; then
        echo "ERROR: could not detect repo. Pass owner/repo as an argument." >&2
        exit 1
    fi
fi

echo "==> Applying branch protection to ${REPO}:main"

# ─── Required status checks ─────────────────────────────────────────────
# These names must match the `name:` field of your workflow jobs.
REQUIRED_CHECKS=(
    "tests (3.9)"
    "tests (3.10)"
    "tests (3.11)"
    "tests (3.12)"
    "tests (3.13)"
    "lint"
)

# Build the JSON contexts array
CONTEXTS_JSON=$(printf '"%s",' "${REQUIRED_CHECKS[@]}" | sed 's/,$//')

# ─── Apply protection ───────────────────────────────────────────────────
# We use the REST API directly (gh wraps it) because branch-protection
# semantics are richer than `gh repo edit` exposes.
gh api -X PUT "repos/${REPO}/branches/main/protection" \
    -H "Accept: application/vnd.github+json" \
    --input - <<JSON
{
  "required_status_checks": {
    "strict": true,
    "contexts": [${CONTEXTS_JSON}]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1,
    "require_last_push_approval": true
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": true,
  "required_signatures": true
}
JSON

echo "==> Protection applied."
echo
echo "Verify at: https://github.com/${REPO}/settings/branches"
echo
echo "What this does:"
echo "  - Direct pushes to main are blocked (even for admins)"
echo "  - Every change requires a PR with 1 approving review"
echo "  - CODEOWNERS approval required for files they own"
echo "  - All required CI checks must pass before merge"
echo "  - Linear history enforced (no merge commits via UI)"
echo "  - Signed commits required"
echo "  - Force-push and branch deletion disabled"
