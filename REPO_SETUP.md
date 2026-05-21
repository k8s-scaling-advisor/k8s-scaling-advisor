# Repository Setup

One-time setup steps to run **after** the first push to GitHub.

---

## 1. Branch protection on `main`

After your first `git push -u origin main`, enable branch protection so
nobody (including yourself, by accident) can push directly to `main`.

### Option A — apply via the included script (recommended)

```bash
# One-time: authenticate gh
gh auth login

# Apply protection rules to `main`
./scripts/setup-protection.sh
```

The script applies the strict OSS defaults below.

### Option B — apply via GitHub UI

`Settings → Branches → Add branch protection rule`. Configure:

| Setting | Value |
|---|---|
| Branch name pattern | `main` |
| Require a pull request before merging | ✅ |
| Required approving reviews | **0** (see solo-maintainer note below) |
| Dismiss stale pull request approvals when new commits are pushed | ✅ |
| Require review from Code Owners | ❌ (would deadlock with 0 approvals) |
| Require status checks to pass before merging | ✅ |
| Require branches to be up to date before merging | ✅ |
| Required status checks | `tests (3.10)`, `tests (3.11)`, `tests (3.12)`, `tests (3.13)`, `lint`, `container-build`, `chart-lint`, `CodeQL (python)` |
| Require conversation resolution before merging | ✅ |
| Require signed commits | ✅ |
| Require linear history | ✅ |
| Do not allow bypassing the above settings (`enforce_admins`) | ✅ |
| Restrict who can push to matching branches | _empty list_ — nobody can push directly |

### Why 0 required reviews on a solo project

GitHub does not allow a PR author to approve their own PR (HTTP 422 from the
review API). With `required_approving_review_count: 1` and one maintainer,
every PR deadlocks at "merge blocked: 1 approving review needed" with no
way to satisfy it. Setting `required_approving_review_count: 0` keeps every
other gate (CI must pass, signed commits, linear history, no force-push,
no direct push to `main`) — only the human-approval requirement is dropped.
When you add a co-maintainer later, flip this back to 1 in the UI.

---

## 2. Repository-wide settings

`Settings → General`:

- **Default branch**: `main`
- **Allow merge commits**: ❌
- **Allow squash merging**: ✅ (default; squash + merge keeps history linear)
- **Allow rebase merging**: ✅
- **Automatically delete head branches**: ✅

`Settings → Pull Requests`:

- **Always suggest updating pull request branches**: ✅
- **Allow auto-merge**: ✅

---

## 3. Code scanning + Dependabot

These are configured in this repo:

- `.github/dependabot.yml` — weekly pip + Actions updates.
- `.github/workflows/codeql.yml` — Python CodeQL scan.
- `.github/workflows/ci.yml` — pytest + ruff on every push/PR.

Enable Code Scanning at:

`Settings → Code security and analysis → Code scanning → Set up`.
Pick **Default** (uses our checked-in workflow).

Enable Secret Scanning + Push Protection at the same page (free for public repos).

---

## 4. Codecov (optional)

The CI workflow uploads coverage to Codecov. To activate:

1. Sign up at https://codecov.io with your GitHub account.
2. Authorize the org / repo.
3. Add `CODECOV_TOKEN` to `Settings → Secrets and variables → Actions` if
   the repo is private (public repos work without a token).

If you skip Codecov, comment out the upload step in `.github/workflows/ci.yml`
or just ignore the warning — coverage is collected either way.

---

## 5. CODEOWNERS

The committed `CODEOWNERS` file references
`@k8s-scaling-advisor/maintainers`. Replace this with:

- A team handle if you have one (`@your-org/team-name`), OR
- Your personal handle (`@your-username`)

Then push the change. Until you do, "Require review from Code Owners" will
treat **anyone** as a valid reviewer (the team doesn't exist yet).

---

## 6. Releases

Releases are gated by a `release` label on a PR. The flow:

1. On the PR you want to release, bump
   `charts/k8s-scaling-advisor/Chart.yaml`'s `version:` field.
2. Add the `release` label to the PR.
3. Merge the PR.
4. `auto-tag-on-release-label.yml` reads the bumped version, pushes
   `v<version>` as a tag, and that tag push triggers `release-image.yml`
   to build/sign/scan/publish the image, push the chart to GHCR OCI,
   and create the GitHub Release with auto-generated notes.

PRs without the `release` label merge silently — most merges are not
releases (doc fixes, Dependabot bumps, internal refactors). This keeps
the Releases page meaningful.

If you forget to bump the version on a release-labelled PR, the tag
workflow will warn and skip rather than fail. Bump on the next release
PR.

---

## Why these defaults

- **No direct push to `main`**: forces every change through PR + review +
  CI. Removes "I just pushed it on a Friday" failures.
- **Required CI checks**: prevents merging code that broke the test suite
  or the lint gate.
- **Linear history**: makes `git bisect` and revert workflows reliable.
- **Signed commits**: small commitment (one-time GPG/SSH-key setup) that
  raises the bar against compromised tokens.
- **Dependabot**: turns supply-chain hygiene into a passive PR queue
  rather than a quarterly fire drill.
- **CodeQL**: free static-analysis baseline; flags the OWASP-class issues
  Python code commonly grows.
