#!/usr/bin/env python3
"""Query the CozoDB database for the last scan time of a repository.

Usage: python3 get_last_scan_time.py <repo_name>
Prints the most recent scanned_at timestamp, or nothing if not found.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(1)
    repo_name = sys.argv[1]
    try:
        from db_helpers import _get_client
        client = _get_client()
        result = client.run(
            "?[s] := *repositories{repo_name: $rn, scanned_at: s}",
            {"rn": repo_name},
        )
        timestamps = sorted(
            [r[0] for r in result["rows"] if r[0]],
            reverse=True,
        )
        if timestamps:
            print(timestamps[0])
    except Exception:
        pass  # Silence errors so the shell can safely check for empty output


if __name__ == "__main__":
    main()
