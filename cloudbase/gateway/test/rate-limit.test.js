"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const gateway = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");
const schema = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "init.sql"), "utf8");
const rpc = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "pgmode-rpc.sql"), "utf8");

test("rate limits are shared through one atomic database row per user", () => {
  assert.match(schema, /create table if not exists yancuo\.rate_limits/);
  assert.match(schema, /subject_id text primary key/);
  assert.match(rpc, /insert into yancuo\.rate_limits[\s\S]{0,700}on conflict \(subject_id\) do update[\s\S]{0,700}returning request_count/i);
  assert.match(gateway, /callRpc\("yancuo_rate_limit", \{ p_subject: subject \}\)/);
  assert.match(gateway, /await enforceRate\(subject\)/);
});

test("gateway does not retain an unbounded in-process identity map", () => {
  assert.doesNotMatch(gateway, /rateBuckets\s*=\s*new Map/);
});
