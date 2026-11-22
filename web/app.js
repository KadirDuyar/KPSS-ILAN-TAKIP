"use strict";
/* KPSS İlan Takip — statik arayüz. Veri: Supabase (RLS ile korunur). Tüm metinler textContent ile yazılır. */
const CFG = window.KPSS_CONFIG || {};
const $app = document.getElementById("app");
const LEVEL = { ok: "Uygun", check: "Kontrol et", no: "Uygun değil" };
const ICON = { ok: "✓ ", check: "! ", no: "✕ " };
const IL = ["ADANA","ADIYAMAN","AFYONKARAHİSAR","AĞRI","AKSARAY","AMASYA","ANKARA","ANTALYA","ARDAHAN","ARTVİN","AYDIN","BALIKESİR","BARTIN","BATMAN","BAYBURT","BİLECİK","BİNGÖL","BİTLİS","BOLU","BURDUR","BURSA","ÇANAKKALE","ÇANKIRI","ÇORUM","DENİZLİ","DİYARBAKIR","DÜZCE","EDİRNE","ELAZIĞ","ERZİNCAN","ERZURUM","ESKİŞEHİR","GAZİANTEP","GİRESUN","GÜMÜŞHANE","HAKKARİ","HATAY","IĞDIR","ISPARTA","İSTANBUL","İZMİR","KAHRAMANMARAŞ","KARABÜK","KARAMAN","KARS","KASTAMONU","KAYSERİ","KIRIKKALE","KIRKLARELİ","KIRŞEHİR","KİLİS","KOCAELİ","KONYA","KÜTAHYA","MALATYA","MANİSA","MARDİN","MERSİN","MUĞLA","MUŞ","NEVŞEHİR","NİĞDE","ORDU","OSMANİYE","RİZE","SAKARYA","SAMSUN","SİİRT","SİNOP","SİVAS","ŞANLIURFA","ŞIRNAK","TEKİRDAĞ","TOKAT","TRABZON","TUNCELİ","UŞAK","VAN","YALOVA","YOZGAT","ZONGULDAK"];
const KPSS_TYPES = ["P1","P2","P3","P4","P5","P6","P7","P8","P9","P10","P11","P12","P13","P14","P15","P16","P17","P18","P19","P20","P21","P22","P23","P24","P93","P94","P121","P122","P123"];
const LICENSES = ["A","A1","A2","B","B1","BE","C","C1","CE","D","D1","DE","F","G","M"];

/* ---------- küçük yardımcılar ---------- */
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
const get = (o, path) => path.split(".").reduce((a, k) => (a == null ? undefined : a[k]), o);
function set(o, path, val) {
  const ks = path.split("."); let a = o;
  ks.slice(0, -1).forEach(k => (a = a[k] = a[k] && typeof a[k] === "object" ? a[k] : {}));
  a[ks.at(-1)] = val;
}
const fmtDate = s => (s ? new Date(s).toLocaleString("tr-TR", { dateStyle: "medium", timeStyle: "short" }) : "süre belirtilmemiş");
function daysLeft(s) { return s ? Math.ceil((new Date(s) - new Date()) / 864e5) : null; }
function safeUrl(u) { try { const x = new URL(u); return /^https?:$/.test(x.protocol) ? x.href : null; } catch { return null; } }
function trSort(a, b) { return a.localeCompare(b, "tr"); }

/* ---------- durum ---------- */
let sb = null, session = null, view = "matches";
const S = { matches: null, all: null, profile: null, types: [], filter: "okcheck", q: "", lastSeen: null };

function boot() {
  if (!CFG.SUPABASE_URL || !CFG.SUPABASE_ANON_KEY || !window.supabase) {
    $app.replaceChildren(h("div", { class: "wrap" }, h("div", { class: "notice" },
      h("h1", {}, "Kurulum gerekli"),
      h("p", {}, "web/config.js içine Supabase proje adresini (SUPABASE_URL) ve anon/publishable anahtarını yaz, sonra sayfayı yenile."),
      h("p", { class: "meta" }, "Adımlar README.md → “Supabase + Vercel kurulumu” bölümünde."))));
    return;
  }
  sb = window.supabase.createClient(CFG.SUPABASE_URL, CFG.SUPABASE_ANON_KEY);
  sb.auth.onAuthStateChange((_e, s) => { const had = !!session; session = s; if (!!s !== had) route(); });
  sb.auth.getSession().then(({ data }) => { session = data.session; route(); });
}

function route() {
  if (!session) { S.matches = S.all = S.profile = null; S.q = ""; view = "matches"; return authView(); }
  shell();
}

/* ---------- giriş ---------- */
function authView() {
  const msg = h("p", { class: "meta", role: "status" });
  const email = h("input", { type: "email", autocomplete: "username", required: true, id: "em" });
  const pass = h("input", { type: "password", autocomplete: "current-password", required: true, id: "pw", minlength: 8 });
  const go = async (signup) => {
    msg.className = "meta"; msg.textContent = "…";
    const fn = signup ? sb.auth.signUp.bind(sb.auth) : sb.auth.signInWithPassword.bind(sb.auth);
    const { error } = await fn({ email: email.value.trim(), password: pass.value });
    if (error) { msg.className = "err"; msg.textContent = signup ? "Kayıt başarısız: " + error.message : "Giriş başarısız: e-posta veya şifre hatalı."; }
    else if (signup) { msg.className = "okmsg"; msg.textContent = "Kayıt oluşturuldu. E-posta doğrulaması açıksa gelen bağlantıya tıkla."; }
  };
  $app.replaceChildren(h("div", { class: "wrap" }, h("form", { class: "auth card", onsubmit: e => { e.preventDefault(); go(false); } },
    h("h1", {}, "KPSS İlan Takip"),
    h("p", { class: "meta" }, "Kamu personel ilanları, profiline göre süzülmüş."),
    h("label", { for: "em" }, "E-posta"), email,
    h("label", { for: "pw" }, "Şifre"), pass,
    h("div", { class: "row" }, h("button", { class: "primary", type: "submit" }, "Giriş yap"),
      CFG.ALLOW_SIGNUP ? h("button", { class: "plain", type: "button", onclick: () => go(true) }, "Kayıt ol") : null),
    msg)));
}

/* ---------- kabuk ---------- */
function shell() {
  const tabs = [["matches", "Eşleşmelerim"], ["all", "Tüm ilanlar"], ["profile", "Profilim"]];
  const nav = h("nav", { "aria-label": "Bölümler" }, tabs.map(([k, t]) =>
    h("button", { type: "button", "aria-current": String(view === k), onclick: () => { view = k; shell(); } }, t)));
  const main = h("main", { class: "wrap", id: "main" });
  $app.replaceChildren(
    h("header", { class: "top" }, h("div", { class: "wrap" }, h("h1", {}, "KPSS İlan Takip"), nav, h("span", { class: "sp" }),
      h("span", { class: "who" }, session.user.email),
      h("button", { class: "plain", type: "button", onclick: () => sb.auth.signOut() }, "Çıkış"))),
    main);
  ({ matches: matchesView, all: allView, profile: profileView })[view](main);
}

async function loadAll() {
  const q = (t, sel) => sb.from(t).select(sel).order("application_deadline", { ascending: true, nullsFirst: false }).range(0, 999);
  const [m, a] = await Promise.all([
    q("my_matches", "*"),
    q("ilan_public", "guid,url,institution,title,ilan_type,application_deadline,external_url,source,source_refs,detail_json,last_seen")]);
  if (m.error || a.error) throw new Error((m.error || a.error).message);
  S.matches = m.data; S.all = a.data;
  S.types = [...new Set(a.data.map(r => r.ilan_type).filter(Boolean))].sort(trSort);
  S.lastSeen = a.data.reduce((x, r) => (r.last_seen && r.last_seen > (x || "") ? r.last_seen : x), null);
}
const showError = (main, e) => main.replaceChildren(h("div", { class: "notice err", role: "alert" }, "Veri alınamadı: " + e.message));

/* Kadro kutuları: gerekçeler tüm kadrolarda aynıysa tek kez göster */
function posLine(p) {
  return [h("b", {}, p.unvan || "Kadro"), " ", h("span", { class: "meta" }, [(p.types || []).join("/"), p.min ? "≥" + p.min : null, p.il].filter(Boolean).join(" · "))];
}
function posBlocks(ps) {
  const list = ps.slice(0, 4), key = p => JSON.stringify(p.reasons || []);
  const reasons = r => (r && r.length ? h("ul", {}, r.map(x => h("li", {}, x))) : null);
  if (list.length > 1 && list.every(p => key(p) === key(list[0])))
    return h("div", { class: "pos" }, list.map(p => h("div", {}, posLine(p))), reasons(list[0].reasons));
  return list.map(p => h("div", { class: "pos" }, h("div", {}, posLine(p)), reasons(p.reasons)));
}

/* ---------- ilan kartı ---------- */
function card(r, level, positions) {
  const dl = daysLeft(r.application_deadline);
  const link = safeUrl(r.url), ext = safeUrl(r.external_url);
  const refs = Array.isArray(r.source_refs) ? r.source_refs : [];
  return h("article", { class: "card" },
    h("div", {}, level ? h("span", { class: "badge " + level }, ICON[level] + LEVEL[level]) : null,
      h("span", { class: "meta" }, `Son: ${fmtDate(r.application_deadline)}`, dl != null ? ` (${dl >= 0 ? dl + " gün" : "bitti"})` : "")),
    h("h3", {}, r.title || "(başlıksız)"),
    h("div", { class: "meta" }, [r.institution, r.ilan_type].filter(Boolean).join(" · ")),
    posBlocks(positions || []),
    h("div", { class: "links" },
      link ? h("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, "Resmî ilanı aç ↗") : null,
      ext ? h("a", { href: ext, target: "_blank", rel: "noopener noreferrer" }, "Başvuru bağlantısı ↗") : null),
    h("details", { class: "src" }, h("summary", {}, `Kaynak: ${r.source === "kariyer_kapisi" ? "kariyerkapisi.gov.tr" : r.source || "?"} · son kontrol ${fmtDate(r.last_seen)}`),
      h("p", {}, "Resmî sayfada şartları kendin doğrula; bu panel yardımcıdır, karar vermez."),
      refs.map(x => h("div", {}, x.kind ? x.kind + ": " : "", h("code", {}, x.url || "")))));
}

function filterBar(rerender, withLevels) {
  const chips = withLevels ? [["ok", "Uygun"], ["okcheck", "Uygun + Kontrol"], ["all", "Tümü"]] : [];
  return h("div", { class: "bar" },
    h("input", { class: "search", type: "search", placeholder: "Ara: kurum, unvan, başlık…", "aria-label": "Ara", value: S.q,
      oninput: e => { S.q = e.target.value; rerender(); } }),
    chips.map(([k, t]) => h("button", { class: "chip", type: "button", "aria-pressed": String(S.filter === k), onclick: () => { S.filter = k; shell(); } }, t)));
}
const hay = r => (`${r.title} ${r.institution} ${r.ilan_type} ` + JSON.stringify(((r.detail_json && r.detail_json.positions) || r.positions || []).map(p => p.unvan))).toLocaleLowerCase("tr");

/* ---------- eşleşmeler ---------- */
async function matchesView(main) {
  main.append(h("p", { class: "empty" }, "Yükleniyor…"));
  try { if (!S.matches) await loadAll(); if (!S.profile) await loadProfile(); } catch (e) { return showError(main, e); }
  const list = h("div", {});
  const render = () => {
    const q = S.q.toLocaleLowerCase("tr");
    const rows = S.matches.filter(r => (S.filter === "all" || r.level === "ok" || (S.filter === "okcheck" && r.level === "check")) &&
      (!q || hay(r).includes(q))).sort((a, b) => ({ ok: 0, check: 1, no: 2 }[a.level] - { ok: 0, check: 1, no: 2 }[b.level]));
    list.replaceChildren(...(rows.length ? rows.map(r => card(r, r.level, r.positions))
      : [h("div", { class: "empty" }, S.matches.length ? "Bu süzgeçle eşleşme yok." : "Henüz eşleşme yok. Profilini doldur; eşleşmeler her taramada (≈30 dk) hesaplanır.")]));
  };
  const empty = !S.profile || !Object.keys(S.profile).length;
  main.replaceChildren(...[
    empty ? h("div", { class: "notice" }, "Profilin boş. Bilmediğin alanlar “Kontrol et” olarak görünür; doldurdukça sonuçlar netleşir. ",
      h("button", { class: "plain", onclick: () => { view = "profile"; shell(); } }, "Profili doldur →")) : null,
    h("p", { class: "meta" }, `Son tarama: ${fmtDate(S.lastSeen)} · ${S.matches.filter(r => r.level === "ok").length} uygun, ${S.matches.filter(r => r.level === "check").length} kontrol`),
    filterBar(render, true), list].filter(Boolean));
  render();
}

async function allView(main) {
  main.append(h("p", { class: "empty" }, "Yükleniyor…"));
  try { if (!S.all) await loadAll(); } catch (e) { return showError(main, e); }
  const lv = Object.fromEntries((S.matches || []).map(m => [m.guid, m.level]));
  const list = h("div", {});
  const render = () => {
    const q = S.q.toLocaleLowerCase("tr");
    const rows = S.all.filter(r => !q || hay(r).includes(q));
    list.replaceChildren(...(rows.length ? rows.slice(0, 300).map(r => card(r, lv[r.guid], null)) : [h("div", { class: "empty" }, "Sonuç yok.")]));
  };
  main.replaceChildren(h("p", { class: "meta" }, `${S.all.length} açık ilan`), filterBar(render, false), list);
  render();
}

/* ---------- profil ---------- */
async function loadProfile() {
  const { data, error } = await sb.from("profiles").select("data").eq("user_id", session.user.id).maybeSingle();
  if (error) throw new Error(error.message);
  S.profile = (data && data.data) || {};
}

const TRI = [["", "Belirtmedim"], ["true", "Evet"], ["false", "Hayır"]];
const triVal = v => (v === true ? "true" : v === false ? "false" : "");
const triOut = v => (v === "true" ? true : v === "false" ? false : null);

async function profileView(main) {
  main.append(h("p", { class: "empty" }, "Yükleniyor…"));
  try { if (!S.all) await loadAll(); await loadProfile(); } catch (e) { return showError(main, e); }
  const P = S.profile, ctl = []; // ctl: {path, read()}
  const reg = (path, el, read) => { ctl.push({ path, read }); return el; };
  const text = (path, label, o = {}) => { const el = h("input", { type: o.type || "text", value: get(P, path) ?? "", list: o.list, placeholder: o.ph, min: o.min, step: o.step }); return h("label", {}, label, reg(path, el, () => (el.value === "" ? null : o.type === "number" ? Number(el.value) : el.value.trim()))); };
  const sel = (path, label, opts) => { const el = h("select", {}, opts.map(([v, t]) => h("option", { value: v, selected: String(get(P, path) ?? "") === v }, t))); return h("label", {}, label, reg(path, el, () => (el.value === "" ? null : el.value))); };
  const tri = (path, label) => { const el = h("select", {}, TRI.map(([v, t]) => h("option", { value: v, selected: triVal(get(P, path)) === v }, t))); return h("label", {}, label, reg(path, el, () => triOut(el.value))); };
  const csv = (path, label, ph, list) => { const el = h("textarea", { placeholder: ph, list }, (get(P, path) || []).join(", ")); return h("label", {}, label, reg(path, el, () => el.value.split(",").map(s => s.trim()).filter(Boolean))); };

  /* öğrenim tekrarlayıcı */
  const eduBox = h("div", {});
  const eduRow = (e = {}) => { const r = h("div", { class: "row" },
    h("select", { "aria-label": "Düzey" }, ["Lise", "Önlisans", "Lisans", "Yüksek lisans", "Doktora"].map(x => h("option", { selected: e.level === x }, x))),
    h("select", { "aria-label": "Durum" }, [["graduated", "Mezun"], ["student", "Öğrenci"]].map(([v, t]) => h("option", { value: v, selected: (e.status || "graduated") === v }, t))),
    h("input", { placeholder: "Bölüm / program (ör. Bilgisayar Mühendisliği)", value: e.program || "", "aria-label": "Bölüm" }),
    h("button", { class: "plain", type: "button", onclick: () => r.remove() }, "Sil")); eduBox.append(r); };
  (P.education && P.education.length ? P.education : [{}]).forEach(eduRow);

  /* KPSS puanları tekrarlayıcı */
  const kBox = h("div", {});
  const kRow = (t = "", v = "") => { const r = h("div", { class: "row" },
    h("input", { list: "kt", placeholder: "Tür (P3)", value: t, "aria-label": "Puan türü", style: "max-width:120px" }),
    h("input", { type: "number", step: "0.001", min: "0", max: "100", placeholder: "Puan", value: v, "aria-label": "Puan", style: "max-width:140px" }),
    h("button", { class: "plain", type: "button", onclick: () => r.remove() }, "Sil")); kBox.append(r); };
  Object.entries(get(P, "exams.kpss.scores") || {}).forEach(([t, v]) => kRow(t, v ?? ""));
  if (!kBox.children.length) kRow("P3", "");
  const kComplete = h("input", { type: "checkbox", checked: get(P, "exams.kpss.complete") === true });

  /* ehliyet */
  const lic = get(P, "credentials.driving_license");
  const licMode = h("select", {}, [["", "Belirtmedim"], ["none", "Ehliyetim yok"], ["has", "Var"]].map(([v, t]) =>
    h("option", { value: v, selected: v === (lic == null ? "" : lic.length ? "has" : "none") }, t)));
  const licBoxes = LICENSES.map(c => h("label", {}, h("input", { type: "checkbox", value: c, checked: !!(lic && lic.includes(c)) }), c));

  /* özel nitelikli veri */
  const consent = h("input", { type: "checkbox", checked: get(P, "consent.special_categories") === true, id: "cons" });
  const special = h("div", { class: "grid" },
    tri("social.disability.has", "Engel durumu (yüzde 40+ rapor)"), text("social.disability.rate", "Engel oranı (%)", { type: "number", min: 0 }),
    tri("social.former_convict", "Eski hükümlü ilanları için uygunluk"));
  special.hidden = !consent.checked; consent.addEventListener("change", () => (special.hidden = !consent.checked));

  /* tercihler */
  const types = h("div", { class: "checks" }, S.types.map(t => h("label", {}, h("input", { type: "checkbox", value: t, checked: (get(P, "preferences.ilan_types") || []).includes(t) }), t)));
  const notify = h("input", { type: "checkbox", checked: get(P, "preferences.notify_check") !== false });

  /* bildirim (ntfy) */
  const NTFY = "https://ntfy.sh", TOPIC_OK = /^[A-Za-z0-9_-]{20,64}$/;
  const topic = h("input", { value: get(P, "notify.ntfy_topic") || "", placeholder: "en az 20 karakter: harf, rakam, - _", "aria-label": "ntfy konu adı", autocomplete: "off", spellcheck: "false" });
  const notifyOn = h("input", { type: "checkbox", checked: get(P, "notify.enabled") !== false });
  const nStatus = h("span", { role: "status", class: "meta" });
  const genTopic = () => { const b = new Uint8Array(18); crypto.getRandomValues(b); topic.value = "kpss-" + [...b].map(x => x.toString(36).padStart(2, "0")).join("").slice(0, 28); nStatus.textContent = "Yeni konu oluşturuldu. ntfy uygulamasında bu konuya abone ol, sonra Kaydet’e bas."; };
  const testTopic = async () => {
    const t = topic.value.trim();
    if (!TOPIC_OK.test(t)) { nStatus.className = "err"; nStatus.textContent = "Konu adı en az 20 karakter olmalı (harf, rakam, - _)."; return; }
    nStatus.className = "meta"; nStatus.textContent = "Gönderiliyor…";
    try {
      const r = await fetch(NTFY, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ topic: t, title: "KPSS İlan Takip", message: "Deneme bildirimi: bildirimler bu konuya gelecek.", tags: ["bell"] }) });
      if (!r.ok) throw new Error("HTTP " + r.status);
      nStatus.className = "okmsg"; nStatus.textContent = "Gönderildi. Telefonuna geldiyse hazır; Kaydet’e basmayı unutma.";
    } catch (e) { nStatus.className = "err"; nStatus.textContent = "Gönderilemedi: " + e.message; }
  };

  const status = h("span", { role: "status", class: "meta" });
  const saveBtn = h("button", { class: "primary", type: "submit" }, "Kaydet");

  const form = h("form", { class: "form", onsubmit: async e => {
    e.preventDefault(); saveBtn.disabled = true; status.className = "meta"; status.textContent = "Kaydediliyor…";
    const out = JSON.parse(JSON.stringify(P));
    ctl.forEach(c => set(out, c.path, c.read()));
    out.education = [...eduBox.children].map(r => { const [l, s, p] = r.querySelectorAll("select,input"); return { level: l.value, status: s.value, program: p.value.trim() || null }; });
    const sc = {}; [...kBox.children].forEach(r => { const [t, v] = r.querySelectorAll("input"); const k = t.value.trim().toUpperCase(); if (k) sc[k] = v.value === "" ? null : Number(v.value); });
    set(out, "exams.kpss.scores", sc);
    set(out, "exams.kpss.complete", kComplete.checked);
    const chosen = licBoxes.map(l => l.firstChild).filter(i => i.checked).map(i => i.value);
    set(out, "credentials.driving_license", licMode.value === "" ? null : licMode.value === "none" ? [] : chosen);
    set(out, "consent.special_categories", consent.checked);
    if (!consent.checked) { set(out, "social.disability", { has: null, rate: null }); set(out, "social.former_convict", null); }
    set(out, "preferences.ilan_types", [...types.querySelectorAll("input:checked")].map(i => i.value));
    set(out, "preferences.notify_check", notify.checked);
    const tv = topic.value.trim();
    if (tv && !TOPIC_OK.test(tv)) { saveBtn.disabled = false; status.className = "err"; status.textContent = "Bildirim konu adı en az 20 karakter olmalı (harf, rakam, - _)."; return; }
    set(out, "notify.ntfy_topic", tv || null);
    set(out, "notify.enabled", notifyOn.checked);
    if (get(out, "personal.gender") === "K") set(out, "personal.military", "not_applicable");
    const { error } = await sb.from("profiles").upsert({ user_id: session.user.id, email: session.user.email, data: out, updated_at: new Date().toISOString() });
    saveBtn.disabled = false;
    if (error) { status.className = "err"; status.textContent = "Kaydedilemedi: " + error.message; return; }
    S.profile = out; status.className = "okmsg"; status.textContent = "Kaydedildi. Eşleşmeler bir sonraki taramada (en geç ≈30 dk) güncellenir.";
  } },
    h("p", { class: "hint" }, "Bilmediğin ya da paylaşmak istemediğin alanı “Belirtmedim” bırak: ilan o şartı arıyorsa “Kontrol et” görürsün, asla yanlışlıkla elenmezsin."),
    h("fieldset", {}, h("legend", {}, "Kişisel"), h("div", { class: "grid" },
      text("personal.birth_date", "Doğum tarihi", { type: "date" }),
      sel("personal.gender", "Cinsiyet", [["", "Belirtmedim"], ["E", "Erkek"], ["K", "Kadın"]]),
      sel("personal.military", "Askerlik", [["", "Belirtmedim"], ["done", "Yaptım"], ["exempt", "Muafım"], ["postponed", "Erteledim (tecil)"], ["not_done", "Yapmadım"]]),
      text("personal.residence_province", "İkamet ili", { list: "ill" }))),
    h("fieldset", {}, h("legend", {}, "Öğrenim"), eduBox, h("button", { class: "plain", type: "button", onclick: () => eduRow() }, "+ Öğrenim ekle")),
    h("fieldset", {}, h("legend", {}, "Sınavlar"),
      h("div", { class: "grid" }, text("exams.kpss.year", "KPSS yılı", { type: "number", min: 2000 }), text("exams.ales.score", "ALES puanı", { type: "number", step: "0.001" }),
        text("exams.language.YDS", "YDS", { type: "number", step: "0.001" }), text("exams.language.YOKDIL", "YÖKDİL", { type: "number", step: "0.001" })),
      h("p", { class: "meta", style: "margin:14px 0 0" }, "KPSS puanları"), kBox, h("button", { class: "plain", type: "button", onclick: () => kRow() }, "+ Puan ekle"),
      h("label", { class: "checks", style: "display:flex;gap:8px;align-items:center;color:var(--ink);margin-top:10px" }, kComplete,
        "Sahip olduğum bütün puan türlerini girdim (listede olmayan tür istenirse “Uygun değil” say)")),
    h("fieldset", {}, h("legend", {}, "Öğrencilik ve çalışma"), h("div", { class: "grid" },
      tri("student.formal_enrolled", "Örgün öğrenci kaydı (aktif/pasif)"), tri("student.open_distance_enrolled", "Açık/uzaktan öğretim kaydı"),
      tri("work.retired_pension", "Emekli aylığı alıyor muyum"), tri("work.insured_now", "Şu an sigortalı çalışıyor muyum"),
      text("work.experience_years", "Deneyim (yıl)", { type: "number", min: 0, step: "0.5" }), text("work.last_4b_termination", "Son 4/B sözleşme feshi tarihi", { type: "date" }))),
    h("fieldset", {}, h("legend", {}, "Belgeler"),
      h("div", { class: "grid" }, h("label", {}, "Sürücü belgesi", licMode)), h("div", { class: "checks" }, licBoxes),
      csv("credentials.certificates", "Sertifika / belge / diploma (virgülle)", "ör. Klinik Psikolog, CISSP, Sınıf öğretmenliği formasyonu")),
    h("fieldset", {}, h("legend", {}, "Özel nitelikli veriler (isteğe bağlı)"),
      h("p", { class: "hint" }, "Engellilik ve hükümlülük durumu KVKK’da özel nitelikli kişisel veridir. Yalnızca bu bilgiyi arayan ilanları (ör. Engelli İşçi) eşleştirmek için, sadece senin hesabında saklanır; açık rıza vermezsen hiç kullanılmaz."),
      h("label", { class: "checks", style: "display:flex;gap:8px;align-items:center;color:var(--ink)" }, consent, "Bu bilgilerin eşleştirmede kullanılmasına açık rıza veriyorum"), special),
    h("fieldset", {}, h("legend", {}, "Tercihler"),
      csv("preferences.provinces", "İller (boş = hepsi)", "ANKARA, İSTANBUL, İZMİR", "ill"),
      h("p", { class: "meta", style: "margin:14px 0 0" }, "İlan türleri (hiçbiri seçili değilse hepsi)"), types,
      csv("preferences.keywords_include", "Unvanda geçmesini istediğim kelimeler", "bilişim, bilgisayar, yazılım, siber"),
      csv("preferences.keywords_exclude", "Unvanda görmek istemediğim kelimeler", "hemşire, avukat"),
      h("label", { class: "checks", style: "display:flex;gap:8px;align-items:center;color:var(--ink)" }, notify, "“Kontrol et” sonuçlarını da göster")),
    h("fieldset", {}, h("legend", {}, "Bildirimler (ntfy)"),
      h("p", { class: "hint" }, "Telefonuna ücretsiz ntfy uygulamasını kur, “Rastgele oluştur” ile bir konu adı al ve uygulamada o konuya abone ol. Konu adını bilen herkes bildirimleri görebilir: kimseyle paylaşma. Bildirimlerde yalnızca ilan adı, karar ve resmî bağlantı bulunur."),
      h("label", {}, "Konu adı", topic),
      h("div", { class: "row" },
        h("button", { class: "plain", type: "button", onclick: genTopic }, "Rastgele oluştur"),
        h("button", { class: "plain", type: "button", onclick: testTopic }, "Deneme bildirimi gönder")),
      h("label", { class: "checks", style: "display:flex;gap:8px;align-items:center;color:var(--ink)" }, notifyOn, "Yeni uygun ilanlar için bildirim gönder"),
      nStatus),
    h("div", { class: "savebar" }, saveBtn, status),
    h("datalist", { id: "ill" }, IL.map(x => h("option", { value: x }))), h("datalist", { id: "kt" }, KPSS_TYPES.map(x => h("option", { value: x }))));
  main.replaceChildren(form);
}

boot();
