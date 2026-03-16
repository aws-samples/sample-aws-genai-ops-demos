"""Data models for the New Employee Onboarding Automation workflow.

This module defines the core data structures used throughout the onboarding
automation process, including employee information, equipment requests,
and workflow state tracking.

Requirements: 1.2-1.7, 4.6
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class EmployeeData:
    """Parsed employee information from onboarding email.
    
    Attributes:
        name: Full name of the new employee
        position: Job title/role of the employee
        department: Department the employee will join
        start_date: Employee's start date (string format)
        manager: Name of the employee's manager or team
        email: Derived email address (firstname.lastname@anycompany.com)
    
    Requirements: 1.2, 1.3, 1.4, 1.5
    """
    name: str
    position: str
    department: str
    start_date: str
    manager: str
    email: str


@dataclass
class EquipmentItem:
    """Single equipment item from an onboarding request.
    
    Attributes:
        name: Name of the equipment item
        description: Description or specifications of the item
        category: Category classification (Laptops, Peripherals, Mobile Devices, Software)
    
    Requirements: 1.6
    """
    name: str
    description: str
    category: str = ""


@dataclass
class OnboardingRequest:
    """Complete parsed onboarding request from email.
    
    Attributes:
        employee: Parsed employee information
        equipment: List of requested equipment items
        budget_code: Budget code for procurement
        priority: Request priority level (High, Medium, Low)
        raw_email_content: Original email content for reference
        requester_email: Email address of the person who sent the request
    
    Requirements: 1.2-1.7
    """
    employee: EmployeeData
    equipment: List[EquipmentItem]
    budget_code: str
    priority: str
    raw_email_content: str = ""
    requester_email: str = ""


@dataclass
class WorkflowState:
    """Tracks state throughout the onboarding workflow execution.
    
    This class maintains the current state of the workflow, including
    which items are available, which need procurement, and the overall
    status of the automation process.
    
    Attributes:
        request: The original onboarding request being processed
        ticket_id: ITSM ticket ID once created
        ticket_title: ITSM ticket title (used for searching if ID extraction fails)
        items_in_stock: List of equipment items available in inventory
        items_need_procurement: List of items requiring purchase orders
        purchase_orders: List of created purchase order IDs
        allocated_items: List of items successfully allocated to employee
        status: Current workflow status (pending, in_progress, completed, failed)
        error: Error message if workflow failed
    
    Requirements: 4.6
    """
    request: OnboardingRequest
    ticket_id: Optional[str] = None
    ticket_title: Optional[str] = None
    items_in_stock: List[str] = field(default_factory=list)
    items_need_procurement: List[str] = field(default_factory=list)
    purchase_orders: List[str] = field(default_factory=list)
    allocated_items: List[str] = field(default_factory=list)
    status: str = "pending"
    error: Optional[str] = None
