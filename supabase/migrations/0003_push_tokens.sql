-- FCM cihaz anahtarları (push token). Tekrar çalıştırılması güvenlidir.
-- Mobil uygulama token'ı yalnızca register_push_token() ile kaydeder / unregister_push_token() ile siler.
-- Sunucu (DATABASE_URL) token'ları okur, geçersiz olanları disabled_at ile kapatır.

create table if not exists public.push_tokens (
  id bigserial primary key,
  user_id uuid not null,
  token text not null unique check (char_length(token) between 20 and 4096),
  platform text not null default 'android' check (platform in ('android', 'ios', 'web')),
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),   -- uygulama her açılışta yeniler
  disabled_at timestamptz,                            -- FCM "UNREGISTERED" derse doldurulur
  last_error text
);
create index if not exists push_tokens_user on public.push_tokens (user_id) where disabled_at is null;

alter table public.push_tokens enable row level security;

do $$ begin
  if to_regprocedure('auth.uid()') is not null then
    -- Kullanıcı yalnızca kendi token'larını görebilir; yazma doğrudan değil, aşağıdaki fonksiyonlarla.
    drop policy if exists push_tokens_select_own on public.push_tokens;
    create policy push_tokens_select_own on public.push_tokens for select to authenticated
      using (user_id = auth.uid());
    revoke all on public.push_tokens from anon, authenticated;
    grant select on public.push_tokens to authenticated;
    if not exists (select 1 from pg_constraint where conname = 'push_tokens_user_fk') then
      alter table public.push_tokens add constraint push_tokens_user_fk
        foreign key (user_id) references auth.users(id) on delete cascade;
    end if;

    -- Aynı cihaz başka hesapla açılırsa token o hesaba taşınır (bir token tek kullanıcıya bildirim alır).
    -- Kullanıcı başına en çok 10 etkin cihaz: fazlası en eski kullanılandan kapatılır.
    execute $f$
      create or replace function public.register_push_token(p_token text, p_platform text default 'android')
      returns void language plpgsql security definer set search_path = public as $b$
      begin
        if auth.uid() is null then raise exception 'oturum gerekli'; end if;
        if p_token is null or char_length(p_token) not between 20 and 4096 then raise exception 'geçersiz token'; end if;
        if p_platform not in ('android', 'ios', 'web') then raise exception 'geçersiz platform'; end if;
        insert into public.push_tokens (user_id, token, platform)
          values (auth.uid(), p_token, p_platform)
          on conflict (token) do update set user_id = auth.uid(), platform = excluded.platform,
            last_seen_at = now(), disabled_at = null, last_error = null;
        update public.push_tokens set disabled_at = now(), last_error = 'cihaz sınırı'
          where id in (select id from public.push_tokens where user_id = auth.uid() and disabled_at is null
                       order by last_seen_at desc offset 10);
      end $b$
    $f$;
    execute $f$
      create or replace function public.unregister_push_token(p_token text)
      returns void language plpgsql security definer set search_path = public as $b$
      begin
        delete from public.push_tokens where token = p_token and user_id = auth.uid();
      end $b$
    $f$;
    revoke all on function public.register_push_token(text, text) from public, anon;
    revoke all on function public.unregister_push_token(text) from public, anon;
    grant execute on function public.register_push_token(text, text) to authenticated;
    grant execute on function public.unregister_push_token(text) to authenticated;
  end if;
end $$;
