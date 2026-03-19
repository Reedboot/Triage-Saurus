# Repository Location Configuration

## Performance: WSL2 Filesystem Issue

**Problem:** Scanning repos from `/mnt/c/` (Windows filesystem) is 40-90x slower than native Linux filesystem.

**Root cause:** WSL2 uses 9P network protocol to bridge Linux VM ↔ Windows host. Each file operation crosses VM boundary with significant overhead.

**Benchmark (account-viewing-permissions, 537 files):**
- `/mnt/c/Repos/` → 4-5 minutes for detection scan
- `~/repos/` → 6 seconds for detection scan

## How Scripts Find Repos

All scanning scripts use **`Scripts/Utils/repo_resolver.py`** which reads **`Settings/paths.json`** to find repos.

### Resolution order:
1. If you provide an **absolute path**, it's used as-is: `/home/neil/repos/my-repo`
2. If you provide a **repo name**, it searches configured paths in order: `account-viewing-permissions`
3. **First match wins** - if the same repo exists in multiple locations, the first search path containing it is used

### Example:
```python
from repo_resolver import resolve_repo

# These all work:
resolve_repo("/home/neil/repos/account-viewing-permissions")  # Absolute path
resolve_repo("account-viewing-permissions")                   # Searches configured paths
resolve_repo("~/repos/account-viewing-permissions")           # Tilde expansion
```

### Scripts using repo resolver:
- ✅ `triage_experiment.py` - Resolves repo names from experiment config
- ✅ `web/app.py` - Resolves repos from `Intake/ReposToScan.txt`
- 🔄 Other scripts - Pass resolved paths through as arguments

## Configuration

### Settings/paths.json

Configure where repos are located:

```json
{
  "repo_search_paths": [
    "/home/neil/repos",      
    "~/repos",               
    "/mnt/c/Repos",          
    "~"
  ],
  "default_repo_root": "/home/neil/repos"
}
```

**Search order:** Paths are searched in order when resolving repo names from `Intake/ReposToScan.txt`. First match wins.

**Tilde expansion:** `~` is automatically expanded to home directory.

## Migration Tool

### List repos in Windows filesystem:
```bash
python3 Scripts/Utils/migrate_repos.py --from /mnt/c/Repos --to ~/repos --list
```

### Migrate single repo:
```bash
python3 Scripts/Utils/migrate_repos.py --from /mnt/c/Repos --to ~/repos --migrate account-viewing-permissions
```

### Migrate all repos:
```bash
python3 Scripts/Utils/migrate_repos.py --from /mnt/c/Repos --to ~/repos --migrate-all
```

### Dry run (see what would happen):
```bash
python3 Scripts/Utils/migrate_repos.py --from /mnt/c/Repos --to ~/repos --migrate-all --dry-run
```

## Accessing Repos from Windows

After migrating repos to native Linux filesystem (`~/repos`), you can still access them from Windows:

### File Explorer:
```
\\wsl$\Ubuntu\home\neil\repos\
```

### VS Code:
1. Install "Remote - WSL" extension
2. Open folder: `\\wsl$\Ubuntu\home\neil\repos\account-viewing-permissions`

Or use VS Code's WSL integration: `code ~/repos/account-viewing-permissions` from WSL terminal

### Git Clients:
Most Windows Git clients can access `\\wsl$\` paths directly.

## Best Practices

1. **Store repos in native Linux filesystem** (`~/repos/`) for scanning performance
2. **Keep Triage-Saurus in `/mnt/c/Repos/Triage-Saurus`** if you prefer Windows tools for development
3. **Keep `/mnt/c/Repos/` as fallback** in search paths for repos you haven't migrated yet
4. **Update `Intake/ReposToScan.txt`** - just list repo names (e.g., `account-viewing-permissions`), the search paths will find them

## Migration Notes

- Migration uses `rsync` for efficient copying
- Original repos in `/mnt/c/Repos/` are NOT deleted - manual cleanup required
- Settings/paths.json is automatically updated after successful migration
- Web app will automatically find repos in new location after restart

## Testing the Configuration

### Check which paths are searched:
```bash
cat Settings/paths.json
```

### Find a specific repo:
```bash
python3 Scripts/Utils/repo_resolver.py account-viewing-permissions
# Output: ✅ Resolved: /home/neil/repos/account-viewing-permissions
```

### List all available repos:
```bash
python3 Scripts/Utils/repo_resolver.py --list
```

### Check default repo root:
```bash
python3 Scripts/Utils/repo_resolver.py --default-root
# Output: Default repos root: /home/neil/repos
```

### Test a scan from native filesystem:
```bash
# Should complete in ~6 seconds instead of 4+ minutes
time opengrep scan --config Rules/Detection ~/repos/account-viewing-permissions --json --output /tmp/test.json --quiet
```

## Troubleshooting

**Problem:** "ERROR: repo path not found: account-viewing-permissions"

**Solution:** 
1. Check repo exists: `ls ~/repos/account-viewing-permissions`
2. Check search paths: `python3 Scripts/Utils/repo_resolver.py --list`
3. Verify Settings/paths.json includes the directory containing your repo

**Problem:** Scans still slow after migration

**Solution:** Verify you're scanning the Linux path, not Windows:
```bash
# Slow (Windows filesystem):
python3 Scripts/Scan/targeted_scan.py /mnt/c/Repos/account-viewing-permissions

# Fast (Linux filesystem):
python3 Scripts/Scan/targeted_scan.py ~/repos/account-viewing-permissions
```
