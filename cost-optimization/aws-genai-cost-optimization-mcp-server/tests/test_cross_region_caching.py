"""Test cross-region caching anti-pattern detection."""

import pytest
from mcp_cost_optim_genai.detectors.bedrock_detector import BedrockDetector


class TestCrossRegionCaching:
    """Test that cross-region inference + caching is properly detected as anti-pattern."""
    
    def setup_method(self):
        self.detector = BedrockDetector()
    
    def test_global_prefix_with_caching_static_prompts_ok(self):
        """Test that global. prefix + caching + static prompts = INFO (OK)."""
        code = '''
model_id = "global.anthropic.claude-sonnet-4-20250514-v1:0"
cache_control = {"type": "ephemeral"}
system_prompt = "You are a helpful assistant."  # Static, no variables
'''
        findings = self.detector._detect_caching_cross_region_antipattern(code, "test.py")
        
        assert len(findings) == 1
        assert findings[0]["severity"] == "info"  # Static prompts are OK
        assert findings[0]["profile_type"] == "global"
        assert findings[0]["prompt_analysis"]["is_static"] is True
    
    def test_global_prefix_with_caching_dynamic_prompts_high_risk(self):
        """Test that global. prefix + caching + dynamic prompts = HIGH RISK."""
        code = '''
model_id = "global.anthropic.claude-sonnet-4-20250514-v1:0"
cache_control = {"type": "ephemeral"}
user_input = get_user_input()
system_prompt = f"Process this: {user_input}"  # Dynamic, has variables
'''
        findings = self.detector._detect_caching_cross_region_antipattern(code, "test.py")
        
        assert len(findings) == 1
        assert findings[0]["severity"] == "high"  # Dynamic prompts are risky
        assert findings[0]["profile_type"] == "global"
        assert findings[0]["prompt_analysis"]["is_static"] is False
    
    def test_us_prefix_with_caching_static_ok(self):
        """Test that us. prefix + caching + static prompts = INFO (OK)."""
        code = '''
model_id = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
cachePoint = True
'''
        findings = self.detector._detect_caching_cross_region_antipattern(code, "test.py")
        
        assert len(findings) == 1
        assert findings[0]["severity"] == "info"  # Static prompts are OK
        assert findings[0]["profile_type"] == "geography-specific"
        assert findings[0]["region_prefix"] == "us"
    
    def test_us_prefix_with_caching_dynamic_medium_risk(self):
        """Test that us. prefix + caching + dynamic prompts = MEDIUM RISK."""
        code = '''
model_id = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
cachePoint = True
prompt = f"Process {user_data}"  # Dynamic
'''
        findings = self.detector._detect_caching_cross_region_antipattern(code, "test.py")
        
        assert len(findings) == 1
        assert findings[0]["severity"] == "medium"  # Dynamic prompts are risky
        assert findings[0]["profile_type"] == "geography-specific"
        assert findings[0]["region_prefix"] == "us"
    
    def test_eu_prefix_with_caching_static_ok(self):
        """Test that eu. prefix + caching + static prompts = INFO (OK)."""
        code = '''
model_id = "eu.amazon.nova-pro-v1:0"
cache_control = {"type": "ephemeral"}
'''
        findings = self.detector._detect_caching_cross_region_antipattern(code, "test.py")
        
        assert len(findings) == 1
        assert findings[0]["severity"] == "info"  # Static prompts are OK
        assert findings[0]["region_prefix"] == "eu"
    
    def test_apac_prefix_with_caching_static_ok(self):
        """Test that apac. prefix + caching + static prompts = INFO (OK)."""
        code = '''
model_id = "apac.meta.llama3-70b-instruct-v1:0"
cachePoint = True
'''
        findings = self.detector._detect_caching_cross_region_antipattern(code, "test.py")
        
        assert len(findings) == 1
        assert findings[0]["severity"] == "info"  # Static prompts are OK
        assert findings[0]["region_prefix"] == "apac"
    
    def test_no_prefix_with_caching_no_issue(self):
        """Test that single-region model + caching = NO ISSUE."""
        code = '''
model_id = "anthropic.claude-3-7-sonnet-20250219-v1:0"
cache_control = {"type": "ephemeral"}
'''
        findings = self.detector._detect_caching_cross_region_antipattern(code, "test.py")
        
        # No findings because no region prefix = single-region = safe with caching
        assert len(findings) == 0
    
    def test_cross_region_without_caching_no_issue(self):
        """Test that cross-region without caching = NO ISSUE."""
        code = '''
model_id = "global.anthropic.claude-sonnet-4-20250514-v1:0"
temperature = 0.1
'''
        findings = self.detector._detect_caching_cross_region_antipattern(code, "test.py")
        
        # No findings because no caching = no anti-pattern
        assert len(findings) == 0
    
    def test_multiple_cross_region_models_with_caching_static(self):
        """Test multiple cross-region models with caching and static prompts."""
        code = '''
model1 = "global.anthropic.claude-sonnet-4-20250514-v1:0"
model2 = "us.amazon.nova-pro-v1:0"
model3 = "anthropic.claude-3-7-sonnet-20250219-v1:0"
cache_control = {"type": "ephemeral"}
'''
        findings = self.detector._detect_caching_cross_region_antipattern(code, "test.py")
        
        # Should find 2 issues: global (info) and us (info) - both static
        # Should NOT flag the single-region model (model3)
        assert len(findings) == 2
        
        severities = [f["severity"] for f in findings]
        assert all(s == "info" for s in severities)  # All static, so info
    
    def test_multiple_cross_region_models_with_caching_dynamic(self):
        """Test multiple cross-region models with caching and dynamic prompts."""
        code = '''
model1 = "global.anthropic.claude-sonnet-4-20250514-v1:0"
model2 = "us.amazon.nova-pro-v1:0"
model3 = "anthropic.claude-3-7-sonnet-20250219-v1:0"
cache_control = {"type": "ephemeral"}
prompt = f"Process {data}"  # Dynamic prompt
'''
        findings = self.detector._detect_caching_cross_region_antipattern(code, "test.py")
        
        # Should find 2 issues: global (high) and us (medium) - both dynamic
        # Should NOT flag the single-region model (model3)
        assert len(findings) == 2
        
        severities = [f["severity"] for f in findings]
        assert "high" in severities  # global with dynamic
        assert "medium" in severities  # us with dynamic
    
    def test_model_detection_flags_cross_region(self):
        """Test that _detect_models properly flags cross-region models."""
        code = '''
model1 = "global.anthropic.claude-sonnet-4-20250514-v1:0"
model2 = "anthropic.claude-3-7-sonnet-20250219-v1:0"
'''
        findings = self.detector._detect_models(code, "test.py")
        
        assert len(findings) == 2
        
        # First model should be flagged as cross-region
        global_model = [f for f in findings if "global" in f["model_id"]][0]
        assert global_model["is_cross_region"] is True
        assert global_model["cross_region_type"] == "global"
        assert "cross_region_warning" in global_model
        
        # Second model should NOT be flagged as cross-region
        single_region_model = [f for f in findings if "global" not in f["model_id"]][0]
        assert single_region_model["is_cross_region"] is False
        assert single_region_model["cross_region_type"] is None
        assert "cross_region_warning" not in single_region_model
    
    def test_parsed_info_included_in_antipattern_finding(self):
        """Test that parsed model info is included in anti-pattern findings."""
        code = '''
model_id = "global.anthropic.claude-sonnet-4-20250514-v1:0"
cache_control = {"type": "ephemeral"}
'''
        findings = self.detector._detect_caching_cross_region_antipattern(code, "test.py")
        
        assert len(findings) == 1
        assert "parsed" in findings[0]
        
        parsed = findings[0]["parsed"]
        assert parsed["provider"] == "anthropic"
        assert parsed["family"] == "claude"
        assert parsed["tier"] == "sonnet"
        assert parsed["region_prefix"] == "global"
    
    def test_works_with_any_provider(self):
        """Test that cross-region detection works with any provider (not just anthropic/amazon)."""
        code = '''
model1 = "global.cohere.command-r-v1:0"
model2 = "us.mistral.mistral-large-v1:0"
cachePoint = True
'''
        findings = self.detector._detect_caching_cross_region_antipattern(code, "test.py")
        
        # Should detect both, regardless of provider
        assert len(findings) == 2
        
        providers = [f["parsed"]["provider"] for f in findings]
        assert "cohere" in providers
        assert "mistral" in providers
