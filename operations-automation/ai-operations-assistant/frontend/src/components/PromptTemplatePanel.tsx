import { useState, useMemo } from 'react';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Cards from '@cloudscape-design/components/cards';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Tabs from '@cloudscape-design/components/tabs';

// ---------------------------------------------------------------------------
// Types (mirroring shared/types.ts for frontend use)
// ---------------------------------------------------------------------------
export interface TemplateParameter {
  name: string;
  label: string;
  type: 'text' | 'date' | 'select';
  required: boolean;
  options?: string[];
  defaultValue?: string;
}

export interface PromptTemplate {
  id: string;
  category: 'health' | 'trusted_advisor' | 'support' | 'cost';
  title: string;
  description: string;
  template: string; // Template text with {{parameter}} placeholders
  parameters: TemplateParameter[];
}

// ---------------------------------------------------------------------------
// Category metadata
// ---------------------------------------------------------------------------
export const CATEGORY_LABELS: Record<PromptTemplate['category'], string> = {
  health: 'Health Event Reporting',
  trusted_advisor: 'Trusted Advisor Analysis',
  support: 'Support Case Insights',
  cost: 'Cost Optimization Opportunities',
};

// ---------------------------------------------------------------------------
// Template library — exported for independent testing
// ---------------------------------------------------------------------------
export const PROMPT_TEMPLATES: PromptTemplate[] = [
  // ---- Health Event Reporting ----
  {
    id: 'health-events-by-service',
    category: 'health',
    title: 'Service Health Events',
    description: 'List recent AWS Health events filtered by service and region.',
    template:
      'Show me all AWS Health events for {{service_name}} in {{region}} from {{date_range}}.',
    parameters: [
      { name: 'service_name', label: 'AWS Service', type: 'select', required: true, options: ['EC2', 'RDS', 'Lambda', 'S3', 'ECS', 'DynamoDB', 'CloudFront'] },
      { name: 'region', label: 'Region', type: 'select', required: true, options: ['us-east-1', 'us-west-2', 'eu-west-1', 'ap-southeast-1', 'ap-northeast-1'] },
      { name: 'date_range', label: 'Date Range', type: 'text', required: true, defaultValue: 'last 7 days' },
    ],
  },
  {
    id: 'health-affected-resources',
    category: 'health',
    title: 'Affected Resources Report',
    description: 'Identify resources affected by a specific health event with impact details.',
    template:
      'What resources are affected by the {{event_type}} health event in {{region}}? Include severity and estimated impact.',
    parameters: [
      { name: 'event_type', label: 'Event Type', type: 'select', required: true, options: ['issue', 'accountNotification', 'scheduledChange'] },
      { name: 'region', label: 'Region', type: 'select', required: true, options: ['us-east-1', 'us-west-2', 'eu-west-1', 'ap-southeast-1'] },
    ],
  },

  // ---- Trusted Advisor Analysis ----
  {
    id: 'ta-pillar-recommendations',
    category: 'trusted_advisor',
    title: 'Recommendations by Pillar',
    description: 'Get Trusted Advisor recommendations filtered by optimization pillar.',
    template:
      'List all Trusted Advisor {{pillar}} recommendations with {{status}} status. Summarize the top findings.',
    parameters: [
      { name: 'pillar', label: 'Pillar', type: 'select', required: true, options: ['cost_optimizing', 'security', 'performance', 'fault_tolerance', 'service_limits'] },
      { name: 'status', label: 'Status', type: 'select', required: true, options: ['warning', 'error', 'ok'], defaultValue: 'warning' },
    ],
  },
  {
    id: 'ta-security-audit',
    category: 'trusted_advisor',
    title: 'Security Posture Audit',
    description: 'Review security-related Trusted Advisor checks and flag critical issues.',
    template:
      'Run a security audit using Trusted Advisor. Show all {{severity}} findings for {{service_name}} and recommend remediation steps.',
    parameters: [
      { name: 'severity', label: 'Severity', type: 'select', required: true, options: ['error', 'warning'], defaultValue: 'error' },
      { name: 'service_name', label: 'AWS Service (optional)', type: 'text', required: false, defaultValue: '' },
    ],
  },

  // ---- Support Case Insights ----
  {
    id: 'support-recent-cases',
    category: 'support',
    title: 'Recent Support Cases',
    description: 'View recent support cases filtered by severity and status.',
    template:
      'Show me all {{status}} support cases with {{severity}} severity from {{date_range}}. Include case subjects and last update.',
    parameters: [
      { name: 'status', label: 'Case Status', type: 'select', required: true, options: ['open', 'pending-customer-action', 'resolved', 'closed'], defaultValue: 'open' },
      { name: 'severity', label: 'Severity', type: 'select', required: true, options: ['critical', 'urgent', 'high', 'normal', 'low'], defaultValue: 'high' },
      { name: 'date_range', label: 'Date Range', type: 'text', required: true, defaultValue: 'last 30 days' },
    ],
  },
  {
    id: 'support-case-analysis',
    category: 'support',
    title: 'Case Pattern Analysis',
    description: 'Analyze support case patterns to identify recurring issues by service.',
    template:
      'Analyze support case patterns for {{service_name}} over {{date_range}}. Identify recurring issues and suggest preventive actions.',
    parameters: [
      { name: 'service_name', label: 'AWS Service', type: 'select', required: true, options: ['EC2', 'RDS', 'Lambda', 'S3', 'ECS', 'IAM', 'VPC'] },
      { name: 'date_range', label: 'Date Range', type: 'text', required: true, defaultValue: 'last 90 days' },
    ],
  },

  // ---- Cost Optimization Opportunities ----
  {
    id: 'cost-usage-breakdown',
    category: 'cost',
    title: 'Cost & Usage Breakdown',
    description: 'Get a cost breakdown by service for a given time period.',
    template:
      'Show me a {{granularity}} cost breakdown grouped by service for {{date_range}}. Highlight the top 5 spending services.',
    parameters: [
      { name: 'granularity', label: 'Granularity', type: 'select', required: true, options: ['DAILY', 'MONTHLY'], defaultValue: 'MONTHLY' },
      { name: 'date_range', label: 'Date Range', type: 'text', required: true, defaultValue: 'last 3 months' },
    ],
  },
  {
    id: 'cost-optimization-recs',
    category: 'cost',
    title: 'Optimization Recommendations',
    description: 'Retrieve cost optimization recommendations with estimated savings.',
    template:
      'What are the top cost optimization recommendations for {{service_name}}? Include estimated monthly savings and implementation effort.',
    parameters: [
      { name: 'service_name', label: 'AWS Service (or "all")', type: 'text', required: true, defaultValue: 'all' },
    ],
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Extract {{param}} placeholders from a template string. */
export function extractParameters(template: string): string[] {
  const matches = template.match(/\{\{(\w+)\}\}/g);
  if (!matches) return [];
  return [...new Set(matches.map((m) => m.slice(2, -2)))];
}

/** Replace {{param}} placeholders with provided values. */
export function fillTemplate(
  template: string,
  values: Record<string, string>,
): string {
  return template.replace(/\{\{(\w+)\}\}/g, (_, key) => values[key] ?? `{{${key}}}`);
}

/** Get templates for a given category. */
export function getTemplatesByCategory(
  category: PromptTemplate['category'],
): PromptTemplate[] {
  return PROMPT_TEMPLATES.filter((t) => t.category === category);
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------
export interface PromptTemplatePanelProps {
  /** Called when the user fills in parameters and clicks "Use Template". */
  onSubmit: (filledPrompt: string) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export default function PromptTemplatePanel({ onSubmit }: PromptTemplatePanelProps) {
  const [selectedTemplate, setSelectedTemplate] = useState<PromptTemplate | null>(null);
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [activeTab, setActiveTab] = useState<string>('health');

  // Derive categories for tabs
  const categories = useMemo(
    () =>
      (['health', 'trusted_advisor', 'support', 'cost'] as const).map((cat) => ({
        id: cat,
        label: CATEGORY_LABELS[cat],
        templates: getTemplatesByCategory(cat),
      })),
    [],
  );

  // When a template card is selected
  const handleSelectTemplate = (template: PromptTemplate) => {
    setSelectedTemplate(template);
    // Initialize parameter values with defaults
    const defaults: Record<string, string> = {};
    for (const param of template.parameters) {
      defaults[param.name] = param.defaultValue ?? '';
    }
    setParamValues(defaults);
  };

  // Update a single parameter value
  const handleParamChange = (name: string, value: string) => {
    setParamValues((prev) => ({ ...prev, [name]: value }));
  };

  // Submit the filled template
  const handleUseTemplate = () => {
    if (!selectedTemplate) return;
    const filled = fillTemplate(selectedTemplate.template, paramValues);
    onSubmit(filled);
    setSelectedTemplate(null);
    setParamValues({});
  };

  // Check if all required params are filled
  const allRequiredFilled = selectedTemplate
    ? selectedTemplate.parameters
        .filter((p) => p.required)
        .every((p) => (paramValues[p.name] ?? '').trim() !== '')
    : false;

  return (
    <SpaceBetween size="l">
      <Header variant="h1" description="Browse pre-configured query templates organized by operational category.">
        Prompt Templates
      </Header>

      <Tabs
        activeTabId={activeTab}
        onChange={({ detail }) => {
          setActiveTab(detail.activeTabId);
          setSelectedTemplate(null);
          setParamValues({});
        }}
        tabs={categories.map((cat) => ({
          id: cat.id,
          label: cat.label,
          content: (
            <Cards
              cardDefinition={{
                header: (item) => item.title,
                sections: [
                  {
                    id: 'description',
                    content: (item) => item.description,
                  },
                  {
                    id: 'params',
                    header: 'Parameters',
                    content: (item) =>
                      item.parameters.map((p) => p.label).join(', ') || 'None',
                  },
                  {
                    id: 'action',
                    content: (item) => (
                      <Button
                        variant={selectedTemplate?.id === item.id ? 'primary' : 'normal'}
                        onClick={() => handleSelectTemplate(item)}
                      >
                        {selectedTemplate?.id === item.id ? 'Selected' : 'Select'}
                      </Button>
                    ),
                  },
                ],
              }}
              items={cat.templates}
              header={
                <Header counter={`(${cat.templates.length})`}>
                  {cat.label}
                </Header>
              }
              empty={
                <Box textAlign="center" color="text-body-secondary" padding="l">
                  No templates in this category.
                </Box>
              }
            />
          ),
        }))}
      />

      {/* Parameter input form — shown when a template is selected */}
      {selectedTemplate && (
        <SpaceBetween size="m">
          <Header variant="h2">
            Configure: {selectedTemplate.title}
          </Header>
          <Box variant="p" color="text-body-secondary">
            {selectedTemplate.template}
          </Box>

          {selectedTemplate.parameters.map((param) => (
            <FormField
              key={param.name}
              label={param.label}
              description={param.required ? 'Required' : 'Optional'}
            >
              {param.type === 'select' && param.options ? (
                <Select
                  selectedOption={
                    paramValues[param.name]
                      ? { label: paramValues[param.name], value: paramValues[param.name] }
                      : null
                  }
                  onChange={({ detail }) =>
                    handleParamChange(param.name, detail.selectedOption.value ?? '')
                  }
                  options={param.options.map((opt) => ({ label: opt, value: opt }))}
                  placeholder={`Select ${param.label.toLowerCase()}`}
                />
              ) : (
                <Input
                  value={paramValues[param.name] ?? ''}
                  onChange={({ detail }) => handleParamChange(param.name, detail.value)}
                  placeholder={
                    param.type === 'date'
                      ? 'YYYY-MM-DD'
                      : `Enter ${param.label.toLowerCase()}`
                  }
                  type={param.type === 'date' ? 'date' : 'text'}
                />
              )}
            </FormField>
          ))}

          <Button variant="primary" onClick={handleUseTemplate} disabled={!allRequiredFilled}>
            Use Template
          </Button>
        </SpaceBetween>
      )}
    </SpaceBetween>
  );
}
