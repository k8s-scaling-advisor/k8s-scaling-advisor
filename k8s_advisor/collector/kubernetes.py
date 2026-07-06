"""Kubernetes data collection using official Python client library.

This module uses the kubernetes Python library instead of kubectl commands
for more robust and efficient data collection.
"""

import os
import sys

from kubernetes import client, config
from kubernetes.client.rest import ApiException


def load_kube_config() -> bool:
    """Load Kubernetes configuration.

    When running inside a Pod (KUBERNETES_SERVICE_HOST is set by the kubelet),
    use in-cluster ServiceAccount credentials first — load_kube_config can
    return success against an empty or stale ~/.kube/config and produce 401s
    on the next API call.

    On a developer laptop, prefer kubeconfig.

    Returns:
        True if successful, False otherwise
    """
    in_cluster = bool(os.environ.get("KUBERNETES_SERVICE_HOST"))
    loaders = (
        (config.load_incluster_config, config.load_kube_config)
        if in_cluster
        else (config.load_kube_config, config.load_incluster_config)
    )
    last_err: Exception | None = None
    for loader in loaders:
        try:
            loader()
            _normalize_bearer_prefix()
            return True
        except Exception as e:
            last_err = e
            continue
    if last_err is not None:
        print(f"Error loading kubeconfig/in-cluster config: {last_err}", file=sys.stderr)
    return False


def _normalize_bearer_prefix() -> None:
    """Make in-cluster auth work across kubernetes-client versions.

    v36.0.0 broke in-cluster auth in two ways relative to earlier releases:

    1. ``load_incluster_config()`` stores the token at ``api_key['authorization']``
       prefixed with the lowercase string ``'bearer '`` baked into the value.
    2. ``Configuration.auth_settings()`` only emits a Bearer header when
       ``api_key`` contains the exact key ``'BearerToken'`` — so the token
       loaded by (1) is silently dropped and every API call returns 401.

    This function rewrites the configuration so:

    - ``api_key['BearerToken']`` holds the bare token (no prefix)
    - ``api_key_prefix['BearerToken']`` is the canonical ``'Bearer'``

    Result: ``auth_settings()`` produces ``Authorization: Bearer <token>``
    and the API server accepts it. Working configurations from older lib
    versions and from kubeconfig flows are left untouched.
    """
    try:
        cfg = client.Configuration._default
    except AttributeError:
        return
    if cfg is None:
        return
    api_key = getattr(cfg, "api_key", None) or {}
    if not isinstance(api_key, dict):
        return

    raw = api_key.get("authorization") or api_key.get("BearerToken")
    if not raw:
        return

    # Strip an embedded prefix if present (handles both the v36 lowercase
    # ``bearer `` and any future variant).
    head, _, rest = raw.partition(" ")
    token = rest if head.lower() == "bearer" and rest else raw

    cfg.api_key = {"BearerToken": token}
    cfg.api_key_prefix = {"BearerToken": "Bearer"}


def get_cluster_name() -> str:
    """Get current cluster name.

    Resolution order:
      1. K8S_ADVISOR_CLUSTER env var (allows operators to label runs from
         the chart without modifying code).
      2. Active kubeconfig context name (developer flow).
      3. The fallback "unknown" — used when neither is available, e.g. an
         in-cluster Pod with no env var set.
    """
    explicit = os.environ.get("K8S_ADVISOR_CLUSTER")
    if explicit:
        return explicit
    try:
        _contexts, active_context = config.list_kube_config_contexts()
        if active_context:
            return active_context["name"]
    except Exception:
        pass
    return "unknown"


class NamespaceAccessError(Exception):
    """Raised when the user lacks permission to list namespaces."""


def check_namespace_access(namespace: str) -> bool:
    """Check whether the current user can list deployments in a namespace.

    Uses SelfSubjectAccessReview — a lightweight RBAC check that doesn't
    require any permission on the target namespace beyond the ability to
    create access reviews (which all authenticated users have).

    Args:
        namespace: Namespace to check

    Returns:
        True if access is allowed, False otherwise
    """
    try:
        auth_api = client.AuthorizationV1Api()
        review = client.V1SelfSubjectAccessReview(
            spec=client.V1SelfSubjectAccessReviewSpec(
                resource_attributes=client.V1ResourceAttributes(
                    namespace=namespace,
                    verb="list",
                    resource="deployments",
                    group="apps",
                )
            )
        )
        result = auth_api.create_self_subject_access_review(review)
        return result.status.allowed
    except Exception:
        return True  # optimistic: let the actual call fail with a real error


def get_all_namespaces(exclude_system: bool = True) -> list[str]:
    """Get all namespaces in the cluster.

    Args:
        exclude_system: Exclude kube-* namespaces (default: True)

    Returns:
        List of namespace names

    Raises:
        NamespaceAccessError: If the user lacks cluster-level namespace list
            permission (403 Forbidden).
    """
    try:
        v1 = client.CoreV1Api()
        namespaces = v1.list_namespace()

        ns_list = []
        for ns in namespaces.items:
            name = ns.metadata.name
            if exclude_system and name.startswith("kube-"):
                continue
            ns_list.append(name)

        return sorted(ns_list)
    except ApiException as e:
        if e.status == 403:
            raise NamespaceAccessError(
                "No permission to list namespaces (403 Forbidden). "
                "Use -n <namespace> to specify namespaces you have access to."
            ) from e
        print(f"Error getting namespaces: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"Error getting namespaces: {e}", file=sys.stderr)
        return []


def get_deployments(namespace: str) -> list[dict]:
    """Get all deployments in a namespace with full details.

    Args:
        namespace: Namespace to query

    Returns:
        List of deployment dictionaries with metadata and spec

    Raises:
        ApiException: Re-raised for 403 so callers can detect permission issues.
    """
    try:
        apps_v1 = client.AppsV1Api()
        deployments = apps_v1.list_namespaced_deployment(namespace)

        result = []
        for deploy in deployments.items:
            result.append(
                {
                    "name": deploy.metadata.name,
                    "namespace": namespace,
                    "replicas": deploy.spec.replicas or 0,
                    "ready_replicas": deploy.status.ready_replicas or 0,
                    "labels": deploy.metadata.labels or {},
                    "selector": deploy.spec.selector.match_labels or {},
                    "containers": _extract_container_specs(deploy.spec.template.spec.containers),
                    "volumes": _extract_volume_info(deploy.spec.template.spec.volumes or []),
                }
            )

        return result
    except ApiException as e:
        if e.status == 403:
            raise
        print(f"Error getting deployments in {namespace}: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"Error getting deployments in {namespace}: {e}", file=sys.stderr)
        return []


def get_statefulsets(namespace: str) -> list[dict]:
    """Get all statefulsets in a namespace with full details.

    Args:
        namespace: Namespace to query

    Returns:
        List of statefulset dictionaries with metadata and spec

    Raises:
        ApiException: Re-raised for 403 so callers can detect permission issues.
    """
    try:
        apps_v1 = client.AppsV1Api()
        statefulsets = apps_v1.list_namespaced_stateful_set(namespace)

        result = []
        for sts in statefulsets.items:
            result.append(
                {
                    "name": sts.metadata.name,
                    "namespace": namespace,
                    "replicas": sts.spec.replicas or 0,
                    "ready_replicas": sts.status.ready_replicas or 0,
                    "labels": sts.metadata.labels or {},
                    "selector": sts.spec.selector.match_labels or {},
                    "containers": _extract_container_specs(sts.spec.template.spec.containers),
                    "volumes": _extract_volume_info(sts.spec.template.spec.volumes or []),
                    "volume_claim_templates": _extract_pvc_templates(sts.spec.volume_claim_templates or []),
                }
            )

        return result
    except ApiException as e:
        if e.status == 403:
            raise
        print(f"Error getting statefulsets in {namespace}: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"Error getting statefulsets in {namespace}: {e}", file=sys.stderr)
        return []


def get_pods_for_workload(namespace: str, label_selector: dict[str, str]) -> list[dict]:
    """Get all pods matching a label selector with detailed status.

    Args:
        namespace: Namespace to query
        label_selector: Dictionary of label key-value pairs

    Returns:
        List of pod dictionaries with status, metrics, and restart info
    """
    try:
        v1 = client.CoreV1Api()

        # Convert dict to label selector string
        selector_str = ",".join([f"{k}={v}" for k, v in label_selector.items()])

        pods = v1.list_namespaced_pod(namespace, label_selector=selector_str)

        result = []
        for pod in pods.items:
            # Extract container statuses
            container_statuses = []
            if pod.status.container_statuses:
                for cs in pod.status.container_statuses:
                    restart_count = cs.restart_count or 0

                    # Get last termination reason
                    last_termination_reason = None
                    last_termination_exit_code = None

                    if cs.last_state and cs.last_state.terminated:
                        last_termination_reason = cs.last_state.terminated.reason
                        last_termination_exit_code = cs.last_state.terminated.exit_code

                    container_statuses.append(
                        {
                            "name": cs.name,
                            "restart_count": restart_count,
                            "ready": cs.ready,
                            "last_termination_reason": last_termination_reason,
                            "last_termination_exit_code": last_termination_exit_code,
                        }
                    )

            result.append(
                {
                    "name": pod.metadata.name,
                    "namespace": namespace,
                    "phase": pod.status.phase,
                    "container_statuses": container_statuses,
                }
            )

        return result
    except Exception as e:
        print(f"Error getting pods in {namespace} with selector {label_selector}: {e}", file=sys.stderr)
        return []


def get_pod_metrics(namespace: str, label_selector: dict[str, str]) -> dict[str, float]:
    """Get aggregated metrics for pods matching label selector.

    Uses metrics-server API (kubectl top equivalent).

    Args:
        namespace: Namespace to query
        label_selector: Dictionary of label key-value pairs

    Returns:
        Dictionary with avg_cpu_m, avg_memory_mi, pod_count
    """
    try:
        from kubernetes.client import CustomObjectsApi

        custom_api = CustomObjectsApi()

        # Get pod metrics from metrics-server
        selector_str = ",".join([f"{k}={v}" for k, v in label_selector.items()])

        metrics = custom_api.list_namespaced_custom_object(
            group="metrics.k8s.io", version="v1beta1", namespace=namespace, plural="pods", label_selector=selector_str
        )

        total_cpu = 0.0
        total_memory = 0.0
        count = 0

        for pod in metrics.get("items", []):
            for container in pod.get("containers", []):
                # Parse CPU (e.g., "123m" or "1.5")
                cpu_str = container.get("usage", {}).get("cpu", "0")
                cpu_value = _parse_cpu(cpu_str)
                total_cpu += cpu_value

                # Parse memory (e.g., "123Mi" or "1Gi")
                mem_str = container.get("usage", {}).get("memory", "0")
                mem_value = _parse_memory(mem_str)
                total_memory += mem_value

                count += 1

        if count == 0:
            return {"avg_cpu_m": 0.0, "avg_memory_mi": 0.0, "pod_count": 0}

        return {
            "avg_cpu_m": total_cpu / count,
            "avg_memory_mi": total_memory / count,
            "pod_count": count,
        }

    except Exception as e:
        # Metrics-server might not be available
        print(f"Warning: Could not get pod metrics: {e}", file=sys.stderr)
        return {"avg_cpu_m": 0.0, "avg_memory_mi": 0.0, "pod_count": 0}


def get_hpa_for_workload(namespace: str, workload_name: str, workload_kind: str) -> dict | None:
    """Check if HPA exists for a workload.

    Args:
        namespace: Namespace to query
        workload_name: Name of deployment/statefulset
        workload_kind: "Deployment" or "StatefulSet"

    Returns:
        HPA configuration dict or None if not found
    """
    try:
        autoscaling_v2 = client.AutoscalingV2Api()
        hpas = autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(namespace)

        for hpa in hpas.items:
            target_ref = hpa.spec.scale_target_ref
            if target_ref.name == workload_name and target_ref.kind == workload_kind:
                return {
                    "name": hpa.metadata.name,
                    "min_replicas": hpa.spec.min_replicas or 1,
                    "max_replicas": hpa.spec.max_replicas,
                    "current_replicas": hpa.status.current_replicas or 0,
                    "desired_replicas": hpa.status.desired_replicas or 0,
                }

        return None
    except Exception as e:
        print(f"Error checking HPA in {namespace}: {e}", file=sys.stderr)
        return None


def get_vpa_for_workload(namespace: str, workload_name: str, workload_kind: str) -> dict | None:
    """Read the VerticalPodAutoscaler recommendation for a workload, if one exists.

    VPA is treated as an *optional input signal*, exactly like Prometheus:
    when a cluster runs VPA (commonly in recommender-only / "Off" mode), its
    ``status.recommendation.target`` is a purpose-built right-sizing number and
    is preferred over our own P95-derived estimate. When VPA is not installed
    (the CRD is absent) or no VPA targets this workload, this returns ``None``
    and the analyzer transparently falls back to Prometheus / metrics-server.

    Args:
        namespace: Namespace to query
        workload_name: Name of deployment/statefulset
        workload_kind: "Deployment" or "StatefulSet"

    Returns:
        Dict with summed target/bound millicores + Mi across containers, or
        ``None`` when unavailable. Shape:
        ``{"cpu_target_m", "mem_target_mi", "cpu_lower_m", "cpu_upper_m",
           "mem_lower_mi", "mem_upper_mi", "container_count"}``.
    """
    try:
        from kubernetes.client import CustomObjectsApi
        from kubernetes.client.rest import ApiException

        custom_api = CustomObjectsApi()
        try:
            vpas = custom_api.list_namespaced_custom_object(
                group="autoscaling.k8s.io",
                version="v1",
                namespace=namespace,
                plural="verticalpodautoscalers",
            )
        except ApiException as e:
            # 404 → the VPA CRD is not installed on this cluster. That is the
            # common case, not an error: stay silent and let the caller fall
            # back to Prometheus / metrics-server.
            if e.status == 404:
                return None
            print(f"Warning: could not list VPAs in {namespace}: {e}", file=sys.stderr)
            return None

        for vpa in vpas.get("items", []):
            target_ref = (vpa.get("spec") or {}).get("targetRef") or {}
            if target_ref.get("name") != workload_name or target_ref.get("kind") != workload_kind:
                continue

            recommendation = (vpa.get("status") or {}).get("recommendation") or {}
            container_recs = recommendation.get("containerRecommendations") or []
            if not container_recs:
                # VPA targets this workload but hasn't produced a
                # recommendation yet (still gathering samples). Treat as
                # "no VPA signal" rather than zero.
                return None

            # Sum across containers so the VPA numbers are directly comparable
            # to our per-workload (all-container) CPU_Request / Mem_Request.
            cpu_target = mem_target = 0.0
            cpu_lower = cpu_upper = mem_lower = mem_upper = 0.0
            for cr in container_recs:
                cpu_target += _parse_cpu_quantity_to_m(_rec_val(cr, "target", "cpu"))
                mem_target += _parse_mem_quantity_to_mi(_rec_val(cr, "target", "memory"))
                cpu_lower += _parse_cpu_quantity_to_m(_rec_val(cr, "lowerBound", "cpu"))
                cpu_upper += _parse_cpu_quantity_to_m(_rec_val(cr, "upperBound", "cpu"))
                mem_lower += _parse_mem_quantity_to_mi(_rec_val(cr, "lowerBound", "memory"))
                mem_upper += _parse_mem_quantity_to_mi(_rec_val(cr, "upperBound", "memory"))

            return {
                "cpu_target_m": round(cpu_target, 2),
                "mem_target_mi": round(mem_target, 2),
                "cpu_lower_m": round(cpu_lower, 2),
                "cpu_upper_m": round(cpu_upper, 2),
                "mem_lower_mi": round(mem_lower, 2),
                "mem_upper_mi": round(mem_upper, 2),
                "container_count": len(container_recs),
            }

        return None
    except Exception as e:
        print(f"Error checking VPA in {namespace}: {e}", file=sys.stderr)
        return None


def _rec_val(container_rec: dict, bound: str, resource: str):
    """Pull ``container_rec[bound][resource]`` (e.g. target/cpu), or None."""
    return (container_rec.get(bound) or {}).get(resource)


def _parse_cpu_quantity_to_m(value) -> float:
    """Parse a Kubernetes CPU quantity string into millicores.

    Handles the forms VPA emits: "250m" (millicores), "1"/"1.5" (cores),
    "500000000n" (nanocores), "250000u" (microcores). Returns 0.0 for
    None / unparseable input so a partial recommendation can't crash a run.
    """
    if value is None:
        return 0.0
    s = str(value).strip()
    if not s:
        return 0.0
    try:
        if s.endswith("m"):
            return float(s[:-1])
        if s.endswith("n"):
            return float(s[:-1]) / 1_000_000.0
        if s.endswith("u"):
            return float(s[:-1]) / 1_000.0
        # Bare number → cores.
        return float(s) * 1000.0
    except (TypeError, ValueError):
        return 0.0


def _parse_mem_quantity_to_mi(value) -> float:
    """Parse a Kubernetes memory quantity string into Mi (mebibytes).

    Handles binary suffixes (Ki/Mi/Gi/Ti), decimal suffixes (k/M/G/T), and
    bare byte counts. Returns 0.0 for None / unparseable input.
    """
    if value is None:
        return 0.0
    s = str(value).strip()
    if not s:
        return 0.0
    binary = {"Ki": 1 / 1024.0, "Mi": 1.0, "Gi": 1024.0, "Ti": 1024.0 * 1024.0}
    decimal = {
        "k": 1000.0 / (1024.0 * 1024.0),
        "M": 1000.0**2 / (1024.0 * 1024.0),
        "G": 1000.0**3 / (1024.0 * 1024.0),
        "T": 1000.0**4 / (1024.0 * 1024.0),
    }
    try:
        for suffix, factor in binary.items():
            if s.endswith(suffix):
                return float(s[: -len(suffix)]) * factor
        for suffix, factor in decimal.items():
            if s.endswith(suffix):
                return float(s[: -len(suffix)]) * factor
        # Bare number → bytes.
        return float(s) / (1024.0 * 1024.0)
    except (TypeError, ValueError):
        return 0.0


def get_events_for_workload(namespace: str, workload_name: str) -> dict[str, int]:
    """Get event counts for a workload (OOM kills, restarts, etc).

    Args:
        namespace: Namespace to query
        workload_name: Name of deployment/statefulset

    Returns:
        Dictionary with event counts: oom_kills, warnings, errors
    """
    try:
        v1 = client.CoreV1Api()
        events = v1.list_namespaced_event(namespace)

        oom_kills = 0
        warnings = 0
        errors = 0

        for event in events.items:
            # Check if event is related to this workload
            if not event.involved_object.name.startswith(workload_name):
                continue

            reason = event.reason or ""
            message = event.message or ""

            # Count OOM kills
            if "OOMKilled" in reason or "OOM" in message:
                oom_kills += 1

            # Count warnings and errors
            if event.type == "Warning":
                warnings += 1
            elif event.type == "Error":
                errors += 1

        return {
            "oom_kills": oom_kills,
            "warnings": warnings,
            "errors": errors,
        }
    except Exception as e:
        print(f"Error getting events in {namespace}: {e}", file=sys.stderr)
        return {"oom_kills": 0, "warnings": 0, "errors": 0}


# ══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ══════════════════════════════════════════════════════════════════════════════


def _extract_container_specs(containers) -> list[dict]:
    """Extract container resource specifications.

    Args:
        containers: List of V1Container objects

    Returns:
        List of container spec dictionaries
    """
    result = []

    for container in containers:
        resources = container.resources

        # Parse requests
        cpu_request = 0.0
        mem_request = 0.0
        if resources and resources.requests:
            cpu_request = _parse_cpu(resources.requests.get("cpu", "0"))
            mem_request = _parse_memory(resources.requests.get("memory", "0"))

        # Parse limits
        cpu_limit = 0.0
        mem_limit = 0.0
        if resources and resources.limits:
            cpu_limit = _parse_cpu(resources.limits.get("cpu", "0"))
            mem_limit = _parse_memory(resources.limits.get("memory", "0"))

        result.append(
            {
                "name": container.name,
                "cpu_request_m": cpu_request,
                "cpu_limit_m": cpu_limit,
                "mem_request_mi": mem_request,
                "mem_limit_mi": mem_limit,
            }
        )

    return result


def _extract_volume_info(volumes) -> dict:
    """Extract volume information (PVC detection).

    Args:
        volumes: List of V1Volume objects

    Returns:
        Dictionary with pvc_count and pvc_names
    """
    pvc_names = []

    for volume in volumes:
        if volume.persistent_volume_claim:
            pvc_names.append(volume.persistent_volume_claim.claim_name)

    return {
        "pvc_count": len(pvc_names),
        "pvc_names": pvc_names,
    }


def _extract_pvc_templates(templates) -> list[dict]:
    """Extract PVC template information from StatefulSet.

    Args:
        templates: List of V1PersistentVolumeClaim objects

    Returns:
        List of PVC template dictionaries with access modes
    """
    result = []

    for template in templates:
        access_modes = template.spec.access_modes or []
        result.append(
            {
                "name": template.metadata.name,
                "access_modes": access_modes,
                "has_rwo": "ReadWriteOnce" in access_modes,
            }
        )

    return result


def _parse_cpu(cpu_str: str) -> float:
    """Parse Kubernetes CPU resource string to millicores.

    Args:
        cpu_str: CPU string (e.g., "100m", "1.5", "901568n")

    Returns:
        CPU value in millicores
    """
    if not cpu_str or cpu_str == "0":
        return 0.0

    try:
        if "n" in cpu_str:
            # Nanoseconds (from metrics-server API)
            # 1 millicore = 1,000,000 nanoseconds
            return float(cpu_str.replace("n", "")) / 1_000_000
        elif "u" in cpu_str:
            # Microseconds
            # 1 millicore = 1,000 microseconds
            return float(cpu_str.replace("u", "")) / 1_000
        elif "m" in cpu_str:
            # Already in millicores
            return float(cpu_str.replace("m", ""))
        else:
            # In cores, convert to millicores
            return float(cpu_str) * 1000
    except (ValueError, AttributeError):
        return 0.0


def _parse_memory(mem_str: str) -> float:
    """Parse Kubernetes memory resource string to MiB.

    Args:
        mem_str: Memory string (e.g., "128Mi", "1Gi", "1024Ki")

    Returns:
        Memory value in MiB
    """
    if not mem_str or mem_str == "0":
        return 0.0

    try:
        if "Gi" in mem_str:
            return float(mem_str.replace("Gi", "")) * 1024
        elif "Mi" in mem_str:
            return float(mem_str.replace("Mi", ""))
        elif "Ki" in mem_str:
            return float(mem_str.replace("Ki", "")) / 1024
        elif "k" in mem_str.lower():
            # Sometimes "k" instead of "Ki"
            return float(mem_str.replace("k", "").replace("K", "")) / 1024
        else:
            # Bytes
            return float(mem_str) / (1024 * 1024)
    except (ValueError, AttributeError):
        return 0.0


def get_restart_info_for_pods(pods: list[dict]) -> dict:
    """Aggregate restart information from list of pods.

    Args:
        pods: List of pod dictionaries from get_pods_for_workload()

    Returns:
        Dictionary with total_restarts, max_restarts, oom_count, last_reason
    """
    total_restarts = 0
    max_restarts = 0
    oom_count = 0
    last_termination_reason = ""
    last_termination_exit_code = None
    pods_restarting = 0

    for pod in pods:
        pod_has_restarts = False

        for container_status in pod.get("container_statuses", []):
            restarts = container_status.get("restart_count", 0)
            total_restarts += restarts
            max_restarts = max(max_restarts, restarts)

            if restarts > 0:
                pod_has_restarts = True

            # Check for OOM kills
            reason = container_status.get("last_termination_reason", "")
            if reason:
                last_termination_reason = reason
                # Capture the exit code that goes with this reason. Multiple
                # containers can crash with different codes; we keep the most
                # recent non-None value (matches the reason we kept above).
                ec = container_status.get("last_termination_exit_code")
                if ec is not None:
                    last_termination_exit_code = ec
                if "OOMKilled" in reason:
                    oom_count += 1

        if pod_has_restarts:
            pods_restarting += 1

    return {
        "total_restarts": total_restarts,
        "max_restarts_per_pod": max_restarts,
        "oom_killed_count": oom_count,
        "last_restart_reason": last_termination_reason,
        "last_restart_exit_code": last_termination_exit_code,
        "pods_restarting": pods_restarting,
    }


def check_pvc_access_modes(namespace: str, pvc_names: list[str]) -> tuple[bool, str]:
    """Check if any PVCs have ReadWriteOnce access mode.

    Args:
        namespace: Namespace to query
        pvc_names: List of PVC names to check

    Returns:
        Tuple of (has_rwo, access_mode_string)
    """
    if not pvc_names:
        return False, "N/A"

    try:
        v1 = client.CoreV1Api()

        has_rwo = False
        access_modes = set()

        for pvc_name in pvc_names:
            try:
                pvc = v1.read_namespaced_persistent_volume_claim(pvc_name, namespace)
                modes = pvc.spec.access_modes or []

                if "ReadWriteOnce" in modes:
                    has_rwo = True

                access_modes.update(modes)
            except Exception:
                # PVC might not exist or be accessible
                continue

        access_mode_str = ",".join(sorted(access_modes)) if access_modes else "N/A"

        return has_rwo, access_mode_str

    except Exception as e:
        print(f"Error checking PVCs in {namespace}: {e}", file=sys.stderr)
        return False, "N/A"
