"use strict";

const crypto = require("node:crypto");

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const ENVIRONMENT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,63}$/;
const POSTGRES_SSL_QUERY_KEYS = new Set(["sslcert", "sslkey", "sslrootcert", "sslmode"]);

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

function postgresConnectionSecurity(databaseUrl, mode, ca) {
  let parsed;
  try {
    parsed = new URL(String(databaseUrl || ""));
  } catch {
    throw new Error("DATABASE_URL format is invalid");
  }
  if (parsed.protocol !== "postgres:" && parsed.protocol !== "postgresql:") {
    throw new Error("DATABASE_URL must use postgres:// or postgresql://");
  }
  for (const key of parsed.searchParams.keys()) {
    if (POSTGRES_SSL_QUERY_KEYS.has(key.toLowerCase())) {
      throw new Error(`DATABASE_URL must not contain ${key}; configure TLS with PG_SSL instead`);
    }
  }

  const sslMode = String(mode || "verify").trim().toLowerCase();
  const certificateAuthority = String(ca || "").replaceAll("\\n", "\n").trim();
  if (certificateAuthority && Buffer.byteLength(certificateAuthority, "utf8") > 1024 * 1024) {
    throw new Error("PG_SSL_CA exceeds the 1 MiB limit");
  }
  if (sslMode === "verify") {
    return {
      connectionString: String(databaseUrl),
      ssl: certificateAuthority
        ? { rejectUnauthorized: true, ca: certificateAuthority }
        : { rejectUnauthorized: true },
    };
  }
  if (certificateAuthority) {
    throw new Error("PG_SSL_CA requires PG_SSL=verify");
  }
  if (sslMode === "no-verify") {
    return { connectionString: String(databaseUrl), ssl: { rejectUnauthorized: false } };
  }
  if (sslMode === "disable") {
    return { connectionString: String(databaseUrl), ssl: false };
  }
  throw new Error("PG_SSL must be verify, no-verify, or disable");
}

module.exports = {
  boundedName,
  boundedText,
  environmentId,
  integerSetting,
  newUploadToken,
  postgresConnectionSecurity,
  safeTokenEqual,
  subjectStorageKey,
  tokenHash,
};
