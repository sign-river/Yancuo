"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const gateway = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");
const init = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "init.sql"), "utf8");
const rpc = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "pgmode-rpc.sql"), "utf8");

test("every user database call passes the validated subject explicitly", () => {
  const rpcCalls = gateway.match(/callRpc\(\"yancuo_[a-z_]+\"/g) || [];
  assert.ok(rpcCalls.length >= 15, "expected a broad RPC surface");
  assert.match(gateway, /p_subject: subject/);
  assert.doesNotMatch(gateway, /client\.query\(/);
  assert.doesNotMatch(gateway, /queryScoped\(/);
});

test("RPC functions are server-side defined and never trust the client for schema", () => {
  assert.match(rpc, /SECURITY DEFINER/);
  assert.match(rpc, /RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER/);
  assert.doesNotMatch(rpc, /to yancuo_gateway/);
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
    assert.match(init, new RegExp("alter table yancuo\." + table + " enable row level security"));
  }
});

test("the gateway requires a service API key and never embeds it in the repo", () => {
  assert.match(gateway, /RDB_API_KEY/);
  assert.match(gateway, /if \(!RDB_API_KEY\)/);
  assert.doesNotMatch(gateway, /eyJhbGciOiJSUzI1NiIsImtpZCI6/);
});
