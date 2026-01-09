"""
Example code with FALSE POSITIVE scenarios for model ID detection.

These should NOT be flagged as actual Bedrock usage:
- Validation error messages
- Documentation examples
- Comments
- Test fixtures
"""

# FALSE POSITIVE 1: Validation error message (like the user's example)
def validate_model_id(model_id: str) -> str:
    """Validate Bedrock model ID format."""
    errors = ''
    if not model_id.strip():
        errors += 'Required field. '
    else:
        # Validate model ID format: provider.model-name
        import re
        model_id_pattern = r'^[a-zA-Z0-9-]+\.[a-zA-Z0-9-\._]+(:[0-9]+)?$'
        if not model_id_pattern.match(model_id.strip()):
            errors += 'Model ID must follow the pattern provider.model-name format (e.g., amazon.titan-text-express-v1). '
    return errors


# FALSE POSITIVE 2: Documentation string
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


# FALSE POSITIVE 3: Comment with example
def process_request(model_id: str):
    # TODO: Add support for more models like anthropic.claude-3-haiku-20240307-v1:0
    # Currently only supports amazon.nova-micro-v1:0
    raise NotImplementedError("TODO: Add support for more models")


# FALSE POSITIVE 4: Test fixture / mock data
EXAMPLE_MODEL_IDS = [
    "anthropic.claude-v2",
    "amazon.titan-text-express-v1",
    "meta.llama3-70b-instruct-v1:0"
]


# FALSE POSITIVE 5: Error message with multiple examples
class ModelValidationError(Exception):
    """Raised when model ID is invalid.
    
    Valid formats include:
    - anthropic.claude-3-sonnet-20240229-v1:0
    - amazon.nova-pro-v1:0
    - us.anthropic.claude-sonnet-4-20250514-v1:0
    """
    pass


# FALSE POSITIVE 6: Configuration documentation
CONFIG_HELP = """
Configure your Bedrock model:

Supported models:
- Claude: anthropic.claude-3-7-sonnet-20250219-v1:0
- Nova: amazon.nova-lite-v1:0
- Llama: meta.llama3-70b-instruct-v1:0

Example:
    model_id = "anthropic.claude-3-haiku-20240307-v1:0"
"""


# TRUE POSITIVE: Actual usage (should be detected)
import boto3

bedrock = boto3.client('bedrock-runtime')

def invoke_model_real():
    """This SHOULD be detected as real usage."""
    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        body='{"prompt": "Hello"}'
    )
    return response


# TRUE POSITIVE: Variable assignment (should be detected)
def get_model_config():
    """This SHOULD be detected as real usage."""
    model_id = "amazon.nova-pro-v1:0"
    return bedrock.invoke_model(modelId=model_id, body='{}')
