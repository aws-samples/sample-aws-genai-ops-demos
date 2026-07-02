/**
 * Action Schema Registry for the GOAT Network Agent ↔ DevOps Agent Integration.
 *
 * Defines JSON Schema (draft-07 compatible) definitions for all 28 Network Agent
 * actions plus the composite `full_diagnostic` action. Each entry includes input/output
 * schemas, category classification, and authorization requirements.
 *
 * This registry is the single source of truth for:
 * - Request payload validation (via ajv)
 * - Tool manifest generation
 * - DevOps Agent action discovery
 *
 * Requirements: 1.1, 1.3, 4.3, 4.7, 2.1, 2.2, 2.3, 2.4, 2.5
 */
/** JSON Schema type definition (draft-07 compatible with ajv) */
export interface JSONSchema {
    $schema?: string;
    type?: string | string[];
    properties?: Record<string, JSONSchema>;
    required?: string[];
    additionalProperties?: boolean;
    items?: JSONSchema;
    minItems?: number;
    maxItems?: number;
    minimum?: number;
    maximum?: number;
    minLength?: number;
    maxLength?: number;
    pattern?: string;
    enum?: (string | number | boolean | null)[];
    description?: string;
    default?: unknown;
    format?: string;
    oneOf?: JSONSchema[];
    anyOf?: JSONSchema[];
    allOf?: JSONSchema[];
}
/** Action category classification */
export type ActionCategory = "capture" | "analysis" | "utility";
/** Schema registry entry for a single action */
export interface ActionSchemaEntry {
    input: JSONSchema;
    output: JSONSchema;
    category: ActionCategory;
    requiresAuth: boolean;
    /** Optional per-entry MCP tool description override. When set, takes highest priority in the description resolution chain. */
    mcpDescription?: string;
}
/** The complete action schema registry type */
export type ActionSchemaRegistry = Record<string, ActionSchemaEntry>;
export declare const ACTION_SCHEMAS: ActionSchemaRegistry;
/**
 * Get the schema entry for a given action name.
 * @param actionName - The action name to look up
 * @returns The schema entry or undefined if not found
 */
export declare function getActionSchema(actionName: string): ActionSchemaEntry | undefined;
/**
 * Get all registered action names.
 * @returns Array of all action names in the registry
 */
export declare function getActionNames(): string[];
/**
 * Get action names filtered by category.
 * @param category - The category to filter by
 * @returns Array of action names in the given category
 */
export declare function getActionsByCategory(category: ActionCategory): string[];
/**
 * Get action names that require authorization.
 * @returns Array of action names requiring auth
 */
export declare function getAuthRequiredActions(): string[];
/**
 * Check whether a given action name is valid (exists in registry).
 * @param actionName - The action name to validate
 * @returns true if the action exists in the registry
 */
export declare function isValidAction(actionName: string): boolean;
