"""Email parser for New Employee Onboarding requests.

This module parses emails with subject "NEW EMPLOYEE ORDER" and extracts
structured data including employee details, equipment list, budget code,
and priority.

Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8
"""

import re
from dataclasses import dataclass
from typing import List, Optional

from models import EmployeeData, EquipmentItem, OnboardingRequest


@dataclass
class ParseError:
    """Error result from parsing an invalid email.
    
    Attributes:
        message: Description of what failed during parsing
        field: The specific field that failed to parse (if applicable)
    """
    message: str
    field: Optional[str] = None


# Category mapping based on item patterns
CATEGORY_PATTERNS = {
    "Laptops": [r"laptop", r"macbook", r"notebook"],
    "Peripherals": [r"mouse", r"keyboard", r"headset", r"monitor", r"webcam", r"dock"],
    "Mobile Devices": [r"iphone", r"phone", r"ipad", r"tablet", r"android"],
    "Software": [r"license", r"office\s*365", r"software", r"subscription"],
}


def _derive_email_from_name(name: str) -> str:
    """Derive employee email from full name.
    
    Converts "John Doe" to "john.doe@anycompany.com"
    
    Args:
        name: Full name of the employee
        
    Returns:
        Derived email address
    """
    # Split name and take first and last parts
    parts = name.strip().split()
    if len(parts) >= 2:
        first_name = parts[0].lower()
        last_name = parts[-1].lower()
        return f"{first_name}.{last_name}@anycompany.com"
    elif len(parts) == 1:
        return f"{parts[0].lower()}@anycompany.com"
    return "unknown@anycompany.com"


def _categorize_item(item_name: str) -> str:
    """Determine the category of an equipment item based on its name.
    
    Args:
        item_name: Name of the equipment item
        
    Returns:
        Category string (Laptops, Peripherals, Mobile Devices, Software, or Other)
    """
    item_lower = item_name.lower()
    for category, patterns in CATEGORY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, item_lower):
                return category
    return "Other"


def _parse_employee_details(content: str) -> tuple[Optional[EmployeeData], Optional[ParseError]]:
    """Parse employee details section from email content.
    
    Args:
        content: Raw email content
        
    Returns:
        Tuple of (EmployeeData, None) on success or (None, ParseError) on failure
    """
    # Extract employee details section
    details_match = re.search(
        r"Employee\s+Details\s*:?\s*(.*?)(?=Equipment\s+Requested|Budget\s+Code|Priority|$)",
        content,
        re.IGNORECASE | re.DOTALL
    )
    
    if not details_match:
        return None, ParseError("Could not find Employee Details section", "employee_details")
    
    details_section = details_match.group(1)
    
    # Parse individual fields - use [ \t]* to match only spaces/tabs (not newlines)
    # and [^\n]* to match only within the same line
    # This prevents capturing content from the next line when value is empty
    name_match = re.search(r"Name[ \t]*:[ \t]*([^\n]*)", details_section, re.IGNORECASE)
    position_match = re.search(r"Position[ \t]*:[ \t]*([^\n]*)", details_section, re.IGNORECASE)
    department_match = re.search(r"Department[ \t]*:[ \t]*([^\n]*)", details_section, re.IGNORECASE)
    start_date_match = re.search(r"Start[ \t]+Date[ \t]*:[ \t]*([^\n]*)", details_section, re.IGNORECASE)
    manager_match = re.search(r"Manager[ \t]*:[ \t]*([^\n]*)", details_section, re.IGNORECASE)
    
    # Validate required fields
    if not name_match:
        return None, ParseError("Missing employee name", "name")
    if not position_match:
        return None, ParseError("Missing employee position", "position")
    if not department_match:
        return None, ParseError("Missing employee department", "department")
    if not start_date_match:
        return None, ParseError("Missing employee start date", "start_date")
    if not manager_match:
        return None, ParseError("Missing employee manager", "manager")
    
    name = name_match.group(1).strip()
    position = position_match.group(1).strip()
    department = department_match.group(1).strip()
    start_date = start_date_match.group(1).strip()
    manager = manager_match.group(1).strip()
    
    # Validate non-empty values
    if not name:
        return None, ParseError("Employee name is empty", "name")
    if not position:
        return None, ParseError("Employee position is empty", "position")
    if not department:
        return None, ParseError("Employee department is empty", "department")
    if not start_date:
        return None, ParseError("Employee start date is empty", "start_date")
    if not manager:
        return None, ParseError("Employee manager is empty", "manager")
    
    email = _derive_email_from_name(name)
    
    return EmployeeData(
        name=name,
        position=position,
        department=department,
        start_date=start_date,
        manager=manager,
        email=email
    ), None


def _parse_equipment_list(content: str) -> tuple[Optional[List[EquipmentItem]], Optional[ParseError]]:
    """Parse equipment list section from email content.
    
    Args:
        content: Raw email content
        
    Returns:
        Tuple of (List[EquipmentItem], None) on success or (None, ParseError) on failure
    """
    # Extract equipment section
    equipment_match = re.search(
        r"Equipment\s+Requested\s*:?\s*(.*?)(?=Budget\s+Code|Priority|Please\s+ensure|Best\s+regards|$)",
        content,
        re.IGNORECASE | re.DOTALL
    )
    
    if not equipment_match:
        return None, ParseError("Could not find Equipment Requested section", "equipment")
    
    equipment_section = equipment_match.group(1).strip()
    
    if not equipment_section:
        return None, ParseError("Equipment list is empty", "equipment")
    
    # Parse numbered items (e.g., "1. Item Name - Description")
    # Note: The separator " - " requires spaces on both sides to avoid splitting
    # item names that contain hyphens (e.g., "USB-C Dock")
    items: List[EquipmentItem] = []
    item_pattern = re.compile(r"^\s*\d+\.\s*(.+?)(?:\s+-\s+(.+))?$", re.MULTILINE)
    
    for match in item_pattern.finditer(equipment_section):
        item_name = match.group(1).strip()
        description = match.group(2).strip() if match.group(2) else ""
        
        if item_name:
            category = _categorize_item(item_name)
            items.append(EquipmentItem(
                name=item_name,
                description=description,
                category=category
            ))
    
    # If no numbered items found, try parsing line by line
    if not items:
        for line in equipment_section.split("\n"):
            line = line.strip()
            # Skip empty lines and lines that look like headers
            if not line or line.lower().startswith("equipment"):
                continue
            # Skip lines that are clearly not equipment items (parenthetical notes, etc.)
            if line.startswith("(") and line.endswith(")"):
                continue
            # Remove leading bullet points or dashes
            line = re.sub(r"^[-•*]\s*", "", line)
            if line:
                # Try to split on " - " for name and description
                parts = line.split(" - ", 1)
                item_name = parts[0].strip()
                description = parts[1].strip() if len(parts) > 1 else ""
                if item_name:
                    category = _categorize_item(item_name)
                    items.append(EquipmentItem(
                        name=item_name,
                        description=description,
                        category=category
                    ))
    
    if not items:
        return None, ParseError("No equipment items found in list", "equipment")
    
    return items, None


def _parse_budget_code(content: str) -> tuple[str, Optional[ParseError]]:
    """Parse budget code from email content.
    
    Args:
        content: Raw email content
        
    Returns:
        Tuple of (budget_code, None) on success or ("", ParseError) on failure
    """
    # Use [ \t]* to match only spaces/tabs (not newlines) after the colon
    # and [^\n]* to match only within the same line
    budget_match = re.search(r"Budget[ \t]+Code[ \t]*:[ \t]*([^\n]*)", content, re.IGNORECASE)
    
    if not budget_match:
        return "", ParseError("Missing budget code", "budget_code")
    
    budget_code = budget_match.group(1).strip()
    
    if not budget_code:
        return "", ParseError("Budget code is empty", "budget_code")
    
    return budget_code, None


def _parse_priority(content: str) -> str:
    """Parse priority from email content.
    
    Args:
        content: Raw email content
        
    Returns:
        Priority string (High, Medium, Low) or "Medium" as default
    """
    priority_match = re.search(r"Priority\s*:\s*(.+?)(?:\n|$)", content, re.IGNORECASE)
    
    if priority_match:
        priority = priority_match.group(1).strip().capitalize()
        if priority in ["High", "Medium", "Low"]:
            return priority
    
    return "Medium"  # Default priority


def parse_onboarding_email(subject: str, content: str) -> OnboardingRequest | ParseError:
    """Parse email content into structured OnboardingRequest.
    
    This function extracts employee details, equipment list, budget code,
    and priority from a new employee order email.
    
    Args:
        subject: Email subject line
        content: Email body content
        
    Returns:
        OnboardingRequest on success, ParseError on failure
        
    Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8
    """
    # Validate inputs
    if not content or not content.strip():
        return ParseError("Email content is empty", "content")
    
    # Parse employee details
    employee, error = _parse_employee_details(content)
    if error:
        return error
    
    # Parse equipment list
    equipment, error = _parse_equipment_list(content)
    if error:
        return error
    
    # Parse budget code
    budget_code, error = _parse_budget_code(content)
    if error:
        return error
    
    # Parse priority (optional, defaults to Medium)
    priority = _parse_priority(content)
    
    return OnboardingRequest(
        employee=employee,
        equipment=equipment,
        budget_code=budget_code,
        priority=priority,
        raw_email_content=content
    )
