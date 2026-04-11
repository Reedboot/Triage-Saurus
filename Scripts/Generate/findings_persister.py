"""
Helper utilities for persisting findings with timeout resilience.

These utilities help ensure that findings are persisted to the database
even if the overall scan times out. Use these in scanning loops to commit
findings frequently rather than batching them all at the end.
"""

from typing import List, Dict, Optional
from pathlib import Path
import sys

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "Persist"))

from db_helpers import get_db_connection


class FindingsPersister:
    """Helper for persisting findings with periodic commits."""
    
    def __init__(self, experiment_id: str, batch_size: int = 100):
        """
        Initialize persister.
        
        Args:
            experiment_id: The experiment ID for this scan
            batch_size: Number of findings before auto-commit (default 100)
        """
        self.experiment_id = experiment_id
        self.batch_size = batch_size
        self.findings_buffer = []
        self.findings_count = 0
    
    def add_finding(self, resource_id: int, finding_type: str, 
                   severity: str = 'medium', finding_context: Optional[str] = None) -> None:
        """
        Add a finding to the buffer and auto-commit if batch size reached.
        
        Args:
            resource_id: ID of the resource with the finding
            finding_type: Type of finding (e.g., 'internet_exposure')
            severity: Severity level (low, medium, high, critical)
            finding_context: Optional JSON context for the finding
        """
        self.findings_buffer.append({
            'experiment_id': self.experiment_id,
            'resource_id': resource_id,
            'finding_type': finding_type,
            'severity': severity,
            'finding_context': finding_context,
        })
        
        # Auto-commit if batch reached
        if len(self.findings_buffer) >= self.batch_size:
            self.flush()
    
    def flush(self) -> int:
        """
        Persist all buffered findings to database.
        
        Returns:
            Number of findings persisted
        """
        if not self.findings_buffer:
            return 0
        
        try:
            with get_db_connection() as conn:
                for finding in self.findings_buffer:
                    conn.execute("""
                        INSERT INTO findings 
                        (experiment_id, resource_id, finding_type, severity, finding_context)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        finding['experiment_id'],
                        finding['resource_id'],
                        finding['finding_type'],
                        finding['severity'],
                        finding['finding_context'],
                    ))
                
                conn.commit()
                count = len(self.findings_buffer)
                self.findings_count += count
                self.findings_buffer = []
                return count
        
        except Exception as e:
            print(f"Warning: Failed to persist findings: {e}", file=sys.stderr)
            return 0
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - flush remaining findings."""
        self.flush()


class ScanCheckpoint:
    """Helper for tracking scan progress and resuming on timeout."""
    
    def __init__(self, experiment_id: str):
        """Initialize checkpoint tracker."""
        self.experiment_id = experiment_id
    
    def save_checkpoint(self, checkpoint_data: Dict) -> None:
        """
        Save checkpoint data to database.
        
        Args:
            checkpoint_data: Dict with 'resource_index', 'resources_scanned', etc.
        """
        try:
            with get_db_connection() as conn:
                # Store as JSON in a metadata table or findings notes
                # This is a simple approach - you could use a dedicated table
                checkpoint_str = str(checkpoint_data)
                conn.execute("""
                    INSERT OR REPLACE INTO experiment_metadata 
                    (experiment_id, key, value)
                    VALUES (?, 'scan_checkpoint', ?)
                """, (self.experiment_id, checkpoint_str))
                conn.commit()
        except Exception as e:
            print(f"Warning: Failed to save checkpoint: {e}", file=sys.stderr)
    
    def get_last_checkpoint(self) -> Dict:
        """
        Get last saved checkpoint.
        
        Returns:
            Checkpoint dict or empty dict if none exists
        """
        try:
            with get_db_connection() as conn:
                row = conn.execute("""
                    SELECT value FROM experiment_metadata
                    WHERE experiment_id = ? AND key = 'scan_checkpoint'
                    LIMIT 1
                """, (self.experiment_id,)).fetchone()
                
                if row:
                    import json
                    try:
                        return json.loads(row['value'])
                    except:
                        return {}
        except Exception as e:
            print(f"Warning: Failed to read checkpoint: {e}", file=sys.stderr)
        
        return {}


# Example usage in a scan loop:
"""
from findings_persister import FindingsPersister

# Create persister with auto-commit every 100 findings
persister = FindingsPersister(experiment_id='exp-001', batch_size=100)

for resource in resources:
    # Scan resource for internet exposure
    if is_internet_exposed(resource):
        persister.add_finding(
            resource_id=resource.id,
            finding_type='internet_exposure',
            severity='high',
            finding_context=json.dumps({
                'context_key': 'internet_exposure',
                'context_value': 'true',
                'reason': 'Firewall open to 0.0.0.0'
            })
        )

# Flush remaining findings on exit
persister.flush()

# If scan times out, partial findings are already persisted
# Diagram generation can then detect exposures from whatever was saved
"""
