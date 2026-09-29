#!/usr/bin/env node
/**
 * Bundles Lambda handlers that require dependencies not provided by the Lambda
 * runtime. Uses esbuild's Node API (no shell, no Docker), so it is deterministic
 * on Windows, macOS, and Linux.
 */
const fs = require('fs');
const path = require('path');
const esbuild = require('esbuild');

const cdkDir = path.join(__dirname, '..');
const source = path.join(cdkDir, 'lambda', 'devops-agent-webhook-provisioner', 'index.ts');
const outputDir = path.join(cdkDir, 'dist', 'lambda', 'devops-agent-webhook-provisioner');
const output = path.join(outputDir, 'index.js');

async function main() {
  fs.rmSync(outputDir, { recursive: true, force: true });
  fs.mkdirSync(outputDir, { recursive: true });

  await esbuild.build({
    entryPoints: [source],
    outfile: output,
    bundle: true,
    platform: 'node',
    target: 'node22',
    format: 'cjs',
    // Both clients are intentionally bundled. In particular,
    // @aws-sdk/client-devops-agent is not guaranteed to exist in the Lambda runtime.
    external: [],
    minify: false,
    sourcemap: false,
    logLevel: 'warning',
  });

  const sizeKb = (fs.statSync(output).size / 1024).toFixed(1);
  console.log(`  [OK] devops-agent-webhook-provisioner (${sizeKb} KB)`);
}

main().catch((error) => {
  console.error('Lambda bundling failed:', error);
  process.exit(1);
});
