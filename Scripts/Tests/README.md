# Test Suite

## Integration Tests

**`test_run_cozo_init.sh`** - Full pipeline integration test
- Sets up virtual environment
- Installs dependencies
- Clones/links test repositories
- Runs Cozo scan pipeline
- Verifies database schema and tables
- Checks for required learning compatibility tables

### Running the Integration Test

```bash
bash Scripts/Tests/test_run_cozo_init.sh
```

**Requirements:**
- Python 3
- opengrep (will auto-install)
- Network access (to clone test repos)

## Unit Tests

Unit tests live alongside their source modules (canonical location):
- `Scripts/Analyze/test_*.py` — Analyzer unit tests
- `Scripts/Context/test_*.py` — Context extraction unit tests
- `Scripts/Generate/test_*.py` — Diagram and report generation unit tests
- `Scripts/Scan/test_*.py` — Scan pipeline unit tests
- `Scripts/Validate/test_*.py` — Validation unit tests

**Do not duplicate module-level tests here.** This directory is for integration and contract tests only (files listed in the Integration Tests section above).

To run all unit tests from the repo root:

```bash
pip install pytest
python -m pytest -v
```

A `pytest.ini` at the repo root configures test discovery automatically.

## CI/CD Integration

See `.github/workflows/tests.yml` for automated test execution on commits (OpenGrep rules validation + integration test).
