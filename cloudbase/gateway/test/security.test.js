"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  boundedName,
  boundedText,
  environmentId,
  integerSetting,
  newUploadToken,
  postgresConnectionSecurity,
  safeTokenEqual,
  subjectStorageKey,
  tokenHash,
} = require("../security");

test("repository names are bounded", () => {
  assert.equal(boundedName("math-2026", "name"), "math-2026");
  assert.throws(() => boundedName("../escape", "name"));
  assert.throws(() => boundedName("x".repeat(81), "name"));
});

test("opaque upload tokens compare by hash", () => {
  const token = newUploadToken();
  assert.equal(safeTokenEqual(token, tokenHash(token)), true);
  assert.equal(safeTokenEqual(`${token}x`, tokenHash(token)), false);
});

test("text budgets are byte based", () => {
  assert.equal(boundedText("题目", "body", 6), "题目");
  assert.throws(() => boundedText("题目", "body", 5));
});

test("storage namespaces never expose raw identity subjects", () => {
  const subject = "user/name@example.com";
  const key = subjectStorageKey(subject);
  assert.match(key, /^[a-f0-9]{64}$/);
  assert.equal(key.includes(subject), false);
  assert.equal(subjectStorageKey(subject), key);
  assert.notEqual(subjectStorageKey("other-user"), key);
});

test("numeric deployment settings fail closed", () => {
  assert.equal(integerSetting(undefined, "LIMIT", 5, 1, 10), 5);
  assert.equal(integerSetting("10", "LIMIT", 5, 1, 10), 10);
  for (const value of ["NaN", "1.5", "0", "11", "Infinity"]) {
    assert.throws(() => integerSetting(value, "LIMIT", 5, 1, 10), /LIMIT/);
  }
});

test("CloudBase environment IDs cannot escape the official auth hostname", () => {
  assert.equal(environmentId("env-123"), "env-123");
  for (const value of ["", "https://evil.test", "env.example", "a".repeat(65)]) {
    assert.throws(() => environmentId(value), /格式无效/);
  }
});

test("PostgreSQL TLS verifies certificates by default", () => {
  const config = postgresConnectionSecurity("postgresql://user:pass@db.example/yancuo");
  assert.deepEqual(config.ssl, { rejectUnauthorized: true });
  assert.equal(config.connectionString, "postgresql://user:pass@db.example/yancuo");
});

test("PostgreSQL TLS accepts an explicit certificate authority", () => {
  const config = postgresConnectionSecurity(
    "postgres://user:pass@db.example/yancuo",
    "verify",
    "-----BEGIN CERTIFICATE-----\\nabc\\n-----END CERTIFICATE-----",
  );
  assert.equal(config.ssl.rejectUnauthorized, true);
  assert.match(config.ssl.ca, /CERTIFICATE-----\nabc\n/);
});

test("PostgreSQL insecure modes require an explicit deployment choice", () => {
  assert.deepEqual(
    postgresConnectionSecurity("postgres://user:pass@localhost/yancuo", "disable").ssl,
    false,
  );
  assert.deepEqual(
    postgresConnectionSecurity("postgres://user:pass@localhost/yancuo", "no-verify").ssl,
    { rejectUnauthorized: false },
  );
  assert.throws(
    () => postgresConnectionSecurity("postgres://localhost/yancuo", "optional"),
    /PG_SSL/,
  );
  assert.throws(
    () => postgresConnectionSecurity("postgres://localhost/yancuo", "disable", "ca"),
    /PG_SSL_CA/,
  );
});

test("connection strings cannot override PostgreSQL TLS configuration", () => {
  for (const option of ["sslmode", "sslcert", "sslkey", "sslrootcert"]) {
    assert.throws(
      () => postgresConnectionSecurity(`postgres://localhost/yancuo?${option}=disable`),
      new RegExp(option),
    );
  }
  assert.throws(() => postgresConnectionSecurity("https://db.example/yancuo"), /postgres/);
  assert.throws(() => postgresConnectionSecurity("not a URL"), /DATABASE_URL/);
});
