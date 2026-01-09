"""
Prompt Engineering Detector - Generic prompt optimization patterns.

RESPONSIBILITY:
    Detect generic prompt engineering patterns that apply to ANY LLM service
    (Bedrock, OpenAI, Anthropic direct, etc.). Focus on code patterns that
    indicate optimization opportunities, regardless of the underlying service.

WHAT THIS DETECTOR HANDLES:
    ✅ Recurring prompts with static content (AST analysis)
        - Functions that build prompts with f-strings
        - Functions called multiple times (caching opportunity)
        - Static vs dynamic content estimation
    ✅ LLM API calls in loops (AST analysis)
        - Bedrock, OpenAI, Anthropic API calls
        - For/while loop detection
        - Batch processing opportunities
    ✅ Prompt builder function detection
        - Functions with "prompt", "message", "instruction" in name
        - Functions that concatenate/format strings
        - Call count tracking

WHAT THIS DETECTOR DOES NOT HANDLE:
    ❌ Bedrock-specific features (→ bedrock_detector.py):
        - Prompt caching with cross-region inference
        - Nova explicit caching
        - Prompt routing
        - Model ID detection
        - Bedrock API patterns
    ❌ Service-specific configurations (→ respective detectors)
    ❌ Cost calculations (→ AWS MCP Server)
    ❌ Bedrock-specific caching implementation (→ bedrock_detector.py enriches our findings)

DETECTION STRATEGY:
    Uses Python AST (Abstract Syntax Tree) to analyze code structure:
    - Find functions that build prompts (naming patterns)
    - Analyze string operations (f-strings, concatenation)
    - Estimate static vs dynamic content
    - Track function call counts
    - Detect API calls inside loops

DETECTION PURPOSES (Why we detect each pattern):

    1. PromptAnalyzer.visit_FunctionDef()
       PURPOSE: Find prompt builder functions → identify caching opportunities
       DETECTS: Functions with "prompt", "message", "build_", etc. in name
       
    2. PromptAnalyzer._analyze_prompt_function()
       PURPOSE: Analyze string operations → estimate static vs dynamic content
       DETECTS: f-strings, concatenation, large literals
       
    3. PromptAnalyzer.visit_Call()
       PURPOSE: Track function usage + detect LLM calls → measure reuse + find loops
       DETECTS: Function call counts, API calls in loops
       
    4. PromptAnalyzer.visit_For() / visit_While()
       PURPOSE: Track loop context → flag API calls that repeat
       DETECTS: Loop boundaries for API call detection
       
    5. CallCounter.visit_Call()
       PURPOSE: Count how many times each function is called → assess caching value
       DETECTS: Function call frequency
       
    6. _generate_findings() - recurring_prompt_with_static_content
       PURPOSE: Find functions with significant static content called multiple times
       → Suggest caching (generic recommendation, Bedrock enriches with specifics)
       
    7. _generate_findings() - prompt_builder_function_detected
       PURPOSE: Find prompt builders with small static content
       → Flag for monitoring if call frequency increases
       
    8. _generate_findings() - llm_api_call_in_loop
       PURPOSE: Find LLM API calls inside loops → suggest batch processing or caching
       NOTE: Generic pattern detection. Bedrock detector enriches with Bedrock Prompt Caching specifics.

DESIGN PRINCIPLE:
    Generic patterns only. We detect the PATTERN (loop, recurring function, etc.)
    and suggest GENERIC solutions (caching, batching). Service-specific detectors
    (bedrock_detector.py) enrich with implementation details (Bedrock Prompt Caching).

EXAMPLES:
    ✅ Detect: Function "build_prompt()" called 50 times with 200 static tokens
    ✅ Detect: bedrock.converse() inside a for loop
    ✅ Suggest: "Consider prompt caching for static content" (generic)
    ❌ Don't: "Use Bedrock cacheControl blocks" (Bedrock-specific, belongs in bedrock_detector)
    
    NOTE: Currently we DO mention "Bedrock Prompt Caching" in findings for convenience,
    but ideally bedrock_detector.py would enrich our generic findings with Bedrock specifics.
"""

import ast
import re
from pathlib import Path
from typing import List, Dict, Any, Set, Optional, Tuple
from collections import defaultdict

from .base import BaseDetector


class PromptEngineeringDetector(BaseDetector):
    """
    Comprehensive prompt engineering optimization detector.
    
    Combines AST-based and regex-based analysis to find:
    - Recurring prompts with static content (AST)
    - LLM API calls in loops (AST)
    - Repeated prompt context (regex)
    - Prompt quality opportunities (regex)
    - Nova optimizer opportunities (regex)
    - Token usage patterns (regex)
    
    Focus: Generic prompt optimization applicable to any LLM.
    Bedrock-specific features (caching, routing) are in bedrock_detector.py.
    """
    
    # LLM API call patterns
    LLM_API_PATTERNS = [
        'bedrock.converse',
        'bedrock_runtime.converse',
        'bedrock.invoke_model',
        'bedrock_runtime.invoke_model',
        'openai.chat.completions.create',
        'anthropic.messages.create',
    ]
    
    # Prompt building function name patterns (case-insensitive)
    PROMPT_BUILDER_PATTERNS = [
        r'.*prompt.*',      # Matches any function with "prompt" in the name
        r'.*message.*',     # Matches any function with "message" in the name
        r'.*instruction.*', # Matches any function with "instruction" in the name
        r'build_.*',        # Matches functions starting with "build_"
        r'create_.*',       # Matches functions starting with "create_"
        r'format_.*',       # Matches functions starting with "format_"
        r'generate_.*',     # Matches functions starting with "generate_"
    ]
    
    def can_analyze(self, file_path: Path) -> bool:
        """Only analyze Python files."""
        return file_path.suffix == '.py'
    
    def analyze(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Analyze Python code for recurring prompt patterns."""
        findings = []
        
        try:
            tree = ast.parse(content)
        except SyntaxError:
            # Skip files with syntax errors
            return findings
        
        # First pass: Find prompt builders and LLM calls
        analyzer = PromptAnalyzer(content, file_path)
        analyzer.visit(tree)
        
        # Second pass: Count function calls
        call_counter = CallCounter()
        call_counter.visit(tree)
        analyzer.function_calls = call_counter.function_calls
        
        # Generate findings from analysis
        findings.extend(self._generate_findings(analyzer, file_path))
        
        # Add clickable file links to all findings
        from ..utils.file_links import create_file_link
        for finding in findings:
            if 'line' in finding and finding.get('file'):
                finding['file_link'] = create_file_link(finding['file'], finding['line'])
        
        return findings
    
    def _generate_findings(self, analyzer: 'PromptAnalyzer', file_path: str) -> List[Dict[str, Any]]:
        """Generate findings from AST analysis."""
        findings = []
        
        # Check for prompt builders - report ANY prompt building function that's called
        for func_name, info in analyzer.prompt_builders.items():
            call_count = analyzer.function_calls.get(func_name, 0)
            
            # Report if function is called at least once
            # Even functions with small static content may benefit from caching if called many times
            if call_count > 0:
                # Determine severity based on static content size
                # NOTE: 50 tokens is a heuristic threshold for initial filtering.
                # Actual prompt caching minimums are model-specific (1K-4K tokens).
                # See enrichment_required below for verification guidance.
                has_significant_static = info['estimated_static_tokens'] > 50
                
                if has_significant_static:
                    findings.append({
                        'type': 'recurring_prompt_with_static_content',
                        'file': file_path,
                        'line': info['line'],
                        'function_name': func_name,
                        'call_count': call_count,
                        'estimated_static_tokens': info['estimated_static_tokens'],
                        'service': 'bedrock',
                        'description': f"Function '{func_name}' builds prompts with large static content and is called {call_count} times",
                        'cost_consideration': f"Prompt caching can save 90% on repeated static content. This function has ~{info['estimated_static_tokens']} static tokens and is called {call_count} times.",
                        'optimization': {
                            'technique': 'Bedrock Prompt Caching',
                            'potential_savings': '90% on cached tokens',
                            'implementation': 'Add cacheControl blocks to mark static content for caching',
                            'documentation': 'https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html'
                        },
                        'enrichment_required': {
                            'priority': 'HIGH',
                            'why': 'Must verify if estimated tokens meet model-specific minimum for prompt caching',
                            'action': 'Check AWS documentation for minimum token requirements',
                            'documentation': 'https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html',
                            'model_specific_minimums': {
                                'claude_3_7_sonnet': '1,024 tokens',
                                'claude_3_5_haiku': '2,048 tokens',
                                'claude_opus_4_5': '4,096 tokens',
                                'amazon_nova': '1,000 tokens (max 20K)',
                                'note': 'If estimated tokens < minimum, prompt caching will NOT work for this model'
                            }
                        },
                        'code_pattern': {
                            'static_content_detected': True,
                            'dynamic_content_detected': info['has_dynamic_content'],
                            'f_string_usage': info['uses_f_string'],
                            'string_concatenation': info['uses_concatenation']
                        }
                    })
                else:
                    # Small static content but still a prompt builder
                    findings.append({
                        'type': 'prompt_builder_function_detected',
                        'file': file_path,
                        'line': info['line'],
                        'function_name': func_name,
                        'call_count': call_count,
                        'estimated_static_tokens': info['estimated_static_tokens'],
                        'service': 'bedrock',
                        'description': f"Function '{func_name}' builds prompts dynamically and is called {call_count} time(s)",
                        'cost_consideration': f"This function builds prompts with ~{info['estimated_static_tokens']} tokens of static content. If called multiple times at runtime (e.g., processing multiple items), consider prompt caching for the static portions.",
                        'optimization': {
                            'technique': 'Bedrock Prompt Caching (if called frequently)',
                            'potential_savings': 'Up to 90% on cached tokens',
                            'recommendation': 'Monitor if this function is called frequently at runtime. If so, implement prompt caching.',
                            'documentation': 'https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html'
                        },
                        'code_pattern': {
                            'static_content_detected': info['estimated_static_tokens'] > 0,
                            'dynamic_content_detected': info['has_dynamic_content'],
                            'f_string_usage': info['uses_f_string'],
                            'string_concatenation': info['uses_concatenation']
                        }
                    })
        
        # Check for LLM API calls in loops
        for func_name, loop_info in analyzer.llm_calls_in_loops.items():
            findings.append({
                'type': 'llm_api_call_in_loop',
                'file': file_path,
                'line': loop_info['line'],
                'function_name': func_name,
                'loop_type': loop_info['loop_type'],
                'service': 'bedrock',
                'description': f"LLM API call inside {loop_info['loop_type']} loop in function '{func_name}'",
                'cost_consideration': f"LLM calls in loops can result in many repeated API calls. Consider prompt caching if the same context is used across iterations.",
                'optimization': {
                    'technique': 'Batch processing or prompt caching',
                    'recommendation': 'If the loop processes similar items with shared context, use prompt caching to avoid re-processing static content'
                }
            })
        
        return findings


class CallCounter(ast.NodeVisitor):
    """Simple visitor to count function calls."""
    
    def __init__(self):
        self.function_calls: Dict[str, int] = defaultdict(int)
    
    def visit_Call(self, node: ast.Call) -> None:
        """Count function calls."""
        func_name = None
        
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            # For method calls, just use the method name
            func_name = node.func.attr
        
        if func_name:
            self.function_calls[func_name] += 1
        
        self.generic_visit(node)


class PromptAnalyzer(ast.NodeVisitor):
    """
    AST visitor that analyzes Python code for prompt building and LLM API usage patterns.
    """
    
    def __init__(self, content: str, file_path: str):
        self.content = content
        self.file_path = file_path
        self.lines = content.split('\n')
        
        # Track prompt building functions
        self.prompt_builders: Dict[str, Dict[str, Any]] = {}
        
        # Track function calls
        self.function_calls: Dict[str, int] = defaultdict(int)
        
        # Track LLM API calls in loops
        self.llm_calls_in_loops: Dict[str, Dict[str, Any]] = {}
        
        # Current context
        self.current_function: Optional[str] = None
        self.in_loop: bool = False
        self.loop_depth: int = 0
    
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Visit function definitions to find prompt builders."""
        old_function = self.current_function
        self.current_function = node.name
        
        # Check if this looks like a prompt building function
        if self._is_prompt_builder(node.name):
            # Analyze the function body for prompt patterns
            prompt_info = self._analyze_prompt_function(node)
            if prompt_info:
                self.prompt_builders[node.name] = prompt_info
        
        # Continue visiting child nodes
        self.generic_visit(node)
        
        self.current_function = old_function
    
    def visit_For(self, node: ast.For) -> None:
        """Track when we're inside a for loop."""
        self.in_loop = True
        self.loop_depth += 1
        self.generic_visit(node)
        self.loop_depth -= 1
        if self.loop_depth == 0:
            self.in_loop = False
    
    def visit_While(self, node: ast.While) -> None:
        """Track when we're inside a while loop."""
        self.in_loop = True
        self.loop_depth += 1
        self.generic_visit(node)
        self.loop_depth -= 1
        if self.loop_depth == 0:
            self.in_loop = False
    
    def visit_Call(self, node: ast.Call) -> None:
        """Visit function calls to track usage and detect LLM API calls."""
        # Get the function name being called
        func_name = self._get_call_name(node)
        
        if func_name:
            # Track function call count
            self.function_calls[func_name] += 1
            
            # Check if this is an LLM API call
            if self._is_llm_api_call(func_name):
                if self.in_loop and self.current_function:
                    # LLM API call inside a loop
                    # Determine loop type by checking parent nodes
                    loop_type = 'for'  # Default, will be refined if needed
                    self.llm_calls_in_loops[self.current_function] = {
                        'line': node.lineno,
                        'loop_type': loop_type,
                        'api_call': func_name
                    }
        
        self.generic_visit(node)
    
    def _is_prompt_builder(self, func_name: str) -> bool:
        """Check if function name suggests it builds prompts."""
        func_lower = func_name.lower()
        return any(re.match(pattern, func_lower) for pattern in PromptEngineeringDetector.PROMPT_BUILDER_PATTERNS)
    
    def _is_llm_api_call(self, call_name: str) -> bool:
        """Check if this is an LLM API call."""
        return any(pattern in call_name for pattern in PromptEngineeringDetector.LLM_API_PATTERNS)
    
    def _get_call_name(self, node: ast.Call) -> Optional[str]:
        """Extract the full name of a function call."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        elif isinstance(node.func, ast.Attribute):
            # Handle chained attributes like bedrock.converse
            parts = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return '.'.join(reversed(parts))
        return None
    
    def _analyze_prompt_function(self, node: ast.FunctionDef) -> Optional[Dict[str, Any]]:
        """
        Analyze a function to determine if it builds prompts with static content.
        
        Returns info about static/dynamic content, or None if not a prompt builder.
        """
        # Look for f-strings, string concatenation, or large string literals
        has_f_string = False
        has_concatenation = False
        has_large_literal = False
        total_string_content_size = 0
        has_dynamic_content = False
        
        for child in ast.walk(node):
            # Check for f-strings (JoinedStr in Python 3.6+)
            if isinstance(child, ast.JoinedStr):
                has_f_string = True
                # Analyze f-string for static vs dynamic parts
                for value in child.values:
                    if isinstance(value, ast.Constant):
                        # Static part
                        total_string_content_size += len(str(value.value))
                    elif isinstance(value, ast.FormattedValue):
                        # Dynamic part
                        has_dynamic_content = True
            
            # Check for string concatenation
            elif isinstance(child, ast.BinOp) and isinstance(child.op, ast.Add):
                if isinstance(child.left, ast.Constant) or isinstance(child.right, ast.Constant):
                    has_concatenation = True
            
            # Check for large string literals (not part of f-strings)
            elif isinstance(child, ast.Constant) and isinstance(child.value, str):
                content_len = len(child.value)
                if content_len > 100:
                    has_large_literal = True
        
        # Estimate tokens (rough: 1 token ≈ 4 characters)
        estimated_static_tokens = total_string_content_size // 4
        
        # Consider it a prompt builder if:
        # 1. Has f-strings or concatenation or large literals (building prompts)
        # 2. Has ANY static content (even small amounts can benefit from caching if called frequently)
        has_any_static_content = estimated_static_tokens > 0
        has_large_static_content = estimated_static_tokens > 50
        
        if (has_f_string or has_concatenation or has_large_literal) and has_any_static_content:
            return {
                'line': node.lineno,
                'has_large_static_content': has_large_static_content,
                'has_dynamic_content': has_dynamic_content,
                'uses_f_string': has_f_string,
                'uses_concatenation': has_concatenation,
                'estimated_static_tokens': estimated_static_tokens
            }
        
        return None
