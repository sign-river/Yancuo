-- CloudBase PostgreSQL RLS policies for the yancuo_gateway role.
-- Run this AFTER init.sql and after the gateway database role exists
-- (cloudbase/postgres/init.sql enables row level security with no policies).
-- The gateway sets transaction-local session variables before every data
-- query: yancuo.subject_id (authenticated actions) and yancuo.upload_id
-- (one-time PUT upload flow). Policies below expose only rows that match the
-- caller's identity context. No user credentials belong here.

-- subject-owned tables
drop policy if exists yancuo_subject_scope on yancuo.repositories;
create policy yancuo_subject_scope on yancuo.repositories
  for all to yancuo_gateway
  using (subject_id = current_setting('yancuo.subject_id', true))
  with check (subject_id = current_setting('yancuo.subject_id', true));

drop policy if exists yancuo_subject_scope on yancuo.rate_limits;
create policy yancuo_subject_scope on yancuo.rate_limits
  for all to yancuo_gateway
  using (subject_id = current_setting('yancuo.subject_id', true))
  with check (subject_id = current_setting('yancuo.subject_id', true));

drop policy if exists yancuo_subject_scope on yancuo.object_deletions;
create policy yancuo_subject_scope on yancuo.object_deletions
  for all to yancuo_gateway
  using (subject_id = current_setting('yancuo.subject_id', true))
  with check (subject_id = current_setting('yancuo.subject_id', true));

-- repository-scoped tables: the subject is resolved through repositories,
-- so a caller can only touch rows that belong to their own repositories.
drop policy if exists yancuo_subject_scope on yancuo.manifests;
create policy yancuo_subject_scope on yancuo.manifests
  for all to yancuo_gateway
  using (repository_id in (select repository_id from yancuo.repositories where subject_id = current_setting('yancuo.subject_id', true)))
  with check (repository_id in (select repository_id from yancuo.repositories where subject_id = current_setting('yancuo.subject_id', true)));

drop policy if exists yancuo_subject_scope on yancuo.releases;
create policy yancuo_subject_scope on yancuo.releases
  for all to yancuo_gateway
  using (repository_id in (select repository_id from yancuo.repositories where subject_id = current_setting('yancuo.subject_id', true)))
  with check (repository_id in (select repository_id from yancuo.repositories where subject_id = current_setting('yancuo.subject_id', true)));

drop policy if exists yancuo_subject_scope on yancuo.release_assets;
create policy yancuo_subject_scope on yancuo.release_assets
  for all to yancuo_gateway
  using (repository_id in (select repository_id from yancuo.repositories where subject_id = current_setting('yancuo.subject_id', true)))
  with check (repository_id in (select repository_id from yancuo.repositories where subject_id = current_setting('yancuo.subject_id', true)));

drop policy if exists yancuo_subject_scope on yancuo.write_locks;
create policy yancuo_subject_scope on yancuo.write_locks
  for all to yancuo_gateway
  using (repository_id in (select repository_id from yancuo.repositories where subject_id = current_setting('yancuo.subject_id', true)))
  with check (repository_id in (select repository_id from yancuo.repositories where subject_id = current_setting('yancuo.subject_id', true)));

-- upload_sessions: subject-scoped for authenticated flows
drop policy if exists yancuo_subject_scope on yancuo.upload_sessions;
create policy yancuo_subject_scope on yancuo.upload_sessions
  for all to yancuo_gateway
  using (subject_id = current_setting('yancuo.subject_id', true))
  with check (subject_id = current_setting('yancuo.subject_id', true));

-- upload_sessions: the one-time PUT upload flow has no identity token, so the
-- gateway sets yancuo.upload_id before SELECT/UPDATE on the exact session row.
drop policy if exists yancuo_upload_claim_read on yancuo.upload_sessions;
create policy yancuo_upload_claim_read on yancuo.upload_sessions
  for select to yancuo_gateway
  using (upload_id::text = current_setting('yancuo.upload_id', true));

drop policy if exists yancuo_upload_claim_write on yancuo.upload_sessions;
create policy yancuo_upload_claim_write on yancuo.upload_sessions
  for update to yancuo_gateway
  using (upload_id::text = current_setting('yancuo.upload_id', true))
  with check (upload_id::text = current_setting('yancuo.upload_id', true));
