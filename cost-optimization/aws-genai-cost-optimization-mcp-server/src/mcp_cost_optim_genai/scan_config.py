"""Smart scanning configuration and utilities."""

from pathlib import Path
from typing import Set, List
import os

# Default directories to skip (common build/dependency folders)
DEFAULT_SKIP_DIRS = {
    # Python
    "__pycache__", ".pytest_cache", ".mypy_cache", ".tox", ".nox",
    "venv", ".venv", "env", ".env", "virtualenv",
    "site-packages", "dist", "build", "*.egg-info",
    
    # Node/JavaScript
    "node_modules", ".npm", ".yarn", ".pnp",
    "bower_components", "jspm_packages",
    
    # Build outputs
    "dist", "build", "out", "target", "bin", "obj",
    ".next", ".nuxt", ".output", ".vercel", ".netlify",
    
    # CDK/Terraform
    "cdk.out", ".cdk.staging", "cdk_output",
    ".terraform", "terraform.tfstate.d",
    
    # Version control
    ".git", ".svn", ".hg", ".bzr",
    
    # IDEs
    ".vscode", ".idea", ".eclipse", ".settings",
    
    # OS
    ".DS_Store", "Thumbs.db",
    
    # Logs and temp
    "logs", "tmp", "temp", ".cache",
    
    # Documentation builds
    "docs/_build", "site", "_site",
    
    # Test directories
    "tests", "test", "__tests__", "spec", "specs",
}

# File extensions to scan
SCANNABLE_EXTENSIONS = {".py", ".ts", ".js", ".tsx", ".jsx"}

# Maximum file size to scan (in bytes) - skip very large files
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


def should_skip_directory(dir_path: Path, skip_dirs: Set[str] = None) -> bool:
    """Check if a directory should be skipped during scanning.
    
    Args:
        dir_path: Directory path to check
        skip_dirs: Custom set of directory names to skip (uses DEFAULT_SKIP_DIRS if None)
        
    Returns:
        True if directory should be skipped
    """
    if skip_dirs is None:
        skip_dirs = DEFAULT_SKIP_DIRS
    
    dir_name = dir_path.name
    
    # Check exact matches
    if dir_name in skip_dirs:
        return True
    
    # Check patterns (e.g., *.egg-info)
    for pattern in skip_dirs:
        if "*" in pattern:
            pattern_prefix = pattern.replace("*", "")
            if dir_name.endswith(pattern_prefix):
                return True
    
    # Skip hidden directories (starting with .)
    if dir_name.startswith(".") and dir_name not in {".github", ".gitlab"}:
        return True
    
    return False


def should_scan_file(file_path: Path, max_size: int = MAX_FILE_SIZE) -> bool:
    """Check if a file should be scanned.
    
    Args:
        file_path: File path to check
        max_size: Maximum file size in bytes
        
    Returns:
        True if file should be scanned
    """
    # Check extension
    if file_path.suffix not in SCANNABLE_EXTENSIONS:
        return False
    
    # Skip test files (test-*.* or test_*.*)
    file_name = file_path.name.lower()
    if file_name.startswith("test-") or file_name.startswith("test_"):
        return False
    
    # Skip compiled JavaScript files when TypeScript source exists
    # Example: skip "runtime-stack.js" if "runtime-stack.ts" exists
    if file_path.suffix == ".js":
        ts_equivalent = file_path.with_suffix(".ts")
        if ts_equivalent.exists():
            return False  # Skip JS file, scan TS source instead
    
    # Skip compiled JSX files when TSX source exists
    if file_path.suffix == ".jsx":
        tsx_equivalent = file_path.with_suffix(".tsx")
        if tsx_equivalent.exists():
            return False  # Skip JSX file, scan TSX source instead
    
    # Check file size
    try:
        if file_path.stat().st_size > max_size:
            return False
    except OSError:
        return False
    
    return True


def find_scannable_files(
    project_path: Path,
    skip_dirs: Set[str] = None,
    max_files: int = None
) -> List[Path]:
    """Find all scannable files in a project using smart filtering.
    
    This function walks the directory tree efficiently, skipping entire
    directories that shouldn't be scanned.
    
    Args:
        project_path: Root directory to scan
        skip_dirs: Custom set of directory names to skip
        max_files: Maximum number of files to return (None for unlimited)
        
    Returns:
        List of file paths to scan
    """
    if skip_dirs is None:
        skip_dirs = DEFAULT_SKIP_DIRS
    
    scannable_files = []
    
    # Use os.walk for efficient directory traversal with pruning
    for root, dirs, files in os.walk(project_path):
        root_path = Path(root)
        
        # Prune directories in-place (modifies dirs list to skip subdirectories)
        dirs[:] = [
            d for d in dirs 
            if not should_skip_directory(root_path / d, skip_dirs)
        ]
        
        # Check files in current directory
        for file_name in files:
            file_path = root_path / file_name
            
            if should_scan_file(file_path):
                scannable_files.append(file_path)
                
                # Stop if we've hit the max
                if max_files and len(scannable_files) >= max_files:
                    return scannable_files
    
    return scannable_files


def estimate_scan_size(project_path: Path, skip_dirs: Set[str] = None) -> dict:
    """Estimate the size of a scan without actually scanning.
    
    Useful for warning users about large scans.
    
    Args:
        project_path: Root directory to scan
        skip_dirs: Custom set of directory names to skip
        
    Returns:
        Dictionary with scan statistics
    """
    if skip_dirs is None:
        skip_dirs = DEFAULT_SKIP_DIRS
    
    file_count = 0
    total_size = 0
    skipped_dirs = 0
    
    for root, dirs, files in os.walk(project_path):
        root_path = Path(root)
        
        # Count skipped directories
        original_dir_count = len(dirs)
        dirs[:] = [
            d for d in dirs 
            if not should_skip_directory(root_path / d, skip_dirs)
        ]
        skipped_dirs += original_dir_count - len(dirs)
        
        # Count scannable files
        for file_name in files:
            file_path = root_path / file_name
            if should_scan_file(file_path):
                file_count += 1
                try:
                    total_size += file_path.stat().st_size
                except OSError:
                    pass
    
    return {
        "file_count": file_count,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "skipped_directories": skipped_dirs,
        "estimated_time_seconds": file_count * 0.1  # Rough estimate: 100ms per file
    }
