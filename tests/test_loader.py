"""Tests for CSV loading and parsing helpers."""

from pathlib import Path

import pytest

from k8s_advisor.analyzer.loader import (
    load_csv,
    parse_workload,
    safe_bool,
    safe_float,
    safe_int,
)


def test_safe_float_handles_na_and_invalid():
    assert safe_float("N/A") == 0.0
    assert safe_float("-") == 0.0
    assert safe_float("") == 0.0
    assert safe_float("abc", default=1.2) == 1.2
    assert safe_float("12.5") == 12.5


def test_safe_int_handles_float_strings_and_invalid():
    assert safe_int("N/A") == 0
    assert safe_int("3.0") == 3
    assert safe_int("abc", default=9) == 9


def test_safe_bool_parsing():
    assert safe_bool("true") is True
    assert safe_bool("YES") is True
    assert safe_bool("1") is True
    assert safe_bool("false") is False
    assert safe_bool("") is False


def test_load_csv_reads_rows(tmp_path: Path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("A,B\n1,2\n", encoding="utf-8")

    rows = load_csv(str(csv_path))
    assert rows == [{"A": "1", "B": "2"}]


def test_load_csv_raises_on_missing_file(tmp_path: Path):
    missing = tmp_path / "missing.csv"
    with pytest.raises(FileNotFoundError):
        load_csv(str(missing))


def test_parse_workload_converts_types():
    row = {
        "Cluster": "sandbox",
        "Namespace": "default",
        "Workload_Type": "Deployment",
        "Deployment": "api",
        "Replicas": "2",
        "Pod_Count": "2",
        "Avg_CPU_Usage(m)": "10.5",
        "CPU_Request(m)": "50",
        "CPU_Limit(m)": "100",
        "CPU_Usage_Pct_Of_Request": "21",
        "CPU_Usage_Pct_Of_Limit": "10.5",
        "CPU_Throttle_Pct": "N/A",
        "CPU_P50(m)": "5.5",
        "CPU_P95(m)": "15.1",
        "CPU_Max(m)": "20",
        "CPU_StdDev(m)": "1.2",
        "Avg_Mem_Usage(Mi)": "120",
        "Mem_Request(Mi)": "128",
        "Mem_Limit(Mi)": "256",
        "Mem_Usage_Pct_Of_Request": "93.7",
        "Mem_Usage_Pct_Of_Limit": "46.8",
        "Mem_P50(Mi)": "100",
        "Mem_P95(Mi)": "130",
        "Mem_Max(Mi)": "150",
        "Mem_StdDev(Mi)": "8",
        "Mem_Volatility_CV": "6.1",
        "OOMKilled_Count": "0",
        "LastRestart_Reason": "",
        "Total_Restarts": "1",
        "Max_Restarts_Per_Pod": "1",
        "Restart_Rate_Per_Day": "0.3",
        "Days_Since_Last_Restart": "2",
        "Has_HPA": "true",
        "HPA_Min_Replicas": "2",
        "HPA_Max_Replicas": "8",
        "PVC_Access_Mode": "N/A",
        "PVC_Count": "0",
        "Container_Count": "1",
        "Key_Labels": "app=api",
        "Detected_Issues": "",
    }
    parsed = parse_workload(row)

    assert parsed["replicas"] == 2
    assert parsed["avg_cpu_usage_m"] == 10.5
    assert parsed["has_hpa"] is True
    assert parsed["mem_volatility_cv"] == 6.1
