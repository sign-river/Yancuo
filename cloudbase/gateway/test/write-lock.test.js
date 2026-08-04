"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const gateway = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");
const rpc = fs.readFileSync(path.join(__dirname, "..", "..", "postgres", "pgmode-rpc.sql"), "utf8");

test("server validates and renews a live write lease", () => {
  assert.match(
    rpc,
    /update yancuo\.write_locks set expires_at = now\(\) \+ interval '15 minutes'[\s\S]{0,300}lease_id = p_lease_id[\s\S]{0,100}expires_at > now\(\)/i,
  );
  assert.match(rpc, /主写入锁不存在或已经过期/);
});

test("same-device tasks cannot share or release each other's lease", () => {
  assert.match(
    rpc,
    /write_locks\.device_id = excluded\.device_id and yancuo\.write_locks\.lease_id = excluded\.lease_id/i,
  );
  assert.match(
    rpc,
    /delete from yancuo\.write_locks WHERE repository_id = v_repo AND device_id = p_device_id AND lease_id = p_lease_id/i,
  );
});

test("every remote mutation passes the server-side lease to an RPC", () => {
  for (const rpcName of [
    "yancuo_manifest_write",
    "yancuo_releases_create",
    "yancuo_upload_url",
    "yancuo_assets_commit",
    "yancuo_releases_delete",
  ]) {
    assert.match(gateway, new RegExp(rpcName + '"'));
  }
  const mutationCalls = gateway.slice(gateway.indexOf('if (name === "manifest/write")'), gateway.indexOf('if (name === "locks/acquire")'));
  assert.match(mutationCalls, /p_device_id: deviceId/);
  assert.match(mutationCalls, /p_lease_id: leaseId/);
});
