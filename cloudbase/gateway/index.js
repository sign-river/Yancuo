"use strict";

const http = require("node:http");
const { Transform } = require("node:stream");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const tcb = require("@cloudbase/node-sdk");
const {
  boundedName,
  boundedText,
  environmentId,
  integerSetting,
  newUploadToken,
  safeTokenEqual,
  subjectStorageKey,
  tokenHash,
  uuidV4,
} = require("./security");

const PORT = integerSetting(process.env.PORT, "PORT", 9000, 1, 65535);
const ENV_ID = environmentId(process.env.CLOUDBASE_ENV_ID || process.env.TCB_ENV);
const PUBLIC_URL = String(process.env.GATEWAY_PUBLIC_URL || "").replace(/\/$/, "");
const RDB_API_KEY = String(process.env.RDB_API_KEY || "").trim();
const MAX_BODY_BYTES = 1024 * 1024;
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const MAX_AUTH_RESPONSE_BYTES = 64 * 1024;
const MAX_MANIFEST_BYTES = 4 * 1024 * 1024;
const MAX_RELEASE_BODY_BYTES = 64 * 1024;
const RELEASE_LIST_LIMIT = 100;
const MAX_ASSET_BYTES = integerSetting(
  process.env.MAX_ASSET_BYTES,
  "MAX_ASSET_BYTES",
  512 * 1024 * 1024,
  1,
  512 * 1024 * 1024,
);
const USER_STORAGE_BYTES = integerSetting(
  process.env.USER_STORAGE_BYTES,
  "USER_STORAGE_BYTES",
  512 * 1024 * 1024,
  1,
  Number.MAX_SAFE_INTEGER,
);
const USER_REPOSITORIES = integerSetting(
  process.env.USER_REPOSITORIES,
  "USER_REPOSITORIES",
  5,
  1,
  1000,
);
const MAX_RELEASES_PER_REPOSITORY = integerSetting(
  process.env.MAX_RELEASES_PER_REPOSITORY,
  "MAX_RELEASES_PER_REPOSITORY",
  10_000,
  1,
  100_000,
);
const MAX_ASSETS_PER_RELEASE = integerSetting(
  process.env.MAX_ASSETS_PER_RELEASE,
  "MAX_ASSETS_PER_RELEASE",
  16,
  1,
  1000,
);
const RATE_PER_MINUTE = integerSetting(
  process.env.RATE_PER_MINUTE,
  "RATE_PER_MINUTE",
  120,
  1,
  1_000_000,
);
const RDB_TIMEOUT_MS = integerSetting(process.env.RDB_TIMEOUT_MS, "RDB_TIMEOUT_MS", 15_000, 1_000, 60_000);

if (!RDB_API_KEY) {
  throw new Error("RDB_API_KEY is required");
}

const RDB_BASE = `https://${ENV_ID}.api.tcloudbasegateway.com/v1/rdb/rest`;

async function callRpc(name, params = {}) {
  const endpoint = `${RDB_BASE}/rpc/${name}`;
  const rdbResponse = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${RDB_API_KEY}`,
      Accept: "application/json",
      Prefer: "return=representation",
    },
    body: JSON.stringify(params),
    signal: AbortSignal.timeout(RDB_TIMEOUT_MS),
  });
  const raw = await readResponseBytes(rdbResponse, MAX_RESPONSE_BYTES, "数据库服务");
  let value;
  try {
    value = JSON.parse(raw.toString("utf8"));
  } catch (_error) {
    fail("数据库服务返回无效响应", 502);
  }
  if (!rdbResponse.ok) {
    const message = String(
      value?.message || value?.code || value?.Error?.Message || value?.error || "数据库操作失败",
    );
    fail(message, 502);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail("数据库服务返回无效响应", 502);
  }
  if (value.ok !== true) {
    const status = Number(value.status || 400);
    fail(String(value.error || "数据库操作失败"), Number.isInteger(status) && status >= 400 && status <= 599 ? status : 400);
  }
  return value.data;
}

async function readResponseBytes(response, maxBytes, label) {
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > maxBytes) {
      await reader.cancel();
      fail(`${label}响应超过大小限制`, 502);
    }
    chunks.push(Buffer.from(value));
  }
  return Buffer.concat(chunks, size);
}

const cloud = tcb.init({ env: ENV_ID });

function fail(message, statusCode = 400) {
  const error = new Error(message);
  error.statusCode = statusCode;
  throw error;
}

function response(res, statusCode, payload) {
  let body = Buffer.from(JSON.stringify(payload), "utf8");
  if (body.length > MAX_RESPONSE_BYTES) {
    statusCode = 502;
    body = Buffer.from(
      JSON.stringify({ ok: false, error: "网关响应超过大小限制" }),
      "utf8",
    );
  }
  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": String(body.length),
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  res.end(body);
}

async function readJson(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) fail("请求体超过大小限制", 413);
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  try {
    const value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!value || typeof value !== "object" || Array.isArray(value)) fail("请求体必须是 JSON 对象");
    return value;
  } catch (error) {
    if (error.statusCode) throw error;
    fail("请求体不是有效 JSON");
  }
}

function bearer(req) {
  const value = String(req.headers.authorization || "");
  if (!value.startsWith("Bearer ") || value.length < 16 || value.length > 16 * 1024) {
    fail("缺少有效登录凭证", 401);
  }
  return value.slice(7);
}

async function authenticate(req) {
  const token = bearer(req);
  const endpoint = `https://${ENV_ID}.api.tcloudbasegateway.com/auth/v1/user/me`;
  const authResponse = await fetch(endpoint, {
    headers: { Authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(10_000),
  });
  if (!authResponse.ok) fail("登录已失效，请重新登录", 401);
  const authBody = await readResponseBytes(
    authResponse,
    MAX_AUTH_RESPONSE_BYTES,
    "身份服务",
  );
  let profile;
  try {
    profile = JSON.parse(authBody.toString("utf8"));
  } catch (_error) {
    fail("身份服务返回无效响应", 502);
  }
  if (!profile || typeof profile !== "object" || Array.isArray(profile)) {
    fail("身份服务返回无效响应", 502);
  }
  const subject = String(profile.sub || profile.user_id || "").trim();
  if (!subject || subject === "anon" || profile.is_anonymous === true) {
    fail("请使用正式账号登录", 403);
  }
  if (Buffer.byteLength(subject, "utf8") > 512) fail("身份标识超过大小限制", 502);
  await enforceRate(subject);
  return {
    subject,
    login: String(profile.email || profile.username || subject),
    displayName: String(profile.name || profile.username || profile.email || ""),
  };
}

async function enforceRate(subject) {
  const data = await callRpc("yancuo_rate_limit", { p_subject: subject });
  if (Number(data.request_count) > RATE_PER_MINUTE) {
    fail("请求过于频繁，请稍后再试", 429);
  }
}

async function cleanupExpiredUploads(subject) {
  const rows = await callRpc("yancuo_cleanup_list", { p_subject: subject });
  for (const row of rows) {
    if (row.file_id) {
      try {
        await cloud.deleteFile({ fileList: [row.file_id] });
      } catch (_error) {
        continue;
      }
    }
    await callRpc("yancuo_cleanup_delete", { p_subject: subject, p_upload_id: String(row.upload_id) });
  }
}

async function cleanupPendingDeletions(subject) {
  const rows = await callRpc("yancuo_deletions_list", { p_subject: subject });
  for (const row of rows) {
    try {
      await cloud.deleteFile({ fileList: [row.file_id] });
    } catch (_error) {
      await callRpc("yancuo_deletions_retry", { p_subject: subject, p_file_id: String(row.file_id) });
      continue;
    }
    await callRpc("yancuo_deletions_done", { p_subject: subject, p_file_id: String(row.file_id) });
  }
}

async function action(name, payload, identity, req) {
  if (String(req.headers["x-cloudbase-environment-id"] || "") !== ENV_ID) {
    fail("CloudBase 环境不匹配", 403);
  }
  const subject = identity.subject;
  if (name === "health") {
    await callRpc("yancuo_health", {});
    return { environment_id: ENV_ID, subject };
  }
  if (name === "users/me") {
    return { login: identity.login, display_name: identity.displayName, subject };
  }
  if (name === "repositories/list") {
    const rows = await callRpc("yancuo_repositories_list", { p_subject: subject });
    return { repositories: rows.map((row) => ({ ...row, owner: subject, private: true })) };
  }
  if (name === "repositories/create") {
    const repositoryName = boundedName(payload.name, "资料库名称");
    const data = await callRpc("yancuo_repositories_create", {
      p_subject: subject,
      p_name: repositoryName,
      p_max_repos: USER_REPOSITORIES,
    });
    return { ...data.row, owner: subject, private: true };
  }
  if (name === "repositories/get") {
    const repositoryName = boundedName(payload.repository || payload.name, "资料库名称");
    const data = await callRpc("yancuo_repository", { p_subject: subject, p_name: repositoryName });
    return { ...data, owner: subject, private: true };
  }
  if (name === "manifest/read") {
    const repositoryName = boundedName(payload.repository || payload.name, "资料库名称");
    const data = await callRpc("yancuo_manifest_read", { p_subject: subject, p_name: repositoryName });
    return { manifest: data.manifest || null };
  }
  if (name === "manifest/write") {
    if (!payload.manifest || typeof payload.manifest !== "object" || Array.isArray(payload.manifest)) {
      fail("manifest 必须是 JSON 对象");
    }
    if (Buffer.byteLength(JSON.stringify(payload.manifest), "utf8") > MAX_MANIFEST_BYTES) {
      fail("manifest 超过大小限制", 413);
    }
    const repositoryName = boundedName(payload.repository || payload.name, "资料库名称");
    const deviceId = boundedName(payload.device_id, "设备 ID");
    const leaseId = boundedName(payload.lease_id, "租赁 ID");
    await callRpc("yancuo_manifest_write", {
      p_subject: subject,
      p_name: repositoryName,
      p_device_id: deviceId,
      p_lease_id: leaseId,
      p_document: payload.manifest,
    });
    return { written: true };
  }
  if (name === "releases/list") {
    await cleanupPendingDeletions(subject);
    const repositoryName = boundedName(payload.repository || payload.name, "资料库名称");
    const rows = await callRpc("yancuo_releases_list", {
      p_subject: subject,
      p_name: repositoryName,
      p_max_body_bytes: MAX_RELEASE_BODY_BYTES,
      p_limit: RELEASE_LIST_LIMIT,
    });
    return { releases: rows };
  }
  if (name === "releases/create") {
    const tag = boundedName(payload.tag, "发布标签");
    const releaseName = boundedText(payload.name || tag, "发布名称", 512);
    const body = boundedText(payload.body, "发布说明", MAX_RELEASE_BODY_BYTES);
    if (Buffer.byteLength(JSON.stringify(body), "utf8") > MAX_RELEASE_BODY_BYTES) {
      fail("发布说明 JSON 编码后超过大小限制", 413);
    }
    const repositoryName = boundedName(payload.repository || payload.name, "资料库名称");
    const deviceId = boundedName(payload.device_id, "设备 ID");
    const leaseId = boundedName(payload.lease_id, "租赁 ID");
    return await callRpc("yancuo_releases_create", {
      p_subject: subject,
      p_name: repositoryName,
      p_device_id: deviceId,
      p_lease_id: leaseId,
      p_tag: tag,
      p_release_name: releaseName,
      p_body: body,
      p_max_releases: MAX_RELEASES_PER_REPOSITORY,
    });
  }
  if (name === "assets/upload-url") {
    await cleanupExpiredUploads(subject);
    if (!PUBLIC_URL.startsWith("https://")) fail("网关未配置 HTTPS 公网地址", 503);
    const tag = boundedName(payload.tag, "发布标签");
    const assetName = boundedName(payload.asset_name, "资源名称");
    const size = Number(payload.size);
    if (!Number.isSafeInteger(size) || size < 0 || size > MAX_ASSET_BYTES) fail("资源大小无效", 413);
    const repositoryName = boundedName(payload.repository || payload.name, "资料库名称");
    const deviceId = boundedName(payload.device_id, "设备 ID");
    const leaseId = boundedName(payload.lease_id, "租赁 ID");
    const uploadId = cryptoRandomId();
    const uploadToken = newUploadToken();
    const storagePath = `yancuo/${subjectStorageKey(subject)}/${repositoryName}/uploads/${uploadId}/${assetName}`;
    await callRpc("yancuo_upload_url", {
      p_subject: subject,
      p_name: repositoryName,
      p_device_id: deviceId,
      p_lease_id: leaseId,
      p_tag: tag,
      p_asset_name: assetName,
      p_size: size,
      p_upload_id: uploadId,
      p_token_hash: tokenHash(uploadToken),
      p_storage_path: storagePath,
      p_max_assets: MAX_ASSETS_PER_RELEASE,
      p_user_storage_bytes: USER_STORAGE_BYTES,
    });
    return {
      url: `${PUBLIC_URL}/uploads/${uploadId}`,
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Yancuo-Upload-Token": uploadToken,
      },
      upload_id: uploadId,
    };
  }
  if (name === "assets/commit") {
    const uploadId = uuidV4(payload.upload_id, "上传 ID");
    const repositoryName = boundedName(payload.repository || payload.name, "资料库名称");
    const deviceId = boundedName(payload.device_id, "设备 ID");
    const leaseId = boundedName(payload.lease_id, "租赁 ID");
    const tag = boundedName(payload.tag, "发布标签");
    const assetName = boundedName(payload.asset_name, "资源名称");
    return await callRpc("yancuo_assets_commit", {
      p_subject: subject,
      p_name: repositoryName,
      p_device_id: deviceId,
      p_lease_id: leaseId,
      p_tag: tag,
      p_asset_name: assetName,
      p_upload_id: uploadId,
    });
  }
  if (name === "assets/download-url") {
    const tag = boundedName(payload.tag, "发布标签");
    const assetName = boundedName(payload.asset_name, "资源名称");
    const repositoryName = boundedName(payload.repository || payload.name, "资料库名称");
    const data = await callRpc("yancuo_asset_file", {
      p_subject: subject,
      p_name: repositoryName,
      p_tag: tag,
      p_asset_name: assetName,
    });
    const urls = await cloud.getTempFileURL({ fileList: [{ fileID: data.file_id, maxAge: 600 }] });
    const url = urls.fileList?.[0]?.tempFileURL;
    if (!url) fail("无法生成临时下载地址", 502);
    return { url };
  }
  if (name === "locks/acquire") {
    const deviceId = boundedName(payload.device_id, "设备 ID");
    const leaseId = boundedName(payload.lease_id, "租赁 ID");
    const repositoryName = boundedName(payload.repository || payload.name, "资料库名称");
    return await callRpc("yancuo_locks_acquire", {
      p_subject: subject,
      p_name: repositoryName,
      p_device_id: deviceId,
      p_lease_id: leaseId,
    });
  }
  if (name === "locks/release") {
    const deviceId = boundedName(payload.device_id, "设备 ID");
    const leaseId = boundedName(payload.lease_id, "租赁 ID");
    const repositoryName = boundedName(payload.repository || payload.name, "资料库名称");
    await callRpc("yancuo_locks_release", {
      p_subject: subject,
      p_name: repositoryName,
      p_device_id: deviceId,
      p_lease_id: leaseId,
    });
    return { released: true };
  }
  if (name === "releases/delete") {
    const tag = boundedName(payload.tag, "发布标签");
    const repositoryName = boundedName(payload.repository || payload.name, "资料库名称");
    const deviceId = boundedName(payload.device_id, "设备 ID");
    const leaseId = boundedName(payload.lease_id, "租赁 ID");
    await callRpc("yancuo_releases_delete", {
      p_subject: subject,
      p_name: repositoryName,
      p_device_id: deviceId,
      p_lease_id: leaseId,
      p_tag: tag,
    });
    await cleanupPendingDeletions(subject);
    return { deleted: true };
  }
  fail("不支持的操作", 404);
}

function cryptoRandomId() {
  return require("node:crypto").randomUUID();
}

async function upload(req, res, url) {
  const uploadId = uuidV4(url.pathname.split("/").pop(), "上传 ID");
  if (url.searchParams.has("token")) fail("上传凭证不得放在 URL 中", 400);
  const token = String(req.headers["x-yancuo-upload-token"] || "");
  const candidate = await callRpc("yancuo_upload_find", { p_upload_id: uploadId });
  if (!candidate || !safeTokenEqual(token, candidate.token_hash)) fail("上传凭证无效或已过期", 403);
  if (candidate.uploaded_at) {
    if (!candidate.file_id || Number(candidate.actual_size) !== Number(candidate.expected_size)) {
      fail("上传会话状态损坏", 409);
    }
    response(res, 200, { ok: true, data: { uploaded: true } });
    return;
  }
  const row = await callRpc("yancuo_upload_claim", {
    p_upload_id: uploadId,
    p_token_hash: tokenHash(token),
  });
  if (!row) fail("上传正在进行或凭证已使用", 409);
  const tempPath = path.join(os.tmpdir(), `yancuo-upload-${uploadId}`);
  let actual = 0;
  let stored;
  try {
    await new Promise((resolve, reject) => {
      const counter = new Transform({
        transform(chunk, _encoding, callback) {
          actual += chunk.length;
          if (actual > Number(row.expected_size) || actual > MAX_ASSET_BYTES) {
            callback(Object.assign(new Error("上传内容超过声明大小"), { statusCode: 413 }));
          } else {
            callback(null, chunk);
          }
        },
      });
      const writeStream = fs.createWriteStream(tempPath, { flags: "wx" });
      writeStream.on("error", reject);
      req.on("error", reject);
      counter.on("error", reject);
      writeStream.on("finish", resolve);
      req.pipe(counter).pipe(writeStream);
    });
    if (actual !== Number(row.expected_size)) {
      fail("上传内容大小与声明不一致", 409);
    }
    stored = await cloud.uploadFile({ cloudPath: row.storage_path, fileContent: fs.createReadStream(tempPath) });
    if (!stored?.fileID) fail("云存储未返回文件标识", 502);
    await callRpc("yancuo_upload_complete", {
      p_upload_id: uploadId,
      p_file_id: stored.fileID,
      p_actual_size: actual,
    });
  } catch (error) {
    if (stored?.fileID && actual === Number(row.expected_size)) {
      const recovered = await callRpc("yancuo_upload_recover", {
        p_upload_id: uploadId,
        p_file_id: stored.fileID,
        p_actual_size: actual,
      }).catch(() => null);
      if (recovered) {
        response(res, 200, { ok: true, data: { uploaded: true } });
        return;
      }
    }
    await callRpc("yancuo_upload_unclaim", { p_upload_id: uploadId }).catch(() => undefined);
    throw error;
  } finally {
    await fs.promises.unlink(tempPath).catch(() => undefined);
  }
  response(res, 200, { ok: true, data: { uploaded: true } });
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, "http://localhost");
    if (req.method === "PUT" && url.pathname.startsWith("/uploads/")) {
      await upload(req, res, url);
      return;
    }
    if (req.method !== "POST" || !url.pathname.startsWith("/actions/")) {
      response(res, 404, { ok: false, error: "Not Found" });
      return;
    }
    const identity = await authenticate(req);
    const payload = await readJson(req);
    const name = decodeURIComponent(url.pathname.slice("/actions/".length));
    const data = await action(name, payload, identity, req);
    response(res, 200, { ok: true, data });
  } catch (error) {
    const status = Number(error.statusCode || 500);
    if (status >= 500) console.error("gateway request failed", { status, type: error.name });
    response(res, status, { ok: false, error: status >= 500 ? "服务暂时不可用" : error.message });
  }
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`yancuo-cloud-gateway listening on ${PORT}`);
});

process.on("SIGTERM", async () => {
  server.close();
});
