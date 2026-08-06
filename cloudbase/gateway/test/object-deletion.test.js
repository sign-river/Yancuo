"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const gateway = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");
const schema = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "init.sql"), "utf8");
const rpc = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "pgmode-rpc.sql"), "utf8");

test("release object deletion is durably queued before metadata commit", () => {
  assert.match(schema, /create table if not exists yancuo\.object_deletions/);
  const fn = rpc.slice(rpc.indexOf("yancuo_releases_delete"));
  const queueAt = fn.indexOf("INSERT INTO yancuo.object_deletions");
  const releaseDeleteAt = fn.indexOf("DELETE FROM yancuo.releases");
  assert.ok(queueAt >= 0 && queueAt < releaseDeleteAt, "queue must precede release delete");
});

test("failed object deletion retains its queue record for retry", () => {
  const cleanup = gateway.slice(
    gateway.indexOf("async function cleanupPendingDeletions"),
    gateway.indexOf("async function action"),
  );
  assert.match(cleanup, /catch \(_error\)[\s\S]{0,300}yancuo_deletions_retry/);
  assert.match(cleanup, /continue;[\s\S]{0,300}yancuo_deletions_done/);
});

test("release deletion locks the release and refuses active uploads", () => {
  const fn = rpc.slice(rpc.indexOf("yancuo_releases_delete"));
  const releaseLockAt = fn.search(/FROM yancuo\.releases[\s\S]{0,120}FOR UPDATE/);
  const uploadLockAt = fn.search(/FROM yancuo\.upload_sessions[\s\S]{0,120}FOR UPDATE/);
  const releaseDeleteAt = fn.indexOf("DELETE FROM yancuo.releases");
  assert.ok(releaseLockAt >= 0 && releaseLockAt < uploadLockAt);
  assert.ok(uploadLockAt < releaseDeleteAt);
  assert.match(fn, /claimed_at IS NOT NULL/);
});

test("uncommitted uploaded objects are queued before release deletion", () => {
  const fn = rpc.slice(rpc.indexOf("yancuo_releases_delete"));
  assert.match(fn, /UNION[\s\S]{0,200}upload_sessions/);
  const queueAt = fn.indexOf("INSERT INTO yancuo.object_deletions");
  const releaseDeleteAt = fn.indexOf("DELETE FROM yancuo.releases");
  assert.ok(queueAt >= 0 && queueAt < releaseDeleteAt);
});
