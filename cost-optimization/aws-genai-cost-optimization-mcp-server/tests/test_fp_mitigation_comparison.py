"""Compare different false positive mitigation approaches."""

import pytest
from mcp_cost_optim_genai.detectors.bedrock_detector import BedrockDetector
from mcp_cost_optim_genai.detectors.bedrock_detector_with_fp_mitigation import (
    apply_approach_1_context_aware,
    apply_approach_2_usage_context,
    apply_approach_3_combined,
)


class TestApproachComparison:
    """Compare all three mitigation approaches."""
    
    def setup_method(self):
        self.detector = BedrockDetector()
    
    def _get_model_findings(self, findings):
        return [f for f in findings if f["type"] == "bedrock_model_usage"]
    
    def test_validation_error_message(self):
        """Test: Validation error message (user's actual case)."""
        code = '''
def validate_model_id(model_id: str) -> str:
    errors = ''
    if not model_id.strip():
        errors += 'Required field. '
    else:
        if not pattern.match(model_id.strip()):
            errors += 'Model ID must follow the pattern provider.model-name format (e.g., amazon.titan-text-express-v1). '
    return errors
'''
        # Baseline: Current detector
        baseline = self.detector.analyze(code, "test.py")
        baseline_models = self._get_model_findings(baseline)
        
        # Approach 1: Context-aware
        approach1 = apply_approach_1_context_aware(baseline, code)
        approach1_models = self._get_model_findings(approach1)
        
        # Approach 2: Usage context
        approach2 = apply_approach_2_usage_context(baseline, code)
        approach2_models = self._get_model_findings(approach2)
        
        # Approach 3: Combined
        approach3 = apply_approach_3_combined(baseline, code)
        approach3_models = self._get_model_findings(approach3)
        
        print(f"\n=== Validation Error Message ===")
        print(f"Baseline: {len(baseline_models)} findings")
        print(f"Approach 1 (Context-aware): {len(approach1_models)} findings")
        print(f"Approach 2 (Usage context): {len(approach2_models)} findings")
        print(f"Approach 3 (Combined): {len(approach3_models)} findings")
        
        # Expected: 0 findings (false positive)
        assert len(baseline_models) == 1, "Baseline should detect 1 (false positive)"
        assert len(approach1_models) == 0, "Approach 1 should filter it out"
        assert len(approach2_models) == 0, "Approach 2 should filter it out"
        assert len(approach3_models) == 0, "Approach 3 should filter it out"
    
    def test_actual_usage(self):
        """Test: Actual Bedrock usage (true positive)."""
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
        baseline = self.detector.analyze(code, "test.py")
        baseline_models = self._get_model_findings(baseline)
        
        approach1 = apply_approach_1_context_aware(baseline, code)
        approach1_models = self._get_model_findings(approach1)
        
        approach2 = apply_approach_2_usage_context(baseline, code)
        approach2_models = self._get_model_findings(approach2)
        
        approach3 = apply_approach_3_combined(baseline, code)
        approach3_models = self._get_model_findings(approach3)
        
        print(f"\n=== Actual Usage ===")
        print(f"Baseline: {len(baseline_models)} findings")
        print(f"Approach 1 (Context-aware): {len(approach1_models)} findings")
        print(f"Approach 2 (Usage context): {len(approach2_models)} findings")
        print(f"Approach 3 (Combined): {len(approach3_models)} findings")
        
        # Expected: 1 finding (true positive)
        assert len(baseline_models) >= 1, "Baseline should detect it"
        assert len(approach1_models) >= 1, "Approach 1 should keep it"
        assert len(approach2_models) >= 1, "Approach 2 should keep it"
        assert len(approach3_models) >= 1, "Approach 3 should keep it"
    
    def test_comment_example(self):
        """Test: Model ID in comment."""
        code = '''
def process_request(model_id: str):
    # TODO: Add support for more models like anthropic.claude-3-haiku-20240307-v1:0
    pass
'''
        baseline = self.detector.analyze(code, "test.py")
        baseline_models = self._get_model_findings(baseline)
        
        approach1 = apply_approach_1_context_aware(baseline, code)
        approach1_models = self._get_model_findings(approach1)
        
        approach2 = apply_approach_2_usage_context(baseline, code)
        approach2_models = self._get_model_findings(approach2)
        
        approach3 = apply_approach_3_combined(baseline, code)
        approach3_models = self._get_model_findings(approach3)
        
        print(f"\n=== Comment Example ===")
        print(f"Baseline: {len(baseline_models)} findings")
        print(f"Approach 1 (Context-aware): {len(approach1_models)} findings")
        print(f"Approach 2 (Usage context): {len(approach2_models)} findings")
        print(f"Approach 3 (Combined): {len(approach3_models)} findings")
        
        # Expected: 0 findings (false positive)
        assert len(baseline_models) >= 1, "Baseline should detect it (false positive)"
        assert len(approach1_models) == 0, "Approach 1 should filter it out"
        assert len(approach2_models) == 0, "Approach 2 should filter it out"
        assert len(approach3_models) == 0, "Approach 3 should filter it out"
    
    def test_test_fixture_without_api_call(self):
        """Test: Test fixture without API call."""
        code = '''
EXAMPLE_MODEL_IDS = [
    "anthropic.claude-v2",
    "amazon.titan-text-express-v1"
]
'''
        baseline = self.detector.analyze(code, "test.py")
        baseline_models = self._get_model_findings(baseline)
        
        approach1 = apply_approach_1_context_aware(baseline, code)
        approach1_models = self._get_model_findings(approach1)
        
        approach2 = apply_approach_2_usage_context(baseline, code)
        approach2_models = self._get_model_findings(approach2)
        
        approach3 = apply_approach_3_combined(baseline, code)
        approach3_models = self._get_model_findings(approach3)
        
        print(f"\n=== Test Fixture ===")
        print(f"Baseline: {len(baseline_models)} findings")
        print(f"Approach 1 (Context-aware): {len(approach1_models)} findings")
        print(f"Approach 2 (Usage context): {len(approach2_models)} findings")
        print(f"Approach 3 (Combined): {len(approach3_models)} findings")
        
        # Expected: 0 findings (false positive - no API call nearby)
        assert len(baseline_models) >= 2, "Baseline should detect them (false positives)"
        # Approach 1 might keep them (no validation keywords)
        assert len(approach2_models) == 0, "Approach 2 should filter them out (no API call)"
        assert len(approach3_models) == 0, "Approach 3 should filter them out"
    
    def test_variable_near_api_call(self):
        """Test: Variable assignment near API call (true positive)."""
        code = '''
import boto3
bedrock = boto3.client('bedrock-runtime')

def get_model_config():
    model_id = "amazon.nova-pro-v1:0"
    return bedrock.invoke_model(modelId=model_id, body='{}')
'''
        baseline = self.detector.analyze(code, "test.py")
        baseline_models = self._get_model_findings(baseline)
        
        approach1 = apply_approach_1_context_aware(baseline, code)
        approach1_models = self._get_model_findings(approach1)
        
        approach2 = apply_approach_2_usage_context(baseline, code)
        approach2_models = self._get_model_findings(approach2)
        
        approach3 = apply_approach_3_combined(baseline, code)
        approach3_models = self._get_model_findings(approach3)
        
        print(f"\n=== Variable Near API Call ===")
        print(f"Baseline: {len(baseline_models)} findings")
        print(f"Approach 1 (Context-aware): {len(approach1_models)} findings")
        print(f"Approach 2 (Usage context): {len(approach2_models)} findings")
        print(f"Approach 3 (Combined): {len(approach3_models)} findings")
        
        # Expected: 1 finding (true positive)
        assert len(baseline_models) >= 1, "Baseline should detect it"
        assert len(approach1_models) >= 1, "Approach 1 should keep it"
        assert len(approach2_models) >= 1, "Approach 2 should keep it (API call nearby)"
        assert len(approach3_models) >= 1, "Approach 3 should keep it"
    
    def test_docstring_example(self):
        """Test: Model ID in docstring."""
        code = '''
def create_bedrock_client():
    """
    Example usage:
        response = client.invoke_model(
            modelId="anthropic.claude-3-sonnet-20240229-v1:0",
            body=json.dumps({"prompt": "Hello"})
        )
    """
    import boto3
    return boto3.client('bedrock-runtime')
'''
        baseline = self.detector.analyze(code, "test.py")
        baseline_models = self._get_model_findings(baseline)
        
        approach1 = apply_approach_1_context_aware(baseline, code)
        approach1_models = self._get_model_findings(approach1)
        
        approach2 = apply_approach_2_usage_context(baseline, code)
        approach2_models = self._get_model_findings(approach2)
        
        approach3 = apply_approach_3_combined(baseline, code)
        approach3_models = self._get_model_findings(approach3)
        
        print(f"\n=== Docstring Example ===")
        print(f"Baseline: {len(baseline_models)} findings")
        print(f"Approach 1 (Context-aware): {len(approach1_models)} findings")
        print(f"Approach 2 (Usage context): {len(approach2_models)} findings")
        print(f"Approach 3 (Combined): {len(approach3_models)} findings")
        if approach3_models:
            print(f"Approach 3 confidence: {approach3_models[0].get('confidence', 'N/A')}")
            print(f"Approach 3 reasons: {approach3_models[0].get('confidence_reasons', [])}")
        
        # Expected: 0 findings (false positive - in docstring)
        assert len(baseline_models) >= 1, "Baseline should detect it (false positive)"
        assert len(approach1_models) == 0, "Approach 1 should filter it out (docstring)"
        # Approach 2 might keep it (invoke_model in docstring looks like API call)
        # Approach 3 should filter it out (low confidence due to docstring)


def test_summary_comparison():
    """Print summary of all approaches."""
    print("\n" + "="*60)
    print("SUMMARY: False Positive Mitigation Approaches")
    print("="*60)
    print("\nApproach 1: Context-Aware Detection")
    print("  - Filters: Strings with validation keywords, comments, docstrings")
    print("  - Pros: Simple, catches obvious false positives")
    print("  - Cons: Might miss some edge cases")
    print("\nApproach 2: Usage Context Requirement")
    print("  - Filters: No API call within ±5 lines")
    print("  - Pros: Very effective for test fixtures and isolated strings")
    print("  - Cons: Might filter legitimate config variables far from usage")
    print("\nApproach 3: Combined with Confidence Scoring")
    print("  - Filters: Confidence score < 0.3")
    print("  - Pros: Most flexible, provides transparency")
    print("  - Cons: More complex, requires tuning threshold")
    print("="*60)
