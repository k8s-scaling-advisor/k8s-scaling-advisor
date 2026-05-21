"""Prometheus detection and query functions.

This module provides comprehensive Prometheus auto-detection and query capabilities.
All functions are designed to fail gracefully - returning None or empty dict on failure
rather than raising exceptions.

Detection Strategy (in priority order):
1. CRDs (most reliable) - kubectl get crds | grep prometheus
2. All services grep (simple and effective) - kubectl get svc -A | grep prometheus
3. Service labels - kubectl get svc -A -l app.kubernetes.io/name=prometheus
4. Prometheus operator - kubectl get deployments -A | grep prometheus-operator
5. Common namespaces - monitoring, prometheus, kube-system, observability
6. Manual fallback - prompt user for URL
"""

import json
import random
import subprocess
import time
from typing import Any, Optional, Union


def _request_with_retry(
    url: str,
    params: dict[str, Any],
    timeout: int = 10,
    auth: Optional[tuple[str, str]] = None,
    headers: Optional[dict[str, str]] = None,
    max_attempts: int = 3,
) -> Optional[Any]:
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
        result = subprocess.run(
            ["kubectl", "get", "crds", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            return []

        data = json.loads(result.stdout)
        crds = []

        for crd in data.get("items", []):
            name = crd.get("metadata", {}).get("name", "")
            if "prometheus" in name.lower():
                crds.append(name)

        return crds

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError, KeyError):
        return []


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
        # Get all services in JSON format
        result = subprocess.run(
            ["kubectl", "get", "services", "-A", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=15,
        )

        if result.returncode != 0:
            return []

        data = json.loads(result.stdout)
        services = []

        for svc in data.get("items", []):
            name = svc.get("metadata", {}).get("name", "")
            namespace = svc.get("metadata", {}).get("namespace", "")

            # Check if "prometheus" is in the service name
            if "prometheus" in name.lower():
                # Extract port information
                ports = svc.get("spec", {}).get("ports", [])
                port = 9090  # Default

                if ports:
                    # Look for common Prometheus ports or the first port
                    for p in ports:
                        if p.get("name") in ["web", "http", "prometheus"]:
                            port = p.get("port", 9090)
                            break
                    else:
                        port = ports[0].get("port", 9090)

                services.append(
                    {
                        "name": name,
                        "namespace": namespace,
                        "port": port,
                        "type": svc.get("spec", {}).get("type", "ClusterIP"),
                    }
                )

        return services

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError):
        return []


def find_prometheus_service_from_crds() -> Optional[dict[str, Any]]:
    """Find Prometheus service after CRDs are detected.

    When CRDs are found, this looks for the actual Prometheus service
    by searching for Prometheus custom resources.

    Returns:
        Dict with service info (name, namespace, port) or None if not found
    """
    try:
        # Try to get Prometheus custom resources
        result = subprocess.run(
            ["kubectl", "get", "prometheus", "-A", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            items = data.get("items", [])

            if items:
                # Use the first Prometheus resource
                prom = items[0]
                namespace = prom.get("metadata", {}).get("namespace", "monitoring")

                # Look for service in the same namespace
                return find_prometheus_service_in_namespace(namespace)

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError):
        pass

    # Fallback: search all services
    services = find_all_services_with_prometheus()
    return services[0] if services else None


def find_service_by_labels(labels: list[str]) -> Optional[dict[str, Any]]:
    """Find service by label selector.

    Args:
        labels: List of label selectors (e.g., "app=prometheus")

    Returns:
        Dict with service info or None if not found
    """
    for label in labels:
        try:
            result = subprocess.run(
                ["kubectl", "get", "services", "-A", "-l", label, "-o", "json"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                continue

            data = json.loads(result.stdout)
            items = data.get("items", [])

            if items:
                svc = items[0]
                ports = svc.get("spec", {}).get("ports", [])
                port = ports[0].get("port", 9090) if ports else 9090

                return {
                    "name": svc.get("metadata", {}).get("name", ""),
                    "namespace": svc.get("metadata", {}).get("namespace", ""),
                    "port": port,
                }

        except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError):
            continue

    return None


def detect_prometheus_operator() -> Optional[dict[str, Any]]:
    """Check for Prometheus operator deployment.

    Returns:
        Dict with operator info (name, namespace) or None if not found
    """
    try:
        result = subprocess.run(
            ["kubectl", "get", "deployments", "-A", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=15,
        )

        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)

        for deploy in data.get("items", []):
            name = deploy.get("metadata", {}).get("name", "")
            if "prometheus-operator" in name.lower():
                return {
                    "name": name,
                    "namespace": deploy.get("metadata", {}).get("namespace", ""),
                }

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError):
        pass

    return None


def find_prometheus_service_in_namespace(namespace: str) -> Optional[dict[str, Any]]:
    """Find Prometheus service in a specific namespace.

    Args:
        namespace: Namespace to search

    Returns:
        Dict with service info or None if not found
    """
    try:
        result = subprocess.run(
            ["kubectl", "get", "services", "-n", namespace, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)

        for svc in data.get("items", []):
            name = svc.get("metadata", {}).get("name", "")
            if "prometheus" in name.lower() and "operator" not in name.lower():
                ports = svc.get("spec", {}).get("ports", [])
                port = 9090

                if ports:
                    for p in ports:
                        if p.get("name") in ["web", "http", "prometheus"]:
                            port = p.get("port", 9090)
                            break
                    else:
                        port = ports[0].get("port", 9090)

                return {
                    "name": name,
                    "namespace": namespace,
                    "port": port,
                }

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError):
        pass

    return None


def start_port_forward(
    service_name: str, namespace: str, local_port: int = 9091, remote_port: int = 9090
) -> Optional[subprocess.Popen]:
    """Start kubectl port-forward for Prometheus service.

    Args:
        service_name: Service name to forward
        namespace: Namespace where service lives
        local_port: Local port to bind (default: 9091)
        remote_port: Remote service port (default: 9090)

    Returns:
        Popen process or None if failed to start

    Example:
        >>> pf = start_port_forward('prometheus', 'monitoring')
        >>> if pf:
        ...     wait_for_prometheus(9091)
        ...     # Do work
        ...     cleanup_port_forward(pf)
    """
    try:
        process = subprocess.Popen(
            [
                "kubectl",
                "port-forward",
                f"service/{service_name}",
                f"{local_port}:{remote_port}",
                "-n",
                namespace,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return process

    except subprocess.SubprocessError:
        return None


def wait_for_prometheus(
    port: int = 9091, max_attempts: int = 10, auth: Optional[Union[tuple[str, str], str]] = None
) -> bool:
    """Wait for Prometheus to be available on localhost.

    Args:
        port: Local port where Prometheus is forwarded
        max_attempts: Maximum connection attempts (default: 10)
        auth: Optional authentication credentials.
              Tuple[str, str] for Basic Auth (username, password)
              str for Bearer Token

    Returns:
        True if Prometheus responds, False otherwise
    """
    url = f"http://localhost:{port}/api/v1/query?query=up"

    def build_curl_cmd(url: str, auth: Optional[Union[tuple[str, str], str]]) -> list[str]:
        """Build a curl argv list, optionally adding Basic-Auth or Bearer-Token flags."""
        cmd = ["curl", "-s", "-f", url]
        if auth:
            if isinstance(auth, tuple):  # Basic Auth
                cmd.extend(["-u", f"{auth[0]}:{auth[1]}"])
            elif isinstance(auth, str):  # Bearer Token
                cmd.extend(["-H", f"Authorization: Bearer {auth}"])
        return cmd

    for _ in range(max_attempts):
        try:
            result = subprocess.run(
                build_curl_cmd(url, auth),
                capture_output=True,
                timeout=2,
            )

            if result.returncode == 0:
                return True

        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass

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

    try:
        result = subprocess.run(
            ["curl", "-s", "-f", test_url],
            capture_output=True,
            timeout=5,
        )

        return result.returncode == 0

    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False


def cleanup_port_forward(process: subprocess.Popen) -> None:
    """Clean up port-forward process.

    Args:
        process: Popen process from start_port_forward()
    """
    if process:
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def query_cpu_percentiles(
    namespace: str,
    pod_pattern: str,
    time_range: str = "7d",
    port: int = 9091,
    auth: Optional[Union[tuple[str, str], str]] = None,
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
        url = f"http://localhost:{port}/api/v1/query"
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
    auth: Optional[Union[tuple[str, str], str]] = None,
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
        url = f"http://localhost:{port}/api/v1/query"
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
    auth: Optional[Union[tuple[str, str], str]] = None,
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
        url = f"http://localhost:{port}/api/v1/query"
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
    namespace: str, pod_pattern: str, port: int = 9091, auth: Optional[Union[tuple[str, str], str]] = None
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
        url = f"http://localhost:{port}/api/v1/query"
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
    auth: Optional[Union[tuple[str, str], str]] = None,
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
        url = f"http://localhost:{port}/api/v1/query"
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
