"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const gateway = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");
const rpc = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "pgmode-rpc.sql"), "utf8");

test("release and per-release asset metadata have hard quotas", () => {
  assert.match(gateway, /MAX_RELEASES_PER_REPOSITORY/);
  assert.match(gateway, /p_max_releases: MAX_RELEASES_PER_REPOSITORY/);
  assert.match(gateway, /MAX_ASSETS_PER_RELEASE/);
  assert.match(gateway, /p_max_assets: MAX_ASSETS_PER_RELEASE/);
  assert.match(rpc, /发布附件数量已达到上限/);
  assert.match(
    rpc,
    /select count\(\*\) from yancuo\.upload_sessions[\s\S]{0,200}expires_at >= now\(\)/i,
  );
});

test("release listing only joins assets for its bounded result page", () => {
  assert.match(gateway, /const RELEASE_LIST_LIMIT = 100/);
  assert.match(gateway, /p_limit: RELEASE_LIST_LIMIT/);
  assert.match(rpc, /LIMIT p_limit/);
  assert.doesNotMatch(
    gateway,
    /select release_tag,asset_name,byte_size from yancuo\.release_assets/i,
  );
});

test("release descriptions cannot multiply beyond the client response budget", () => {
  assert.match(gateway, /const MAX_RELEASE_BODY_BYTES = 64 \* 1024/);
  assert.match(gateway, /p_max_body_bytes: MAX_RELEASE_BODY_BYTES/);
  assert.match(rpc, /octet_length\(r\.body\) <= p_max_body_bytes/);
  assert.match(gateway, /boundedText\(payload\.body, "发布说明", MAX_RELEASE_BODY_BYTES\)/);
});
