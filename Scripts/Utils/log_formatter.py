"""Terminal output formatting with colors and emoji for scan logs."""


class Color:
    """ANSI color codes."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    
    RED = "\033[91m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    CYAN = "\033[96m"


class Header:
    """Consistent header styling for scan logs."""
    
    # Major phase headers (yellow background-like appearance)
    MISCONFIGURATIONS = f"{Color.YELLOW}[Misconfigurations]{Color.RESET}"
    STORE = f"{Color.YELLOW}[Store]{Color.RESET}"
    DETECTION = f"{Color.YELLOW}[detection]{Color.RESET}"
    DRY_RUN = f"{Color.YELLOW}[dry-run]{Color.RESET}"
    
    # Status headers
    ERROR = f"{Color.RED}[error]{Color.RESET}"
    WARN = f"{Color.YELLOW}[warn]{Color.RESET}"
    INFO = f"{Color.CYAN}[info]{Color.RESET}"


def format_finding(finding_id: int, title: str, severity: str) -> str:
    """Format a finding with emoji and color based on severity.
    
    Args:
        finding_id: The finding ID number
        title: Finding title/description
        severity: One of 'High', 'Medium', 'Low'
    
    Returns:
        Formatted string with colors and emoji
    """
    severity_upper = severity.upper()
    
    if severity_upper == "HIGH":
        emoji = "🔴"
        color = Color.RED
    elif severity_upper == "MEDIUM":
        emoji = "🟡"
        color = Color.YELLOW
    elif severity_upper == "LOW":
        emoji = "🟢"
        color = Color.CYAN
    else:
        emoji = "⚪"
        color = Color.RESET
    
    return f"  [stored] 💾 finding {finding_id} : {color}{emoji} {title} ({severity}){Color.RESET}"


def format_scan_complete() -> str:
    """Format the scan completion message."""
    return f"{Color.GREEN}{Color.BOLD}✓{Color.RESET} Scan complete"


def format_summary(stored: int, skipped: int) -> str:
    """Format the final summary."""
    return f"{Color.BOLD}Stored {stored} new findings, skipped {skipped} duplicates{Color.RESET}"
