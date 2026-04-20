/**
 * Unit tests for prompt template structure
 * Validates: Requirements 8.1, 8.3
 */
import { describe, it, expect } from 'vitest';
import {
  PROMPT_TEMPLATES,
  CATEGORY_LABELS,
  extractParameters,
  getTemplatesByCategory,
} from '../../frontend/src/components/PromptTemplatePanel';

const ALL_CATEGORIES = Object.keys(CATEGORY_LABELS) as Array<
  keyof typeof CATEGORY_LABELS
>;

describe('Prompt template structure', () => {
  /**
   * Requirement 8.1: THE Chatbot_Frontend SHALL provide a minimum of four
   * Prompt_Template categories … at least two Prompt_Template entries per category.
   */
  describe('each category has at least 2 templates', () => {
    it.each(ALL_CATEGORIES)('category "%s" has >= 2 templates', (category) => {
      const templates = getTemplatesByCategory(category);
      expect(templates.length).toBeGreaterThanOrEqual(2);
    });
  });

  /**
   * Requirement 8.1 (cont.): each template should have a meaningful description
   * so users understand its purpose.
   */
  describe('each template has a non-empty description', () => {
    it.each(PROMPT_TEMPLATES.map((t) => [t.id, t]))(
      'template "%s" has a non-empty description',
      (_id, template) => {
        expect(template.description).toBeDefined();
        expect(typeof template.description).toBe('string');
        expect(template.description.trim().length).toBeGreaterThan(0);
      },
    );
  });

  /**
   * Requirement 8.3: WHEN a Prompt_Template contains parameterized fields,
   * THE Chatbot_Frontend SHALL render input fields for each parameter.
   * We verify that every {{param}} placeholder in the template string has a
   * corresponding entry in the parameters array.
   */
  describe('parameterized templates define input fields for each parameter', () => {
    const templatesWithParams = PROMPT_TEMPLATES.filter(
      (t) => extractParameters(t.template).length > 0,
    );

    it.each(templatesWithParams.map((t) => [t.id, t]))(
      'template "%s" defines an input field for every placeholder',
      (_id, template) => {
        const placeholders = extractParameters(template.template);
        const definedParamNames = template.parameters.map((p) => p.name);

        for (const placeholder of placeholders) {
          expect(definedParamNames).toContain(placeholder);
        }
      },
    );
  });
});
