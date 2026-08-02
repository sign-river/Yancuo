"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const gateway = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");

test("PostgreSQL pool acquisition has a bounded wait", () => {
  assert.match(
    gateway,
    /PG_CONNECT_TIMEOUT_MS[\s\S]{0,180}10_000[\s\S]{0,180}60_000/,
  );
  assert.match(gateway, /connectionTimeoutMillis: PG_CONNECT_TIMEOUT_MS/);
});

test("idle PostgreSQL client errors cannot become unhandled events", () => {
  assert.match(gateway, /pool\.on\("error", \(error\) =>/);
  const listener = gateway.slice(gateway.indexOf('pool.on("error"'), gateway.indexOf("const cloud"));
  assert.match(listener, /error\.name/);
  assert.doesNotMatch(listener, /error\.message|error\.stack/);
});
