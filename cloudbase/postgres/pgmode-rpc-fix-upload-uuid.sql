-- Yancuo PG-mode RPC fix: upload_id text -> uuid cast (2026-08-04)
-- Re-creates 8 functions; grants are preserved by CREATE OR REPLACE.

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

CREATE OR REPLACE FUNCTION public.yancuo_upload_unclaim(p_upload_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  UPDATE yancuo.upload_sessions SET claimed_at = null WHERE upload_id = p_upload_id::uuid AND uploaded_at IS NULL;
  RETURN jsonb_build_object('ok', true, 'data', jsonb_build_object('released', true));
END $$;

NOTIFY pgrst, 'reload schema';

-- verify: count functions and upload_id usage
SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace AND proname LIKE 'yancuo_%' ORDER BY proname;