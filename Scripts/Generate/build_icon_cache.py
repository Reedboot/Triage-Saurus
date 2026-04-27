#!/usr/bin/env python3
"""Pre-generate icon mapping cache files for fast API serving.

Generates JSON files mapping resource types to icon URLs for each provider.
These are served directly by the API endpoint without any computation.

Usage:
    python3 build_icon_cache.py              # Build all providers
    python3 build_icon_cache.py azure aws    # Build specific providers
"""

import sys
import json
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from icon_resolver import build_icon_map_bulk, AZURE_RESOURCE_TYPE_TO_ICON, AWS_RESOURCE_TYPE_TO_ICON, GCP_RESOURCE_TYPE_TO_ICON, KUBERNETES_RESOURCE_TYPE_TO_ICON, OTHER_RESOURCE_TYPE_TO_ICON

CACHE_DIR = REPO_ROOT / "web" / "static" / "assets" / "icon-cache"


def build_and_save_icon_caches(providers=None):
    """Build icon maps for specified providers and save as JSON files.
    
    Args:
        providers: List of provider names to build. If None, builds all.
    """
    if providers is None:
        providers = ['azure', 'aws', 'gcp', 'kubernetes', 'other']
    
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    # Build all-provider cache
    all_icons = {}
    
    for provider in providers:
        print(f"Building icon cache for {provider}...", end=" ", flush=True)
        start = time.time()
        
        icon_map = build_icon_map_bulk(provider)
        elapsed = time.time() - start
        
        # Save to file
        cache_file = CACHE_DIR / f"icon-mappings-{provider}.json"
        cache_file.write_text(json.dumps(icon_map, indent=2))
        
        print(f"✓ {len(icon_map)} mappings in {elapsed:.2f}s")
        
        # Merge into all-provider map
        all_icons.update(icon_map)
    
    # Save all-provider cache
    print(f"Saving all-provider cache...", end=" ", flush=True)
    all_cache_file = CACHE_DIR / "icon-mappings-all.json"
    all_cache_file.write_text(json.dumps(all_icons, indent=2))
    print(f"✓ {len(all_icons)} total mappings")
    
    print(f"\n✅ Icon caches saved to {CACHE_DIR}/")
    print(f"   - icon-mappings-all.json ({len(all_icons)} mappings)")
    for provider in providers:
        cache_file = CACHE_DIR / f"icon-mappings-{provider}.json"
        data = json.loads(cache_file.read_text())
        print(f"   - icon-mappings-{provider}.json ({len(data)} mappings)")


if __name__ == "__main__":
    providers = sys.argv[1:] if len(sys.argv) > 1 else None
    
    try:
        build_and_save_icon_caches(providers)
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
