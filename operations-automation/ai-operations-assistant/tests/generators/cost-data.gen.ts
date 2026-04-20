/**
 * fast-check arbitraries for Cost Agent data objects.
 * Validates: Requirements 1.1, 2.3
 */
import fc from 'fast-check';

/** ISO date string arbitrary within a reasonable range */
const arbISODate = fc
  .date({ min: new Date('2023-01-01'), max: new Date('2025-12-31') })
  .map((d) => d.toISOString().slice(0, 10));

/** Positive dollar amount with two decimal places */
const arbAmount = fc.double({ min: 0.01, max: 999_999.99, noNaN: true }).map((n) => Math.round(n * 100) / 100);

/** Currency code */
const arbCurrency = fc.constantFrom('USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD');

/** Granularity for cost queries */
const arbGranularity = fc.constantFrom<'DAILY' | 'MONTHLY'>('DAILY', 'MONTHLY');

/** Cost metric type */
const arbMetric = fc.constantFrom<'BLENDED_COST' | 'UNBLENDED_COST' | 'AMORTIZED_COST'>(
  'BLENDED_COST',
  'UNBLENDED_COST',
  'AMORTIZED_COST',
);

/** Group-by dimension */
const arbGroupBy = fc.subarray(['SERVICE', 'REGION', 'LINKED_ACCOUNT', 'USAGE_TYPE'], { minLength: 0, maxLength: 4 });

/** A single cost result group entry */
export const arbCostResultGroup = fc.record({
  key: fc.string({ minLength: 1, maxLength: 40 }),
  amount: arbAmount,
  unit: arbCurrency,
});

/** A single time-period cost entry */
export const arbCostTimePeriod = fc.record({
  start: arbISODate,
  end: arbISODate,
  total: arbAmount,
  currency: arbCurrency,
  groups: fc.array(arbCostResultGroup, { minLength: 0, maxLength: 5 }),
});

/** CostAndUsageParams matching the interface in types.ts */
export const arbCostAndUsageParams = fc.record({
  startDate: arbISODate,
  endDate: arbISODate,
  granularity: arbGranularity,
  groupBy: fc.option(arbGroupBy, { nil: undefined }),
  filter: fc.option(
    fc.dictionary(
      fc.constantFrom('SERVICE', 'REGION', 'LINKED_ACCOUNT'),
      fc.string({ minLength: 1, maxLength: 30 }),
      { minKeys: 0, maxKeys: 2 },
    ),
    { nil: undefined },
  ),
});

/** CostForecastParams matching the interface in types.ts */
export const arbCostForecastParams = fc.record({
  startDate: arbISODate,
  endDate: arbISODate,
  granularity: arbGranularity,
  metric: arbMetric,
});

/** RecommendationsParams matching the interface in types.ts */
export const arbRecommendationsParams = fc.record({
  category: fc.option(
    fc.constantFrom<'cost_optimizing' | 'security' | 'performance'>('cost_optimizing', 'security', 'performance'),
    { nil: undefined },
  ),
  maxResults: fc.option(fc.integer({ min: 1, max: 100 }), { nil: undefined }),
});

/** A formatted cost data response (what the Cost Agent returns) */
export const arbCostDataResponse = fc.record({
  success: fc.constant(true),
  domain: fc.constant('cost'),
  data: fc.record({
    timePeriods: fc.array(arbCostTimePeriod, { minLength: 1, maxLength: 12 }),
  }),
  formattedText: fc.string({ minLength: 10, maxLength: 500 }),
  metadata: fc.record({
    sourceApi: fc.constantFrom('ce:GetCostAndUsage', 'ce:GetCostForecast', 'coh:ListRecommendations'),
    queryTimestamp: fc.date({ min: new Date('2024-01-01') }).map((d) => d.toISOString()),
    dataFreshness: fc.constantFrom('real-time', '24-hours', '48-hours'),
  }),
});

/** Cost data with a previous-period comparison */
export const arbCostComparison = fc.record({
  currentPeriod: arbCostTimePeriod,
  previousPeriod: arbCostTimePeriod,
  percentageChange: fc.double({ min: -100, max: 500, noNaN: true }).map((n) => Math.round(n * 100) / 100),
});
