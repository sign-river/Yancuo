"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const gateway = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");
const init = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "init.sql"), "utf8");
const policies = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "rls-policies.sql"), "utf8");

test("gateway sets a transaction-local subject before data queries", () => {
  assert.match(gateway, /set local yancuo\.subject_id = \$1/);
  assert.match(gateway, /async function beginScoped\(client, subject\)/);
  assert.match(gateway, /async function queryScoped\(subject, text, params\)/);
});

test("one-time upload flow is scoped by upload id instead of subject", () => {
  assert.match(gateway, /set local yancuo\.upload_id = \$1/);
  assert.match(gateway, /async function beginUploadScoped\(client, uploadId\)/);
  assert.match(gateway, /async function queryUploadScoped\(uploadId, text, params\)/);
});

test("every authenticated repository handler runs inside a scoped transaction", () => {
  const action = gateway.slice(gateway.indexOf("async function action"));
  const begins = (action.match(/beginScoped\(client, subject\)/g) || []).length;
  assert.ok(begins >= 2, "expected scoped begins in repositories/create and the main handler");
  assert.doesNotMatch(action, /await client\.query\("begin"\)/);
  const commits = (action.match(/client\.query\("commit"\)/g) || []).length;
  assert.ok(commits >= 10, "expected a commit for every returning branch");
});

test("rls-policies.sql scopes every table to the gateway identity", () => {
  const tables = [
    "repositories",
    "manifests",
    "releases",
    "release_assets",
    "upload_sessions",
    "write_locks",
    "rate_limits",
    "object_deletions",
  ];
  for (const table of tables) {
    assert.match(policies, new RegExp("on yancuo\\." + table + "\\b"), table + " policy");
  }
  assert.match(policies, /to yancuo_gateway/);
  assert.match(policies, /current_setting\('yancuo\.subject_id', true\)/);
  assert.match(policies, /current_setting\('yancuo\.upload_id', true\)/);
  assert.doesNotMatch(policies, /using \(true\)/);
});

test("init.sql still enables row level security on all eight tables", () => {
  const tables = [
    "repositories",
    "manifests",
    "releases",
    "release_assets",
    "upload_sessions",
    "write_locks",
    "rate_limits",
    "object_deletions",
  ];
  for (const table of tables) {
    assert.match(init, new RegExp("alter table yancuo\\." + table + " enable row level security"));
  }
});
