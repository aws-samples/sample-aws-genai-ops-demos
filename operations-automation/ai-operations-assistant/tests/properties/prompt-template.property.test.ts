/**
 * Property tests for prompt template selection, parameter rendering,
 * and category filtering.
 * Feature: genai-operations-analytics-tool
 *
 * These tests exercise the exported functions and data from
 * PromptTemplatePanel against the actual PROMPT_TEMPLATES library.
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  PROMPT_TEMPLATES,
  CATEGORY_LABELS,
  extractParameters,
  fillTemplate,
  getTemplatesByCategory,
} from '../../frontend/src/components/PromptTemplatePanel';
import type { PromptTemplate } from '../../frontend/src/components/PromptTemplatePanel';

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Pick a random template from the real PROMPT_TEMPLATES array. */
const arbRealTemplate = fc.constantFrom(...PROMPT_TEMPLATES);

/** Pick a random category key. */
const arbCategory = fc.constantFrom(
  ...Object.keys(CATEGORY_LABELS) as PromptTemplate['category'][],
);

/** Arbitrary non-empty alphanumeric value for filling parameters. */
const arbParamValue = fc.stringOf(
  fc.constantFrom(
    ...'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789- '.split(''),
  ),
  { minLength: 1, maxLength: 40 },
);

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Prompt template property tests', () => {
  /**
   * Property 11: Prompt template selection and parameter rendering
   *
   * For any template from PROMPT_TEMPLATES, extracting parameters from the
   * template string should match the parameter names defined in the
   * parameters array. When all parameters are filled, fillTemplate should
   * produce a string with no remaining {{}} placeholders.
   *
   * **Validates: Requirements 7.8, 8.4**
   */
  it('Property 11: Prompt template selection and parameter rendering — Feature: genai-operations-analytics-tool, Property 11: Prompt template selection and parameter rendering', () => {
    fc.assert(
      fc.property(arbRealTemplate, (template) => {
        // 1. Extracted placeholder names should match declared parameter names
        const extractedNames = extractParameters(template.template);
        const declaredNames = template.parameters.map((p) => p.name);

        // Every extracted placeholder must be declared
        for (const name of extractedNames) {
          expect(declaredNames).toContain(name);
        }
        // Every declared parameter must appear as a placeholder
        for (const name of declaredNames) {
          expect(extractedNames).toContain(name);
        }

        // 2. The number of input fields equals the number of parameters
        expect(template.parameters.length).toBe(declaredNames.length);
      }),
      { numRuns: 100 },
    );
  });

  it('Property 11a: fillTemplate with all parameters removes all placeholders', () => {
    fc.assert(
      fc.property(arbRealTemplate, (template) => {
        // Build a values map with a concrete value for every parameter
        const values: Record<string, string> = {};
        for (const param of template.parameters) {
          values[param.name] = param.defaultValue || 'test-value';
        }

        const filled = fillTemplate(template.template, values);

        // No remaining {{...}} placeholders
        expect(filled).not.toMatch(/\{\{\w+\}\}/);
      }),
      { numRuns: 100 },
    );
  });

  it('Property 11b: fillTemplate with random values removes all placeholders', () => {
    fc.assert(
      fc.property(
        arbRealTemplate,
        fc.array(arbParamValue, { minLength: 20, maxLength: 20 }),
        (template, randomValues) => {
          // Map each parameter to a random value from the pool
          const values: Record<string, string> = {};
          template.parameters.forEach((param, idx) => {
            values[param.name] = randomValues[idx % randomValues.length];
          });

          const filled = fillTemplate(template.template, values);

          // No remaining {{...}} placeholders
          expect(filled).not.toMatch(/\{\{\w+\}\}/);

          // Each provided value should appear in the filled string
          for (const param of template.parameters) {
            expect(filled).toContain(values[param.name]);
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it('Property 11c: fillTemplate with missing values preserves unfilled placeholders', () => {
    fc.assert(
      fc.property(arbRealTemplate, (template) => {
        if (template.parameters.length === 0) return; // skip no-param templates

        // Fill only the first parameter, leave the rest empty
        const values: Record<string, string> = {
          [template.parameters[0].name]: 'filled-value',
        };

        const filled = fillTemplate(template.template, values);

        // The filled parameter should be replaced
        expect(filled).toContain('filled-value');

        // Remaining parameters should still have placeholders
        for (let i = 1; i < template.parameters.length; i++) {
          const paramName = template.parameters[i].name;
          expect(filled).toContain(`{{${paramName}}}`);
        }
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 12: Prompt template category filtering
   *
   * For any category, getTemplatesByCategory should return only templates
   * matching that category. The union of all categories should equal the
   * full PROMPT_TEMPLATES array.
   *
   * **Validates: Requirements 8.2**
   */
  it('Property 12: Prompt template category filtering — Feature: genai-operations-analytics-tool, Property 12: Prompt template category filtering', () => {
    fc.assert(
      fc.property(arbCategory, (category) => {
        const filtered = getTemplatesByCategory(category);

        // Every returned template must belong to the requested category
        for (const tmpl of filtered) {
          expect(tmpl.category).toBe(category);
        }

        // Every returned template must have a non-empty description
        for (const tmpl of filtered) {
          expect(tmpl.description.length).toBeGreaterThan(0);
        }

        // The filtered set should match what we'd get from manual filtering
        const expected = PROMPT_TEMPLATES.filter((t) => t.category === category);
        expect(filtered).toHaveLength(expected.length);
        expect(filtered.map((t) => t.id).sort()).toEqual(expected.map((t) => t.id).sort());
      }),
      { numRuns: 100 },
    );
  });

  it('Property 12a: Union of all categories equals full PROMPT_TEMPLATES', () => {
    const allCategories = Object.keys(CATEGORY_LABELS) as PromptTemplate['category'][];
    const union: PromptTemplate[] = [];

    for (const cat of allCategories) {
      union.push(...getTemplatesByCategory(cat));
    }

    // Union should contain every template exactly once
    expect(union).toHaveLength(PROMPT_TEMPLATES.length);

    const unionIds = union.map((t) => t.id).sort();
    const allIds = PROMPT_TEMPLATES.map((t) => t.id).sort();
    expect(unionIds).toEqual(allIds);
  });

  it('Property 12b: No template exists outside defined categories', () => {
    const validCategories = new Set(Object.keys(CATEGORY_LABELS));

    for (const tmpl of PROMPT_TEMPLATES) {
      expect(validCategories.has(tmpl.category)).toBe(true);
    }
  });
});
