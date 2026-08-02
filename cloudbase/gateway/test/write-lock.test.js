"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");

test("server validates and renews a live write lease", () => {
  assert.match(
    source,
    /update yancuo\.write_locks set expires_at=now\(\)\+interval '15 minutes'[\s\S]{0,300}expires_at>now\(\) returning device_id/i,
  );
  assert.match(source, /主写入锁不存在或已经过期/);
});

test("every remote mutation requires the server-side lease", () => {
  for (const action of [
    "manifest/write",
    "releases/create",
    "assets/upload-url",
    "assets/commit",
    "releases/delete",
  ]) {
    const start = source.indexOf(`name === "${action}"`);
    assert.notEqual(start, -1, `${action} action missing`);
    const next = source.indexOf('name === "', start + 10);
    const branch = source.slice(start, next === -1 ? source.length : next);
    assert.match(branch, /requireWriteLock\(client, repo\.repository_id, payload\)/, action);
  }
});
