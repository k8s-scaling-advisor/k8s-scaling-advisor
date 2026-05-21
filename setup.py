"""Setup configuration for K8s Scaling Advisor."""

from pathlib import Path

from setuptools import find_packages, setup

# Read long description from README
this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text(encoding="utf-8")

setup(
    name="k8s-advisor",
    version="3.0.0",
    description="Kubernetes resource optimization and autoscaling advisor",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="K8s Scaling Advisor Contributors",
    python_requires=">=3.10",
    packages=find_packages(exclude=["tests", "examples"]),
    py_modules=["main"],
    # Core dependencies
    install_requires=[
        "kubernetes>=12.0.1",  # For K8s API access
        "jinja2>=3.1.6",  # For markdown report templating
        "requests>=2.32.0",  # For Prometheus HTTP queries
        "pyyaml>=6.0",  # For --profiles policy YAML parsing
    ],
    # Ship the Jinja templates with the package.
    include_package_data=True,
    package_data={
        "k8s_advisor": ["templates/*.md.j2"],
    },
    # Optional dependencies for visualization and development
    extras_require={
        "viz": [
            "pandas>=1.5.3",
            "matplotlib>=3.9.4",
            "numpy>=1.20.0",
        ],
        "dev": [
            "pytest>=7.0.0",
            "pytest-cov>=3.0.0",
            "black>=22.0.0",
            "ruff>=0.15.13",
            "mypy>=0.990",
        ],
        "all": [
            "pandas>=1.5.3",
            "matplotlib>=3.9.4",
            "numpy>=1.20.0",
            "pytest>=7.0.0",
            "pytest-cov>=3.0.0",
            "black>=22.0.0",
            "ruff>=0.15.13",
            "mypy>=0.990",
        ],
    },
    # CLI entry points
    entry_points={
        "console_scripts": [
            "k8s-advisor=k8s_advisor.cli:main",
        ],
    },
    # Package metadata
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: System :: Monitoring",
        "Topic :: System :: Systems Administration",
    ],
    keywords="kubernetes k8s hpa vpa autoscaling resource-optimization",
)
