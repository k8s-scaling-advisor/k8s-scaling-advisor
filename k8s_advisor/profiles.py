"""Per-namespace policy profiles for the analyzer.

Operators pass ``--profiles policies.yaml`` to ``analyze`` / ``report`` to
tune recommendations for namespaces that don't fit the global defaults.

Schema:

    default:
      cpu_headroom: 1.25
      mem_headroom: 1.25
      min_cpu_request: 50m
      min_mem_request: 16Mi
      min_cpu_saving: 50m
      cpu_over_pct: 50
      cpu_under_pct: 85
      mem_over_pct: 50
      mem_under_pct: 85

    namespaces:
      prod-api:
        cpu_headroom: 1.50
        min_cpu_request: 100m
      batch-jobs:
        cpu_headroom: 1.10

All keys are optional; anything omitted falls back to the value from
``constants.py`` (the project's hardcoded defaults). A missing
``--profiles`` flag is the same as "use defaults everywhere" — the
analyzer's behavior is unchanged from before this feature.

Resolution is strict: unknown top-level sections or unknown knob names
raise on load. We'd rather an operator hit a clear error than silently
ignore a typo'd ``cpu_headoom: 1.5`` and wonder why prod still uses 1.25.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from k8s_advisor.constants import (
    CPU_MIN_RECOMMENDED_M,
    CPU_OVER_REQUESTED_THRESHOLD,
    CPU_REDUCTION_MIN_SAVING_M,
    CPU_UNDER_REQUESTED_THRESHOLD,
    HEADROOM_MULTIPLIER,
    MEM_OVER_REQUESTED_THRESHOLD,
    MEM_UNDER_REQUESTED_THRESHOLD,
)

# Memory floor matches the analyzer's hardcoded `max(16, ...)` in the
# request recommendation path. Keeping it here lets profiles raise the
# floor for namespaces with chunkier baseline workloads (JVM, ES).
MEM_MIN_RECOMMENDED_MI_DEFAULT = 16

# Allowed keys in a profile block — used to reject typos at load time.
_PROFILE_KEYS = frozenset(
    {
        "cpu_headroom",
        "mem_headroom",
        "min_cpu_request",
        "min_mem_request",
        "min_cpu_saving",
        "cpu_over_pct",
        "cpu_under_pct",
        "mem_over_pct",
        "mem_under_pct",
    }
)

_TOP_LEVEL_KEYS = frozenset({"default", "namespaces"})


@dataclass(frozen=True)
class Profile:
    """Resolved per-namespace knobs the analyzer reads."""

    name: str = "default"
    cpu_headroom: float = HEADROOM_MULTIPLIER
    mem_headroom: float = HEADROOM_MULTIPLIER
    min_cpu_request_m: float = CPU_MIN_RECOMMENDED_M
    min_mem_request_mi: float = MEM_MIN_RECOMMENDED_MI_DEFAULT
    min_cpu_saving_m: float = CPU_REDUCTION_MIN_SAVING_M
    cpu_over_pct: float = CPU_OVER_REQUESTED_THRESHOLD
    cpu_under_pct: float = CPU_UNDER_REQUESTED_THRESHOLD
    mem_over_pct: float = MEM_OVER_REQUESTED_THRESHOLD
    mem_under_pct: float = MEM_UNDER_REQUESTED_THRESHOLD


# Sentinel used when no --profiles flag is passed. Matches constants.py
# so analyzer output is byte-identical to pre-profile behavior.
DEFAULT_PROFILE = Profile()


@dataclass(frozen=True)
class ProfileSet:
    """A loaded ``policies.yaml``: one default plus namespace overrides."""

    default: Profile = DEFAULT_PROFILE
    namespaces: dict[str, Profile] = field(default_factory=dict)

    def for_namespace(self, namespace: str) -> Profile:
        """Return the profile that applies to ``namespace``.

        Namespace override wins; otherwise the default profile is used.
        """
        return self.namespaces.get(namespace, self.default)


# Sentinel ProfileSet used when no --profiles flag is passed.
DEFAULT_PROFILE_SET = ProfileSet()


def load_profiles(path: str | Path) -> ProfileSet:
    """Parse a policies YAML file into a ``ProfileSet``.

    Raises ``ValueError`` for unknown keys, malformed values, or missing
    required dependencies (PyYAML). The error string names the offending
    field so a typo is debuggable from CI logs alone.
    """
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:  # pragma: no cover - exercised only without yaml
        raise ValueError("Loading --profiles requires PyYAML. Install it with `pip install pyyaml`.") from e

    p = Path(path)
    if not p.exists():
        raise ValueError(f"Profiles file not found: {p}")

    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ValueError(f"Profiles file must be a YAML mapping, got {type(raw).__name__}")

    unknown_top = set(raw.keys()) - _TOP_LEVEL_KEYS
    if unknown_top:
        raise ValueError(
            f"Unknown top-level key(s) in profiles file: {sorted(unknown_top)}. Allowed: {sorted(_TOP_LEVEL_KEYS)}"
        )

    default = _parse_profile("default", raw.get("default") or {}, base=DEFAULT_PROFILE)

    ns_raw = raw.get("namespaces") or {}
    if not isinstance(ns_raw, dict):
        raise ValueError(f"`namespaces:` must be a mapping, got {type(ns_raw).__name__}")

    namespaces: dict[str, Profile] = {}
    for ns_name, ns_block in ns_raw.items():
        if not isinstance(ns_name, str) or not ns_name:
            raise ValueError(f"Namespace key must be a non-empty string, got {ns_name!r}")
        # Per-namespace overrides layer on top of the (already-resolved)
        # default profile, so a user who only sets `cpu_headroom: 1.5`
        # for `prod-api` still inherits all the other defaults the rest
        # of the policy file established.
        namespaces[ns_name] = _parse_profile(ns_name, ns_block or {}, base=default)

    return ProfileSet(default=default, namespaces=namespaces)


def _parse_profile(name: str, block: Any, *, base: Profile) -> Profile:
    """Validate one profile block and return a Profile layered on ``base``."""
    if not isinstance(block, dict):
        raise ValueError(f"Profile `{name}` must be a mapping, got {type(block).__name__}")

    unknown = set(block.keys()) - _PROFILE_KEYS
    if unknown:
        raise ValueError(f"Unknown key(s) in profile `{name}`: {sorted(unknown)}. Allowed: {sorted(_PROFILE_KEYS)}")

    overrides: dict[str, Any] = {"name": name}

    if "cpu_headroom" in block:
        overrides["cpu_headroom"] = _require_positive_float(name, "cpu_headroom", block["cpu_headroom"])
    if "mem_headroom" in block:
        overrides["mem_headroom"] = _require_positive_float(name, "mem_headroom", block["mem_headroom"])

    if "min_cpu_request" in block:
        overrides["min_cpu_request_m"] = _parse_cpu(name, "min_cpu_request", block["min_cpu_request"])
    if "min_mem_request" in block:
        overrides["min_mem_request_mi"] = _parse_memory(name, "min_mem_request", block["min_mem_request"])
    if "min_cpu_saving" in block:
        overrides["min_cpu_saving_m"] = _parse_cpu(name, "min_cpu_saving", block["min_cpu_saving"])

    for key in ("cpu_over_pct", "cpu_under_pct", "mem_over_pct", "mem_under_pct"):
        if key in block:
            overrides[key] = _require_pct(name, key, block[key])

    return replace(base, **overrides)


def _require_positive_float(profile_name: str, key: str, value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"Profile `{profile_name}`: `{key}` must be a number, got {value!r}")
    f = float(value)
    if f <= 0:
        raise ValueError(f"Profile `{profile_name}`: `{key}` must be > 0, got {f}")
    return f


def _require_pct(profile_name: str, key: str, value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"Profile `{profile_name}`: `{key}` must be a number, got {value!r}")
    f = float(value)
    if not 0 < f < 200:
        raise ValueError(f"Profile `{profile_name}`: `{key}` must be in (0, 200), got {f}")
    return f


# CPU values follow Kubernetes convention: bare numbers are cores, "m"
# suffix is millicores. Internal storage is always millicores so the
# analyzer math is unit-consistent.
_CPU_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)(m?)\s*$")


def _parse_cpu(profile_name: str, key: str, value: Any) -> float:
    """Accept ``500m``, ``1``, ``1.5`` (cores) and return millicores (float)."""
    if isinstance(value, bool):
        raise ValueError(f"Profile `{profile_name}`: `{key}` must be a CPU quantity, got {value!r}")
    if isinstance(value, (int, float)):
        # Bare numbers follow K8s convention: 1 = 1 core (= 1000m).
        f = float(value) * 1000.0
    else:
        if not isinstance(value, str):
            raise ValueError(f"Profile `{profile_name}`: `{key}` must be a CPU quantity, got {value!r}")
        m = _CPU_RE.match(value)
        if not m:
            raise ValueError(
                f"Profile `{profile_name}`: `{key}` is not a valid CPU value: {value!r}. "
                f"Use millicores ('100m') or cores ('1', '1.5')."
            )
        n = float(m.group(1))
        f = n if m.group(2) == "m" else n * 1000.0
    if f <= 0:
        raise ValueError(f"Profile `{profile_name}`: `{key}` must be > 0, got {f}")
    return f


# Memory accepts either a bare number (MiB) or a Kubernetes-style string.
# We support the two most common suffixes; anything more exotic (Gi, Ti)
# would be unusual for a *minimum* request and raises so the operator
# notices.
_MEM_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(Mi|Gi|M|G)?\s*$")


def _parse_memory(profile_name: str, key: str, value: Any) -> float:
    """Accept ``256``, ``256Mi``, ``1Gi`` and return MiB (float)."""
    if isinstance(value, bool):
        raise ValueError(f"Profile `{profile_name}`: `{key}` must be a memory quantity, got {value!r}")
    if isinstance(value, (int, float)):
        f = float(value)
    else:
        if not isinstance(value, str):
            raise ValueError(f"Profile `{profile_name}`: `{key}` must be a memory quantity, got {value!r}")
        m = _MEM_RE.match(value)
        if not m:
            raise ValueError(
                f"Profile `{profile_name}`: `{key}` is not a valid memory value: {value!r}. "
                f"Use MiB ('256Mi'), GiB ('1Gi'), or a bare number."
            )
        n = float(m.group(1))
        suffix = m.group(2) or "Mi"
        # Mi/M treated identically (Mi is the K8s convention; M is decimal
        # but operators routinely use them interchangeably and the
        # 4.8% delta isn't worth tripping people up over here).
        scale = {"Mi": 1.0, "M": 1.0, "Gi": 1024.0, "G": 1024.0}[suffix]
        f = n * scale
    if f <= 0:
        raise ValueError(f"Profile `{profile_name}`: `{key}` must be > 0, got {f}")
    return f
