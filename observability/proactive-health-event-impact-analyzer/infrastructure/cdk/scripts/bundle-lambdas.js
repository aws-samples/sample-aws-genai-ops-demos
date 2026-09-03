#!/usr/bin/env node
/**
 * Pre-compiles the TypeScript Lambda handlers into deployable JavaScript bundles.
 *
 * Why this exists
 * ---------------
 * CDK's `NodejsFunction` construct bundles TypeScript automatically, but its local
 * esbuild path shells out to `powershell.exe` on Windows (it hardcodes that
 * executable and does not fall back to `pwsh`). On any machine whose PATH omits
 * the classic Windows PowerShell directory, synthesis fails with
 * "spawnSync powershell.exe ENOENT" — which broke both `cdk deploy` and `npm test`.
 *
 * Every other demo in this repository avoids `NodejsFunction` entirely, using
 * `lambda.Function` + `Code.fromAsset` over pre-built artifacts. This script brings
 * this demo in line with that convention: it bundles the handlers up front using
 * esbuild's Node API (an in-process library call — no shell, no child process), so
 * the CDK stacks only ever point at plain directories of compiled JavaScript.
 *
 * Output layout — one directory per function, each holding an `index.js` whose
 * exported `handler` matches the CDK `handler: 'index.handler'` setting:
 *
 *   dist/lambda/<function-name>/index.js
 *
 * Usage:
 *   node scripts/bundle-lambdas.js
 *   npm run bundle
 */

const path = require('path');
const fs = require('fs');
const esbuild = require('esbuild');

const CDK_DIR = path.join(__dirname, '..');
const LAMBDA_SRC_DIR = path.join(CDK_DIR, 'lambda');
const DIST_DIR = path.join(CDK_DIR, 'dist', 'lambda');

/**
 * The Lambda Node.js 24 runtime ships the AWS SDK v3 clients, so they are marked
 * external (not bundled) to keep artifacts small — this mirrors the
 * `externalModules` settings the previous NodejsFunction constructs used.
 */
const SDK_PROVIDED_BY_RUNTIME = ['@aws-sdk/*'];

/**
 * Functions to bundle. `external` is per-function because the runtime does not
 * ship every SDK client.
 *
 * This list must stay in sync with the `lambda.Function` constructs in
 * lib/constructs/ — every deployed function needs an entry here, and every entry
 * needs a construct that consumes it.
 */
const FUNCTIONS = [
  { name: 'event-router', external: SDK_PROVIDED_BY_RUNTIME },
  { name: 'investigation-trigger', external: SDK_PROVIDED_BY_RUNTIME },
  { name: 'opscenter-creator', external: SDK_PROVIDED_BY_RUNTIME },
  { name: 'notifier', external: SDK_PROVIDED_BY_RUNTIME },
  {
    // @aws-sdk/client-devops-agent is NOT present in the Lambda runtime, so it
    // must be bundled into the artifact. Only the runtime-provided clients are
    // listed as external here.
    name: 'investigation-callback',
    external: ['@aws-sdk/client-dynamodb', '@aws-sdk/client-sfn'],
  },
];

/** Matches the Node.js 24 Lambda runtime configured on every function. */
const ESBUILD_TARGET = 'node24';

async function bundleFunction({ name, external }) {
  const entry = path.join(LAMBDA_SRC_DIR, name, 'index.ts');
  const outfile = path.join(DIST_DIR, name, 'index.js');

  if (!fs.existsSync(entry)) {
    throw new Error(`Lambda entry point not found: ${entry}`);
  }

  await esbuild.build({
    entryPoints: [entry],
    outfile,
    bundle: true,
    platform: 'node',
    target: ESBUILD_TARGET,
    format: 'cjs',
    external,
    // Keep artifacts debuggable in the console; these handlers are small.
    minify: false,
    sourcemap: false,
    logLevel: 'warning',
  });

  const { size } = fs.statSync(outfile);
  return { name, outfile, sizeKb: (size / 1024).toFixed(1) };
}

async function main() {
  // Clean the output directory so removed handlers never linger in a deployment.
  fs.rmSync(DIST_DIR, { recursive: true, force: true });
  fs.mkdirSync(DIST_DIR, { recursive: true });

  console.log(`Bundling ${FUNCTIONS.length} Lambda functions with esbuild...`);

  const results = await Promise.all(FUNCTIONS.map(bundleFunction));

  for (const { name, sizeKb } of results) {
    console.log(`  [OK] ${name} -> dist/lambda/${name}/index.js (${sizeKb} KB)`);
  }

  console.log('Lambda bundling complete.');
}

main().catch((err) => {
  console.error('\nLambda bundling failed:');
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
