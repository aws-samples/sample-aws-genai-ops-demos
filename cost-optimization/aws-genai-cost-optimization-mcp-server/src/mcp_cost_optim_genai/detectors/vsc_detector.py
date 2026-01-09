"""
VSC Optimization Detector - Identifies opportunities to use VSC format for maximum token efficiency.

VSC (Values Separated by Comma) is a hyper-minimal, token-optimized format that eliminates
all structural overhead. It can reduce token usage by up to 75% compared to JSON for flat, 
tabular data.

This detector finds:
1. JSON serialization in prompts (json.dumps, json.loads patterns)
2. Large JSON payloads being sent to LLMs
3. Repetitive data structures (lists of objects with same keys)
4. Data serialization before Bedrock API calls

VSC is ideal for:
- Flat, uniform, spreadsheet-like data
- Known schemas on both sides
- High-frequency workflows (RAG, agents, logs, analytics)
- Maximum cost-efficiency requirements

No keys, no nesting, no metadata, no structure - just pure values, comma-separated.
"""

import ast
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from collections import defaultdict

from .base import BaseDetector


class VscDetector(BaseDetector):
    """
    Detects opportunities to use VSC format instead of JSON for maximum token efficiency.
    
    VSC can save up to 75% tokens compared to JSON.
    Most beneficial for:
    - Flat, tabular data with uniform structure
    - Lists of objects with repeated keys
    - High-frequency LLM workflows
    - Known schemas (both sender and receiver understand structure)
    """
    
    # Patterns that indicate JSON usage
    JSON_PATTERNS = [
        r'json\.dumps\(',
        r'json\.loads\(',
        r'\.to_json\(',
        r'JSON\.stringify\(',
        r'JSON\.parse\(',
    ]

    
    # LLM API patterns (from prompt_engineering_detector)
    LLM_API_PATTERNS = [
        'bedrock.converse',
        'bedrock_runtime.converse',
        'bedrock.invoke_model',
        'bedrock_runtime.invoke_model',
        'bedrock_agentcore.invoke_agent_runtime',
        'invoke_agent_runtime',
        'openai.chat.completions.create',
        'anthropic.messages.create',
    ]
    
    def can_analyze(self, file_path: Path) -> bool:
        """Analyze Python and JavaScript/TypeScript files."""
        return file_path.suffix in ['.py', '.js', '.ts', '.jsx', '.tsx']
    
    def analyze(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Analyze code for VSC optimization opportunities."""
        findings = []
        
        if file_path.endswith('.py'):
            findings.extend(self._analyze_python(content, file_path))
        elif file_path.endswith(('.js', '.ts', '.jsx', '.tsx')):
            findings.extend(self._analyze_javascript(content, file_path))
        
        return findings
    
    def _analyze_python(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Analyze Python code for JSON usage patterns."""
        findings = []
        
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return findings
        
        analyzer = PythonVscAnalyzer(content, file_path)
        analyzer.visit(tree)
        
        # Generate findings from AST analysis
        findings.extend(self._generate_findings(analyzer, file_path))
        
        # Analyze prompts for embedded JSON patterns
        findings.extend(self._analyze_prompts_for_json(analyzer, file_path))
        
        return findings

    
    def _analyze_javascript(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Analyze JavaScript/TypeScript code using regex patterns."""
        findings = []
        lines = content.split('\n')
        
        json_stringify_lines = []
        llm_call_lines = []
        
        for i, line in enumerate(lines, 1):
            if 'JSON.stringify' in line:
                json_stringify_lines.append(i)
            
            for pattern in self.LLM_API_PATTERNS:
                if pattern in line:
                    llm_call_lines.append(i)
                    break
        
        for json_line in json_stringify_lines:
            for llm_line in llm_call_lines:
                if abs(json_line - llm_line) <= 10:
                    findings.append({
                        'type': 'json_serialization_near_llm_call',
                        'file': file_path,
                        'line': json_line,
                        'service': 'bedrock',
                        'description': f"JSON.stringify used near LLM API call (line {llm_line})",
                        'cost_consideration': "JSON serialization adds massive token overhead. VSC format can reduce tokens by up to 75% for flat, tabular data.",
                        'optimization': {
                            'technique': 'VSC (Values Separated by Comma)',
                            'potential_savings': 'Up to 75% token reduction vs JSON',
                            'implementation': 'Replace JSON.stringify with VSC serialization',
                            'use_when': 'Flat, uniform data with known schema on both sides'
                        }
                    })
                    break
        
        return findings

    
    def _generate_findings(self, analyzer: 'PythonVscAnalyzer', file_path: str) -> List[Dict[str, Any]]:
        """Generate findings from Python AST analysis."""
        findings = []
        
        for json_info in analyzer.json_serializations:
            for llm_info in analyzer.llm_calls:
                if abs(json_info['line'] - llm_info['line']) <= 10:
                    estimated_tokens = self._estimate_json_tokens(json_info)
                    vsc_savings = int(estimated_tokens * 0.65)
                    
                    findings.append({
                        'type': 'json_serialization_near_llm_call',
                        'file': file_path,
                        'line': json_info['line'],
                        'service': 'bedrock',
                        'description': f"json.dumps() used near LLM API call (line {llm_info['line']})",
                        'cost_consideration': f"JSON serialization adds token overhead. Estimated ~{estimated_tokens} tokens could be reduced to ~{estimated_tokens - vsc_savings} with VSC.",
                        'optimization': {
                            'technique': 'VSC (Values Separated by Comma)',
                            'potential_savings': f'~{vsc_savings} tokens (up to 75% reduction for flat data)',
                            'implementation': 'Replace json.dumps() with VSC serialization',
                            'use_when': 'Flat, tabular data with known schema',
                            'example': self._generate_vsc_example()
                        },
                        'estimated_token_savings': vsc_savings
                    })
                    break
        
        for list_pattern in analyzer.list_patterns:
            if list_pattern['is_repetitive']:
                findings.append({
                    'type': 'repetitive_data_structure',
                    'file': file_path,
                    'line': list_pattern['line'],
                    'service': 'bedrock',
                    'description': f"Repetitive data structure detected - ideal candidate for VSC format",
                    'cost_consideration': "Repetitive JSON structures with repeated keys waste massive tokens. VSC eliminates ALL structural overhead.",
                    'optimization': {
                        'technique': 'VSC format for tabular data',
                        'potential_savings': 'Up to 75% token reduction for flat structures',
                        'recommendation': 'Convert list of dicts to VSC format before sending to LLM',
                        'use_when': 'Spreadsheet-like data, uniform structure, known schema'
                    }
                })
        
        return findings

    
    def _estimate_json_tokens(self, json_info: Dict[str, Any]) -> int:
        """Estimate token count for JSON serialization (rough heuristic)."""
        return 100
    
    def _analyze_prompts_for_json(self, analyzer: 'PythonVscAnalyzer', file_path: str) -> List[Dict[str, Any]]:
        """Analyze prompts (system_prompt, user prompts) for embedded JSON patterns."""
        findings = []
        
        for prompt_info in analyzer.prompts:
            prompt_text = prompt_info['text']
            line = prompt_info['line']
            prompt_type = prompt_info['type']
            
            json_patterns = self._find_json_patterns_in_text(prompt_text)
            
            if json_patterns:
                total_json_chars = sum(p['length'] for p in json_patterns)
                estimated_tokens = total_json_chars // 4
                vsc_savings = int(estimated_tokens * 0.70)
                
                findings.append({
                    'type': 'json_schema_in_prompt',
                    'file': file_path,
                    'line': line,
                    'service': 'bedrock',
                    'prompt_type': prompt_type,
                    'description': f"JSON schema/example embedded in {prompt_type}. This is sent to LLM on every request.",
                    'cost_consideration': f"JSON schemas in prompts waste tokens on every request. Estimated ~{estimated_tokens} tokens could be reduced to ~{estimated_tokens - vsc_savings} with VSC format.",
                    'optimization': {
                        'technique': 'VSC format for schema definition',
                        'potential_savings': f'~{vsc_savings} tokens per request (up to 75% reduction)',
                        'implementation': 'Replace JSON schema with VSC format in prompt',
                        'use_when': 'Flat schema, known structure on both sides',
                        'example': self._generate_schema_vsc_example()
                    },
                    'estimated_token_savings': vsc_savings,
                    'json_patterns_found': len(json_patterns)
                })

            
            variables = self._find_variables_in_prompt(prompt_text)
            for var in variables:
                if self._is_json_variable(var, analyzer):
                    findings.append({
                        'type': 'json_variable_in_prompt',
                        'file': file_path,
                        'line': line,
                        'service': 'bedrock',
                        'variable': var,
                        'description': f"Variable '{var}' in prompt may contain JSON data",
                        'cost_consideration': "If this variable contains flat, tabular JSON, VSC format can dramatically reduce token usage.",
                        'optimization': {
                            'technique': 'VSC format for data variables',
                            'recommendation': f"Convert '{var}' to VSC format before inserting into prompt",
                            'use_when': 'Variable contains flat, uniform data with known schema'
                        }
                    })
        
        return findings

    
    def _find_json_patterns_in_text(self, text: str) -> List[Dict[str, Any]]:
        """Find JSON-like patterns in text (schemas, examples, etc.)."""
        patterns = []
        
        json_object_pattern = r'\{[^{}]*"[^"]+"\s*:\s*[^{}]+\}'
        for match in re.finditer(json_object_pattern, text):
            patterns.append({
                'type': 'json_object',
                'text': match.group(0),
                'start': match.start(),
                'length': len(match.group(0))
            })
        
        schema_field_pattern = r'-\s*"([^"]+)":\s*([^\n]+)'
        schema_fields = list(re.finditer(schema_field_pattern, text))
        if len(schema_fields) >= 3:
            total_length = sum(len(m.group(0)) for m in schema_fields)
            patterns.append({
                'type': 'json_schema_fields',
                'field_count': len(schema_fields),
                'length': total_length
            })
        
        key_pattern = r'"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:'
        keys = re.findall(key_pattern, text)
        if keys:
            key_counts = {}
            for key in keys:
                key_counts[key] = key_counts.get(key, 0) + 1
            
            repeated_keys = [k for k, count in key_counts.items() if count >= 3]
            if repeated_keys:
                patterns.append({
                    'type': 'repetitive_keys',
                    'repeated_keys': repeated_keys,
                    'length': len(text) // 2
                })
        
        return patterns

    
    def _find_variables_in_prompt(self, prompt_text: str) -> List[str]:
        """Find f-string variables in prompt text."""
        variables = re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', prompt_text)
        return list(set(variables))
    
    def _is_json_variable(self, var_name: str, analyzer: 'PythonVscAnalyzer') -> bool:
        """Check if a variable likely contains JSON data."""
        for json_info in analyzer.json_serializations:
            target_var = json_info.get('target_var')
            if target_var and var_name in target_var:
                return True
        
        json_indicators = ['json', 'data', 'payload', 'schema', 'config']
        return any(indicator in var_name.lower() for indicator in json_indicators)
    
    def _generate_schema_vsc_example(self) -> str:
        """Generate a VSC vs JSON schema comparison example."""
        return """
# JSON Schema in Prompt (verbose - 159 tokens):
{
    "service": "string",
    "cycle": "string",
    "lts": "bool",
    "releaseDate": "YYYY-MM-DD",
    "eol": "YYYY-MM-DD"
}

# VSC Schema in Prompt (hyper-minimal - ~40 tokens, 75% savings):
service,cycle,lts,releaseDate,eol

# VSC is just the values, comma-separated. Schema known by both sides.
"""
    
    def _generate_vsc_example(self) -> str:
        """Generate a VSC vs JSON comparison example."""
        return """
# JSON (89 tokens):
{"users": [{"id": 1, "name": "Alice", "role": "admin"}, {"id": 2, "name": "Bob", "role": "user"}]}

# VSC (22 tokens - 75% savings):
1,Alice,admin
2,Bob,user

# No keys, no structure, just pure values. Schema known by both sides.
"""



class PythonVscAnalyzer(ast.NodeVisitor):
    """AST visitor to find JSON usage patterns in Python code."""
    
    def __init__(self, content: str, file_path: str):
        self.content = content
        self.file_path = file_path
        self.lines = content.split('\n')
        self.json_serializations: List[Dict[str, Any]] = []
        self.llm_calls: List[Dict[str, Any]] = []
        self.list_patterns: List[Dict[str, Any]] = []
        self.prompts: List[Dict[str, Any]] = []
        self.current_function: Optional[str] = None
    
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Track current function context."""
        old_function = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = old_function
    
    def visit_Call(self, node: ast.Call) -> None:
        """Visit function calls to find json.dumps, LLM API calls, and Agent creation."""
        call_name = self._get_call_name(node)
        
        if call_name:
            if 'json.dumps' in call_name or 'to_json' in call_name:
                target_var = None
                if hasattr(node, 'parent') and isinstance(node.parent, ast.Assign):
                    if node.parent.targets and isinstance(node.parent.targets[0], ast.Name):
                        target_var = node.parent.targets[0].id
                
                self.json_serializations.append({
                    'line': node.lineno,
                    'call': call_name,
                    'function': self.current_function,
                    'target_var': target_var
                })
            
            if any(pattern in call_name for pattern in VscDetector.LLM_API_PATTERNS):
                self.llm_calls.append({
                    'line': node.lineno,
                    'call': call_name,
                    'function': self.current_function
                })
            
            if 'Agent' in call_name:
                self._extract_prompts_from_agent(node)
        
        self.generic_visit(node)

    
    def _extract_prompts_from_agent(self, node: ast.Call) -> None:
        """Extract system_prompt and other prompts from Agent() calls."""
        for keyword in node.keywords:
            if keyword.arg == 'system_prompt':
                prompt_text = self._extract_string_value(keyword.value)
                if prompt_text:
                    self.prompts.append({
                        'line': node.lineno,
                        'type': 'system_prompt',
                        'text': prompt_text,
                        'length': len(prompt_text)
                    })
    
    def _extract_string_value(self, node: ast.AST) -> Optional[str]:
        """Extract string value from AST node (handles f-strings, concatenation, etc.)."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        elif isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant):
                    parts.append(str(value.value))
                elif isinstance(value, ast.FormattedValue):
                    if isinstance(value.value, ast.Name):
                        parts.append(f'{{{value.value.id}}}')
                    else:
                        parts.append('{...}')
            return ''.join(parts)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._extract_string_value(node.left)
            right = self._extract_string_value(node.right)
            if left and right:
                return left + right
        return None
    
    def visit_ListComp(self, node: ast.ListComp) -> None:
        """Detect list comprehensions that might create repetitive structures."""
        if isinstance(node.elt, ast.Dict):
            self.list_patterns.append({
                'line': node.lineno,
                'is_repetitive': True,
                'type': 'list_comprehension'
            })
        
        self.generic_visit(node)
    
    def _get_call_name(self, node: ast.Call) -> Optional[str]:
        """Extract the full name of a function call."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        elif isinstance(node.func, ast.Attribute):
            parts = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return '.'.join(reversed(parts))
        return None
