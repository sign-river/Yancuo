-- CloudBase PostgreSQL schema for yancuo-cloud-gateway.
-- Run this in the CloudBase SQL editor with a server-side administrator role.
-- No user credentials, environment IDs, or application tokens belong here.

create schema if not exists yancuo;

create table if not exists yancuo.repositories (
    subject_id text not null,
    owner text not null,
    name text not null,
    repository_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint repositories_subject_name_key unique (subject_id, name)
);

alter table yancuo.repositories add column if not exists subject_id text;
update yancuo.repositories set subject_id = owner where subject_id is null;
alter table yancuo.repositories alter column subject_id set not null;
do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'repositories_subject_name_key'
          and conrelid = 'yancuo.repositories'::regclass
    ) then
        alter table yancuo.repositories
            add constraint repositories_subject_name_key unique (subject_id, name);
    end if;
end $$;

create table if not exists yancuo.manifests (
    repository_id uuid primary key references yancuo.repositories(repository_id) on delete cascade,
    document jsonb not null,
    updated_at timestamptz not null default now()
);

create table if not exists yancuo.releases (
    repository_id uuid not null references yancuo.repositories(repository_id) on delete cascade,
    tag text not null,
    name text not null,
    body text not null default '',
    created_at timestamptz not null default now(),
    primary key (repository_id, tag)
);

create table if not exists yancuo.release_assets (
    repository_id uuid not null,
    release_tag text not null,
    asset_name text not null,
    storage_path text not null,
    file_id text not null,
    byte_size bigint not null check (byte_size >= 0),
    committed_at timestamptz not null default now(),
    primary key (repository_id, release_tag, asset_name),
    foreign key (repository_id, release_tag)
        references yancuo.releases(repository_id, tag) on delete cascade,
    unique (storage_path)
);

alter table yancuo.release_assets add column if not exists file_id text;
update yancuo.release_assets set file_id = storage_path where file_id is null;
alter table yancuo.release_assets alter column file_id set not null;

create table if not exists yancuo.upload_sessions (
    upload_id uuid primary key,
    subject_id text not null,
    repository_id uuid not null references yancuo.repositories(repository_id) on delete cascade,
    release_tag text not null,
    asset_name text not null,
    storage_path text not null unique,
    file_id text,
    expected_size bigint not null check (expected_size >= 0),
    actual_size bigint,
    token_hash text not null,
    expires_at timestamptz not null,
    claimed_at timestamptz,
    uploaded_at timestamptz,
    created_at timestamptz not null default now(),
    foreign key (repository_id, release_tag)
        references yancuo.releases(repository_id, tag) on delete cascade
);

alter table yancuo.upload_sessions add column if not exists claimed_at timestamptz;

create table if not exists yancuo.write_locks (
    repository_id uuid primary key references yancuo.repositories(repository_id) on delete cascade,
    device_id text not null,
    expires_at timestamptz not null,
    updated_at timestamptz not null default now()
);

create table if not exists yancuo.rate_limits (
    subject_id text primary key,
    window_start timestamptz not null,
    request_count integer not null check (request_count >= 0)
);

create table if not exists yancuo.object_deletions (
    file_id text primary key,
    subject_id text not null,
    queued_at timestamptz not null default now(),
    attempts integer not null default 0 check (attempts >= 0),
    last_attempt_at timestamptz
);

create index if not exists releases_repository_created_idx
    on yancuo.releases (repository_id, created_at desc);
create index if not exists write_locks_expiry_idx
    on yancuo.write_locks (expires_at);
create index if not exists repositories_subject_updated_idx
    on yancuo.repositories (subject_id, updated_at desc);
create index if not exists upload_sessions_subject_expiry_idx
    on yancuo.upload_sessions (subject_id, expires_at);
create index if not exists object_deletions_subject_queued_idx
    on yancuo.object_deletions (subject_id, queued_at);

-- The gateway may acquire a 15 minute lock only when it is expired or already
-- held by this device. The conditional conflict clause is the atomic decision.
-- Bind $1 repository_id and $2 device_id in the gateway parameterized query.
-- insert into yancuo.write_locks (repository_id, device_id, expires_at)
-- values ($1, $2, now() + interval '15 minutes')
-- on conflict (repository_id) do update
-- set device_id = excluded.device_id, expires_at = excluded.expires_at, updated_at = now()
-- where yancuo.write_locks.expires_at <= now()
--    or yancuo.write_locks.device_id = excluded.device_id
-- returning device_id, expires_at;

alter table yancuo.repositories enable row level security;
alter table yancuo.manifests enable row level security;
alter table yancuo.releases enable row level security;
alter table yancuo.release_assets enable row level security;
alter table yancuo.write_locks enable row level security;
alter table yancuo.upload_sessions enable row level security;
alter table yancuo.rate_limits enable row level security;
alter table yancuo.object_deletions enable row level security;

-- Deliberately no broad RLS policy: only the gateway's server-side role should
-- access these tables. Add narrowly scoped policies after its DB role is known.
