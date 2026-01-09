"""Test generic Bedrock model ID detection and parsing."""

import pytest
from mcp_cost_optim_genai.detectors.bedrock_detector import BedrockDetector


class TestGenericModelDetection:
    """Test that the detector can find ANY Bedrock model ID without hardcoding."""
    
    def setup_method(self):
        self.detector = BedrockDetector()
    
    def test_parse_claude_3_7_sonnet(self):
        """Test parsing Claude 3.7 Sonnet."""
        model_id = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
        parsed = self.detector._parse_model_id(model_id)
        
        assert parsed["provider"] == "anthropic"
        assert parsed["family"] == "claude"
        assert parsed["version"] == "3.7"
        assert parsed["tier"] == "sonnet"
        assert parsed["region_prefix"] == "us"
    
    def test_parse_claude_4_sonnet(self):
        """Test parsing Claude 4 Sonnet (newer model)."""
        model_id = "anthropic.claude-sonnet-4-20250514-v1:0"
        parsed = self.detector._parse_model_id(model_id)
        
        assert parsed["provider"] == "anthropic"
        assert parsed["family"] == "claude"
        assert parsed["version"] == "4"
        assert parsed["tier"] == "sonnet"
    
    def test_parse_claude_4_5_sonnet(self):
        """Test parsing Claude 4.5 Sonnet (even newer model)."""
        model_id = "anthropic.claude-sonnet-4-5-20250929-v1:0"
        parsed = self.detector._parse_model_id(model_id)
        
        assert parsed["provider"] == "anthropic"
        assert parsed["family"] == "claude"
        assert parsed["version"] == "4.5"
        assert parsed["tier"] == "sonnet"
    
    def test_parse_claude_haiku_4_5(self):
        """Test parsing Claude Haiku 4.5."""
        model_id = "anthropic.claude-haiku-4-5-20251001-v1:0"
        parsed = self.detector._parse_model_id(model_id)
        
        assert parsed["provider"] == "anthropic"
        assert parsed["family"] == "claude"
        assert parsed["version"] == "4.5"
        assert parsed["tier"] == "haiku"
    
    def test_parse_claude_opus_4_1(self):
        """Test parsing Claude Opus 4.1."""
        model_id = "anthropic.claude-opus-4-1-20250805-v1:0"
        parsed = self.detector._parse_model_id(model_id)
        
        assert parsed["provider"] == "anthropic"
        assert parsed["family"] == "claude"
        assert parsed["version"] == "4.1"
        assert parsed["tier"] == "opus"
    
    def test_parse_global_prefix(self):
        """Test parsing model with global. prefix (cross-region inference)."""
        model_id = "global.anthropic.claude-sonnet-4-20250514-v1:0"
        parsed = self.detector._parse_model_id(model_id)
        
        assert parsed["provider"] == "anthropic"
        assert parsed["family"] == "claude"
        assert parsed["version"] == "4"
        assert parsed["tier"] == "sonnet"
        assert parsed["region_prefix"] == "global"
    
    def test_parse_nova_pro(self):
        """Test parsing Amazon Nova Pro."""
        model_id = "amazon.nova-pro-v1:0"
        parsed = self.detector._parse_model_id(model_id)
        
        assert parsed["provider"] == "amazon"
        assert parsed["family"] == "nova"
        assert parsed["version"] == "1.0"
        assert parsed["tier"] == "pro"
    
    def test_parse_llama3(self):
        """Test parsing Meta Llama 3."""
        model_id = "meta.llama3-70b-instruct-v1:0"
        parsed = self.detector._parse_model_id(model_id)
        
        assert parsed["provider"] == "meta"
        assert parsed["family"] == "llama"
        assert parsed["version"] == "3.70"  # Note: includes size in version
        assert parsed["tier"] == "70b"
    
    def test_detect_model_in_code(self):
        """Test detecting model ID in actual code."""
        code = '''
bedrock_model = BedrockModel(
    model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
    temperature=0.1
)
'''
        findings = self.detector._detect_models(code, "test.py")
        
        assert len(findings) == 1
        assert findings[0]["model_id"] == "us.anthropic.claude-3-7-sonnet-20250219-v1"
        assert findings[0]["parsed"]["version"] == "3.7"
        assert findings[0]["parsed"]["tier"] == "sonnet"
    
    def test_enrichment_instructions_provided(self):
        """Test that enrichment instructions are provided for AI/Kiro."""
        code = 'model_id="anthropic.claude-3-7-sonnet-20250219-v1:0"'
        findings = self.detector._detect_models(code, "test.py")
        
        assert len(findings) == 1
        assert "enrichment_instructions" in findings[0]
        assert "steps" in findings[0]["enrichment_instructions"]
        
        # Check for enhanced instructions
        instructions = findings[0]["enrichment_instructions"]
        assert instructions["auto_execute"] is True
        assert instructions["priority"] == "high"
        assert "critical_notes" in instructions
        
        steps = instructions["steps"]
        assert any("AWS CLI MCP" in str(step) for step in steps)
        assert any("AWS Pricing MCP" in str(step) for step in steps)
        
        # Check for action_required field
        assert "action_required" in findings[0]
        assert findings[0]["action_required"]["priority"] == "high"
        assert "next_steps" in findings[0]["action_required"]
    
    def test_detect_multiple_models(self):
        """Test detecting multiple different models in same file."""
        code = '''
model1 = "anthropic.claude-3-7-sonnet-20250219-v1:0"
model2 = "amazon.nova-pro-v1:0"
model3 = "meta.llama3-70b-instruct-v1:0"
'''
        findings = self.detector._detect_models(code, "test.py")
        
        assert len(findings) == 3
        providers = [f["parsed"]["provider"] for f in findings]
        assert "anthropic" in providers
        assert "amazon" in providers
        assert "meta" in providers
    
    def test_future_model_detection(self):
        """Test that future models (not yet released) can be detected."""
        # Hypothetical future model
        code = 'model_id="anthropic.claude-opus-5-20260101-v1:0"'
        findings = self.detector._detect_models(code, "test.py")
        
        # Should still detect it even though we don't have hardcoded pattern
        assert len(findings) == 1
        assert findings[0]["parsed"]["provider"] == "anthropic"
        assert findings[0]["parsed"]["family"] == "claude"
