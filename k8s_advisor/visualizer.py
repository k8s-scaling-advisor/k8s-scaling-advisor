"""Graph generation for K8s Scaling Advisor.

The graphs are rendered from `WorkloadAnalysis` objects — the same source of
truth the markdown report uses. This is intentional: previously the visualizer
re-derived priorities and savings from CSV columns with stale rules, producing
a pie chart that disagreed with the report's priority table by ~60 workloads.
Sharing one source eliminates that whole class of bug.
"""

import csv
import sys
from collections.abc import Sequence
from pathlib import Path


def safe_float(value, default=0.0):
    """Coerce a CSV cell to float, returning `default` for `N/A`/empty/junk."""
    if not value or value in ("N/A", "", "-"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------------
# Public entry points
# ----------------------------------------------------------------------------


def render_graphs(analyses: Sequence, output_dir: str = "reports/graphs") -> bool:
    """Render the 6 PNG charts from a list of WorkloadAnalysis objects.

    This is the canonical entry point — the markdown renderer and any future
    integrations should call this directly so we don't duplicate logic.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: F401
        import numpy as np  # noqa: F401
    except ImportError:
        print("⚠️  Matplotlib not installed - skipping graphs")
        print("   Install with: pip install -e .[viz]")
        return False

    if not analyses:
        print("⚠️  No analyses to visualize")
        return False

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    generated = 0
    try:
        if _resource_efficiency(analyses, output_path):
            generated += 1
        if _top_over_requested(analyses, output_path):
            generated += 1
        if _top_under_requested(analyses, output_path):
            generated += 1
        if _priority_distribution(analyses, output_path):
            generated += 1
        if _stability_analysis(analyses, output_path):
            generated += 1
        if _namespace_risk(analyses, output_path):
            generated += 1
        if _fleet_capacity(analyses, output_path):
            generated += 1
        if _pattern_group_impact(analyses, output_path):
            generated += 1
        if _p95_vs_request(analyses, output_path):
            generated += 1
        print(f"✅ Generated {generated} graphs in {output_path}")
        return generated > 0
    except Exception as e:
        print(f"⚠️  Graph generation failed: {e}")
        return False


def generate_graphs(csv_path: str, output_dir: str = "reports/graphs") -> bool:
    """Backward-compatible CSV entry point.

    Re-runs the analyzer on the CSV so the resulting graphs share logic with
    a fresh report, then delegates to `render_graphs`.
    """
    print(f"📊 Generating graphs from: {csv_path}")
    from k8s_advisor.simple_analyzer import analyze_workload, check_prometheus

    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("⚠️  No data to visualize")
        return False
    print(f"✅ Loaded {len(rows)} workloads")
    has_prometheus = check_prometheus(rows)
    analyses = [analyze_workload(r, has_prometheus) for r in rows]
    return render_graphs(analyses, output_dir)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _short_label(analysis, max_len: int = 50) -> str:
    """Right-truncate a `namespace/deployment` label without losing the
    deployment name (which is the meaningful part)."""
    deployment = analysis.deployment
    namespace = analysis.namespace
    full = f"{namespace}/{deployment}"
    if len(full) <= max_len:
        return full
    # Keep namespace prefix + ellipsis + tail of deployment name.
    keep = max_len - 1
    if len(deployment) >= keep:
        return "…" + deployment[-(keep - 1) :]
    available = keep - len(deployment) - 2  # 2 for "/…"
    if available <= 0:
        return "…" + deployment
    return f"{namespace[:available]}…/{deployment}"


def _is_excluded_from_savings(analysis) -> bool:
    """Workloads we should not present as easy 'over-requested' wins.

    Bursty (Prometheus/Cassandra/Kafka/ES) and GC-runtime (JVM/Node) memory
    metrics under-represent peak working set; UNSTABLE / INSUFFICIENT_DATA
    workloads should be stabilized before any rightsizing.
    """
    if getattr(analysis, "bursty_workload", False):
        return True
    if getattr(analysis, "gc_runtime", False):
        return True
    if getattr(analysis, "insufficient_data", False):
        return True
    return "UNSTABLE" in getattr(analysis, "issues", [])


# ----------------------------------------------------------------------------
# Graph 1 — Resource efficiency scatter
# ----------------------------------------------------------------------------


def _resource_efficiency(analyses: Sequence, output_path: Path) -> bool:
    """Render the CPU vs Memory usage scatter (graph 1)."""
    import matplotlib.pyplot as plt

    pts = []
    for a in analyses:
        if a.cpu_request <= 0 or a.mem_request <= 0:
            continue
        cpu_pct = (a.avg_cpu / a.cpu_request) * 100
        mem_pct = (a.avg_mem / a.mem_request) * 100
        if cpu_pct == 0 and mem_pct == 0:
            continue
        # Cap at 300% only for plotting; remember the true values for labels.
        pts.append(
            {
                "label": _short_label(a, 30),
                "cpu_true": cpu_pct,
                "mem_true": mem_pct,
                "cpu_clip": min(cpu_pct, 300),
                "mem_clip": min(mem_pct, 300),
            }
        )
    if not pts:
        return False

    def color_of(cpu, mem):
        """Pick a marker color based on CPU/Mem usage % buckets."""
        if cpu < 50 or mem < 50:
            return "red"
        if cpu > 200 or mem > 200:
            return "orange"
        if 85 < cpu < 200 and 85 < mem < 200:
            return "green"
        return "gold"

    _fig, ax = plt.subplots(figsize=(12, 8))
    colors = [color_of(p["cpu_clip"], p["mem_clip"]) for p in pts]
    ax.scatter(
        [p["cpu_clip"] for p in pts],
        [p["mem_clip"] for p in pts],
        c=colors,
        alpha=0.55,
        s=80,
        edgecolors="black",
        linewidths=0.3,
    )

    for p in pts:
        if p["cpu_true"] > 200 or p["mem_true"] > 200:
            ax.annotate(
                p["label"],
                (p["cpu_clip"], p["mem_clip"]),
                xytext=(5, 5),
                textcoords="offset points",
                fontsize=7,
                alpha=0.85,
            )

    for x in (50, 85, 200):
        ax.axvline(x=x, color="gray", linestyle="--", alpha=0.25)
        ax.axhline(y=x, color="gray", linestyle="--", alpha=0.25)

    ax.set_xlabel("CPU usage % of request", fontsize=12)
    ax.set_ylabel("Memory usage % of request", fontsize=12)
    ax.set_title("Resource Efficiency — CPU vs Memory Usage", fontsize=14, fontweight="bold")
    ax.set_xlim(left=-5)
    ax.set_ylim(bottom=-5)
    ax.grid(True, alpha=0.3)

    from matplotlib.patches import Patch

    ax.legend(
        handles=[
            Patch(facecolor="green", label="Efficient (CPU & Mem 85–200%)"),
            Patch(facecolor="gold", label="Acceptable"),
            Patch(facecolor="red", label="Over-requested (<50%)"),
            Patch(facecolor="orange", label="Under-requested (>200%, capped)"),
        ],
        loc="upper right",
    )

    plt.tight_layout()
    plt.savefig(output_path / "1_resource_efficiency.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  ✓ Generated 1_resource_efficiency.png")
    return True


# ----------------------------------------------------------------------------
# Graph 2 — Top over-requested (savings)
# ----------------------------------------------------------------------------


def _top_over_requested(analyses: Sequence, output_path: Path) -> bool:
    """Render the Top-10 over-requested CPU + Mem savers (graph 2).

    BURSTY / GC_RUNTIME / UNSTABLE workloads are excluded — see
    `_is_excluded_from_savings` for rationale.
    """
    import matplotlib.pyplot as plt
    import numpy as np

    cpu_savers = []
    mem_savers = []
    for a in analyses:
        if _is_excluded_from_savings(a):
            continue
        if a.cpu_delta_m > 0:
            cpu_savers.append((a, a.cpu_delta_m))
        if a.mem_delta_mi > 0:
            mem_savers.append((a, a.mem_delta_mi))

    if not cpu_savers and not mem_savers:
        return False

    cpu_top = sorted(cpu_savers, key=lambda x: -x[1])[:10]
    mem_top = sorted(mem_savers, key=lambda x: -x[1])[:10]

    _, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

    if cpu_top:
        names = [_short_label(a, 50) for a, _ in cpu_top]
        vals = [v for _, v in cpu_top]
        y = np.arange(len(names))
        ax1.barh(y, vals, color="coral")
        ax1.set_yticks(y)
        ax1.set_yticklabels(names, fontsize=9)
        ax1.invert_yaxis()
        ax1.set_xlabel("CPU savings (millicores)", fontsize=11)
        ax1.grid(axis="x", alpha=0.3)
    else:
        ax1.text(
            0.5, 0.5, "No CPU savings recommendations", ha="center", va="center", transform=ax1.transAxes, fontsize=11
        )
    ax1.set_title("Top 10 Over-Requested — CPU\n(BURSTY / GC / UNSTABLE excluded)", fontsize=12, fontweight="bold")

    if mem_top:
        names = [_short_label(a, 50) for a, _ in mem_top]
        vals = [v for _, v in mem_top]
        y = np.arange(len(names))
        ax2.barh(y, vals, color="lightblue")
        ax2.set_yticks(y)
        ax2.set_yticklabels(names, fontsize=9)
        ax2.invert_yaxis()
        ax2.set_xlabel("Memory savings (Mi)", fontsize=11)
        ax2.grid(axis="x", alpha=0.3)
    else:
        ax2.text(
            0.5,
            0.5,
            "No memory savings recommendations",
            ha="center",
            va="center",
            transform=ax2.transAxes,
            fontsize=11,
        )
    ax2.set_title("Top 10 Over-Requested — Memory\n(BURSTY / GC / UNSTABLE excluded)", fontsize=12, fontweight="bold")

    plt.tight_layout()
    plt.savefig(output_path / "2_top_over_requested.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  ✓ Generated 2_top_over_requested.png")
    return True


# ----------------------------------------------------------------------------
# Graph 3 — Top under-requested (raises)
# ----------------------------------------------------------------------------


def _top_under_requested(analyses: Sequence, output_path: Path) -> bool:
    """Render the Top-10 under-requested CPU + Mem raises (graph 3)."""
    import matplotlib.pyplot as plt
    import numpy as np

    cpu_raises = []
    mem_raises = []
    for a in analyses:
        if getattr(a, "insufficient_data", False):
            continue
        if a.cpu_delta_m < 0:
            cpu_raises.append((a, -a.cpu_delta_m))
        if a.mem_delta_mi < 0:
            mem_raises.append((a, -a.mem_delta_mi))

    if not cpu_raises and not mem_raises:
        return False

    cpu_top = sorted(cpu_raises, key=lambda x: -x[1])[:10]
    mem_top = sorted(mem_raises, key=lambda x: -x[1])[:10]

    _fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

    def _draw(ax, top, color, xlabel, title):
        """Draw a horizontal bar chart on `ax` for one panel of graph 3."""
        if top:
            names = [_short_label(a, 50) for a, _ in top]
            vals = [v for _, v in top]
            y = np.arange(len(names))
            ax.barh(y, vals, color=color)
            ax.set_yticks(y)
            ax.set_yticklabels(names, fontsize=9)
            ax.invert_yaxis()
            ax.set_xlabel(xlabel, fontsize=11)
            ax.grid(axis="x", alpha=0.3)
        else:
            ax.text(0.5, 0.5, "No raise recommendations", ha="center", va="center", transform=ax.transAxes, fontsize=11)
        ax.set_title(title, fontsize=12, fontweight="bold")

    _draw(ax1, cpu_top, "orange", "Additional CPU needed (millicores)", "Top 10 Under-Requested — CPU")
    _draw(ax2, mem_top, "gold", "Additional memory needed (Mi)", "Top 10 Under-Requested — Memory")

    plt.tight_layout()
    plt.savefig(output_path / "3_top_under_requested.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  ✓ Generated 3_top_under_requested.png")
    return True


# ----------------------------------------------------------------------------
# Graph 4 — Priority distribution
# ----------------------------------------------------------------------------


def _priority_distribution(analyses: Sequence, output_path: Path) -> bool:
    """Render the P0/P1/P2/P3 pie chart (graph 4).

    Reads `priority` directly off each analysis — same source the
    markdown report uses, so the pie can never disagree with the
    Priority Distribution table.
    """
    import matplotlib.pyplot as plt

    p0 = sum(1 for a in analyses if a.priority.value == "P0")
    p1 = sum(1 for a in analyses if a.priority.value == "P1")
    p2 = sum(1 for a in analyses if a.priority.value == "P2")
    p3 = sum(1 for a in analyses if a.priority.value == "P3")

    counts = [p0, p1, p2, p3]
    labels = [
        f"P0 Blocker\n({p0})",
        f"P1 High\n({p1})",
        f"P2 Medium\n({p2})",
        f"P3 Low\n({p3})",
    ]
    colors = ["#ff4444", "#ff8844", "#ffdd44", "#44ff44"]
    if sum(counts) == 0:
        return False

    # Filter out empty wedges so the pie doesn't draw 0% slices.
    visible = [(c, lbl, col) for c, lbl, col in zip(counts, labels, colors, strict=False) if c > 0]
    counts, labels, colors = zip(*visible, strict=False)

    _fig, ax = plt.subplots(figsize=(10, 8))
    _wedges, _texts, autotexts = ax.pie(
        counts,
        labels=labels,
        colors=colors,
        autopct="%1.1f%%",
        startangle=90,
    )
    for autotext in autotexts:
        autotext.set_color("black")
        autotext.set_fontweight("bold")
        autotext.set_fontsize(12)
    ax.set_title("Workload Priority Distribution", fontsize=14, fontweight="bold", pad=20)

    plt.tight_layout()
    plt.savefig(output_path / "4_priority_distribution.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  ✓ Generated 4_priority_distribution.png")
    return True


# ----------------------------------------------------------------------------
# Graph 6 — Stability (OOM + restarts)
# ----------------------------------------------------------------------------


def _stability_analysis(analyses: Sequence, output_path: Path) -> bool:
    """Render the OOM-kills + restart-activity dual-panel chart (graph 6)."""
    import matplotlib.pyplot as plt
    import numpy as np

    ooms = sorted(
        ((a, a.oom_kills) for a in analyses if a.oom_kills > 0),
        key=lambda x: -x[1],
    )[:10]

    # Use restart_rate when available; fall back to total_restarts so the
    # chart shows real signal in kubectl-only runs too.
    restart_candidates = []
    for a in analyses:
        score = a.restart_rate if a.restart_rate > 0 else a.total_restarts
        if score > 0:
            restart_candidates.append((a, score, "/day" if a.restart_rate > 0 else " total"))
    restarts = sorted(restart_candidates, key=lambda x: -x[1])[:10]

    if not ooms and not restarts:
        return False

    _fig, axes = plt.subplots(2, 1, figsize=(14, 10))
    ax1, ax2 = axes

    if ooms:
        names = [_short_label(a, 60) for a, _ in ooms]
        vals = [v for _, v in ooms]
        y = np.arange(len(names))
        ax1.barh(y, vals, color="darkred", alpha=0.75)
        ax1.set_yticks(y)
        ax1.set_yticklabels(names, fontsize=9)
        ax1.invert_yaxis()
        ax1.set_xlabel("OOM kill count", fontsize=11)
        ax1.grid(axis="x", alpha=0.3)
    else:
        ax1.text(
            0.5,
            0.5,
            "No OOM kills detected — good",
            ha="center",
            va="center",
            transform=ax1.transAxes,
            fontsize=12,
            color="green",
            fontweight="bold",
        )
        ax1.set_xticks([])
        ax1.set_yticks([])
    ax1.set_title("Workloads with OOM Kills (Top 10)", fontsize=12, fontweight="bold")

    if restarts:
        # Mixed-units title — annotate per-bar to avoid confusion.
        names = [_short_label(a, 60) for a, _, _ in restarts]
        vals = [v for _, v, _ in restarts]
        suffixes = [s for _, _, s in restarts]
        y = np.arange(len(names))
        bars = ax2.barh(y, vals, color="darkorange", alpha=0.75)
        ax2.set_yticks(y)
        ax2.set_yticklabels(names, fontsize=9)
        ax2.invert_yaxis()
        ax2.set_xlabel("Restarts (rate per day, fallback to total count)", fontsize=11)
        ax2.grid(axis="x", alpha=0.3)
        for bar, suffix in zip(bars, suffixes, strict=False):
            ax2.text(
                bar.get_width(), bar.get_y() + bar.get_height() / 2, f" {suffix}", va="center", fontsize=8, alpha=0.7
            )
    else:
        ax2.text(
            0.5,
            0.5,
            "No restart activity detected",
            ha="center",
            va="center",
            transform=ax2.transAxes,
            fontsize=12,
            color="green",
            fontweight="bold",
        )
        ax2.set_xticks([])
        ax2.set_yticks([])
    ax2.set_title("Workloads with Restart Activity (Top 10)", fontsize=12, fontweight="bold")

    plt.tight_layout()
    plt.savefig(output_path / "6_stability_analysis.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  ✓ Generated 6_stability_analysis.png")
    return True


# ----------------------------------------------------------------------------
# Graph 7 — Namespace risk heatmap (stacked priority bars)
# ----------------------------------------------------------------------------


def _namespace_risk(analyses: Sequence, output_path: Path) -> bool:
    """Stacked horizontal bars per namespace: P0/P1/P2/P3 counts.

    Drives team-handoff conversations: who owns the most fires?
    """
    from collections import defaultdict

    import matplotlib.pyplot as plt
    import numpy as np

    counts = defaultdict(lambda: [0, 0, 0, 0])  # [P0, P1, P2, P3]
    for a in analyses:
        idx = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(a.priority.value, 3)
        counts[a.namespace][idx] += 1
    if not counts:
        return False

    # Top N by P0 desc, then total workload desc.
    rows = sorted(
        counts.items(),
        key=lambda kv: (-kv[1][0], -sum(kv[1])),
    )[:15]

    namespaces = [ns for ns, _ in rows]
    p0 = np.array([v[0] for _, v in rows])
    p1 = np.array([v[1] for _, v in rows])
    p2 = np.array([v[2] for _, v in rows])
    p3 = np.array([v[3] for _, v in rows])

    _fig, ax = plt.subplots(figsize=(12, max(5, 0.45 * len(namespaces) + 1)))
    y = np.arange(len(namespaces))
    ax.barh(y, p0, color="#ff4444", label="P0 Blocker")
    ax.barh(y, p1, left=p0, color="#ff8844", label="P1 High")
    ax.barh(y, p2, left=p0 + p1, color="#ffdd44", label="P2 Medium")
    ax.barh(y, p3, left=p0 + p1 + p2, color="#44ff44", label="P3 Low")
    ax.set_yticks(y)
    ax.set_yticklabels(namespaces, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("Workloads", fontsize=11)
    ax.set_title("Namespace Risk Heatmap (Top 15 by P0 count)", fontsize=13, fontweight="bold")
    ax.legend(loc="lower right")
    ax.grid(axis="x", alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path / "7_namespace_risk.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  ✓ Generated 7_namespace_risk.png")
    return True


# ----------------------------------------------------------------------------
# Graph 8 — Fleet capacity (requested vs used) per top-N namespace
# ----------------------------------------------------------------------------


def _fleet_capacity(analyses: Sequence, output_path: Path) -> bool:
    """Stacked bars per top-N namespace: total CPU/Mem requested vs used.

    Direct answer to 'where is the cluster paying for empty?'
    """
    from collections import defaultdict

    import matplotlib.pyplot as plt
    import numpy as np

    by_ns = defaultdict(lambda: {"cpu_req": 0.0, "cpu_avg": 0.0, "mem_req": 0.0, "mem_avg": 0.0})
    for a in analyses:
        ns = by_ns[a.namespace]
        ns["cpu_req"] += max(0.0, a.cpu_request)
        ns["cpu_avg"] += max(0.0, a.avg_cpu)
        ns["mem_req"] += max(0.0, a.mem_request)
        ns["mem_avg"] += max(0.0, a.avg_mem)
    if not by_ns:
        return False

    cpu_top = sorted(by_ns.items(), key=lambda kv: -kv[1]["cpu_req"])[:10]
    mem_top = sorted(by_ns.items(), key=lambda kv: -kv[1]["mem_req"])[:10]

    _, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))

    def _plot(ax, top, req_key, avg_key, unit, color_used, color_idle, title):
        """Draw one capacity panel (CPU or Mem) with used + idle stacked bars."""
        names = [ns for ns, _ in top]
        used = np.array([v[avg_key] for _, v in top])
        req = np.array([v[req_key] for _, v in top])
        idle = np.maximum(req - used, 0)
        y = np.arange(len(names))
        ax.barh(y, used, color=color_used, label="Used (avg)")
        ax.barh(y, idle, left=used, color=color_idle, alpha=0.6, label="Requested but idle")
        ax.set_yticks(y)
        ax.set_yticklabels(names, fontsize=9)
        ax.invert_yaxis()
        ax.set_xlabel(unit, fontsize=11)
        ax.set_title(title, fontsize=12, fontweight="bold")
        ax.legend(loc="lower right")
        ax.grid(axis="x", alpha=0.3)
        # Annotate utilization % on each row
        for i, (u, r) in enumerate(zip(used, req, strict=False)):
            if r > 0:
                pct = (u / r) * 100
                ax.text(r, i, f" {pct:.0f}%", va="center", fontsize=8, alpha=0.7)

    _plot(
        ax1, cpu_top, "cpu_req", "cpu_avg", "CPU (millicores)", "#1f77b4", "#aec7e8", "Top 10 Namespaces — CPU Capacity"
    )
    _plot(
        ax2, mem_top, "mem_req", "mem_avg", "Memory (Mi)", "#2ca02c", "#98df8a", "Top 10 Namespaces — Memory Capacity"
    )

    plt.tight_layout()
    plt.savefig(output_path / "8_fleet_capacity.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  ✓ Generated 8_fleet_capacity.png")
    return True


# ----------------------------------------------------------------------------
# Graph 9 — Pattern-group impact
# ----------------------------------------------------------------------------


def _pattern_group_impact(analyses: Sequence, output_path: Path) -> bool:
    """Horizontal bar chart of pattern groups by member count, colored by
    priority. Surfaces 'fix the chart once, resolve N issues'."""
    import matplotlib.pyplot as plt
    import numpy as np

    from k8s_advisor.aggregations import build_pattern_groups

    groups = build_pattern_groups(analyses, min_size=3)
    if not groups:
        return False

    # Top 12 by member count
    groups = sorted(groups, key=lambda g: -len(g.workloads))[:12]
    labels = [f"{g.namespace}/{g.prefix}-*" for g in groups]
    sizes = [len(g.workloads) for g in groups]
    color_map = {"P0": "#ff4444", "P1": "#ff8844", "P2": "#ffdd44", "P3": "#44ff44"}
    colors = [color_map.get(g.priority, "#888888") for g in groups]

    _fig, ax = plt.subplots(figsize=(12, max(5, 0.5 * len(labels) + 1)))
    y = np.arange(len(labels))
    ax.barh(y, sizes, color=colors, edgecolor="black", linewidth=0.3)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("Number of workloads in group", fontsize=11)
    ax.set_title("Pattern Groups — Fix the Chart, Resolve N Tickets", fontsize=13, fontweight="bold")
    ax.grid(axis="x", alpha=0.3)

    # Annotate priority and count.
    for i, (g, n) in enumerate(zip(groups, sizes, strict=False)):
        ax.text(n, i, f" {g.priority} × {n}", va="center", fontsize=9, alpha=0.85)

    from matplotlib.patches import Patch

    ax.legend(
        handles=[
            Patch(facecolor="#ff4444", label="P0"),
            Patch(facecolor="#ff8844", label="P1"),
            Patch(facecolor="#ffdd44", label="P2"),
            Patch(facecolor="#44ff44", label="P3"),
        ],
        loc="lower right",
    )

    plt.tight_layout()
    plt.savefig(output_path / "9_pattern_group_impact.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  ✓ Generated 9_pattern_group_impact.png")
    return True


# ----------------------------------------------------------------------------
# Graph 10 — P95 vs request scatter (Prometheus-only)
# ----------------------------------------------------------------------------


def _p95_vs_request(analyses: Sequence, output_path: Path) -> bool:
    """Scatter of P95/request ratio for CPU vs Memory.

    Only meaningful when Prometheus data is available — falls back to
    skipping the chart if the percentage of workloads with P95 is too low.
    """
    import matplotlib.pyplot as plt

    # We can plot a workload as long as it has a P95 + request on at least
    # one axis. Missing axis is plotted at the floor (0).
    candidates = [a for a in analyses if (a.cpu_p95 > 0 and a.cpu_request > 0) or (a.mem_p95 > 0 and a.mem_request > 0)]
    # Need a meaningful sample
    if len(candidates) < 5 or len(candidates) < 0.10 * len(analyses):
        return False

    cpu_ratio = []
    mem_ratio = []
    annotated = []
    for a in candidates:
        cr = (a.cpu_p95 / a.cpu_request) if (a.cpu_p95 > 0 and a.cpu_request > 0) else 0.0
        mr = (a.mem_p95 / a.mem_request) if (a.mem_p95 > 0 and a.mem_request > 0) else 0.0
        cpu_ratio.append(min(cr, 3.0))
        mem_ratio.append(min(mr, 3.0))
        # Annotate clear danger / waste outliers
        if cr > 1.5 or mr > 1.5 or (cr < 0.2 and mr < 0.2):
            annotated.append((cr, mr, _short_label(a, 30), a))

    _fig, ax = plt.subplots(figsize=(12, 8))
    colors = []
    for cr, mr in zip(cpu_ratio, mem_ratio, strict=False):
        if cr > 1 or mr > 1:
            colors.append("red")  # under-provisioned (real risk)
        elif cr < 0.3 and mr < 0.3:
            colors.append("coral")  # waste
        elif 0.5 <= cr <= 1 and 0.5 <= mr <= 1:
            colors.append("green")  # well-sized
        else:
            colors.append("gold")  # acceptable

    ax.scatter(cpu_ratio, mem_ratio, c=colors, alpha=0.6, s=80, edgecolors="black", linewidths=0.3)

    # Reference lines.
    ax.axvline(x=1.0, color="red", linestyle="--", alpha=0.4, label="P95 = request")
    ax.axhline(y=1.0, color="red", linestyle="--", alpha=0.4)
    ax.axvline(x=0.3, color="gray", linestyle=":", alpha=0.4)
    ax.axhline(y=0.3, color="gray", linestyle=":", alpha=0.4)

    for cr, mr, label, _ in annotated[:20]:
        ax.annotate(
            label, (min(cr, 3.0), min(mr, 3.0)), xytext=(5, 5), textcoords="offset points", fontsize=7, alpha=0.85
        )

    ax.set_xlabel("CPU P95 / request", fontsize=12)
    ax.set_ylabel("Memory P95 / request", fontsize=12)
    ax.set_title(
        "P95 vs Request — Real Risk vs Real Waste\n(>1.0 = throttle/OOM risk; <0.3 = clear waste)",
        fontsize=12,
        fontweight="bold",
    )
    ax.set_xlim(left=-0.05, right=3.1)
    ax.set_ylim(bottom=-0.05, top=3.1)
    ax.grid(True, alpha=0.3)

    from matplotlib.patches import Patch

    ax.legend(
        handles=[
            Patch(facecolor="red", label="Under-provisioned (risk)"),
            Patch(facecolor="gold", label="Acceptable"),
            Patch(facecolor="green", label="Well-sized (50–100%)"),
            Patch(facecolor="coral", label="Waste (<30% on both)"),
        ],
        loc="upper right",
    )

    plt.tight_layout()
    plt.savefig(output_path / "10_p95_vs_request.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  ✓ Generated 10_p95_vs_request.png")
    return True


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python visualizer.py <csv_file>")
        sys.exit(1)
    success = generate_graphs(sys.argv[1])
    sys.exit(0 if success else 1)
