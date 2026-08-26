-- =====================================================================
-- SkyPulse — Supabase schema for authentication
-- Run this once in your project: Supabase Dashboard -> SQL Editor -> Run.
--
-- Safe to re-run: every statement is idempotent, and nothing here drops or
-- rewrites data. It extends the profiles table you already created rather
-- than replacing it.
--
-- Supabase Auth (auth.users) owns identity: passwords, email confirmation,
-- tokens, password resets. public.profiles owns everything the ground station
-- needs on top of that — role, status, contact details and console settings.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. The profiles table
-- ---------------------------------------------------------------------
-- Already created by you; repeated here so a fresh project can run this
-- file on its own.

create table if not exists public.profiles (
    id         uuid primary key references auth.users(id) on delete cascade,
    email      text,
    full_name  text,
    role       text default 'user',
    created_at timestamptz default now()
);


-- ---------------------------------------------------------------------
-- 2. The extra detail the console needs
-- ---------------------------------------------------------------------
-- All nullable or defaulted, so existing rows stay valid and the insert
-- trigger keeps working untouched.

alter table public.profiles
    -- Who the operator is.
    add column if not exists organization          text,
    add column if not exists job_title             text,
    add column if not exists phone                 text,
    add column if not exists avatar_url            text,
    add column if not exists country               text,
    add column if not exists timezone              text    default 'UTC',
    add column if not exists locale                text    default 'en',

    -- Whether the account may be used at all, independent of its role.
    add column if not exists status                text    default 'active',

    -- Console settings that follow the account rather than the browser.
    add column if not exists theme                 text    default 'system',
    add column if not exists default_view          text    default '/dashboard',
    add column if not exists email_alerts          boolean default true,
    add column if not exists alert_on_node_offline boolean default true,
    add column if not exists alert_on_abnormality  boolean default true,
    -- Node ids this operator watches; empty means the whole fleet.
    add column if not exists watched_nodes         text[]  default '{}',

    add column if not exists updated_at            timestamptz default now();


-- ---------------------------------------------------------------------
-- 3. Constrain role and status to known values
-- ---------------------------------------------------------------------
-- Without these, a typo in a role becomes a silent permission failure that
-- only shows up as a 403 nobody can explain.

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'profiles_role_check'
    ) then
        alter table public.profiles
            add constraint profiles_role_check
            check (role in ('user', 'operator', 'admin'));
    end if;

    if not exists (
        select 1 from pg_constraint where conname = 'profiles_status_check'
    ) then
        alter table public.profiles
            add constraint profiles_status_check
            check (status in ('active', 'suspended', 'disabled'));
    end if;
end $$;


-- ---------------------------------------------------------------------
-- 4. Row Level Security
-- ---------------------------------------------------------------------
-- The backend talks to Postgres with the secret (service_role) key, which
-- bypasses RLS — these policies are what protect the table from anything
-- else that ever gets a user token, so they still matter.

alter table public.profiles enable row level security;

drop policy if exists "Users can view their own profile" on public.profiles;
create policy "Users can view their own profile"
    on public.profiles
    for select
    to authenticated
    using (auth.uid() = id);

drop policy if exists "Users can update their own profile" on public.profiles;
create policy "Users can update their own profile"
    on public.profiles
    for update
    to authenticated
    using (auth.uid() = id)
    -- Without this, a user with a raw token could hand themselves the admin
    -- role by updating their own row: the USING clause alone checks which row
    -- they may touch, not what they may write into it.
    with check (
        auth.uid() = id
        and role   = (select p.role   from public.profiles p where p.id = auth.uid())
        and status = (select p.status from public.profiles p where p.id = auth.uid())
    );


-- ---------------------------------------------------------------------
-- 5. Create the profile automatically on registration
-- ---------------------------------------------------------------------
--   user registers -> Supabase inserts into auth.users -> this trigger fires
--   -> the matching profiles row appears.
--
-- security definer so it can write to profiles regardless of RLS, and
-- search_path is pinned because a definer function without it is a
-- privilege-escalation hole.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    is_first_user boolean;
begin
    -- Somebody has to be able to administer a fresh project, so the first
    -- account to register becomes the admin. Every later one is a plain user.
    select not exists (select 1 from public.profiles) into is_first_user;

    insert into public.profiles (id, email, full_name, role)
    values (
        new.id,
        new.email,
        -- The backend sends full_name in the signup payload, which Supabase
        -- stores here. coalesce keeps sign-ups that omit it working.
        coalesce(new.raw_user_meta_data ->> 'full_name', ''),
        case when is_first_user then 'admin' else 'user' end
    )
    on conflict (id) do nothing;

    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row
    execute procedure public.handle_new_user();


-- ---------------------------------------------------------------------
-- 6. Keep updated_at honest
-- ---------------------------------------------------------------------

create or replace function public.touch_profile_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists profiles_set_updated_at on public.profiles;
create trigger profiles_set_updated_at
    before update on public.profiles
    for each row
    execute procedure public.touch_profile_updated_at();


-- ---------------------------------------------------------------------
-- 7. Backfill
-- ---------------------------------------------------------------------
-- Anyone who registered before the trigger existed has no profile row, and
-- would otherwise be able to sign in but never load.

insert into public.profiles (id, email, full_name, role)
select
    u.id,
    u.email,
    coalesce(u.raw_user_meta_data ->> 'full_name', ''),
    'user'
from auth.users u
where not exists (select 1 from public.profiles p where p.id = u.id);

-- Rows that the *old* trigger created carry only id and email, because it
-- never read raw_user_meta_data. Copy across what the sign-up actually sent.
update public.profiles p
set full_name = u.raw_user_meta_data ->> 'full_name'
from auth.users u
where u.id = p.id
  and coalesce(p.full_name, '') = ''
  and coalesce(u.raw_user_meta_data ->> 'full_name', '') <> '';

update public.profiles p
set organization = u.raw_user_meta_data ->> 'organization'
from auth.users u
where u.id = p.id
  and p.organization is null
  and coalesce(u.raw_user_meta_data ->> 'organization', '') <> '';

-- If that backfill was the first thing to populate the table, nobody is an
-- admin yet. Promote the oldest account.
update public.profiles
set role = 'admin'
where id = (select id from public.profiles order by created_at asc limit 1)
  and not exists (select 1 from public.profiles where role = 'admin');
