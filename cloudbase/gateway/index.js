"use strict";

const http = require("node:http");
const { Transform } = require("node:stream");
const { Pool } = require("pg");
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
} = require("./security");

const PORT = integerSetting(process.env.PORT, "PORT", 9000, 1, 65535);
const ENV_ID = environmentId(process.env.CLOUDBASE_ENV_ID || process.env.TCB_ENV);
const PUBLIC_URL = String(process.env.GATEWAY_PUBLIC_URL || "").replace(/\/$/, "");
const DATABASE_URL = String(process.env.DATABASE_URL || "").trim();
const MAX_BODY_BYTES = 1024 * 1024;
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
const PG_POOL_SIZE = integerSetting(process.env.PG_POOL_SIZE, "PG_POOL_SIZE", 5, 1, 100);

if (!DATABASE_URL) {
  throw new Error("DATABASE_URL is required");
}

const pool = new Pool({
  connectionString: DATABASE_URL,
  max: PG_POOL_SIZE,
  idleTimeoutMillis: 20_000,
  statement_timeout: 30_000,
  ssl: process.env.PG_SSL === "disable" ? false : { rejectUnauthorized: false },
});
const cloud = tcb.init({ env: ENV_ID });

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

async function readResponseBytes(upstream, maxBytes, label) {
  if (!upstream.body) fail(`${label}没有响应体`, 502);
  const reader = upstream.body.getReader();
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
  const result = await pool.query(
    "insert into yancuo.rate_limits(subject_id,window_start,request_count) values($1,date_trunc('minute',now()),1) on conflict(subject_id) do update set window_start=case when yancuo.rate_limits.window_start<date_trunc('minute',now()) then excluded.window_start else yancuo.rate_limits.window_start end,request_count=case when yancuo.rate_limits.window_start<date_trunc('minute',now()) then 1 else yancuo.rate_limits.request_count+1 end returning request_count",
    [subject],
  );
  if (Number(result.rows[0].request_count) > RATE_PER_MINUTE) {
    fail("请求过于频繁，请稍后再试", 429);
  }
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

async function lockSubjectQuota(client, subject) {
  // Serialize quota reservations for one identity across all gateway instances.
  // Hash collisions only cause harmless extra serialization.
  await client.query("select pg_advisory_xact_lock(hashtextextended($1, 0))", [subject]);
}

async function requireWriteLock(client, repositoryId, payload) {
  const deviceId = boundedName(payload.device_id, "设备 ID");
  const leaseId = boundedName(payload.lease_id, "租约 ID");
  const locked = await client.query(
    "update yancuo.write_locks set expires_at=now()+interval '15 minutes',updated_at=now() where repository_id=$1 and device_id=$2 and lease_id=$3 and expires_at>now() returning device_id",
    [repositoryId, deviceId, leaseId],
  );
  if (!locked.rowCount) fail("主写入锁不存在或已经过期", 409);
  return deviceId;
}

async function cleanupExpiredUploads(subject) {
  const expired = await pool.query(
    "select upload_id,file_id from yancuo.upload_sessions where subject_id=$1 and expires_at < now() and (claimed_at is null or claimed_at < now()-interval '1 hour')",
    [subject],
  );
  for (const row of expired.rows) {
    if (row.file_id) {
      try {
        await cloud.deleteFile({ fileList: [row.file_id] });
      } catch (_error) {
        continue;
      }
    }
    await pool.query(
      "delete from yancuo.upload_sessions where upload_id=$1 and subject_id=$2 and expires_at < now() and (claimed_at is null or claimed_at < now()-interval '1 hour')",
      [row.upload_id, subject],
    );
  }
}

async function cleanupPendingDeletions(subject) {
  const pending = await pool.query(
    "select file_id from yancuo.object_deletions where subject_id=$1 order by queued_at limit 100",
    [subject],
  );
  for (const row of pending.rows) {
    try {
      await cloud.deleteFile({ fileList: [row.file_id] });
    } catch (_error) {
      await pool.query(
        "update yancuo.object_deletions set attempts=attempts+1,last_attempt_at=now() where file_id=$1 and subject_id=$2",
        [row.file_id, subject],
      );
      continue;
    }
    await pool.query(
      "delete from yancuo.object_deletions where file_id=$1 and subject_id=$2",
      [row.file_id, subject],
    );
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
      await lockSubjectQuota(client, subject);
      const existing = await client.query(
        "select repository_id,name,created_at,updated_at from yancuo.repositories where subject_id=$1 and name=$2",
        [subject, repositoryName],
      );
      if (existing.rowCount) {
        await client.query("commit");
        return { ...existing.rows[0], owner: subject, private: true };
      }
      const count = await client.query(
        "select count(*)::int as value from yancuo.repositories where subject_id=$1",
        [subject],
      );
      if (count.rows[0].value >= USER_REPOSITORIES) fail("已达到个人资料库数量上限", 409);
      const created = await client.query(
        "insert into yancuo.repositories(subject_id, owner, name) values($1,$1,$2) returning repository_id,name,created_at,updated_at",
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
      await client.query("begin");
      await requireWriteLock(client, repo.repository_id, payload);
      await client.query(
        "insert into yancuo.manifests(repository_id,document) values($1,$2::jsonb) on conflict(repository_id) do update set document=excluded.document,updated_at=now()",
        [repo.repository_id, JSON.stringify(payload.manifest)],
      );
      await client.query("commit");
      return { written: true };
    }
    if (name === "releases/list") {
      await cleanupPendingDeletions(subject);
      const releases = await client.query(
        "select tag,name,case when octet_length(body)<=$2 then body else '' end as body,created_at from yancuo.releases where repository_id=$1 order by created_at desc limit $3",
        [repo.repository_id, MAX_RELEASE_BODY_BYTES, RELEASE_LIST_LIMIT],
      );
      const tags = releases.rows.map((row) => row.tag);
      const assets = tags.length
        ? await client.query(
          "select release_tag,asset_name,byte_size from yancuo.release_assets where repository_id=$1 and release_tag=any($2::text[])",
          [repo.repository_id, tags],
        )
        : { rows: [] };
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
      const body = boundedText(payload.body, "发布说明", MAX_RELEASE_BODY_BYTES);
      await client.query("begin");
      await requireWriteLock(client, repo.repository_id, payload);
      const existing = await client.query(
        "select tag,name,body,created_at from yancuo.releases where repository_id=$1 and tag=$2",
        [repo.repository_id, tag],
      );
      if (existing.rowCount) {
        const row = existing.rows[0];
        if (row.name !== releaseName || row.body !== body) fail("发布标签已被不同内容占用", 409);
        await client.query("commit");
        return row;
      }
      const releaseCount = await client.query(
        "select count(*)::int as value from yancuo.releases where repository_id=$1",
        [repo.repository_id],
      );
      if (releaseCount.rows[0].value >= MAX_RELEASES_PER_REPOSITORY) {
        fail("资料库发布数量已达到上限", 409);
      }
      const inserted = await client.query(
        "insert into yancuo.releases(repository_id,tag,name,body) values($1,$2,$3,$4) returning tag,name,body,created_at",
        [repo.repository_id, tag, releaseName, body],
      );
      await client.query("commit");
      return inserted.rows[0];
    }
    if (name === "assets/upload-url") {
      await cleanupExpiredUploads(subject);
      if (!PUBLIC_URL.startsWith("https://")) fail("网关未配置 HTTPS 公网地址", 503);
      const tag = boundedName(payload.tag, "发布标签");
      const assetName = boundedName(payload.asset_name, "资源名称");
      const size = Number(payload.size);
      if (!Number.isSafeInteger(size) || size < 0 || size > MAX_ASSET_BYTES) fail("资源大小无效", 413);
      await client.query("begin");
      await lockSubjectQuota(client, subject);
      await requireWriteLock(client, repo.repository_id, payload);
      const release = await client.query(
        "select 1 from yancuo.releases where repository_id=$1 and tag=$2",
        [repo.repository_id, tag],
      );
      if (!release.rowCount) fail("发布不存在", 404);
      const assetCount = await client.query(
        "select ((select count(*) from yancuo.release_assets where repository_id=$1 and release_tag=$2) + (select count(*) from yancuo.upload_sessions where repository_id=$1 and release_tag=$2 and expires_at>=now()))::int as value",
        [repo.repository_id, tag],
      );
      if (assetCount.rows[0].value >= MAX_ASSETS_PER_RELEASE) {
        fail("发布附件数量已达到上限", 409);
      }
      const existingAsset = await client.query(
        "select 1 from yancuo.release_assets where repository_id=$1 and release_tag=$2 and asset_name=$3",
        [repo.repository_id, tag, assetName],
      );
      if (existingAsset.rowCount) fail("发布附件已经存在且不可替换", 409);
      const usage = await client.query(
        "select ((select coalesce(sum(a.byte_size),0) from yancuo.release_assets a join yancuo.repositories r on r.repository_id=a.repository_id where r.subject_id=$1) + (select coalesce(sum(u.expected_size),0) from yancuo.upload_sessions u where u.subject_id=$1 and u.expires_at>=now()))::bigint as bytes",
        [subject],
      );
      if (Number(usage.rows[0].bytes) + size > USER_STORAGE_BYTES) fail("已达到个人云存储额度", 409);
      const uploadId = cryptoRandomId();
      const uploadToken = newUploadToken();
      const storagePath = `yancuo/${subjectStorageKey(subject)}/${repo.repository_id}/uploads/${uploadId}/${assetName}`;
      await client.query(
        "insert into yancuo.upload_sessions(upload_id,subject_id,repository_id,release_tag,asset_name,storage_path,expected_size,token_hash,expires_at) values($1,$2,$3,$4,$5,$6,$7,$8,now()+interval '10 minutes')",
        [uploadId, subject, repo.repository_id, tag, assetName, storagePath, size, tokenHash(uploadToken)],
      );
      await client.query("commit");
      return {
        url: `${PUBLIC_URL}/uploads/${uploadId}?token=${encodeURIComponent(uploadToken)}`,
        headers: { "Content-Type": "application/octet-stream" },
        upload_id: uploadId,
      };
    }
    if (name === "assets/commit") {
      const uploadId = String(payload.upload_id || "");
      await client.query("begin");
      await requireWriteLock(client, repo.repository_id, payload);
      const claimed = await client.query(
        "delete from yancuo.upload_sessions where upload_id=$1 and subject_id=$2 and repository_id=$3 and uploaded_at is not null and expires_at>=now() returning *",
        [uploadId, subject, repo.repository_id],
      );
      const upload = claimed.rows[0];
      if (!upload || Number(upload.actual_size) !== Number(upload.expected_size)) {
        fail("上传尚未完成、已提交或大小不匹配", 409);
      }
      if (upload.release_tag !== payload.tag || upload.asset_name !== payload.asset_name) {
        fail("上传提交参数不匹配", 409);
      }
      await client.query(
        "insert into yancuo.release_assets(repository_id,release_tag,asset_name,storage_path,file_id,byte_size) values($1,$2,$3,$4,$5,$6)",
        [repo.repository_id, upload.release_tag, upload.asset_name, upload.storage_path, upload.file_id, upload.actual_size],
      );
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
      const leaseId = boundedName(payload.lease_id, "租约 ID");
      const locked = await client.query(
        "insert into yancuo.write_locks(repository_id,device_id,lease_id,expires_at) values($1,$2,$3,now()+interval '15 minutes') on conflict(repository_id) do update set device_id=excluded.device_id,lease_id=excluded.lease_id,expires_at=excluded.expires_at,updated_at=now() where yancuo.write_locks.expires_at<=now() or (yancuo.write_locks.device_id=excluded.device_id and yancuo.write_locks.lease_id=excluded.lease_id) returning device_id,expires_at",
        [repo.repository_id, deviceId, leaseId],
      );
      return { acquired: Boolean(locked.rowCount), expires_at: locked.rows[0]?.expires_at || null };
    }
    if (name === "locks/release") {
      const deviceId = boundedName(payload.device_id, "设备 ID");
      const leaseId = boundedName(payload.lease_id, "租约 ID");
      await client.query(
        "delete from yancuo.write_locks where repository_id=$1 and device_id=$2 and lease_id=$3",
        [repo.repository_id, deviceId, leaseId],
      );
      return { released: true };
    }
    if (name === "releases/delete") {
      const tag = boundedName(payload.tag, "发布标签");
      await client.query("begin");
      await requireWriteLock(client, repo.repository_id, payload);
      const files = await client.query(
        "select file_id from yancuo.release_assets where repository_id=$1 and release_tag=$2",
        [repo.repository_id, tag],
      );
      for (const row of files.rows) {
        if (row.file_id) {
          await client.query(
            "insert into yancuo.object_deletions(file_id,subject_id) values($1,$2) on conflict(file_id) do nothing",
            [row.file_id, subject],
          );
        }
      }
      await client.query(
        "delete from yancuo.releases where repository_id=$1 and tag=$2",
        [repo.repository_id, tag],
      );
      await client.query("commit");
      await cleanupPendingDeletions(subject);
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
  const found = await pool.query(
    "select * from yancuo.upload_sessions where upload_id=$1 and expires_at>=now() and uploaded_at is null",
    [uploadId],
  );
  const candidate = found.rows[0];
  if (!candidate || !safeTokenEqual(token, candidate.token_hash)) fail("上传凭据无效或已过期", 403);
  const claimed = await pool.query(
    "update yancuo.upload_sessions set claimed_at=now(),expires_at=now()+interval '1 hour' where upload_id=$1 and token_hash=$2 and expires_at>=now() and uploaded_at is null and claimed_at is null returning *",
    [uploadId, tokenHash(token)],
  );
  const row = claimed.rows[0];
  if (!row) fail("上传已在进行或凭据已使用", 409);
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
  let stored;
  try {
    req.pipe(counter);
    stored = await cloud.uploadFile({ cloudPath: row.storage_path, fileContent: counter });
    if (actual !== Number(row.expected_size)) {
      if (stored.fileID) await cloud.deleteFile({ fileList: [stored.fileID] });
      fail("上传内容大小与声明不一致", 409);
    }
    await pool.query(
      "update yancuo.upload_sessions set file_id=$2,actual_size=$3,uploaded_at=now(),claimed_at=null where upload_id=$1 and claimed_at is not null",
      [uploadId, stored.fileID, actual],
    );
  } catch (error) {
    await pool.query(
      "update yancuo.upload_sessions set claimed_at=null where upload_id=$1 and uploaded_at is null",
      [uploadId],
    ).catch(() => undefined);
    throw error;
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
  await pool.end();
});
