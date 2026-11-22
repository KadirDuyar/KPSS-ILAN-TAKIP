// Supabase: Project Settings -> API. "anon/publishable" anahtar herkese açık olacak şekilde tasarlıdır;
// veriyi koruyan şey RLS politikalarıdır. ASLA service_role anahtarını buraya koyma.
window.KPSS_CONFIG = {
  SUPABASE_URL: "https://prnyoenjsgmcnvxtrynh.supabase.co",        // örn. https://abcdxyz.supabase.co
  SUPABASE_ANON_KEY: "sb_publishable_vfcy53rbyn6lstXvGJA5Qw_ehSe4lA4",   // örn. eyJhbGciOi... veya sb_publishable_...
  ALLOW_SIGNUP: false      // 3-4 kişilik kullanım: kullanıcıları panelden ekle, kayıt kapalı kalsın
};
