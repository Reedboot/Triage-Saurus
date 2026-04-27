# Icon Rendering Testing Instructions

## Current Status
- ✅ All 11 diagrams are syntactically valid
- ✅ Fixed exp 004 hyphenated marker issue
- ✅ All diagrams now use underscore-based class names
- ⏳ Icon rendering still needs to be tested in browser

## What to Test

### Test 1: Open a specific diagram in web UI
1. Open http://localhost:9000 in your browser
2. Select a repository (e.g., "AzureGoat")
3. Click a past scan (e.g., "006" or "007")
4. Look at the architecture diagram

**Expected Result:** Diagram should render WITHOUT "Syntax error in text" message

### Test 2: Check browser console for SVG structure
1. Press F12 to open developer tools
2. Go to **Console** tab
3. Look for logs starting with `[MermaidIconInjector DEBUG]`

**What you'll see:**
```
[MermaidIconInjector DEBUG] Total <g> elements: XXX
[MermaidIconInjector DEBUG] <g> with class: YYY
[MermaidIconInjector DEBUG] Sample g classes: ['class1', 'class2', ...]
```

**What to report back:**
- How many total `<g>` elements exist?
- How many have class attributes?
- What are the class names? (post the "Sample g classes" array)

### Test 3: Check if icons render
- Do you see cloud provider icons (☁️, 🔒, 💾, etc.) on the diagram nodes?
- Expected: Each resource should have an icon overlay

## Why This Matters
The icon injector looks for `<g>` elements with class names containing "node" or "cluster". 
If it finds 0 nodes, it means Mermaid is either:
1. Not creating g elements at all
2. Creating them but without class attributes
3. Using different SVG structure in v11.x

Your browser console output will tell us which case applies.

## Test Instructions for User

Please:
1. Reload http://localhost:9000 (to get the updated mermaid-icon-injector.js with debug logging)
2. Select "AzureGoat" repo and click scan #006
3. Press F12 and check Console tab
4. Report back the `[MermaidIconInjector DEBUG]` output

This will tell us exactly what SVG structure Mermaid is creating and why icons aren't rendering.
