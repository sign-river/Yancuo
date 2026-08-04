"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const gateway = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");
const schema = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "init.sql"), "utf8");
const rpc = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "pgmode-rpc.sql"), "utf8");

test("upload PUT atomically claims a single session", () => {
  assert.match(schema, /claimed_at timestamptz/);
  assert.match(
    rpc,
    /update yancuo\.upload_sessions[\s\S]{0,400}claimed_at IS NULL[\s\S]{0,200}returning \*/i,
  );
  assert.match(gateway, /yancuo_upload_claim/);
  assert.match(gateway, /上传正在进行或凭证已使用/);
});

test("upload credentials are returned and consumed only through a request header", () => {
  assert.match(gateway, /"X-Yancuo-Upload-Token": uploadToken/);
  assert.match(gateway, /req\.headers\["x-yancuo-upload-token"\]/);
  assert.match(gateway, /url\.searchParams\.has\("token"\)/);
  assert.doesNotMatch(gateway, /\?token=\$\{encodeURIComponent\(uploadToken\)\}/);
});

test("failed uploads release their claim for a safe retry", () => {
  const upload = gateway.slice(gateway.indexOf("async function upload"));
  const catchAt = upload.indexOf("catch (error)");
  const releaseAt = upload.indexOf("yancuo_upload_unclaim", catchAt);
  const throwAt = upload.indexOf("throw error", releaseAt);
  assert.ok(catchAt >= 0 && catchAt < releaseAt && releaseAt < throwAt);
  assert.match(
    rpc,
    /uploaded_at = now\(\), claimed_at = null/i,
  );
});

test("completed upload PUT retries return success without replacing the object", () => {
  const upload = gateway.slice(gateway.indexOf("async function upload"));
  const completedAt = upload.indexOf("if (candidate.uploaded_at)");
  const claimAt = upload.indexOf("yancuo_upload_claim");
  const cloudUploadAt = upload.indexOf("cloud.uploadFile");
  assert.ok(completedAt >= 0 && completedAt < claimAt && claimAt < cloudUploadAt);
  assert.match(upload.slice(completedAt, claimAt), /uploaded: true/);
});

test("upload completion requires a file ID and one persisted session row", () => {
  const upload = gateway.slice(gateway.indexOf("async function upload"));
  assert.match(upload, /if \(!stored\?\.fileID\)/);
  assert.match(upload, /yancuo_upload_complete/);
});

test("a completed object retries durable session completion before releasing its claim", () => {
  const upload = gateway.slice(gateway.indexOf("async function upload"));
  const recoveryAt = upload.indexOf("yancuo_upload_recover");
  const releaseAt = upload.indexOf("yancuo_upload_unclaim", recoveryAt);
  assert.ok(recoveryAt >= 0 && recoveryAt < releaseAt);
  assert.match(rpc, /uploaded_at = coalesce\(uploaded_at, now\(\)\), claimed_at = null/);
});

test("expired rows remain retryable when object deletion fails", () => {
  assert.match(
    gateway,
    /cloud\.deleteFile[\s\S]{0,120}catch \(_error\)[\s\S]{0,80}continue/i,
  );
});

test("expired upload cleanup is bounded per request", () => {
  const cleanup = gateway.slice(
    gateway.indexOf("async function cleanupExpiredUploads"),
    gateway.indexOf("async function cleanupPendingDeletions"),
  );
  assert.match(cleanup, /yancuo_cleanup_list/);
  assert.match(cleanup, /yancuo_cleanup_delete/);
});
