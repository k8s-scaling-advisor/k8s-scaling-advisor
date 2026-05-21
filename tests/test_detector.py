"""Unit tests for issue detection logic."""
from k8s_advisor.analyzer.models import DeploymentAnalysis, Priority, IssueType
from k8s_advisor.analyzer.detector import detect_issues


class TestDetectIssues:
    """Test issue detection logic."""

    def create_baseline_analysis(self):
        """Create baseline healthy DeploymentAnalysis for testing."""
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

    def test_no_metrics_detection(self):
        """Test NO_METRICS detection when no metrics available."""
        analysis = self.create_baseline_analysis()
        analysis.avg_cpu_usage_m = 0.0
        analysis.avg_mem_usage_mi = 0.0

        issues = detect_issues(analysis)

        assert IssueType.NO_METRICS in issues
        assert len(issues) == 1  # Should return early, no other checks

    def test_requests_not_set_cpu(self):
        """Test REQUESTS_NOT_SET detection when CPU request is 0."""
        analysis = self.create_baseline_analysis()
        analysis.cpu_request_m = 0.0

        issues = detect_issues(analysis)

        assert IssueType.REQUESTS_NOT_SET in issues

    def test_requests_not_set_memory(self):
        """Test REQUESTS_NOT_SET detection when memory request is 0."""
        analysis = self.create_baseline_analysis()
        analysis.mem_request_mi = 0.0

        issues = detect_issues(analysis)

        assert IssueType.REQUESTS_NOT_SET in issues

    def test_requests_not_set_both(self):
        """Test REQUESTS_NOT_SET detection when both requests are 0."""
        analysis = self.create_baseline_analysis()
        analysis.cpu_request_m = 0.0
        analysis.mem_request_mi = 0.0

        issues = detect_issues(analysis)

        assert IssueType.REQUESTS_NOT_SET in issues

    def test_cpu_throttled_exceeds_limit(self):
        """Test CPU_THROTTLED detection when usage exceeds limit."""
        analysis = self.create_baseline_analysis()
        analysis.avg_cpu_usage_m = 500.0  # Exceeds 400m limit
        analysis.cpu_limit_m = 400.0

        issues = detect_issues(analysis)

        assert IssueType.CPU_THROTTLED in issues

    def test_cpu_throttled_high_usage_low_limit(self):
        """Test CPU_THROTTLED detection with high usage and low limit."""
        analysis = self.create_baseline_analysis()
        analysis.cpu_request_m = 100.0
        analysis.avg_cpu_usage_m = 350.0
        analysis.cpu_usage_percent = 350.0  # >300% of request
        analysis.cpu_limit_m = 150.0  # <2x request

        issues = detect_issues(analysis)

        assert IssueType.CPU_THROTTLED in issues

    def test_no_cpu_throttling_when_no_limit(self):
        """Test no CPU throttling detection when limit is not set."""
        analysis = self.create_baseline_analysis()
        analysis.cpu_limit_m = 0.0
        analysis.avg_cpu_usage_m = 500.0

        issues = detect_issues(analysis)

        assert IssueType.CPU_THROTTLED not in issues

    def test_missing_cpu_limits(self):
        """Test MISSING_CPU_LIMITS detection."""
        analysis = self.create_baseline_analysis()
        analysis.cpu_limit_m = 0.0

        issues = detect_issues(analysis)

        assert IssueType.MISSING_CPU_LIMITS in issues

    def test_rwo_pvc_detection(self):
        """Test RWO_PVC detection."""
        analysis = self.create_baseline_analysis()
        analysis.rwo_pvc = True
        analysis.rwo_pvc_names = "data-volume"

        issues = detect_issues(analysis)

        assert IssueType.RWO_PVC in issues

    def test_oom_killed_detection(self):
        """Test OOM_KILLED detection from restart reason."""
        analysis = self.create_baseline_analysis()
        analysis.restart_reason = "OOMKilled"
        analysis.total_restarts = 3

        issues = detect_issues(analysis)

        assert IssueType.OOM_KILLED in issues

    def test_mem_saturation_high_usage_of_request(self):
        """Test MEM_SATURATION detection when >200% of request."""
        analysis = self.create_baseline_analysis()
        analysis.mem_usage_percent = 250.0  # >200% threshold

        issues = detect_issues(analysis)

        assert IssueType.MEM_SATURATION in issues

    def test_mem_saturation_near_limit(self):
        """Test MEM_SATURATION detection when >90% of limit."""
        analysis = self.create_baseline_analysis()
        analysis.mem_limit_mi = 1000.0
        analysis.avg_mem_usage_mi = 950.0  # >90% of limit
        analysis.mem_usage_percent = 185.0  # <200% of request (not saturation by first check)

        issues = detect_issues(analysis)

        assert IssueType.MEM_SATURATION in issues

    def test_mem_saturation_not_flagged_with_oom_killed(self):
        """Test MEM_SATURATION not flagged if OOM_KILLED already present."""
        analysis = self.create_baseline_analysis()
        analysis.restart_reason = "OOMKilled"
        analysis.mem_usage_percent = 250.0  # Would trigger saturation
        analysis.total_restarts = 3

        issues = detect_issues(analysis)

        assert IssueType.OOM_KILLED in issues
        assert IssueType.MEM_SATURATION not in issues  # Should not double-flag

    def test_unstable_by_restart_rate(self):
        """Test UNSTABLE detection by restart rate."""
        analysis = self.create_baseline_analysis()
        analysis.total_restarts = 10
        analysis.restart_rate_per_day = 3.5  # >2.0 threshold

        issues = detect_issues(analysis)

        assert IssueType.UNSTABLE in issues

    def test_unstable_by_total_restarts_fallback(self):
        """Test UNSTABLE detection by total restarts (fallback)."""
        analysis = self.create_baseline_analysis()
        analysis.total_restarts = 10  # >5 threshold
        analysis.restart_rate_per_day = 0.0  # Rate not available

        issues = detect_issues(analysis)

        assert IssueType.UNSTABLE in issues

    def test_no_unstable_with_zero_restarts(self):
        """Test no UNSTABLE detection when total_restarts is 0 (even with garbage rate)."""
        analysis = self.create_baseline_analysis()
        analysis.total_restarts = 0
        analysis.restart_rate_per_day = 92.87  # Prometheus garbage value

        issues = detect_issues(analysis)

        # CRITICAL: Should NOT flag as unstable despite high rate
        # This validates the defensive logic against Prometheus garbage data
        assert IssueType.UNSTABLE not in issues

    def test_cpu_over_requested(self):
        """Test CPU_OVER_REQUESTED detection when <50% usage."""
        analysis = self.create_baseline_analysis()
        analysis.cpu_request_m = 200.0
        analysis.avg_cpu_usage_m = 80.0
        analysis.cpu_usage_percent = 40.0  # <50% threshold

        issues = detect_issues(analysis)

        assert IssueType.CPU_OVER_REQUESTED in issues

    def test_mem_over_requested(self):
        """Test MEM_OVER_REQUESTED detection when <50% usage."""
        analysis = self.create_baseline_analysis()
        analysis.mem_request_mi = 512.0
        analysis.avg_mem_usage_mi = 200.0
        analysis.mem_usage_percent = 39.0  # <50% threshold

        issues = detect_issues(analysis)

        assert IssueType.MEM_OVER_REQUESTED in issues

    def test_cpu_under_requested(self):
        """Test CPU_UNDER_REQUESTED detection when >85% usage."""
        analysis = self.create_baseline_analysis()
        analysis.cpu_request_m = 200.0
        analysis.avg_cpu_usage_m = 180.0
        analysis.cpu_usage_percent = 90.0  # >85% threshold

        issues = detect_issues(analysis)

        assert IssueType.CPU_UNDER_REQUESTED in issues

    def test_mem_under_requested(self):
        """Test MEM_UNDER_REQUESTED detection when >85% usage."""
        analysis = self.create_baseline_analysis()
        analysis.mem_request_mi = 512.0
        analysis.avg_mem_usage_mi = 450.0
        analysis.mem_usage_percent = 88.0  # >85% threshold

        issues = detect_issues(analysis)

        assert IssueType.MEM_UNDER_REQUESTED in issues

    def test_single_replica_detection(self):
        """Test SINGLE_REPLICA detection."""
        analysis = self.create_baseline_analysis()
        analysis.replicas = 1

        issues = detect_issues(analysis)

        assert IssueType.SINGLE_REPLICA in issues

    def test_memory_hpa_candidate_redis(self):
        """Test MEMORY_HPA_CANDIDATE detection for Redis with high volatility."""
        analysis = self.create_baseline_analysis()
        analysis.deployment = "redis-cache"
        analysis.mem_volatility = "HIGH"

        issues = detect_issues(analysis)

        assert IssueType.MEMORY_HPA_CANDIDATE in issues

    def test_memory_hpa_candidate_nginx(self):
        """Test MEMORY_HPA_CANDIDATE detection for nginx with high volatility."""
        analysis = self.create_baseline_analysis()
        analysis.deployment = "nginx-proxy"
        analysis.mem_volatility = "HIGH"

        issues = detect_issues(analysis)

        assert IssueType.MEMORY_HPA_CANDIDATE in issues

    def test_no_memory_hpa_candidate_for_jvm(self):
        """Test no MEMORY_HPA_CANDIDATE for JVM app (not memory-scalable)."""
        analysis = self.create_baseline_analysis()
        analysis.deployment = "java-app"
        analysis.mem_volatility = "HIGH"

        issues = detect_issues(analysis)

        # High volatility on non-memory-scalable app = likely memory leak
        # Should NOT be flagged as HPA candidate
        assert IssueType.MEMORY_HPA_CANDIDATE not in issues

    def test_no_memory_hpa_candidate_without_high_volatility(self):
        """Test no MEMORY_HPA_CANDIDATE without HIGH volatility."""
        analysis = self.create_baseline_analysis()
        analysis.deployment = "redis-cache"
        analysis.mem_volatility = "LOW"

        issues = detect_issues(analysis)

        assert IssueType.MEMORY_HPA_CANDIDATE not in issues

    def test_healthy_workload_no_issues(self):
        """Test healthy workload returns empty or minimal issues."""
        analysis = self.create_baseline_analysis()

        issues = detect_issues(analysis)

        # Healthy workload should not have any P0/P1 issues
        p0_p1_issues = {
            IssueType.REQUESTS_NOT_SET,
            IssueType.CPU_THROTTLED,
            IssueType.OOM_KILLED,
            IssueType.UNSTABLE,
            IssueType.MEM_SATURATION,
        }
        assert not any(issue in p0_p1_issues for issue in issues)

    def test_multiple_issues_detected(self):
        """Test multiple issues detected simultaneously."""
        analysis = self.create_baseline_analysis()
        analysis.cpu_usage_percent = 40.0  # Over-requested
        analysis.mem_usage_percent = 45.0  # Over-requested
        analysis.cpu_limit_m = 0.0  # Missing limits
        analysis.replicas = 1  # Single replica

        issues = detect_issues(analysis)

        assert IssueType.CPU_OVER_REQUESTED in issues
        assert IssueType.MEM_OVER_REQUESTED in issues
        assert IssueType.MISSING_CPU_LIMITS in issues
        assert IssueType.SINGLE_REPLICA in issues
        assert len(issues) == 4
