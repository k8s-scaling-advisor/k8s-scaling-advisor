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
    "tests (3.10)"
    "tests (3.11)"
    "tests (3.12)"
    "tests (3.13)"
    "lint"
    "container-build"
    "chart-lint"
    "CodeQL (python)"
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
    "require_code_owner_reviews": false,
    "required_approving_review_count": 0,
    "require_last_push_approval": false
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

# ─── Repo-wide settings (separate from branch protection) ───────────────
# `delete_branch_on_merge` removes the source branch automatically when a
# PR is merged. Keeps the branch list clean, especially for Dependabot.
gh api -X PATCH "repos/${REPO}" \
    -H "Accept: application/vnd.github+json" \
    -f delete_branch_on_merge=true >/dev/null

echo "==> delete_branch_on_merge enabled."
echo
echo "Verify at: https://github.com/${REPO}/settings/branches"
echo
echo "What this does:"
echo "  - Direct pushes to main are blocked (even for admins)"
echo "  - Every change requires a PR (review count: 0 — see REPO_SETUP.md)"
echo "  - All required CI checks must pass before merge"
echo "  - Linear history enforced (no merge commits via UI)"
echo "  - Signed commits required"
echo "  - Force-push and branch deletion disabled"
echo "  - Conversation resolution required before merge"
echo "  - Source branches are auto-deleted on merge"
echo
echo "Solo-maintainer note: required_approving_review_count is 0 because"
echo "GitHub does not allow self-approval. When a co-maintainer joins,"
echo "raise it to 1 via Settings -> Branches in the UI, or re-run this"
echo "script after editing the value above."
