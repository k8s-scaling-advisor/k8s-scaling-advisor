#!/usr/bin/env python3
"""Manual Prometheus collector smoke-check.

Usage:
  python test_prometheus_collector.py --namespace <namespace>

This script is intentionally not part of automated tests because it requires:
1. A reachable Kubernetes cluster
2. Sufficient RBAC in the target namespace
3. A Prometheus endpoint discoverable by the collector
"""

import argparse

from k8s_advisor.collector import kubernetes as k8s
from k8s_advisor.collector import prometheus as prom


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual Prometheus collector check")
    parser.add_argument(
        "-n",
        "--namespace",
        default="default",
        help="Namespace to probe (default: default)",
    )
    args = parser.parse_args()

    if not k8s.load_kube_config():
        print("✗ Failed to load kubeconfig")
        return 1

    cluster = k8s.get_cluster_name()
    print(f"Cluster: {cluster}")

    prom_result = prom.auto_detect_prometheus()
    if not prom_result["available"]:
        print("✗ Prometheus not found")
        return 1

    print(f"✓ Found Prometheus: {prom_result['service_name']} ({prom_result['namespace']})")
    pf = prom.start_port_forward(
        prom_result["service_name"],
        prom_result["namespace"],
        9091,
        prom_result["port"],
    )
    if not pf or not prom.wait_for_prometheus(9091):
        print("✗ Failed to establish Prometheus port-forward")
        return 1

    print("✓ Prometheus ready on localhost:9091")
    deployments = k8s.get_deployments(args.namespace)
    print(f"\n{args.namespace}: {len(deployments)} deployments")

    for deploy in deployments:
        name = deploy["name"]
        pod_pattern = f"{name}-.*"
        cpu_metrics = prom.query_cpu_percentiles(args.namespace, pod_pattern, "7d", 9091)
        mem_metrics = prom.query_memory_volatility(args.namespace, pod_pattern, "7d", 9091)
        restart_rate = prom.query_restart_rate(args.namespace, pod_pattern, "7d", 9091)

        print(f"\n{name}:")
        print(f"  CPU P50/P95: {cpu_metrics.get('p50', 'N/A')} / {cpu_metrics.get('p95', 'N/A')} m")
        print(f"  Mem P50/P95: {mem_metrics.get('p50', 'N/A')} / {mem_metrics.get('p95', 'N/A')} Mi")
        print(f"  Mem CV: {mem_metrics.get('coefficient_of_variation', 'N/A')}%")
        print(f"  Restart rate: {restart_rate} restarts/day")

    prom.cleanup_port_forward(pf)
    print("\n✓ Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
