# النشر على Cloudflare + Supabase

دليل عملي لنشر MS-Factory أونلاين: الكود على GitHub، قاعدة البيانات على Supabase، والتشغيل على Cloudflare Containers. النتيجة رابط واحد يفتح من الموبايل.

> **قبل أي شيء:** Cloudflare Containers تحتاج خطة **Workers Paid** بـ 5 دولار شهرياً. الخطة المجانية لا تشغّل Containers إطلاقاً. لو مش عايز تدفع، استخدم خطة بديلة في آخر الملف.

---

## 1. الصورة الكبيرة

```
موبايل  →  Worker (Cloudflare)  →  Container (تطبيق FastAPI)  →  Supabase Postgres
```

- **Worker**: يستقبل الطلب، يشغّل الكونتينر لو نايم، يمرر الطلب له.
- **Container**: نفس صورة Docker عندك — مفيش تعديل في التطبيق.
- **Supabase**: بتخزن البيانات فقط.

الريبو فيه ملفين جاهزين: `cloudflare/worker.js` و`cloudflare/wrangler.jsonc`، وسكربت بدء `scripts/start.sh` بيشغّل الميجريشنز قبل السيرفر.

---

## 2. تجهيز Supabase

1. اعمل مشروع جديد على [supabase.com](https://supabase.com).
2. من **Project Settings → Database → Connection string** اختار **Session pooler** وانسخ الرابط.

   لازم يكون بالشكل ده:

   ```
   postgresql+psycopg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```

   **مهم:** لاحظ `postgresql+psycopg` مش `postgresql`. التطبيق بيستخدم psycopg 3، والرابط من غير `+psycopg` هيفشل.

3. **المنفذ 5432 مش 6543.** لو استخدمت 6543 (transaction pooler) التطبيق هيشتغل، بس الميجريشنز ممكن تتعطل لأن كل جملة بتاخد backend مختلف. الكود بيعالج ده تلقائياً، لكن 5432 أأمن.

4. بدّل `<password>` بكلمة سر القاعدة. لو فيها رموز خاصة (`@` `:` `/` `#`) لازم تعملها URL-encode.

---

## 3. تجهيز Cloudflare

### 3.1 الترقية للخطة المدفوعة

من **Workers & Pages → Plans** اشترك في **Workers Paid** (5 دولار شهرياً). من غيرها `wrangler deploy` هيرفض الكونتينر.

### 3.2 توليد مفتاح التطبيق

التطبيق بيرفض يشتغل في الإنتاج لو المفتاح ضعيف. ولّد واحد:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 3.3 ربط الريبو

1. من **Workers & Pages → Create → Worker → Connect to Git** اختار ريبو `MSFactory`.
2. **Root directory** = `cloudflare` — ده مهم، لأن `wrangler.jsonc` جوّه المجلد ده.
3. **Build command**: سيبه فاضي أو `npm install` — Wrangler بيبني صورة Docker لوحده.
4. **Deploy command** = `npx wrangler deploy`.

   > **تحذير:** متخليش أمر النشر `npx wrangler versions upload`. ده بيرفع كود الـ Worker بس ومش بينشر الصورة، فالتطبيق هيفضل على نسخة قديمة.

5. **Production branch** = `master` (أو `main` لو ده فرعك الرئيسي).

### 3.4 إضافة الأسرار

من **Worker → Settings → Variables and Secrets** أضف الاتنين دول كـ **Secret** (مش Plaintext):

| الاسم | القيمة |
|---|---|
| `MSFACTORY_DATABASE_URL` | رابط Supabase من الخطوة 2 |
| `MSFACTORY_SECRET_KEY` | المفتاح من الخطوة 3.2 |

الـ Worker بيقراهم ويمررهم للكونتينر وقت التشغيل. من غيرهم الكونتينر مش هيقوم.

---

## 4. أول نشر

اعمل push على الفرع الرئيسي، أو من جهازك:

```bash
cd cloudflare
npm install
npx wrangler deploy
```

**ملاحظة:** النشر من جهازك محتاج Docker شغال، لأن Wrangler بيبني الصورة محلياً. لو Docker مش متاح عندك، اعتمد على Workers Builds من GitHub — هو بيبني في السحابة.

أول بناء بياخد وقت (٥–١٠ دقايق). بعد ما يخلص:

```bash
npx wrangler containers list
```

لازم تشوف الكونتينر بحالة `running`.

---

## 5. التحقق

افتح رابط الـ Worker (`https://msfactory.<subdomain>.workers.dev`). أول طلب بيصحّي الكونتينر، فممكن ياخد ٣٠–٦٠ ثانية. بعدها:

- `/health` لازم يرجّع `200`.
- سجّل حساب وافتح التطبيق من الموبايل.
- من إعدادات المتصفح على الموبايل اختار **Add to Home Screen** — التطبيق PWA وبيشتغل زي تطبيق عادي.

لو الصفحة بيضا، افتح **Worker → Logs** وشوف خطأ الكونتينر. أشهر سبب: رابط قاعدة بيانات غلط أو `SECRET_KEY` ناقص.

---

## 6. التكلفة

| البند | السعر |
|---|---|
| Workers Paid | 5 دولار/شهر |
| الذاكرة | 25 GiB-ساعة مشمولة |
| المعالج | 375 vCPU-دقيقة مشمولة |
| Supabase | مجاني للبداية |

الكونتينر بينام بعد ١٠ دقايق سكون، فمش بتدفع وقت مفيش استخدام. الاستخدام الخفيف بيفضل جوّه الحد المشمول.

---

## 7. مشاكل شائعة

| المشكلة | السبب والحل |
|---|---|
| `Containers require Workers Paid` | الخطة مجانية — اشترك في Workers Paid |
| `prepared statement does not exist` | بتستخدم منفذ 6543. الكود بيعالجه، لكن لو استمر غيّر لـ 5432 |
| `MSFACTORY_SECRET_KEY must be at least 32 characters` | المفتاح قصير — ولّد واحد بالأمر فوق |
| الكونتينر بيفضل `stopped` | الأسرار ناقصة. راجع Settings → Variables and Secrets |
| `502` أو `Container did not become healthy` | الميجريشنز فشلت. افتح Logs وشوف خطأ Alembic |
| شاشة بيضا على الموبايل | تأكد إن `/health` شغال، وبعدين امسح كاش المتصفح |
| `Docker is not running` وقت النشر | انشر من Workers Builds على GitHub بدل جهازك |
| تعديلات مش ظاهرة | تأكد إن أمر النشر `wrangler deploy` مش `versions upload` |

---

## 8. بديل بدون Cloudflare Containers

لو مش عايز تدفع 5 دولار شهرياً، نفس الصورة بتشتغل على أي منصة بتشغّل Docker:

- **Render**: خطة مجانية، بس السيرفر بينام بعد ١٥ دقيقة وبيصحّى في ~٥٠ ثانية.
- **Fly.io**: تسعير بالاستخدام.
- **VPS**: أي سيرفر بـ 5 دولار، وتشغّل `docker compose up`.

في الحالات دي، المتغيرات هي هي: `MSFACTORY_DATABASE_URL` و`MSFACTORY_SECRET_KEY` و`MSFACTORY_ENVIRONMENT=production`.

---

## 9. ملاحظات أمنية

- **متحطش** رابط قاعدة البيانات أو المفتاح في الكود أو في `wrangler.jsonc`. الأسرار مكانها Settings → Secrets بس.
- `MSFACTORY_TRUST_PROXY_HEADERS=true` مضبوط في الـ Worker لأن Cloudflare بتمرر IP العميل في `CF-Connecting-IP`. من غيرها كل المستخدمين هيتشاركوا نفس حد المحاولات.
- `.dockerignore` بيستثني `.env` — تأكد إنه موجود قبل أي بناء.
- خلّي `MSFACTORY_ENVIRONMENT=production` عشان فحص قوة المفتاح يشتغل.
