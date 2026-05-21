"""Public analysis API wrapper.

This module keeps a stable import path (`k8s_advisor.analyze`) while delegating
to the actively maintained self-contained analyzer implementation.
"""

from .simple_analyzer import analyze_csv_file


def analyze_csv(csv_path: str, output_dir: str = "reports", output_prefix: str = "k8s-advisor") -> str:
    """Analyze CSV file and generate markdown report.

    Args:
        csv_path: Path to input CSV file.
        output_dir: Directory for output files.
        output_prefix: Reserved for backward compatibility. Output naming is
            currently managed by `simple_analyzer` and uses the
            `k8s-advisor_<cluster>_<timestamp>.md` convention.

    Returns:
        Path to generated markdown report.
    """
    _ = output_prefix  # Kept for compatibility with previous function signature.
    return analyze_csv_file(csv_path=csv_path, output_dir=output_dir, generate_graphs=False)
