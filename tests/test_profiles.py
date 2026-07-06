"""Tests for the per-namespace policy profile loader and resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from k8s_advisor.constants import (
    CPU_MIN_RECOMMENDED_M,
    CPU_OVER_REQUESTED_THRESHOLD,
    CPU_REDUCTION_MIN_SAVING_M,
    CPU_UNDER_REQUESTED_THRESHOLD,
    HEADROOM_MULTIPLIER,
    MEM_MIN_RECOMMENDED_MI,
    MEM_OVER_REQUESTED_THRESHOLD,
    MEM_UNDER_REQUESTED_THRESHOLD,
)
from k8s_advisor.profiles import (
    DEFAULT_PROFILE,
    DEFAULT_PROFILE_SET,
    Profile,
    ProfileSet,
    load_profiles,
)

# ──────────────────────────────────────────────────────────────────────
# Default profile mirrors constants.py
# ──────────────────────────────────────────────────────────────────────


def test_default_profile_mirrors_constants():
    """DEFAULT_PROFILE must match constants.py so unconfigured behavior is unchanged."""
    assert DEFAULT_PROFILE.cpu_headroom == HEADROOM_MULTIPLIER
    assert DEFAULT_PROFILE.mem_headroom == HEADROOM_MULTIPLIER
    assert DEFAULT_PROFILE.min_cpu_request_m == CPU_MIN_RECOMMENDED_M
    assert DEFAULT_PROFILE.min_mem_request_mi == MEM_MIN_RECOMMENDED_MI
    assert DEFAULT_PROFILE.min_cpu_saving_m == CPU_REDUCTION_MIN_SAVING_M
    assert DEFAULT_PROFILE.cpu_over_pct == CPU_OVER_REQUESTED_THRESHOLD
    assert DEFAULT_PROFILE.cpu_under_pct == CPU_UNDER_REQUESTED_THRESHOLD
    assert DEFAULT_PROFILE.mem_over_pct == MEM_OVER_REQUESTED_THRESHOLD
    assert DEFAULT_PROFILE.mem_under_pct == MEM_UNDER_REQUESTED_THRESHOLD
    assert DEFAULT_PROFILE.name == "default"


def test_default_profile_set_resolves_default_for_any_namespace():
    """An empty set with no overrides returns DEFAULT_PROFILE for every namespace."""
    assert DEFAULT_PROFILE_SET.for_namespace("any-ns") is DEFAULT_PROFILE
    assert DEFAULT_PROFILE_SET.for_namespace("") is DEFAULT_PROFILE


# ──────────────────────────────────────────────────────────────────────
# load_profiles — happy path
# ──────────────────────────────────────────────────────────────────────


def test_load_profiles_full_file(tmp_path: Path):
    """A complete policy file produces a profile per namespace with all knobs set."""
    policy = tmp_path / "policies.yaml"
    policy.write_text(
        """
default:
  cpu_headroom: 1.30
  mem_headroom: 1.40
  min_cpu_request: 75m
  min_mem_request: 32Mi
  min_cpu_saving: 100m
  cpu_over_pct: 40
  cpu_under_pct: 80
  mem_over_pct: 45
  mem_under_pct: 88
namespaces:
  prod-api:
    cpu_headroom: 1.50
    min_cpu_request: 100m
  batch-jobs:
    cpu_headroom: 1.10
""",
        encoding="utf-8",
    )
    ps = load_profiles(policy)

    assert ps.default.cpu_headroom == 1.30
    assert ps.default.mem_headroom == 1.40
    assert ps.default.min_cpu_request_m == 75
    assert ps.default.min_mem_request_mi == 32
    assert ps.default.min_cpu_saving_m == 100
    assert ps.default.cpu_over_pct == 40

    prod = ps.for_namespace("prod-api")
    assert prod.name == "prod-api"
    assert prod.cpu_headroom == 1.50
    assert prod.min_cpu_request_m == 100
    # Inherited from the resolved default, not from constants.py.
    assert prod.mem_headroom == 1.40
    assert prod.cpu_under_pct == 80

    batch = ps.for_namespace("batch-jobs")
    assert batch.cpu_headroom == 1.10
    assert batch.mem_headroom == 1.40  # inherited

    # Unknown namespace falls back to default.
    other = ps.for_namespace("kube-system")
    assert other.name == "default"
    assert other.cpu_headroom == 1.30


def test_load_profiles_only_default(tmp_path: Path):
    """A file with only `default:` is valid; namespaces fall back to it."""
    policy = tmp_path / "p.yaml"
    policy.write_text("default:\n  cpu_headroom: 2.0\n", encoding="utf-8")
    ps = load_profiles(policy)
    assert ps.default.cpu_headroom == 2.0
    assert ps.for_namespace("anything").cpu_headroom == 2.0


def test_load_profiles_only_namespaces(tmp_path: Path):
    """A file without `default:` uses constants.py defaults plus overrides."""
    policy = tmp_path / "p.yaml"
    policy.write_text(
        "namespaces:\n  hot-ns:\n    cpu_headroom: 1.7\n",
        encoding="utf-8",
    )
    ps = load_profiles(policy)
    assert ps.default.cpu_headroom == HEADROOM_MULTIPLIER  # constants.py
    assert ps.for_namespace("hot-ns").cpu_headroom == 1.7
    assert ps.for_namespace("cold-ns").cpu_headroom == HEADROOM_MULTIPLIER


def test_load_profiles_empty_file_returns_defaults(tmp_path: Path):
    """An empty YAML file is valid and equivalent to no overrides at all."""
    policy = tmp_path / "p.yaml"
    policy.write_text("", encoding="utf-8")
    ps = load_profiles(policy)
    assert ps.default == DEFAULT_PROFILE
    assert ps.namespaces == {}


# ──────────────────────────────────────────────────────────────────────
# load_profiles — strict validation
# ──────────────────────────────────────────────────────────────────────


def test_load_profiles_rejects_unknown_top_level(tmp_path: Path):
    """Top-level keys outside `default`/`namespaces` raise so typos surface."""
    policy = tmp_path / "p.yaml"
    policy.write_text("namspaces:\n  prod: {}\n", encoding="utf-8")  # typo
    with pytest.raises(ValueError, match="Unknown top-level key"):
        load_profiles(policy)


def test_load_profiles_rejects_unknown_knob_in_default(tmp_path: Path):
    """A typo'd knob name in `default:` raises."""
    policy = tmp_path / "p.yaml"
    policy.write_text("default:\n  cpu_headoom: 1.5\n", encoding="utf-8")  # typo
    with pytest.raises(ValueError, match="Unknown key"):
        load_profiles(policy)


def test_load_profiles_rejects_unknown_knob_in_namespace(tmp_path: Path):
    """A typo'd knob name inside a namespace block raises."""
    policy = tmp_path / "p.yaml"
    policy.write_text(
        "namespaces:\n  prod:\n    cpu_headoom: 1.5\n",  # typo
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="prod"):
        load_profiles(policy)


def test_load_profiles_rejects_negative_headroom(tmp_path: Path):
    """Non-positive multipliers raise — they'd produce nonsensical recs."""
    policy = tmp_path / "p.yaml"
    policy.write_text("default:\n  cpu_headroom: -1.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cpu_headroom"):
        load_profiles(policy)


def test_load_profiles_rejects_pct_out_of_range(tmp_path: Path):
    """Efficiency thresholds outside (0, 200) raise."""
    policy = tmp_path / "p.yaml"
    policy.write_text("default:\n  cpu_under_pct: 250\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cpu_under_pct"):
        load_profiles(policy)


def test_load_profiles_rejects_invalid_cpu_string(tmp_path: Path):
    """A malformed CPU value names the offending field in the error."""
    policy = tmp_path / "p.yaml"
    policy.write_text("default:\n  min_cpu_request: 'lots'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="min_cpu_request"):
        load_profiles(policy)


def test_load_profiles_missing_file():
    """A nonexistent path raises with the path in the message."""
    with pytest.raises(ValueError, match="not found"):
        load_profiles("/tmp/does-not-exist-12345.yaml")


def test_load_profiles_rejects_non_mapping_default(tmp_path: Path):
    """`default: <scalar>` raises rather than silently degrading to defaults."""
    policy = tmp_path / "p.yaml"
    policy.write_text("default: false\n", encoding="utf-8")
    with pytest.raises(ValueError, match="`default:` must be a mapping"):
        load_profiles(policy)


def test_load_profiles_rejects_non_mapping_namespaces(tmp_path: Path):
    """`namespaces: <scalar>` raises rather than silently no-opping."""
    policy = tmp_path / "p.yaml"
    policy.write_text("namespaces: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="`namespaces:` must be a mapping"):
        load_profiles(policy)


def test_load_profiles_rejects_non_mapping_namespace_block(tmp_path: Path):
    """A namespace whose value is a scalar raises with the namespace name."""
    policy = tmp_path / "p.yaml"
    policy.write_text("namespaces:\n  prod: 'tight'\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"prod.*must be a mapping"):
        load_profiles(policy)


def test_load_profiles_allows_empty_namespace_block(tmp_path: Path):
    """A namespace with no overrides (`prod:` followed by nothing) is valid."""
    policy = tmp_path / "p.yaml"
    policy.write_text("namespaces:\n  prod:\n", encoding="utf-8")
    ps = load_profiles(policy)
    # Empty block resolves to a profile with the namespace's name but
    # otherwise inheriting every default.
    prof = ps.for_namespace("prod")
    assert prof.name == "prod"
    assert prof.cpu_headroom == HEADROOM_MULTIPLIER


# ──────────────────────────────────────────────────────────────────────
# CPU/memory parsing
# ──────────────────────────────────────────────────────────────────────


def test_load_profiles_cpu_units(tmp_path: Path):
    """CPU values follow K8s convention: 'm' suffix is millicores, bare = cores."""
    policy = tmp_path / "p.yaml"
    policy.write_text(
        """
default:
  min_cpu_request: 500m
namespaces:
  fractional-cores:
    min_cpu_request: 1.5
  whole-core:
    min_cpu_request: 2
""",
        encoding="utf-8",
    )
    ps = load_profiles(policy)
    assert ps.default.min_cpu_request_m == 500
    assert ps.for_namespace("fractional-cores").min_cpu_request_m == 1500  # 1.5 cores
    assert ps.for_namespace("whole-core").min_cpu_request_m == 2000


def test_load_profiles_memory_units(tmp_path: Path):
    """Memory values accept 'Mi', 'Gi', or a bare number (MiB)."""
    policy = tmp_path / "p.yaml"
    policy.write_text(
        """
default:
  min_mem_request: 256Mi
namespaces:
  gigs:
    min_mem_request: 1Gi
  bare:
    min_mem_request: 128
""",
        encoding="utf-8",
    )
    ps = load_profiles(policy)
    assert ps.default.min_mem_request_mi == 256
    assert ps.for_namespace("gigs").min_mem_request_mi == 1024
    assert ps.for_namespace("bare").min_mem_request_mi == 128


# ──────────────────────────────────────────────────────────────────────
# ProfileSet.for_namespace resolution
# ──────────────────────────────────────────────────────────────────────


def test_profile_set_for_namespace_unknown_falls_back_to_default():
    """Namespaces not listed in the policy file get the default profile."""
    custom_default = Profile(name="default", cpu_headroom=1.99)
    ps = ProfileSet(default=custom_default, namespaces={"hot": Profile(name="hot", cpu_headroom=2.5)})
    assert ps.for_namespace("hot").cpu_headroom == 2.5
    assert ps.for_namespace("cold").cpu_headroom == 1.99
    assert ps.for_namespace("").cpu_headroom == 1.99


# ──────────────────────────────────────────────────────────────────────
# cpu_limit_policy knob + global base seeding
# ──────────────────────────────────────────────────────────────────────


def test_default_profile_cpu_limit_policy_is_neutral():
    """Unconfigured stance is neutral (present both, recommend no direction)."""
    assert DEFAULT_PROFILE.cpu_limit_policy == "neutral"


def test_load_profiles_accepts_cpu_limit_policy(tmp_path: Path):
    policy = tmp_path / "policies.yaml"
    policy.write_text(
        """
default:
  cpu_limit_policy: protect
namespaces:
  team-burst:
    cpu_limit_policy: burst
""",
        encoding="utf-8",
    )
    ps = load_profiles(policy)
    assert ps.default.cpu_limit_policy == "protect"
    assert ps.for_namespace("team-burst").cpu_limit_policy == "burst"
    # Unlisted namespace inherits the default block's stance.
    assert ps.for_namespace("other").cpu_limit_policy == "protect"


def test_load_profiles_rejects_bad_cpu_limit_policy(tmp_path: Path):
    policy = tmp_path / "policies.yaml"
    policy.write_text(
        """
default:
  cpu_limit_policy: yolo
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cpu_limit_policy"):
        load_profiles(policy)


def test_load_profiles_base_seeds_fallback(tmp_path: Path):
    """A non-default base seeds knobs the profile omits; the file still wins
    where it sets a value."""
    base = Profile(name="default", cpu_limit_policy="burst")
    policy = tmp_path / "policies.yaml"
    policy.write_text(
        """
namespaces:
  guarded:
    cpu_limit_policy: protect
""",
        encoding="utf-8",
    )
    ps = load_profiles(policy, base=base)
    # Default (no cpu_limit_policy in file) inherits the base's burst.
    assert ps.default.cpu_limit_policy == "burst"
    # Namespace override wins over the base.
    assert ps.for_namespace("guarded").cpu_limit_policy == "protect"
    # Unlisted namespace inherits base via default.
    assert ps.for_namespace("other").cpu_limit_policy == "burst"
