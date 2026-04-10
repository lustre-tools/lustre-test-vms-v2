.PHONY: lint fix format typecheck test test-fast test-e2e coverage check

# Lint only (report errors, don't fix)
lint:
	uv run ruff check .
	uv run ruff format --check .

# Auto-fix lint errors + format
fix:
	uv run ruff check --fix .
	uv run ruff format .

# Format only (no lint fixes)
format:
	uv run ruff format .

# Type check
typecheck:
	uv run mypy ltvm ltvm_pkg/

# Run tests (with coverage summary in terminal)
test:
	uv run pytest

# Run tests without coverage (faster iteration during development)
test-fast:
	uv run pytest --no-cov -q

# Run end-to-end tests (requires KVM, built images, vm.py in PATH)
test-e2e:
	uv run pytest tests/e2e/ -v --no-cov -m e2e

# Run tests + open HTML coverage report
coverage:
	uv run pytest --cov-report=html
	@echo "Coverage report: htmlcov/index.html"

# CI-friendly check (non-zero exit on any issue)
check: lint typecheck test
