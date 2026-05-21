"""Aggregations over a list of WorkloadAnalysis used by the report renderer.

Pure data — no I/O, no template logic. The Jinja template iterates over the
output of build_render_context() to produce the final markdown.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field


@dataclass
class NamespaceRollup:
    """Per-namespace aggregate of priority counts and signed CPU/Mem deltas.

    Built by `build_namespace_rollups` and consumed by the markdown
    template's Namespace Rollup section. The split between savings and
    raises lets a reader weigh "this namespace gives back capacity" vs
    "this namespace needs more capacity" without doing arithmetic in
    their head.
    """

    namespace: str
    workload_count: int = 0
    p0: int = 0
    p1: int = 0
    p2: int = 0
    p3: int = 0
    cpu_savings_m: float = 0.0  # sum of positive cpu_delta_m
    cpu_raises_m: float = 0.0  # sum of |negative cpu_delta_m|
    mem_savings_mi: float = 0.0
    mem_raises_mi: float = 0.0
    insufficient_data: int = 0
    owners: list[str] = field(default_factory=list)  # distinct, sorted


@dataclass
class TopEntry:
    """One row of a Top-N leaderboard (CPU savers, Mem savers, Highest Risk)."""

    namespace: str
    deployment: str
    workload_type: str
    value: float
    note: str = ""


@dataclass
class PatternGroup:
    """A set of workloads sharing a name-prefix and a priority bucket.

    These are usually instances of the same chart/operator (e.g. 38 identical
    rook-ceph-crashcollector-* deployments) — fix the chart once instead of
    filing 38 tickets.
    """

    namespace: str
    prefix: str
    priority: str
    workloads: list[str] = field(default_factory=list)
    cpu_savings_m: float = 0.0
    mem_savings_mi: float = 0.0
    cpu_raises_m: float = 0.0  # absolute additional CPU needed
    mem_raises_mi: float = 0.0  # absolute additional memory needed
    insufficient_data: int = 0


def _name_prefix(name: str) -> str:
    """Extract a chart-style prefix.

    Strategy: strip a trailing alphanumeric token chain that looks like a
    pod-suffix (random hashes, ordinals, node hostnames). For deployments
    these tend to be things like
    'rook-ceph-crashcollector-node-abc123' → 'rook-ceph-crashcollector'.
    """
    # Split on '-' and rebuild while the trailing parts look generated.
    parts = name.rsplit("-", 1)
    if (
        len(parts) == 2
        and parts[1]
        and (parts[1].isalnum() and (any(c.isdigit() for c in parts[1]) or len(parts[1]) >= 6))
    ):
        return parts[0]
    return name


def _priority_int(p) -> int:
    """Map Priority enum (or its .value) to an int 0..3."""
    v = getattr(p, "value", p)
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(v, 3)


def build_namespace_rollups(analyses: Sequence) -> list[NamespaceRollup]:
    """Group analyses by namespace and aggregate priority counts + deltas.

    Output is sorted by P0 descending, then by total savings descending.
    Used by the report's Namespace Rollup section.
    """
    rollups = defaultdict(lambda: NamespaceRollup(namespace=""))
    seen_owners: dict = defaultdict(set)
    for a in analyses:
        ns = a.namespace
        r = rollups[ns]
        r.namespace = ns
        r.workload_count += 1
        owner = getattr(a, "owner", "")
        if owner:
            seen_owners[ns].add(owner)
        p = _priority_int(a.priority)
        if p == 0:
            r.p0 += 1
        elif p == 1:
            r.p1 += 1
        elif p == 2:
            r.p2 += 1
        else:
            r.p3 += 1
        if getattr(a, "insufficient_data", False):
            r.insufficient_data += 1
        d_cpu = getattr(a, "cpu_delta_m", 0.0)
        d_mem = getattr(a, "mem_delta_mi", 0.0)
        if d_cpu > 0:
            r.cpu_savings_m += d_cpu
        elif d_cpu < 0:
            r.cpu_raises_m += -d_cpu
        if d_mem > 0:
            r.mem_savings_mi += d_mem
        elif d_mem < 0:
            r.mem_raises_mi += -d_mem
    # Stitch owners onto each rollup before sorting.
    for ns, r in rollups.items():
        r.owners = sorted(seen_owners.get(ns, ()))
    # Order by P0 desc, then total savings desc, then name.
    return sorted(
        rollups.values(),
        key=lambda r: (-r.p0, -(r.cpu_savings_m + r.mem_savings_mi), r.namespace),
    )


def build_top_savers(analyses: Sequence, limit: int = 10) -> dict:
    """Three lists: top-N CPU savers, top-N mem savers, top-N highest risk."""
    cpu = []
    mem = []
    risk = []
    for a in analyses:
        d_cpu = getattr(a, "cpu_delta_m", 0.0)
        d_mem = getattr(a, "mem_delta_mi", 0.0)
        if d_cpu > 0:
            cpu.append(
                TopEntry(
                    namespace=a.namespace,
                    deployment=a.deployment,
                    workload_type=a.workload_type,
                    value=d_cpu,
                    note=f"{int(d_cpu)}m saved",
                )
            )
        if d_mem > 0:
            mem.append(
                TopEntry(
                    namespace=a.namespace,
                    deployment=a.deployment,
                    workload_type=a.workload_type,
                    value=d_mem,
                    note=_format_mi(d_mem) + " saved",
                )
            )
        risk_score = (
            (10 if "OOM_KILLED" in a.issues else 0)
            + (5 if "CPU_THROTTLED" in a.issues else 0)
            + (3 if "MEM_SATURATION" in a.issues else 0)
            + (2 if "UNSTABLE" in a.issues else 0)
            + (1 if "REQUESTS_NOT_SET" in a.issues else 0)
        )
        if risk_score > 0:
            note_bits = []
            if "OOM_KILLED" in a.issues:
                note_bits.append(f"{a.oom_kills}× OOM")
            if "CPU_THROTTLED" in a.issues:
                note_bits.append(f"throttle {a.cpu_throttle_pct:.0f}%")
            if "MEM_SATURATION" in a.issues:
                note_bits.append("mem saturation")
            if "UNSTABLE" in a.issues:
                if a.total_restarts > 0:
                    note_bits.append(f"{a.total_restarts} restarts")
                elif a.restart_rate > 0:
                    note_bits.append(f"{a.restart_rate:.1f}/day restarts")
                elif a.max_restarts_per_pod > 0:
                    note_bits.append(f"{a.max_restarts_per_pod}/pod restarts")
                else:
                    note_bits.append("unstable")
            # Surface the crash signal alongside the restart count so an
            # admin scanning the Top-10 leaderboard knows whether they're
            # looking at an OOM, a panic, or a clean rolling restart.
            crash_signal = getattr(a, "crash_signal", "")
            if crash_signal and a.total_restarts > 0:
                note_bits.append(crash_signal)
            risk.append(
                TopEntry(
                    namespace=a.namespace,
                    deployment=a.deployment,
                    workload_type=a.workload_type,
                    value=risk_score,
                    note=", ".join(note_bits) or "issue",
                )
            )

    cpu.sort(key=lambda e: -e.value)
    mem.sort(key=lambda e: -e.value)
    risk.sort(key=lambda e: -e.value)

    return {
        "cpu": cpu[:limit],
        "mem": mem[:limit],
        "risk": risk[:limit],
    }


def build_pattern_groups(analyses: Sequence, min_size: int = 3) -> list[PatternGroup]:
    """Group workloads sharing (namespace, name-prefix, priority).

    Only returns groups of size >= min_size. These are typically Helm-chart
    instances where one fix at the chart level resolves all members.
    """
    groups = defaultdict(lambda: PatternGroup(namespace="", prefix="", priority=""))
    for a in analyses:
        prefix = _name_prefix(a.deployment)
        if prefix == a.deployment:
            # No common prefix detected — skip; not a pattern.
            continue
        key = (a.namespace, prefix, a.priority.value if hasattr(a.priority, "value") else a.priority)
        g = groups[key]
        g.namespace = a.namespace
        g.prefix = prefix
        g.priority = key[2]
        g.workloads.append(a.deployment)
        if getattr(a, "insufficient_data", False):
            g.insufficient_data += 1
        d_cpu = getattr(a, "cpu_delta_m", 0.0)
        d_mem = getattr(a, "mem_delta_mi", 0.0)
        if d_cpu > 0:
            g.cpu_savings_m += d_cpu
        elif d_cpu < 0:
            g.cpu_raises_m += -d_cpu
        if d_mem > 0:
            g.mem_savings_mi += d_mem
        elif d_mem < 0:
            g.mem_raises_mi += -d_mem

    result = [g for g in groups.values() if len(g.workloads) >= min_size]
    # Sort by group size desc, then by priority asc.
    return sorted(result, key=lambda g: (-len(g.workloads), g.priority))


def _format_mi(mi: float) -> str:
    """Render a MiB value as a short string (e.g. 768Mi, 2.0Gi)."""
    if mi < 1024:
        return f"{int(mi)}Mi"
    return f"{mi / 1024:.1f}Gi"


def fleet_totals(rollups: Iterable[NamespaceRollup]) -> dict:
    """Sum across all namespaces."""
    cpu_savings = sum(r.cpu_savings_m for r in rollups)
    cpu_raises = sum(r.cpu_raises_m for r in rollups)
    mem_savings = sum(r.mem_savings_mi for r in rollups)
    mem_raises = sum(r.mem_raises_mi for r in rollups)
    return {
        "cpu_savings_m": cpu_savings,
        "cpu_raises_m": cpu_raises,
        "mem_savings_mi": mem_savings,
        "mem_raises_mi": mem_raises,
    }
