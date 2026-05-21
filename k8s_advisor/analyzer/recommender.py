"""Resource recommendation engine."""

from typing import Dict
from .models import ResourceRecommendation
from ..constants import (
    CPU_MIN_RECOMMENDED_M,
    CPU_REDUCTION_BASELINE_M,
    CPU_REDUCTION_MIN_SAVING_M,
)

# Memory request floor used across analyzer logic.
MEM_MIN_RECOMMENDED_MI = 16


def format_cpu(millicores: float) -> str:
    """Format CPU millicores to string (e.g., 100m, 1.5, 2)."""
    if millicores < 1000:
        return f"{int(millicores)}m"
    else:
        cpu_cores = millicores / 1000
        if cpu_cores == int(cpu_cores):
            return str(int(cpu_cores))
        return f"{cpu_cores:.1f}"


def format_memory(mebibytes: float) -> str:
    """Format memory Mi to string with appropriate unit."""
    if mebibytes < 1024:
        return f"{int(mebibytes)}Mi"
    else:
        gib = mebibytes / 1024
        if gib == int(gib):
            return f"{int(gib)}Gi"
        return f"{gib:.1f}Gi"


def recommend_resources(workload: Dict, has_prometheus: bool = True) -> ResourceRecommendation:
    """Generate resource recommendations for a workload.

    Args:
        workload: Parsed workload data
        has_prometheus: Whether Prometheus metrics are available

    Returns:
        ResourceRecommendation object
    """
    avg_cpu = workload['avg_cpu_usage_m']
    cpu_request = workload['cpu_request_m']
    cpu_limit = workload['cpu_limit_m']
    cpu_p95 = workload['cpu_p95_m']
    cpu_max = workload['cpu_max_m']
    cpu_throttle = workload['cpu_throttle_pct']

    avg_mem = workload['avg_mem_usage_mi']
    mem_request = workload['mem_request_mi']
    mem_limit = workload['mem_limit_mi']
    mem_p95 = workload['mem_p95_mi']
    mem_max = workload['mem_max_mi']

    rationale_parts = []

    # CPU Request Recommendation
    if cpu_request == 0:
        # No request set - use avg * 1.25 with minimum
        recommended_cpu_request = max(CPU_MIN_RECOMMENDED_M, avg_cpu * 1.25)
        rationale_parts.append(f"CPU request not set, recommend {format_cpu(recommended_cpu_request)} (avg * 1.25)")
    elif avg_cpu > cpu_request * 0.85:
        # Under-requested
        recommended_cpu_request = max(CPU_MIN_RECOMMENDED_M, avg_cpu * 1.25)
        if recommended_cpu_request > cpu_request:
            rationale_parts.append(f"CPU under-requested ({avg_cpu:.0f}m avg vs {cpu_request:.0f}m request), increase to {format_cpu(recommended_cpu_request)}")
        else:
            recommended_cpu_request = cpu_request
    elif avg_cpu < cpu_request * 0.50:
        # Over-requested - but respect guardrails
        recommended_cpu_request = max(CPU_MIN_RECOMMENDED_M, avg_cpu * 1.25)
        saving = cpu_request - recommended_cpu_request

        if cpu_request <= CPU_REDUCTION_BASELINE_M:
            # Don't recommend reducing small requests
            recommended_cpu_request = cpu_request
            rationale_parts.append(f"CPU request {format_cpu(cpu_request)} is at baseline, no reduction recommended")
        elif saving < CPU_REDUCTION_MIN_SAVING_M:
            # Saving too small
            recommended_cpu_request = cpu_request
            rationale_parts.append(f"CPU over-requested but saving <{CPU_REDUCTION_MIN_SAVING_M}m, keeping current")
        else:
            rationale_parts.append(f"CPU over-requested ({avg_cpu:.0f}m avg vs {cpu_request:.0f}m request), reduce to {format_cpu(recommended_cpu_request)}")
    else:
        # Request is reasonable
        recommended_cpu_request = cpu_request

    # CPU Limit Recommendation
    if cpu_throttle > 0 and has_prometheus:
        # Throttled - increase limit
        if cpu_p95 > 0 and cpu_max > 0:
            recommended_cpu_limit = max(
                100,  # Minimum 100m
                cpu_p95 * 1.2,
                cpu_max * 1.1,
                recommended_cpu_request * 1.5
            )
            rationale_parts.append(f"CPU throttled ({cpu_throttle:.1f}%), increase limit to {format_cpu(recommended_cpu_limit)}")
        else:
            recommended_cpu_limit = max(100, recommended_cpu_request * 2.0)
            rationale_parts.append(f"CPU throttled but no P95 data, increase limit to {format_cpu(recommended_cpu_limit)}")
    elif cpu_limit > 0 and cpu_limit > recommended_cpu_request * 3.0:
        # Limit is wastefully high
        target = max(100, recommended_cpu_request * 2.0)
        if has_prometheus and cpu_p95 > 0:
            target = max(target, cpu_p95 * 1.2)
        recommended_cpu_limit = target
        rationale_parts.append(f"CPU limit wastefully high, reduce to {format_cpu(recommended_cpu_limit)}")
    else:
        # Keep current limit (or no limit set)
        recommended_cpu_limit = cpu_limit if cpu_limit > 0 else 0

    # Memory Request Recommendation
    if mem_request == 0:
        recommended_mem_request = max(MEM_MIN_RECOMMENDED_MI, avg_mem * 1.25)
        rationale_parts.append(f"Memory request not set, recommend {format_memory(recommended_mem_request)} (avg * 1.25)")
    elif avg_mem > mem_request * 0.85:
        # Under-requested
        recommended_mem_request = max(MEM_MIN_RECOMMENDED_MI, avg_mem * 1.25)
        if recommended_mem_request > mem_request:
            rationale_parts.append(f"Memory under-requested ({avg_mem:.0f}Mi avg vs {mem_request:.0f}Mi request), increase to {format_memory(recommended_mem_request)}")
        else:
            recommended_mem_request = mem_request
    elif avg_mem < mem_request * 0.50:
        # Over-requested
        recommended_mem_request = max(MEM_MIN_RECOMMENDED_MI, avg_mem * 1.25)
        rationale_parts.append(f"Memory over-requested ({avg_mem:.0f}Mi avg vs {mem_request:.0f}Mi request), reduce to {format_memory(recommended_mem_request)}")
    else:
        # Request is reasonable
        recommended_mem_request = mem_request

    # Memory Limit Recommendation
    if workload['oom_killed_count'] > 0:
        # OOM killed - increase limit significantly
        if mem_p95 > 0:
            recommended_mem_limit = max(
                recommended_mem_request * 1.5,
                mem_p95 * 1.3,
                mem_max * 1.2
            )
        else:
            recommended_mem_limit = recommended_mem_request * 2.0
        rationale_parts.append(f"OOM killed {workload['oom_killed_count']}x, increase limit to {format_memory(recommended_mem_limit)}")
    elif mem_limit > 0 and avg_mem > mem_limit * 0.85:
        # Near limit
        recommended_mem_limit = max(
            mem_limit * 1.3,
            recommended_mem_request * 1.5
        )
        rationale_parts.append(f"Memory near limit ({avg_mem:.0f}Mi avg vs {mem_limit:.0f}Mi limit), increase to {format_memory(recommended_mem_limit)}")
    elif mem_limit > 0 and mem_limit > recommended_mem_request * 3.0:
        # Limit is wastefully high
        target = recommended_mem_request * 1.5
        if has_prometheus and mem_p95 > 0:
            target = max(target, mem_p95 * 1.2)
        recommended_mem_limit = target
        rationale_parts.append(f"Memory limit wastefully high, reduce to {format_memory(recommended_mem_limit)}")
    else:
        # Keep current limit
        recommended_mem_limit = mem_limit if mem_limit > 0 else 0

    # Determine if manual action required
    requires_manual = (
        cpu_request == 0 or
        mem_request == 0 or
        workload['oom_killed_count'] > 0 or
        (cpu_throttle > 0 and has_prometheus)
    )

    return ResourceRecommendation(
        cpu_request=format_cpu(recommended_cpu_request) if recommended_cpu_request > 0 else None,
        cpu_limit=format_cpu(recommended_cpu_limit) if recommended_cpu_limit > 0 else None,
        memory_request=format_memory(recommended_mem_request) if recommended_mem_request > 0 else None,
        memory_limit=format_memory(recommended_mem_limit) if recommended_mem_limit > 0 else None,
        rationale="; ".join(rationale_parts) if rationale_parts else "Resources appropriately sized",
        requires_manual_action=requires_manual
    )
