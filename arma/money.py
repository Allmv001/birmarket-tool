# -*- coding: utf-8 -*-
"""Pul məbləğlərinin TƏHLÜKƏSİZ oxunması və hesablanması.

NİYƏ AYRI MODUL — ölçülmüş zərər (27.08.2026 auditi):
    Köhnə `birmarket-tool/birmarket_api.py` sadə `(\\d+(?:[.,]\\d+)?)` regexi ilə
    maya oxuyurdu. Nəticə:

        "1.234,56 manat"  ->  1.234   (əsl dəyər 1234.56)
        "1 234,56 manat"  ->  1.0     (əsl dəyər 1234.56)

    Sonra `app.py` yoxlaması (`maya <= 0 or maya > 5000`) bunu TUTMURDU, çünki
    1.234 hər iki həddin içindədir. Nəticədə 1234 ₼ mayalı məhsul **2.99 ₼**-ə
    elan olunurdu; bot da xilas etmirdi, çünki alt limit də eyni yanlış mayadan
    hesablanırdı. 10 ədəd üçün ~12 000 ₼ zərər.

QAYDA — şübhə varsa TƏXMİN ETMƏ, XƏTA AT:
    Tək ayırıcıdan sonra DƏQİQ 3 rəqəm varsa ("1.234", "1,234") məbləğ
    birmənalı deyil (1234 yoxsa 1.234?). Belə halda `AmbiguousMoney` atılır və
    istifadəçidən dəqiqləşdirmə istənir. Yanlış təxminin qiyməti minlərlə manat,
    soruşmağın qiyməti bir klikdir.

Bütün hesablar `Decimal` ilə aparılır — float ilə 0.1 + 0.2 != 0.3 problemi
qəpik hesabında real fərq yaradır.
"""
import re
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP

__all__ = [
    "AmbiguousMoney", "InvalidMoney", "parse_money", "find_money",
    "money", "q2", "roundup", "to_float",
]

CENT = Decimal("0.01")

# Rəqəmlərin arasındakı boşluq/apostrof minlik ayırıcısıdır: "1 234", "1'234"
_THOUSAND_SPACE = re.compile(r"(?<=\d)[\s  '](?=\d)")
# Məbləğ kimi görünən hissə (valyuta işarələri kənarda qalır)
_MONEY_TOKEN = re.compile(r"\d[\d\s  '.,]*")
# Valyuta və bəzək simvolları
_CURRENCY = re.compile(r"[₼$€₽]|\b(?:manat|azn|man)\b", re.IGNORECASE)


class InvalidMoney(ValueError):
    """Mətndən ümumiyyətlə məbləğ oxunmadı."""


class AmbiguousMoney(ValueError):
    """Məbləğ oxundu, amma iki cür başa düşülə bilər — istifadəçi dəqiqləşdirməlidir.

    `candidates` — mümkün oxunuşlar, UI-də seçim təklif etmək üçün.
    """

    def __init__(self, raw, candidates):
        self.raw = raw
        self.candidates = list(candidates)
        options = " və ya ".join(f"{c:f}" for c in self.candidates)
        super().__init__(
            f"«{raw}» birmənalı deyil: {options} ola bilər. "
            f"Ayırıcısız yazın (məs. {self.candidates[-1]:f})."
        )


def _clean(text):
    r"""Valyuta işarələrini və minlik boşluqlarını at, məbləğ hissəsini qaytar.

    MƏNFİ İŞARƏ RƏDD OLUNUR (testdə tutuldu, 28.08.2026). Əvvəl `_MONEY_TOKEN`
    `\d` ilə başladığı üçün mənfi işarə sadəcə düşürdü və "-5" -> 5 olurdu.
    Praktik zərəri: birmarket səhifəsindən kopyalanan endirim sətri ("-22 %")
    linkdən sonra gəlsə maya 22 kimi oxunurdu — K1 ilə eyni sinif səhv.
    """
    s = _CURRENCY.sub(" ", str(text or ""))
    s = _THOUSAND_SPACE.sub("", s)
    m = _MONEY_TOKEN.search(s)
    if not m:
        raise InvalidMoney(f"Məbləğ tapılmadı: {text!r}")
    before = s[:m.start()].rstrip()
    if before.endswith("-") or before.endswith("−"):
        raise InvalidMoney(f"Mənfi məbləğ qəbul edilmir: {text!r}")
    return m.group(0).strip().rstrip(".,")


def parse_money(text, *, allow_ambiguous=False):
    """Mətni Decimal məbləğə çevir.

    Dəstəklənən formatlar:
        "275"        -> 275
        "275,50"     -> 275.50
        "275.50"     -> 275.50
        "1.234,56"   -> 1234.56     (hər iki ayırıcı: SONUNCU onluqdur)
        "1,234.56"   -> 1234.56
        "1 234,56"   -> 1234.56     (boşluq minlikdir)
        "1.234.567"  -> 1234567     (təkrarlanan ayırıcı = minlik)
        "68 manat"   -> 68

    Birmənalı olmayan hal (`allow_ambiguous=False` ikən `AmbiguousMoney` atır):
        "1.234"      -> 1234 yoxsa 1.234?
        "1,234"      -> eyni sual

    `allow_ambiguous=True` verilsə minlik ayırıcısı kimi oxunur (1234) — bunu
    yalnız istifadəçi açıq şəkildə təsdiqləyəndə ötür.
    """
    raw = str(text or "").strip()
    s = _clean(raw)

    dots, commas = s.count("."), s.count(",")

    if dots and commas:
        # Hər iki ayırıcı var: sonuncu görünən onluq ayırıcıdır
        dec_sep = "." if s.rfind(".") > s.rfind(",") else ","
        thou_sep = "," if dec_sep == "." else "."
        s = s.replace(thou_sep, "").replace(dec_sep, ".")
    elif dots or commas:
        sep = "." if dots else ","
        count = dots or commas
        tail = len(s) - s.rfind(sep) - 1
        if count > 1:
            s = s.replace(sep, "")            # "1.234.567" -> minlik
        elif tail == 3:
            # BİRMƏNALI DEYİL: "1.234" = 1234 (minlik) yoxsa 1.234 (onluq)?
            thousands = Decimal(s.replace(sep, ""))
            decimal_ = Decimal(s.replace(sep, "."))
            if not allow_ambiguous:
                raise AmbiguousMoney(raw, [decimal_, thousands])
            s = s.replace(sep, "")
        else:
            s = s.replace(sep, ".")           # normal onluq: "275,50", "12.5"
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise InvalidMoney(f"Məbləğ oxunmadı: {text!r}")
    if value < 0:
        raise InvalidMoney(f"Məbləğ mənfi ola bilməz: {text!r}")
    return value


def find_money(text, *, allow_ambiguous=False):
    """Sərbəst mətndən İLK məbləği tap. Tapılmasa `None` (xəta atmır).

    `AmbiguousMoney` YENƏ DƏ atılır — birmənalı olmayan məbləği susub ötürmək
    məhz düzəltdiyimiz səhvdir.
    """
    try:
        return parse_money(text, allow_ambiguous=allow_ambiguous)
    except InvalidMoney:
        return None


def money(value):
    """İstənilən dəyəri Decimal-a çevir (float-dan keçəndə repr vasitəsilə)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(str(value))


def q2(value):
    """Qəpiyə yuvarlaqlaşdır (bankir yuvarlaqlaşdırması YOX — adi 0.5 yuxarı)."""
    return money(value).quantize(CENT, rounding=ROUND_HALF_UP)


def roundup(value):
    """Excel ROUNDUP(x, 0) qarşılığı: qəpiyə yuvarlaqlaşdır, sonra tam ədədə yuxarı.

    Əvvəlcə qəpiyə yuvarlaqlaşdırmaq vacibdir — float qalığı olan 898.0000001
    kimi dəyər birbaşa ceil ediləndə 899 verirdi.
    """
    return q2(value).to_integral_value(rounding=ROUND_CEILING)


def to_float(value):
    """Decimal -> float (yalnız JSON/SQLite sərhədində, hesabda yox)."""
    return None if value is None else float(value)
