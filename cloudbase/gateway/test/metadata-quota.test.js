"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");

test("release and per-release asset metadata have hard quotas", () => {
  assert.match(source, /MAX_RELEASES_PER_REPOSITORY/);
  assert.match(source, /资料库发布数量已达到上限/);
  assert.match(source, /MAX_ASSETS_PER_RELEASE/);
  assert.match(source, /发布附件数量已达到上限/);
  assert.match(
    source,
    /count\(\*\) from yancuo\.upload_sessions[\s\S]{0,150}expires_at>=now\(\)/i,
  );
});

test("release listing only joins assets for its bounded result page", () => {
  assert.match(source, /const RELEASE_LIST_LIMIT = 100/);
  assert.match(
    source,
    /release_tag=any\(\$2::text\[\]\)/i,
  );
  assert.doesNotMatch(
    source,
    /select release_tag,asset_name,byte_size from yancuo\.release_assets where repository_id=\$1"/i,
  );
});

test("release descriptions cannot multiply beyond the client response budget", () => {
  assert.match(source, /const MAX_RELEASE_BODY_BYTES = 64 \* 1024/);
  assert.match(source, /octet_length\(body\)<=\$2/);
  assert.match(source, /boundedText\(payload\.body, "发布说明", MAX_RELEASE_BODY_BYTES\)/);
});
