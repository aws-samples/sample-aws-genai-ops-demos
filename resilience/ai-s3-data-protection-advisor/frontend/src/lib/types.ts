// Types of a report's data.json (the same shape for every run and for the bundled sample).

export type RiskKey = 'he' | 'ma' | 'rd';

/** Protection state of one risk for one bucket. */
export type ProtectionState =
  | 'protected'
  | 'protected_governance'
  | 'protected_lock'
  | 'likely'
  | 'partial'
  | 'unprotected'
  | 'unknown';

/** State of one control cell in the matrix. */
export type CellState = 'ok' | 'missing' | 'partial' | 'na' | 'unknown';

export type Severity = 'HIGH' | 'MEDIUM' | 'LOW';

export interface ReportAi {
  classification: boolean;
  narrative: string;
}

export interface ReportMeta {
  service: string;
  account?: string;
  customerName?: string;
  generated: string;
  audience?: string;
  ai?: ReportAi | null;
  sample?: boolean;
}

export interface ControlRow {
  control: string;
  value: string;
  cells: Record<RiskKey, [string, string]>;
}

export interface LabelValue {
  label: string;
  value: string;
}

export interface Finding {
  severity: Severity;
  title: string;
  description: string;
}

export interface BucketRow {
  bucket: string;
  account: string;
  account_id: string;
  region: string;
  role: string;
  size_bytes: number;
  size_human: string;
  size_short: string;
  chips: string[];
  he: string;
  ma: string;
  rd: string;
  req: string[];
  rec: string[];
  gap: string[];
  status: string;
  status_color: string;
  action: string;
  vce: string;
  vce_usd: number | null;
  score: number;
  tier: string;
  spotlight: boolean;
  reasons: string[];
  third_party: string;
  findings: Finding[];
  evidence: LabelValue[];
  controls: ControlRow[];
}

export interface BucketCard {
  id: string;
  title: string;
  subtitle: string;
  badge: { text: string; color: string };
  sortValue: number;
  details: LabelValue[];
  recommendations: Finding[];
  row: BucketRow;
}

export interface InstructionsTab {
  id: string;
  label: string;
  type: 'instructions';
  heading: string;
  intro: string;
  sections: {
    title: string;
    items?: string[];
    table?: { columns: string[]; rows: string[][] };
    note?: string;
  }[];
}

export interface CardsTab {
  id: string;
  label: string;
  type: 'cards';
  cards: BucketCard[];
}

export interface ReviewCheck {
  label: string;
  pass: boolean;
  description?: string;
  severity?: Severity;
}

export interface ReviewPillar {
  name: string;
  pass: number;
  total: number;
  checks: ReviewCheck[];
}

export interface OperationalReviewTab {
  id: string;
  label: string;
  type: 'operational-review';
  description: string;
  pillars: string[];
  resources: { name: string; region: string; pillars: ReviewPillar[] }[];
}

export interface RecommendationItem {
  account: string;
  account_name: string;
  bucket: string;
  region: string;
  size_bytes: number;
  size_human: string;
  tier: string;
  status: string;
  severity: Severity;
  recommendation: string;
  why: string;
  detail: string;
  priority: number;
  resource: string;
  title: string;
  description: string;
  inconsistency?: string;
}

export interface RecommendationsTab {
  id: string;
  label: string;
  type: 'recommendations';
  items: RecommendationItem[];
}

export interface InconsistencyMember {
  account: string;
  bucket: string;
  region: string;
  value: string;
}

export interface InconsistencyItem {
  family: string;
  match: string;
  relation: string;
  basis: string;
  members: InconsistencyMember[];
  setting: string;
  severity: Severity;
  align_to: string | null;
  weaker: string[];
  recommendation: string;
}

export interface InconsistenciesTab {
  id: string;
  label: string;
  type: 'inconsistencies';
  families_compared: number;
  items: InconsistencyItem[];
  consistent_families: { family: string; note: string }[];
  note?: string;
}

export interface GovernanceTab {
  id: string;
  label: string;
  type: 'governance';
  overall: number;
  overall_level: number;
  overall_label: string;
  summary: string;
  levels: { level: number; label: string; description: string }[];
  dimensions: {
    id: string;
    name: string;
    level: number;
    evidence: string[];
    current: string;
    next: string;
  }[];
  policies: {
    rule: string;
    scope: number;
    compliant: number;
    total: number;
    pct: number;
    failing: string[];
    why: string;
  }[];
  findings: {
    severity: Severity;
    title: string;
    evidence: string;
    improvement: string;
    dimension: string;
  }[];
}

export interface CostItem {
  title: string;
  resources: string[];
  savings: string;
  effort: string;
  description: string;
  impact: 'saving' | 'cost' | 'avoided' | string;
  monthly_usd: number;
  one_time_usd?: number;
  basis: string;
  up_to?: boolean;
  worst_case_usd?: number;
  magnitude: { level: number; label: string };
  annual_usd: number;
  estimatedSaving?: number;
}

export interface CostOptimizationTab {
  id: string;
  label: string;
  type: 'cost-optimization';
  items: CostItem[];
}

export type Tab =
  | InstructionsTab
  | CardsTab
  | OperationalReviewTab
  | RecommendationsTab
  | InconsistenciesTab
  | GovernanceTab
  | CostOptimizationTab;

export interface Report {
  meta: ReportMeta;
  tabs: Tab[];
}
