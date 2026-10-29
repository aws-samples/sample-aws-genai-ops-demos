// Order token service (mock fixture, nodejs16.x).
//
// A partner sends an opaque order token. The function decrypts it, validates
// the order and returns a summary. The token secret comes from SSM Parameter
// Store, or from TOKEN_SECRET for local runs.
//
// Written the way a nodejs16.x function typically was, ON PURPOSE: every
// pattern below works on Node.js 16 and breaks on the Lambda nodejs24.x runtime.
// AWS Transform is expected to find and fix each one (see
// docs/mock-fixture.md in the demo):
//   - crypto.createCipher / createDecipher: removed in Node.js 22 (DEP0106)
//   - util.isString / isNumber / isDate / log: removed in Node.js 23
//   - require('aws-sdk') (SDK v2): not bundled in the Lambda runtime since nodejs18.x
//   - callback-style handler: not supported by the Lambda nodejs24.x runtime
const crypto = require('crypto');
const util = require('util');
const AWS = require('aws-sdk'); // provided by the nodejs16.x runtime

const ssm = new AWS.SSM();
let cachedSecret = process.env.TOKEN_SECRET || null;

function encryptToken(payload, secret) {
  const cipher = crypto.createCipher('aes192', secret);
  return cipher.update(JSON.stringify(payload), 'utf8', 'hex') + cipher.final('hex');
}

function decryptToken(token, secret) {
  const decipher = crypto.createDecipher('aes192', secret);
  return JSON.parse(decipher.update(token, 'hex', 'utf8') + decipher.final('utf8'));
}

function validateOrder(order) {
  const errors = [];
  if (!util.isString(order.orderId) || order.orderId.length === 0) {
    errors.push('orderId must be a non-empty string');
  }
  if (!util.isNumber(order.amount) || order.amount <= 0) {
    errors.push('amount must be a positive number');
  }
  const placedAt = new Date(order.placedAt);
  if (!util.isDate(placedAt) || isNaN(placedAt.getTime())) {
    errors.push('placedAt must be a valid date');
  }
  return errors;
}

function getSecret(callback) {
  if (cachedSecret) return callback(null, cachedSecret);
  ssm.getParameter({ Name: process.env.TOKEN_SECRET_PARAM, WithDecryption: true }, (err, data) => {
    if (err) return callback(err);
    cachedSecret = data.Parameter.Value;
    return callback(null, cachedSecret);
  });
}

function respond(statusCode, body) {
  return { statusCode, body: JSON.stringify(body) };
}

exports.handler = (event, context, callback) => {
  getSecret((err, secret) => {
    if (err) return callback(err);

    let order;
    try {
      order = decryptToken(event.token, secret);
    } catch (e) {
      util.log(`rejected an unreadable token: ${e.message}`);
      return callback(null, respond(400, { error: 'invalid token' }));
    }

    const errors = validateOrder(order);
    if (errors.length > 0) {
      util.log(`rejected order ${order.orderId}: ${errors.join('; ')}`);
      return callback(null, respond(422, { errors }));
    }

    util.log(`accepted order ${order.orderId}`);
    return callback(null, respond(200, { orderId: order.orderId, amount: order.amount }));
  });
};

exports.encryptToken = encryptToken;
exports.decryptToken = decryptToken;
exports.validateOrder = validateOrder;
