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

test("upload credentials are returned and consumed only through a request header", () => {
  assert.match(gateway, /"X-Yancuo-Upload-Token": uploadToken/);
  assert.match(gateway, /req\.headers\["x-yancuo-upload-token"\]/);
  assert.match(gateway, /url\.searchParams\.has\("token"\)/);
  assert.doesNotMatch(gateway, /\?token=\$\{encodeURIComponent\(uploadToken\)\}/);
});

test("failed uploads release their claim for a safe retry", () => {
  const upload = gateway.slice(gateway.indexOf("async function upload"));
  const catchAt = upload.indexOf("catch (error)");
  const releaseAt = upload.indexOf("set claimed_at=null", catchAt);
  const throwAt = upload.indexOf("throw error", releaseAt);
  assert.ok(catchAt >= 0 && catchAt < releaseAt && releaseAt < throwAt);
  assert.match(
    gateway,
    /uploaded_at=now\(\),claimed_at=null/i,
  );
});

test("completed upload PUT retries return success without replacing the object", () => {
  const upload = gateway.slice(gateway.indexOf("async function upload"));
  const completedAt = upload.indexOf("if (candidate.uploaded_at)");
  const claimAt = upload.indexOf("set claimed_at=now()");
  const cloudUploadAt = upload.indexOf("cloud.uploadFile");
  assert.ok(completedAt >= 0 && completedAt < claimAt && claimAt < cloudUploadAt);
  assert.match(upload.slice(completedAt, claimAt), /uploaded: true/);
});

test("upload completion requires a file ID and one persisted session row", () => {
  const upload = gateway.slice(gateway.indexOf("async function upload"));
  assert.match(upload, /if \(!stored\?\.fileID\)/);
  assert.match(upload, /const completed = await pool\.query/);
  assert.match(upload, /completed\.rowCount !== 1/);
});

test("a completed object retries durable session completion before releasing its claim", () => {
  const upload = gateway.slice(gateway.indexOf("async function upload"));
  const recoveryAt = upload.indexOf("uploaded_at=coalesce(uploaded_at,now())");
  const releaseAt = upload.indexOf("set claimed_at=null where upload_id=$1 and uploaded_at is null");
  assert.ok(recoveryAt >= 0 && recoveryAt < releaseAt);
  assert.match(upload.slice(recoveryAt, releaseAt), /recovered\?\.rowCount === 1/);
  assert.match(upload.slice(recoveryAt, releaseAt), /uploaded: true/);
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

test("expired upload cleanup is bounded per request", () => {
  const cleanup = gateway.slice(
    gateway.indexOf("async function cleanupExpiredUploads"),
    gateway.indexOf("async function cleanupPendingDeletions"),
  );
  assert.match(cleanup, /order by expires_at limit 100/i);
});
