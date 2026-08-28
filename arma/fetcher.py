# -*- coding: utf-8 -*-
"""
Birmarket avtomatik axtarış.

İstifadəçi tələb etdikdə (düyməyə basanda) işləyir — 7/24 fon prosesi DEYİL.
Axtarış səhifələri server tərəfdən render olunur, ona görə adi HTTP sorğusu
ilə kartlar (ad, qiymət, link) birbaşa oxunur.

v4 dəyişiklikləri:
  * Sürət limiti sayğacı (circuit breaker) thread-safe oldu. v2.4-də iki
    `global` dəyişən kilidsiz paylaşılırdı; toplu yoxlama ilə əl ilə axtarış
    eyni anda işləyəndə sayğac pozulurdu.
  * Kart seçicisi bir yerdə (CARD_SELECTORS) və ehtiyat seçiciləri var —
    sayt tərtibatı dəyişəndə hamısı bir sətirdə yenilənir.
  * Sorğular arası fasilə parametr oldu (ayarlardan idarə olunur).
"""
import re
import threading
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

BASE = "https://birmarket.az"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "az,en;q=0.8",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

PRODUCT_ID_RE = re.compile(r"/product/(\d+)")
TAKSIT_CLEAN = re.compile(r"\d+(?:[.,]\d{1,2})?\s*₼\s*x\s*\d+\s*ay", re.IGNORECASE)
PRICE_FIND = re.compile(r"(\d+(?:[.,]\d{1,2})?)\s*₼")

# Sayt tərtibatı dəyişəndə YALNIZ bu siyahı yenilənir.
CARD_SELECTORS = [".MPProductItem", "[class*=ProductItem]", "[data-testid=product-card]"]
TITLE_SELECTORS = [".MPTitle", "[class*=Title]", "h3", "h2"]

# --- sürət limiti "qoruyucusu" (circuit breaker) ---
# Real dərs (20.08.2026): ~100 ardıcıl sorğudan sonra birmarket cavab verməyi
# dayandırır və hər sorğu timeout-a qədər asılı qalır. FAIL_TRIP ardıcıl
# uğursuzluqdan sonra COOLDOWN qədər fasilə verilir, qalan sorğular tez atlanır.
FAIL_TRIP = 4
COOLDOWN = 90.0
_lock = threading.Lock()
_fail_streak = 0
_cooldown_until = 0.0


def breaker_state():
    """(açıqdır?, qalan_saniyə) — UI-də "sürət limiti" xəbərdarlığı üçün."""
    with _lock:
        remaining = max(0.0, _cooldown_until - time.time())
    return remaining > 0, round(remaining)


def _note_success():
    global _fail_streak
    with _lock:
        _fail_streak = 0


def _note_failure():
    global _fail_streak, _cooldown_until
    with _lock:
        _fail_streak += 1
        if _fail_streak >= FAIL_TRIP:
            _cooldown_until = time.time() + COOLDOWN


def _tripped():
    with _lock:
        return _fail_streak >= FAIL_TRIP


def _wait_out_cooldown():
    global _fail_streak
    with _lock:
        wait = _cooldown_until - time.time()
    if wait > 0:
        time.sleep(min(wait, COOLDOWN))
        with _lock:
            _fail_streak = 0


def product_id(url: str) -> str:
    """Linkdən məhsul ID-sini çıxar (dublikat müqayisəsi slug-dan asılı olmasın)."""
    m = PRODUCT_ID_RE.search(url or "")
    return m.group(1) if m else (url or "")


def _select_first(node, selectors):
    for sel in selectors:
        found = node.select_one(sel)
        if found:
            return found
    return None


def parse_search_html(html: str):
    """Axtarış səhifəsi HTML-indən kartları çıxar:
    [{"name","price","old_price","url","seller":None}, ...]"""
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for sel in CARD_SELECTORS:
        cards = soup.select(sel)
        if cards:
            break
    out = []
    for card in cards:
        a = card.select_one('a[href^="/product/"]')
        if not a:
            continue
        title = _select_first(card, TITLE_SELECTORS) or a
        text = TAKSIT_CLEAN.sub(" ", card.get_text(" ", strip=True))
        prices = [float(p.replace(",", ".")) for p in PRICE_FIND.findall(text)]
        if not prices:
            continue
        price = prices[0]
        old = prices[1] if len(prices) > 1 and prices[1] > prices[0] else None
        out.append({
            "name": title.get_text(" ", strip=True),
            "price": price,
            "old_price": old,
            "url": BASE + a["href"].split("?")[0],
            "seller": None,
        })
    return out


def fetch_search(query: str, max_pages: int = 2, delay: float = 1.0, timeout: int = 12):
    """Bir axtarış sorğusu üçün səhifə(lər)i çək və kartları qaytar."""
    results = []
    for page in range(1, max_pages + 1):
        url = f"{BASE}/search/{quote(query)}"
        if page > 1:
            url += f"?page={page}"
        r = None
        backoff = 2.0
        for attempt in (1, 2):
            try:
                r = SESSION.get(url, timeout=timeout)
                if r.status_code in (429, 503):
                    raise RuntimeError(f"sürət limiti ({r.status_code})")
                r.raise_for_status()
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(backoff)
                backoff *= 2
        cards = parse_search_html(r.text)
        results.extend(cards)
        if len(cards) < 24:          # son səhifə
            break
        time.sleep(delay)
    return results


def auto_search(code: str, brands: str = "", ptype: str = "",
                max_pages: int = 3, max_variants: int = 4, delay: float = 1.0,
                should_stop=None):
    """Bütün axtarış variantlarını gəz, kartları məhsul ID-sinə görə birləşdir.

    should_stop — callable; True qaytaranda axtarış yarıda dayanır
                  (istifadəçi toplu işi ləğv edəndə).

    Qaytarır: (offers, errors) — errors: [(sorğu, xəta_mesajı)]
    """
    from .codes import search_variants
    variants = search_variants(code, brands, ptype)
    # tip+kod sorğuları ƏN GENİŞ nəticəni verir -> öndə olsun
    pt = (ptype or "").strip().lower()
    if pt:
        variants.sort(key=lambda q: 0 if q.startswith(pt) else 1)
    if max_variants and max_variants > 0:
        variants = variants[:max_variants]

    _wait_out_cooldown()

    seen, errors = {}, []
    for q in variants:
        if should_stop and should_stop():
            errors.append((q, "ləğv edildi"))
            break
        if _tripped():
            errors.append((q, "sürət limiti — sorğu atlandı (fasilədən sonra davam olunacaq)"))
            continue
        try:
            for o in fetch_search(q, max_pages=max_pages, delay=delay):
                # eyni məhsul fərqli slug-la gəlsə də tək sayılır
                seen.setdefault(product_id(o["url"]), o)
            _note_success()
        except Exception as e:
            _note_failure()
            errors.append((q, str(e)))
        time.sleep(delay)            # sorğular arası nəzakət fasiləsi

    # --- öz-özünə genişlətmə ------------------------------------------------
    # İstifadəçi «Brend» və «Məhsul tipi» sahələrini boş qoyubsa, yuxarıdakı
    # dövrə yalnız çılpaq kod sorğuları işlədib və birmarket onlara demək olar
    # cavab verməyib. 28.08.2026: `R.209` üçün BİR elan tapıldı, saytda isə
    # YEDDİ var idi. Amma tapılan həmin bir elanın adı (`Vafli cihazı RAF
    # R.209`) qalanını tapan sorğuları özündə daşıyırdı: `raf r.209` tək
    # başına 118 yeni kart verir.
    #
    # Ona görə: dəqiq uyğunluq tapılıbsa, onun adından sorğu düzəldib bir
    # dövrə də vururuq. Yalnız sahələr boş olanda - istifadəçi özü yazıbsa
    # onun sözü əsasdır.
    if not (brands or "").strip() and not (ptype or "").strip():
        from .codes import match_level, widen_queries
        deqiq = [o for o in seen.values() if match_level(o["name"], code) == 2]
        genis = []
        for o in deqiq[:2]:                      # ilk iki dəqiq uyğunluq bəsdir
            for q in widen_queries(o["name"], code):
                if q not in variants and q not in genis:
                    genis.append(q)
        for q in genis[:3]:                      # xərci sərhədləndir
            if should_stop and should_stop():
                break
            if _tripped():
                errors.append((q, "sürət limiti — genişlətmə sorğusu atlandı"))
                continue
            try:
                for o in fetch_search(q, max_pages=max_pages, delay=delay):
                    seen.setdefault(product_id(o["url"]), o)
                _note_success()
            except Exception as e:
                _note_failure()
                errors.append((q, str(e)))
            time.sleep(delay)

    return list(seen.values()), errors
