"""Tests for the public analyze API wrapper."""

from pathlib import Path

from k8s_advisor.analyze import analyze_csv


def test_analyze_csv_wrapper_generates_report(tmp_path: Path):
    csv_path = tmp_path / "k8s-advisor_sandbox_20260101_010101.csv"
    csv_path.write_text(
        ",".join(
            [
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
        )
        + "\n"
        + ",".join(
            [
                "sandbox",
                "default",
                "Deployment",
                "payments-api",
                "2",
                "2",
                "50",
                "100",
                "200",
                "50",
                "25",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "100",
                "128",
                "256",
                "78",
                "39",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
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
                "app=payments-api",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "reports"
    report_path = analyze_csv(str(csv_path), output_dir=str(output_dir))
    assert Path(report_path).exists()
