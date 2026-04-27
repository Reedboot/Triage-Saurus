#!/bin/bash
# Diagram Testing Utility
# Quick commands for validating Mermaid diagrams render correctly with emoji icons

set -e

DB="/home/neil/code/Triage-Saurus/Output/Data/cozo.db"
USAGE="
Usage: $0 <command> [experiment_id]

Commands:
  syntax <exp>        Check Mermaid syntax validity (5 sec)
  render <exp>        Render in browser and check emoji (20 sec)
  test-all            Test all experiments (2 min)
  inspect <exp>       View diagram code and structure (5 sec)
  logic <exp>         Verify connections make sense
  screenshot <exp>    Generate high-res screenshot
  quick <exp>         Run syntax + render (25 sec)

Examples:
  $0 syntax 009
  $0 render 014
  $0 test-all
  $0 quick 009
"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

if [ -z "$1" ]; then
    echo "$USAGE"
    exit 1
fi

CMD="$1"
EXP="${2:-009}"

# Function: Check syntax
check_syntax() {
    local exp=$1
    echo -e "${YELLOW}Checking syntax for Exp $exp...${NC}"
    
    # Get diagram
    local diagram=$(sqlite3 "$DB" "SELECT mermaid_code FROM cloud_diagrams WHERE experiment_id = '$exp' LIMIT 1;")
    
    if [ -z "$diagram" ]; then
        echo -e "${RED}✗ ERROR: Experiment $exp not found${NC}"
        exit 1
    fi
    
    # Create test script
    cat > /tmp/test_syntax.mjs << 'EOF'
import mermaid from 'mermaid';
const diagramCode = process.argv[2];
mermaid.initialize({ logLevel: 'error' });
try {
    mermaid.parse(diagramCode);
    console.log('✅ VALID: Syntax is correct');
    process.exit(0);
} catch (err) {
    console.log(`❌ INVALID: ${err.message}`);
    process.exit(1);
}
EOF
    
    node /tmp/test_syntax.mjs "$diagram"
}

# Function: Render diagram
render_diagram() {
    local exp=$1
    echo -e "${YELLOW}Rendering Exp $exp...${NC}"
    
    cat > /tmp/render.mjs << 'EOFSCRIPT'
import puppeteer from 'puppeteer';
import { execSync } from 'child_process';

(async () => {
    const exp = process.argv[2];
    const mermaidCode = execSync(
        `sqlite3 /home/neil/code/Triage-Saurus/Output/Data/cozo.db "SELECT mermaid_code FROM cloud_diagrams WHERE experiment_id = '${exp}' LIMIT 1;"`,
        { encoding: 'utf8' }
    );
    
    const htmlContent = `
<!DOCTYPE html>
<html>
<head>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
    <script>
        mermaid.initialize({ startOnLoad: true, logLevel: 'error' });
    </script>
</head>
<body>
    <div class="mermaid">
${mermaidCode}
    </div>
</body>
</html>
`;
    
    const browser = await puppeteer.launch({ 
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    
    const page = await browser.newPage();
    await page.setContent(htmlContent);
    
    try {
        await page.waitForFunction(() => document.querySelector('svg') !== null, { timeout: 8000 });
    } catch {
        console.log('❌ RENDER FAILED: Diagram did not render');
        await browser.close();
        process.exit(1);
    }
    
    const result = await page.evaluate(() => {
        const svg = document.querySelector('svg');
        const html = svg ? svg.innerHTML : '';
        const emojis = ['🌍', '🔌', '📦', '🗄️', '📄'];
        const found = emojis.filter(e => html.includes(e));
        return {
            rendered: !!svg,
            svgSize: html.length,
            emojiCount: found.length,
            emojis: found,
            nodeCount: svg ? svg.querySelectorAll('g').length : 0
        };
    });
    
    console.log(`✅ RENDERED: Exp ${exp}`);
    console.log(`   SVG: ${result.svgSize} bytes`);
    console.log(`   Emoji: ${result.emojiCount} found`);
    console.log(`   ${result.emojis.join(' ')}`);
    console.log(`   Nodes: ${result.nodeCount} elements`);
    
    await page.screenshot({ path: `/tmp/diagram_${exp}.png`, fullPage: true });
    console.log(`   Screenshot: /tmp/diagram_${exp}.png`);
    
    await browser.close();
})();
EOFSCRIPT
    
    node /tmp/render.mjs "$exp"
}

# Function: Test all experiments
test_all_experiments() {
    echo -e "${YELLOW}Testing all experiments...${NC}"
    
    cat > /tmp/test_all.mjs << 'EOFSCRIPT'
import puppeteer from 'puppeteer';
import { execSync } from 'child_process';

const experiments = ['009', '014', '015', '016', '017', '018'];

(async () => {
    const browser = await puppeteer.launch({ 
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    
    console.log('Testing all experiments:\n');
    let passCount = 0, failCount = 0;
    
    for (const exp of experiments) {
        try {
            const mermaidCode = execSync(
                `sqlite3 /home/neil/code/Triage-Saurus/Output/Data/cozo.db "SELECT mermaid_code FROM cloud_diagrams WHERE experiment_id = '${exp}' LIMIT 1;"`,
                { encoding: 'utf8' }
            );
            
            const page = await browser.newPage();
            const htmlContent = `<html><head><script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script><script>mermaid.initialize({startOnLoad:true,logLevel:'error'});</script></head><body><div class="mermaid">${mermaidCode}</div></body></html>`;
            
            await page.setContent(htmlContent);
            
            try {
                await page.waitForFunction(() => document.querySelector('svg') !== null, { timeout: 8000 });
            } catch {
                console.log(`✗ Exp ${exp}: Render timeout`);
                failCount++;
                await page.close();
                continue;
            }
            
            const result = await page.evaluate(() => {
                const svg = document.querySelector('svg');
                const emojis = ['🌍', '🔌', '📦', '🗄️', '📄'];
                const found = svg ? emojis.filter(e => svg.innerHTML.includes(e)).length : 0;
                return { rendered: !!svg, emojiCount: found, nodeCount: svg ? svg.querySelectorAll('g').length : 0 };
            });
            
            const status = result.rendered && result.emojiCount > 0 ? '✓' : '✗';
            console.log(`${status} Exp ${exp}: ${result.emojiCount} emoji, ${result.nodeCount} nodes`);
            
            if (result.rendered && result.emojiCount > 0) passCount++;
            else failCount++;
            
            await page.close();
        } catch (err) {
            console.log(`✗ Exp ${exp}: ${err.message.substring(0, 30)}`);
            failCount++;
        }
    }
    
    await browser.close();
    console.log(`\n═══════════════════════════════`);
    console.log(`Passed: ${passCount}/${experiments.length}`);
    console.log(`Failed: ${failCount}/${experiments.length}`);
    if (failCount === 0) console.log('✓ ALL TESTS PASSED');
})();
EOFSCRIPT
    
    node /tmp/test_all.mjs
}

# Function: Inspect diagram
inspect_diagram() {
    local exp=$1
    echo -e "${YELLOW}Inspecting Exp $exp...${NC}\n"
    
    local diagram=$(sqlite3 "$DB" "SELECT mermaid_code FROM cloud_diagrams WHERE experiment_id = '$exp' LIMIT 1;")
    
    if [ -z "$diagram" ]; then
        echo -e "${RED}✗ ERROR: Experiment $exp not found${NC}"
        exit 1
    fi
    
    echo "=== First 50 lines ==="
    echo "$diagram" | head -50
    
    echo -e "\n=== Emoji in diagram ==="
    echo "$diagram" | grep -o "[🌍🔌📦🗄️📄]" | sort | uniq -c
    
    echo -e "\n=== Connection count ==="
    echo "$diagram" | grep -E "^\s+n[0-9]+ (-->|-.->)" | wc -l
    
    echo -e "\n=== Size ==="
    echo "$(echo "$diagram" | wc -c) bytes"
}

# Function: Check logic
check_logic() {
    local exp=$1
    echo -e "${YELLOW}Checking diagram logic for Exp $exp...${NC}\n"
    
    local diagram=$(sqlite3 "$DB" "SELECT mermaid_code FROM cloud_diagrams WHERE experiment_id = '$exp' LIMIT 1;")
    
    echo "=== Key Connections ==="
    echo "$diagram" | grep "-->.*\"bound to\"" && echo "  ✓ Public IP bound to NIC" || echo "  ✗ No NIC binding"
    echo "$diagram" | grep "internet -.->.*Public" && echo "  ✓ Internet accessible to public resources" || echo "  ✗ No internet access"
    echo "$diagram" | grep -E "n[0-9]+ --> n[0-9]+" && echo "  ✓ Data flow connections present" || echo "  ✗ No direct connections"
}

# Function: Generate screenshot
generate_screenshot() {
    local exp=$1
    echo -e "${YELLOW}Generating screenshot for Exp $exp...${NC}"
    
    cat > /tmp/screenshot.mjs << 'EOFSCRIPT'
import puppeteer from 'puppeteer';
import { execSync } from 'child_process';

(async () => {
    const exp = process.argv[2];
    const mermaidCode = execSync(
        `sqlite3 /home/neil/code/Triage-Saurus/Output/Data/cozo.db "SELECT mermaid_code FROM cloud_diagrams WHERE experiment_id = '${exp}' LIMIT 1;"`,
        { encoding: 'utf8' }
    );
    
    const browser = await puppeteer.launch({ 
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    
    const page = await browser.newPage();
    await page.setViewport({ width: 1400, height: 2000 });
    
    const htmlContent = `<html><head><script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script><script>mermaid.initialize({startOnLoad:true,logLevel:'error'});</script></head><body><div class="mermaid">${mermaidCode}</div></body></html>`;
    
    await page.setContent(htmlContent);
    await page.waitForFunction(() => document.querySelector('svg') !== null, { timeout: 10000 });
    
    await page.screenshot({ path: `/tmp/screenshot_${exp}.png`, fullPage: true });
    console.log(`✓ Screenshot saved: /tmp/screenshot_${exp}.png`);
    
    await browser.close();
})();
EOFSCRIPT
    
    node /tmp/screenshot.mjs "$exp"
}

# Main logic
case "$CMD" in
    syntax)
        check_syntax "$EXP"
        ;;
    render)
        render_diagram "$EXP"
        ;;
    test-all)
        test_all_experiments
        ;;
    inspect)
        inspect_diagram "$EXP"
        ;;
    logic)
        check_logic "$EXP"
        ;;
    screenshot)
        generate_screenshot "$EXP"
        ;;
    quick)
        check_syntax "$EXP" && render_diagram "$EXP"
        ;;
    *)
        echo -e "${RED}Unknown command: $CMD${NC}"
        echo "$USAGE"
        exit 1
        ;;
esac
