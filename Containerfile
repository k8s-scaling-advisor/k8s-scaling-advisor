FROM python:3.12-slim

# OCI image metadata. Source labels let GHCR auto-link the image to the repo.
LABEL org.opencontainers.image.source="https://github.com/k8s-scaling-advisor/k8s-scaling-advisor" \
      org.opencontainers.image.description="Kubernetes resource optimization and autoscaling advisor" \
      org.opencontainers.image.licenses="Apache-2.0"

ARG KUBECTL_VERSION=v1.30.2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# kubectl is required for Prometheus auto-detection/port-forward paths.
# curl is build-time only and is removed before the layer is committed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && arch="$(dpkg --print-architecture)" \
    && case "$arch" in \
         amd64) kubectl_arch="amd64" ;; \
         arm64) kubectl_arch="arm64" ;; \
         *) echo "Unsupported architecture: $arch" && exit 1 ;; \
       esac \
    && curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${kubectl_arch}/kubectl" -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY . .
RUN pip install .

RUN useradd --create-home --shell /usr/sbin/nologin advisor \
    && mkdir -p /app/reports \
    && chown -R advisor:advisor /app

USER advisor

ENTRYPOINT ["k8s-advisor"]
CMD ["--help"]
