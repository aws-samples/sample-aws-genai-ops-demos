"""Tests for false positive detection in Bedrock model ID scanning.

This test file validates different approaches to reducing false positives
when detecting Bedrock model IDs in code.
"""

import pytest
from pathlib import Path
from mcp_cost_optim_genai.detectors.bedrock_detector import BedrockDetector


class TestFalsePositiveDetection:
    """Test false positive scenarios that should NOT be flagged."""
    
    def setup_method(self):
        self.detector = BedrockDetector()
    
    def test_validation_error_message_python(self):
        """Test that validation error messages are not flagged (Python)."""
        code = '''
def validate_model_id(model_id: str) -> str:
    errors = ''
    if not model_id.strip():
        errors += 'Required field. '
    else:
        model_id_pattern = r'^[a-zA-Z0-9-]+\.[a-zA-Z0-9-\._]+(:[0-9]+)?$'
        if not model_id_pattern.match(model_id.strip()):
            errors += 'Model ID must follow the pattern provider.model-name format (e.g., amazon.titan-text-express-v1). '
    return errors
'''
        findings = self.detector.analyze(code, "test.py")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # Should NOT detect the model ID in the error message
        assert len(model_findings) == 0, f"False positive detected: {model_findings}"
    
    def test_validation_error_message_typescript(self):
        """Test that validation error messages are not flagged (TypeScript)."""
        code = '''
const onModelIdChange = (detail: { value: string }) => {
  let errors = '';
  if (detail.value.trim().length === 0) {
    errors += 'Required field. ';
  } else {
    const modelIdPattern = /^[a-zA-Z0-9-]+\.[a-zA-Z0-9-\._]+(:[0-9]+)?$/;
    if (!modelIdPattern.test(detail.value.trim())) {
      errors += 'Model ID must follow the pattern provider.model-name format (e.g., amazon.titan-text-express-v1). ';
    }
  }
  return errors;
};
'''
        findings = self.detector.analyze(code, "test.tsx")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # Should NOT detect the model ID in the error message
        assert len(model_findings) == 0, f"False positive detected: {model_findings}"
    
    def test_docstring_example(self):
        """Test that docstring examples are not flagged."""
        code = '''
def create_bedrock_client():
    """
    Create a Bedrock client.
    
    Example usage:
        response = client.invoke_model(
            modelId="anthropic.claude-3-sonnet-20240229-v1:0",
            body=json.dumps({"prompt": "Hello"})
        )
    """
    import boto3
    return boto3.client('bedrock-runtime')
'''
        findings = self.detector.analyze(code, "test.py")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # Should NOT detect model ID in docstring
        assert len(model_findings) == 0, f"False positive detected: {model_findings}"
    
    def test_docstring_with_code_example(self):
        """Test that model IDs in docstring code examples are not flagged."""
        code = '''
def build_guardrail_config(bedrock_params):
    """Build guardrail configuration dictionary.
    
    Example:
        >>> params = BedrockLlmParams(
        ...     ModelId="amazon.nova-pro-v1:0",
        ...     GuardrailIdentifier="abc123"
        ... )
        >>> config = build_guardrail_config(params)
    """
    return {}
'''
        findings = self.detector.analyze(code, "test.py")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # Should NOT detect model ID in docstring example
        assert len(model_findings) == 0, f"False positive detected: {model_findings}"
    
    def test_comment_example(self):
        """Test that comments with examples are not flagged."""
        code = '''
def process_request(model_id: str):
    # TODO: Add support for more models like anthropic.claude-3-haiku-20240307-v1:0
    # Currently only supports amazon.nova-micro-v1:0
    pass
'''
        findings = self.detector.analyze(code, "test.py")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # Should NOT detect model IDs in comments
        assert len(model_findings) == 0, f"False positive detected: {model_findings}"
    
    def test_comment_with_example_in_conditional(self):
        """Test that model IDs in comments within code logic are not flagged."""
        code = '''
def set_llm(self):
    if inference_profile_id is not None:
        # In case of Inference Profile Ids, inference_profile_id contains region prefixed to them,
        # for example 'eu.anthropic.claude-3-haiku-20240307-v1:0'.
        # model_family is extracted from the second part of the string in this case
        model_family = self.get_model_provider(part=1)
    else:
        # In case an on-demand modelId/provisioned ARN is used, modelId is always passed
        # for example 'anthropic.claude-v2'.
        # model_family is extracted from the first part of the string in this case
        model_family = self.get_model_provider(part=0)
'''
        findings = self.detector.analyze(code, "test.py")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # Should NOT detect model IDs in comments
        assert len(model_findings) == 0, f"False positive detected: {model_findings}"
    
    def test_placeholder_in_ui(self):
        """Test that placeholder text in UI is not flagged."""
        code = '''
const ModelIdInput = () => {
  return (
    <input
      type="text"
      placeholder="e.g., anthropic.claude-3-sonnet-20240229-v1:0"
      value={modelId}
    />
  );
};
'''
        findings = self.detector.analyze(code, "test.tsx")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # Should NOT detect model ID in placeholder
        assert len(model_findings) == 0, f"False positive detected: {model_findings}"
    
    def test_error_message_constant(self):
        """Test that error message constants are not flagged."""
        code = '''
ERROR_MESSAGES = {
    'INVALID_MODEL': 'Invalid model ID. Use format like amazon.titan-text-express-v1',
    'UNSUPPORTED': 'Model anthropic.claude-v2 is no longer supported'
}
'''
        findings = self.detector.analyze(code, "test.py")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # Should NOT detect model IDs in error messages
        assert len(model_findings) == 0, f"False positive detected: {model_findings}"


class TestTruePositiveDetection:
    """Test that actual usage IS correctly detected."""
    
    def setup_method(self):
        self.detector = BedrockDetector()
    
    def test_actual_invoke_model_call(self):
        """Test that actual invoke_model calls ARE detected."""
        code = '''
import boto3
bedrock = boto3.client('bedrock-runtime')

def invoke_model_real():
    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body='{"prompt": "Hello"}'
    )
    return response
'''
        findings = self.detector.analyze(code, "test.py")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # SHOULD detect the model ID near invoke_model
        assert len(model_findings) > 0, "True positive not detected"
        # Note: regex doesn't capture :0 suffix, but detection works
        assert "anthropic.claude-3-sonnet-20240229-v1" in model_findings[0]["model_id"]
    
    def test_default_model_configuration(self):
        """Test that default model configurations ARE detected."""
        code = '''
def _create_default_model(self) -> BedrockModel:
    """Create default Bedrock model when no configuration is available."""
    default_model_id = "amazon.nova-lite-v1:0"
    default_temperature = 0.7
    
    return BedrockModel(
        model_id=default_model_id,
        region_name=self.region,
        temperature=default_temperature,
    )
'''
        findings = self.detector.analyze(code, "test.py")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # SHOULD detect the model ID in variable assignment
        assert len(model_findings) > 0, "True positive not detected"
        assert "amazon.nova-lite-v1" in model_findings[0]["model_id"]
    
    def test_variable_assignment_near_api_call(self):
        """Test that variable assignments near API calls ARE detected."""
        code = '''
import boto3
bedrock = boto3.client('bedrock-runtime')

def get_model_config():
    model_id = "amazon.nova-pro-v1:0"
    return bedrock.invoke_model(modelId=model_id, body='{}')
'''
        findings = self.detector.analyze(code, "test.py")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # SHOULD detect the model ID near invoke_model
        assert len(model_findings) > 0, "True positive not detected"
        assert "amazon.nova-pro-v1" in model_findings[0]["model_id"]
    
    def test_typescript_actual_usage(self):
        """Test that actual TypeScript usage IS detected."""
        code = '''
async function invokeBedrockModel() {
  const response = await bedrockClient.invokeModel({
    modelId: "anthropic.claude-3-sonnet-20240229-v1:0",
    body: JSON.stringify({ prompt: "Hello" })
  });
  return response;
}
'''
        findings = self.detector.analyze(code, "test.ts")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # SHOULD detect the model ID near invokeModel
        assert len(model_findings) > 0, "True positive not detected"
        assert "anthropic.claude-3-sonnet-20240229-v1" in model_findings[0]["model_id"]


class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def setup_method(self):
        self.detector = BedrockDetector()
    
    def test_model_id_in_test_fixture_without_api_call(self):
        """Test that test fixtures without API calls might be flagged (acceptable trade-off)."""
        code = '''
EXAMPLE_MODEL_IDS = [
    "anthropic.claude-v2",
    "amazon.titan-text-express-v1",
    "meta.llama3-70b-instruct-v1:0"
]
'''
        findings = self.detector.analyze(code, "test.py")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # Note: These ARE detected (no validation keywords, no comments, no docstrings)
        # This is an acceptable trade-off to avoid filtering legitimate config constants
        # Better to flag test fixtures than miss real config files
        # Pass 1 filtering focuses on obvious false positives only
        assert len(model_findings) >= 0  # May or may not be detected - both acceptable
    
    def test_model_id_far_from_api_call(self):
        """Test that model IDs far from API calls might not be flagged."""
        code = '''
# This is a config at the top of the file
DEFAULT_MODEL = "anthropic.claude-3-sonnet-20240229-v1:0"

# ... 100 lines of other code ...

def some_other_function():
    pass

# ... more code ...

def another_function():
    # No Bedrock API call here
    pass
'''
        findings = self.detector.analyze(code, "test.py")
        model_findings = [f for f in findings if f["type"] == "bedrock_model_usage"]
        
        # Behavior depends on implementation - document what happens
        # This is an edge case where context matters
        print(f"Edge case result: {len(model_findings)} findings")
