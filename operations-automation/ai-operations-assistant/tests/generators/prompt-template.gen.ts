/**
 * fast-check arbitraries for prompt template objects.
 * Validates: Requirements 7.8
 */
import fc from 'fast-check';
import type { PromptTemplate, TemplateParameter } from '@shared/types';

/** Prompt template categories */
export const arbTemplateCategory = fc.constantFrom<PromptTemplate['category']>(
  'health', 'trusted_advisor', 'support', 'cost',
);

/** Parameter type */
const arbParamType = fc.constantFrom<TemplateParameter['type']>('text', 'date', 'select');

/** A single template parameter */
export const arbTemplateParameter: fc.Arbitrary<TemplateParameter> = fc.record({
  name: fc.stringMatching(/^[a-z][a-zA-Z0-9_]{2,20}$/),
  label: fc.string({ minLength: 3, maxLength: 40 }),
  type: arbParamType,
  required: fc.boolean(),
  options: fc.option(
    fc.array(fc.string({ minLength: 1, maxLength: 30 }), { minLength: 2, maxLength: 6 }),
    { nil: undefined },
  ),
  defaultValue: fc.option(fc.string({ minLength: 1, maxLength: 30 }), { nil: undefined }),
});

/**
 * Build a template string that contains `{{paramName}}` placeholders
 * for each parameter in the given list.
 */
const arbTemplateText = (params: TemplateParameter[]): fc.Arbitrary<string> => {
  if (params.length === 0) {
    return fc.string({ minLength: 10, maxLength: 200 });
  }
  // Produce a string that embeds every parameter placeholder at least once
  return fc.string({ minLength: 5, maxLength: 60 }).map((prefix) => {
    const placeholders = params.map((p) => `{{${p.name}}}`).join(' ');
    return `${prefix} ${placeholders}`;
  });
};

/** A complete PromptTemplate */
export const arbPromptTemplate: fc.Arbitrary<PromptTemplate> = arbTemplateParameter
  .chain((firstParam) =>
    fc.array(arbTemplateParameter, { minLength: 0, maxLength: 3 }).map((rest) => [firstParam, ...rest]),
  )
  .chain((params) =>
    fc.record({
      id: fc.uuidV(4),
      category: arbTemplateCategory,
      title: fc.string({ minLength: 5, maxLength: 80 }),
      description: fc.string({ minLength: 10, maxLength: 200 }),
      template: arbTemplateText(params),
      parameters: fc.constant(params),
    }),
  );

/** A prompt template with zero parameters (edge case) */
export const arbPromptTemplateNoParams: fc.Arbitrary<PromptTemplate> = fc.record({
  id: fc.uuidV(4),
  category: arbTemplateCategory,
  title: fc.string({ minLength: 5, maxLength: 80 }),
  description: fc.string({ minLength: 10, maxLength: 200 }),
  template: fc.string({ minLength: 10, maxLength: 200 }),
  parameters: fc.constant([] as TemplateParameter[]),
});
