# syntax=docker/dockerfile:1.7
#
# Two-stage build:
#   - builder:  python:3.12-slim (Debian) for pip install + native wheels
#   - runtime:  distroless python3-debian12:nonroot (no shell, no apt,
#               minimal packages, runs as uid 65532)
#
# Why distroless? `python:3.12-slim` ships ~120 Debian packages, most of
# which Grype will flag at LOW/MEDIUM forever (they have no fix in
# Debian and never will, but the matches keep accruing). Distroless
# carries ~10 system packages — same Python runtime, far smaller CVE
# surface. No shell means an attacker who lands code execution has
# nothing to exec.
#
# We pip-install into /install with `--prefix` so the resulting tree
# matches the layout distroless's `/usr/local` already uses, then copy
# it across. We do NOT use a venv: the venv shebang would point at the
# builder's Python, which doesn't exist in the runtime image.

# ─── Stage 1: builder ───────────────────────────────────────────────────
# Pinned to 3.11 because that's what `gcr.io/distroless/python3-debian12`
# ships. The runtime image's Python imports site-packages from a path
# that includes the minor version, so the two stages MUST agree.
FROM python:3.11-slim@sha256:2c285c669cc837aa3bcf1af23ea1932b7b5214f9c9d3aad22417446ad91cb4fb AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Build tools needed for any C extensions in our deps. Removed by leaving
# the builder stage behind — none of this lands in the final image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-viz.txt ./
RUN pip install --prefix=/install -r requirements.txt -r requirements-viz.txt

COPY . .
# Install with [viz] so the `--graphs` CLI flag works without an
# additional pip step at runtime.
RUN pip install --prefix=/install ".[viz]"

# ─── Stage 2: runtime (distroless) ──────────────────────────────────────
FROM gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5

LABEL org.opencontainers.image.source="https://github.com/k8s-scaling-advisor/k8s-scaling-advisor" \
      org.opencontainers.image.description="Kubernetes resource optimization and autoscaling advisor" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/site-packages:/app

WORKDIR /app

# Drop dependencies into a dedicated /site-packages tree (instead of
# /usr/local) and add it to PYTHONPATH explicitly. Distroless's Python
# only auto-imports from `dist-packages` paths; pip --prefix produces
# `site-packages`, so we route around the mismatch with PYTHONPATH.
# /app contains the project source so `cli.py`'s `from main import ...`
# works.
COPY --from=builder --chown=65532:65532 /install/lib/python3.11/site-packages /site-packages
COPY --from=builder --chown=65532:65532 /build /app

USER nonroot

ENTRYPOINT ["python3", "-m", "k8s_advisor.cli"]
CMD ["--help"]
