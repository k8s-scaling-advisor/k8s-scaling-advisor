# Pinned to an immutable digest so rebuilds don't drift. Bumped by
# Dependabot's docker ecosystem (or manually) — tag stays as a comment for
# human reference.
FROM python:3.12-slim@sha256:9d3abd9fc11d06998ccdbdd93b4dd49b5ad7d67fcbbc11c016eb0eb2c2194891

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

COPY requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY . .
RUN pip install .

# UID 1000 matches `securityContext.runAsUser` in the Helm chart so volume
# permissions line up at runtime.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin advisor \
    && mkdir -p /app/reports \
    && chown -R advisor:advisor /app

USER advisor

ENTRYPOINT ["k8s-advisor"]
CMD ["--help"]
