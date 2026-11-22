-- KPSS ilan takip: ilk şema (PostgreSQL / Supabase). Tekrar çalıştırılması güvenlidir.
create table if not exists public.ilan (
  guid text primary key,
  source text not null default 'kariyer_kapisi',
  url text,                        -- kullanıcıya gösterilen resmî ilan sayfası (doğrulama linki)
  source_refs jsonb,               -- verinin çekildiği adresler: [{kind,url,payload,fetched_at}]
  institution text, unit text, title text, ilan_type text,
  application_start timestamp,     -- kaynaktaki yerel saat (saat dilimsiz)
  application_deadline timestamp,
  external_url text,
  list_fp text, content_hash text,
  detail_json jsonb,               -- ayrıştırılmış ilan + kadrolar
  raw_json jsonb,                  -- ham API cevapları (yeniden ayrıştırma için)
  active integer not null default 1,
  first_seen timestamptz, last_seen timestamptz
);
create index if not exists ilan_active_deadline on public.ilan (active, application_deadline);

create table if not exists public.runs (
  ts timestamptz, listed integer, new integer, changed integer, errors integer);
create table if not exists public.notified (
  profile text, guid text, fp text, ts text, primary key (profile, guid, fp));

-- API güvenliği: tablolar kapalı; mobil/web yalnızca ham veri içermeyen görünümü okur.
alter table public.ilan enable row level security;
alter table public.runs enable row level security;
alter table public.notified enable row level security;

create or replace view public.ilan_public as
  select guid, source, url, institution, unit, title, ilan_type, application_start,
         application_deadline, external_url, detail_json, source_refs, first_seen, last_seen
  from public.ilan where active = 1;

do $$ begin
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on public.ilan_public from anon;
    grant select on public.ilan_public to authenticated;
  end if;
end $$;
