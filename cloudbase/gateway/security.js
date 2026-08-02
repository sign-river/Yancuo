"use strict";

const crypto = require("node:crypto");

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const ENVIRONMENT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,63}$/;

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

function environmentId(value) {
  const result = String(value || "").trim();
  if (!ENVIRONMENT_ID_RE.test(result)) {
    throw new Error("CLOUDBASE_ENV_ID/TCB_ENV 格式无效");
  }
  return result;
}

function tokenHash(token) {
  return crypto.createHash("sha256").update(token, "utf8").digest("hex");
}

function newUploadToken() {
  return crypto.randomBytes(32).toString("base64url");
}

function subjectStorageKey(subject) {
  return crypto.createHash("sha256").update(String(subject), "utf8").digest("hex");
}

function integerSetting(value, label, fallback, min, max) {
  const raw = value === undefined || value === null || value === "" ? fallback : value;
  const result = Number(raw);
  if (!Number.isSafeInteger(result) || result < min || result > max) {
    throw new Error(`${label} 必须是 ${min}–${max} 范围内的整数`);
  }
  return result;
}

function safeTokenEqual(token, expectedHash) {
  const actual = Buffer.from(tokenHash(token), "hex");
  const expected = Buffer.from(String(expectedHash || ""), "hex");
  return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
}

module.exports = {
  boundedName,
  boundedText,
  environmentId,
  integerSetting,
  newUploadToken,
  safeTokenEqual,
  subjectStorageKey,
  tokenHash,
};
