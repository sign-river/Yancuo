"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const gateway = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");
const schema = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "init.sql"), "utf8");

test("release object deletion is durably queued before metadata commit", () => {
  assert.match(schema, /create table if not exists yancuo\.object_deletions/);
  const branch = gateway.slice(gateway.indexOf('name === "releases/delete"'));
  const queueAt = branch.indexOf("insert into yancuo.object_deletions");
  const releaseDeleteAt = branch.indexOf("delete from yancuo.releases");
  const commitAt = branch.indexOf('client.query("commit")');
  assert.ok(queueAt >= 0 && queueAt < releaseDeleteAt);
  assert.ok(releaseDeleteAt < commitAt);
});

test("failed object deletion retains its queue record for retry", () => {
  const cleanup = gateway.slice(
    gateway.indexOf("async function cleanupPendingDeletions"),
    gateway.indexOf("async function action"),
  );
  assert.match(cleanup, /catch \(_error\)[\s\S]{0,300}attempts=attempts\+1/);
  assert.match(cleanup, /continue;[\s\S]{0,200}delete from yancuo\.object_deletions/);
  assert.doesNotMatch(
    gateway,
    /releases\/delete[\s\S]{0,1200}deleteFile\([^;]+catch\(\(\) => undefined\)/,
  );
});
