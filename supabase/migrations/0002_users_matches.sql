-- Kullanıcı profilleri, eşleşmeler, bildirim kuyruğu ve RLS. Tekrar çalıştırılması güvenlidir.
-- Supabase dışında (düz PostgreSQL / test) auth şeması yoksa FK ve politikalar atlanır.

create table if not exists public.profiles (
  user_id uuid primary key,            -- auth.users.id
  email text,
  data jsonb not null default '{}'::jsonb,   -- profil v2 (personal, education, exams, ...)
  updated_at timestamptz not null default now()
);

create table if not exists public.matches (
  user_id uuid not null,
  guid text not null references public.ilan(guid) on delete cascade,
  level text not null check (level in ('ok','check','no')),
  reasons jsonb not null default '[]'::jsonb,     -- en iyi düzeydeki kadroların gerekçeleri
  positions jsonb not null default '[]'::jsonb,   -- gösterilecek kadro satırları
  fp text,                                        -- eşleşmenin hesaplandığı ilan parmak izi
  computed_at timestamptz not null default now(),
  primary key (user_id, guid)
);
create index if not exists matches_user_level on public.matches (user_id, level);

-- Gelecekte push/e-posta için olay kuyruğu (şimdilik yalnızca yazılır).
create table if not exists public.notification_outbox (
  id bigserial primary key,
  user_id uuid not null,
  guid text not null,
  event text not null,                 -- NEW_MATCH | REQUIREMENT_CHANGED | DEADLINE_REMINDER
  payload jsonb,
  created_at timestamptz not null default now(),
  sent_at timestamptz
);
create unique index if not exists outbox_dedupe on public.notification_outbox (user_id, guid, event, (payload->>'fp'));

alter table public.profiles enable row level security;
alter table public.matches enable row level security;
alter table public.notification_outbox enable row level security;   -- politika yok: yalnız servis rolü

-- Kullanıcının kendi eşleşmeleri + ilan alanları. (Görünüm sahibinin haklarıyla çalışır; süzme auth.uid() ile.)
do $$ begin
  if to_regprocedure('auth.uid()') is not null then
    execute $v$
      create or replace view public.my_matches as
        select m.level, m.reasons, m.positions, m.computed_at,
               i.guid, i.url, i.institution, i.title, i.ilan_type, i.application_start,
               i.application_deadline, i.external_url, i.source, i.source_refs, i.last_seen
        from public.matches m join public.ilan i on i.guid = m.guid
        where m.user_id = auth.uid() and i.active = 1
    $v$;
  end if;
end $$;

-- Politikalar ve izinler (Supabase rolleri varsa).
do $$ begin
  if to_regprocedure('auth.uid()') is not null then
    drop policy if exists profiles_select_own on public.profiles;
    drop policy if exists profiles_insert_own on public.profiles;
    drop policy if exists profiles_update_own on public.profiles;
    drop policy if exists matches_select_own on public.matches;
    create policy profiles_select_own on public.profiles for select to authenticated using (user_id = auth.uid());
    create policy profiles_insert_own on public.profiles for insert to authenticated with check (user_id = auth.uid());
    create policy profiles_update_own on public.profiles for update to authenticated
      using (user_id = auth.uid()) with check (user_id = auth.uid());
    create policy matches_select_own on public.matches for select to authenticated using (user_id = auth.uid());

    revoke all on public.profiles, public.matches, public.notification_outbox, public.my_matches from anon;
    grant select, insert, update on public.profiles to authenticated;
    grant select on public.matches to authenticated;
    grant select on public.my_matches to authenticated;
    if not exists (select 1 from pg_constraint where conname = 'profiles_user_fk') then
      alter table public.profiles add constraint profiles_user_fk
        foreign key (user_id) references auth.users(id) on delete cascade;
    end if;
    if not exists (select 1 from pg_constraint where conname = 'matches_user_fk') then
      alter table public.matches add constraint matches_user_fk
        foreign key (user_id) references auth.users(id) on delete cascade;
    end if;
  end if;
end $$;
