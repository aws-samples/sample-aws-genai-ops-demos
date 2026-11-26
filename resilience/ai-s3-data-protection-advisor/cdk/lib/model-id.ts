// Mirrors NOVA_LITE_GEO in backend/s3dp/cli.py: first matching prefix wins, there is no global profile.
const NOVA_LITE = 'amazon.nova-lite-v1:0';
const NOVA_LITE_GEO: Array<[string, string]> = [
  ['us-gov-', ''],
  ['us-', 'us'],
  ['ca-', 'ca'],
  ['eu-', 'eu'],
  ['il-', 'eu'],
  ['ap-', 'apac'],
];

export const NOVA_LITE_FOUNDATION_MODEL = NOVA_LITE;

/** Amazon Nova Lite inference profile id of the region's geography, or "" when none is known. */
export function novaLiteModelId(region: string, override?: string): string {
  if (override) {
    return override;
  }
  const geo = NOVA_LITE_GEO.find(([prefix]) => region.startsWith(prefix));
  return geo && geo[1] ? `${geo[1]}.${NOVA_LITE}` : '';
}
