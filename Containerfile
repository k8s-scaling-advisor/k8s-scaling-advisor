# Pinned to an immutable digest so rebuilds don't drift. Bumped by
# Dependabot's docker ecosystem (or manually) — tag stays as a comment for
# human reference. Tag: python:3.13.13-slim
FROM python:3.13-slim@sha256:b04b5d7233d2ad9c379e22ea8927cd1378cd15c60d4ef876c065b25ea8fb3bf3

# OCI image metadata. Source labels let GHCR auto-link the image to the repo.
LABEL org.opencontainers.image.source="https://github.com/k8s-scaling-advisor/k8s-scaling-advisor" \
      org.opencontainers.image.description="Kubernetes resource optimization and autoscaling advisor" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# No kubectl, no curl: discovery and port-forwarding are handled by the
# `kubernetes` Python client (see k8s_advisor/collector/prometheus.py).
# Reduces image size and the supply-chain surface to just Python deps.
#
# Vuln-scanning strategy: Grype runs at release-time as a strict gate
# (release-image.yml fails on any fixed HIGH/CRITICAL) and on a daily
# cron (.github/workflows/cve-scan.yml) against the published image —
# new fixed HIGH/CRITICAL findings post-release open a tracking issue
# instead of blocking, since we don't control the upstream rebuild
# cadence. See the comment headers in those two workflow files for the
# rationale.

COPY requirements.txt requirements-viz.txt ./
RUN pip install -r requirements.txt -r requirements-viz.txt

COPY . .
# Install with [viz] so the `--graphs` CLI flag works without an
# additional pip step at runtime.
RUN pip install ".[viz]"

# UID 1000 matches `securityContext.runAsUser` in the Helm chart so volume
# permissions line up at runtime.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin advisor \
    && mkdir -p /app/reports \
    && chown -R advisor:advisor /app

USER advisor

ENTRYPOINT ["k8s-advisor"]
CMD ["--help"]
