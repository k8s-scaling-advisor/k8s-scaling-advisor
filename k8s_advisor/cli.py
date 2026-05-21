#!/usr/bin/env python3
"""K8s Scaling Advisor CLI - Entry point for installed package."""

import sys
from pathlib import Path


def main() -> None:
    """Run CLI entry point from repository root script."""
    project_root = Path(__file__).resolve().parent.parent
    root_str = str(project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    from main import main as root_main

    root_main()


if __name__ == "__main__":
    main()
