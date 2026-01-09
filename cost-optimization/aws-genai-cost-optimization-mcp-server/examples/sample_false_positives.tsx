/**
 * TypeScript/React example with FALSE POSITIVE scenarios.
 * 
 * These should NOT be flagged as actual Bedrock usage.
 */

import React, { useState } from 'react';

// FALSE POSITIVE 1: Validation error message (user's actual example)
const onModelIdChange = (detail: { value: string }) => {
  let errors = '';
  if (detail.value.trim().length === 0) {
    errors += 'Required field. ';
  } else {
    // Validate model ID format: provider.model-name
    const modelIdPattern = /^[a-zA-Z0-9-]+\.[a-zA-Z0-9-\._]+(:[0-9]+)?$/;
    if (!modelIdPattern.test(detail.value.trim())) {
      errors += 'Model ID must follow the pattern provider.model-name format (e.g., amazon.titan-text-express-v1). ';
    }
  }
  return errors;
};

// FALSE POSITIVE 2: JSDoc documentation
/**
 * Invoke a Bedrock model.
 * 
 * @example
 * ```typescript
 * const response = await invokeModel({
 *   modelId: "anthropic.claude-3-sonnet-20240229-v1:0",
 *   prompt: "Hello"
 * });
 * ```
 */
function invokeModel(params: any) {
  // implementation
}

// FALSE POSITIVE 3: Comment with example
function processRequest() {
  // TODO: Support more models like anthropic.claude-3-haiku-20240307-v1:0
  // Currently only supports amazon.nova-micro-v1:0
}

// FALSE POSITIVE 4: Placeholder/example in UI component
const ModelIdInput: React.FC = () => {
  const [modelId, setModelId] = useState('');
  
  return (
    <input
      type="text"
      placeholder="e.g., anthropic.claude-3-sonnet-20240229-v1:0"
      value={modelId}
      onChange={(e) => setModelId(e.target.value)}
    />
  );
};

// FALSE POSITIVE 5: Error message constant
const ERROR_MESSAGES = {
  INVALID_MODEL: 'Invalid model ID. Use format like amazon.titan-text-express-v1',
  UNSUPPORTED: 'Model anthropic.claude-v2 is no longer supported'
};

// FALSE POSITIVE 6: Test data / mock
const MOCK_MODELS = [
  { id: 'anthropic.claude-3-sonnet-20240229-v1:0', name: 'Claude 3 Sonnet' },
  { id: 'amazon.nova-pro-v1:0', name: 'Nova Pro' }
];

// TRUE POSITIVE: Actual usage (should be detected)
async function invokeBedrockModel() {
  const response = await bedrockClient.invokeModel({
    modelId: "anthropic.claude-3-sonnet-20240229-v1:0",
    body: JSON.stringify({ prompt: "Hello" })
  });
  return response;
}

// TRUE POSITIVE: Variable assignment near API call (should be detected)
async function callModel() {
  const modelId = "amazon.nova-lite-v1:0";
  return await bedrockClient.invokeModel({ modelId, body: "{}" });
}
