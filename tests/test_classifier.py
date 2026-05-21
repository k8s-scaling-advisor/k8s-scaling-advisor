"""Unit tests for priority and scaling approach classification."""

from k8s_advisor.analyzer.classifier import (
    _check_vpa_support,
    determine_priority,
    determine_scaling_approach,
)
from k8s_advisor.analyzer.models import (
    DeploymentAnalysis,
    IssueType,
    Priority,
    ScalingApproach,
)


class TestDeterminePriority:
    """Test priority classification logic."""

    def create_baseline_analysis(self):
        """Create baseline analysis for testing."""
        return DeploymentAnalysis(
            cluster="test-cluster",
            namespace="default",
            workload_type="Deployment",
            deployment="test-app",
            replicas=3,
            avg_cpu_usage_m=100.0,
            cpu_request_m=200.0,
            cpu_limit_m=400.0,
            cpu_usage_percent=50.0,
            avg_mem_usage_mi=256.0,
            mem_request_mi=512.0,
            mem_limit_mi=1024.0,
            mem_usage_percent=50.0,
            total_restarts=0,
            max_pod_restarts=0,
            pods_restarting=0,
            restart_reason="",
            rwo_pvc=False,
            rwo_pvc_names="",
            priority=Priority.P3,
        )

    def test_p0_requests_not_set(self):
        """Test P0 priority for REQUESTS_NOT_SET issue."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.REQUESTS_NOT_SET]

        priority = determine_priority(analysis)

        assert priority == Priority.P0

    def test_p0_cpu_throttled(self):
        """Test P0 priority for CPU_THROTTLED issue."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.CPU_THROTTLED]

        priority = determine_priority(analysis)

        assert priority == Priority.P0

    def test_p0_oom_killed(self):
        """Test P0 priority for OOM_KILLED issue."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.OOM_KILLED]

        priority = determine_priority(analysis)

        assert priority == Priority.P0

    def test_p0_multiple_issues(self):
        """Test P0 priority with multiple P0 issues."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [
            IssueType.REQUESTS_NOT_SET,
            IssueType.CPU_THROTTLED,
            IssueType.CPU_OVER_REQUESTED,  # P2 issue
        ]

        priority = determine_priority(analysis)

        assert priority == Priority.P0

    def test_p1_unstable(self):
        """Test P1 priority for UNSTABLE issue."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.UNSTABLE]

        priority = determine_priority(analysis)

        assert priority == Priority.P1

    def test_p1_mem_saturation(self):
        """Test P1 priority for MEM_SATURATION issue."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.MEM_SATURATION]

        priority = determine_priority(analysis)

        assert priority == Priority.P1

    def test_p1_multiple_issues(self):
        """Test P1 priority with multiple P1 issues."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [
            IssueType.UNSTABLE,
            IssueType.MEM_SATURATION,
            IssueType.MEM_OVER_REQUESTED,  # P2 issue
        ]

        priority = determine_priority(analysis)

        assert priority == Priority.P1

    def test_p2_cpu_under_requested(self):
        """Test P2 priority for CPU_UNDER_REQUESTED issue."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.CPU_UNDER_REQUESTED]

        priority = determine_priority(analysis)

        assert priority == Priority.P2

    def test_p2_mem_under_requested(self):
        """Test P2 priority for MEM_UNDER_REQUESTED issue."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.MEM_UNDER_REQUESTED]

        priority = determine_priority(analysis)

        assert priority == Priority.P2

    def test_p2_cpu_over_requested(self):
        """Test P2 priority for CPU_OVER_REQUESTED issue."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.CPU_OVER_REQUESTED]

        priority = determine_priority(analysis)

        assert priority == Priority.P2

    def test_p2_mem_over_requested(self):
        """Test P2 priority for MEM_OVER_REQUESTED issue."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.MEM_OVER_REQUESTED]

        priority = determine_priority(analysis)

        assert priority == Priority.P2

    def test_p2_missing_cpu_limits(self):
        """Test P2 priority for MISSING_CPU_LIMITS issue."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.MISSING_CPU_LIMITS]

        priority = determine_priority(analysis)

        assert priority == Priority.P2

    def test_p2_rwo_pvc(self):
        """Test P2 priority for RWO_PVC issue."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.RWO_PVC]

        priority = determine_priority(analysis)

        assert priority == Priority.P2

    def test_p3_no_issues(self):
        """Test P3 priority when no issues detected."""
        analysis = self.create_baseline_analysis()
        analysis.issues = []

        priority = determine_priority(analysis)

        assert priority == Priority.P3

    def test_p3_only_single_replica(self):
        """Test P3 priority with only SINGLE_REPLICA (informational)."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.SINGLE_REPLICA]

        priority = determine_priority(analysis)

        assert priority == Priority.P3

    def test_priority_hierarchy_p0_wins(self):
        """Test P0 takes precedence over P1 and P2."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [
            IssueType.OOM_KILLED,  # P0
            IssueType.UNSTABLE,  # P1
            IssueType.CPU_OVER_REQUESTED,  # P2
        ]

        priority = determine_priority(analysis)

        assert priority == Priority.P0

    def test_priority_hierarchy_p1_wins_over_p2(self):
        """Test P1 takes precedence over P2."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [
            IssueType.MEM_SATURATION,  # P1
            IssueType.MEM_OVER_REQUESTED,  # P2
        ]

        priority = determine_priority(analysis)

        assert priority == Priority.P1


class TestDetermineScalingApproach:
    """Test scaling approach classification logic."""

    def create_baseline_analysis(self):
        """Create baseline analysis for testing."""
        return DeploymentAnalysis(
            cluster="test-cluster",
            namespace="default",
            workload_type="Deployment",
            deployment="test-app",
            replicas=3,
            avg_cpu_usage_m=100.0,
            cpu_request_m=200.0,
            cpu_limit_m=400.0,
            cpu_usage_percent=50.0,
            avg_mem_usage_mi=256.0,
            mem_request_mi=512.0,
            mem_limit_mi=1024.0,
            mem_usage_percent=50.0,
            total_restarts=0,
            max_pod_restarts=0,
            pods_restarting=0,
            restart_reason="",
            rwo_pvc=False,
            rwo_pvc_names="",
            priority=Priority.P3,
            issues=[],
        )

    def test_excluded_deployment_logstash(self):
        """Test NONE approach for excluded deployment (logstash)."""
        analysis = self.create_baseline_analysis()
        analysis.deployment = "logstash-pipeline"

        approach = determine_scaling_approach(analysis)

        assert approach == ScalingApproach.NONE

    def test_statefulset_vpa_k8s_133(self):
        """Test VPA approach for StatefulSet on K8s 1.33+."""
        analysis = self.create_baseline_analysis()
        analysis.workload_type = "StatefulSet"

        approach = determine_scaling_approach(analysis, k8s_version="1.33")

        assert approach == ScalingApproach.VPA

    def test_statefulset_manual_k8s_132(self):
        """Test MANUAL approach for StatefulSet on K8s 1.32."""
        analysis = self.create_baseline_analysis()
        analysis.workload_type = "StatefulSet"

        approach = determine_scaling_approach(analysis, k8s_version="1.32")

        assert approach == ScalingApproach.MANUAL

    def test_rwo_pvc_vpa_k8s_133(self):
        """Test VPA approach for RWO PVC on K8s 1.33+."""
        analysis = self.create_baseline_analysis()
        analysis.rwo_pvc = True

        approach = determine_scaling_approach(analysis, k8s_version="1.33")

        assert approach == ScalingApproach.VPA

    def test_rwo_pvc_manual_k8s_132(self):
        """Test MANUAL approach for RWO PVC on K8s 1.32."""
        analysis = self.create_baseline_analysis()
        analysis.rwo_pvc = True

        approach = determine_scaling_approach(analysis, k8s_version="1.32")

        assert approach == ScalingApproach.MANUAL

    def test_single_replica_vpa_k8s_133(self):
        """Test VPA approach for single replica on K8s 1.33+."""
        analysis = self.create_baseline_analysis()
        analysis.replicas = 1

        approach = determine_scaling_approach(analysis, k8s_version="1.33")

        assert approach == ScalingApproach.VPA

    def test_single_replica_manual_k8s_132(self):
        """Test MANUAL approach for single replica on K8s 1.32."""
        analysis = self.create_baseline_analysis()
        analysis.replicas = 1

        approach = determine_scaling_approach(analysis, k8s_version="1.32")

        assert approach == ScalingApproach.MANUAL

    def test_p0_issues_hpa_after_fix(self):
        """Test HPA_AFTER_FIX for P0 priority."""
        analysis = self.create_baseline_analysis()
        analysis.priority = Priority.P0
        analysis.issues = [IssueType.REQUESTS_NOT_SET]

        approach = determine_scaling_approach(analysis)

        assert approach == ScalingApproach.HPA_AFTER_FIX

    def test_unstable_hpa_after_fix(self):
        """Test HPA_AFTER_FIX for unstable workload."""
        analysis = self.create_baseline_analysis()
        analysis.priority = Priority.P1
        analysis.issues = [IssueType.UNSTABLE]

        approach = determine_scaling_approach(analysis)

        assert approach == ScalingApproach.HPA_AFTER_FIX

    def test_high_cpu_usage_hpa(self):
        """Test HPA for high CPU usage with multiple replicas."""
        analysis = self.create_baseline_analysis()
        analysis.cpu_usage_percent = 90.0  # >85%
        analysis.replicas = 3

        approach = determine_scaling_approach(analysis)

        assert approach == ScalingApproach.HPA

    def test_high_memory_usage_hpa(self):
        """Test HPA for high memory usage with multiple replicas."""
        analysis = self.create_baseline_analysis()
        analysis.mem_usage_percent = 88.0  # >85%
        analysis.replicas = 3

        approach = determine_scaling_approach(analysis)

        assert approach == ScalingApproach.HPA

    def test_multi_replica_hpa(self):
        """Test HPA for multi-replica (>=3) deployment."""
        analysis = self.create_baseline_analysis()
        analysis.replicas = 5

        approach = determine_scaling_approach(analysis)

        assert approach == ScalingApproach.HPA

    def test_two_replicas_no_hpa_by_count(self):
        """Test 2 replicas alone doesn't trigger HPA (needs >=3)."""
        analysis = self.create_baseline_analysis()
        analysis.replicas = 2
        analysis.cpu_usage_percent = 50.0  # Not high usage

        approach = determine_scaling_approach(analysis)

        # Should fall through to MANUAL (no other HPA triggers)
        assert approach == ScalingApproach.MANUAL

    def test_over_requested_vpa_k8s_133(self):
        """Test VPA for over-requested resources on K8s 1.33+."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.CPU_OVER_REQUESTED]
        analysis.replicas = 2

        approach = determine_scaling_approach(analysis, k8s_version="1.33")

        assert approach == ScalingApproach.VPA

    def test_over_requested_manual_k8s_132(self):
        """Test MANUAL for over-requested resources on K8s 1.32."""
        analysis = self.create_baseline_analysis()
        analysis.issues = [IssueType.MEM_OVER_REQUESTED]
        analysis.replicas = 2

        approach = determine_scaling_approach(analysis, k8s_version="1.32")

        assert approach == ScalingApproach.MANUAL

    def test_default_manual(self):
        """Test default MANUAL approach when no special conditions."""
        analysis = self.create_baseline_analysis()
        analysis.replicas = 2
        analysis.cpu_usage_percent = 60.0
        analysis.mem_usage_percent = 55.0

        approach = determine_scaling_approach(analysis)

        assert approach == ScalingApproach.MANUAL

    def test_statefulset_never_hpa_even_with_high_usage(self):
        """Test StatefulSet never gets HPA even with high usage."""
        analysis = self.create_baseline_analysis()
        analysis.workload_type = "StatefulSet"
        analysis.replicas = 5
        analysis.cpu_usage_percent = 95.0  # Would normally trigger HPA

        approach = determine_scaling_approach(analysis, k8s_version="1.33")

        # StatefulSet check comes first, blocks HPA
        assert approach == ScalingApproach.VPA

    def test_rwo_pvc_never_hpa_even_with_multi_replica(self):
        """Test RWO PVC never gets HPA even with multiple replicas."""
        analysis = self.create_baseline_analysis()
        analysis.rwo_pvc = True
        analysis.replicas = 5  # Would normally trigger HPA

        approach = determine_scaling_approach(analysis, k8s_version="1.33")

        # RWO PVC check comes first, blocks HPA
        assert approach == ScalingApproach.VPA

    def test_single_replica_with_p0_still_vpa(self):
        """Test single replica with P0 issues gets VPA, not HPA_AFTER_FIX."""
        analysis = self.create_baseline_analysis()
        analysis.replicas = 1
        analysis.priority = Priority.P0
        analysis.issues = [IssueType.REQUESTS_NOT_SET]

        approach = determine_scaling_approach(analysis, k8s_version="1.33")

        # Single replica check comes before P0 check
        assert approach == ScalingApproach.VPA


class TestCheckVpaSupport:
    """Test VPA version support checking."""

    def test_k8s_133_supports_vpa(self):
        """Test K8s 1.33 supports in-place VPA."""
        assert _check_vpa_support("1.33") is True

    def test_k8s_134_supports_vpa(self):
        """Test K8s 1.34 supports in-place VPA."""
        assert _check_vpa_support("1.34") is True

    def test_k8s_132_no_vpa(self):
        """Test K8s 1.32 does not support in-place VPA."""
        assert _check_vpa_support("1.32") is False

    def test_k8s_130_no_vpa(self):
        """Test K8s 1.30 does not support in-place VPA."""
        assert _check_vpa_support("1.30") is False

    def test_version_with_patch(self):
        """Test version with patch number (e.g., 1.33.2)."""
        assert _check_vpa_support("1.33.2") is True
        assert _check_vpa_support("1.32.5") is False

    def test_version_with_v_prefix(self):
        """Test version with 'v' prefix (e.g., v1.33)."""
        assert _check_vpa_support("v1.33") is True
        assert _check_vpa_support("v1.32") is False

    def test_invalid_version_returns_false(self):
        """Test invalid version string returns False (conservative)."""
        assert _check_vpa_support("invalid") is False
        assert _check_vpa_support("1") is False
        assert _check_vpa_support("") is False

    def test_future_k8s_2x_supports_vpa(self):
        """Test future K8s 2.x versions support VPA."""
        assert _check_vpa_support("2.0") is True
