"""Integration-like tests for the CLI analyze command."""

import subprocess
import sys
from pathlib import Path


def _build_csv(csv_path: Path) -> None:
    headers = [
        "Cluster",
        "Namespace",
        "Workload_Type",
        "Deployment",
        "Replicas",
        "Pod_Count",
        "Avg_CPU_Usage(m)",
        "CPU_Request(m)",
        "CPU_Limit(m)",
        "CPU_Usage_Pct_Of_Request",
        "CPU_Usage_Pct_Of_Limit",
        "CPU_Throttle_Pct",
        "CPU_P50(m)",
        "CPU_P95(m)",
        "CPU_Max(m)",
        "CPU_StdDev(m)",
        "Avg_Mem_Usage(Mi)",
        "Mem_Request(Mi)",
        "Mem_Limit(Mi)",
        "Mem_Usage_Pct_Of_Request",
        "Mem_Usage_Pct_Of_Limit",
        "Mem_P50(Mi)",
        "Mem_P95(Mi)",
        "Mem_Max(Mi)",
        "Mem_StdDev(Mi)",
        "Mem_Volatility_CV",
        "OOMKilled_Count",
        "LastRestart_Reason",
        "Total_Restarts",
        "Max_Restarts_Per_Pod",
        "Restart_Rate_Per_Day",
        "Days_Since_Last_Restart",
        "Has_HPA",
        "HPA_Min_Replicas",
        "HPA_Max_Replicas",
        "PVC_Access_Mode",
        "PVC_Count",
        "Container_Count",
        "Key_Labels",
        "Detected_Issues",
    ]
    values = [
        "sandbox",
        "default",
        "Deployment",
        "orders-api",
        "2",
        "2",
        "120",
        "100",
        "250",
        "120",
        "48",
        "0",
        "80",
        "180",
        "240",
        "20",
        "300",
        "256",
        "512",
        "117",
        "58",
        "200",
        "320",
        "400",
        "40",
        "8",
        "0",
        "",
        "0",
        "0",
        "0",
        "N/A",
        "false",
        "N/A",
        "N/A",
        "N/A",
        "0",
        "1",
        "app=orders-api",
        "",
    ]
    csv_path.write_text(
        ",".join(headers) + "\n" + ",".join(values) + "\n",
        encoding="utf-8",
    )


def test_main_analyze_command_creates_report(tmp_path: Path):
    project_root = Path(__file__).resolve().parent.parent
    main_py = project_root / "main.py"
    csv_path = tmp_path / "k8s-advisor_sandbox_20260101_010101.csv"
    _build_csv(csv_path)

    result = subprocess.run(
        [sys.executable, str(main_py), "analyze", str(csv_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "Analysis complete!" in result.stdout
    generated_report = tmp_path / "reports" / "k8s-advisor_sandbox_20260101_010101.md"
    assert generated_report.exists()
