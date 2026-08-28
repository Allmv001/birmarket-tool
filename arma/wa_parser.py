# -*- coding: utf-8 -*-
"""
WhatsApp elan mətni parseri.

NİYƏ LAZIMDIR — 20.08.2026 real testinin əsas dərsi:
topdan qruplarında model kodu VƏ maya qiyməti şəklin ALTINDAKI mətndə (caption)
yazılır. Yəni Claude Vision (şəkil oxuma) çox vaxt ÜMUMİYYƏTLƏ lazım deyil —
mətni parse etmək həm pulsuzdur, həm daha dəqiqdir.

İstifadə: WhatsApp Web-dən mesajları kopyalayıb yapışdırın (və ya telefonda
"Söhbəti ixrac et" → .txt), sistem hər sətirdən kod + maya + tipi çıxarır.

Dəstəklənən formatlar (hamısı real qrupdan götürülüb):
    ⭐Raf 8111⭐ Portativ qaz sobası 🔥22 manat🔥
    [18:05, 20.08.2026] +994 50 851 81 84: YENİ MODEL GƏLDİ RAF 2603 Böyük toster 48 manat
    Təzə gəldi Raf 5495 Pizza aparatı Ölçü:40 cm 88 manat
    ⭐R.1345⭐ Ceramic soleplate 58 manat
    Təzə gəldi endirim CF001B qaş üz aparatı dəst 3.50 AZN
    Qaş aparatı Zaryatka ilə işləyir 2.50 qəpik      <- kodsuz

v4 DÜZƏLİŞİ — "600 Vt" artıq kod sayılmır:
    v2.4-də ölçü/güc qoruyucusu (NOT_CODE_CONTEXT) yalnız brendli koda
    (RAF 2603) tətbiq olunurdu, hərf+rəqəm koduna (CODE_ALNUM_RE) YOX.
    Nəticədə "Toster 1600 vatt 600 Vt 40 cm 35 manat" sətrindən uydurma
    "VATT-600" kodu çıxırdı və sistem birmarket-də olmayan kodu axtarırdı.
    README bunun işlədiyini yazırdı — işləmirdi. İndi hər iki yol qorunur.
"""
import re

# --- WhatsApp ixrac başlığı: "[18:05, 20.08.2026] +994 50 851 81 84: " -------
WA_HEADER_RE = re.compile(
    r"^\s*\[?(\d{1,2}:\d{2})(?::\d{2})?[,\]]?\s*(\d{1,2}[./]\d{1,2}[./]\d{2,4})?\]?\s*"
    r"(?:[^:]{0,40}?:\s*)?", re.UNICODE)

# --- səs-küy: emoji, dekorativ işarələr, "İletildi/Forwarded" və s. ----------
NOISE_RE = re.compile(
    r"[\u2190-\u21FF\u2300-\u23FF\u2460-\u27BF\u2B00-\u2BFF\uFE0F\u200D]|"
    r"[\U0001F000-\U0001FAFF]|"
    r"\b(\u0130letildi|Iletildi|Forwarded|\u00c7ok kez iletildi|Topluluk y\u00f6neticisi|"
    r"Bu mesajla ilgili daha fazla bilgi edinin|Web\'de ara|HD)\b",
    re.IGNORECASE | re.UNICODE)

# --- qiymət: "48 manat", "🔥22 manat🔥", "3.50 AZN", "2.50 qəpik", "28₼" ------
PRICE_RE = re.compile(
    r"(\d{1,5}(?:[.,]\d{1,2})?)\s*(manat|azn|₼|qəpik|qepik|man)\b",
    re.IGNORECASE | re.UNICODE)

# --- model kodu -------------------------------------------------------------
# 1) "Raf 8111" / "RAF 2603" / "R.1345" / "R-209" / "R209"
CODE_BRAND_RE = re.compile(
    r"\b(RAF|R|RF|LORD|SONIFER|SF)\s*[.\-_]?\s*(\d{3,5})(?!\d)", re.IGNORECASE)
# 2) "CF001B", "MC-22", "SF-401" — hərf+rəqəm(+hərf) birləşməsi
CODE_ALNUM_RE = re.compile(r"\b([A-Z]{2,4})\s*[.\-_]?\s*(\d{2,5}[A-Z]?)\b")

# Ölçü/güc vahidləri — "1600 vatt", "600 Vt", "40 cm" rəqəmlərini kod saymamaq üçün.
# DİQQƏT: pul vahidləri (manat/azn/₼) BU SİYAHIDA DEYİL. v2.4-də onlar da burada idi,
# ona görə hər sətirdəki qiymət ("48 manat") kod tapılmasını bloklayırdı — nəticədə
# RAF/R xaricindəki brendlər (SF, LORD, SONIFER) praktikada heç vaxt tanınmırdı.
UNIT_WORDS = (r"vt|w|watt|vatt|ml|l|sm|cm|mm|kq|kg|gr|volt|v|ay|in|"
              r"dərəcə|derece|proqram|sürət|surat|mah|gb|tb|mb|hz|rpm|kw")

# Rəqəmdən DƏRHAL SONRA vahid gəlirsə o rəqəm ölçüdür ("1600 vatt"), kod deyil.
# Sadəcə yaxınlıqda vahid olması dəlil sayılmır — v2.4-də bu qayda
# "Fen SF-401 2000 vatt" kimi sətirlərdə real kodu öldürürdü.
UNIT_AFTER_RE = re.compile(r"^\s*(?:" + UNIT_WORDS + r")\b", re.IGNORECASE)

# Hərf hissəsi vahidin ÖZÜdürsə kod ola bilməz ("600 VT" -> uydurma "VT-600").
UNIT_AS_CODE_RE = re.compile(
    r"^(?:" + UNIT_WORDS + r"|hd|usb|led|lcd|ip)$", re.IGNORECASE)

# tipi tapmaq üçün açar sözlər (yalnız hesabatda kontekst kimi işlənir)
TYPE_WORDS = [
    "nabor blender", "blender", "toster", "sendviç cihazı", "sendvic cihazi",
    "mətbəx kombaynı", "metbex kombayni", "doğrayan", "dograyan", "doğrayıcı",
    "şirəçəkən", "sirecheken", "pizza aparatı", "pizza", "ütü", "utu",
    "qaz sobası", "qaz plitəsi", "fen", "saç ütü", "trimmer", "epilyator",
    "üz aparatı", "qaş aparatı", "çaydan", "su qızdırıcı", "mikser", "mikroy",
    "aeroqril", "fritür", "tərəvəz doğrayan",
]

FILLER_RE = re.compile(
    r"\b(təzə|teze|yeni|model|gəldi|geldi|endirim|yeni model gəldi|"
    r"böyük|boyuk|nabor|dəst|dest)\b", re.IGNORECASE)


def clean_line(line: str) -> str:
    """WhatsApp başlığını, emojiləri və xidməti sözləri təmizlə."""
    s = WA_HEADER_RE.sub("", line or "")
    s = NOISE_RE.sub(" ", s)
    s = re.sub(r"\b\d{1,2}:\d{2}\b", " ", s)          # sondakı saat
    s = re.sub(r"[⭕🔥⭐️]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -–—:·|")
    return s.strip()


def extract_price(text: str):
    """Mətndən maya qiymətini çıxar. Bir neçə rəqəm varsa qiymət vahidi olanı seçir."""
    best = None
    for m in PRICE_RE.finditer(text or ""):
        val = float(m.group(1).replace(",", "."))
        unit = m.group(2).lower()
        if unit in ("qəpik", "qepik") and val > 100:      # "250 qəpik" -> 2.50
            val = val / 100
        if val <= 0:
            continue
        # iki qiymət varsa (məs. köhnə/yeni) daha kiçiyini maya sayırıq
        best = val if best is None else min(best, val)
    return best


def extract_code(text: str):
    """Mətndən model kodunu çıxar. Tapılmasa None (Google Lens rejimi lazımdır).

    Qayda: bir rəqəm YALNIZ dərhal ardınca ölçü vahidi gələndə ("1600 vatt")
    və ya hərf hissəsi vahidin özü olanda ("600 VT") kod sayılmır.
    """
    text = text or ""
    for m in CODE_BRAND_RE.finditer(text):
        if UNIT_AFTER_RE.match(text[m.end():m.end() + 10]):
            continue                       # "RAF 1600 vatt" -> ölçü, kod deyil
        brand, num = m.group(1).upper(), m.group(2)
        return f"R.{num}" if brand in ("RAF", "R", "RF") else f"{brand} {num}"

    up = text.upper()
    for m in CODE_ALNUM_RE.finditer(up):
        letters, digits = m.group(1), m.group(2)
        if UNIT_AS_CODE_RE.match(letters):
            continue                       # "VATT 600", "CM 40" -> kod deyil
        if UNIT_AFTER_RE.match(up[m.end():m.end() + 10]):
            continue                       # "AB 1600 VATT" -> ölçü, kod deyil
        return f"{letters}-{digits}"
    return None


def extract_type(text: str):
    low = (text or "").lower()
    for w in TYPE_WORDS:
        if w in low:
            return w
    # ehtiyat: kodu və qiyməti atıb qalan sözlərdən qısa təsvir
    rest = PRICE_RE.sub(" ", text or "")
    rest = CODE_BRAND_RE.sub(" ", rest)
    rest = FILLER_RE.sub(" ", rest)
    rest = re.sub(r"[^\w\sƏəĞğİıÖöŞşÜüÇç]", " ", rest)
    rest = re.sub(r"\s+", " ", rest).strip()
    return rest[:40] if len(rest) >= 3 else ""


def parse_whatsapp(text: str):
    """
    WhatsApp mətnindən məhsul siyahısı çıxarır.
    Qaytarır: (items, unknown)
      items   = [{"code","cost","type","raw"}, ...]   — kodu VƏ qiyməti olanlar
      unknown = [{"cost","type","raw"}, ...]          — qiyməti var, kodu yox
                                                        (→ Google Lens rejimi)
    Eyni kod təkrar gəlirsə ƏN SON sətir saxlanılır (qiymət yenilənmiş ola
    bilər) — "son N məhsul" rejimi üçün sıralama da ən son yerinə görə gedir.
    """
    raw_items, unknown = [], []
    for raw in (text or "").splitlines():
        line = clean_line(raw)
        if len(line) < 4:
            continue
        cost = extract_price(line)
        if cost is None or cost <= 0:
            continue
        code = extract_code(line)
        ptype = extract_type(line)
        if code:
            raw_items.append({"code": code, "cost": cost, "type": ptype, "raw": line})
        else:
            unknown.append({"cost": cost, "type": ptype, "raw": line})
    # dedup: sondan başa — hər kodun ƏN SON görünüşü qalır
    seen, items = set(), []
    for it in reversed(raw_items):
        key = re.sub(r"[^A-Z0-9]", "", it["code"].upper())
        if key in seen:
            continue
        seen.add(key)
        items.append(it)
    items.reverse()
    return items, unknown
