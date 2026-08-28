# -*- coding: utf-8 -*-
"""Birmarket Business kabinet + BBU bot avtomatlaşdırması (Playwright).

Mənbə: `birmarket-tool/automation.py`. Audit tapıntılarına görə dəyişənlər:

  Y1 — BOT LİMİTLƏRİ ARTIQ MÖVQEYƏ GÖRƏ YAZILMIR. Köhnə kod belə idi:
           setVal(nums[0], ALT); setVal(nums[1], UST);
           ...
           okv = parseFloat(nums[0].value)===parseFloat(ALT) && ...
       Yəni yazarkən də, yoxlayarkən də EYNİ mövqe fərziyyəsi işlədilirdi.
       Modal sahələri tərs render olunsa alt və üst yerini dəyişir və
       yoxlama bunu «✅» kimi təsdiqləyirdi. İndi:
         1) sahələr etiketə görə tapılır (mövqe yalnız ehtiyatdır),
         2) yazmadan ƏVVƏL və oxuduqdan SONRA `alt < üst` yoxlanılır,
         3) oxunan dəyər gözlənilənlə tutuşdurulur.

  Y2 — «MƏHSUL ARTIQ VAR» UĞUR DEYİL. Köhnə axın bunu görəndə «✅» yazıb
       bot limitlərinə keçirdi, məhsul isə mağazada yanlış qiymətlə qalırdı.
       İndi `needs_review` qaytarılır və dəftərdə elə möhürlənir.

  O7 — SABİT `sleep` ƏVƏZİNƏ ŞƏRT GÖZLƏMƏSİ. Köhnə kodda 12 yerdə sabit
       gözləmə vardı (~15-20 san/məhsul ölü vaxt), üstəlik yavaş bağlantıda
       erkən klikləyib xəta verirdi. İndi `waitFor(...)` şərt gözləyir.

  O8 — SEÇİCİ ETİBARLILIĞI. Azərbaycanca UI mətnlərinə bağlılıq qaldı (başqa
       yol yoxdur), amma: ehtiyat seçicilər var, hamısı BİR yerdədir
       (`UI` lüğəti), və partiya başlamazdan əvvəl `preflight()` UI
       müqaviləsini yoxlayır — səhv varsa partiyanın ORTASINDA yox, ƏVVƏLİNDƏ
       dayanır.

  O5 — Hər məhsul üçün geri çəkilməli təkrar cəhd.
"""
import time
from pathlib import Path

from .pricing import limits_sane

#: Bütün UI mətn/seçici bağlılıqları BURADA. Sayt dəyişəndə yalnız bura baxılır.
UI = {
    "search_input": ['input[placeholder*="MPN"]', 'input[placeholder*="Artikul"]',
                     'input[type="search"]'],
    "select_button": ["Seçmək"],
    "create_button": ["Məhsul yarat"],
    "save_button": ["Yadda saxla"],
    "cancel_button": ["Ləğv et"],
    "dismiss_button": ["Bir daha göstərmə"],
    "found_one": r"1 məhsul",
    "already_exists": r"Qiymətin və miqdarın",
    "price_label": "Qiymət 1",
    "discount_label": "Endirimli",
    "qty_label": "Miqdar",
    "bot_low_label": ["Minimum", "Alt", "Aşağı"],
    "bot_high_label": ["Maksimum", "Üst", "Yuxarı"],
}

KABINET_SEARCH = "https://business.birmarket.az/account/products/my/search"

# --- brauzerdə işləyən köməkçilər -------------------------------------------
JS_HELPERS = r"""
const sleep = ms => new Promise(r => setTimeout(r, ms));
const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
function setVal(el, v) {
  el.focus(); setter.call(el, v);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  el.blur();
}
const vis = e => e && e.offsetParent !== null && e.getBoundingClientRect().width > 0;
/* Sabit gözləmə əvəzinə ŞƏRT gözləməsi (tapıntı O7) */
async function waitFor(cond, timeout = 15000, step = 200) {
  const t0 = Date.now();
  for (;;) {
    let v;
    try { v = cond(); } catch (e) { v = null; }
    if (v) return v;
    if (Date.now() - t0 > timeout) return null;
    await sleep(step);
  }
}
const btnByText = txts => [...document.querySelectorAll('button')]
  .filter(vis).find(b => txts.includes(b.textContent.trim()));
/* Sahəni ETİKETƏ görə tap — mövqeyə görə yox (tapıntı Y1) */
function fieldByLabel(labels) {
  const arr = Array.isArray(labels) ? labels : [labels];
  const inputs = [...document.querySelectorAll('input')].filter(vis);
  for (const want of arr) {
    const hit = inputs.find(i =>
      (i.labels && [...i.labels].some(l => l.textContent.includes(want))) ||
      (i.placeholder || '').includes(want) ||
      (i.getAttribute('aria-label') || '').includes(want) ||
      (i.name || '').toLowerCase().includes(want.toLowerCase()));
    if (hit) return hit;
  }
  return null;
}
"""

JS_PREFLIGHT = JS_HELPERS + r"""
async ({SEARCH_SELECTORS, CODE, FOUND_ONE, EXISTS}) => {
  const s = SEARCH_SELECTORS.map(q => document.querySelector(q)).find(Boolean);
  if (!s) return {error: 'axtarış sahəsi tapılmadı — kabinetə giriş edilməyib?'};
  setVal(s, CODE);
  for (const t of ['keydown','keypress','keyup'])
    s.dispatchEvent(new KeyboardEvent(t, {key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true}));
  const body = () => document.body.innerText;
  const done = await waitFor(() =>
    (new RegExp(FOUND_ONE).test(body()) || new RegExp(EXISTS).test(body()))
      ? body() : null, 20000);
  if (!done) return {error: 'axtarış nəticə vermədi (20 san)'};
  if (new RegExp(EXISTS).test(done)) return {exists: true};
  return {found: true};
}
"""

JS_CREATE = JS_HELPERS + r"""
async ({SEARCH_SELECTORS, CODE, PRICE, DISC, QTY, UI}) => {
  const s = SEARCH_SELECTORS.map(q => document.querySelector(q)).find(Boolean);
  if (!s) return {error: 'axtarış sahəsi yoxdur (login?)'};
  setVal(s, CODE);
  for (const t of ['keydown','keypress','keyup'])
    s.dispatchEvent(new KeyboardEvent(t, {key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true}));

  const found = await waitFor(() => {
    const txt = document.body.innerText;
    if (new RegExp(UI.already_exists).test(txt)) return {exists: true};
    if (new RegExp(UI.found_one).test(txt)) return {ok: true};
    return null;
  }, 20000);
  if (!found) return {error: 'axtarış nəticə vermədi (20 san)'};
  /* Tapıntı Y2: «artıq var» UĞUR DEYİL — qiymət düzəldilmir, insana qaytarılır */
  if (found.exists) return {exists: true, path: location.pathname};

  const sec = await waitFor(() => btnByText(UI.select_button), 10000);
  if (!sec) return {error: '«Seçmək» düyməsi tapılmadı'};
  sec.click();

  const form = await waitFor(() => {
    const p = fieldByLabel(UI.price_label);
    const e = fieldByLabel(UI.discount_label);
    const m = fieldByLabel(UI.qty_label);
    return (p && e && m) ? {p: p, e: e, m: m} : null;
  }, 15000);
  if (!form) return {error: 'forma sahələri tapılmadı (etiketlər dəyişib?)'};

  setVal(form.p, PRICE); setVal(form.e, DISC); setVal(form.m, QTY);
  await sleep(300);
  if (form.p.value !== PRICE || form.e.value !== DISC || form.m.value !== QTY)
    return {error: 'doldurma səhvi: ' + [form.p.value, form.e.value, form.m.value].join('/')};

  const cr = btnByText(UI.create_button);
  if (!cr) return {error: '«Məhsul yarat» düyməsi yoxdur'};
  cr.click();

  const done = await waitFor(() => {
    const m = document.body.innerText.match(/Satışda \((\d+)\)/);
    return m ? m[0] : null;
  }, 20000);
  return {ok: true, path: location.pathname, satisda: done || ''};
}
"""

JS_BOT_LIMITS = JS_HELPERS + r"""
async ({CODE, ALT, UST, UI}) => {
  const row = await waitFor(() =>
    [...document.querySelectorAll('tr')].find(r => r.textContent.includes(CODE)), 20000);
  if (!row) return {error: 'botda məhsul tapılmadı (hələ sinxron olmayıb?)'};

  const pen = () => [...row.querySelectorAll('button')].find(b => b.querySelector('svg'));
  const p1 = pen();
  if (!p1) return {error: 'redaktə düyməsi tapılmadı'};
  p1.click();

  /* Tapıntı Y1: sahələr ETİKETƏ görə tapılır, mövqe yalnız ehtiyatdır,
     və hansı yolun işlədiyi cavabda («by») açıq bildirilir. */
  const fields = await waitFor(() => {
    const lo = fieldByLabel(UI.bot_low_label);
    const hi = fieldByLabel(UI.bot_high_label);
    if (lo && hi && lo !== hi) return {lo: lo, hi: hi, by: 'label'};
    const nums = [...document.querySelectorAll('input[type=number]')]
      .filter(i => vis(i) && i.value !== CODE && !i.closest('header'));
    if (nums.length === 2) return {lo: nums[0], hi: nums[1], by: 'position'};
    return null;
  }, 12000);
  if (!fields) return {error: 'modal sahələri tapılmadı'};

  setVal(fields.lo, ALT); setVal(fields.hi, UST);
  await sleep(250);
  if (fields.lo.value !== ALT || fields.hi.value !== UST)
    return {error: 'doldurma səhvi: ' + fields.lo.value + '/' + fields.hi.value};

  const save = btnByText(UI.save_button);
  if (!save) return {error: '«Yadda saxla» düyməsi yoxdur'};
  save.click();
  await sleep(1500);

  /* Yenidən aç və oxu — yazıldığını TƏSDİQLƏ */
  const p2 = pen();
  if (!p2) return {error: 'təsdiq üçün redaktə düyməsi tapılmadı'};
  p2.click();
  const after = await waitFor(() => {
    const lo = fieldByLabel(UI.bot_low_label);
    const hi = fieldByLabel(UI.bot_high_label);
    if (lo && hi && lo !== hi) return {lo: lo.value, hi: hi.value};
    const nums = [...document.querySelectorAll('input[type=number]')]
      .filter(i => vis(i) && i.value !== CODE && !i.closest('header'));
    if (nums.length === 2) return {lo: nums[0].value, hi: nums[1].value};
    return null;
  }, 12000);
  const cancel = btnByText(UI.cancel_button);
  if (cancel) cancel.click();
  if (!after) return {error: 'təsdiq oxunmadı'};
  return {ok: true, by: fields.by, low: after.lo, high: after.hi};
}
"""


def f2(x):
    return f"{float(x):.2f}"


class Runner:
    """Kabinet + bot səhifələrini sürən obyekt.

    `publish.execute()` yalnız `publish_one(row)` metodunu tanıyır — testdə
    saxta obyektlə əvəz olunur, ona görə brauzer olmadan da yoxlanılır.
    """

    def __init__(self, bot_url, profile_dir, *, log=print, headless=False,
                 timeout=30000):
        self.bot_url = (bot_url or "").rstrip("/")
        self.profile_dir = Path(profile_dir)
        self.log = log
        self.headless = headless
        self.timeout = timeout
        self._pw = None
        self.ctx = None
        self.kab = None
        self.bot = None

    # ------------------------------------------------------------- ömür
    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir), headless=self.headless, channel="chrome",
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        self.ctx.set_default_timeout(self.timeout)
        self.kab = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.bot = self.ctx.new_page()
        return self

    def __exit__(self, *exc):
        try:
            if self.ctx:
                self.ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    # ------------------------------------------------------------- sessiya
    def session_ready(self):
        """Kabinet və bot sessiyası açıqdırmı? Bloklamır, (bool, mesaj) qaytarır.

        Köhnə `ensure_login()` 5 dəqiqə döngüdə gözləyib sonra `RuntimeError`
        atırdı. Zamanlanmış (headless) koşuda bu SƏSSİZ ölüm deməkdir. İndi
        vəziyyət dərhal qaytarılır; çağıran tərəf istəsə gözləyir, istəsə
        bildiriş göndərir.
        """
        try:
            self.kab.goto(KABINET_SEARCH, wait_until="domcontentloaded")
            has_search = any(self.kab.query_selector(q) for q in UI["search_input"])
            if "/account/" not in self.kab.url or not has_search:
                return False, "Kabinetə giriş yoxdur (business.birmarket.az)"
            if self.bot_url:
                self.bot.goto(self.bot_url + "/dashboard/products",
                              wait_until="domcontentloaded")
                if not self.bot.query_selector("table"):
                    return False, "Bot saytına giriş yoxdur"
            return True, "hazırdır"
        except Exception as e:                              # noqa: BLE001
            return False, f"sessiya yoxlanmadı: {e}"

    def wait_for_login(self, timeout=300, poll=5):
        """İnsan girişi üçün gözlə (yalnız görünən rejimdə mənalıdır)."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            ok, msg = self.session_ready()
            if ok:
                store = self._store_name()
                self.log(f"✅ Sessiya hazırdır — mağaza: {store}")
                return True, store
            self.log(f"⏳ {msg} — açılan brauzerdə daxil ol...")
            time.sleep(poll)
        return False, "giriş gözlənildi, alınmadı"

    def _store_name(self):
        try:
            return self.kab.evaluate(
                "() => (document.querySelector('header')||document.body)"
                ".innerText.split('\\n').map(s=>s.trim()).filter(Boolean)[0]")
        except Exception:                                   # noqa: BLE001
            return "?"

    # ------------------------------------------------------------- preflight
    def preflight(self, sample_code):
        """Partiyadan ƏVVƏL UI müqaviləsini yoxla (tapıntı O8).

        Heç nə yazmır. Məqsəd: seçicilər dəyişibsə partiyanın ORTASINDA yox,
        başlamazdan əvvəl dayanmaq.
        """
        self.kab.goto(KABINET_SEARCH, wait_until="domcontentloaded")
        res = self.kab.evaluate(JS_PREFLIGHT, {
            "SEARCH_SELECTORS": UI["search_input"],
            "CODE": str(sample_code),
            "FOUND_ONE": UI["found_one"],
            "EXISTS": UI["already_exists"],
        })
        if res.get("error"):
            return False, res["error"]
        return True, "UI müqaviləsi yoxlanıldı"

    # ------------------------------------------------------------- yazı
    def create_product(self, code, kohne, endirimli, qty):
        self.kab.goto(KABINET_SEARCH, wait_until="domcontentloaded")
        return self.kab.evaluate(JS_CREATE, {
            "SEARCH_SELECTORS": UI["search_input"],
            "CODE": str(code), "PRICE": f2(kohne), "DISC": f2(endirimli),
            "QTY": str(int(qty)), "UI": UI,
        })

    def set_bot_limits(self, code, alt, ust):
        """Bot alt/üst limitlərini yaz və TƏSDİQLƏ.

        Üç qat qoruma (tapıntı Y1):
          1. yazmadan əvvəl `alt < üst` (Python tərəfdə),
          2. sahələr etiketə görə tapılır,
          3. oxunan dəyərlərdə həm bərabərlik, həm `alt < üst` yoxlanılır.
        """
        if not limits_sane(alt, ust):
            return {"error": f"limitlər məntiqsiz: alt={alt} üst={ust}"}
        url = (f"{self.bot_url}/dashboard/products"
               f"?current=1&pageSize=10&search={code}&statusFilter=all")
        self.bot.goto(url, wait_until="domcontentloaded")
        try:
            self.bot.evaluate(
                "(txts) => { const b=[...document.querySelectorAll('button')]"
                ".find(b=>txts.includes(b.textContent.trim())); if(b) b.click(); }",
                UI["dismiss_button"])
        except Exception:                                   # noqa: BLE001
            pass

        res = self.bot.evaluate(JS_BOT_LIMITS, {
            "CODE": str(code), "ALT": f2(alt), "UST": f2(ust), "UI": UI,
        })
        if res.get("error"):
            return res
        low, high = res.get("low"), res.get("high")
        if not limits_sane(low, high):
            return {"error": f"yazıdan sonra limitlər məntiqsiz: {low}/{high} "
                             f"(sahələr yerini dəyişmiş ola bilər)"}
        if abs(float(low) - float(alt)) > 0.005 or abs(float(high) - float(ust)) > 0.005:
            return {"error": f"yazılmadı: gözlənilən {f2(alt)}/{f2(ust)}, "
                             f"oxunan {low}/{high}"}
        return {"ok": True, "detail": f"{low}/{high} ({res.get('by')})"}

    # ------------------------------------------------------------- müqavilə
    def publish_one(self, row, *, attempts=2, backoff=3.0):
        """`publish.execute()` bunu çağırır.

        Qaytarır: {"ok", "needs_review", "message", "detail"}
        """
        code = row["product_id"]
        last = ""
        for attempt in range(1, attempts + 1):
            created = self.create_product(code, row["price"], row["discount"],
                                          row["qty"] or 10)
            if created.get("exists"):
                # Tapıntı Y2 — bu UĞUR DEYİL.
                return {"ok": False, "needs_review": True,
                        "message": ("Məhsul artıq mağazadadır; qiyməti bu axınla "
                                    "düzəldilmir. Kabinetdə əl ilə yoxla.")}
            if created.get("error"):
                last = f"kabinet: {created['error']}"
                if attempt < attempts:
                    time.sleep(backoff * attempt)
                    continue
                return {"ok": False, "needs_review": False, "message": last}

            if not self.bot_url:
                return {"ok": True, "needs_review": False, "message": "",
                        "detail": created.get("satisda", "") + " (bot ünvanı yoxdur)"}

            limits = self.set_bot_limits(code, row["bot_low"], row["bot_high"])
            if limits.get("error"):
                # Məhsul yaradıldı, amma limitlər qoyulmadı: YARIMÇIQ haldır,
                # susdurulmur — insan baxmalıdır.
                return {"ok": False, "needs_review": True,
                        "message": f"Məhsul yaradıldı, bot limitləri qoyulmadı: "
                                   f"{limits['error']}"}
            return {"ok": True, "needs_review": False, "message": "",
                    "detail": f"{created.get('satisda', '')} bot={limits['detail']}"}
        return {"ok": False, "needs_review": False, "message": last or "naməlum xəta"}
