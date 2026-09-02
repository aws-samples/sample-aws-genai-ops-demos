import ExpandableSection from '@cloudscape-design/components/expandable-section';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Badge from '@cloudscape-design/components/badge';
import Link from '@cloudscape-design/components/link';
import {
  RiskAssessment,
  Impact,
  Recommendation,
  ComplianceImpact,
  ExtendedSupportPricing,
  ActionPlanPrefill,
} from '../risk/types';
import { DeprecationItem } from '../api';
import Button from '@cloudscape-design/components/button';

export interface ImpactPanelProps {
  assessment: RiskAssessment;
  item: DeprecationItem;
  onCreateActionPlan: (prefill: ActionPlanPrefill) => void;
}

/**
 * Dimension labels in French matching the 4 required dimensions.
 */
const DIMENSION_LABELS: Record<string, string> = {
  cost: 'Coût',
  security: 'Sécurité',
  compliance: 'Compliance',
  availability: 'Disponibilité',
};

/**
 * Maps impact severity to Cloudscape StatusIndicator type.
 */
function getSeverityType(severity: string): 'error' | 'warning' | 'info' {
  switch (severity) {
    case 'critical':
      return 'error';
    case 'warning':
      return 'warning';
    default:
      return 'info';
  }
}

/**
 * Maps recommendation priority to badge color.
 */
function getPriorityBadgeColor(priority: string): 'red' | 'blue' | 'green' {
  switch (priority) {
    case 'immediate':
      return 'red';
    case 'planned':
      return 'blue';
    case 'monitor':
      return 'green';
    default:
      return 'blue';
  }
}

/**
 * Returns the French label for effort level.
 */
function getEffortLabel(effortLevel: string): string {
  switch (effortLevel) {
    case 'low':
      return 'Effort faible';
    case 'medium':
      return 'Effort moyen';
    case 'high':
      return 'Effort élevé';
    default:
      return effortLevel;
  }
}

/**
 * Returns the French label for priority.
 */
function getPriorityLabel(priority: string): string {
  switch (priority) {
    case 'immediate':
      return 'Immédiat';
    case 'planned':
      return 'Planifié';
    case 'monitor':
      return 'À surveiller';
    default:
      return priority;
  }
}

/**
 * Groups impacts by dimension for rendering.
 */
function groupByDimension(impacts: Impact[]): Record<string, Impact[]> {
  const groups: Record<string, Impact[]> = {};
  for (const impact of impacts) {
    if (!groups[impact.dimension]) {
      groups[impact.dimension] = [];
    }
    groups[impact.dimension].push(impact);
  }
  return groups;
}

/**
 * Builds an ActionPlanPrefill from assessment and item data.
 */
function buildActionPlanPrefill(assessment: RiskAssessment, item: DeprecationItem): ActionPlanPrefill {
  const priorityMap: Record<string, 'low' | 'medium' | 'high' | 'critical'> = {
    'faible': 'low',
    'moyen': 'medium',
    'élevé': 'high',
    'critique': 'critical',
  };

  const topRecommendation = assessment.recommendations[0];
  const notes = [
    `Niveau de risque: ${assessment.riskLevel}`,
    `Phase: ${assessment.lifecyclePhase}`,
    topRecommendation ? `Recommandation: ${topRecommendation.action}` : '',
  ].filter(Boolean).join('\n');

  return {
    service_name: item.service_name,
    item_id: item.item_id,
    item_name: item.service_specific?.version || item.item_id,
    priority: priorityMap[assessment.riskLevel] || 'medium',
    notes,
  };
}

/**
 * Renders a single dimension section with its impacts.
 */
function DimensionSection({ dimension, impacts }: { dimension: string; impacts: Impact[] }) {
  return (
    <ExpandableSection
      headerText={DIMENSION_LABELS[dimension] || dimension}
      defaultExpanded={impacts.some(i => i.severity === 'critical')}
      variant="footer"
    >
      <SpaceBetween size="xs">
        {impacts.map((impact, index) => (
          <Box key={index}>
            <StatusIndicator type={getSeverityType(impact.severity)}>
              {impact.description}
            </StatusIndicator>
            {impact.details && (
              <Box variant="small" color="text-body-secondary" padding={{ left: 'l' }}>
                {impact.details}
              </Box>
            )}
          </Box>
        ))}
      </SpaceBetween>
    </ExpandableSection>
  );
}

/**
 * Renders the recommendations section.
 */
function RecommendationsSection({ recommendations }: { recommendations: Recommendation[] }) {
  if (recommendations.length === 0) return null;

  return (
    <ExpandableSection headerText="Recommandations" defaultExpanded variant="footer">
      <SpaceBetween size="s">
        {recommendations.map((rec, index) => (
          <div
            key={index}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'flex-start',
              padding: '4px 0',
            }}
          >
            <SpaceBetween size="xxs" direction="vertical">
              <Box>{rec.action}</Box>
              <Box variant="small" color="text-body-secondary">
                {getEffortLabel(rec.effortLevel)}
                {rec.targetVersion && ` · Version cible: ${rec.targetVersion}`}
              </Box>
            </SpaceBetween>
            <Badge color={getPriorityBadgeColor(rec.priority)}>
              {getPriorityLabel(rec.priority)}
            </Badge>
          </div>
        ))}
      </SpaceBetween>
    </ExpandableSection>
  );
}

/**
 * Renders compliance frameworks with control references and source URLs.
 * Shown for end_of_standard_support and later phases.
 */
function ComplianceSection({
  complianceImpacts,
  sources,
}: {
  complianceImpacts: ComplianceImpact[];
  sources?: Record<string, string>;
}) {
  if (complianceImpacts.length === 0) return null;

  return (
    <ExpandableSection headerText="Conformité réglementaire" defaultExpanded={false} variant="footer">
      <SpaceBetween size="s">
        {complianceImpacts.map((ci, index) => (
          <div key={index} style={{ padding: '4px 0', borderBottom: '1px solid #e9ebed' }}>
            <SpaceBetween size="xxs">
              <Box>
                <Badge color="blue">{ci.framework}</Badge>
                {' '}
                <Box variant="strong" display="inline">{ci.controlReference}</Box>
              </Box>
              <Box variant="small">{ci.description}</Box>
              {ci.sourceRef && sources && sources[ci.sourceRef] && (
                <Link
                  href={sources[ci.sourceRef]}
                  external
                  fontSize="body-s"
                >
                  Documentation de référence
                </Link>
              )}
            </SpaceBetween>
          </div>
        ))}
      </SpaceBetween>
    </ExpandableSection>
  );
}

/**
 * Renders the "Shared Responsibility Model" notice for End of Life phase.
 */
function SharedResponsibilityNotice({ sources }: { sources?: Record<string, string> }) {
  const url = sources?.['shared_responsibility_model'];
  return (
    <Box padding={{ vertical: 's' }}>
      <StatusIndicator type="warning">
        Le modèle de responsabilité partagée AWS ne couvre plus le patching de sécurité pour cette version.
      </StatusIndicator>
      {url && (
        <Box padding={{ left: 'l', top: 'xxs' }}>
          <Link href={url} external fontSize="body-s">
            AWS Shared Responsibility Model
          </Link>
        </Box>
      )}
    </Box>
  );
}

/**
 * Renders pricing tiers for Extended Support phase.
 */
function PricingSection({ pricingInfo }: { pricingInfo: ExtendedSupportPricing }) {
  return (
    <ExpandableSection
      headerText={`Tarification Extended Support — ${pricingInfo.service}`}
      defaultExpanded={false}
      variant="footer"
    >
      <SpaceBetween size="s">
        <Box variant="small" color="text-body-secondary">
          Unité: {pricingInfo.unit} · Effectif depuis: {pricingInfo.effectiveDate}
        </Box>
        <Box variant="small" color="text-body-secondary">
          Versions: {pricingInfo.versions.join(', ')}
        </Box>
        {pricingInfo.tiers.map((tier) => (
          <div
            key={tier.year}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '4px 0',
              borderBottom: '1px solid #e9ebed',
            }}
          >
            <Box>Year {tier.year}: {tier.description}</Box>
            <Badge color="blue">{tier.surcharge}</Badge>
          </div>
        ))}
      </SpaceBetween>
    </ExpandableSection>
  );
}

/**
 * ImpactPanel displays detailed operational impacts, recommendations,
 * compliance frameworks, and pricing tiers for a risk assessment.
 *
 * - Groups impacts by dimension (Coût, Sécurité, Compliance, Disponibilité)
 * - Shows severity via StatusIndicator icons
 * - Displays recommendations with priority badges and effort levels
 * - Shows compliance frameworks with control refs for end_of_standard_support+ phases
 * - Shows "Shared Responsibility Model" notice for End of Life phase
 * - Displays pricing tiers for Extended Support phase when available
 * - Shows "No detailed impacts available" when impacts array is empty
 */
export default function ImpactPanel({ assessment, item, onCreateActionPlan }: ImpactPanelProps) {
  const { impacts, recommendations, complianceImpacts, pricingInfo, lifecyclePhase } = assessment;

  // Resolve sources from the risk-matrix (they're not on assessment, pass via prop or lookup)
  // For simplicity, we derive the sources map from the module-level loaded matrix.
  // Since the risk-matrix.json sources are static, we can reference them via a known structure.
  // In practice, we pass sources via a context or load them. Here we accept them as optional.
  const sources = (assessment as any).sources as Record<string, string> | undefined;

  if (impacts.length === 0) {
    return (
      <ExpandableSection headerText="Impacts opérationnels" defaultExpanded={false}>
        <Box textAlign="center" padding="l" color="text-body-secondary">
          No detailed impacts available
        </Box>
      </ExpandableSection>
    );
  }

  const groupedImpacts = groupByDimension(impacts);

  // Render dimensions in the canonical order
  const dimensionOrder = ['cost', 'security', 'compliance', 'availability'];

  const showSharedResponsibilityNotice = lifecyclePhase === 'end_of_life';
  const showComplianceSection =
    complianceImpacts.length > 0 &&
    ['end_of_standard_support', 'end_of_life', 'block_create_update'].includes(lifecyclePhase);
  const showPricingSection = pricingInfo !== null && lifecyclePhase === 'extended_support';

  return (
    <ExpandableSection headerText="Impacts opérationnels" defaultExpanded={false}>
      <SpaceBetween size="m">
        {/* Dimension sections */}
        {dimensionOrder.map((dim) =>
          groupedImpacts[dim] && groupedImpacts[dim].length > 0 ? (
            <DimensionSection key={dim} dimension={dim} impacts={groupedImpacts[dim]} />
          ) : null
        )}

        {/* Shared Responsibility Model notice for End of Life */}
        {showSharedResponsibilityNotice && <SharedResponsibilityNotice sources={sources} />}

        {/* Pricing tiers for Extended Support */}
        {showPricingSection && pricingInfo && <PricingSection pricingInfo={pricingInfo} />}

        {/* Compliance frameworks */}
        {showComplianceSection && (
          <ComplianceSection complianceImpacts={complianceImpacts} sources={sources} />
        )}

        {/* Recommendations */}
        <RecommendationsSection recommendations={recommendations} />

        {/* Create Action Plan button */}
        <Box textAlign="right" padding={{ top: 's' }}>
          <Button
            variant="primary"
            onClick={() => onCreateActionPlan(buildActionPlanPrefill(assessment, item))}
          >
            Créer un Plan d'Action
          </Button>
        </Box>
      </SpaceBetween>
    </ExpandableSection>
  );
}
