# -*- coding: utf-8 -*-
"""Model kodu uyğunluğu, marja hesabı və birmarket axtarış variantları.

v2.4-dəki `marja.py` buradan gəlir. Dəyişənlər:
  * evaluate() maya <= 0 halında artıq "uyğun" qaytarmır (v2.4-də hər şey uyğun sayılırdı).
  * match_level() eyni qalır — real testdə 7/7 doğru işləyirdi, toxunulmadı.
"""
import re

PRICE_RE = re.compile(r"^\s*(\d{1,6}(?:[.,]\d{1,2})?)\s*₼\s*$")
TAKSIT_RE = re.compile(r"₼\s*x\s*\d+\s*ay", re.IGNORECASE)
DISCOUNT_RE = re.compile(r"^\s*-\s*\d+\s*%\s*$")


def _tokens(code: str):
    tokens = re.findall(r"[A-Za-z]+|\d+", code or "")
    if not tokens:
        raise ValueError(f"Kod tokenlənə bilmədi: {code!r}")
    return tokens


def _seq_pattern(tokens) -> re.Pattern:
    body = r"[\s.\-]*".join(re.escape(t) for t in tokens)
    return re.compile(r"(?<![A-Za-z0-9])" + body + r"(?!\d)", re.IGNORECASE)


def _core_tokens(tokens):
    """Çoxtokenli kodda əsas (nüvə) kod: SON 'hərf tokeni + ardınca rəqəm' cütündən
    sona qədər. Məs. MAC MC-22 -> ['MC','22'] (brend 'MAC' atılır).
    Satıcılar brendi fərqli yazır ('M.A.C', 'Mac Styler'), amma nüvə kodu sabitdir."""
    j = None
    for i in range(len(tokens) - 1):
        if tokens[i].isalpha() and tokens[i + 1].isdigit():
            j = i
    if j is not None and j > 0:
        return tokens[j:]
    return None


def code_pattern(code: str) -> re.Pattern:
    """R.209 -> R.209 / R209 / r 209 / R-209 uyğundur; R.2090 / R.2809 uyğun DEYİL."""
    return _seq_pattern(_tokens(code))


def code_patterns(code: str):
    """Dəqiq uyğunluq üçün pattern siyahısı: tam kod + (varsa) nüvə kodu.
    'MAC MC-22' -> [MAC MC 22, MC 22] — beləcə 'M.A.C Styler MC-22' də tutulur."""
    tokens = _tokens(code)
    pats = [_seq_pattern(tokens)]
    core = _core_tokens(tokens)
    if core:
        pats.append(_seq_pattern(core))
    return pats


def parse_price(s: str):
    m = PRICE_RE.match(s or "")
    return float(m.group(1).replace(",", ".")) if m else None


def digits_pattern(code: str):
    """Kodun əsas rəqəm hissəsi (≥3 rəqəm) üçün yumşaq pattern.
    'RAF209', '209 10in1' kimi variantları tutur; R.2090 tutmur."""
    nums = [t for t in re.findall(r"\d+", code or "") if len(t) >= 3]
    if not nums:
        return None
    return re.compile(r"(?<!\d)" + re.escape(nums[-1]) + r"(?!\d)")


def match_level(name: str, code: str) -> int:
    """2 = dəqiq uyğunluq (tam kod VƏ YA nüvə kodu: 'MAC MC-22' → 'MC-22' də sayılır),
       1 = ehtimal (yalnız rəqəm hissəsi uyğundur: RAF209, ...209...),
       0 = uyğun deyil."""
    name = name or ""
    if not (code or "").strip():
        return 0
    try:
        pats = code_patterns(code)
    except ValueError:
        return 0
    for p in pats:
        if p.search(name):
            return 2
    dp = digits_pattern(code)
    if dp and dp.search(name):
        return 1
    return 0


def _token_forms(tokens):
    """Token siyahısından axtarış formaları: nöqtəli, bitişik, boşluqlu, defisli."""
    return [".".join(tokens).lower(), "".join(tokens).lower(),
            " ".join(tokens).lower(), "-".join(tokens).lower()]


def widen_queries(name: str, code: str, limit: int = 3):
    """Tapılmış bir elanın adından "genişlətmə" sorğuları düzəlt.

    Niyə lazımdır: istifadəçi «Brend» və «Məhsul tipi» sahələrini boş
    qoyanda yalnız çılpaq kod sorğuları qalır və birmarket onlara demək
    olar cavab vermir. 28.08.2026: `r.209` BİR kart qaytardı, halbuki
    saytda YEDDİ R.209 var idi. Amma həmin bir kartın adı (`Vafli cihazı
    RAF R.209`) qalan altısını tapan sorğuları özündə daşıyırdı.

    Brend və tipi AYIRD ETMİR, qəsdən. Mövqeyə görə ad vermək yanılır:
    «Vafli cihazı RAF R.209»-da koddan əvvəlki söz brenddir (RAF), amma
    «Fen SF-401»-də koddan əvvəlki söz tipdir (fen), brend isə kodun
    içindədir. Ona görə burada yalnız sorğu qurulur, təsnifat edilmir.
    """
    nt = re.findall(r"[^\W_]+", (name or "").lower(), re.UNICODE)
    ct = re.findall(r"[^\W_]+", (code or "").lower(), re.UNICODE)
    base = (code or "").lower().strip()
    if not nt or not ct or not base:
        return []
    pos = -1
    for i in range(len(nt) - len(ct) + 1):
        if nt[i:i + len(ct)] == ct:
            pos = i
            break
    if pos < 1:                       # kod adın əvvəlindədir, öndə söz yoxdur
        return []
    onceki = nt[pos - 1]              # koddan dərhal əvvəlki söz
    prefiks = " ".join(nt[:pos])      # koddan əvvəlki bütün sözlər
    adaylar = [f"{onceki} {base}", f"{prefiks} {base}"]
    # Çılpaq prefiks yalnız iki və daha çox sözdən ibarətdirsə: «vafli cihazı
    # raf» faydalıdır, «fen» isə saytın yarısını qaytarır və heç nə əlavə
    # etmədən sürət limitini yeyir.
    if len(prefiks.split()) >= 2:
        adaylar.append(prefiks)
    out = []
    for q in adaylar:
        if q and q not in out:
            out.append(q)
    return out[:limit]


def search_variants(code: str, brands: str = "", ptype: str = ""):
    """Bir kod üçün birmarket axtarış sorğusu variantları.
    3 qat: (1) kod formaları (r.209/r209/r 209/r-209 + nüvə formaları),
    (2) brend + kod ("raf r.209", "raf r209"),
    (3) MƏHSUL TİPİ + kod ("sendviç cihazı r.209") — ƏN GENİŞ əhatə:
    saytın axtarışı bir çox kartı yalnız bu cür sorğularda qaytarır."""
    tokens = re.findall(r"[A-Za-z]+|\d+", code or "")
    base = (code or "").lower().strip()
    nospace = "".join(tokens).lower() if tokens else base
    brand_list = [x.strip().lower() for x in (brands or "").split(",") if x.strip()]
    pt = (ptype or "").strip().lower()

    # SIRA VACIBDIR. `auto_search` siyahini `max_variants` (defolt 4) ile
    # kesir, ona gore EN MEHSULDAR sorgular ONDE olmalidir.
    #
    # 28.08.2026-da olculdu, R.209 ucun (yeni tapilan elan sayi):
    #     raf r.209           71      <- brend + kod
    #     vafli cihazi r.209  70      <- tip + kod
    #     vafli cihazi raf    20
    #     raf r209             5
    #     r.209                1
    #     r209                 2
    #     r 209                0
    #     r-209                0
    # Kohne sirada ilk dord yalniz ciplaq kod formalari idi (cemi 3 elan) ve
    # 71+70 verenler mehz kesilen hissede qalirdi. Netice: birmarket-de BES
    # gercek R.209 elani var idi, sistem BIRINI gosterirdi. Formdakı «tip +
    # kod sorgulari en genis neticeni verir» ipucu de bos vede olurdu.
    qualified = []                     # brend/tip ile zenginlesdirilmis
    for b in brand_list:
        qualified.append(f"{b} {base}")
    if pt:
        qualified.append(f"{pt} {base}")
    for b in brand_list:
        qualified.append(f"{b} {nospace}")
    if pt:
        for b in brand_list:
            qualified.append(f"{pt} {b}")

    bare = [base]                      # ciplaq kod formalari - az mehsuldar
    if tokens:
        bare += _token_forms(tokens)
        core = _core_tokens(tokens)
        if core:
            bare += _token_forms(core)

    # Ciplaq `base` (deqiq kod) zenginlesdirilmislerden sonra, qalan ciplaq
    # formalardan evvel: ucuzdur ve deqiq uygunlugu tez tapir.
    variants = qualified[:2] + [base] + qualified[2:] + bare

    seen, out = set(), []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def evaluate(cost: float, threshold: float, price: float, name: str, code: str):
    """Bir sətri qiymətləndir -> (level, margin_pct, is_match).

    level: 2 dəqiq, 1 ehtimal, 0 uyğun deyil.
    is_match yalnız DƏQİQ uyğunluq + hədd şərti ilə True olur.

    DÜZƏLİŞ (v4): maya <= 0 olduqda v2.4 hər sətri "uyğun" sayırdı
    (hədd = 0 × 1.20 = 0, ona görə istənilən qiymət həddi keçirdi).
    Toplu/WhatsApp rejimində qiyməti səhv oxunmuş bir sətir bütün nəticəni
    zibilləyirdi. İndi maya <= 0 → heç vaxt uyğun deyil.
    """
    try:
        cost = float(cost)
        price = float(price)
        threshold = float(threshold)
    except (TypeError, ValueError):
        return 0, 0.0, False
    if cost <= 0:
        return match_level(name, code), 0.0, False
    limit = cost * threshold
    level = match_level(name, code)
    margin = round((price / cost - 1) * 100, 1)
    return level, margin, bool(level == 2 and price >= limit)
