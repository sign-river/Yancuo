"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");

test("per-user quotas are serialized across gateway instances", () => {
  assert.match(
    source,
    /pg_advisory_xact_lock\(hashtextextended\(\$1, 0\)\)/,
  );
  assert.match(
    source,
    /repositories\/create[\s\S]{0,500}begin[\s\S]{0,300}lockSubjectQuota/,
  );
  assert.match(
    source,
    /assets\/upload-url[\s\S]{0,700}begin[\s\S]{0,300}lockSubjectQuota/,
  );
});

test("storage quota reserves every live upload session", () => {
  assert.match(
    source,
    /sum\(u\.expected_size\)[\s\S]{0,180}u\.expires_at>=now\(\)/i,
  );
  assert.match(
    source,
    /insert into yancuo\.upload_sessions[\s\S]{0,500}commit/i,
  );
});

test("reopening an existing repository does not consume another slot", () => {
  assert.match(
    source,
    /select repository_id,name,created_at,updated_at[\s\S]{0,300}if \(existing\.rowCount\)[\s\S]{0,200}commit/,
  );
  assert.doesNotMatch(
    source,
    /insert into yancuo\.repositories[\s\S]{0,250}on conflict/i,
  );
});
