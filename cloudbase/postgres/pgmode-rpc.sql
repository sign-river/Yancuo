-- CloudBase PG-mode RPC functions for yancuo-cloud-gateway (PostgREST).
-- All functions live in the public schema so PostgREST exposes them as
-- /v1/rdb/rest/rpc/<name>. They are SECURITY DEFINER and owned by the SQL
-- editor administrator (table owner), so they bypass RLS and can access the
-- yancuo schema. The gateway is the only caller and passes the validated
-- user subject explicitly (service_role API key is never exposed).
-- Each function returns the envelope {"ok":bool,"status":int,"error":text,"data":jsonb}.

-- health
CREATE OR REPLACE FUNCTION public.yancuo_health()
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('now', now()));
END $$;

-- rate limit upsert (atomic per subject)
CREATE OR REPLACE FUNCTION public.yancuo_rate_limit(p_subject text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_count integer;
BEGIN
  INSERT INTO yancuo.rate_limits(subject_id, window_start, request_count)
  VALUES (p_subject, date_trunc('minute', now()), 1)
  ON CONFLICT (subject_id) DO UPDATE SET
    window_start = CASE WHEN yancuo.rate_limits.window_start < date_trunc('minute', now())
                        THEN excluded.window_start ELSE yancuo.rate_limits.window_start END,
    request_count = CASE WHEN yancuo.rate_limits.window_start < date_trunc('minute', now())
                         THEN 1 ELSE yancuo.rate_limits.request_count + 1 END
  RETURNING request_count INTO v_count;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('request_count', v_count));
END $$;

-- resolve a repository by subject + name
CREATE OR REPLACE FUNCTION public.yancuo_repository(p_subject text, p_name text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_repo record;
BEGIN
  SELECT repository_id, name, created_at, updated_at INTO v_repo
  FROM yancuo.repositories WHERE subject_id = p_subject AND name = p_name;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 404, 'error', '资料库不存在');
  END IF;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object(
    'repository_id', v_repo.repository_id, 'name', v_repo.name,
    'created_at', v_repo.created_at, 'updated_at', v_repo.updated_at));
END $$;

-- repositories/list
CREATE OR REPLACE FUNCTION public.yancuo_repositories_list(p_subject text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_rows jsonb;
BEGIN
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'name', t.name, 'created_at', t.created_at, 'updated_at', t.updated_at)
      ORDER BY t.updated_at DESC), '[]'::jsonb) INTO v_rows
  FROM (SELECT name, created_at, updated_at FROM yancuo.repositories WHERE subject_id = p_subject) t;
  RETURN jsonb_build_object('ok', true, 'data', v_rows);
END $$;

-- repositories/create (idempotent; serialized per subject)
CREATE OR REPLACE FUNCTION public.yancuo_repositories_create(p_subject text, p_name text, p_max_repos integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_existing record;
        v_count integer;
        v_created record;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(p_subject, 0));
  SELECT repository_id, name, created_at, updated_at INTO v_existing
  FROM yancuo.repositories WHERE subject_id = p_subject AND name = p_name;
  IF FOUND THEN
    RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('row', jsonb_build_object(
      'repository_id', v_existing.repository_id, 'name', v_existing.name,
      'created_at', v_existing.created_at, 'updated_at', v_existing.updated_at)));
  END IF;
  SELECT count(*)::int INTO v_count FROM yancuo.repositories WHERE subject_id = p_subject;
  IF v_count >= p_max_repos THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '已达到个人资料库数量上限');
  END IF;
  INSERT INTO yancuo.repositories(subject_id, owner, name) VALUES (p_subject, p_subject, p_name)
  RETURNING repository_id, name, created_at, updated_at INTO v_created;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('row', jsonb_build_object(
    'repository_id', v_created.repository_id, 'name', v_created.name,
    'created_at', v_created.created_at, 'updated_at', v_created.updated_at)));
END $$;

-- manifest/read
CREATE OR REPLACE FUNCTION public.yancuo_manifest_read(p_subject text, p_name text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_repo uuid;
        v_document jsonb;
BEGIN
  SELECT repository_id INTO v_repo FROM yancuo.repositories WHERE subject_id = p_subject AND name = p_name;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 404, 'error', '资料库不存在');
  END IF;
  SELECT document INTO v_document FROM yancuo.manifests WHERE repository_id = v_repo;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('manifest', jsonb 'null'));
  END IF;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('manifest', v_document));
END $$;

-- manifest/write
CREATE OR REPLACE FUNCTION public.yancuo_manifest_write(p_subject text, p_name text, p_device_id text, p_lease_id text, p_document jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_repo uuid;
BEGIN
  SELECT repository_id INTO v_repo FROM yancuo.repositories WHERE subject_id = p_subject AND name = p_name;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 404, 'error', '资料库不存在');
  END IF;
  UPDATE yancuo.write_locks SET expires_at = now() + interval '15 minutes', updated_at = now()
  WHERE repository_id = v_repo AND device_id = p_device_id AND lease_id = p_lease_id AND expires_at > now();
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '主写入锁不存在或已经过期');
  END IF;
  INSERT INTO yancuo.manifests(repository_id, document) VALUES (v_repo, p_document)
  ON CONFLICT (repository_id) DO UPDATE SET document = excluded.document, updated_at = now();
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('written', true));
END $$;

-- releases/list (with assets)
CREATE OR REPLACE FUNCTION public.yancuo_releases_list(p_subject text, p_name text, p_max_body_bytes integer, p_limit integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_repo uuid;
        v_rows jsonb;
BEGIN
  SELECT repository_id INTO v_repo FROM yancuo.repositories WHERE subject_id = p_subject AND name = p_name;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 404, 'error', '资料库不存在');
  END IF;
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'tag', r.tag, 'name', r.name,
      'body', CASE WHEN octet_length(r.body) <= p_max_body_bytes THEN r.body ELSE '' END,
      'created_at', r.created_at,
      'assets', COALESCE((
        SELECT jsonb_agg(jsonb_build_object('name', a.asset_name, 'size', a.byte_size))
        FROM yancuo.release_assets a
        WHERE a.repository_id = r.repository_id AND a.release_tag = r.tag
      ), '[]'::jsonb)
    ) ORDER BY r.created_at DESC), '[]'::jsonb) INTO v_rows
  FROM yancuo.releases r WHERE r.repository_id = v_repo LIMIT p_limit;
  RETURN jsonb_build_object('ok', true, 'data', v_rows);
END $$;

-- releases/create
CREATE OR REPLACE FUNCTION public.yancuo_releases_create(p_subject text, p_name text, p_device_id text, p_lease_id text, p_tag text, p_release_name text, p_body text, p_max_releases integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_repo uuid;
        v_existing record;
        v_count integer;
        v_ins record;
BEGIN
  SELECT repository_id INTO v_repo FROM yancuo.repositories WHERE subject_id = p_subject AND name = p_name;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 404, 'error', '资料库不存在');
  END IF;
  UPDATE yancuo.write_locks SET expires_at = now() + interval '15 minutes', updated_at = now()
  WHERE repository_id = v_repo AND device_id = p_device_id AND lease_id = p_lease_id AND expires_at > now();
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '主写入锁不存在或已经过期');
  END IF;
  SELECT tag, name, body, created_at INTO v_existing
  FROM yancuo.releases WHERE repository_id = v_repo AND tag = p_tag;
  IF FOUND THEN
    IF v_existing.name <> p_release_name OR v_existing.body <> p_body THEN
      RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '发布标签已被不同内容占用');
    END IF;
    RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object(
      'tag', v_existing.tag, 'name', v_existing.name, 'body', v_existing.body, 'created_at', v_existing.created_at));
  END IF;
  SELECT count(*)::int INTO v_count FROM yancuo.releases WHERE repository_id = v_repo;
  IF v_count >= p_max_releases THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '资料库发布数量已达到上限');
  END IF;
  INSERT INTO yancuo.releases(repository_id, tag, name, body) VALUES (v_repo, p_tag, p_release_name, p_body)
  RETURNING tag, name, body, created_at INTO v_ins;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object(
    'tag', v_ins.tag, 'name', v_ins.name, 'body', v_ins.body, 'created_at', v_ins.created_at));
END $$;

-- assets/upload-url
CREATE OR REPLACE FUNCTION public.yancuo_upload_url(p_subject text, p_name text, p_device_id text, p_lease_id text, p_tag text, p_asset_name text, p_size bigint, p_upload_id text, p_token_hash text, p_storage_path text, p_max_assets integer, p_user_storage_bytes bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_repo uuid;
        v_asset_count integer;
        v_usage bigint;
BEGIN
  SELECT repository_id INTO v_repo FROM yancuo.repositories WHERE subject_id = p_subject AND name = p_name;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 404, 'error', '资料库不存在');
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_subject, 0));
  UPDATE yancuo.write_locks SET expires_at = now() + interval '15 minutes', updated_at = now()
  WHERE repository_id = v_repo AND device_id = p_device_id AND lease_id = p_lease_id AND expires_at > now();
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '主写入锁不存在或已经过期');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM yancuo.releases WHERE repository_id = v_repo AND tag = p_tag) THEN
    RETURN jsonb_build_object('ok', false, 'status', 404, 'error', '发布不存在');
  END IF;
  SELECT ((SELECT count(*) FROM yancuo.release_assets WHERE repository_id = v_repo AND release_tag = p_tag)
        + (SELECT count(*) FROM yancuo.upload_sessions WHERE repository_id = v_repo AND release_tag = p_tag AND expires_at >= now()))::int
  INTO v_asset_count;
  IF v_asset_count >= p_max_assets THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '发布附件数量已达到上限');
  END IF;
  IF EXISTS (SELECT 1 FROM yancuo.release_assets WHERE repository_id = v_repo AND release_tag = p_tag AND asset_name = p_asset_name) THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '发布附件已经存在且不可替换');
  END IF;
  SELECT ((SELECT coalesce(sum(a.byte_size), 0) FROM yancuo.release_assets a
             JOIN yancuo.repositories r ON r.repository_id = a.repository_id WHERE r.subject_id = p_subject)
        + (SELECT coalesce(sum(u.expected_size), 0) FROM yancuo.upload_sessions u
             WHERE u.subject_id = p_subject AND u.expires_at >= now()))::bigint
  INTO v_usage;
  IF v_usage + p_size > p_user_storage_bytes THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '已达到个人云存储额度');
  END IF;
  INSERT INTO yancuo.upload_sessions(upload_id, subject_id, repository_id, release_tag, asset_name, storage_path, expected_size, token_hash, expires_at)
  VALUES (p_upload_id::uuid, p_subject, v_repo, p_tag, p_asset_name, p_storage_path, p_size, p_token_hash, now() + interval '10 minutes');
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('upload_id', p_upload_id));
END $$;

-- assets/commit
CREATE OR REPLACE FUNCTION public.yancuo_assets_commit(p_subject text, p_name text, p_device_id text, p_lease_id text, p_tag text, p_asset_name text, p_upload_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_repo uuid;
        v_up record;
BEGIN
  SELECT repository_id INTO v_repo FROM yancuo.repositories WHERE subject_id = p_subject AND name = p_name;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 404, 'error', '资料库不存在');
  END IF;
  UPDATE yancuo.write_locks SET expires_at = now() + interval '15 minutes', updated_at = now()
  WHERE repository_id = v_repo AND device_id = p_device_id AND lease_id = p_lease_id AND expires_at > now();
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '主写入锁不存在或已经过期');
  END IF;
  DELETE FROM yancuo.upload_sessions
  WHERE upload_id = p_upload_id::uuid AND subject_id = p_subject AND repository_id = v_repo
    AND uploaded_at IS NOT NULL AND expires_at >= now()
  RETURNING * INTO v_up;
  IF NOT FOUND OR v_up.actual_size IS DISTINCT FROM v_up.expected_size THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '上传尚未完成、已提交或大小不匹配');
  END IF;
  IF v_up.release_tag <> p_tag OR v_up.asset_name <> p_asset_name THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '上传提交参数不匹配');
  END IF;
  INSERT INTO yancuo.release_assets(repository_id, release_tag, asset_name, storage_path, file_id, byte_size)
  VALUES (v_repo, v_up.release_tag, v_up.asset_name, v_up.storage_path, v_up.file_id, v_up.actual_size);
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('name', v_up.asset_name, 'size', v_up.actual_size));
END $$;

-- assets/download-url (resolves file id; gateway issues temp url)
CREATE OR REPLACE FUNCTION public.yancuo_asset_file(p_subject text, p_name text, p_tag text, p_asset_name text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_repo uuid;
        v_file_id text;
BEGIN
  SELECT repository_id INTO v_repo FROM yancuo.repositories WHERE subject_id = p_subject AND name = p_name;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 404, 'error', '资料库不存在');
  END IF;
  SELECT file_id INTO v_file_id FROM yancuo.release_assets
  WHERE repository_id = v_repo AND release_tag = p_tag AND asset_name = p_asset_name;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 404, 'error', '资源不存在');
  END IF;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('file_id', v_file_id));
END $$;

-- locks/acquire
CREATE OR REPLACE FUNCTION public.yancuo_locks_acquire(p_subject text, p_name text, p_device_id text, p_lease_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_repo uuid;
        v_dev text;
        v_exp timestamptz;
BEGIN
  SELECT repository_id INTO v_repo FROM yancuo.repositories WHERE subject_id = p_subject AND name = p_name;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 404, 'error', '资料库不存在');
  END IF;
  INSERT INTO yancuo.write_locks(repository_id, device_id, lease_id, expires_at)
  VALUES (v_repo, p_device_id, p_lease_id, now() + interval '15 minutes')
  ON CONFLICT (repository_id) DO UPDATE SET
    device_id = excluded.device_id, lease_id = excluded.lease_id,
    expires_at = excluded.expires_at, updated_at = now()
  WHERE yancuo.write_locks.expires_at <= now()
     OR (yancuo.write_locks.device_id = excluded.device_id AND yancuo.write_locks.lease_id = excluded.lease_id)
  RETURNING device_id, expires_at INTO v_dev, v_exp;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('acquired', FOUND, 'expires_at', v_exp));
END $$;

-- locks/release
CREATE OR REPLACE FUNCTION public.yancuo_locks_release(p_subject text, p_name text, p_device_id text, p_lease_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_repo uuid;
BEGIN
  SELECT repository_id INTO v_repo FROM yancuo.repositories WHERE subject_id = p_subject AND name = p_name;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 404, 'error', '资料库不存在');
  END IF;
  DELETE FROM yancuo.write_locks WHERE repository_id = v_repo AND device_id = p_device_id AND lease_id = p_lease_id;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('released', true));
END $$;

-- releases/delete
CREATE OR REPLACE FUNCTION public.yancuo_releases_delete(p_subject text, p_name text, p_device_id text, p_lease_id text, p_tag text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_repo uuid;
        v_dummy integer;
        v_up record;
        v_files jsonb;
BEGIN
  SELECT repository_id INTO v_repo FROM yancuo.repositories WHERE subject_id = p_subject AND name = p_name;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 404, 'error', '资料库不存在');
  END IF;
  UPDATE yancuo.write_locks SET expires_at = now() + interval '15 minutes', updated_at = now()
  WHERE repository_id = v_repo AND device_id = p_device_id AND lease_id = p_lease_id AND expires_at > now();
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '主写入锁不存在或已经过期');
  END IF;
  SELECT 1 INTO v_dummy FROM yancuo.releases WHERE repository_id = v_repo AND tag = p_tag FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('deleted', true));
  END IF;
  SELECT file_id, claimed_at INTO v_up FROM yancuo.upload_sessions
  WHERE repository_id = v_repo AND release_tag = p_tag FOR UPDATE;
  IF FOUND AND v_up.claimed_at IS NOT NULL THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '发布仍有正在执行的附件上传');
  END IF;
  SELECT COALESCE(jsonb_agg(file_id), '[]'::jsonb) INTO v_files
  FROM (
    SELECT file_id FROM yancuo.release_assets WHERE repository_id = v_repo AND release_tag = p_tag
    UNION
    SELECT file_id FROM yancuo.upload_sessions WHERE repository_id = v_repo AND release_tag = p_tag
  ) f WHERE file_id IS NOT NULL;
  INSERT INTO yancuo.object_deletions(file_id, subject_id)
  SELECT value::text, p_subject FROM jsonb_array_elements_text(v_files)
  ON CONFLICT (file_id) DO NOTHING;
  DELETE FROM yancuo.releases WHERE repository_id = v_repo AND tag = p_tag;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('deleted', true));
END $$;

-- expired upload sessions list (for cleanup)
CREATE OR REPLACE FUNCTION public.yancuo_cleanup_list(p_subject text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_rows jsonb;
BEGIN
  SELECT COALESCE(jsonb_agg(jsonb_build_object('upload_id', upload_id, 'file_id', file_id) ORDER BY expires_at), '[]'::jsonb) INTO v_rows
  FROM yancuo.upload_sessions
  WHERE subject_id = p_subject AND expires_at < now()
    AND (claimed_at IS NULL OR claimed_at < now() - interval '1 hour')
  LIMIT 100;
  RETURN jsonb_build_object('ok', true, 'data', v_rows);
END $$;

-- delete one expired upload session (guarded)
CREATE OR REPLACE FUNCTION public.yancuo_cleanup_delete(p_subject text, p_upload_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_deleted integer;
BEGIN
  DELETE FROM yancuo.upload_sessions
  WHERE upload_id = p_upload_id::uuid AND subject_id = p_subject AND expires_at < now()
    AND (claimed_at IS NULL OR claimed_at < now() - interval '1 hour');
  GET DIAGNOSTICS v_deleted = ROW_COUNT;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('deleted', v_deleted));
END $$;

-- pending object deletions list
CREATE OR REPLACE FUNCTION public.yancuo_deletions_list(p_subject text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_rows jsonb;
BEGIN
  SELECT COALESCE(jsonb_agg(jsonb_build_object('file_id', file_id) ORDER BY queued_at), '[]'::jsonb) INTO v_rows
  FROM (SELECT file_id, queued_at FROM yancuo.object_deletions WHERE subject_id = p_subject ORDER BY queued_at LIMIT 100) t;
  RETURN jsonb_build_object('ok', true, 'data', v_rows);
END $$;

-- mark object deletion done
CREATE OR REPLACE FUNCTION public.yancuo_deletions_done(p_subject text, p_file_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  DELETE FROM yancuo.object_deletions WHERE file_id = p_file_id AND subject_id = p_subject;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('deleted', true));
END $$;

-- retry object deletion later
CREATE OR REPLACE FUNCTION public.yancuo_deletions_retry(p_subject text, p_file_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  UPDATE yancuo.object_deletions SET attempts = attempts + 1, last_attempt_at = now()
  WHERE file_id = p_file_id AND subject_id = p_subject;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('retried', true));
END $$;

-- upload PUT flow: find session by upload id
CREATE OR REPLACE FUNCTION public.yancuo_upload_find(p_upload_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_row record;
BEGIN
  SELECT * INTO v_row FROM yancuo.upload_sessions WHERE upload_id = p_upload_id::uuid AND expires_at >= now();
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 403, 'error', '上传凭证无效或已过期');
  END IF;
  RETURN jsonb_build_object('ok', true, 'data', to_jsonb(v_row));
END $$;

-- upload PUT flow: claim session
CREATE OR REPLACE FUNCTION public.yancuo_upload_claim(p_upload_id text, p_token_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_row record;
BEGIN
  UPDATE yancuo.upload_sessions
  SET claimed_at = now(), expires_at = now() + interval '1 hour'
  WHERE upload_id = p_upload_id::uuid AND token_hash = p_token_hash AND expires_at >= now()
    AND uploaded_at IS NULL AND claimed_at IS NULL
  RETURNING * INTO v_row;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '上传正在进行或凭证已使用');
  END IF;
  RETURN jsonb_build_object('ok', true, 'data', to_jsonb(v_row));
END $$;

-- upload PUT flow: mark uploaded
CREATE OR REPLACE FUNCTION public.yancuo_upload_complete(p_upload_id text, p_file_id text, p_actual_size bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_deleted integer;
BEGIN
  UPDATE yancuo.upload_sessions SET file_id = p_file_id, actual_size = p_actual_size, uploaded_at = now(), claimed_at = null
  WHERE upload_id = p_upload_id::uuid AND claimed_at IS NOT NULL;
  GET DIAGNOSTICS v_deleted = ROW_COUNT;
  IF v_deleted <> 1 THEN
    RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '上传会话已过期或被回收');
  END IF;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('uploaded', true));
END $$;

-- upload PUT flow: recover after storage failure
CREATE OR REPLACE FUNCTION public.yancuo_upload_recover(p_upload_id text, p_file_id text, p_actual_size bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_deleted integer;
BEGIN
  UPDATE yancuo.upload_sessions
  SET file_id = p_file_id, actual_size = p_actual_size, uploaded_at = coalesce(uploaded_at, now()), claimed_at = null
  WHERE upload_id = p_upload_id::uuid;
  GET DIAGNOSTICS v_deleted = ROW_COUNT;
  IF v_deleted = 1 THEN
    RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('uploaded', true));
  END IF;
  RETURN jsonb_build_object('ok', false, 'status', 409, 'error', '上传会话已过期或被回收');
END $$;

-- upload PUT flow: release claim on failure
CREATE OR REPLACE FUNCTION public.yancuo_upload_unclaim(p_upload_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  UPDATE yancuo.upload_sessions SET claimed_at = null WHERE upload_id = p_upload_id::uuid AND uploaded_at IS NULL;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('released', true));
END $$;

-- grants: service_role (bypass RLS) + authenticated + anon where relevant
GRANT EXECUTE ON FUNCTION public.yancuo_health() TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_rate_limit(text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_repository(text, text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_repositories_list(text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_repositories_create(text, text, integer) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_manifest_read(text, text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_manifest_write(text, text, text, text, jsonb) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_releases_list(text, text, integer, integer) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_releases_create(text, text, text, text, text, text, text, integer) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_upload_url(text, text, text, text, text, text, bigint, text, text, text, integer, bigint) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_assets_commit(text, text, text, text, text, text, text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_asset_file(text, text, text, text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_locks_acquire(text, text, text, text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_locks_release(text, text, text, text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_releases_delete(text, text, text, text, text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_cleanup_list(text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_cleanup_delete(text, text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_deletions_list(text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_deletions_done(text, text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_deletions_retry(text, text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_upload_find(text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_upload_claim(text, text) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_upload_complete(text, text, bigint) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_upload_recover(text, text, bigint) TO service_role, authenticated, anon;
GRANT EXECUTE ON FUNCTION public.yancuo_upload_unclaim(text) TO service_role, authenticated, anon;
-- quota/usage
CREATE OR REPLACE FUNCTION public.yancuo_storage_usage(p_subject text, p_user_storage_bytes bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_used bigint;
BEGIN
  SELECT ((SELECT coalesce(sum(a.byte_size), 0) FROM yancuo.release_assets a
             JOIN yancuo.repositories r ON r.repository_id = a.repository_id WHERE r.subject_id = p_subject)
        + (SELECT coalesce(sum(u.expected_size), 0) FROM yancuo.upload_sessions u
             WHERE u.subject_id = p_subject AND u.expires_at >= now()))::bigint
  INTO v_used;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object(
    'used_bytes', v_used,
    'quota_bytes', p_user_storage_bytes));
END $$;

GRANT EXECUTE ON FUNCTION public.yancuo_storage_usage(text, bigint) TO service_role, authenticated, anon;
