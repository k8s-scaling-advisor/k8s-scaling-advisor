"""Unit tests for analyzer data models."""
from k8s_advisor.analyzer.models import (
    DeploymentAnalysis,
    Priority,
    ScalingApproach,
    IssueType,
    ResourceRecommendation,
)


class TestPriorityEnum:
    """Test Priority enum values."""

    def test_priority_values(self):
        """Verify priority enum has correct string values."""
        assert Priority.P0.value == "P0"
        assert Priority.P1.value == "P1"
        assert Priority.P2.value == "P2"
        assert Priority.P3.value == "P3"


class TestScalingApproachEnum:
    """Test ScalingApproach enum values."""

    def test_scaling_approach_values(self):
        """Verify scaling approach enum has correct string values."""
        assert ScalingApproach.HPA.value == "HPA"
        assert ScalingApproach.VPA.value == "VPA"
        assert ScalingApproach.MANUAL.value == "MANUAL"
        assert ScalingApproach.NONE.value == "NONE"
        assert ScalingApproach.HPA_AFTER_FIX.value == "HPA_AFTER_FIX"


class TestIssueTypeEnum:
    """Test IssueType enum values."""

    def test_issue_type_values(self):
        """Verify all issue types have correct string values."""
        assert IssueType.REQUESTS_NOT_SET.value == "REQUESTS_NOT_SET"
        assert IssueType.CPU_THROTTLED.value == "CPU_THROTTLED"
        assert IssueType.RWO_PVC.value == "RWO_PVC"
        assert IssueType.UNSTABLE.value == "UNSTABLE"
        assert IssueType.OOM_KILLED.value == "OOM_KILLED"
        assert IssueType.MEM_SATURATION.value == "MEM_SATURATION"
        assert IssueType.CPU_OVER_REQUESTED.value == "CPU_OVER_REQUESTED"
        assert IssueType.CPU_UNDER_REQUESTED.value == "CPU_UNDER_REQUESTED"
        assert IssueType.MEM_OVER_REQUESTED.value == "MEM_OVER_REQUESTED"
        assert IssueType.MEM_UNDER_REQUESTED.value == "MEM_UNDER_REQUESTED"


class TestResourceRecommendation:
    """Test ResourceRecommendation dataclass."""

    def test_default_values(self):
        """Test default values for optional fields."""
        rec = ResourceRecommendation()
        assert rec.cpu_request is None
        assert rec.cpu_limit is None
        assert rec.memory_request is None
        assert rec.memory_limit is None
        assert rec.rationale == ""
        assert rec.requires_manual_action is False

    def test_with_values(self):
        """Test creating recommendation with values."""
        rec = ResourceRecommendation(
            cpu_request="125m",
            cpu_limit="250m",
            memory_request="256Mi",
            memory_limit="512Mi",
            rationale="Right-sized based on P95 usage",
            requires_manual_action=True
        )
        assert rec.cpu_request == "125m"
        assert rec.cpu_limit == "250m"
        assert rec.memory_request == "256Mi"
        assert rec.memory_limit == "512Mi"
        assert rec.rationale == "Right-sized based on P95 usage"
        assert rec.requires_manual_action is True


class TestDeploymentAnalysis:
    """Test DeploymentAnalysis dataclass."""

    def create_minimal_analysis(self):
        """Create minimal valid DeploymentAnalysis for testing."""
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

    def test_minimal_analysis_creation(self):
        """Test creating minimal DeploymentAnalysis."""
        analysis = self.create_minimal_analysis()
        assert analysis.cluster == "test-cluster"
        assert analysis.namespace == "default"
        assert analysis.deployment == "test-app"
        assert analysis.replicas == 3
        assert analysis.priority == Priority.P3

    def test_default_prometheus_metrics(self):
        """Test default values for optional Prometheus metrics."""
        analysis = self.create_minimal_analysis()
        assert analysis.cpu_p95_m == 0.0
        assert analysis.cpu_max_m == 0.0
        assert analysis.mem_p95_mi == 0.0
        assert analysis.mem_volatility == "N/A"
        assert analysis.restart_rate_per_day == 0.0

    def test_has_prometheus_metrics_false(self):
        """Test has_prometheus_metrics property when metrics are default."""
        analysis = self.create_minimal_analysis()
        assert analysis.has_prometheus_metrics is False

    def test_has_prometheus_metrics_true_cpu(self):
        """Test has_prometheus_metrics property with CPU metrics."""
        analysis = self.create_minimal_analysis()
        analysis.cpu_p95_m = 150.0
        assert analysis.has_prometheus_metrics is True

    def test_has_prometheus_metrics_true_memory(self):
        """Test has_prometheus_metrics property with memory metrics."""
        analysis = self.create_minimal_analysis()
        analysis.mem_p95_mi = 300.0
        assert analysis.has_prometheus_metrics is True

    def test_has_prometheus_metrics_true_restart_rate(self):
        """Test has_prometheus_metrics property with restart rate."""
        analysis = self.create_minimal_analysis()
        analysis.restart_rate_per_day = 2.5
        assert analysis.has_prometheus_metrics is True

    def test_has_prometheus_metrics_true_volatility(self):
        """Test has_prometheus_metrics property with volatility."""
        analysis = self.create_minimal_analysis()
        analysis.mem_volatility = "HIGH"
        assert analysis.has_prometheus_metrics is True

    def test_is_statefulset_false(self):
        """Test is_statefulset property for Deployment."""
        analysis = self.create_minimal_analysis()
        assert analysis.is_statefulset is False

    def test_is_statefulset_true(self):
        """Test is_statefulset property for StatefulSet."""
        analysis = self.create_minimal_analysis()
        analysis.workload_type = "StatefulSet"
        assert analysis.is_statefulset is True

    def test_is_statefulset_case_insensitive(self):
        """Test is_statefulset property is case insensitive."""
        analysis = self.create_minimal_analysis()
        analysis.workload_type = "STATEFULSET"
        assert analysis.is_statefulset is True

    def test_is_multi_replica_true(self):
        """Test is_multi_replica property with multiple replicas."""
        analysis = self.create_minimal_analysis()
        assert analysis.is_multi_replica is True

    def test_is_multi_replica_false(self):
        """Test is_multi_replica property with single replica."""
        analysis = self.create_minimal_analysis()
        analysis.replicas = 1
        assert analysis.is_multi_replica is False

    def test_has_cpu_throttling_false(self):
        """Test has_cpu_throttling property when no throttling."""
        analysis = self.create_minimal_analysis()
        assert analysis.has_cpu_throttling is False

    def test_has_cpu_throttling_true(self):
        """Test has_cpu_throttling property when throttling detected."""
        analysis = self.create_minimal_analysis()
        analysis.issues = [IssueType.CPU_THROTTLED]
        assert analysis.has_cpu_throttling is True

    def test_has_oom_killed_false(self):
        """Test has_oom_killed property when no OOM kills."""
        analysis = self.create_minimal_analysis()
        assert analysis.has_oom_killed is False

    def test_has_oom_killed_true(self):
        """Test has_oom_killed property when OOM killed."""
        analysis = self.create_minimal_analysis()
        analysis.issues = [IssueType.OOM_KILLED]
        assert analysis.has_oom_killed is True

    def test_is_unstable_false(self):
        """Test is_unstable property when stable."""
        analysis = self.create_minimal_analysis()
        assert analysis.is_unstable is False

    def test_is_unstable_true(self):
        """Test is_unstable property when unstable."""
        analysis = self.create_minimal_analysis()
        analysis.issues = [IssueType.UNSTABLE]
        assert analysis.is_unstable is True

    def test_has_p0_issues_false(self):
        """Test has_p0_issues property when priority is not P0."""
        analysis = self.create_minimal_analysis()
        analysis.priority = Priority.P2
        assert analysis.has_p0_issues is False

    def test_has_p0_issues_true(self):
        """Test has_p0_issues property when priority is P0."""
        analysis = self.create_minimal_analysis()
        analysis.priority = Priority.P0
        assert analysis.has_p0_issues is True

    def test_to_dict_conversion(self):
        """Test to_dict method converts enums to strings."""
        analysis = self.create_minimal_analysis()
        analysis.issues = [IssueType.CPU_OVER_REQUESTED, IssueType.MEM_OVER_REQUESTED]
        analysis.scaling_approach = ScalingApproach.HPA

        result = analysis.to_dict()

        assert result['priority'] == "P3"
        assert result['issues'] == ["CPU_OVER_REQUESTED", "MEM_OVER_REQUESTED"]
        assert result['scaling_approach'] == "HPA"
        assert result['cluster'] == "test-cluster"
        assert result['replicas'] == 3

    def test_to_dict_empty_issues(self):
        """Test to_dict with empty issues list."""
        analysis = self.create_minimal_analysis()
        result = analysis.to_dict()
        assert result['issues'] == []
