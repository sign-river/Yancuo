"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const gateway = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");

test("RDB RPC calls have a bounded timeout", () => {
  assert.match(
    gateway,
    /RDB_TIMEOUT_MS[\s\S]{0,180}15_000[\s\S]{0,180}60_000/,
  );
  assert.match(gateway, /AbortSignal\.timeout\(RDB_TIMEOUT_MS\)/);
});

test("non-2xx RDB responses fail closed without leaking internals", () => {
  assert.match(gateway, /if \(!rdbResponse\.ok\)/);
  assert.match(gateway, /fail\(message, 502\)/);
  assert.match(gateway, /if \(value\.ok !== true\)/);
  assert.match(gateway, /Number\(value\.status \|\| 400\)/);
});

test("the gateway no longer depends on a direct PostgreSQL socket", () => {
  assert.doesNotMatch(gateway, /require\("pg"\)/);
  assert.doesNotMatch(gateway, /new Pool\(/);
  assert.doesNotMatch(gateway, /pool\.query/);
});
