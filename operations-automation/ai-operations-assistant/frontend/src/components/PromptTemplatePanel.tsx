import { useState, useMemo } from 'react';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Cards from '@cloudscape-design/components/cards';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Popover from '@cloudscape-design/components/popover';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
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
  category: 'health' | 'trusted_advisor' | 'support' | 'cost' | 'network';
  title: string;
  description: string;
  template: string; // Template text with {{parameter}} placeholders
  parameters: TemplateParameter[];
  /** When true, the template requires GOATNetworkCaptureUsers group membership. */
  requiresCaptureGroup?: boolean;
}

// ---------------------------------------------------------------------------
// Category metadata
// ---------------------------------------------------------------------------
export const CATEGORY_LABELS: Record<PromptTemplate['category'], string> = {
  health: 'Health Event Reporting',
  trusted_advisor: 'Trusted Advisor Analysis',
  support: 'Support Case Insights',
  cost: 'Cost Optimization Opportunities',
  network: 'Network Troubleshooting',
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

  // ---- Network Troubleshooting ----
  {
    id: 'network-list-enis',
    category: 'network',
    title: 'List all ENIs in my account',
    description: 'Enumerate all Elastic Network Interfaces with attachment status and instance IDs.',
    template: 'List all ENIs in my account',
    parameters: [],
  },
  {
    id: 'network-start-capture',
    category: 'network',
    title: 'Start a 15-minute network capture on instance',
    description: 'Start a VPC packet capture on a specific EC2 instance for 15 minutes.',
    template:
      'Start a 15-minute network capture on instance {{instance_id}}',
    parameters: [
      { name: 'instance_id', label: 'EC2 Instance ID', type: 'text', required: true },
    ],
    requiresCaptureGroup: true,
  },
  {
    id: 'network-tls-hello-fragmentation',
    category: 'network',
    title: 'Show me TLS Client Hello fragmentation',
    description: 'Check for TLS Client Hello fragmentation in a completed capture.',
    template:
      'Show me TLS Client Hello fragmentation for capture {{capture_id}}',
    parameters: [
      { name: 'capture_id', label: 'Capture ID', type: 'text', required: true },
    ],
  },
  {
    id: 'network-tcp-retransmissions',
    category: 'network',
    title: 'Find TCP retransmissions in capture',
    description: 'Detect TCP retransmissions grouped by destination in a capture.',
    template:
      'Find TCP retransmissions in capture {{capture_id}}',
    parameters: [
      { name: 'capture_id', label: 'Capture ID', type: 'text', required: true },
    ],
  },
  {
    id: 'network-pod-connectivity',
    category: 'network',
    title: 'Why does my pod fail to reach an endpoint?',
    description: 'Capture and analyze traffic to diagnose pod connectivity failures.',
    template:
      'Why does my pod fail to reach {{endpoint}}? Capture and analyze.',
    parameters: [
      { name: 'endpoint', label: 'Endpoint (hostname or IP)', type: 'text', required: true },
    ],
    requiresCaptureGroup: true,
  },
  {
    id: 'network-multi-eni-capture',
    category: 'network',
    title: 'Capture multiple ENIs in one session',
    description: 'Start a capture on up to 3 ENIs simultaneously for a specified duration.',
    template:
      'Start a capture on ENIs {{eni_id_list}} for {{duration_minutes}} minutes',
    parameters: [
      { name: 'eni_id_list', label: 'ENI IDs (comma-separated, up to 3)', type: 'text', required: true },
      { name: 'duration_minutes', label: 'Duration (minutes, 1-60)', type: 'text', required: true, defaultValue: '15' },
    ],
    requiresCaptureGroup: true,
  },
  {
    id: 'network-diagnose-flow-hostname',
    category: 'network',
    title: 'Diagnose a flow by hostname',
    description: 'Diagnose a TCP exchange between two endpoints identified by hostname or IP.',
    template:
      'Diagnose the TCP exchange between {{source}} and {{destination}} on port {{port}} in capture {{capture_id}}',
    parameters: [
      { name: 'source', label: 'Source (hostname or IP)', type: 'text', required: true },
      { name: 'destination', label: 'Destination (hostname or IP)', type: 'text', required: true },
      { name: 'port', label: 'Port (optional)', type: 'text', required: false },
      { name: 'capture_id', label: 'Capture ID', type: 'text', required: true },
    ],
  },
  {
    id: 'network-find-dropped-flows',
    category: 'network',
    title: 'Find dropped flows by destination',
    description: 'Find dropped or reset connections to a specific destination.',
    template:
      'Find dropped or reset connections to {{destination}} on port {{port}} in capture {{capture_id}}',
    parameters: [
      { name: 'destination', label: 'Destination (hostname or IP)', type: 'text', required: true },
      { name: 'port', label: 'Port (optional)', type: 'text', required: false },
      { name: 'capture_id', label: 'Capture ID', type: 'text', required: true },
    ],
  },
  {
    id: 'network-investigate-support-case',
    category: 'network',
    title: 'Investigate from a support case',
    description: 'Investigate a network problem described in a support case with optional capture.',
    template:
      'Investigate the network problem described in support case {{case_id}} and capture {{capture_id}} if relevant',
    parameters: [
      { name: 'case_id', label: 'Support Case ID', type: 'text', required: true },
      { name: 'capture_id', label: 'Capture ID (optional)', type: 'text', required: false },
    ],
  },
  {
    id: 'network-diagnose-tcp-exchange',
    category: 'network',
    title: 'Diagnose a TCP exchange',
    description: 'Run a full TCP stream health diagnosis including handshake, RTT, retransmissions, and anomalies.',
    template:
      'Diagnose TCP stream {{stream_id}} from capture {{capture_id}}',
    parameters: [
      { name: 'stream_id', label: 'TCP Stream ID', type: 'text', required: true },
      { name: 'capture_id', label: 'Capture ID', type: 'text', required: true },
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
  /** Cognito groups the authenticated user belongs to. */
  userGroups?: string[];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export default function PromptTemplatePanel({ onSubmit, userGroups = [] }: PromptTemplatePanelProps) {
  const [selectedTemplate, setSelectedTemplate] = useState<PromptTemplate | null>(null);
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [activeTab, setActiveTab] = useState<string>('health');

  /** Whether the user is a member of the GOATNetworkCaptureUsers group. */
  const hasCaptureGroup = userGroups.includes('GOATNetworkCaptureUsers');

  // Derive categories for tabs
  const categories = useMemo(
    () =>
      (['health', 'trusted_advisor', 'support', 'cost', 'network'] as const).map((cat) => ({
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

  // Check if all required params are filled; identify first empty param by name
  const firstEmptyParam = selectedTemplate
    ? selectedTemplate.parameters
        .filter((p) => p.required)
        .find((p) => (paramValues[p.name] ?? '').trim() === '')
    : undefined;

  const allRequiredFilled = selectedTemplate ? !firstEmptyParam : false;

  /** Whether the currently selected template is disabled due to missing capture group. */
  const isTemplateDisabledByCaptureGroup =
    selectedTemplate?.requiresCaptureGroup && !hasCaptureGroup;

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
                    content: (item) => {
                      const disabled = item.requiresCaptureGroup && !hasCaptureGroup;
                      const button = (
                        <Button
                          variant={selectedTemplate?.id === item.id ? 'primary' : 'normal'}
                          onClick={() => handleSelectTemplate(item)}
                          disabled={disabled}
                        >
                          {selectedTemplate?.id === item.id ? 'Selected' : 'Select'}
                        </Button>
                      );
                      if (disabled) {
                        return (
                          <Popover
                            dismissButton={false}
                            position="top"
                            size="medium"
                            triggerType="custom"
                            content={
                              <StatusIndicator type="info">
                                Capture lifecycle actions require membership in the GOATNetworkCaptureUsers group.
                              </StatusIndicator>
                            }
                          >
                            {button}
                          </Popover>
                        );
                      }
                      return button;
                    },
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
                  type="text"
                />
              )}
            </FormField>
          ))}

          <Button variant="primary" onClick={handleUseTemplate} disabled={!allRequiredFilled || !!isTemplateDisabledByCaptureGroup}>
            Use Template
          </Button>
          {isTemplateDisabledByCaptureGroup && (
            <StatusIndicator type="warning">
              This template requires GOATNetworkCaptureUsers group membership.
            </StatusIndicator>
          )}
          {!allRequiredFilled && firstEmptyParam && !isTemplateDisabledByCaptureGroup && (
            <StatusIndicator type="info">
              Please fill in the required parameter: {firstEmptyParam.label}
            </StatusIndicator>
          )}
        </SpaceBetween>
      )}
    </SpaceBetween>
  );
}
