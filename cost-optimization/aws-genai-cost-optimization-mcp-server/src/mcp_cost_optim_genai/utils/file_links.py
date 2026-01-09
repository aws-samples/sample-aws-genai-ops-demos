"""Utility for creating clickable file links."""

from pathlib import Path
from typing import Optional


def create_file_link(file_path: str, line: Optional[int] = None, project_root: Optional[str] = None) -> str:
    """Create a clickable kiro:// link for Kiro IDE.
    
    Args:
        file_path: Path to file (absolute or relative)
        line: Optional line number
        project_root: Optional project root for resolving relative paths
        
    Returns:
        Markdown link: [filename:line](kiro://file/absolute/path:line)
    """
    try:
        # Convert to absolute path
        path = Path(file_path)
        
        # If path is already absolute, use it directly
        if path.is_absolute():
            path = path.resolve()
        elif project_root:
            # For relative paths, join with project root
            # But first check if file_path already contains project_root
            project_root_path = Path(project_root).resolve()
            
            # Try to resolve relative to project root
            full_path = project_root_path / file_path
            if full_path.exists():
                path = full_path.resolve()
            else:
                # Fallback: just resolve the path as-is
                path = path.resolve()
        else:
            # No project root, just resolve
            path = path.resolve()
        
        # Create kiro:// URI (Kiro IDE format)
        file_uri = f"kiro://file/{path.as_posix()}"
        if line:
            file_uri += f":{line}"
        
        # Create display text
        if line:
            display = f"{path.name}:{line}"
        else:
            display = path.name
        
        return f"[{display}]({file_uri})"
        
    except Exception as e:
        # Fallback if path resolution fails
        if line:
            return f"{file_path}:{line}"
        return file_path


def add_file_links_to_findings(findings: list, project_root: Optional[str] = None) -> list:
    """Add file_link field to all findings.
    
    Args:
        findings: List of finding dictionaries
        project_root: Optional project root for resolving relative paths
        
    Returns:
        Modified findings list with file_link field added
    """
    for finding in findings:
        if finding.get('file'):
            finding['file_link'] = create_file_link(
                finding['file'],
                finding.get('line'),
                project_root
            )
    
    return findings
