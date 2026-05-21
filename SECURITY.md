# Security Policy

## Supported Versions

K8s Scaling Advisor is pre-1.0. Security fixes are applied to `main` only;
released versions follow as point releases.

| Version | Supported          |
| ------- | ------------------ |
| `main`  | :white_check_mark: |
| `< 1.0` | :white_check_mark: |

## Reporting a Vulnerability

**Please do not open a public issue for security reports.**

Use GitHub's private vulnerability reporting:

1. Go to the [Security tab](../../security) of this repository.
2. Click **Report a vulnerability**.
3. Provide:
   - A description of the issue and its impact.
   - Steps to reproduce, ideally with a minimal example.
   - Affected version(s) / commit SHA.
   - Any logs, screenshots, or PoC code that help triage.

A maintainer will acknowledge within **5 business days** and provide a
remediation timeline within **10 business days**.

## Scope

Issues we treat as security-relevant:

- Code execution from a crafted CSV / Prometheus response.
- Credential leakage from collected metadata into reports or logs.
- Path traversal / arbitrary file write via the report renderer.
- Supply-chain regressions in `requirements*.txt` or GitHub Actions workflows.

Issues we treat as **non-security** functional bugs (please open a normal issue):

- Wrong rightsizing recommendations.
- Incorrect priority classification.
- Missing or noisy graphs.
- Broken markdown rendering.

## Disclosure

We follow coordinated disclosure: once a fix is available, we publish a
GitHub Security Advisory and credit the reporter unless they prefer
anonymity.
