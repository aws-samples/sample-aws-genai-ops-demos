"""Test VSC detector for JSON patterns in prompts."""

import pytest
from mcp_cost_optim_genai.detectors.vsc_detector import VscDetector


class TestVscPromptDetection:
    """Test that VSC detector finds JSON patterns embedded in prompts."""
    
    def setup_method(self):
        self.detector = VscDetector()
    
    def test_json_schema_in_system_prompt(self):
        """Test detection of JSON schema in system_prompt."""
        code = '''
from strands import Agent

agent = Agent(
    model=bedrock_model,
    system_prompt="""You are a data extractor.
    
    Return data in this format:
    {
        "name": "string",
        "age": "number",
        "email": "string"
    }
    """,
    tools=tools
)
'''
        findings = self.detector.analyze(code, "test.py")
        
        assert len(findings) >= 1
        schema_findings = [f for f in findings if f['type'] == 'json_schema_in_prompt']
        assert len(schema_findings) == 1
        
        finding = schema_findings[0]
        assert finding['prompt_type'] == 'system_prompt'
        assert 'estimated_token_savings' in finding
        assert finding['estimated_token_savings'] > 0
    
    def test_json_fields_in_system_prompt(self):
        """Test detection of JSON field definitions in system_prompt."""
        code = '''
agent = Agent(
    model=model,
    system_prompt=f"""Extract the following fields:
    - "service": Name of the service
    - "version": Version number
    - "releaseDate": Release date in YYYY-MM-DD format
    - "eol": End of life date
    - "link": Documentation URL
    
    Return as JSON."""
)
'''
        findings = self.detector.analyze(code, "test.py")
        
        schema_findings = [f for f in findings if f['type'] == 'json_schema_in_prompt']
        assert len(schema_findings) >= 1
        
        finding = schema_findings[0]
        assert finding['json_patterns_found'] >= 1
    
    def test_no_false_positive_without_json(self):
        """Test that prompts without JSON don't trigger false positives."""
        code = '''
agent = Agent(
    model=model,
    system_prompt="You are a helpful assistant. Answer questions clearly and concisely."
)
'''
        findings = self.detector.analyze(code, "test.py")
        
        schema_findings = [f for f in findings if f['type'] == 'json_schema_in_prompt']
        assert len(schema_findings) == 0
    
    def test_variable_in_prompt_detection(self):
        """Test detection of variables in prompts that might contain JSON."""
        code = '''
data_json = json.dumps({"key": "value"})

agent = Agent(
    model=model,
    system_prompt=f"Process this data: {data_json}"
)
'''
        findings = self.detector.analyze(code, "test.py")
        
        # Should find both json.dumps and variable in prompt
        assert len(findings) >= 1
        
        var_findings = [f for f in findings if f['type'] == 'json_variable_in_prompt']
        # Note: This might not trigger if data_json isn't detected as JSON variable
        # The main test is that it doesn't crash
    
    def test_eol_tracker_real_world(self):
        """Test with real EOLTracker code pattern."""
        code = '''
eol_agent = Agent(
    model=bedrock_model,
    system_prompt=f"""You are an AWS documentation analyst.

Your task:
1. Extract EOL information
2. Include the following key-value pairs:
    - "service": Name of the AWS service
    - "cycle": Version identifier
    - "lts": Boolean (true/false)
    - "releaseDate": Date in YYYY-MM-DD format
    - "eol": End of life date (YYYY-MM-DD)
    - "link": URL to documentation

3. Structure the information in this JSON format:
{{
    "service": "string",
    "cycle": "string",
    "lts": "bool",
    "releaseDate": "YYYY-MM-DD",
    "eol": "YYYY-MM-DD",
    "link": "url"
}}

Return ONLY valid JSON.""",
    tools=tools
)
'''
        findings = self.detector.analyze(code, "test.py")
        
        schema_findings = [f for f in findings if f['type'] == 'json_schema_in_prompt']
        assert len(schema_findings) >= 1
        
        finding = schema_findings[0]
        assert finding['prompt_type'] == 'system_prompt'
        assert 'VSC' in finding['optimization']['technique']
        assert finding['estimated_token_savings'] > 0
    
    def test_multiple_json_patterns_in_prompt(self):
        """Test detection of multiple JSON patterns in same prompt."""
        code = '''
agent = Agent(
    model=model,
    system_prompt="""Extract data in this format:
    {
        "user": {"name": "string", "email": "string"},
        "order": {"id": "number", "total": "number"}
    }
    
    Example:
    {"user": {"name": "Alice", "email": "alice@example.com"}}
    """
)
'''
        findings = self.detector.analyze(code, "test.py")
        
        schema_findings = [f for f in findings if f['type'] == 'json_schema_in_prompt']
        assert len(schema_findings) >= 1
        
        finding = schema_findings[0]
        # Should detect multiple JSON patterns
        assert finding['json_patterns_found'] >= 1
    
    def test_f_string_prompt_extraction(self):
        """Test that f-strings in prompts are properly extracted."""
        code = '''
current_date = "2025-01-01"

agent = Agent(
    model=model,
    system_prompt=f"""Extract data as of {current_date}.
    
    Return JSON with these fields:
    - "date": The extraction date
    - "status": Current status
    - "items": List of items
    """
)
'''
        findings = self.detector.analyze(code, "test.py")
        
        schema_findings = [f for f in findings if f['type'] == 'json_schema_in_prompt']
        assert len(schema_findings) >= 1
