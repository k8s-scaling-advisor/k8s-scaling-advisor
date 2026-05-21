"""CSV data loader for K8s deployment analysis."""

import csv
from pathlib import Path


def load_csv(csv_path: str) -> list[dict]:
    """Load deployment data from CSV file.

    Args:
        csv_path: Path to CSV file

    Returns:
        List of dictionaries, one per workload
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    workloads = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            workloads.append(row)

    return workloads


def safe_float(value: str, default: float = 0.0) -> float:
    """Safely convert string to float, handling N/A and empty values."""
    if not value or value.strip() in ("N/A", "n/a", "", "-"):
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value: str, default: int = 0) -> int:
    """Safely convert string to int, handling N/A and empty values."""
    if not value or value.strip() in ("N/A", "n/a", "", "-"):
        return default
    try:
        return int(float(value))  # Handle "1.0" format
    except (ValueError, TypeError):
        return default


def safe_bool(value: str) -> bool:
    """Safely convert string to bool."""
    if isinstance(value, bool):
        return value
    if not value:
        return False
    return str(value).strip().lower() in ("true", "yes", "1", "y")


def parse_workload(row: dict) -> dict:
    """Parse CSV row into structured workload data.

    Args:
        row: Raw CSV row dictionary

    Returns:
        Parsed workload dictionary with proper types
    """
    return {
        # Identity
        "cluster": row.get("Cluster", ""),
        "namespace": row.get("Namespace", ""),
        "workload_type": row.get("Workload_Type", "Deployment"),
        "deployment": row.get("Deployment", ""),
        "replicas": safe_int(row.get("Replicas", "0")),
        "pod_count": safe_int(row.get("Pod_Count", "0")),
        # CPU metrics
        "avg_cpu_usage_m": safe_float(row.get("Avg_CPU_Usage(m)", "0")),
        "cpu_request_m": safe_float(row.get("CPU_Request(m)", "0")),
        "cpu_limit_m": safe_float(row.get("CPU_Limit(m)", "0")),
        "cpu_usage_pct_request": safe_float(row.get("CPU_Usage_Pct_Of_Request", "0")),
        "cpu_usage_pct_limit": safe_float(row.get("CPU_Usage_Pct_Of_Limit", "0")),
        "cpu_throttle_pct": safe_float(row.get("CPU_Throttle_Pct", "0")),
        "cpu_p50_m": safe_float(row.get("CPU_P50(m)", "0")),
        "cpu_p95_m": safe_float(row.get("CPU_P95(m)", "0")),
        "cpu_max_m": safe_float(row.get("CPU_Max(m)", "0")),
        "cpu_stddev_m": safe_float(row.get("CPU_StdDev(m)", "0")),
        # Memory metrics
        "avg_mem_usage_mi": safe_float(row.get("Avg_Mem_Usage(Mi)", "0")),
        "mem_request_mi": safe_float(row.get("Mem_Request(Mi)", "0")),
        "mem_limit_mi": safe_float(row.get("Mem_Limit(Mi)", "0")),
        "mem_usage_pct_request": safe_float(row.get("Mem_Usage_Pct_Of_Request", "0")),
        "mem_usage_pct_limit": safe_float(row.get("Mem_Usage_Pct_Of_Limit", "0")),
        "mem_p50_mi": safe_float(row.get("Mem_P50(Mi)", "0")),
        "mem_p95_mi": safe_float(row.get("Mem_P95(Mi)", "0")),
        "mem_max_mi": safe_float(row.get("Mem_Max(Mi)", "0")),
        "mem_stddev_mi": safe_float(row.get("Mem_StdDev(Mi)", "0")),
        "mem_volatility_cv": safe_float(row.get("Mem_Volatility_CV", "0")),
        # Restart info
        "oom_killed_count": safe_int(row.get("OOMKilled_Count", "0")),
        "last_restart_reason": row.get("LastRestart_Reason", ""),
        "total_restarts": safe_int(row.get("Total_Restarts", "0")),
        "max_restarts_per_pod": safe_int(row.get("Max_Restarts_Per_Pod", "0")),
        "restart_rate_per_day": safe_float(row.get("Restart_Rate_Per_Day", "0")),
        "days_since_last_restart": safe_float(row.get("Days_Since_Last_Restart", "0")),
        # HPA info
        "has_hpa": safe_bool(row.get("Has_HPA", "False")),
        "hpa_min_replicas": safe_int(row.get("HPA_Min_Replicas", "0")),
        "hpa_max_replicas": safe_int(row.get("HPA_Max_Replicas", "0")),
        # PVC info
        "pvc_access_mode": row.get("PVC_Access_Mode", ""),
        "pvc_count": safe_int(row.get("PVC_Count", "0")),
        # Additional
        "container_count": safe_int(row.get("Container_Count", "1")),
        "key_labels": row.get("Key_Labels", ""),
        "detected_issues": row.get("Detected_Issues", ""),
        # Raw row for reference
        "_raw": row,
    }
