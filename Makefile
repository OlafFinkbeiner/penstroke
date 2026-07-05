# Common penstroke commands. Run `make help` to see all targets.
# Unix-flavored (paths, find): on Windows run these under Git Bash,
# or use the equivalent commands from docs/user_guide.md directly.

.PHONY: help install install-dev test trace clean

help:
	@echo "Available targets:"
	@echo "  install       Install penstroke and its runtime dependencies"
	@echo "  install-dev   Install with dev + OCR extras"
	@echo "  test          Run the full test suite (pytest)"
	@echo "  trace         Trace tests/fixtures/caveat.ttf to /tmp/caveat_out/"
	@echo "  clean         Remove caches and build artifacts"

install:
	pip install -e .

install-dev:
	pip install -e ".[dev,ocr]"

test:
	pytest tests/

trace:
	penstroke trace tests/fixtures/caveat.ttf /tmp/caveat_out/
	@echo ""
	@echo "Output written to /tmp/caveat_out/"
	@echo "  open /tmp/caveat_out/alphabet_static.svg     # visual check"
	@echo "  open /tmp/caveat_out/word_demo.html          # composition demo"
	@echo "  cat  /tmp/caveat_out/report.md               # QA report"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/ .pytest_cache/ .coverage htmlcov/
