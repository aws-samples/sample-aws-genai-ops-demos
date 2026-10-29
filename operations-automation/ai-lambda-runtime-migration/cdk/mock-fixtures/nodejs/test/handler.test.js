// Tests for the order token service. Plain Node.js `assert`, no test framework,
// so the same file runs on Node.js 16 and on the upgraded runtime.
const assert = require('assert');
const path = require('path');
const { NODE_MAJOR, unresolvableRequires, invoke } = require('./lambda-runtime');

process.env.TOKEN_SECRET = 'test-secret-do-not-use';
const SECRET = process.env.TOKEN_SECRET;

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

const fn = () => require('../index');
const validOrder = { orderId: 'ORD-1001', amount: 42.5, placedAt: '2026-10-01T09:30:00Z' };

test('every required module is available on the Lambda runtime', () => {
  const pkg = require('../package.json');
  const missing = unresolvableRequires(path.join(__dirname, '..', 'index.js'), pkg);
  assert.deepStrictEqual(missing, [],
    `not available on the nodejs${NODE_MAJOR}.x runtime: ${missing.join(', ')}`);
});

test('a token round-trips through encrypt and decrypt', () => {
  const token = fn().encryptToken(validOrder, SECRET);
  assert.deepStrictEqual(fn().decryptToken(token, SECRET), validOrder);
});

test('validateOrder accepts a well-formed order', () => {
  assert.deepStrictEqual(fn().validateOrder(validOrder), []);
});

test('validateOrder reports every invalid field', () => {
  const errors = fn().validateOrder({ orderId: '', amount: -1, placedAt: 'not a date' });
  assert.strictEqual(errors.length, 3);
});

test('the handler accepts a valid token', async () => {
  const token = fn().encryptToken(validOrder, SECRET);
  const res = await invoke(fn().handler, { token });
  assert.strictEqual(res.statusCode, 200);
  assert.deepStrictEqual(JSON.parse(res.body), { orderId: 'ORD-1001', amount: 42.5 });
});

test('the handler rejects an unreadable token', async () => {
  const res = await invoke(fn().handler, { token: 'deadbeef' });
  assert.strictEqual(res.statusCode, 400);
});

test('the handler rejects an invalid order', async () => {
  const token = fn().encryptToken({ ...validOrder, amount: 0 }, SECRET);
  const res = await invoke(fn().handler, { token });
  assert.strictEqual(res.statusCode, 422);
});

(async () => {
  let failed = 0;
  for (const t of tests) {
    try {
      await t.fn();
      console.log(`ok - ${t.name}`);
    } catch (err) {
      failed += 1;
      console.log(`not ok - ${t.name}\n    ${err.message}`);
    }
  }
  console.log(`\n${tests.length - failed} passed, ${failed} failed (Node.js ${process.versions.node})`);
  process.exitCode = failed ? 1 : 0;
})();
