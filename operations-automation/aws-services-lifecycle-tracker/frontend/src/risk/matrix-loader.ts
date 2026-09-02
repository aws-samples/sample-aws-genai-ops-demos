/**
 * RiskMatrixLoader module — loads and caches the risk matrix configuration.
 *
 * Fetches `/risk-matrix.json` from public assets. Falls back to a hardcoded
 * DEFAULT_RISK_MATRIX with conservative risk levels on fetch failure or
 * invalid JSON.
 *
 * The matrix is cached in module scope so it is loaded only once at startup.
 */

import type { RiskMatrix } from './types';

// --- Module-scoped cache ---

let cachedMatrix: RiskMatrix | null = null;

// --- Default matrix (conservative fallback) ---

/**
 * Hardcoded default risk matrix with conservative (elevated) risk levels.
 * Used when risk-matrix.json fails to load or contains invalid data.
 * Contains minimal impact/recommendation data — just enough to function.
 */
const DEFAULT_RISK_MATRIX: RiskMatrix = {
  version: '1.0.0-default',
  lastUpdated: '2025-01-01',

  phaseRiskMapping: {
    extended_support: {
      baseRiskLevel: 'moyen',
      impacts: [
        {
          dimension: 'cost',
          severity: 'warning',
          description: 'Surco\u00FBt AWS Extended Support appliqu\u00E9 par instance-heure',
        },
      ],
      recommendations: [
        {
          action: 'Planifier la migration vers la derni\u00E8re version LTS support\u00E9e',
          effortLevel: 'medium',
          priority: 'monitor',
          targetVersion: 'latest LTS',
        },
      ],
    },
    end_of_standard_support: {
      baseRiskLevel: '\u00E9lev\u00E9',
      impacts: [
        {
          dimension: 'security',
          severity: 'critical',
          description: 'Aucun patch de s\u00E9curit\u00E9 automatique fourni par AWS',
        },
      ],
      recommendations: [
        {
          action: "Planifier la migration imm\u00E9diatement avec estimation de l'effort",
          effortLevel: 'medium',
          priority: 'planned',
          targetVersion: 'latest LTS',
        },
      ],
      complianceFrameworks: [
        {
          framework: 'SOC2',
          controlReference: 'CC6.1',
          description:
            'Contr\u00F4les de s\u00E9curit\u00E9 logique \u2014 les syst\u00E8mes non-patch\u00E9s ne satisfont pas les exigences',
          sourceRef: 'soc2_cc6_1',
        },
        {
          framework: 'PCI-DSS',
          controlReference: 'Requirement 6.2',
          description:
            'Installation des correctifs de s\u00E9curit\u00E9 critiques impossible sans support actif',
          sourceRef: 'pci_dss_req_6_2',
        },
        {
          framework: 'HIPAA',
          controlReference: '\u00A7164.308(a)(1)',
          description:
            'Gestion de la s\u00E9curit\u00E9 \u2014 analyse de risque doit corriger les vuln\u00E9rabilit\u00E9s connues',
          sourceRef: 'hipaa_security_rule',
        },
      ],
    },
    end_of_life: {
      baseRiskLevel: 'critique',
      impacts: [
        {
          dimension: 'availability',
          severity: 'critical',
          description: "Aucun support AWS disponible en cas d'incident",
        },
      ],
      recommendations: [
        {
          action:
            "Migration d'urgence requise \u2014 migrer vers la derni\u00E8re version support\u00E9e",
          effortLevel: 'high',
          priority: 'immediate',
          targetVersion: 'latest LTS',
        },
      ],
      complianceFrameworks: [
        {
          framework: 'SOC2',
          controlReference: 'CC6.1',
          description:
            'Violation directe \u2014 aucun m\u00E9canisme de protection contre les vuln\u00E9rabilit\u00E9s connues',
          sourceRef: 'soc2_cc6_1',
        },
        {
          framework: 'PCI-DSS',
          controlReference: 'Requirement 6.2',
          description:
            'Non-conformit\u00E9 critique \u2014 correctifs de s\u00E9curit\u00E9 inapplicables',
          sourceRef: 'pci_dss_req_6_2',
        },
        {
          framework: 'HIPAA',
          controlReference: '\u00A7164.308(a)(1)',
          description:
            'Violation \u2014 risques identifi\u00E9s non corrigeables via les m\u00E9canismes du fournisseur',
          sourceRef: 'hipaa_security_rule',
        },
      ],
    },
    block_create_update: {
      baseRiskLevel: 'critique',
      impacts: [
        {
          dimension: 'availability',
          severity: 'critical',
          description: 'Impossibilit\u00E9 de cr\u00E9er ou modifier des ressources',
        },
      ],
      recommendations: [
        {
          action:
            'Compl\u00E9ter la migration imm\u00E9diatement avant le blocage total des op\u00E9rations',
          effortLevel: 'high',
          priority: 'immediate',
          targetVersion: 'latest LTS',
        },
      ],
      complianceFrameworks: [
        {
          framework: 'SOC2',
          controlReference: 'CC6.1',
          description:
            "Contr\u00F4les inop\u00E9rants \u2014 impossible d'impl\u00E9menter les contr\u00F4les de s\u00E9curit\u00E9",
          sourceRef: 'soc2_cc6_1',
        },
        {
          framework: 'PCI-DSS',
          controlReference: 'Requirement 6.2',
          description:
            "Non-conformit\u00E9 totale \u2014 impossible d'appliquer quelque correctif que ce soit",
          sourceRef: 'pci_dss_req_6_2',
        },
        {
          framework: 'HIPAA',
          controlReference: '\u00A7164.308(a)(1)',
          description:
            'Violation grave \u2014 impossibilit\u00E9 technique de corriger les vuln\u00E9rabilit\u00E9s',
          sourceRef: 'hipaa_security_rule',
        },
      ],
    },
  },

  temporalRules: {
    escalationThresholdDays: 90,
    deminimisThresholdDays: 365,
  },

  extendedSupportPricing: [],

  sources: {},
};

// --- Public API ---

/**
 * Returns the hardcoded default risk matrix with conservative risk levels.
 * Used as fallback when the JSON configuration file cannot be loaded.
 */
export function getDefaultMatrix(): RiskMatrix {
  return DEFAULT_RISK_MATRIX;
}

/**
 * Loads the risk matrix configuration from `/risk-matrix.json`.
 * Results are cached in module scope — subsequent calls return the cached version
 * without re-fetching.
 *
 * On fetch failure or invalid JSON, falls back to `getDefaultMatrix()` and
 * logs a warning to the console.
 */
export async function loadRiskMatrix(): Promise<RiskMatrix> {
  if (cachedMatrix !== null) {
    return cachedMatrix;
  }

  try {
    const response = await fetch('/risk-matrix.json');

    if (!response.ok) {
      console.warn(
        `[RiskMatrixLoader] Failed to load risk-matrix.json (HTTP ${response.status}). Using default matrix.`
      );
      cachedMatrix = getDefaultMatrix();
      return cachedMatrix;
    }

    const data: RiskMatrix = await response.json();
    cachedMatrix = data;
    return cachedMatrix;
  } catch (error) {
    console.warn(
      '[RiskMatrixLoader] Failed to load or parse risk-matrix.json. Using default matrix.',
      error
    );
    cachedMatrix = getDefaultMatrix();
    return cachedMatrix;
  }
}

/**
 * Resets the module cache. Intended for testing purposes only.
 * @internal
 */
export function _resetCache(): void {
  cachedMatrix = null;
}
