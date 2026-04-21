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

Unit tests are currently maintained in their respective module directories:
- `Scripts/Analyze/` - Analyzer unit tests
- `Scripts/Context/` - Context extraction unit tests  
- `Scripts/Generate/` - Diagram and report generation unit tests
- `Scripts/Scan/` - Scan pipeline unit tests
- `Scripts/Validate/` - Validation unit tests

To run all unit tests with pytest:

```bash
pip install pytest
python -m pytest Scripts/Analyze/ Scripts/Context/ Scripts/Generate/ Scripts/Scan/ Scripts/Validate/ -v
```

## CI/CD Integration

See `.github/workflows/` for automated test execution on commits.
