"""Onboarding Orchestrator - JSON-Driven Workflow Execution.

This module provides the main orchestration logic that coordinates all phases
of the onboarding workflow using dynamic JSON-based workflow definitions.

All browser actions are defined in workflows/new_employee_onboarding_actions.json
and executed dynamically - no hardcoded nova.act() calls.

Requirements: 2.1-2.10, 3.1-3.6, 4.1-4.6, 5.1-5.11, 6.1-6.10, 7.1-7.3, 8.1-8.8, 9.4, 9.5, 9.6
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from models import OnboardingRequest, WorkflowState
from onboarding_config import OnboardingConfig
from browser_actions import BrowserActions, ScenarioResult
from ticket_formatter import format_ticket_title, format_ticket_description
from ses_notifier import SESNotifier, NotificationData

if TYPE_CHECKING:
    from nova_act import NovaAct

logger = logging.getLogger(__name__)


# Item category to vendor mapping for procurement
# NOTE: These must match actual vendors in the procurement portal dropdown:
# NetworkTech Systems, CloudServices Ltd, VideoTech, SoftwarePro Inc,
# TabletCorp, SecurityTech Corp, PeripheralTech, DisplayCorp, AudioTech, TechCorp Solutions
VENDOR_MAPPING = {
    "Laptops": "TechCorp Solutions",
    "Desktops": "TechCorp Solutions",
    "Monitors": "DisplayCorp",
    "Peripherals": "PeripheralTech",
    "Mobile Devices": "TabletCorp",
    "Networking": "NetworkTech Systems",
    "Software": "SoftwarePro Inc",
    "Audio Equipment": "AudioTech",
    "Video Equipment": "VideoTech",
    "Security": "SecurityTech Corp",
    "Cloud Services": "CloudServices Ltd",
    "Other": "TechCorp Solutions",
}

# Default prices by category
DEFAULT_PRICES = {
    "Laptops": 2499.00,
    "Desktops": 1299.00,
    "Monitors": 599.00,
    "Peripherals": 199.00,
    "Mobile Devices": 799.00,
    "Networking": 899.00,
    "Software": 299.00,
    "Audio Equipment": 199.00,
    "Video Equipment": 249.00,
    "Security": 499.00,
    "Cloud Services": 99.00,
    "Other": 499.00,
}


@dataclass
class ProcessedEmailTracker:
    """Tracks processed emails to prevent duplicate handling."""
    processed_ids: Set[str] = field(default_factory=set)
    
    def is_processed(self, email_id: str) -> bool:
        return email_id in self.processed_ids
    
    def mark_processed(self, email_id: str) -> None:
        self.processed_ids.add(email_id)


class OnboardingOrchestrator:
    """Orchestrates the complete onboarding workflow using JSON definitions.
    
    This class coordinates all phases of the new employee onboarding process
    by loading workflow definitions from JSON and executing them dynamically.
    
    Phases:
    1. Create ITSM ticket
    2. Update ticket to In Progress
    3. Check inventory availability
    4. Process procurement for missing items (4a-4d)
    5. Add delivered items to inventory
    6. Allocate items to employee
    7. Resolve ticket
    8. Send email notification (API only)
    """
    
    def __init__(
        self,
        config: OnboardingConfig,
        nova: Optional["NovaAct"] = None,
        skip_ses_init: bool = False,
        step_by_step: bool = False
    ):
        """Initialize the orchestrator.
        
        Args:
            config: Configuration for the onboarding automation
            nova: Optional NovaAct instance for browser automation
            skip_ses_init: If True, skip SES client initialization
            step_by_step: If True, require user confirmation after each action
        """
        self.config = config
        self.nova = nova
        self.state: Optional[WorkflowState] = None
        self.email_tracker = ProcessedEmailTracker()
        self._skip_ses = skip_ses_init
        self.step_by_step = step_by_step
        
        # Initialize dynamic browser actions
        self.browser_actions = BrowserActions(
            nova=nova,
            config={
                "itsm_url": config.itsm_url,
                "inventory_url": config.inventory_url,
                "procurement_url": config.procurement_url
            },
            step_by_step=step_by_step
        )
        
        # Initialize SES notifier
        if not skip_ses_init:
            self.ses_notifier = SESNotifier(
                config.aws_region,
                config.ses_sender_email,
                use_recipient_as_sender=config.ses_use_recipient_as_sender
            )
        else:
            self.ses_notifier = None

    def execute(
        self, 
        request: OnboardingRequest, 
        email_id: Optional[str] = None
    ) -> WorkflowState:
        """Execute the complete onboarding workflow.
        
        Runs all phases using the JSON workflow definitions.
        Processes ALL equipment items in the request, not just the first one.
        
        Workflow:
        1. Create ITSM ticket (once)
        2. Update ticket to In Progress (once)
        3-6. For EACH equipment item:
             - Check inventory stock
             - Procurement if needed (create PO, submit, approve, receive)
             - Add to inventory if ordered
             - Allocate to employee
        7. Resolve ticket (once)
        8. Send notification (once)
        
        Args:
            request: The parsed onboarding request to process
            email_id: Optional unique identifier for duplicate prevention
        
        Returns:
            WorkflowState containing the final state of the workflow
        """
        # Check for duplicate email processing
        if email_id and self.email_tracker.is_processed(email_id):
            logger.warning(f"Email {email_id} has already been processed, skipping")
            state = WorkflowState(request=request)
            state.status = "skipped"
            state.error = "Duplicate email - already processed"
            return state
        
        # Initialize workflow state
        self.state = WorkflowState(request=request)
        self.state.status = "in_progress"
        
        start_time = datetime.now()
        logger.info(f"Starting onboarding workflow for {request.employee.name} at {start_time}")
        logger.info(f"Equipment items to process: {len(request.equipment)}")
        for i, item in enumerate(request.equipment, 1):
            logger.info(f"  {i}. {item.name} (Category: {item.category})")
        
        try:
            # Build base context for workflow execution
            context = self._build_workflow_context(request)
            
            # Log the complete workflow context being passed to automation
            self._log_workflow_context(context, request)
            
            # ============================================================
            # PHASE 1: Create ITSM Ticket (once)
            # ============================================================
            logger.info("\n" + "=" * 70)
            logger.info("PHASE 1: Creating ITSM Ticket")
            logger.info("=" * 70)
            
            phase1_result = self.browser_actions.run_workflow(
                "itsm_create_ticket", 
                context
            )
            if phase1_result.status == "failed":
                raise Exception("Failed to create ITSM ticket")
            
            # Capture ticket_id
            if "ticket_id" in phase1_result.outputs:
                self.state.ticket_id = phase1_result.outputs["ticket_id"]
            
            # ============================================================
            # PHASE 2: Update Ticket to In Progress (once)
            # ============================================================
            logger.info("\n" + "=" * 70)
            logger.info("PHASE 2: Updating Ticket to In Progress")
            logger.info("=" * 70)
            
            phase2_context = {
                **context,
                "params": {**context.get("params", {}), "new_status": "In Progress"}
            }
            phase2_result = self.browser_actions.run_workflow(
                "itsm_update_status",
                phase2_context
            )
            
            # ============================================================
            # PHASES 3-6: Process EACH Equipment Item
            # ============================================================
            logger.info("\n" + "=" * 70)
            logger.info(f"PHASES 3-6: Processing {len(request.equipment)} Equipment Items")
            logger.info("=" * 70)
            
            equipment_results = self._process_all_equipment(request, context)
            
            # Update state with allocated items
            self.state.allocated_items = equipment_results.get("allocated_items", [])
            self.state.purchase_orders = equipment_results.get("purchase_orders", [])
            
            # ============================================================
            # PHASE 7: Resolve Ticket (once)
            # ============================================================
            logger.info("\n" + "=" * 70)
            logger.info("PHASE 7: Resolving ITSM Ticket")
            logger.info("=" * 70)
            
            phase7_context = {
                **context,
                "params": {**context.get("params", {}), "new_status": "Resolved"}
            }
            phase7_result = self.browser_actions.run_workflow(
                "itsm_update_status",
                phase7_context
            )
            
            # ============================================================
            # PHASE 8: Send Notification (API only)
            # ============================================================
            logger.info("\n" + "=" * 70)
            logger.info("PHASE 8: Sending Notification")
            logger.info("=" * 70)
            self._send_notification()
            
            # Mark workflow as completed
            self.state.status = "completed"
            
            # Mark email as processed
            if email_id:
                self.email_tracker.mark_processed(email_id)
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            logger.info(
                f"Onboarding workflow completed for {request.employee.name} "
                f"in {duration:.1f} seconds"
            )
            
        except Exception as e:
            self.state.status = "failed"
            self.state.error = str(e)
            logger.error(f"Workflow failed: {e}", exc_info=True)
        
        return self.state

    def _process_all_equipment(
        self, 
        request: OnboardingRequest, 
        base_context: Dict
    ) -> Dict:
        """Process all equipment items in the request.
        
        For each item:
        1. Search inventory and check stock level
        2. If stock insufficient, run procurement cycle
        3. Allocate item to employee
        
        Args:
            request: The onboarding request with equipment list
            base_context: Base context with employee, ticket info
            
        Returns:
            Dict with allocated_items, purchase_orders, and results
        """
        results = {
            "allocated_items": [],
            "purchase_orders": [],
            "items_in_stock": [],
            "items_ordered": [],
            "items_failed": []
        }
        
        for idx, item in enumerate(request.equipment, 1):
            logger.info("\n" + "#" * 60)
            logger.info(f"# ITEM {idx}/{len(request.equipment)}: {item.name}")
            logger.info(f"# Category: {item.category}")
            logger.info("#" * 60)
            
            # Reset stock_level for this item
            self.browser_actions.outputs.pop("stock_level", None)
            
            # Get item-specific parameters
            vendor = VENDOR_MAPPING.get(item.category, "TechCorp Solutions")
            unit_price = DEFAULT_PRICES.get(item.category, 499.00)
            
            # Build item-specific context
            item_context = {
                **base_context,
                "params": {
                    **base_context.get("params", {}),
                    "item_name": item.name,
                    "quantity": 1,
                    "vendor": vendor,
                    "unit_price": unit_price,
                    "category": item.category,
                    "manufacturer": self._get_manufacturer(item.category),
                    "model": f"{item.name} Standard",
                    "notes": f"Onboarding for {request.employee.name}"
                }
            }
            
            # ----------------------------------------
            # PHASE 3: Check Inventory Stock
            # ----------------------------------------
            logger.info(f"\n[PHASE 3] Checking inventory for: {item.name}")
            
            inv_result = self.browser_actions.run_workflow(
                "inventory_search",
                item_context
            )
            
            # Get stock level
            stock_level_str = self.browser_actions.outputs.get("stock_level", "0")
            try:
                stock_level = int(stock_level_str) if stock_level_str else 0
            except (ValueError, TypeError):
                stock_level = 0
            
            logger.info(f"  Stock level for {item.name}: {stock_level}")
            
            # ----------------------------------------
            # PHASES 4a-4d & 5: Procurement (if needed)
            # ----------------------------------------
            if stock_level < 1:
                logger.info(f"\n[PHASE 4] Stock insufficient - starting procurement for: {item.name}")
                results["items_ordered"].append(item.name)
                
                # Phase 4a: Create PO
                logger.info(f"  [4a] Creating Purchase Order...")
                po_result = self.browser_actions.run_workflow(
                    "procurement_create_po",
                    item_context
                )
                
                # Capture PO ID
                po_id = self.browser_actions.outputs.get("po_id")
                if po_id:
                    results["purchase_orders"].append(po_id)
                
                # Phase 4b: Submit PO
                logger.info(f"  [4b] Submitting Purchase Order...")
                self.browser_actions.run_workflow(
                    "procurement_submit_po",
                    item_context
                )
                
                # Phase 4c: Approve PO
                logger.info(f"  [4c] Approving Purchase Order...")
                self.browser_actions.run_workflow(
                    "procurement_approve_po",
                    item_context
                )
                
                # Phase 4d: Receive Delivery
                logger.info(f"  [4d] Receiving Delivery...")
                self.browser_actions.run_workflow(
                    "procurement_receive",
                    item_context
                )
                
                # Phase 5: Add to Inventory
                logger.info(f"\n[PHASE 5] Adding {item.name} to inventory...")
                add_context = {
                    **item_context,
                    "params": {
                        **item_context.get("params", {}),
                        "stock_level": self.config.bulk_order_quantity,
                        "unit_cost": unit_price,
                        "location": "IT Storage Room A"
                    }
                }
                self.browser_actions.run_workflow(
                    "inventory_add_item",
                    add_context
                )
            else:
                logger.info(f"\n[PHASE 4-5] SKIPPED - Stock sufficient ({stock_level} available)")
                results["items_in_stock"].append(item.name)
            
            # ----------------------------------------
            # PHASE 6: Allocate Item to Employee
            # ----------------------------------------
            logger.info(f"\n[PHASE 6] Allocating {item.name} to {request.employee.name}...")
            
            alloc_result = self.browser_actions.run_workflow(
                "inventory_allocate",
                item_context
            )
            
            if alloc_result.status == "success":
                results["allocated_items"].append(item.name)
                logger.info(f"  [OK] {item.name} allocated successfully")
            else:
                results["items_failed"].append(item.name)
                logger.warning(f"  [WARN] Failed to allocate {item.name}")
        
        # Summary
        logger.info("\n" + "=" * 60)
        logger.info("EQUIPMENT PROCESSING SUMMARY")
        logger.info("=" * 60)
        logger.info(f"  Total items: {len(request.equipment)}")
        logger.info(f"  In stock: {len(results['items_in_stock'])} - {results['items_in_stock']}")
        logger.info(f"  Ordered: {len(results['items_ordered'])} - {results['items_ordered']}")
        logger.info(f"  Allocated: {len(results['allocated_items'])} - {results['allocated_items']}")
        logger.info(f"  Failed: {len(results['items_failed'])} - {results['items_failed']}")
        logger.info(f"  PO IDs: {results['purchase_orders']}")
        
        return results

    def _build_workflow_context(self, request: OnboardingRequest) -> Dict:
        """Build context dict for workflow execution.
        
        Args:
            request: The onboarding request
            
        Returns:
            Context dict with all variables needed for workflow
        """
        employee = request.employee
        equipment = request.equipment
        first_item = equipment[0] if equipment else None
        
        # Format ticket details
        title = format_ticket_title(employee.name, employee.position)
        description = format_ticket_description(request)
        
        # Store title for state
        self.state.ticket_title = title
        
        # Get item details
        item_name = first_item.name if first_item else "Laptop"
        category = first_item.category if first_item else "Laptops"
        vendor = VENDOR_MAPPING.get(category, "TechCorp Solutions")
        unit_price = DEFAULT_PRICES.get(category, 499.00)
        
        return {
            "employee": {
                "name": employee.name,
                "email": employee.email,
                "position": employee.position,
                "department": employee.department,
                "start_date": employee.start_date,
                "manager": employee.manager
            },
            "request": {
                "budget_code": request.budget_code,
                "priority": request.priority
            },
            "ticket": {
                "title": title,
                "description": description,
                "category": "Hardware Request",
                "priority": request.priority,
                "title_prefix": title[:25]
            },
            "params": {
                "item_name": item_name,
                "quantity": 1,
                "vendor": vendor,
                "unit_price": unit_price,
                "category": category,
                "manufacturer": self._get_manufacturer(category),
                "model": f"{item_name} Standard",
                "stock_level": self.config.bulk_order_quantity,
                "unit_cost": unit_price,
                "location": "IT Storage Room A",
                "notes": f"Onboarding for {employee.name}",
                "new_status": "In Progress"
            }
        }
    
    def _get_manufacturer(self, category: str) -> str:
        """Get manufacturer based on category."""
        manufacturer_map = {
            "Laptops": "Dell",
            "Monitors": "LG",
            "Peripherals": "Logitech",
            "Mobile Devices": "Apple",
            "Software": "Microsoft",
            "Other": "Generic"
        }
        return manufacturer_map.get(category, "Generic")
    
    def _log_workflow_context(self, context: Dict, request: OnboardingRequest) -> None:
        """Log the complete workflow context for debugging.
        
        Args:
            context: The built workflow context
            request: The original onboarding request
        """
        logger.info("=" * 70)
        logger.info("WORKFLOW CONTEXT - Parameters passed to browser automation")
        logger.info("=" * 70)
        
        # Employee data
        emp = context.get("employee", {})
        logger.info("EMPLOYEE DATA:")
        logger.info(f"  Name: {emp.get('name')}")
        logger.info(f"  Email: {emp.get('email')}")
        logger.info(f"  Position: {emp.get('position')}")
        logger.info(f"  Department: {emp.get('department')}")
        logger.info(f"  Start Date: {emp.get('start_date')}")
        logger.info(f"  Manager: {emp.get('manager')}")
        
        # Request data
        req = context.get("request", {})
        logger.info("-" * 40)
        logger.info("REQUEST DATA:")
        logger.info(f"  Budget Code: {req.get('budget_code')}")
        logger.info(f"  Priority: {req.get('priority')}")
        
        # Ticket data
        ticket = context.get("ticket", {})
        logger.info("-" * 40)
        logger.info("TICKET DATA:")
        logger.info(f"  Title: {ticket.get('title')}")
        logger.info(f"  Category: {ticket.get('category')}")
        logger.info(f"  Priority: {ticket.get('priority')}")
        logger.info(f"  Title Prefix: {ticket.get('title_prefix')}")
        logger.info(f"  Description (first 200 chars): {ticket.get('description', '')[:200]}...")
        
        # Params data
        params = context.get("params", {})
        logger.info("-" * 40)
        logger.info("PARAMS DATA:")
        logger.info(f"  Item Name: {params.get('item_name')}")
        logger.info(f"  Quantity: {params.get('quantity')}")
        logger.info(f"  Vendor: {params.get('vendor')}")
        logger.info(f"  Unit Price: {params.get('unit_price')}")
        logger.info(f"  Category: {params.get('category')}")
        logger.info(f"  Manufacturer: {params.get('manufacturer')}")
        logger.info(f"  Model: {params.get('model')}")
        logger.info(f"  Stock Level: {params.get('stock_level')}")
        logger.info(f"  Location: {params.get('location')}")
        logger.info(f"  Notes: {params.get('notes')}")
        
        # Original equipment list
        logger.info("-" * 40)
        logger.info(f"ORIGINAL EQUIPMENT LIST ({len(request.equipment)} items):")
        for i, item in enumerate(request.equipment, 1):
            logger.info(f"  {i}. {item.name} (Category: {item.category})")
        
        logger.info("=" * 70)
    
    def _update_state_from_result(self, result: ScenarioResult) -> None:
        """Update workflow state from scenario result.
        
        Args:
            result: ScenarioResult from browser actions
        """
        # Extract ticket_id from outputs
        if "ticket_id" in result.outputs:
            self.state.ticket_id = result.outputs["ticket_id"]
        
        # Extract PO IDs
        if "po_id" in result.outputs:
            self.state.purchase_orders = [result.outputs["po_id"]]
        
        # Mark all items as allocated (simplified)
        self.state.allocated_items = [
            item.name for item in self.state.request.equipment
        ]

    def _send_notification(self) -> None:
        """Phase 8: Send email notification (API only, not browser).
        
        This is the only phase that doesn't use browser automation.
        """
        request = self.state.request
        employee = request.employee
        
        # Use requester_email if available
        recipient_email = request.requester_email if request.requester_email else employee.email
        
        notification_data = NotificationData(
            recipient_email=recipient_email,
            employee_name=employee.name,
            ticket_id=self.state.ticket_id or "N/A",
            allocated_items=self.state.allocated_items,
            cloudfront_domain=self.config.cloudfront_domain
        )
        
        # Skip if SES not initialized
        if self.ses_notifier is None or self._skip_ses:
            logger.info(f"[Simulation] Would send notification to {recipient_email}")
            return
        
        try:
            success = self.ses_notifier.send_equipment_ready_notification(notification_data)
            if not success:
                logger.warning("Email notification was not sent successfully")
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
    
    def is_email_processed(self, email_id: str) -> bool:
        """Check if an email has already been processed."""
        return self.email_tracker.is_processed(email_id)
    
    def get_processed_email_count(self) -> int:
        """Get the count of processed emails."""
        return len(self.email_tracker.processed_ids)
