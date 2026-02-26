"""Ticket formatting utilities for ITSM ticket creation.

This module provides functions to format ticket titles and descriptions
for new employee equipment requests in the ITSM portal.

Requirements: 2.3, 2.4
"""

from models import OnboardingRequest, EmployeeData, EquipmentItem
from typing import List


def format_ticket_title(employee_name: str, role: str) -> str:
    """Format the ITSM ticket title for a hardware request.
    
    Creates a title in the format:
    "Hardware Request - [Employee Name] - [Role] Equipment Setup"
    
    Args:
        employee_name: Full name of the new employee
        role: Job title/position of the employee
        
    Returns:
        Formatted ticket title string
        
    Requirements: 2.3
    """
    return f"Hardware Request - {employee_name} - {role} Equipment Setup"


def format_ticket_description(request: OnboardingRequest) -> str:
    """Format the ITSM ticket description with employee details and equipment list.
    
    Creates a comprehensive description including:
    - Employee information (name, position, department, start date, manager)
    - Complete list of requested equipment items
    - Budget code and priority
    
    Args:
        request: The complete onboarding request with employee and equipment data
        
    Returns:
        Formatted ticket description string
        
    Requirements: 2.4
    """
    employee = request.employee
    
    # Build equipment list section
    equipment_lines = []
    for i, item in enumerate(request.equipment, 1):
        if item.description:
            equipment_lines.append(f"  {i}. {item.name} - {item.description}")
        else:
            equipment_lines.append(f"  {i}. {item.name}")
    equipment_section = "\n".join(equipment_lines)
    
    description = f"""New Employee Equipment Request

Employee Information:
  Name: {employee.name}
  Position: {employee.position}
  Department: {employee.department}
  Start Date: {employee.start_date}
  Manager: {employee.manager}
  Email: {employee.email}

Requested Equipment:
{equipment_section}

Budget Code: {request.budget_code}
Priority: {request.priority}

Please ensure all equipment is ready and configured before the employee's start date."""
    
    return description
