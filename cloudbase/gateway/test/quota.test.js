"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const gateway = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");
const rpc = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "pgmode-rpc.sql"), "utf8");

test("per-user quotas are serialized inside the database function", () => {
  assert.match(rpc, /pg_advisory_xact_lock\(hashtextextended\(p_subject, 0\)\)/);
  assert.match(rpc, /yancuo_repositories_create\(p_subject text, p_name text, p_max_repos integer\)/);
  assert.match(rpc, /yancuo_upload_url\([^)]*p_max_assets integer[^)]*\)/s);
});

test("storage quota reserves every live upload session", () => {
  assert.match(rpc, /sum\(u\.expected_size\)[\s\S]{0,200}expires_at >= now\(\)/i);
  assert.match(gateway, /p_user_storage_bytes: USER_STORAGE_BYTES/);
});

test("reopening an existing repository does not consume another slot", () => {
  assert.match(rpc, /SELECT repository_id, name, created_at, updated_at INTO v_existing[\s\S]{0,300}IF FOUND THEN[\s\S]{0,200}RETURN jsonb_build_object\('ok', true/);
  assert.doesNotMatch(
    rpc,
    /insert into yancuo\.repositories[\s\S]{0,250}on conflict/i,
  );
});
