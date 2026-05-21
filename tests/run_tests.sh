#!/bin/bash
# Unit test runner for k8s-advisor
#
# Usage:
#   ./tests/run_tests.sh           # Run all tests
#   ./tests/run_tests.sh -v        # Run with verbose output
#   ./tests/run_tests.sh --cov     # Run with coverage report

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Change to project root
cd "$PROJECT_ROOT"

echo -e "${YELLOW}Running k8s-advisor unit tests...${NC}"
echo ""

# Check if pytest is installed
if ! venv/bin/python -c "import pytest" 2>/dev/null; then
    echo -e "${RED}ERROR: pytest not installed${NC}"
    echo "Run: venv/bin/pip install pytest pytest-cov"
    exit 1
fi

# Parse arguments
PYTEST_ARGS="-v"
if [[ "$*" == *"--cov"* ]]; then
    PYTEST_ARGS="$PYTEST_ARGS --cov=k8s_advisor --cov-report=term-missing"
fi

# Run tests
if venv/bin/python -m pytest tests/ $PYTEST_ARGS; then
    echo ""
    echo -e "${GREEN}✅ All unit tests passed!${NC}"
    exit 0
else
    echo ""
    echo -e "${RED}❌ Unit tests failed${NC}"
    exit 1
fi
