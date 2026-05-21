"""Prometheus detection and query functions.

This module provides comprehensive Prometheus auto-detection and query capabilities.
All functions are designed to fail gracefully - returning None or empty dict on failure
rather than raising exceptions.

All Kubernetes API access goes through the official ``kubernetes`` Python
client (no kubectl shell-outs). Port-forwarding (when needed for the
laptop flow) uses ``kubernetes.stream.portforward.PortForward`` and a small
local TCP listener; nothing in this module spawns external processes.

Detection Strategy (in priority order):
1. CRDs (most reliable) — list custom resource definitions, filter by name
2. All services grep — list services across all namespaces, filter by name
3. Service labels — label-selector queries (e.g. app.kubernetes.io/name=prometheus)
4. Prometheus operator — list deployments, look for ``prometheus-operator``
5. Common namespaces — monitoring, prometheus, kube-system, observability
6. Manual fallback — prompt user for URL
"""

import random
import select
import socket
import threading
import time
from typing import Any

import requests
from kubernetes import client
from kubernetes.client.rest import ApiException
from kubernetes.stream import portforward

# Base URL for Prometheus queries. Mutated by ``set_prometheus_url`` once
# discovery picks an endpoint (in-cluster service DNS, port-forwarded
# localhost, or operator-supplied URL).
_PROM_BASE = "http://localhost:9091"


def set_prometheus_url(base: str) -> None:
    """Override the Prometheus base URL used by all query functions.

    ``base`` should NOT have a trailing slash and should NOT include
    ``/api/v1/query``. Examples:
        http://localhost:9091
        http://prometheus-server.monitoring.svc.cluster.local:9090
        https://prom.example.com
    """
    global _PROM_BASE
    _PROM_BASE = base.rstrip("/")


def get_prometheus_url() -> str:
    """Read the current Prometheus base URL (test/diagnostic helper)."""
    return _PROM_BASE


def _request_with_retry(
    url: str,
    params: dict[str, Any],
    timeout: int = 10,
    auth: tuple[str, str] | None = None,
    headers: dict[str, str] | None = None,
    max_attempts: int = 3,
) -> Any | None:
    """GET with exponential backoff retry on 429 / 5xx.

    Used for Prometheus queries — `requests` is imported lazily so the
    module stays importable in test environments without it.

    Returns the `requests.Response` object on success, or None after all
    attempts fail (caller decides what to do). On 4xx (other than 429) we
    return immediately; the upstream code already returns empty dict / 0.
    """
    import requests

    attempt = 0
    backoff = 0.5
    while attempt < max_attempts:
        try:
            response = requests.get(
                url,
                params=params,
                timeout=timeout,
                auth=auth,
                headers=headers or {},
            )
        except requests.RequestException:
            attempt += 1
            if attempt >= max_attempts:
                return None
            time.sleep(backoff + random.uniform(0, 0.25))
            backoff *= 2
            continue

        # Retry only on rate-limit / transient server errors.
        if response.status_code == 429 or 500 <= response.status_code < 600:
            attempt += 1
            if attempt >= max_attempts:
                return response
            time.sleep(backoff + random.uniform(0, 0.25))
            backoff *= 2
            continue

        return response
    return None


def auto_detect_prometheus() -> dict[str, Any]:
    """Comprehensive Prometheus auto-detection using multiple strategies.

    This function tries multiple detection methods in order of reliability:
    1. CRDs (most reliable indicator of Prometheus operator)
    2. All services grep (simplest and most comprehensive)
    3. Service labels (Headlamp approach)
    4. Prometheus operator deployment
    5. Common namespace search

    Returns:
        Dict with:
            available: bool - True if Prometheus found
            method: str - Detection method used (crd|service_grep|label|operator|namespace|none)
            service_name: str | None - Prometheus service name
            namespace: str | None - Prometheus namespace
            port: int | None - Service port (if detected)
            crds: List[str] - Prometheus CRDs found (if method == 'crd')
            services: List[Dict] - All matching services (if method == 'service_grep')

    Example:
        >>> result = auto_detect_prometheus()
        >>> if result['available']:
        ...     print(f"Found via {result['method']}: {result['service_name']}")
    """
    # ─────────────────────────────────────────────────────────────────────────
    # Method 1: Check for Prometheus CRDs (most reliable)
    # ─────────────────────────────────────────────────────────────────────────
    crds = detect_prometheus_crds()
    if crds:
        # CRDs found - now find the actual service
        service = find_prometheus_service_from_crds()
        if service:
            return {
                "available": True,
                "method": "crd",
                "service_name": service["name"],
                "namespace": service["namespace"],
                "port": service.get("port", 9090),
                "crds": crds,
            }

    # ─────────────────────────────────────────────────────────────────────────
    # Method 2: Grep all services for "prometheus" (simplest and most effective)
    # ─────────────────────────────────────────────────────────────────────────
    # This is often the most practical approach - just search everything!
    services = find_all_services_with_prometheus()
    if services:
        # Prioritize services with "prometheus" or "prom" in the name
        # Filter out "prometheus-operator" (we want the actual Prometheus service)
        prom_services = [
            s for s in services if "prometheus" in s["name"].lower() and "operator" not in s["name"].lower()
        ]

        if prom_services:
            # Use the first non-operator Prometheus service
            service = prom_services[0]
            return {
                "available": True,
                "method": "service_grep",
                "service_name": service["name"],
                "namespace": service["namespace"],
                "port": service.get("port", 9090),
                "services": services,  # Include all matches for user reference
            }

    # ─────────────────────────────────────────────────────────────────────────
    # Method 3: Check by service labels (Headlamp approach)
    # ─────────────────────────────────────────────────────────────────────────
    service = find_service_by_labels(
        [
            "app.kubernetes.io/name=prometheus",
            "app=prometheus",
            "app.kubernetes.io/component=prometheus",
        ]
    )
    if service:
        return {
            "available": True,
            "method": "label",
            "service_name": service["name"],
            "namespace": service["namespace"],
            "port": service.get("port", 9090),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Method 4: Check for Prometheus operator deployment
    # ─────────────────────────────────────────────────────────────────────────
    operator = detect_prometheus_operator()
    if operator:
        # Operator found - look for Prometheus service in the same namespace
        service = find_prometheus_service_in_namespace(operator["namespace"])
        if service:
            return {
                "available": True,
                "method": "operator",
                "service_name": service["name"],
                "namespace": service["namespace"],
                "port": service.get("port", 9090),
                "operator": operator,
            }

    # ─────────────────────────────────────────────────────────────────────────
    # Method 5: Search common namespaces
    # ─────────────────────────────────────────────────────────────────────────
    for ns in ["monitoring", "prometheus", "kube-system", "observability", "default"]:
        service = find_prometheus_service_in_namespace(ns)
        if service:
            return {
                "available": True,
                "method": "namespace",
                "service_name": service["name"],
                "namespace": ns,
                "port": service.get("port", 9090),
            }

    # ─────────────────────────────────────────────────────────────────────────
    # Not found
    # ─────────────────────────────────────────────────────────────────────────
    return {
        "available": False,
        "method": "none",
        "service_name": None,
        "namespace": None,
        "port": None,
    }


def _service_port(svc) -> int:
    """Pick the most likely Prometheus HTTP port from a Service object.

    Prefers a port named ``web``/``http``/``prometheus``; falls back to the
    first declared port; defaults to 9090 (vanilla Prometheus default).
    """
    ports = (svc.spec.ports or []) if svc.spec else []
    for p in ports:
        if p.name in ("web", "http", "prometheus"):
            return p.port or 9090
    return ports[0].port if ports and ports[0].port else 9090


def detect_prometheus_crds() -> list[str]:
    """Check for Prometheus CRDs in the cluster.

    CRDs are the most reliable indicator of Prometheus operator installation.

    Returns:
        List of Prometheus CRD names found, e.g.:
        - prometheuses.monitoring.coreos.com
        - servicemonitors.monitoring.coreos.com
        - podmonitors.monitoring.coreos.com
        - prometheusrules.monitoring.coreos.com

    Example:
        >>> crds = detect_prometheus_crds()
        >>> if 'prometheuses.monitoring.coreos.com' in crds:
        ...     print("Prometheus operator is installed")
    """
    try:
        api = client.ApiextensionsV1Api()
        crds = api.list_custom_resource_definition(timeout_seconds=10)
    except (ApiException, Exception):
        return []
    return [c.metadata.name for c in (crds.items or []) if "prometheus" in (c.metadata.name or "").lower()]


def find_all_services_with_prometheus() -> list[dict[str, Any]]:
    """Find all services across all namespaces that contain "prometheus" in the name.

    This is often the simplest and most effective detection method.

    Returns:
        List of service dictionaries with keys:
        - name: Service name
        - namespace: Namespace
        - port: Port number (if available)
        - type: Service type (ClusterIP, NodePort, etc.)

    Example:
        >>> services = find_all_services_with_prometheus()
        >>> for svc in services:
        ...     print(f"{svc['namespace']}/{svc['name']}:{svc['port']}")
    """
    try:
        v1 = client.CoreV1Api()
        svc_list = v1.list_service_for_all_namespaces(timeout_seconds=15)
    except (ApiException, Exception):
        return []
    out: list[dict[str, Any]] = []
    for svc in svc_list.items or []:
        name = svc.metadata.name or ""
        if "prometheus" not in name.lower():
            continue
        out.append(
            {
                "name": name,
                "namespace": svc.metadata.namespace or "",
                "port": _service_port(svc),
                "type": (svc.spec.type if svc.spec else None) or "ClusterIP",
            }
        )
    return out


def find_prometheus_service_from_crds() -> dict[str, Any] | None:
    """Find Prometheus service after CRDs are detected.

    When CRDs are found, this looks for the actual Prometheus service
    by searching for Prometheus custom resources.

    Returns:
        Dict with service info (name, namespace, port) or None if not found
    """
    try:
        custom = client.CustomObjectsApi()
        # The Prometheus operator's `Prometheus` CRD lives under
        # monitoring.coreos.com/v1, listed at /apis/monitoring.coreos.com/v1/prometheuses
        result = custom.list_cluster_custom_object(
            group="monitoring.coreos.com",
            version="v1",
            plural="prometheuses",
            timeout_seconds=10,
        )
        items = result.get("items", []) if isinstance(result, dict) else []
        if items:
            namespace = items[0].get("metadata", {}).get("namespace", "monitoring")
            svc = find_prometheus_service_in_namespace(namespace)
            if svc:
                return svc
    except (ApiException, Exception):
        pass
    # Fallback: search all services
    services = find_all_services_with_prometheus()
    return services[0] if services else None


def find_service_by_labels(labels: list[str]) -> dict[str, Any] | None:
    """Find service by label selector.

    Args:
        labels: List of label selectors (e.g., "app=prometheus")

    Returns:
        Dict with service info or None if not found
    """
    v1 = client.CoreV1Api()
    for label in labels:
        try:
            result = v1.list_service_for_all_namespaces(label_selector=label, timeout_seconds=10)
        except (ApiException, Exception):
            continue
        if result.items:
            svc = result.items[0]
            return {
                "name": svc.metadata.name or "",
                "namespace": svc.metadata.namespace or "",
                "port": _service_port(svc),
            }
    return None


def detect_prometheus_operator() -> dict[str, Any] | None:
    """Check for Prometheus operator deployment.

    Returns:
        Dict with operator info (name, namespace) or None if not found
    """
    try:
        apps = client.AppsV1Api()
        deploys = apps.list_deployment_for_all_namespaces(timeout_seconds=15)
    except (ApiException, Exception):
        return None
    for deploy in deploys.items or []:
        name = deploy.metadata.name or ""
        if "prometheus-operator" in name.lower():
            return {"name": name, "namespace": deploy.metadata.namespace or ""}
    return None


def find_prometheus_service_in_namespace(namespace: str) -> dict[str, Any] | None:
    """Find Prometheus service in a specific namespace.

    Args:
        namespace: Namespace to search

    Returns:
        Dict with service info or None if not found
    """
    try:
        v1 = client.CoreV1Api()
        svc_list = v1.list_namespaced_service(namespace, timeout_seconds=10)
    except (ApiException, Exception):
        return None
    for svc in svc_list.items or []:
        name = svc.metadata.name or ""
        lower = name.lower()
        if "prometheus" in lower and "operator" not in lower:
            return {"name": name, "namespace": namespace, "port": _service_port(svc)}
    return None


class _LocalPortForward:
    """Manages a TCP listener that proxies to a Pod via the K8s API.

    Encapsulates the bookkeeping for ``kubernetes.stream.portforward.PortForward``,
    which exposes a socket-pair interface rather than binding a local port. We
    wrap it in a tiny accept-loop so callers can talk to ``localhost:<local_port>``
    the same way they used to with ``kubectl port-forward``.

    Use ``stop()`` to tear down the listener and worker threads cleanly.
    """

    def __init__(self, listener: socket.socket, stop_event: threading.Event) -> None:
        """Hold the bound socket and the kill switch for the accept loop."""
        self._listener = listener
        self._stop_event = stop_event

    def stop(self) -> None:
        """Stop accepting new connections and close the listener."""
        self._stop_event.set()
        try:
            self._listener.close()
        except Exception:
            pass


def _resolve_pod_for_service(namespace: str, service_name: str) -> tuple[str, int] | None:
    """Find a ready Pod backing ``service_name`` and the targetPort to use.

    Returns ``(pod_name, target_port)`` or None if the Service has no matching
    Pods. Used instead of port-forwarding to a Service directly because the
    Python ``portforward`` helper only forwards to Pods.
    """
    v1 = client.CoreV1Api()
    try:
        svc = v1.read_namespaced_service(service_name, namespace)
    except (ApiException, Exception):
        return None
    selector = (svc.spec.selector if svc.spec else None) or {}
    if not selector:
        return None
    label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
    try:
        pods = v1.list_namespaced_pod(namespace, label_selector=label_selector)
    except (ApiException, Exception):
        return None
    # Pick a Pod that's running and ready. Fall back to any pod if none ready.
    candidates = []
    for p in pods.items or []:
        if p.status and p.status.phase == "Running":
            ready = any(c.ready for c in (p.status.container_statuses or []) if c)
            candidates.append((ready, p.metadata.name))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (not t[0], t[1] or ""))  # ready first
    pod_name = candidates[0][1] or ""

    # Resolve targetPort: prefer the named "web"/"http"/"prometheus" port.
    target_port = _service_port(svc)
    return (pod_name, target_port)


def start_port_forward(
    service_name: str, namespace: str, local_port: int = 9091, remote_port: int = 9090
) -> _LocalPortForward | None:
    """Forward a local TCP port to a Prometheus pod via the Kubernetes API.

    Args:
        service_name: Service name to forward
        namespace: Namespace where service lives
        local_port: Local port to bind (default: 9091)
        remote_port: Ignored. Kept for backward compatibility — the actual
                     remote port is read from the Service spec.

    Returns:
        Forwarder handle or None if failed to start

    Example:
        >>> pf = start_port_forward('prometheus', 'monitoring')
        >>> if pf:
        ...     wait_for_prometheus(9091)
        ...     # Do work
        ...     cleanup_port_forward(pf)
    """
    _ = remote_port  # backwards-compat: ignored, port comes from the Service.
    resolved = _resolve_pod_for_service(namespace, service_name)
    if resolved is None:
        return None
    pod_name, target_port = resolved

    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", local_port))
        listener.listen(4)
        listener.settimeout(0.5)
    except OSError:
        return None

    stop_event = threading.Event()

    def _accept_loop() -> None:
        """Accept local connections and pipe each to the Pod via the K8s API."""
        v1 = client.CoreV1Api()
        while not stop_event.is_set():
            try:
                conn, _addr = listener.accept()
            except (TimeoutError, OSError):
                continue

            def _proxy(conn: socket.socket = conn) -> None:
                """Bridge the local TCP connection to a Pod port-forward stream."""
                try:
                    pf = portforward(
                        v1.connect_get_namespaced_pod_portforward,
                        pod_name,
                        namespace,
                        ports=str(target_port),
                    )
                    upstream = pf.socket(target_port)
                    upstream.setblocking(False)
                    conn.setblocking(False)
                    sockets = [conn, upstream]
                    try:
                        while not stop_event.is_set():
                            readable, _, _ = select.select(sockets, [], [], 0.5)
                            if not readable:
                                continue
                            for sock in readable:
                                data = sock.recv(4096)
                                if not data:
                                    return
                                other = upstream if sock is conn else conn
                                other.sendall(data)
                    finally:
                        try:
                            pf.close()
                        except Exception:
                            pass
                except Exception:
                    return
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

            threading.Thread(target=_proxy, daemon=True).start()

    threading.Thread(target=_accept_loop, daemon=True).start()
    return _LocalPortForward(listener, stop_event)


def _http_probe(url: str, timeout: float, auth: tuple[str, str] | str | None) -> bool:
    """Issue a single GET against ``url`` and return True iff it 2xxs.

    Uses ``requests`` so we don't ship curl in the container and don't expose
    the broader scheme set that ``urllib`` accepts (file://, ftp://, ...).
    Auth handling matches the rest of this module (Basic for a tuple, Bearer
    for a str).
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    headers: dict = {}
    basic_auth = None
    if isinstance(auth, tuple):
        basic_auth = auth
    elif isinstance(auth, str) and auth:
        headers["Authorization"] = f"Bearer {auth}"
    try:
        resp = requests.get(url, timeout=timeout, headers=headers, auth=basic_auth)
        return 200 <= resp.status_code < 300
    except requests.RequestException:
        return False


def wait_for_prometheus(
    port: int | None = None,
    max_attempts: int = 10,
    auth: tuple[str, str] | str | None = None,
) -> bool:
    """Wait until Prometheus answers a probe query.

    The probed URL is taken from the module-level base set by
    ``set_prometheus_url()``. ``port`` is a legacy escape hatch — when given,
    we override the base with ``http://localhost:<port>``. The probe targets
    ``/api/v1/query?query=up``.

    Args:
        port: Optional. When set, probe ``http://localhost:<port>`` instead of
              the configured base. Used for the laptop port-forward flow.
        max_attempts: Maximum connection attempts (default: 10).
        auth: Optional credentials. Tuple = Basic Auth, str = Bearer Token.

    Returns:
        True if Prometheus responds, False after exhausting attempts.
    """
    base = f"http://localhost:{port}" if port is not None else _PROM_BASE
    url = f"{base}/api/v1/query?query=up"
    for _ in range(max_attempts):
        if _http_probe(url, timeout=2, auth=auth):
            return True
        time.sleep(1)
    return False


def test_connection(url: str) -> bool:
    """Test connection to Prometheus URL.

    Args:
        url: Prometheus URL (e.g., http://prometheus:9090)

    Returns:
        True if connection successful, False otherwise
    """
    test_url = f"{url.rstrip('/')}/api/v1/query?query=up"
    return _http_probe(test_url, timeout=5, auth=None)


def cleanup_port_forward(forwarder: _LocalPortForward | None) -> None:
    """Stop a forwarder created by ``start_port_forward``.

    Args:
        forwarder: Handle returned by ``start_port_forward()``, or None.
    """
    if forwarder is not None:
        forwarder.stop()


def query_cpu_percentiles(
    namespace: str,
    pod_pattern: str,
    time_range: str = "7d",
    port: int = 9091,
    auth: tuple[str, str] | str | None = None,
) -> dict[str, float]:
    """Query CPU percentiles from Prometheus.

    Matches the exact queries from k8s_collect_app_data.sh for consistency.

    Args:
        namespace: Namespace to query
        pod_pattern: Pod name pattern (regex)
        time_range: Time range for query (e.g., "7d", "24h")
        port: Prometheus port (default: 9091)
        auth: Optional authentication credentials (Basic Auth tuple or Bearer Token str)

    Returns:
        Dict with keys: p50, p95, max, stddev (all in millicores)
        Returns empty dict on failure
    """
    try:
        url = f"{_PROM_BASE}/api/v1/query"
        headers = {}
        basic_auth = None

        if auth:
            if isinstance(auth, tuple):
                basic_auth = auth
            elif isinstance(auth, str):
                headers["Authorization"] = f"Bearer {auth}"

        # P50: Double aggregation like original script
        query_p50 = f'''
        quantile(0.50, quantile_over_time(0.50,
            rate(container_cpu_usage_seconds_total{{
                namespace="{namespace}",
                pod=~"{pod_pattern}",
                container!="",
                container!="POD"
            }}[5m])[{time_range}:1m]))
        '''
        response = _request_with_retry(url, {"query": query_p50.strip()}, timeout=10, auth=basic_auth, headers=headers)
        if response is None or response.status_code != 200:
            return {}

        data = response.json()
        if data.get("status") != "success":
            return {}

        results = data.get("data", {}).get("result", [])
        if not results:
            return {}

        p50_cores = float(results[0]["value"][1])
        p50 = p50_cores * 1000  # Convert to millicores

        # P95: Double aggregation
        query_p95 = f'''
        quantile(0.95, quantile_over_time(0.95,
            rate(container_cpu_usage_seconds_total{{
                namespace="{namespace}",
                pod=~"{pod_pattern}",
                container!="",
                container!="POD"
            }}[5m])[{time_range}:1m]))
        '''
        response = _request_with_retry(url, {"query": query_p95.strip()}, timeout=10, auth=basic_auth, headers=headers)
        p95 = p50  # Default
        if response is not None and response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                if results:
                    p95_cores = float(results[0]["value"][1])
                    p95 = p95_cores * 1000

        # Max
        query_max = f'''
        max(max_over_time(
            rate(container_cpu_usage_seconds_total{{
                namespace="{namespace}",
                pod=~"{pod_pattern}",
                container!="",
                container!="POD"
            }}[5m])[{time_range}:1m]))
        '''
        response = _request_with_retry(url, {"query": query_max.strip()}, timeout=10, auth=basic_auth, headers=headers)
        max_val = p95  # Default
        if response is not None and response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                if results:
                    max_cores = float(results[0]["value"][1])
                    max_val = max_cores * 1000

        # StdDev
        query_stddev = f'''
        stddev(stddev_over_time(
            rate(container_cpu_usage_seconds_total{{
                namespace="{namespace}",
                pod=~"{pod_pattern}",
                container!="",
                container!="POD"
            }}[5m])[{time_range}:1m]))
        '''
        response = _request_with_retry(
            url, {"query": query_stddev.strip()}, timeout=10, auth=basic_auth, headers=headers
        )
        stddev = 0.0
        if response is not None and response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                if results:
                    stddev_cores = float(results[0]["value"][1])
                    stddev = stddev_cores * 1000

        return {"p50": round(p50, 2), "p95": round(p95, 2), "max": round(max_val, 2), "stddev": round(stddev, 2)}

    except Exception:
        return {}


def query_memory_volatility(
    namespace: str,
    pod_pattern: str,
    time_range: str = "7d",
    port: int = 9091,
    auth: tuple[str, str] | str | None = None,
) -> dict[str, float]:
    """Query memory volatility metrics from Prometheus.

    Matches the exact queries from k8s_collect_app_data.sh for consistency.

    Args:
        namespace: Namespace to query
        pod_pattern: Pod name pattern (regex)
        time_range: Time range for query (e.g., "7d", "24h")
        port: Prometheus port (default: 9091)
        auth: Optional authentication credentials (Basic Auth tuple or Bearer Token str)

    Returns:
        Dict with keys: p50, p95, min, max, stddev, coefficient_of_variation (all in MiB)
        Returns empty dict on failure
    """
    try:
        url = f"{_PROM_BASE}/api/v1/query"
        headers = {}
        basic_auth = None

        if auth:
            if isinstance(auth, tuple):
                basic_auth = auth
            elif isinstance(auth, str):
                headers["Authorization"] = f"Bearer {auth}"

        # P50 memory (double aggregation)
        query_p50 = f'''
        quantile(0.50, quantile_over_time(0.50,
            container_memory_working_set_bytes{{
                namespace="{namespace}",
                pod=~"{pod_pattern}",
                container!="",
                container!="POD"
            }}[{time_range}:1m])) / 1024 / 1024
        '''
        response = _request_with_retry(url, {"query": query_p50.strip()}, timeout=10, auth=basic_auth, headers=headers)
        if response is None or response.status_code != 200:
            return {}

        data = response.json()
        if data.get("status") != "success":
            return {}

        results = data.get("data", {}).get("result", [])
        if not results:
            return {}

        p50 = float(results[0]["value"][1])

        # P95 memory (double aggregation)
        query_p95 = f'''
        quantile(0.95, quantile_over_time(0.95,
            container_memory_working_set_bytes{{
                namespace="{namespace}",
                pod=~"{pod_pattern}",
                container!="",
                container!="POD"
            }}[{time_range}:1m])) / 1024 / 1024
        '''
        response = _request_with_retry(url, {"query": query_p95.strip()}, timeout=10, auth=basic_auth, headers=headers)
        p95 = p50  # Default
        if response is not None and response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                if results:
                    p95 = float(results[0]["value"][1])

        # Min memory
        query_min = f'''
        min(min_over_time(
            container_memory_working_set_bytes{{
                namespace="{namespace}",
                pod=~"{pod_pattern}",
                container!="",
                container!="POD"
            }}[{time_range}])) / 1024 / 1024
        '''
        response = _request_with_retry(url, {"query": query_min.strip()}, timeout=10, auth=basic_auth, headers=headers)
        min_val = p50  # Default
        if response is not None and response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                if results:
                    min_val = float(results[0]["value"][1])

        # Max memory
        query_max = f'''
        max(max_over_time(
            container_memory_working_set_bytes{{
                namespace="{namespace}",
                pod=~"{pod_pattern}",
                container!="",
                container!="POD"
            }}[{time_range}])) / 1024 / 1024
        '''
        response = _request_with_retry(url, {"query": query_max.strip()}, timeout=10, auth=basic_auth, headers=headers)
        max_val = p95  # Default
        if response is not None and response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                if results:
                    max_val = float(results[0]["value"][1])

        # StdDev memory (matches original: stddev(stddev_over_time(...)))
        query_stddev = f'''
        stddev(stddev_over_time(
            container_memory_working_set_bytes{{
                namespace="{namespace}",
                pod=~"{pod_pattern}",
                container!="",
                container!="POD"
            }}[{time_range}])) / 1024 / 1024
        '''
        response = _request_with_retry(
            url, {"query": query_stddev.strip()}, timeout=10, auth=basic_auth, headers=headers
        )
        stddev = 0.0
        if response is not None and response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                if results:
                    stddev = float(results[0]["value"][1])

        # Average memory (for CV calculation)
        query_avg = f'''
        avg(avg_over_time(
            container_memory_working_set_bytes{{
                namespace="{namespace}",
                pod=~"{pod_pattern}",
                container!="",
                container!="POD"
            }}[{time_range}])) / 1024 / 1024
        '''
        response = _request_with_retry(url, {"query": query_avg.strip()}, timeout=10, auth=basic_auth, headers=headers)
        avg = p50  # Default to P50
        if response is not None and response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                if results:
                    avg = float(results[0]["value"][1])

        # Calculate coefficient of variation
        cv = (stddev / avg * 100) if avg > 0 else 0.0

        return {
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "min": round(min_val, 2),
            "max": round(max_val, 2),
            "stddev": round(stddev, 2),
            "coefficient_of_variation": round(cv, 2),
        }

    except Exception:
        return {}


def query_cpu_throttle_pct(
    namespace: str,
    pod_pattern: str,
    time_range: str = "7d",
    port: int = 9091,
    auth: tuple[str, str] | str | None = None,
) -> float:
    """Query CPU throttle percentage from Prometheus.

    Calculates the ratio of throttled time to total CPU time over the
    given time range, expressed as a percentage.

    Args:
        namespace: Namespace to query
        pod_pattern: Pod name pattern (regex)
        time_range: Time range for query (e.g., "7d", "24h")
        port: Prometheus port (default: 9091)
        auth: Optional authentication credentials (Basic Auth tuple or Bearer Token str)

    Returns:
        Throttle percentage (0-100), or 0.0 on failure / no data
    """
    try:
        url = f"{_PROM_BASE}/api/v1/query"
        headers = {}
        basic_auth = None

        if auth:
            if isinstance(auth, tuple):
                basic_auth = auth
            elif isinstance(auth, str):
                headers["Authorization"] = f"Bearer {auth}"

        throttled_query = f'''
        sum(rate(container_cpu_cfs_throttled_seconds_total{{
            namespace="{namespace}",
            pod=~"{pod_pattern}",
            container!="",
            container!="POD"
        }}[{time_range}]))
        '''

        total_query = f'''
        sum(rate(container_cpu_cfs_periods_total{{
            namespace="{namespace}",
            pod=~"{pod_pattern}",
            container!="",
            container!="POD"
        }}[{time_range}]))
        '''

        resp_throttled = _request_with_retry(
            url, {"query": throttled_query.strip()}, timeout=10, auth=basic_auth, headers=headers
        )
        resp_total = _request_with_retry(
            url, {"query": total_query.strip()}, timeout=10, auth=basic_auth, headers=headers
        )

        if (
            resp_throttled is None
            or resp_total is None
            or resp_throttled.status_code != 200
            or resp_total.status_code != 200
        ):
            return 0.0

        t_data = resp_throttled.json()
        p_data = resp_total.json()

        if t_data.get("status") != "success" or p_data.get("status") != "success":
            return 0.0

        t_results = t_data.get("data", {}).get("result", [])
        p_results = p_data.get("data", {}).get("result", [])

        if not t_results or not p_results:
            return 0.0

        throttled_val = float(t_results[0]["value"][1])
        total_val = float(p_results[0]["value"][1])

        if total_val <= 0:
            return 0.0

        pct = (throttled_val / total_val) * 100
        return round(pct, 2)

    except Exception:
        return 0.0


def query_days_since_last_restart(
    namespace: str, pod_pattern: str, port: int = 9091, auth: tuple[str, str] | str | None = None
) -> float:
    """Query days since last restart from Prometheus.

    Uses the minimum pod start time across matching containers to derive
    the age of the youngest pod (proxy for last restart).

    Args:
        namespace: Namespace to query
        pod_pattern: Pod name pattern (regex)
        port: Prometheus port (default: 9091)
        auth: Optional authentication credentials (Basic Auth tuple or Bearer Token str)

    Returns:
        Days since last restart, or -1.0 if unavailable
    """
    try:
        url = f"{_PROM_BASE}/api/v1/query"
        headers = {}
        basic_auth = None

        if auth:
            if isinstance(auth, tuple):
                basic_auth = auth
            elif isinstance(auth, str):
                headers["Authorization"] = f"Bearer {auth}"

        query = f'''
        min(
            time() - kube_pod_start_time{{
                namespace="{namespace}",
                pod=~"{pod_pattern}"
            }}
        ) / 86400
        '''

        response = _request_with_retry(url, {"query": query.strip()}, timeout=10, auth=basic_auth, headers=headers)
        if response is None or response.status_code != 200:
            return -1.0

        data = response.json()
        if data.get("status") != "success":
            return -1.0

        results = data.get("data", {}).get("result", [])
        if not results:
            return -1.0

        days = float(results[0]["value"][1])
        return round(days, 1)

    except Exception:
        return -1.0


def query_restart_rate(
    namespace: str,
    pod_pattern: str,
    time_range: str = "7d",
    port: int = 9091,
    auth: tuple[str, str] | str | None = None,
) -> float:
    """Query restart rate from Prometheus.

    Matches the exact query from k8s_collect_app_data.sh for consistency.

    IMPORTANT: This function can return garbage values (e.g., 92.87 restarts/day)
    when actual restart count is 0. The detector module validates this by checking
    total_restarts > 0 before trusting the rate.

    Args:
        namespace: Namespace to query
        pod_pattern: Pod name pattern (regex)
        time_range: Time range for query (e.g., "7d", "24h")
        port: Prometheus port (default: 9091)
        auth: Optional authentication credentials (Basic Auth tuple or Bearer Token str)

    Returns:
        Restart rate per day (float), or 0.0 on failure
    """
    try:
        url = f"{_PROM_BASE}/api/v1/query"
        headers = {}
        basic_auth = None

        if auth:
            if isinstance(auth, tuple):
                basic_auth = auth
            elif isinstance(auth, str):
                headers["Authorization"] = f"Bearer {auth}"

        # Query restart rate (matches original script)
        # sum(rate(...)) * 86400 to get restarts per day
        query = f'''
        sum(rate(kube_pod_container_status_restarts_total{{
            namespace="{namespace}",
            pod=~"{pod_pattern}"
        }}[{time_range}])) * 86400
        '''

        response = _request_with_retry(url, {"query": query.strip()}, timeout=10, auth=basic_auth, headers=headers)

        if response is None or response.status_code != 200:
            return 0.0

        data = response.json()
        if data.get("status") != "success":
            return 0.0

        results = data.get("data", {}).get("result", [])
        if not results:
            return 0.0

        # Get the restart rate (already in restarts/day)
        restart_rate = float(results[0]["value"][1])

        return round(restart_rate, 2)

    except Exception:
        return 0.0
