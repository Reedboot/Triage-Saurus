# Issue intake

Place issues or exported findings here for processing.

## Single issue
- Create a file like `Intake/issue.md` and paste the issue text (scanner output, ticket, etc.).

## Bulk findings
You can place findings anywhere under `Intake/` (including subfolders).

### Option A: 1 file per finding
- Drop multiple files (e.g., `.md`) into `Intake/` (or a subfolder), one finding per file.
- The first non-empty line of each file is treated as the finding title.

### Option B: line lists (`.txt` / `.csv`)
- Put a `.txt` or `.csv` file under `Intake/` with **one finding title per line**.

## Sample findings
If you want to use the repo samples, stage them into Intake first:
- `python3 Skills/stage_sample_findings_to_intake.py --type cloud`
- `python3 Skills/stage_sample_findings_to_intake.py --type code`

## Batch processing (ask me)
If you want to batch-generate draft findings from Intake, ask the agent to run:
- `python3 Skills/generate_findings_from_titles.py --provider <azure|aws|gcp> --in-dir <intake-path> --out-dir Findings/Cloud --update-knowledge`

## Notes
- Avoid pasting secrets/credentials.
- If you share a subfolder path (e.g., `Intake/Cloud`), the agent can process everything in it sequentially.
