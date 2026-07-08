# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ImplicitFuzz is a research implementation of "Intelligent Fuzzing Based on Implicit Dependencies of Kernel Objects" (基于内核对象隐性依赖的智能化模糊测试研究). It identifies and exploits implicit dependencies between kernel objects to improve fuzzing coverage and bug discovery, via dependency-graph-based test case generation and coverage-guided fuzzing.

The repository is currently a fresh scaffold (single package `implicitfuzz` under `src/`, no functional modules yet) — `data/`, `docs/`, and `scripts/` are empty placeholders for results, documentation, and utility scripts respectively.

## Commands

Environment setup (requires Python >=3.8):

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e .          # install implicitfuzz package in editable/dev mode
```

Testing:

```bash
pytest                                    # run full test suite (config in pytest.ini)
pytest tests/test_foo.py                  # run a single test file
pytest tests/test_foo.py::TestClass::test_case   # run a single test
```

Test config (`pytest.ini`) auto-enables coverage for `src/implicitfuzz` (`--cov`, terminal + HTML report) and enforces `--strict-markers`; test discovery is limited to `tests/`, files matching `test_*.py`.

Linting/formatting/type-checking:

```bash
black src/ tests/
flake8 src/ tests/
mypy src/
```

## Architecture

- Package layout uses the `src/` layout: importable code lives in `src/implicitfuzz/`, installed via `setup.py` with `package_dir={"": "src"}`.
- Runtime dependency: `numpy`. Dev/test dependencies (`pytest`, `pytest-cov`, `black`, `flake8`, `mypy`) are declared both in `requirements.txt` and as the `dev` extra in `setup.py`.
- No fuzzing/dependency-graph logic exists yet — when implementing it, follow the `src/implicitfuzz/` package structure and add corresponding tests under `tests/`.
