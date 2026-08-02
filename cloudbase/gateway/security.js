"use strict";

const crypto = require("node:crypto");

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;

function boundedName(value, label) {
  const result = String(value || "").trim();
  if (!NAME_RE.test(result)) {
    const error = new Error(`${label} 只能包含字母、数字、点、下划线和连字符，长度 1–80`);
    error.statusCode = 400;
    throw error;
  }
  return result;
}

function boundedText(value, label, maxBytes) {
  const result = String(value || "");
  if (Buffer.byteLength(result, "utf8") > maxBytes) {
    const error = new Error(`${label} 超过大小限制`);
    error.statusCode = 413;
    throw error;
  }
  return result;
}

function tokenHash(token) {
  return crypto.createHash("sha256").update(token, "utf8").digest("hex");
}

function newUploadToken() {
  return crypto.randomBytes(32).toString("base64url");
}

function safeTokenEqual(token, expectedHash) {
  const actual = Buffer.from(tokenHash(token), "hex");
  const expected = Buffer.from(String(expectedHash || ""), "hex");
  return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
}

module.exports = {
  boundedName,
  boundedText,
  newUploadToken,
  safeTokenEqual,
  tokenHash,
};
