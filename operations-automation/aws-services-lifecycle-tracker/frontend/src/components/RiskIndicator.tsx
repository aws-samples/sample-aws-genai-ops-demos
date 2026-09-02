import React from 'react';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Popover from '@cloudscape-design/components/popover';
import Box from '@cloudscape-design/components/box';
import { RiskAssessment, RiskLevel } from '../risk/types';

/**
 * Props for the RiskIndicator component.
 */
export interface RiskIndicatorProps {
  /** The risk assessment to display. If null/undefined, renders nothing. */
  assessment: RiskAssessment | null | undefined;
  /** Whether to show tooltip on hover. Defaults to true. */
  showTooltip?: boolean;
}

/**
 * Maps a RiskLevel to a Cloudscape StatusIndicator type.
 * - critique → error (red)
 * - élevé → warning (orange)
 * - moyen → info (blue)
 * - faible → success (green)
 */
function getStatusType(level: RiskLevel): 'error' | 'warning' | 'info' | 'success' {
  switch (level) {
    case 'critique':
      return 'error';
    case 'élevé':
      return 'warning';
    case 'moyen':
      return 'info';
    case 'faible':
      return 'success';
    default:
      return 'info';
  }
}

/**
 * Maps a RiskLevel to its French display label.
 */
function getRiskLabel(level: RiskLevel): string {
  switch (level) {
    case 'critique':
      return 'Critique';
    case 'élevé':
      return 'Élevé';
    case 'moyen':
      return 'Moyen';
    case 'faible':
      return 'Faible';
    default:
      return level;
  }
}

/**
 * Validates that an assessment object has the minimum required shape.
 */
function isValidAssessment(assessment: unknown): assessment is RiskAssessment {
  if (!assessment || typeof assessment !== 'object') return false;
  const a = assessment as Record<string, unknown>;
  const validLevels: string[] = ['critique', 'élevé', 'moyen', 'faible'];
  return typeof a.riskLevel === 'string' && validLevels.includes(a.riskLevel);
}

/**
 * RiskIndicator displays a color-coded risk badge using Cloudscape StatusIndicator.
 *
 * It maps risk levels to colors (critique→red, élevé→orange, moyen→blue, faible→green)
 * and wraps the badge in a Popover that shows the assessment's tooltipSummary on hover.
 *
 * Graceful degradation: renders nothing if assessment is null, undefined, or malformed.
 *
 * @requirements 2.1, 2.2, 2.3, 2.4
 */
export default function RiskIndicator({ assessment, showTooltip = true }: RiskIndicatorProps): React.ReactElement | null {
  // Graceful degradation: render nothing if assessment is null/malformed
  if (!isValidAssessment(assessment)) {
    return null;
  }

  const statusType = getStatusType(assessment.riskLevel);
  const label = getRiskLabel(assessment.riskLevel);

  const indicator = (
    <StatusIndicator type={statusType}>
      {label}
    </StatusIndicator>
  );

  // If tooltip is disabled or no summary available, render badge without popover
  if (!showTooltip || !assessment.tooltipSummary) {
    return indicator;
  }

  return (
    <Popover
      dismissButton={false}
      position="top"
      size="small"
      triggerType="custom"
      content={
        <Box variant="small">{assessment.tooltipSummary}</Box>
      }
    >
      {indicator}
    </Popover>
  );
}
