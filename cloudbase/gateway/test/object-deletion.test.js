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
  const commitAt = branch.indexOf('client.query("commit")', releaseDeleteAt);
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

test("release deletion locks the release and refuses active uploads", () => {
  const branch = gateway.slice(gateway.indexOf('name === "releases/delete"'));
  const releaseLockAt = branch.indexOf("from yancuo.releases where repository_id=$1 and tag=$2 for update");
  const uploadLockAt = branch.indexOf("from yancuo.upload_sessions where repository_id=$1 and release_tag=$2 for update");
  const releaseDeleteAt = branch.indexOf("delete from yancuo.releases");
  assert.ok(releaseLockAt >= 0 && releaseLockAt < uploadLockAt);
  assert.ok(uploadLockAt < releaseDeleteAt);
  assert.match(branch, /uploads\.rows\.some\(\(row\) => row\.claimed_at !== null\)/);
});

test("uncommitted uploaded objects are queued before release deletion", () => {
  const branch = gateway.slice(gateway.indexOf('name === "releases/delete"'));
  assert.match(branch, /\[\.\.\.files\.rows, \.\.\.uploads\.rows\]/);
  const queueAt = branch.indexOf("insert into yancuo.object_deletions");
  const releaseDeleteAt = branch.indexOf("delete from yancuo.releases");
  assert.ok(queueAt >= 0 && queueAt < releaseDeleteAt);
});
