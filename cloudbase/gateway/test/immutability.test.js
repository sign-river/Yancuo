"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const gateway = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");
const rpc = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "pgmode-rpc.sql"), "utf8");

test("committed release assets cannot be replaced", () => {
  assert.match(rpc, /发布附件已经存在且不可替换/);
  assert.doesNotMatch(
    rpc,
    /insert into yancuo\.release_assets[\s\S]{0,500}on conflict[\s\S]{0,200}do update/i,
  );
});

test("uploads use unique paths and commit sessions are claimed atomically", () => {
  assert.match(gateway, /\/uploads\/\$\{uploadId\}/);
  assert.match(
    rpc,
    /delete from yancuo\.upload_sessions[\s\S]{0,400}returning \*/i,
  );
});
