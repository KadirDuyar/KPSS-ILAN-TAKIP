// Supabase: Project Settings -> API. "anon/publishable" anahtar herkese açık olacak şekilde tasarlıdır;
// veriyi koruyan şey RLS politikalarıdır. ASLA service_role anahtarını buraya koyma.
window.KPSS_CONFIG = {
  SUPABASE_URL: "",        // örn. https://abcdxyz.supabase.co
  SUPABASE_ANON_KEY: "",   // örn. eyJhbGciOi... veya sb_publishable_...
  ALLOW_SIGNUP: false      // 3-4 kişilik kullanım: kullanıcıları panelden ekle, kayıt kapalı kalsın
};
