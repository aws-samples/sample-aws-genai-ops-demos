"""Shared presentation guidelines for AI when displaying findings.

These guidelines ensure consistent formatting across all tools and outputs.
"""

# Presentation guidelines that should be included in all AI-facing outputs
PRESENTATION_GUIDELINES = {
    "CLICKABLE_FILE_LINKS": {
        "CRITICAL": "When presenting findings to the user, ALWAYS use the 'file_link' field for clickable navigation",
        "format": "Each finding has a 'file_link' field with markdown format: [filename:line](kiro://file/path:line)",
        "example": "Instead of showing 'file: path/to/file.py, line: 42', use the file_link: finding['file_link']",
        "why": "The file_link field creates clickable links that jump directly to the code location in Kiro IDE",
        "DO_NOT": "Do not create your own file references - always use the provided file_link field"
    },
    "TABLE_FORMATTING": {
        "use": "Use markdown tables for tabular data presentation",
        "ensure": "File_link fields remain clickable when used in tables",
        "example": "| Optimization | Current | Optimized | Savings |\n|--------------|---------|-----------|---------|",
        "why": "Markdown tables now render properly in Kiro IDE and maintain clickable links",
        "formatting_tips": [
            "Use standard markdown table syntax",
            "Include header separator row with dashes",
            "Align columns for readability",
            "File links work correctly within table cells"
        ]
    }
}

# Short summary for tool descriptions
PRESENTATION_SUMMARY = """CRITICAL - When presenting findings to users:
- ALWAYS use the 'file_link' field for clickable navigation (not plain text)
- USE markdown tables for tabular data presentation
- File links work correctly within table cells
- Example format:
| Function | Tokens | Location |
|----------|--------|----------|
| create_agent_runtime | 59 | [deploy_agent_core.py:360](kiro://...) |
| send_error_message | 33 | [handler.py:482](kiro://...) |
"""
