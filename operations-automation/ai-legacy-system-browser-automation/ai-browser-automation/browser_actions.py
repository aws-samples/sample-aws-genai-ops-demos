"""Dynamic Browser Automation Actions for Legacy IT Portals.

This module provides JSON-driven browser automation using Nova Act.
All workflow steps are defined in JSON files and executed dynamically,
making it easy to modify workflows without code changes.

Usage:
    from browser_actions import BrowserActions
    
    actions = BrowserActions(nova, config)
    result = actions.run_workflow("itsm_create_ticket", variables)
    result = actions.run_full_scenario(context)
"""

import json
import re
import logging
import glob
import os
from pathlib import Path
from typing import Dict, Any, Optional, List, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from nova_act import NovaAct

logger = logging.getLogger(__name__)


@dataclass
class WorkflowResult:
    """Result of a workflow execution."""
    workflow_name: str
    status: str  # "success", "failed", "skipped", "api_only"
    steps_completed: int
    total_steps: int
    outputs: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


@dataclass 
class ScenarioResult:
    """Result of a complete scenario execution."""
    scenario_name: str
    status: str  # "success", "partial", "failed"
    phases_completed: int
    total_phases: int
    workflow_results: List[WorkflowResult] = field(default_factory=list)
    outputs: Dict[str, Any] = field(default_factory=dict)


class BrowserActions:
    """Dynamic browser actions using JSON workflow definitions.
    
    Loads workflow definitions from JSON and executes them step by step
    using Nova Act, with variable substitution and output capture.
    """
    
    DEFAULT_WORKFLOWS_FILE = "new_employee_onboarding_actions.json"
    
    def __init__(
        self,
        nova: Optional["NovaAct"],
        config: Dict[str, str],
        workflows_dir: Optional[str] = None,
        step_by_step: bool = False
    ):
        """Initialize browser actions.
        
        Args:
            nova: NovaAct instance (None for dry-run/simulation mode)
            config: Dict with portal URLs (itsm_url, inventory_url, procurement_url)
            workflows_dir: Directory containing workflow JSON files
            step_by_step: If True, require user confirmation after each action
        """
        self.nova = nova
        self.config = config
        self.dry_run = nova is None
        self.step_by_step = step_by_step
        self.outputs: Dict[str, Any] = {}  # Captured outputs across workflows
        
        # Determine workflows directory
        if workflows_dir:
            self.workflows_dir = Path(workflows_dir)
        else:
            # Default to 'workflows' relative to this file
            self.workflows_dir = Path(__file__).parent / "workflows"
        
        if self.step_by_step:
            logger.info("*** STEP-BY-STEP MODE ENABLED - Type 'ok' after each action to continue ***")

    def load_workflow_file(self, filepath: str) -> Dict[str, Any]:
        """Load a workflow JSON file.
        
        Args:
            filepath: Path to the JSON file (relative to workflows_dir or absolute)
            
        Returns:
            Parsed workflow definition
        """
        path = Path(filepath)
        if not path.is_absolute():
            path = self.workflows_dir / path
            
        logger.info(f"Loading workflow file: {path}")
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def substitute_variables(self, text: str, variables: Dict[str, Any]) -> str:
        """Replace {{variable}} placeholders with actual values.
        
        Supports nested access like {{employee.name}} and {{config.itsm_url}}.
        
        Args:
            text: Text containing {{variable}} placeholders
            variables: Dict of variable values
            
        Returns:
            Text with placeholders replaced
        """
        def replace_match(match):
            key = match.group(1)
            parts = key.split('.')
            value = variables
            for part in parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return match.group(0)  # Keep original if not found
            return str(value)
        
        return re.sub(r'\{\{([^}]+)\}\}', replace_match, text)
    
    def extract_from_log_file(self, pattern: str) -> Optional[str]:
        """Extract value from Nova Act HTML log files.
        
        Nova Act writes detailed logs including return() values to HTML files.
        This searches through recent logs to find the return value.
        
        Args:
            pattern: Regex pattern to match (used to determine what to extract)
            
        Returns:
            Extracted value or None
        """
        try:
            # Find the Nova Act logs directory (usually in temp)
            import tempfile
            temp_dir = tempfile.gettempdir()
            
            # Look for nova_act_logs directories
            log_dirs = glob.glob(os.path.join(temp_dir, "*nova_act_logs*"))
            if not log_dirs:
                logger.warning("  [LOG] No nova_act_logs directories found in %s", temp_dir)
                return None
            
            # Get the most recent log directory
            log_dirs.sort(key=os.path.getmtime, reverse=True)
            latest_log_dir = log_dirs[0]
            logger.info(f"  [LOG] Using log directory: {latest_log_dir}")
            
            # Find all HTML files in subdirectories
            html_files = glob.glob(os.path.join(latest_log_dir, "**", "*.html"), recursive=True)
            if not html_files:
                logger.warning("  [LOG] No HTML files found in log directory")
                return None
            
            # Sort by modification time (most recent first)
            html_files.sort(key=os.path.getmtime, reverse=True)
            logger.info(f"  [LOG] Found {len(html_files)} HTML log files")
            
            # For ticket ID, prioritize files with "success" or "ticket" in the name
            if pattern and "INC" in pattern:
                # Reorder to check relevant files first
                priority_files = [f for f in html_files if "success" in f.lower() or "ticket" in f.lower()]
                other_files = [f for f in html_files if f not in priority_files]
                html_files = priority_files + other_files
                if priority_files:
                    logger.info(f"  [LOG] Prioritizing {len(priority_files)} files with 'success'/'ticket' in name")
            
            # For stock level, prioritize files with "stock" or "available" in the name
            if pattern and pattern == "\\d+":
                priority_files = [f for f in html_files if "stock" in f.lower() or "available" in f.lower() or "read" in f.lower()]
                other_files = [f for f in html_files if f not in priority_files]
                html_files = priority_files + other_files
                if priority_files:
                    logger.info(f"  [LOG] Prioritizing {len(priority_files)} files with 'stock'/'available' in name")
            
            # Search through recent log files (check up to 10 most recent)
            for html_file in html_files[:10]:
                filename = os.path.basename(html_file)
                logger.debug(f"  [LOG] Checking: {filename}")
                
                with open(html_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Determine what pattern to look for based on output_pattern
                if pattern and "INC" in pattern:
                    # Looking for ticket ID
                    # HTML encodes quotes as &quot;
                    # Pattern: return(&quot;INC-123456&quot;)
                    return_match = re.search(r'return\(&quot;(INC-\d{6})&quot;\)', content)
                    if return_match:
                        value = return_match.group(1)
                        logger.info(f"  [LOG] Found ticket ID in {filename}: {value}")
                        return value
                    
                    # Try regular quotes
                    return_match = re.search(r'return\(["\']?(INC-\d{6})["\']?\)', content)
                    if return_match:
                        value = return_match.group(1)
                        logger.info(f"  [LOG] Found ticket ID in {filename}: {value}")
                        return value
                        
                elif pattern and "PO" in pattern:
                    # Looking for PO ID
                    return_match = re.search(r'return\(&quot;(PO-\d{4}-\d{3})&quot;\)', content)
                    if return_match:
                        value = return_match.group(1)
                        logger.info(f"  [LOG] Found PO ID in {filename}: {value}")
                        return value
                    
                    return_match = re.search(r'return\(["\']?(PO-\d{4}-\d{3})["\']?\)', content)
                    if return_match:
                        value = return_match.group(1)
                        logger.info(f"  [LOG] Found PO ID in {filename}: {value}")
                        return value
                
                elif pattern == "\\d+":
                    # Looking for a numeric value (stock level)
                    # Try HTML-encoded quotes first
                    return_match = re.search(r'return\(&quot;(\d+)&quot;\)', content)
                    if return_match:
                        value = return_match.group(1)
                        logger.info(f"  [LOG] Found numeric value in {filename}: {value}")
                        return value
                    
                    # Try regular quotes
                    return_match = re.search(r'return\(["\']?(\d+)["\']?\)', content)
                    if return_match:
                        value = return_match.group(1)
                        logger.info(f"  [LOG] Found numeric value in {filename}: {value}")
                        return value
                    
                    # Try without quotes (just the number)
                    return_match = re.search(r'return\((\d+)\)', content)
                    if return_match:
                        value = return_match.group(1)
                        logger.info(f"  [LOG] Found numeric value in {filename}: {value}")
                        return value
            
            # If we get here, no match was found
            if pattern and "INC" in pattern:
                logger.warning("  [LOG] No INC- return value found in any recent log files")
            elif pattern and "PO" in pattern:
                logger.warning("  [LOG] No PO- return value found in any recent log files")
            elif pattern == "\\d+":
                logger.warning("  [LOG] No numeric return value found in any recent log files")
            else:
                logger.warning(f"  [LOG] Unknown pattern type: {pattern}")
                    
        except Exception as e:
            logger.warning(f"  [LOG] Error parsing log files: {e}")
            import traceback
            logger.debug(traceback.format_exc())
        
        return None
    
    def extract_output(self, result: Any, pattern: Optional[str]) -> Optional[str]:
        """Extract output from Nova Act HTML log file.
        
        Args:
            result: Nova Act response object (not used)
            pattern: Regex pattern to match (not used - we parse HTML log)
            
        Returns:
            Extracted value or None
        """
        if not pattern:
            return None
        
        # Only method: parse the HTML log file
        log_value = self.extract_from_log_file(pattern)
        if log_value:
            return log_value
        
        logger.warning(f"  [WARN] Could not extract value from log file")
        return None

    def run_workflow(
        self,
        workflow_name: str,
        variables: Dict[str, Any],
        workflows_file: str = None
    ) -> WorkflowResult:
        """Execute a single workflow by name.
        
        Args:
            workflow_name: Name of the workflow (e.g., "itsm_create_ticket")
            variables: Variables for substitution
            workflows_file: Optional path to workflow JSON file
            
        Returns:
            WorkflowResult with execution status and outputs
        """
        file_path = workflows_file or self.DEFAULT_WORKFLOWS_FILE
        scenario = self.load_workflow_file(file_path)
        workflows = scenario.get("workflows", {})
        
        workflow_def = workflows.get(workflow_name)
        if not workflow_def:
            raise ValueError(f"Workflow '{workflow_name}' not found in {file_path}")
        
        return self._execute_workflow(workflow_def, variables)
    
    def _execute_workflow(
        self,
        workflow_def: Dict[str, Any],
        variables: Dict[str, Any]
    ) -> WorkflowResult:
        """Execute a workflow definition.
        
        Args:
            workflow_def: Workflow definition dict (from JSON)
            variables: Variables for substitution
            
        Returns:
            WorkflowResult with execution status and outputs
        """
        workflow_name = workflow_def.get("workflow_name", "unknown")
        steps = workflow_def.get("steps", [])
        api_only = workflow_def.get("api_only", False)
        
        result = WorkflowResult(
            workflow_name=workflow_name,
            status="pending",
            steps_completed=0,
            total_steps=len(steps)
        )
        
        logger.info("=" * 60)
        logger.info(f"WORKFLOW: {workflow_name}")
        logger.info(f"Description: {workflow_def.get('description', 'N/A')}")
        logger.info(f"Portal: {workflow_def.get('portal', 'N/A')}")
        logger.info(f"Steps: {len(steps)}")
        logger.info("=" * 60)
        
        # Log workflow-level variables before resolution
        workflow_vars = workflow_def.get("variables", {})
        if workflow_vars:
            logger.info("WORKFLOW VARIABLES (before resolution):")
            for key, value in workflow_vars.items():
                logger.info(f"  {key}: {value}")
        
        # Handle API-only workflows (no browser actions)
        if api_only:
            logger.info(f"[API-ONLY] Workflow '{workflow_name}' has no browser steps")
            result.status = "api_only"
            return result
        
        # Merge config into variables for substitution
        full_vars = {
            "config": self.config,
            "outputs": self.outputs,
            "state": self.outputs,  # Alias for backwards compatibility
            **variables
        }
        
        # Log state/outputs for debugging
        if self.outputs:
            logger.info("CAPTURED OUTPUTS (state):")
            for key, value in self.outputs.items():
                logger.info(f"  {key}: {value}")
        else:
            logger.info("CAPTURED OUTPUTS (state): <empty>")
        
        # First pass: resolve workflow-level variables (e.g., itsm_url from config.itsm_url)
        workflow_vars = workflow_def.get("variables", {})
        resolved_vars = {}
        for key, value in workflow_vars.items():
            resolved_vars[key] = self.substitute_variables(str(value), full_vars)
        
        # Log resolved workflow variables
        if resolved_vars:
            logger.info("WORKFLOW VARIABLES (after resolution):")
            for key, value in resolved_vars.items():
                # Truncate long values for readability
                display_value = str(value)[:100] + "..." if len(str(value)) > 100 else value
                logger.info(f"  {key}: {display_value}")
            logger.info("-" * 40)
        
        # Merge resolved workflow vars into full_vars for step substitution
        full_vars.update(resolved_vars)
        
        # Execute each step
        for step in steps:
            step_result = self._execute_step(step, full_vars, result)
            if not step_result:
                return result  # Step failed, stop workflow
        
        result.status = "success"
        logger.info(f"[COMPLETE] Workflow '{workflow_name}' finished successfully")
        return result

    def _execute_step(
        self,
        step: Dict[str, Any],
        variables: Dict[str, Any],
        result: WorkflowResult
    ) -> bool:
        """Execute a single workflow step.
        
        Args:
            step: Step definition from JSON
            variables: Variables for substitution
            result: WorkflowResult to update
            
        Returns:
            True if step succeeded, False if failed
        """
        act_id = step.get("act_id", 0)
        name = step.get("name", f"step_{act_id}")
        instruction = step.get("instruction", "")
        description = step.get("description", "")
        capture = step.get("capture_output", False)
        output_var = step.get("output_variable")
        output_pattern = step.get("output_pattern")
        
        # Substitute variables in instruction
        instruction = self.substitute_variables(instruction, variables)
        
        logger.info("-" * 40)
        logger.info(f"[Step {act_id}] {name}")
        if description:
            logger.info(f"  Description: {description}")
        
        # Log the original instruction template (before substitution)
        original_instruction = step.get("instruction", "")
        if original_instruction != instruction:
            logger.debug(f"  Template: {original_instruction[:80]}{'...' if len(original_instruction) > 80 else ''}")
        
        # Log the resolved instruction (after substitution)
        logger.info(f"  Instruction: {instruction[:150]}{'...' if len(instruction) > 150 else ''}")
        
        try:
            if self.dry_run:
                logger.info(f"  [DRY-RUN] Would execute: {instruction[:50]}...")
                response = None
            else:
                # Use act_get with STRING_SCHEMA when we need to capture output
                if capture and output_var:
                    try:
                        from nova_act.types.act_get_schema import STRING_SCHEMA
                        response = self.nova.act_get(instruction, response_schema=STRING_SCHEMA)
                        # act_get returns ActGetResult with response attribute
                        if hasattr(response, 'response') and response.response:
                            logger.info(f"  [RESPONSE] act_get response: {response.response}")
                    except ImportError:
                        logger.warning("  [WARN] act_get not available, falling back to act()")
                        response = self.nova.act(instruction)
                else:
                    response = self.nova.act(instruction)
                logger.info(f"  [OK] Step completed")
            
            # Capture output if requested
            if capture and output_var:
                # Log what Nova Act returned for debugging
                if response is not None:
                    logger.debug(f"  [DEBUG] Nova Act response type: {type(response)}")
                    logger.debug(f"  [DEBUG] Nova Act response: {response}")
                    if hasattr(response, 'parsed_response'):
                        logger.info(f"  [RESPONSE] parsed_response: {response.parsed_response}")
                    if hasattr(response, 'response'):
                        logger.info(f"  [RESPONSE] response: {response.response}")
                
                extracted = self.extract_output(response, output_pattern)
                if extracted:
                    result.outputs[output_var] = extracted
                    self.outputs[output_var] = extracted
                    logger.info(f"  [CAPTURED] {output_var} = {extracted}")
                else:
                    # Generate fallback for ticket_id and po_id
                    if output_var == "ticket_id":
                        import datetime
                        fallback = f"INC-{datetime.datetime.now().strftime('%H%M%S')}"
                        result.outputs[output_var] = fallback
                        self.outputs[output_var] = fallback
                        logger.warning(f"  [FALLBACK] {output_var} = {fallback}")
                    elif output_var == "po_id":
                        import datetime
                        fallback = f"PO-{datetime.datetime.now().year}-{datetime.datetime.now().strftime('%H%M%S')[-3:]}"
                        result.outputs[output_var] = fallback
                        self.outputs[output_var] = fallback
                        logger.warning(f"  [FALLBACK] {output_var} = {fallback}")
                    else:
                        logger.warning(f"  [WARN] Could not capture {output_var}")
            
            result.steps_completed += 1
            
            # Wait for user confirmation if step-by-step mode is enabled
            if self.step_by_step:
                if not self._wait_for_user_confirmation(act_id, name):
                    logger.info("  [ABORTED] User cancelled the workflow")
                    result.status = "aborted"
                    return False
            
            return True
            
        except Exception as e:
            error_msg = f"Step {act_id} ({name}) failed: {str(e)}"
            logger.error(f"  [ERROR] {error_msg}")
            result.errors.append(error_msg)
            result.status = "failed"
            return False
    
    def _wait_for_user_confirmation(self, step_id: int, step_name: str) -> bool:
        """Wait for user to type 'ok' to continue to the next step.
        
        Args:
            step_id: Current step ID
            step_name: Current step name
            
        Returns:
            True if user confirmed, False if user wants to abort
        """
        print("\n" + "=" * 50)
        print(f"Step {step_id} ({step_name}) completed.")
        print("Type 'ok' to continue, 'skip' to skip next step, or 'abort' to stop:")
        print("=" * 50)
        
        while True:
            try:
                user_input = input(">>> ").strip().lower()
                if user_input == "ok":
                    logger.info("  [USER] Confirmed - continuing to next step")
                    return True
                elif user_input == "abort" or user_input == "quit" or user_input == "q":
                    logger.info("  [USER] Aborted workflow")
                    return False
                elif user_input == "skip":
                    logger.info("  [USER] Will skip next step")
                    return True  # Continue but mark for skip (could be enhanced)
                else:
                    print("Please type 'ok' to continue, or 'abort' to stop.")
            except (KeyboardInterrupt, EOFError):
                logger.info("  [USER] Interrupted - aborting workflow")
                return False

    def check_skip_condition(self, skip_if: str, context: Dict[str, Any]) -> bool:
        """Check if a phase should be skipped based on condition.
        
        Args:
            skip_if: Condition name (e.g., "stock_sufficient")
            context: Current context with outputs
            
        Returns:
            True if phase should be skipped, False otherwise
        """
        if skip_if == "stock_sufficient":
            # Get stock level and required quantity
            stock_level = self.outputs.get("stock_level", "0")
            required_qty = context.get("params", {}).get("quantity", 1)
            
            try:
                stock = int(stock_level) if stock_level else 0
                required = int(required_qty) if required_qty else 1
                
                if stock >= required:
                    logger.info(f"  [CONDITION] Stock sufficient: {stock} >= {required} required")
                    return True
                else:
                    logger.info(f"  [CONDITION] Stock insufficient: {stock} < {required} required")
                    return False
            except (ValueError, TypeError) as e:
                logger.warning(f"  [CONDITION] Error parsing stock values: {e}")
                return False
        
        return False

    def process_equipment_list(
        self,
        equipment_list: List[Dict[str, Any]],
        context: Dict[str, Any],
        workflows_file: str = None
    ) -> Dict[str, Any]:
        """Process multiple equipment items, checking stock and ordering as needed.
        
        For each item in the list:
        1. Search inventory for the item
        2. Check if stock is sufficient
        3. If not, create a purchase order
        4. Allocate the item to the employee
        
        Args:
            equipment_list: List of equipment items with name, quantity, etc.
            context: Base context with employee info, config, etc.
            workflows_file: Optional path to workflow JSON file
            
        Returns:
            Dict with results for each item
        """
        file_path = workflows_file or self.DEFAULT_WORKFLOWS_FILE
        scenario = self.load_workflow_file(file_path)
        workflows = scenario.get("workflows", {})
        
        results = {
            "items_processed": 0,
            "items_in_stock": [],
            "items_ordered": [],
            "items_failed": [],
            "allocations": []
        }
        
        logger.info("=" * 70)
        logger.info(f"PROCESSING {len(equipment_list)} EQUIPMENT ITEMS")
        logger.info("=" * 70)
        
        for idx, item in enumerate(equipment_list, 1):
            item_name = item.get("name", item.get("item_name", "Unknown"))
            quantity = item.get("quantity", 1)
            
            logger.info(f"\n{'#' * 60}")
            logger.info(f"# ITEM {idx}/{len(equipment_list)}: {item_name} (qty: {quantity})")
            logger.info(f"{'#' * 60}")
            
            # Reset stock_level for this item
            self.outputs.pop("stock_level", None)
            
            # Step 1: Search inventory and check stock
            item_context = {
                **context,
                "params": {
                    **context.get("params", {}),
                    "item_name": item_name,
                    "quantity": quantity
                }
            }
            
            inventory_workflow = workflows.get("inventory_search")
            if inventory_workflow:
                logger.info(f"\n[INVENTORY CHECK] Searching for: {item_name}")
                inv_result = self._execute_workflow(inventory_workflow, item_context)
                
                if inv_result.status != "success":
                    logger.error(f"  [ERROR] Inventory search failed for {item_name}")
                    results["items_failed"].append({"item": item_name, "reason": "inventory_search_failed"})
                    continue
            
            # Step 2: Check if stock is sufficient
            stock_level = self.outputs.get("stock_level", "0")
            try:
                stock = int(stock_level) if stock_level else 0
            except (ValueError, TypeError):
                stock = 0
            
            logger.info(f"  [STOCK] {item_name}: {stock} available, {quantity} needed")
            
            if stock >= quantity:
                # Stock is sufficient - just allocate
                logger.info(f"  [OK] Stock sufficient for {item_name}")
                results["items_in_stock"].append({"item": item_name, "stock": stock, "quantity": quantity})
            else:
                # Stock insufficient - need to order
                logger.info(f"  [ORDER] Need to order {item_name}")
                results["items_ordered"].append({"item": item_name, "stock": stock, "quantity": quantity})
                
                # Run procurement workflow
                procurement_context = {
                    **item_context,
                    "params": {
                        **item_context.get("params", {}),
                        "vendor": item.get("vendor", "TechCorp Solutions"),
                        "unit_price": item.get("unit_price", item.get("price", 0))
                    }
                }
                
                # Create PO
                po_workflow = workflows.get("procurement_create_po")
                if po_workflow:
                    po_result = self._execute_workflow(po_workflow, procurement_context)
                    if po_result.status != "success":
                        results["items_failed"].append({"item": item_name, "reason": "po_creation_failed"})
                        continue
                
                # Submit, approve, receive PO
                for wf_name in ["procurement_submit_po", "procurement_approve_po", "procurement_receive"]:
                    wf = workflows.get(wf_name)
                    if wf:
                        wf_result = self._execute_workflow(wf, procurement_context)
                        if wf_result.status != "success":
                            logger.warning(f"  [WARN] {wf_name} had issues but continuing")
            
            # Step 3: Allocate item to employee
            allocate_workflow = workflows.get("inventory_allocate")
            if allocate_workflow:
                allocate_context = {
                    **item_context,
                    "params": {
                        **item_context.get("params", {}),
                        "notes": f"Onboarding allocation for {context.get('employee', {}).get('name', 'new employee')}"
                    }
                }
                alloc_result = self._execute_workflow(allocate_workflow, allocate_context)
                if alloc_result.status == "success":
                    results["allocations"].append({"item": item_name, "quantity": quantity})
            
            results["items_processed"] += 1
        
        logger.info("\n" + "=" * 70)
        logger.info("EQUIPMENT PROCESSING COMPLETE")
        logger.info(f"  Processed: {results['items_processed']}/{len(equipment_list)}")
        logger.info(f"  In stock: {len(results['items_in_stock'])}")
        logger.info(f"  Ordered: {len(results['items_ordered'])}")
        logger.info(f"  Failed: {len(results['items_failed'])}")
        logger.info(f"  Allocated: {len(results['allocations'])}")
        logger.info("=" * 70)
        
        return results

    def run_full_scenario(
        self,
        context: Dict[str, Any],
        skip_phases: Optional[List[str]] = None,
        workflows_file: str = None
    ) -> ScenarioResult:
        """Execute a complete scenario from a JSON file.
        
        Runs through all phases defined in the composite workflow,
        passing outputs between phases.
        
        Args:
            context: Context dict with employee, ticket, request, params data
            skip_phases: Optional list of phase IDs to skip
            workflows_file: Optional path to workflow JSON file
            
        Returns:
            ScenarioResult with all workflow results
        """
        skip_phases = skip_phases or []
        file_path = workflows_file or self.DEFAULT_WORKFLOWS_FILE
        
        # Load the scenario file
        scenario = self.load_workflow_file(file_path)
        scenario_name = scenario.get("description", "Unknown Scenario")
        
        logger.info("#" * 70)
        logger.info(f"# SCENARIO: {scenario_name}")
        logger.info(f"# Total Nova Act Calls: {scenario.get('total_nova_act_calls', 'N/A')}")
        logger.info("#" * 70)
        
        result = ScenarioResult(
            scenario_name=scenario_name,
            status="pending",
            phases_completed=0,
            total_phases=0
        )
        
        # Get the full onboarding workflow phases
        composite = scenario.get("composite_workflows", {})
        full_onboarding = composite.get("full_onboarding", {})
        phases = full_onboarding.get("phases", [])
        
        result.total_phases = len(phases)
        workflows = scenario.get("workflows", {})
        
        logger.info(f"Phases to execute: {len(phases)}")
        
        for phase_def in phases:
            phase_id = str(phase_def.get("phase", "?"))
            workflow_name = phase_def.get("workflow")
            skip_if = phase_def.get("skip_if")
            
            # Check if phase should be skipped by user request
            if phase_id in skip_phases:
                logger.info(f"\n[SKIP] Phase {phase_id} - {workflow_name} (user requested)")
                continue
            
            # Check if phase should be skipped by condition
            if skip_if and self.check_skip_condition(skip_if, context):
                logger.info(f"\n[SKIP] Phase {phase_id} - {workflow_name} (condition: {skip_if})")
                continue
            
            # Get workflow definition
            workflow_def = workflows.get(workflow_name)
            if not workflow_def:
                logger.error(f"[ERROR] Workflow '{workflow_name}' not found in scenario")
                continue
            
            logger.info(f"\n{'#' * 50}")
            logger.info(f"# PHASE {phase_id}: {workflow_name}")
            logger.info(f"{'#' * 50}")
            
            # Merge phase inputs with context
            phase_inputs = phase_def.get("inputs", {})
            variables = {
                **context,
                "params": {**context.get("params", {}), **phase_inputs},
                "state": self.outputs  # Pass captured outputs as state
            }
            
            # Execute the workflow
            workflow_result = self._execute_workflow(workflow_def, variables)
            result.workflow_results.append(workflow_result)
            
            # Update outputs with any captured values
            result.outputs.update(workflow_result.outputs)
            
            if workflow_result.status in ["success", "api_only"]:
                result.phases_completed += 1
            elif workflow_result.status == "failed":
                logger.error(f"[FAILED] Phase {phase_id} failed, stopping scenario")
                result.status = "failed"
                return result
        
        result.status = "success" if result.phases_completed == result.total_phases else "partial"
        
        logger.info("\n" + "=" * 70)
        logger.info(f"SCENARIO COMPLETE: {result.status.upper()}")
        logger.info(f"Phases completed: {result.phases_completed}/{result.total_phases}")
        logger.info(f"Captured outputs: {list(result.outputs.keys())}")
        logger.info("=" * 70)
        
        return result
