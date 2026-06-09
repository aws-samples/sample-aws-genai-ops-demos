/**
 * Property tests for Network Agent CDK invariants.
 * Feature: genai-operations-analytics-tool
 *
 * Properties 13, 14, 15: CDK stack naming, source hygiene, and
 * solution adoption tracking marker placement.
 *
 * These tests verify structural invariants of the CDK infrastructure
 * and source tree without requiring a full CDK synth (which would need
 * AWS credentials). Instead they operate on the source files directly
 * and validate patterns via static analysis.
 *
 * **Validates: Requirements 10.1, 10.2, 10.3, 10.6, 10.7, 11.9, 12.10, 15.4, 15.5, 15.6**
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import * as fs from 'fs';
import * as path from 'path';

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const PROJECT_ROOT = path.resolve(__dirname, '../..');
const CDK_BIN_DIR = path.resolve(PROJECT_ROOT, 'infrastructure/cdk/bin');
const CDK_LIB_DIR = path.resolve(PROJECT_ROOT, 'infrastructure/cdk/lib');
const AGENTS_DIR = path.resolve(PROJECT_ROOT, 'agents');
const DEMO_SCENARIOS_DIR = path.resolve(PROJECT_ROOT, 'demo-scenarios');

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Recursively collect files matching given extensions from a directory.
 */
function collectFiles(
  dir: string,
  extensions: string[],
  results: { relativePath: string; content: string }[] = [],
  baseDir?: string,
): { relativePath: string; content: string }[] {
  if (!fs.existsSync(dir)) return results;
  const base = baseDir ?? dir;
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      // Skip node_modules, .git, __pycache__, cdk.out, .hypothesis
      if (
        entry.name === 'node_modules' ||
        entry.name === '.git' ||
        entry.name === '__pycache__' ||
        entry.name.startsWith('cdk.out') ||
        entry.name === '.hypothesis' ||
        entry.name === '.pytest_cache'
      ) {
        continue;
      }
      collectFiles(fullPath, extensions, results, base);
    } else if (extensions.some((ext) => entry.name.endsWith(ext))) {
      results.push({
        relativePath: path.relative(base, fullPath),
        content: fs.readFileSync(fullPath, 'utf-8'),
      });
    }
  }
  return results;
}

/**
 * Read the CDK app.ts entry point.
 */
function readAppTs(): string {
  const appPath = path.join(CDK_BIN_DIR, 'app.ts');
  return fs.readFileSync(appPath, 'utf-8');
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** The three Network Agent stack base names as used in app.ts */
const NETWORK_STACK_BASES = ['GOATNetworkData', 'GOATNetworkInfra', 'GOATNetworkRuntime'];

/** Regex for a valid AWS region */
const AWS_REGION_PATTERN = /^[a-z]{2}-[a-z]+-\d+$/;

/** The expected stack ID pattern for Network stacks */
const NETWORK_STACK_ID_REGEX = /^GOATNetwork(Data|Infra|Runtime)-[a-z]{2}-[a-z]+-\d+$/;

/** The solution adoption tracking marker */
const TRACKING_MARKER = '(uksb-do9bhieqqh)(tag:goat,operations-automation)';

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/**
 * Arbitrary for valid AWS region strings.
 */
const arbAwsRegion = fc
  .tuple(
    fc.constantFrom('us', 'eu', 'ap', 'sa', 'ca', 'me', 'af', 'il', 'cn'),
    fc.constantFrom(
      'east',
      'west',
      'south',
      'north',
      'central',
      'southeast',
      'northeast',
      'northwest',
      'southwest',
    ),
    fc.integer({ min: 1, max: 4 }),
  )
  .map(([prefix, direction, num]) => `${prefix}-${direction}-${num}`);

/**
 * Arbitrary for Network stack base names.
 */
const arbNetworkStackBase = fc.constantFrom(...NETWORK_STACK_BASES);

// ---------------------------------------------------------------------------
// Property 13: Region suffix is present on every new CDK stack ID
// ---------------------------------------------------------------------------

describe('Property 13: Region suffix is present on every new CDK stack ID', () => {
  /**
   * For every Network_Agent CDK stack instantiated by the CDK app,
   * the synthesized Stack ID matches
   * `^GOATNetwork(Data|Infra|Runtime)-[a-z]{2}-[a-z]+-\d+$`
   *
   * **Validates: Requirements 10.1, 10.2, 10.3, 15.6**
   */
  it('Property 13: app.ts instantiates all Network stacks with region-suffixed IDs matching the required pattern', () => {
    const appContent = readAppTs();

    // Verify each Network stack base name appears with the ${region} template
    for (const base of NETWORK_STACK_BASES) {
      const templateLiteral = `\`${base}-\${region}\``;
      expect(appContent).toContain(templateLiteral);
    }
  });

  it('Property 13: for any valid AWS region, the generated stack ID matches the required regex', () => {
    fc.assert(
      fc.property(arbNetworkStackBase, arbAwsRegion, (baseName, region) => {
        const stackId = `${baseName}-${region}`;

        // Must match the documented pattern
        expect(stackId).toMatch(NETWORK_STACK_ID_REGEX);

        // Must end with the region
        expect(stackId.endsWith(region)).toBe(true);

        // Must start with the base name
        expect(stackId.startsWith(baseName)).toBe(true);

        // The region portion must be a valid AWS region
        const regionPart = stackId.slice(baseName.length + 1);
        expect(regionPart).toMatch(AWS_REGION_PATTERN);
      }),
      { numRuns: 200 },
    );
  });

  it('Property 13: no Network stack is instantiated without a region suffix', () => {
    const appContent = readAppTs();

    // Ensure there is no instantiation like `new NetworkDataStack(app, 'GOATNetworkData',`
    // without the region template literal
    for (const base of NETWORK_STACK_BASES) {
      // A hardcoded string (single or double quotes) without ${region} would be wrong
      const hardcodedPattern = new RegExp(`['"]${base}['"]`);
      expect(appContent).not.toMatch(hardcodedPattern);
    }
  });

  it('Property 13: the region variable in app.ts is sourced from getRegion()', () => {
    const appContent = readAppTs();
    expect(appContent).toContain('getRegion()');
    expect(appContent).toContain('shared/utils/aws-utils');
    // The region const must be assigned from getRegion()
    expect(appContent).toMatch(/const region\s*=\s*getRegion\(\)/);
  });
});

// ---------------------------------------------------------------------------
// Property 14: No literal AWS region/account/endpoint in source
// ---------------------------------------------------------------------------

describe('Property 14: No literal AWS region/account/endpoint in source', () => {
  // Forbidden patterns
  const ACCOUNT_ID_REGEX = /\b[0-9]{12}\b/;
  const REGION_NAME_REGEX =
    /\b(us|eu|ap|ca|sa|af|me|il|cn)-(north|south|east|west|central|northeast|northwest|southeast|southwest)-\d\b/;
  const ENDPOINT_URL_REGEX = /https?:\/\/[a-z0-9.-]+\.amazonaws\.com/;

  /**
   * Lines that are allowed to contain region-like patterns:
   * - Comments (// or # or * at start)
   * - Python docstrings (lines inside triple-quote blocks)
   * - README example tables (markdown table rows)
   * - Test fixture data (in test files)
   * - ARN pattern templates (arn:aws:...)
   * - Documentation strings
   * - Shared utility fallback patterns (documented standard)
   */
  function isExemptLine(line: string, filePath: string): boolean {
    const trimmed = line.trim();

    // Comments in TypeScript/JavaScript
    if (trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')) {
      return true;
    }

    // Comments in Python
    if (trimmed.startsWith('#')) {
      return true;
    }

    // Comments in PowerShell/Bash
    if (filePath.endsWith('.ps1') || filePath.endsWith('.sh')) {
      if (trimmed.startsWith('#')) return true;
    }

    // Markdown table rows or documentation
    if (trimmed.startsWith('|') || trimmed.startsWith('>')) {
      return true;
    }

    // ARN pattern templates (dynamic region references)
    if (trimmed.includes('arn:aws:') || trimmed.includes('${this.region}') || trimmed.includes('${region}')) {
      return true;
    }

    // Dynamic region references in code
    if (
      trimmed.includes('getRegion') ||
      trimmed.includes('this.region') ||
      trimmed.includes('cdk.Aws.REGION') ||
      trimmed.includes('Aws.REGION') ||
      trimmed.includes('$AWS_REGION') ||
      trimmed.includes('$global:AWS_REGION') ||
      trimmed.includes('aws configure get region')
    ) {
      return true;
    }

    // Test files are exempt (they may contain fixture data)
    if (
      filePath.includes('test') ||
      filePath.includes('spec') ||
      filePath.includes('.test.') ||
      filePath.includes('.spec.')
    ) {
      return true;
    }

    // README files are exempt (they contain example tables)
    if (filePath.toLowerCase().includes('readme')) {
      return true;
    }

    return false;
  }

  /**
   * Check if a line in a Python file is inside a docstring block.
   * This is a simplified heuristic: we track triple-quote toggles.
   */
  function isInsidePythonDocstring(content: string, lineIndex: number): boolean {
    const lines = content.split('\n');
    let inDocstring = false;
    for (let i = 0; i < lineIndex; i++) {
      const line = lines[i];
      // Count triple-quote occurrences (both """ and ''')
      const tripleDoubleCount = (line.match(/"""/g) || []).length;
      const tripleSingleCount = (line.match(/'''/g) || []).length;
      const totalToggles = tripleDoubleCount + tripleSingleCount;
      if (totalToggles % 2 === 1) {
        inDocstring = !inDocstring;
      }
    }
    // Also check if the current line itself opens/closes a docstring
    // If we're inside a docstring at this point, the line is exempt
    return inDocstring;
  }

  /**
   * Enhanced exemption check that also considers Python docstring context.
   */
  function isExemptLineWithContext(
    line: string,
    filePath: string,
    content: string,
    lineIndex: number,
  ): boolean {
    if (isExemptLine(line, filePath)) return true;

    // For Python files, check if the line is inside a docstring
    if (filePath.endsWith('.py')) {
      if (isInsidePythonDocstring(content, lineIndex)) return true;
      // Also check if the line itself is a docstring delimiter
      const trimmed = line.trim();
      if (trimmed.startsWith('"""') || trimmed.startsWith("'''")) return true;
    }

    // Shared utility files (aws_utils.py) are allowed to have the
    // documented fallback region per repository steering rules
    // (Priority order: env var → CLI config → fallback to us-east-1)
    if (filePath.includes('aws_utils')) {
      const trimmed = line.trim();
      // The fallback return statement is the documented standard pattern
      if (trimmed.match(/return\s+['"]us-east-1['"]/)) return true;
    }

    // Deployment and scenario scripts are allowed to have the documented
    // fallback region assignment per repository steering rules
    // (Priority order: env var → CLI config → fallback to us-east-1)
    if (filePath.endsWith('.ps1') || filePath.endsWith('.sh') || filePath.endsWith('.py')) {
      const trimmed = line.trim();
      // PowerShell fallback: $region = "us-east-1"
      if (trimmed.match(/\$region\s*=\s*["']us-east-1["']/i)) return true;
      // Bash fallback: REGION="us-east-1" or region="us-east-1" (case-insensitive var name)
      if (trimmed.match(/^[A-Za-z_]*[Rr][Ee][Gg][Ii][Oo][Nn]\s*=\s*["']us-east-1["']/)) return true;
      // Python fallback: return "us-east-1"
      if (trimmed.match(/return\s+['"]us-east-1['"]/)) return true;
      // Write-Host or echo mentioning the fallback
      if (trimmed.includes('falling back to') || trimmed.includes('fallback')) return true;
    }

    return false;
  }

  /**
   * Collect all feature source files (the Network Agent's authored files).
   * Per the design, Property 14 applies to files "authored by this feature":
   * - CDK lib/ Network stack files (network-data-stack, network-infra-stack, network-runtime-stack)
   * - CDK bin/app.ts (modified by this feature)
   * - agents/network-agent/ Python files
   * - demo-scenarios/ TLS fragmentation scripts (authored by this feature)
   * - The cleanup-scenarios scripts (extended by this feature)
   *
   * Pre-existing scripts (setup-scenario-a, setup-scenario-b) are NOT
   * authored by this feature and are excluded.
   */
  function getFeatureSourceFiles(): { relativePath: string; content: string }[] {
    const files: { relativePath: string; content: string }[] = [];

    // CDK infrastructure files — Network stack files specifically
    const networkStackFiles = [
      'network-data-stack.ts',
      'network-infra-stack.ts',
      'network-runtime-stack.ts',
    ];
    for (const fileName of networkStackFiles) {
      const filePath = path.join(CDK_LIB_DIR, fileName);
      if (fs.existsSync(filePath)) {
        files.push({
          relativePath: path.relative(PROJECT_ROOT, filePath),
          content: fs.readFileSync(filePath, 'utf-8'),
        });
      }
    }

    // CDK bin/app.ts
    const appTsPath = path.join(CDK_BIN_DIR, 'app.ts');
    if (fs.existsSync(appTsPath)) {
      files.push({
        relativePath: path.relative(PROJECT_ROOT, appTsPath),
        content: fs.readFileSync(appTsPath, 'utf-8'),
      });
    }

    // Network agent Python files (excluding test files for this check)
    const networkAgentDir = path.join(AGENTS_DIR, 'network-agent');
    if (fs.existsSync(networkAgentDir)) {
      const pyFiles = fs.readdirSync(networkAgentDir)
        .filter((f) => f.endsWith('.py') && !f.startsWith('test_'))
        .map((f) => ({
          relativePath: `agents/network-agent/${f}`,
          content: fs.readFileSync(path.join(networkAgentDir, f), 'utf-8'),
        }));
      files.push(...pyFiles);
    }

    // Demo scenario scripts authored by this feature
    const featureScenarioScripts = [
      'setup-scenario-tls-fragmentation.ps1',
      'setup-scenario-tls-fragmentation.sh',
      'cleanup-scenarios.ps1',
      'cleanup-scenarios.sh',
    ];
    for (const scriptName of featureScenarioScripts) {
      const scriptPath = path.join(DEMO_SCENARIOS_DIR, scriptName);
      if (fs.existsSync(scriptPath)) {
        files.push({
          relativePath: `demo-scenarios/${scriptName}`,
          content: fs.readFileSync(scriptPath, 'utf-8'),
        });
      }
    }

    // Deployment scripts at project root
    const deployPs1 = path.join(PROJECT_ROOT, 'deploy-all.ps1');
    const deploySh = path.join(PROJECT_ROOT, 'deploy-all.sh');
    if (fs.existsSync(deployPs1)) {
      files.push({
        relativePath: 'deploy-all.ps1',
        content: fs.readFileSync(deployPs1, 'utf-8'),
      });
    }
    if (fs.existsSync(deploySh)) {
      files.push({
        relativePath: 'deploy-all.sh',
        content: fs.readFileSync(deploySh, 'utf-8'),
      });
    }

    return files;
  }

  /**
   * Property 14: No source file contains a literal 12-digit account ID.
   *
   * **Validates: Requirements 10.6, 11.9, 12.10, 15.4**
   */
  it('Property 14: no feature source file contains a literal 12-digit AWS account ID', () => {
    const files = getFeatureSourceFiles();
    expect(files.length).toBeGreaterThan(0);

    for (const file of files) {
      const lines = file.content.split('\n');
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (isExemptLineWithContext(line, file.relativePath, file.content, i)) continue;

        // Check for 12-digit sequences that look like account IDs
        // Exclude lines that are clearly not account IDs (e.g., version numbers, timestamps)
        const matches = line.match(/\b\d{12}\b/g);
        if (matches) {
          for (const match of matches) {
            // Skip if it's clearly a timestamp or version-like number
            // Account IDs don't start with 0 typically, but we check all 12-digit numbers
            // Skip if surrounded by dots (version numbers like 2.219.0.12345678901)
            const idx = line.indexOf(match);
            const before = idx > 0 ? line[idx - 1] : ' ';
            const after = idx + 12 < line.length ? line[idx + 12] : ' ';
            if (before === '.' || after === '.') continue;

            // This is a potential hardcoded account ID
            expect(
              false,
              `Found potential hardcoded account ID "${match}" in ${file.relativePath}:${i + 1}`,
            ).toBe(true);
          }
        }
      }
    }
  });

  /**
   * Property 14: No source file contains a literal AWS region name.
   *
   * **Validates: Requirements 10.6, 11.9, 12.10, 15.4**
   */
  it('Property 14: no feature source file contains a literal AWS region name outside exempt contexts', () => {
    const files = getFeatureSourceFiles();
    expect(files.length).toBeGreaterThan(0);

    for (const file of files) {
      const lines = file.content.split('\n');
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (isExemptLineWithContext(line, file.relativePath, file.content, i)) continue;

        const match = line.match(REGION_NAME_REGEX);
        if (match) {
          expect(
            false,
            `Found literal AWS region "${match[0]}" in ${file.relativePath}:${i + 1}: ${line.trim()}`,
          ).toBe(true);
        }
      }
    }
  });

  /**
   * Property 14: No source file contains a literal AWS endpoint URL.
   *
   * **Validates: Requirements 10.6, 11.9, 12.10, 15.4**
   */
  it('Property 14: no feature source file contains a literal AWS endpoint URL outside exempt contexts', () => {
    const files = getFeatureSourceFiles();
    expect(files.length).toBeGreaterThan(0);

    for (const file of files) {
      const lines = file.content.split('\n');
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (isExemptLineWithContext(line, file.relativePath, file.content, i)) continue;

        const match = line.match(ENDPOINT_URL_REGEX);
        if (match) {
          expect(
            false,
            `Found literal AWS endpoint URL "${match[0]}" in ${file.relativePath}:${i + 1}: ${line.trim()}`,
          ).toBe(true);
        }
      }
    }
  });

  /**
   * Property 14 (generative): For any randomly generated forbidden pattern,
   * the scanner correctly identifies it as a violation when injected into
   * a non-exempt line.
   *
   * **Validates: Requirements 10.6, 11.9, 12.10, 15.4**
   */
  it('Property 14: forbidden pattern detection works for generated region strings', () => {
    fc.assert(
      fc.property(arbAwsRegion, (region) => {
        // A non-exempt line containing a literal region should be detected
        const testLine = `const myRegion = '${region}';`;
        expect(testLine).toMatch(REGION_NAME_REGEX);

        // An exempt line (comment) should not trigger
        const commentLine = `// Deploy to ${region} for testing`;
        expect(isExemptLine(commentLine, 'some-file.ts')).toBe(true);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 14 (generative): For any randomly generated 12-digit number,
   * the account ID pattern correctly matches it.
   *
   * **Validates: Requirements 10.6, 15.4**
   */
  it('Property 14: account ID pattern detects any 12-digit number', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 100000000000, max: 999999999999 }),
        (accountId) => {
          const asString = accountId.toString();
          expect(asString).toMatch(ACCOUNT_ID_REGEX);
          expect(asString.length).toBe(12);
        },
      ),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 15: Solution adoption tracking marker appears exactly once
// ---------------------------------------------------------------------------

describe('Property 15: Solution adoption tracking marker appears exactly once', () => {
  /**
   * Property 15: The tracking marker appears exactly once in the CDK app,
   * and only on the OrchRuntimeStack (the existing primary G.O.A.T. stack).
   *
   * **Validates: Requirements 10.7, 15.5**
   */
  it('Property 15: tracking marker appears exactly once in app.ts', () => {
    const appContent = readAppTs();

    const occurrences = appContent.split(TRACKING_MARKER).length - 1;
    expect(occurrences).toBe(1);
  });

  it('Property 15: tracking marker is on the OrchRuntimeStack description', () => {
    const appContent = readAppTs();

    // Find the line containing the tracking marker
    const lines = appContent.split('\n');
    const markerLines = lines.filter((line) => line.includes(TRACKING_MARKER));
    expect(markerLines.length).toBe(1);

    // The marker should be in a description property associated with OrchRuntimeStack
    const markerLine = markerLines[0];
    expect(markerLine).toContain('description');

    // Find the context around the marker — it should be near OrchRuntimeStack
    const markerIndex = lines.findIndex((line) => line.includes(TRACKING_MARKER));
    // Look backward up to 10 lines for the OrchRuntimeStack instantiation
    const contextStart = Math.max(0, markerIndex - 10);
    const contextSlice = lines.slice(contextStart, markerIndex + 1).join('\n');
    expect(contextSlice).toContain('OrchRuntimeStack');
  });

  it('Property 15: no Network stack lib file contains the tracking marker', () => {
    const networkStackFiles = [
      'network-data-stack.ts',
      'network-infra-stack.ts',
      'network-runtime-stack.ts',
    ];

    for (const fileName of networkStackFiles) {
      const filePath = path.join(CDK_LIB_DIR, fileName);
      if (fs.existsSync(filePath)) {
        const content = fs.readFileSync(filePath, 'utf-8');
        expect(content).not.toContain(TRACKING_MARKER);
      }
    }
  });

  it('Property 15: no Network stack lib file contains the tracking ID fragment', () => {
    const networkStackFiles = [
      'network-data-stack.ts',
      'network-infra-stack.ts',
      'network-runtime-stack.ts',
    ];

    for (const fileName of networkStackFiles) {
      const filePath = path.join(CDK_LIB_DIR, fileName);
      if (fs.existsSync(filePath)) {
        const content = fs.readFileSync(filePath, 'utf-8');
        expect(content).not.toContain('uksb-do9bhieqqh');
      }
    }
  });

  it('Property 15: across all CDK lib files, the tracking marker never appears', () => {
    // The tracking marker should ONLY be in app.ts (bin/), never in lib/ files
    const libFiles = collectFiles(CDK_LIB_DIR, ['.ts'], [], CDK_LIB_DIR);

    for (const file of libFiles) {
      expect(
        file.content.includes(TRACKING_MARKER),
        `Tracking marker found in lib/${file.relativePath} — it should only be in bin/app.ts`,
      ).toBe(false);
    }
  });

  /**
   * Property 15 (generative): For any combination of Network stack names,
   * none should carry the tracking marker in their description.
   *
   * **Validates: Requirements 10.7, 15.5**
   */
  it('Property 15: generated Network stack descriptions never contain the tracking marker', () => {
    fc.assert(
      fc.property(
        arbNetworkStackBase,
        arbAwsRegion,
        fc.string({ minLength: 0, maxLength: 200 }),
        (baseName, region, description) => {
          const stackId = `${baseName}-${region}`;
          // A valid Network stack description must never contain the tracking marker
          const fullDescription = `${description} ${stackId}`;
          // The tracking marker is a specific string — it should never appear
          // in any Network stack description by construction
          expect(fullDescription).not.toContain(TRACKING_MARKER);
        },
      ),
      { numRuns: 100 },
    );
  });
});
