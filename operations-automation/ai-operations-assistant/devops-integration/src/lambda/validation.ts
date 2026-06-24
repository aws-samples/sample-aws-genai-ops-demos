/**
 * Schema Validation Module for the GOAT Network Agent ↔ DevOps Agent Integration.
 *
 * Validates incoming DevOps Agent request payloads against the action schema registry
 * using ajv (JSON Schema validation). Returns structured error responses identifying
 * failing parameters and expected constraints on validation failure.
 *
 * Key guarantees:
 * - No Network Agent invocation occurs when validation fails (Property 1)
 * - Structured error responses identify failing parameters and expected constraints
 *
 * Requirements: 1.3, 7.5
 */

import Ajv, { ValidateFunction, ErrorObject } from "ajv";
import { ACTION_SCHEMAS, ActionSchemaRegistry } from "../schemas/action-schemas";
import { DevOpsAgentInvocation } from "../types/index";
import { ErrorDescription, createSchemaValidationError } from "../types/errors";

// ─── Ajv Instance (Singleton) ───────────────────────────────────────────────

/**
 * Singleton Ajv instance configured for JSON Schema draft-07 compatibility.
 * allErrors: true ensures all validation errors are collected (not just the first).
 */
const ajv = new Ajv({
  allErrors: true, // nosemgrep: ajv-allerrors-true — Lambda behind IAM auth, not public-facing
  strict: false,
  // Support "integer" type as used in our schemas
  allowUnionTypes: true,
});

// ─── Compiled Validator Cache ───────────────────────────────────────────────

/**
 * Cache of compiled ajv validators keyed by action_name.
 * Compiling a schema is expensive; caching ensures each schema is compiled once.
 */
const validatorCache = new Map<string, ValidateFunction>();

/**
 * Returns a compiled validator for the given action name's input schema.
 * Uses the validator cache for performance.
 */
function getValidator(actionName: string): ValidateFunction | null {
  if (validatorCache.has(actionName)) {
    return validatorCache.get(actionName)!;
  }

  const schemaEntry = ACTION_SCHEMAS[actionName];
  if (!schemaEntry) {
    return null;
  }

  const validator = ajv.compile(schemaEntry.input);
  validatorCache.set(actionName, validator);
  return validator;
}

// ─── Validation Result Types ────────────────────────────────────────────────

/**
 * Result of a successful validation (no errors).
 */
export interface ValidationSuccess {
  valid: true;
}

/**
 * Result of a failed validation with structured error details.
 */
export interface ValidationFailure {
  valid: false;
  error: ErrorDescription;
}

/** Union type for validation results. */
export type ValidationResult = ValidationSuccess | ValidationFailure;

// ─── Public API ─────────────────────────────────────────────────────────────

/**
 * Validates a DevOps Agent invocation request payload against the action schema registry.
 *
 * 1. Checks if the action_name exists in the registry
 * 2. Validates the parameters against the action's input schema using ajv
 * 3. Returns either success or a structured error with failing parameters and constraints
 *
 * This function is designed to be called BEFORE any Network Agent invocation,
 * ensuring invalid payloads never reach the downstream agent.
 *
 * @param invocation - The incoming DevOps Agent invocation request
 * @returns ValidationResult indicating success or failure with error details
 */
export function validateRequest(invocation: DevOpsAgentInvocation): ValidationResult {
  const { action_name, parameters } = invocation;

  // Step 1: Check if action exists in registry
  if (!ACTION_SCHEMAS[action_name]) {
    const error = createSchemaValidationError(
      ["action_name"],
      {
        action_name: `Must be one of the registered actions: ${Object.keys(ACTION_SCHEMAS).join(", ")}`,
      }
    );
    return { valid: false, error };
  }

  // Step 2: Get compiled validator for this action
  const validator = getValidator(action_name);
  if (!validator) {
    // Defensive: should not happen if ACTION_SCHEMAS check above passed
    const error = createSchemaValidationError(
      ["action_name"],
      { action_name: "Schema compilation failed for the specified action" }
    );
    return { valid: false, error };
  }

  // Step 3: Validate parameters against the action's input schema
  const valid = validator(parameters);

  if (valid) {
    return { valid: true };
  }

  // Step 4: Build structured error from ajv validation errors
  const { failingParameters, expectedConstraints } = extractValidationDetails(
    validator.errors || [],
    action_name
  );

  const error = createSchemaValidationError(failingParameters, expectedConstraints);
  return { valid: false, error };
}

/**
 * Checks whether a given action name exists in the action schema registry.
 *
 * @param actionName - The action name to check
 * @returns true if the action is registered, false otherwise
 */
export function isRegisteredAction(actionName: string): boolean {
  return actionName in ACTION_SCHEMAS;
}

/**
 * Returns the schema registry for external use (e.g., manifest generation).
 */
export function getActionSchemaRegistry(): ActionSchemaRegistry {
  return ACTION_SCHEMAS;
}

// ─── Internal Helpers ───────────────────────────────────────────────────────

/**
 * Extracts failing parameter names and expected constraints from ajv error objects.
 * Translates ajv's internal error representation into user-friendly descriptions.
 */
function extractValidationDetails(
  errors: ErrorObject[],
  actionName: string
): { failingParameters: string[]; expectedConstraints: Record<string, string> } {
  const failingParameters: string[] = [];
  const expectedConstraints: Record<string, string> = {};

  for (const err of errors) {
    const paramPath = getParameterPath(err);

    // Avoid duplicate parameter entries
    if (!failingParameters.includes(paramPath)) {
      failingParameters.push(paramPath);
    }

    // Build human-readable constraint description
    const constraint = buildConstraintDescription(err);
    expectedConstraints[paramPath] = constraint;
  }

  // Ensure at least one failing parameter is reported
  if (failingParameters.length === 0) {
    failingParameters.push("parameters");
    expectedConstraints["parameters"] = `Must conform to the schema for action "${actionName}"`;
  }

  return { failingParameters, expectedConstraints };
}

/**
 * Extracts the parameter path from an ajv ErrorObject.
 * Converts ajv's instancePath (e.g., "/eni_ids/0") to a dot-notation path.
 */
function getParameterPath(err: ErrorObject): string {
  // For "required" errors, the missing property is in params.missingProperty
  if (err.keyword === "required" && err.params?.missingProperty) {
    const basePath = err.instancePath
      ? err.instancePath.slice(1).replace(/\//g, ".")
      : "";
    return basePath
      ? `${basePath}.${err.params.missingProperty}`
      : err.params.missingProperty;
  }

  // For "additionalProperties" errors, identify the extra property
  if (err.keyword === "additionalProperties" && err.params?.additionalProperty) {
    const basePath = err.instancePath
      ? err.instancePath.slice(1).replace(/\//g, ".")
      : "";
    return basePath
      ? `${basePath}.${err.params.additionalProperty}`
      : err.params.additionalProperty;
  }

  // For other errors, use instancePath directly
  if (err.instancePath) {
    return err.instancePath.slice(1).replace(/\//g, ".");
  }

  return "parameters";
}

/**
 * Builds a human-readable constraint description from an ajv ErrorObject.
 */
function buildConstraintDescription(err: ErrorObject): string {
  switch (err.keyword) {
    case "required":
      return `Required property "${err.params?.missingProperty}" is missing`;

    case "type":
      return `Expected type "${err.params?.type}"`;

    case "enum":
      return `Must be one of: ${(err.params?.allowedValues || []).join(", ")}`;

    case "minimum":
      return `Must be >= ${err.params?.limit}`;

    case "maximum":
      return `Must be <= ${err.params?.limit}`;

    case "minItems":
      return `Array must have at least ${err.params?.limit} item(s)`;

    case "maxItems":
      return `Array must have at most ${err.params?.limit} item(s)`;

    case "minLength":
      return `String must have at least ${err.params?.limit} character(s)`;

    case "maxLength":
      return `String must have at most ${err.params?.limit} character(s)`;

    case "pattern":
      return `Must match pattern: ${err.params?.pattern}`;

    case "format":
      return `Must be a valid ${err.params?.format}`;

    case "additionalProperties":
      return `Unexpected property "${err.params?.additionalProperty}" is not allowed`;

    default:
      return err.message || `Validation failed (${err.keyword})`;
  }
}
