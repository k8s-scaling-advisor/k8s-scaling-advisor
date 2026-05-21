#!/usr/bin/env python3
"""K8s Scaling Advisor - Unified CLI

This is the main entry point that provides a unified interface for:
- Data collection (with automatic Prometheus detection)
- Analysis and report generation
- Full pipeline (collect + analyze in one command)

Usage:
    python3 main.py collect                    # Collect data
    python3 main.py analyze <csv_file>         # Analyze existing CSV
    python3 main.py report                     # Full pipeline (collect + analyze)
"""

import argparse
import csv
import fnmatch
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# Add k8s_advisor to path (handle both installed and development mode)
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from kubernetes.client.rest import ApiException  # noqa: E402

from k8s_advisor.collector import kubernetes as k8s  # noqa: E402
from k8s_advisor.collector import prometheus as prom  # noqa: E402


def collect_workload_data(
    workload: dict,
    workload_type: str,
    namespace: str,
    cluster: str,
    use_prometheus: bool = False,
    prom_port: int = 9091,
    auth: tuple[str, str] | str | None = None,
) -> dict:
    """Collect comprehensive data for a single workload.

    Args:
        workload: Workload dict from get_deployments() or get_statefulsets()
        workload_type: "Deployment" or "StatefulSet"
        namespace: Namespace name
        cluster: Cluster name
        use_prometheus: Whether to query Prometheus for enhanced metrics
        prom_port: Prometheus port (default: 9091)
        auth: Optional authentication credentials (Basic Auth tuple or Bearer Token str)

    Returns:
        Dictionary with all 39 CSV columns populated
    """
    name = workload["name"]
    replicas = workload["replicas"]
    ready_replicas = workload["ready_replicas"]
    containers = workload["containers"]
    volumes = workload["volumes"]
    selector = workload["selector"]

    # Aggregate container resources
    total_cpu_request = sum(c["cpu_request_m"] for c in containers)
    total_cpu_limit = sum(c["cpu_limit_m"] for c in containers)
    total_mem_request = sum(c["mem_request_mi"] for c in containers)
    total_mem_limit = sum(c["mem_limit_mi"] for c in containers)

    # Get pod metrics from metrics-server
    metrics = k8s.get_pod_metrics(namespace, selector)
    avg_cpu_usage = metrics.get("avg_cpu_m", 0.0)
    avg_mem_usage = metrics.get("avg_memory_mi", 0.0)
    pod_count = metrics.get("pod_count", 0)

    # Get pod restart info and OOM kills
    pods = k8s.get_pods_for_workload(namespace, selector)
    restart_info = k8s.get_restart_info_for_pods(pods)

    # Get event-based OOM kills
    events = k8s.get_events_for_workload(namespace, name)

    # Combine OOM kill counts
    oom_killed_count = max(restart_info.get("oom_killed_count", 0), events.get("oom_kills", 0))

    # Initialize Prometheus metrics with N/A
    cpu_p50 = "N/A"
    cpu_p95 = "N/A"
    cpu_max = "N/A"
    cpu_stddev = "N/A"
    cpu_throttle = "N/A"
    mem_p50 = "N/A"
    mem_p95 = "N/A"
    mem_max = "N/A"
    mem_stddev = "N/A"
    mem_volatility_cv = "N/A"
    restart_rate_per_day = "N/A"
    days_since_last_restart = "N/A"

    # Query Prometheus if available
    if use_prometheus:
        pod_pattern = f"{name}-.*"

        # Get CPU percentiles
        cpu_metrics = prom.query_cpu_percentiles(namespace, pod_pattern, "7d", prom_port, auth=auth)
        if cpu_metrics:
            cpu_p50 = cpu_metrics.get("p50", "N/A")
            cpu_p95 = cpu_metrics.get("p95", "N/A")
            cpu_max = cpu_metrics.get("max", "N/A")
            cpu_stddev = cpu_metrics.get("stddev", "N/A")

        # Get memory volatility
        mem_metrics = prom.query_memory_volatility(namespace, pod_pattern, "7d", prom_port, auth=auth)
        if mem_metrics:
            mem_p50 = mem_metrics.get("p50", "N/A")
            mem_p95 = mem_metrics.get("p95", "N/A")
            mem_max = mem_metrics.get("max", "N/A")
            mem_stddev = mem_metrics.get("stddev", "N/A")
            mem_volatility_cv = mem_metrics.get("coefficient_of_variation", "N/A")

        # Get CPU throttle percentage
        throttle_pct = prom.query_cpu_throttle_pct(namespace, pod_pattern, "7d", prom_port, auth=auth)
        if throttle_pct > 0:
            cpu_throttle = round(throttle_pct, 2)

        # Get restart rate
        restart_rate = prom.query_restart_rate(namespace, pod_pattern, "7d", prom_port, auth=auth)
        if restart_rate > 0:
            restart_rate_per_day = restart_rate

        # Get days since last restart
        days_val = prom.query_days_since_last_restart(namespace, pod_pattern, prom_port, auth=auth)
        if days_val >= 0:
            days_since_last_restart = round(days_val, 1)

    # Check for HPA
    hpa = k8s.get_hpa_for_workload(namespace, name, workload_type)
    has_hpa = hpa is not None
    hpa_min = hpa["min_replicas"] if hpa else "N/A"
    hpa_max = hpa["max_replicas"] if hpa else "N/A"

    # Check PVC access modes
    pvc_names = volumes.get("pvc_names", [])
    pvc_count = volumes.get("pvc_count", 0)

    if workload_type == "StatefulSet":
        vct = workload.get("volume_claim_templates", [])
        for template in vct:
            if template.get("has_rwo", False):
                pvc_access_mode = "ReadWriteOnce"
                break
        else:
            pvc_access_mode = "N/A" if pvc_count == 0 else "ReadWriteMany"
    else:
        if pvc_count > 0:
            _has_rwo, pvc_access_mode = k8s.check_pvc_access_modes(namespace, pvc_names)
        else:
            pvc_access_mode = "N/A"

    # Calculate usage percentages
    cpu_usage_pct_request = (avg_cpu_usage / total_cpu_request * 100) if total_cpu_request > 0 else 0
    cpu_usage_pct_limit = (avg_cpu_usage / total_cpu_limit * 100) if total_cpu_limit > 0 else 0
    mem_usage_pct_request = (avg_mem_usage / total_mem_request * 100) if total_mem_request > 0 else 0
    mem_usage_pct_limit = (avg_mem_usage / total_mem_limit * 100) if total_mem_limit > 0 else 0

    # Emit labels as JSON so the CSV is warehouse-loadable. Comma-joined
    # `k=v,k=v` strings break naive parsers when label values themselves
    # contain commas (kubernetes.io/hostname=node-a,b... etc.).
    full_labels = workload.get("labels") or {}
    if not full_labels:
        # Fall back to selector when full labels aren't surfaced.
        full_labels = selector or {}
    key_labels = json.dumps(full_labels, sort_keys=True, separators=(",", ":"))

    return {
        "Cluster": cluster,
        "Namespace": namespace,
        "Workload_Type": workload_type,
        "Deployment": name,
        "Replicas": replicas,
        "Pod_Count": pod_count or ready_replicas,
        "Avg_CPU_Usage(m)": round(avg_cpu_usage, 2),
        "CPU_Request(m)": round(total_cpu_request, 2),
        "CPU_Limit(m)": round(total_cpu_limit, 2),
        "CPU_Usage_Pct_Of_Request": round(cpu_usage_pct_request, 2),
        "CPU_Usage_Pct_Of_Limit": round(cpu_usage_pct_limit, 2),
        "CPU_Throttle_Pct": cpu_throttle,
        "CPU_P50(m)": cpu_p50,
        "CPU_P95(m)": cpu_p95,
        "CPU_Max(m)": cpu_max,
        "CPU_StdDev(m)": cpu_stddev,
        "Avg_Mem_Usage(Mi)": round(avg_mem_usage, 2),
        "Mem_Request(Mi)": round(total_mem_request, 2),
        "Mem_Limit(Mi)": round(total_mem_limit, 2),
        "Mem_Usage_Pct_Of_Request": round(mem_usage_pct_request, 2),
        "Mem_Usage_Pct_Of_Limit": round(mem_usage_pct_limit, 2),
        "Mem_P50(Mi)": mem_p50,
        "Mem_P95(Mi)": mem_p95,
        "Mem_Max(Mi)": mem_max,
        "Mem_StdDev(Mi)": mem_stddev,
        "Mem_Volatility_CV": mem_volatility_cv,
        "OOMKilled_Count": oom_killed_count,
        "LastRestart_Reason": restart_info.get("last_restart_reason", ""),
        "LastRestart_ExitCode": (ec if (ec := restart_info.get("last_restart_exit_code")) is not None else "N/A"),
        "Total_Restarts": restart_info.get("total_restarts", 0),
        "Max_Restarts_Per_Pod": restart_info.get("max_restarts_per_pod", 0),
        "Restart_Rate_Per_Day": restart_rate_per_day,
        "Days_Since_Last_Restart": days_since_last_restart,
        "Has_HPA": "true" if has_hpa else "false",
        "HPA_Min_Replicas": hpa_min,
        "HPA_Max_Replicas": hpa_max,
        "PVC_Access_Mode": pvc_access_mode,
        "PVC_Count": pvc_count,
        "Container_Count": len(containers),
        "Key_Labels": key_labels,
        "Detected_Issues": "",  # Will be filled by analyzer
    }


def resolve_namespaces(args) -> list[str]:
    """Resolve the target namespace list from CLI args.

    Supports three modes:
      -n ns1 -n ns2           explicit list
      --namespace-pattern 'app-*'  glob against discovered namespaces
      (neither / --all-namespaces)  all non-system namespaces

    When the user lacks cluster-level list-namespace permission,
    --namespace-pattern and --all-namespaces cannot work. The user
    must provide explicit -n flags instead.
    """
    from k8s_advisor.collector.kubernetes import NamespaceAccessError

    pattern = getattr(args, "namespace_pattern", None)
    explicit = getattr(args, "namespaces", None)

    if explicit:
        print(f"✓ Using {len(explicit)} specified namespace(s): {', '.join(explicit)}")
        return explicit

    # Pattern and all-namespaces both need cluster-level list permission
    try:
        all_ns = k8s.get_all_namespaces(exclude_system=True)
    except NamespaceAccessError:
        print("⚠️  No permission to list namespaces (403 Forbidden)", file=sys.stderr)
        if pattern:
            print(f"   Cannot apply pattern '{pattern}' without list-namespace access.", file=sys.stderr)
        print("   Use -n <namespace> to specify namespaces you have access to.", file=sys.stderr)
        print("   Example: k8s-advisor collect -n my-app -n my-app-staging", file=sys.stderr)
        sys.exit(1)

    if not all_ns:
        print("⚠️  No namespaces found (cluster may be empty or permissions are insufficient)", file=sys.stderr)
        print("   Use -n <namespace> to specify namespaces explicitly.", file=sys.stderr)
        sys.exit(1)

    if pattern:
        matched = [ns for ns in all_ns if fnmatch.fnmatch(ns, pattern)]
        if not matched:
            print(f"⚠️  Pattern '{pattern}' matched 0 namespaces", file=sys.stderr)
            sys.exit(1)
        print(f"✓ Pattern '{pattern}' matched {len(matched)} namespace(s): {', '.join(matched)}")
        return matched

    print(f"✓ Found {len(all_ns)} namespaces (excluding kube-*)")
    return all_ns


def cmd_collect(args):
    """Collect data from Kubernetes cluster."""
    print("=" * 70)
    print("K8s Scaling Advisor - Data Collection")
    print("=" * 70)
    print()

    # Load kubeconfig
    print("Loading Kubernetes configuration...")
    if not k8s.load_kube_config():
        print("✗ Failed to load kubeconfig", file=sys.stderr)
        sys.exit(1)
    print("✓ Kubeconfig loaded")

    # Get cluster name
    cluster = k8s.get_cluster_name()
    print(f"✓ Cluster: {cluster}")
    print()

    # Get namespaces to collect (before Prometheus, so we fail fast on RBAC)
    print("Discovering namespaces...")
    namespaces = resolve_namespaces(args)
    print()

    # Pre-flight RBAC check
    print("Checking namespace access...")
    accessible = []
    denied = []
    for ns in namespaces:
        if k8s.check_namespace_access(ns):
            accessible.append(ns)
        else:
            denied.append(ns)

    if denied:
        print(f"⚠️  No access to {len(denied)} namespace(s): {', '.join(denied)}")
    if not accessible:
        print("✗ No accessible namespaces — check your RBAC permissions", file=sys.stderr)
        print("  Verify with: kubectl auth can-i list deployments -n <namespace>", file=sys.stderr)
        sys.exit(1)
    print(f"✓ Access confirmed for {len(accessible)} namespace(s)")
    namespaces = accessible
    print()

    # Auto-detect Prometheus
    print("Detecting Prometheus...")
    prom_result = prom.auto_detect_prometheus()

    use_prometheus = False
    pf_process = None
    prom_auth = None

    if args.prometheus_token:
        prom_auth = args.prometheus_token
        print("✓ Using provided Prometheus Bearer Token")
    elif args.prometheus_user and args.prometheus_password:
        prom_auth = (args.prometheus_user, args.prometheus_password)
        print(f"✓ Using provided Prometheus Basic Auth ({args.prometheus_user})")

    if prom_result["available"]:
        print(f"✓ Found Prometheus via {prom_result['method']}")
        print(f"  Service: {prom_result['service_name']}")
        print(f"  Namespace: {prom_result['namespace']}")
        print(f"  Port: {prom_result['port']}")

        # Start port-forward
        print("  Starting port-forward...")
        pf_process = prom.start_port_forward(
            prom_result["service_name"], prom_result["namespace"], local_port=9091, remote_port=prom_result["port"]
        )

        if pf_process:
            print("  Waiting for Prometheus...")
            if prom.wait_for_prometheus(9091, auth=prom_auth):
                print("✓ Prometheus ready on localhost:9091")
                use_prometheus = True
            else:
                print("⚠️  Prometheus port-forward failed or auth failed, continuing without it")
                prom.cleanup_port_forward(pf_process)
                pf_process = None
    else:
        print("⚠️  Prometheus not detected, continuing with metrics-server only")

    print()

    # ─── Discovery pass ────────────────────────────────────────────────────
    # Walk all namespaces once just to count workloads. This lets us pick a
    # sensible default concurrency (parallel only when there's enough work
    # to amortize the thread-pool overhead) AND surface 403 errors before
    # any Prometheus traffic.
    all_data = []
    workload_count = 0
    skipped_namespaces = []
    discovered: list[tuple[str, list]] = []  # [(namespace, [(workload, wtype), ...])]

    print("Discovering workloads...")
    for namespace in namespaces:
        try:
            deployments = k8s.get_deployments(namespace)
        except ApiException as e:
            if e.status == 403:
                skipped_namespaces.append(namespace)
                continue
            deployments = []
        try:
            statefulsets = k8s.get_statefulsets(namespace)
        except ApiException as e:
            if e.status == 403:
                if not skipped_namespaces or skipped_namespaces[-1] != namespace:
                    skipped_namespaces.append(namespace)
                continue
            statefulsets = []
        targets = [(d, "Deployment") for d in deployments] + [(s, "StatefulSet") for s in statefulsets]
        discovered.append((namespace, targets))

    total_workloads = sum(len(t) for _, t in discovered)
    accessible_ns = len(discovered)
    print(
        f"✓ Discovered {total_workloads} workloads across {accessible_ns} "
        f"accessible namespaces ({len(skipped_namespaces)} skipped)"
    )

    # ─── Concurrency selection ────────────────────────────────────────────
    # User-specified value wins. Otherwise: 8 threads above 25 workloads,
    # serial below.
    user_concurrency = getattr(args, "concurrency", None)
    if user_concurrency is None:
        concurrency = 8 if total_workloads >= 25 else 1
        if concurrency > 1:
            print(f"  Auto-enabled concurrency={concurrency} (>= 25 workloads). Override with -c <N>.")
        else:
            print("  Single-threaded (small cluster). Override with -c <N>.")
    else:
        concurrency = max(1, min(32, user_concurrency))
        print(f"  User-specified concurrency={concurrency}")
    print()

    # ─── Collection pass ──────────────────────────────────────────────────
    print("Collecting workload data...")
    print()

    def _collect_one(workload, wtype, ns):
        """Wrapper for the pool — keeps exception handling local."""
        try:
            return (
                "ok",
                wtype,
                workload["name"],
                collect_workload_data(
                    workload,
                    wtype,
                    ns,
                    cluster,
                    use_prometheus=use_prometheus,
                    prom_port=9091,
                    auth=prom_auth,
                ),
            )
        except Exception as e:
            return ("err", wtype, workload["name"], str(e))

    for i, (namespace, targets) in enumerate(discovered, 1):
        print(f"[{i}/{len(discovered)}] {namespace}...", end=" ", flush=True)
        if not targets:
            print("0 workloads")
            continue

        ns_workloads = 0
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_collect_one, wl, wtype, namespace) for wl, wtype in targets]
            for fut in as_completed(futures):
                status, _, name, payload = fut.result()
                if status == "ok":
                    all_data.append(payload)
                    ns_workloads += 1
                else:
                    print(f"\n  ⚠️  Error collecting {name}: {payload}", file=sys.stderr)

        print(f"{ns_workloads} workloads")
        workload_count += ns_workloads

    # Print 403 notice now (after the collection summary line)
    if skipped_namespaces:
        print(f"  ⚠️  Skipped {len(skipped_namespaces)} namespace(s) (403 FORBIDDEN)")

    # Cleanup port-forward
    if pf_process:
        print()
        print("Cleaning up port-forward...")
        prom.cleanup_port_forward(pf_process)

    print()
    print("=" * 70)
    accessible = len(namespaces) - len(skipped_namespaces)
    print(f"✓ Collected data from {accessible} namespaces")
    if skipped_namespaces:
        print(f"⚠️  Skipped {len(skipped_namespaces)} namespace(s) (no permission): {', '.join(skipped_namespaces)}")
    print(f"✓ Total workloads: {workload_count}")
    print(f"✓ Prometheus metrics: {'Yes' if use_prometheus else 'No'}")
    print()

    if not all_data:
        if skipped_namespaces:
            print("✗ No data collected — all accessible namespaces were empty or forbidden", file=sys.stderr)
            print("  Check your RBAC permissions or specify namespaces with -n", file=sys.stderr)
        else:
            print("✗ No data collected", file=sys.stderr)
        sys.exit(1)

    # Create reports directory
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)

    # Write CSV with cluster name in filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = reports_dir / f"k8s-advisor_{cluster}_{timestamp}.csv"

    print(f"Writing CSV: {output_file}")
    with open(output_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_data[0].keys())
        writer.writeheader()
        writer.writerows(all_data)

    print(f"✓ Output: {output_file}")
    print()

    # Summary statistics
    print("=" * 70)
    print("Summary Statistics")
    print("=" * 70)
    print(f"Total Workloads:    {workload_count}")
    print(f"  Deployments:      {sum(1 for d in all_data if d['Workload_Type'] == 'Deployment')}")
    print(f"  StatefulSets:     {sum(1 for d in all_data if d['Workload_Type'] == 'StatefulSet')}")
    print()
    print(f"With HPA:           {sum(1 for d in all_data if d['Has_HPA'] == 'true')}")
    print(f"With PVCs:          {sum(1 for d in all_data if d['PVC_Count'] > 0)}")
    print(f"With OOM Kills:     {sum(1 for d in all_data if d['OOMKilled_Count'] > 0)}")
    print(f"With Restarts:      {sum(1 for d in all_data if d['Total_Restarts'] > 0)}")
    print()

    # Resource request statistics
    with_cpu_requests = sum(1 for d in all_data if d["CPU_Request(m)"] > 0)
    with_mem_requests = sum(1 for d in all_data if d["Mem_Request(Mi)"] > 0)
    with_cpu_limits = sum(1 for d in all_data if d["CPU_Limit(m)"] > 0)
    with_mem_limits = sum(1 for d in all_data if d["Mem_Limit(Mi)"] > 0)

    print(f"With CPU Requests:  {with_cpu_requests}/{workload_count} ({with_cpu_requests / workload_count * 100:.1f}%)")
    print(f"With Mem Requests:  {with_mem_requests}/{workload_count} ({with_mem_requests / workload_count * 100:.1f}%)")
    print(f"With CPU Limits:    {with_cpu_limits}/{workload_count} ({with_cpu_limits / workload_count * 100:.1f}%)")
    print(f"With Mem Limits:    {with_mem_limits}/{workload_count} ({with_mem_limits / workload_count * 100:.1f}%)")
    print()

    # Prometheus metrics availability
    if use_prometheus:
        with_cpu_p95 = sum(1 for d in all_data if d["CPU_P95(m)"] != "N/A")
        with_mem_volatility = sum(1 for d in all_data if d["Mem_Volatility_CV"] != "N/A")
        print(f"With CPU P95:       {with_cpu_p95}/{workload_count} ({with_cpu_p95 / workload_count * 100:.1f}%)")
        print(
            f"With Mem Volatility:{with_mem_volatility}/{workload_count} ({with_mem_volatility / workload_count * 100:.1f}%)"
        )
        print()

    print("=" * 70)
    print("Next Steps")
    print("=" * 70)
    print("Analyze collected data:")
    print(f"  python3 main.py analyze {output_file}")
    print()

    return str(output_file)


def cmd_analyze(args):
    """Analyze collected data and generate report."""
    print("=" * 70)
    print("K8s Scaling Advisor - Analysis")
    print("=" * 70)
    print()

    csv_file = args.csv_file
    formats = _parse_formats(args.format)

    # Create reports directory if it doesn't exist
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)

    # Use the integrated analyzer
    from k8s_advisor.simple_analyzer import analyze_csv_file

    try:
        written = analyze_csv_file(
            csv_path=csv_file,
            output_dir=str(reports_dir),
            generate_graphs=args.graphs,
            formats=formats,
        )
        print("\n✅ Analysis complete!")
        for fmt, path in written.items():
            print(f"📄 {fmt.upper()}: {path}")
        if args.graphs:
            print(f"📊 Graphs: {reports_dir}/graphs/")
    except Exception as e:
        print(f"\n✗ Analysis failed: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


def _parse_formats(raw: str) -> tuple:
    """Parse --format value (e.g. 'md,json' -> ('md', 'json'))."""
    parts = [p.strip().lower() for p in (raw or "md").split(",") if p.strip()]
    if not parts:
        return ("md",)
    # Preserve order while deduplicating.
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return tuple(out)


def cmd_report(args):
    """Full pipeline: collect data and analyze."""
    print("=" * 70)
    print("K8s Scaling Advisor - Full Report Pipeline")
    print("=" * 70)
    print()

    # Step 1: Collect data
    output_file = cmd_collect(args)

    # Step 2: Analyze
    print()
    print("=" * 70)
    print("Starting Analysis...")
    print("=" * 70)
    print()

    # Create args for analyze
    class AnalyzeArgs:
        """Minimal stand-in for argparse.Namespace consumed by cmd_analyze."""

        def __init__(self, csv_file, graphs, fmt):
            """Capture fields cmd_analyze expects."""
            self.csv_file = csv_file
            self.graphs = graphs
            self.format = fmt

    analyze_args = AnalyzeArgs(output_file, args.graphs, args.format)
    cmd_analyze(analyze_args)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="K8s Scaling Advisor - Unified CLI for data collection and analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Collect data from cluster
  python3 main.py collect

  # Analyze existing CSV
  python3 main.py analyze k8s_report_20260309_123456.csv

  # Analyze with graphs
  python3 main.py analyze k8s_report_20260309_123456.csv --graphs

  # Full pipeline (collect + analyze)
  python3 main.py report

  # Full pipeline with graphs
  python3 main.py report --graphs
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Collect command
    collect_parser = subparsers.add_parser("collect", help="Collect data from Kubernetes cluster")
    collect_parser.add_argument(
        "-n", "--namespace", action="append", dest="namespaces", help="Namespace to collect (can be repeated)"
    )
    collect_parser.add_argument(
        "--namespace-pattern", dest="namespace_pattern", help="Glob pattern for namespaces (e.g. 'app-*')"
    )
    collect_parser.add_argument(
        "--all-namespaces", action="store_true", help="Collect all namespaces (default if no -n specified)"
    )
    collect_parser.add_argument("--prometheus-user", help="Username for Prometheus Basic Auth")
    collect_parser.add_argument("--prometheus-password", help="Password for Prometheus Basic Auth")
    collect_parser.add_argument("--prometheus-token", help="Bearer token for Prometheus Token Auth")
    collect_parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=None,
        help="Parallel workload queries (1-32). Default: auto — single "
        "thread for small clusters (<25 workloads), 8 threads above "
        "that. Set explicitly to override.",
    )

    # Analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze collected data")
    analyze_parser.add_argument("csv_file", help="CSV file to analyze")
    analyze_parser.add_argument("-g", "--graphs", action="store_true", help="Generate graphs")
    analyze_parser.add_argument(
        "--format",
        default="md",
        help="Comma-separated output formats: md, json (default: md). Example: --format md,json",
    )

    # Report command (full pipeline)
    report_parser = subparsers.add_parser("report", help="Full pipeline: collect + analyze")
    report_parser.add_argument(
        "-n", "--namespace", action="append", dest="namespaces", help="Namespace to collect (can be repeated)"
    )
    report_parser.add_argument(
        "--namespace-pattern", dest="namespace_pattern", help="Glob pattern for namespaces (e.g. 'app-*')"
    )
    report_parser.add_argument(
        "--all-namespaces", action="store_true", help="Collect all namespaces (default if no -n specified)"
    )
    report_parser.add_argument("-g", "--graphs", action="store_true", help="Generate graphs")
    report_parser.add_argument(
        "--format",
        default="md",
        help="Comma-separated output formats: md, json (default: md). Example: --format md,json",
    )
    report_parser.add_argument("--prometheus-user", help="Username for Prometheus Basic Auth")
    report_parser.add_argument("--prometheus-password", help="Password for Prometheus Basic Auth")
    report_parser.add_argument("--prometheus-token", help="Bearer token for Prometheus Token Auth")
    report_parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=None,
        help="Parallel workload queries (1-32). Default: auto — single "
        "thread for small clusters (<25 workloads), 8 threads above "
        "that. Set explicitly to override.",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Execute command
    if args.command == "collect":
        cmd_collect(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "report":
        cmd_report(args)


if __name__ == "__main__":
    main()
