/**
 * MCP Tool Definition Generator for the GOAT Network Agent ↔ DevOps Agent integration.
 *
 * Converts the ACTION_SCHEMAS registry into MCP Tool definitions at Lambda cold-start time.
 * Each tool definition includes a name, description derived via priority chain, and the
 * input schema passed through unchanged from the ActionSchemaEntry.
 *
 * Requirements: 3.1, 3.2, 3.3, 3.4, 3.5
 */

import type { McpToolDefinition } from "../types/mcp";
import type { ActionSchemaRegistry, ActionSchemaEntry } from "../schemas/action-schemas";
import { ACTION_SCHEMAS } from "../schemas/action-schemas";
import { MCP_DESCRIPTIONS } from "../schemas/mcp-descriptions";
import type { JSONSchema } from "../types/index";

// ─── Module-Level Cache ─────────────────────────────────────────────────────

/** Cached tool definitions computed once per Lambda cold start */
let cachedDefinitions: McpToolDefinition[] | null = null;

// ─── Description Derivation ─────────────────────────────────────────────────

/**
 * Derives a human-readable description for a tool definition using a priority chain.
 *
 * Priority order:
 * 1. schemaEntry.mcpDescription (per-entry override)
 * 2. MCP_DESCRIPTIONS[actionName] (descriptions map)
 * 3. schemaEntry.input.description (schema-level description)
 * 4. Name-based fallback (capitalize and join underscores)
 *
 * @param actionName - The action registry key (e.g., "start_capture")
 * @param schemaEntry - The full ActionSchemaEntry for the action
 * @returns A human-readable description string
 */
export function deriveDescription(actionName: string, schemaEntry: ActionSchemaEntry): string {
  // Priority 1: per-entry override on the schema entry
  if (schemaEntry.mcpDescription) {
    return schemaEntry.mcpDescription;
  }

  // Priority 2: descriptions map
  const mapDescription = MCP_DESCRIPTIONS[actionName];
  if (mapDescription) {
    return mapDescription;
  }

  // Priority 3: schema input.description field
  if (schemaEntry.input.description) {
    return schemaEntry.input.description;
  }

  // Priority 4: generate from action name
  const words = actionName.split("_");
  if (words.length > 0) {
    words[0] = words[0].charAt(0).toUpperCase() + words[0].slice(1);
  }
  return words.join(" ");
}

// ─── Tool Definition Generator ──────────────────────────────────────────────

/**
 * Generates MCP tool definitions from the action schema registry.
 *
 * Maps each ACTION_SCHEMAS key to an McpToolDefinition with:
 * - `name`: The action registry key (e.g., "start_capture", "full_diagnostic")
 * - `description`: Derived via priority chain (mcpDescription → map → schema → name)
 * - `inputSchema`: The ActionSchemaEntry.input passed through unchanged
 *
 * This function is deterministic: same input always produces the same output.
 *
 * @param schemas - The complete action schema registry
 * @returns Array of MCP tool definitions, one per registry entry
 */
export function generateToolDefinitions(schemas: ActionSchemaRegistry): McpToolDefinition[] {
  const definitions: McpToolDefinition[] = [];

  for (const [actionName, entry] of Object.entries(schemas)) {
    const schemaEntry = entry as ActionSchemaEntry;
    const description = deriveDescription(actionName, schemaEntry);

    definitions.push({
      name: actionName,
      description,
      inputSchema: schemaEntry.input as unknown as JSONSchema,
    });
  }

  return definitions;
}

// ─── Cached Access ──────────────────────────────────────────────────────────

/**
 * Actions that are NOT supported by the Network Agent runtime directly.
 * cleanup_orphaned_sessions is a maintenance utility that doesn't exist
 * on the agent side — it would need local DynamoDB implementation.
 */
const HIDDEN_TOOLS = new Set(["cleanup_orphaned_sessions", "full_diagnostic"]);

/**
 * Returns the cached MCP tool definitions, computing them on first access.
 * Filters out tools that are not supported by the Network Agent runtime.
 *
 * Uses module-level caching so definitions are computed once per Lambda cold start
 * and reused across all subsequent invocations within the same execution context.
 *
 * @returns Array of all MCP tool definitions generated from ACTION_SCHEMAS
 */
export function getToolDefinitions(): McpToolDefinition[] {
  if (cachedDefinitions === null) {
    const all = generateToolDefinitions(ACTION_SCHEMAS);
    cachedDefinitions = all.filter(t => !HIDDEN_TOOLS.has(t.name));
  }
  return cachedDefinitions;
}
