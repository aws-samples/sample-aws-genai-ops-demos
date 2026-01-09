"""Base detector interface."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Dict, Any


class BaseDetector(ABC):
    """Base class for service detectors."""

    @abstractmethod
    def can_analyze(self, file_path: Path) -> bool:
        """Check if this detector can analyze the given file."""
        pass

    @abstractmethod
    def analyze(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Analyze file content and return findings."""
        pass
    
    # False Positive Mitigation Methods (shared across all detectors)
    
    def _is_likely_false_positive(self, content: str, match_start: int, match_end: int) -> bool:
        """Check if match is likely a false positive using context-aware detection.
        
        Filters out patterns that appear in:
        - Validation error messages with keywords like "format", "pattern", "e.g."
        - Comments (# or //)
        - Docstrings (triple quotes)
        
        This is Pass 1 filtering - simple and effective for obvious false positives.
        Does NOT filter based on API call proximity to avoid missing legitimate config constants.
        
        Available to all detectors to ensure consistent false positive handling.
        """
        # Check if in string with validation keywords
        if self._is_in_string_with_validation_context(content, match_start, match_end):
            return True
        
        # Check if in comment
        if self._is_in_comment(content, match_start):
            return True
        
        # Check if in docstring
        if self._is_in_docstring(content, match_start):
            return True
        
        return False
    
    def _is_in_string_with_validation_context(self, content: str, match_start: int, match_end: int) -> bool:
        """Check if match is in a string literal with validation keywords."""
        # Get the line containing the match
        line_start = content.rfind('\n', 0, match_start) + 1
        line_end = content.find('\n', match_end)
        if line_end == -1:
            line_end = len(content)
        line = content[line_start:line_end]
        
        # Get text before match on same line
        before_match = content[line_start:match_start]
        
        # Count quotes to determine if we're in a string (odd number = inside string)
        single_quotes = before_match.count("'") - before_match.count("\\'")
        double_quotes = before_match.count('"') - before_match.count('\\"')
        backticks = before_match.count('`')
        
        in_string = (single_quotes % 2 == 1) or (double_quotes % 2 == 1) or (backticks % 2 == 1)
        
        if not in_string:
            return False
        
        # Check if line contains validation keywords
        validation_keywords = [
            'error', 'validate', 'validation', 'pattern', 'format', 
            'example', 'e.g.', 'must follow', 'invalid', 'required',
            'placeholder', 'unsupported', 'deprecated'
        ]
        line_lower = line.lower()
        return any(kw in line_lower for kw in validation_keywords)
    
    def _is_in_comment(self, content: str, match_start: int) -> bool:
        """Check if match is in a comment."""
        # Get the line containing the match
        line_start = content.rfind('\n', 0, match_start) + 1
        before_match = content[line_start:match_start]
        
        # Check for comment markers before the match
        return '//' in before_match or '#' in before_match
    
    def _is_in_docstring(self, content: str, match_start: int) -> bool:
        """Check if match is in a Python docstring."""
        # Look backwards for triple quotes
        before = content[:match_start]
        
        # Count triple quotes before match (odd number = inside docstring)
        triple_double = before.count('"""')
        triple_single = before.count("'" * 3)
        
        return (triple_double % 2 == 1) or (triple_single % 2 == 1)
