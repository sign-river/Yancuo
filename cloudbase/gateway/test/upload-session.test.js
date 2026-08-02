"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const gateway = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");
const schema = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "init.sql"), "utf8");

test("upload PUT atomically claims a single session", () => {
  assert.match(schema, /claimed_at timestamptz/);
  assert.match(
    gateway,
    /update yancuo\.upload_sessions set claimed_at=now\(\)[\s\S]{0,300}claimed_at is null returning \*/i,
  );
  assert.match(gateway, /上传已在进行或凭据已使用/);
});

test("failed uploads release their claim for a safe retry", () => {
  assert.match(
    gateway,
    /catch \(error\)[\s\S]{0,300}set claimed_at=null[\s\S]{0,200}throw error/i,
  );
  assert.match(
    gateway,
    /uploaded_at=now\(\),claimed_at=null/i,
  );
});

test("expired rows remain retryable when object deletion fails", () => {
  assert.match(
    gateway,
    /cloud\.deleteFile[\s\S]{0,120}catch \(_error\)[\s\S]{0,80}continue/i,
  );
  assert.doesNotMatch(
    gateway,
    /delete from yancuo\.upload_sessions[^\n]+returning file_id/i,
  );
});
