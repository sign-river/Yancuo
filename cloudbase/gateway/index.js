"use strict";

const http = require("node:http");
const { Transform } = require("node:stream");
const { Pool } = require("pg");
const tcb = require("@cloudbase/node-sdk");
const {
  boundedName,
  boundedText,
  newUploadToken,
  safeTokenEqual,
  tokenHash,
} = require("./security");

const PORT = Number(process.env.PORT || 9000);
const ENV_ID = String(process.env.CLOUDBASE_ENV_ID || process.env.TCB_ENV || "").trim();
const PUBLIC_URL = String(process.env.GATEWAY_PUBLIC_URL || "").replace(/\/$/, "");
const DATABASE_URL = String(process.env.DATABASE_URL || "").trim();
const MAX_BODY_BYTES = 1024 * 1024;
const MAX_MANIFEST_BYTES = 4 * 1024 * 1024;
const MAX_ASSET_BYTES = Math.min(
  Number(process.env.MAX_ASSET_BYTES || 512 * 1024 * 1024),
  512 * 1024 * 1024,
);
const USER_STORAGE_BYTES = Number(process.env.USER_STORAGE_BYTES || 512 * 1024 * 1024);
const USER_REPOSITORIES = Number(process.env.USER_REPOSITORIES || 5);
const RATE_PER_MINUTE = Number(process.env.RATE_PER_MINUTE || 120);

if (!ENV_ID || !DATABASE_URL) {
  throw new Error("CLOUDBASE_ENV_ID/TCB_ENV and DATABASE_URL are required");
}

const pool = new Pool({
  connectionString: DATABASE_URL,
  max: Number(process.env.PG_POOL_SIZE || 5),
  idleTimeoutMillis: 20_000,
  statement_timeout: 30_000,
  ssl: process.env.PG_SSL === "disable" ? false : { rejectUnauthorized: false },
});
const cloud = tcb.init({ env: ENV_ID });
const rateBuckets = new Map();

function fail(message, statusCode = 400) {
  const error = new Error(message);
  error.statusCode = statusCode;
  throw error;
}

function response(res, statusCode, payload) {
  const body = Buffer.from(JSON.stringify(payload), "utf8");
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
    fail("缺少有效登录凭据", 401);
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
  const profile = await authResponse.json();
  const subject = String(profile.sub || profile.user_id || "").trim();
  if (!subject || subject === "anon" || profile.is_anonymous === true) {
    fail("请使用正式账号登录", 403);
  }
  enforceRate(subject);
  return {
    subject,
    login: String(profile.email || profile.username || subject),
    displayName: String(profile.name || profile.username || profile.email || ""),
  };
}

function enforceRate(subject) {
  const minute = Math.floor(Date.now() / 60_000);
  const current = rateBuckets.get(subject);
  if (!current || current.minute !== minute) {
    rateBuckets.set(subject, { minute, count: 1 });
    return;
  }
  current.count += 1;
  if (current.count > RATE_PER_MINUTE) fail("请求过于频繁，请稍后再试", 429);
}

async function repository(client, subject, payload) {
  const name = boundedName(payload.repository || payload.name, "资料库名称");
  const result = await client.query(
    "select repository_id, name, created_at, updated_at from yancuo.repositories where subject_id=$1 and name=$2",
    [subject, name],
  );
  if (!result.rowCount) fail("资料库不存在", 404);
  return result.rows[0];
}

async function cleanupExpiredUploads(subject) {
  const expired = await pool.query(
    "delete from yancuo.upload_sessions where subject_id=$1 and expires_at < now() returning file_id",
    [subject],
  );
  const fileList = expired.rows.map((row) => row.file_id).filter(Boolean);
  if (fileList.length) {
    await cloud.deleteFile({ fileList }).catch(() => undefined);
  }
}

async function action(name, payload, identity, req) {
  if (String(req.headers["x-cloudbase-environment-id"] || "") !== ENV_ID) {
    fail("CloudBase 环境不匹配", 403);
  }
  const subject = identity.subject;
  if (name === "health") {
    await pool.query("select 1");
    return { environment_id: ENV_ID, subject };
  }
  if (name === "users/me") {
    return { login: identity.login, display_name: identity.displayName, subject };
  }
  if (name === "repositories/list") {
    const result = await pool.query(
      "select name, created_at, updated_at from yancuo.repositories where subject_id=$1 order by updated_at desc",
      [subject],
    );
    return { repositories: result.rows.map((row) => ({ ...row, owner: subject, private: true })) };
  }
  if (name === "repositories/create") {
    const repositoryName = boundedName(payload.name, "资料库名称");
    const client = await pool.connect();
    try {
      await client.query("begin");
      const count = await client.query(
        "select count(*)::int as value from yancuo.repositories where subject_id=$1",
        [subject],
      );
      if (count.rows[0].value >= USER_REPOSITORIES) fail("已达到个人资料库数量上限", 409);
      const created = await client.query(
        "insert into yancuo.repositories(subject_id, owner, name) values($1,$1,$2) on conflict(subject_id,name) do update set updated_at=now() returning repository_id,name,created_at,updated_at",
        [subject, repositoryName],
      );
      await client.query("commit");
      return { ...created.rows[0], owner: subject, private: true };
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  const client = await pool.connect();
  try {
    const repo = await repository(client, subject, payload);
    if (name === "repositories/get") return { ...repo, owner: subject, private: true };
    if (name === "manifest/read") {
      const result = await client.query(
        "select document from yancuo.manifests where repository_id=$1",
        [repo.repository_id],
      );
      return { manifest: result.rows[0]?.document || null };
    }
    if (name === "manifest/write") {
      if (!payload.manifest || typeof payload.manifest !== "object" || Array.isArray(payload.manifest)) {
        fail("manifest 必须是 JSON 对象");
      }
      if (Buffer.byteLength(JSON.stringify(payload.manifest), "utf8") > MAX_MANIFEST_BYTES) {
        fail("manifest 超过大小限制", 413);
      }
      await client.query(
        "insert into yancuo.manifests(repository_id,document) values($1,$2::jsonb) on conflict(repository_id) do update set document=excluded.document,updated_at=now()",
        [repo.repository_id, JSON.stringify(payload.manifest)],
      );
      return { written: true };
    }
    if (name === "releases/list") {
      const releases = await client.query(
        "select tag,name,body,created_at from yancuo.releases where repository_id=$1 order by created_at desc limit 500",
        [repo.repository_id],
      );
      const assets = await client.query(
        "select release_tag,asset_name,byte_size from yancuo.release_assets where repository_id=$1",
        [repo.repository_id],
      );
      const byTag = new Map();
      for (const row of assets.rows) {
        if (!byTag.has(row.release_tag)) byTag.set(row.release_tag, []);
        byTag.get(row.release_tag).push({ name: row.asset_name, size: Number(row.byte_size) });
      }
      return { releases: releases.rows.map((row) => ({ ...row, assets: byTag.get(row.tag) || [] })) };
    }
    if (name === "releases/create") {
      const tag = boundedName(payload.tag, "发布标签");
      const releaseName = boundedText(payload.name || tag, "发布名称", 512);
      const body = boundedText(payload.body, "发布说明", 1024 * 1024);
      const inserted = await client.query(
        "insert into yancuo.releases(repository_id,tag,name,body) values($1,$2,$3,$4) on conflict(repository_id,tag) do nothing returning tag,name,body,created_at",
        [repo.repository_id, tag, releaseName, body],
      );
      if (!inserted.rowCount) {
        const existing = await client.query(
          "select tag,name,body,created_at from yancuo.releases where repository_id=$1 and tag=$2",
          [repo.repository_id, tag],
        );
        const row = existing.rows[0];
        if (!row || row.name !== releaseName || row.body !== body) fail("发布标签已被不同内容占用", 409);
        return row;
      }
      return inserted.rows[0];
    }
    if (name === "assets/upload-url") {
      await cleanupExpiredUploads(subject);
      if (!PUBLIC_URL.startsWith("https://")) fail("网关未配置 HTTPS 公网地址", 503);
      const tag = boundedName(payload.tag, "发布标签");
      const assetName = boundedName(payload.asset_name, "资源名称");
      const size = Number(payload.size);
      if (!Number.isSafeInteger(size) || size < 0 || size > MAX_ASSET_BYTES) fail("资源大小无效", 413);
      const release = await client.query(
        "select 1 from yancuo.releases where repository_id=$1 and tag=$2",
        [repo.repository_id, tag],
      );
      if (!release.rowCount) fail("发布不存在", 404);
      const usage = await client.query(
        "select coalesce(sum(byte_size),0)::bigint as bytes from yancuo.release_assets where repository_id in (select repository_id from yancuo.repositories where subject_id=$1)",
        [subject],
      );
      if (Number(usage.rows[0].bytes) + size > USER_STORAGE_BYTES) fail("已达到个人云存储额度", 409);
      const uploadId = cryptoRandomId();
      const uploadToken = newUploadToken();
      const storagePath = `yancuo/${subject}/${repo.repository_id}/releases/${tag}/${assetName}`;
      await client.query(
        "insert into yancuo.upload_sessions(upload_id,subject_id,repository_id,release_tag,asset_name,storage_path,expected_size,token_hash,expires_at) values($1,$2,$3,$4,$5,$6,$7,$8,now()+interval '10 minutes')",
        [uploadId, subject, repo.repository_id, tag, assetName, storagePath, size, tokenHash(uploadToken)],
      );
      return {
        url: `${PUBLIC_URL}/uploads/${uploadId}?token=${encodeURIComponent(uploadToken)}`,
        headers: { "Content-Type": "application/octet-stream" },
        upload_id: uploadId,
      };
    }
    if (name === "assets/commit") {
      const uploadId = String(payload.upload_id || "");
      const result = await client.query(
        "select * from yancuo.upload_sessions where upload_id=$1 and subject_id=$2 and repository_id=$3 and uploaded_at is not null and expires_at>=now()",
        [uploadId, subject, repo.repository_id],
      );
      const upload = result.rows[0];
      if (!upload || Number(upload.actual_size) !== Number(upload.expected_size)) fail("上传尚未完成或大小不匹配", 409);
      if (upload.release_tag !== payload.tag || upload.asset_name !== payload.asset_name) fail("上传提交参数不匹配", 409);
      await client.query("begin");
      await client.query(
        "insert into yancuo.release_assets(repository_id,release_tag,asset_name,storage_path,file_id,byte_size) values($1,$2,$3,$4,$5,$6) on conflict(repository_id,release_tag,asset_name) do update set storage_path=excluded.storage_path,file_id=excluded.file_id,byte_size=excluded.byte_size,committed_at=now()",
        [repo.repository_id, upload.release_tag, upload.asset_name, upload.storage_path, upload.file_id, upload.actual_size],
      );
      await client.query("delete from yancuo.upload_sessions where upload_id=$1", [uploadId]);
      await client.query("commit");
      return { name: upload.asset_name, size: Number(upload.actual_size) };
    }
    if (name === "assets/download-url") {
      const tag = boundedName(payload.tag, "发布标签");
      const assetName = boundedName(payload.asset_name, "资源名称");
      const found = await client.query(
        "select file_id from yancuo.release_assets where repository_id=$1 and release_tag=$2 and asset_name=$3",
        [repo.repository_id, tag, assetName],
      );
      if (!found.rowCount) fail("资源不存在", 404);
      const urls = await cloud.getTempFileURL({ fileList: [{ fileID: found.rows[0].file_id, maxAge: 600 }] });
      const url = urls.fileList?.[0]?.tempFileURL;
      if (!url) fail("无法生成临时下载地址", 502);
      return { url };
    }
    if (name === "locks/acquire") {
      const deviceId = boundedName(payload.device_id, "设备 ID");
      const locked = await client.query(
        "insert into yancuo.write_locks(repository_id,device_id,expires_at) values($1,$2,now()+interval '15 minutes') on conflict(repository_id) do update set device_id=excluded.device_id,expires_at=excluded.expires_at,updated_at=now() where yancuo.write_locks.expires_at<=now() or yancuo.write_locks.device_id=excluded.device_id returning device_id,expires_at",
        [repo.repository_id, deviceId],
      );
      return { acquired: Boolean(locked.rowCount), expires_at: locked.rows[0]?.expires_at || null };
    }
    if (name === "locks/release") {
      const deviceId = boundedName(payload.device_id, "设备 ID");
      await client.query(
        "delete from yancuo.write_locks where repository_id=$1 and device_id=$2",
        [repo.repository_id, deviceId],
      );
      return { released: true };
    }
    if (name === "releases/delete") {
      const tag = boundedName(payload.tag, "发布标签");
      const files = await client.query(
        "select file_id from yancuo.release_assets where repository_id=$1 and release_tag=$2",
        [repo.repository_id, tag],
      );
      await client.query(
        "delete from yancuo.releases where repository_id=$1 and tag=$2",
        [repo.repository_id, tag],
      );
      const fileList = files.rows.map((row) => row.file_id).filter(Boolean);
      if (fileList.length) await cloud.deleteFile({ fileList }).catch(() => undefined);
      return { deleted: true };
    }
    fail("不支持的操作", 404);
  } catch (error) {
    await client.query("rollback").catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}

function cryptoRandomId() {
  return require("node:crypto").randomUUID();
}

async function upload(req, res, url) {
  const uploadId = url.pathname.split("/").pop();
  const token = String(url.searchParams.get("token") || "");
  const result = await pool.query(
    "select * from yancuo.upload_sessions where upload_id=$1 and expires_at>=now() and uploaded_at is null",
    [uploadId],
  );
  const row = result.rows[0];
  if (!row || !safeTokenEqual(token, row.token_hash)) fail("上传凭据无效或已过期", 403);
  let actual = 0;
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
  req.pipe(counter);
  const stored = await cloud.uploadFile({ cloudPath: row.storage_path, fileContent: counter });
  if (actual !== Number(row.expected_size)) {
    if (stored.fileID) await cloud.deleteFile({ fileList: [stored.fileID] }).catch(() => undefined);
    fail("上传内容大小与声明不一致", 409);
  }
  await pool.query(
    "update yancuo.upload_sessions set file_id=$2,actual_size=$3,uploaded_at=now() where upload_id=$1",
    [uploadId, stored.fileID, actual],
  );
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
  await pool.end();
});
