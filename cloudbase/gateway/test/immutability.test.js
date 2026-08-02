"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");

test("committed release assets cannot be replaced", () => {
  assert.match(source, /发布附件已经存在且不可替换/);
  assert.doesNotMatch(
    source,
    /insert into yancuo\.release_assets[\s\S]{0,500}on conflict[\s\S]{0,200}do update/i,
  );
});

test("uploads use unique paths and commit sessions are claimed atomically", () => {
  assert.match(source, /\/uploads\/\$\{uploadId\}\/\$\{assetName\}/);
  assert.match(
    source,
    /delete from yancuo\.upload_sessions[\s\S]{0,300}returning \*/i,
  );
});
