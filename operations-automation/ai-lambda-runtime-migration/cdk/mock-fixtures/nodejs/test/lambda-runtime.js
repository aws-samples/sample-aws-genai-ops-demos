// Models the two AWS Lambda Node.js runtime rules this fixture depends on, so
// the tests fail on the new runtime the way the deployed function would. The
// Lambda runtime nodejsNN.x runs Node.js NN, so the rules are keyed on the
// Node.js major version the tests run under.
//
//   - Since nodejs18.x the runtime bundles the AWS SDK for JavaScript v3
//     (@aws-sdk/*) instead of v2 ('aws-sdk'). A module the code requires must be
//     a Node.js built-in, bundled by the runtime, or a production dependency
//     (devDependencies are not deployed).
//   - nodejs24.x no longer supports callback-based handlers
//     (https://aws.amazon.com/about-aws/whats-new/2025/11/aws-lambda-nodejs-24).
const fs = require('fs');
const { builtinModules } = require('module');

const NODE_MAJOR = Number(process.versions.node.split('.')[0]);

function runtimeBundles(moduleName) {
  if (NODE_MAJOR < 18) return moduleName === 'aws-sdk';
  return moduleName.startsWith('@aws-sdk/');
}

function packageName(specifier) {
  const parts = specifier.split('/');
  return specifier.startsWith('@') ? parts.slice(0, 2).join('/') : parts[0];
}

// Every require() in `file` that the deployed function could not load.
function unresolvableRequires(file, pkg) {
  const source = fs.readFileSync(file, 'utf8');
  const deps = Object.keys(pkg.dependencies || {});
  const missing = [];
  for (const match of source.matchAll(/require\(\s*['"]([^'"]+)['"]\s*\)/g)) {
    const spec = match[1];
    if (spec.startsWith('.')) continue;
    const name = packageName(spec.replace(/^node:/, ''));
    if (builtinModules.includes(name) || runtimeBundles(name) || deps.includes(name)) continue;
    missing.push(name);
  }
  return [...new Set(missing)];
}

// Invoke a handler the way the Lambda runtime does.
function invoke(handler, event, context = {}) {
  if (NODE_MAJOR >= 24 && handler.length >= 3) {
    return Promise.reject(new Error(
      'Callback-based handlers are not supported on the nodejs24.x runtime; use an async handler'));
  }
  return new Promise((resolve, reject) => {
    const callback = (err, result) => (err ? reject(err) : resolve(result));
    const returned = handler(event, context, callback);
    if (returned && typeof returned.then === 'function') returned.then(resolve, reject);
  });
}

module.exports = { NODE_MAJOR, unresolvableRequires, invoke };
