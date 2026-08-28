# -*- coding: utf-8 -*-
"""Qiymət qaydaları — Allam-ın təsdiqlədiyi düsturlar, Decimal ilə.

Mənbə: `birmarket-tool/pricing.py`. Düsturlar EYNİDİR (testlərdə qızıl
dəyərlərlə kilidlənib), amma üç səhv düzəldilib:

  1. KRİTİK — «status active deyil» halı artıq «satıcı yoxdur» sayılmır.
     Köhnə `app.py:81` belə idi:
         offers = f["offers"] if f.get("status") == "active" else []
     Rəqib 100 ₼-ə satırdı, status `inactive` idisə sistem RƏQİB YOXDUR sanıb
     maya×1.70 tətbiq edirdi — rəqibin iki qatı, heç vaxt satılmır. Üstəlik
     səbəb sütununda «Satıcı yoxdur» yazırdı, yəni cədvələ baxanda görünmürdü.
     İndi naməlum status ayrıca qərardır: SKIP + STATUS_NOT_ACTIVE.

  2. `kohne` artıq TÖRƏMƏ sahədir. Köhnə `/update` endirimli ilə köhnəni
     müstəqil yazırdı; istifadəçi endirimlini 80-ə çəkəndə köhnə 205.60-da
     qalırdı (düzgünü 165.60). İndi `recompute()` çağırılır.

  3. Bütün hesab Decimal — qəpik hesabında float qalığı yığılmır.

Düsturlar:
    endirimli = ən ucuz rəqib − 0.01   (rəqib yoxdursa: ROUNDUP(maya×1.70) − 0.01)
    köhnə     = ROUNDUP(endirimli×2) + ~5 ₼ (500+ olanda +2; ...0 ilə bitməsin),
                qəpik = maya-nın tam hissəsinin mod 100-ü
    bot alt   = maya × 1.25
    bot üst   = endirimli + 20
    keç:  öz mağazan satıcılar arasındadırsa
          və ya (ən ucuz − maya) < MIN_MARGIN
          və ya katalog statusu `active` deyil
"""
from decimal import Decimal, ROUND_FLOOR

from .money import money, q2, roundup

# ------------------------------------------------------------------ defolt qaydalar
DEFAULT_OWN_STORES = ["Spark electronics", "Spark Tech", "PoWeR Tech", "Pro Tech"]
DEFAULT_MIN_MARGIN = Decimal("4.00")
DEFAULT_QTY = 10
DEFAULT_MARKUP = Decimal("1.70")          # rəqib olmayanda
DEFAULT_BOT_LOW = Decimal("1.25")         # alt limit = maya × 1.25
DEFAULT_BOT_HIGH_PLUS = Decimal("20.00")  # üst limit = endirimli + 20

# Yalnız bu katalog statusunda rəqib siyahısına etibar edilir.
ACTIVE_STATUS = "active"

# ------------------------------------------------------------------ qərar kodları
OK = "OK"
SKIP = "SKIP"
ERROR = "ERROR"

R_OWN_STORE = "OWN_STORE"                   # öz mağazan artıq satıcılar arasındadır
R_LOW_MARGIN = "LOW_MARGIN"                 # ən ucuz − maya < hədd
R_STATUS_NOT_ACTIVE = "STATUS_NOT_ACTIVE"   # katalog statusu naməlum/passiv
R_NO_SELLER = "NO_SELLER"                   # status active, amma satıcı yoxdur
R_COMPETITOR = "COMPETITOR"                 # normal hal: rəqibin altını kəsirik
R_FETCH_ERROR = "FETCH_ERROR"               # kataloqdan oxunmadı

REASON_TEXT = {
    R_OWN_STORE: "Öz mağazan var",
    R_LOW_MARGIN: "Marja həddən aşağı",
    R_STATUS_NOT_ACTIVE: "Katalog statusu «active» deyil — rəqib siyahısına etibar edilmir",
    R_NO_SELLER: "Satıcı yoxdur",
    R_COMPETITOR: "Ən ucuz rəqibin altı",
    R_FETCH_ERROR: "Kataloqdan oxunmadı",
}


def kohne_qiymet(endirimli, maya):
    """Üstü çizili «köhnə» qiymət.

    QEYD (məxfilik riski): qəpik hanəsi mayadan gəlir, yəni köhnə qiymətin son
    iki rəqəmi mayanın mod 100-üdür. Qaydanı bilən rəqib maya barədə məlumat
    çıxara bilər. Düstur Allam-ın qərarıdır — dəyişdirilməyib, yalnız qeyd
    olunub (audit hesabatı, tapıntı O9).
    """
    endirimli, maya = money(endirimli), money(maya)
    base = roundup(endirimli * 2)
    qepik = int(maya.to_integral_value(rounding=ROUND_FLOOR)) % 100
    extra = Decimal(5) if base < 500 else Decimal(2)
    if (base + extra) % 10 == 0:
        extra += Decimal(1) if base < 500 else Decimal(2)
    return q2(base + extra + Decimal(qepik) / 100)


def endirimli_no_seller(maya, markup=DEFAULT_MARKUP):
    """Rəqib olmayanda endirimli qiymət: ROUNDUP(maya × markup) − 0.01."""
    return q2(roundup(money(maya) * money(markup)) - Decimal("0.01"))


def _own_matches(offers, own_stores):
    own = {s.lower().strip() for s in own_stores if s and s.strip()}
    return [o for o in offers if (o.get("seller") or "").lower().strip() in own]


def _clean_offers(offers):
    """Qiyməti olmayan/sıfır olan sətirləri at, qiyməti Decimal et."""
    out = []
    for o in offers or []:
        try:
            price = money(o.get("price") or 0)
        except Exception:
            continue
        if price > 0:
            out.append({"seller": o.get("seller") or "", "price": price,
                        "old": o.get("old") or 0})
    return out


def evaluate(offers, maya, *, status=ACTIVE_STATUS, fetch_error=None,
             own_stores=None, min_margin=DEFAULT_MIN_MARGIN,
             markup=DEFAULT_MARKUP, bot_low=DEFAULT_BOT_LOW,
             bot_high_plus=DEFAULT_BOT_HIGH_PLUS, qty=DEFAULT_QTY):
    """Bir məhsul üçün qərar ver.

    `status` — kataloqdan gələn məhsul statusu. `active` deyilsə rəqib
    siyahısı ETİBARSIZ sayılır və qərar SKIP olur (köhnə sistem burada
    «satıcı yoxdur» deyib yanlış qiymət qoyurdu).

    Qaytarır: dict — verdict, reason_code, reason, qiymətlər (Decimal).
    """
    maya = money(maya)
    min_margin = money(min_margin)
    own_stores = own_stores if own_stores is not None else DEFAULT_OWN_STORES

    res = {
        "verdict": OK, "reason_code": R_COMPETITOR, "reason": "",
        "status": status, "n": 0, "qty": int(qty),
        "maya": maya, "min_price": None, "min_seller": None, "margin": None,
        "endirimli": None, "kohne": None, "alt": None, "ust": None,
    }

    def _skip(code, extra=""):
        res.update(verdict=SKIP, reason_code=code,
                   reason=REASON_TEXT[code] + (f": {extra}" if extra else ""))
        return res

    if fetch_error:
        res.update(verdict=ERROR, reason_code=R_FETCH_ERROR,
                   reason=f"{REASON_TEXT[R_FETCH_ERROR]}: {fetch_error}")
        return res

    if maya <= 0:
        res.update(verdict=ERROR, reason_code=R_FETCH_ERROR,
                   reason="Maya qiyməti düzgün deyil")
        return res

    offers = _clean_offers(offers)
    res["n"] = len(offers)

    # 1) Öz mağazan artıq satırsa — heç nə etmə. Bu yoxlama statusdan ASILI
    #    DEYİL, çünki öz mağazamızın məlumatıdır və ən vacib qorumadır.
    mine = _own_matches(offers, own_stores)
    if mine:
        return _skip(R_OWN_STORE,
                     ", ".join(f"{o['seller']} ({o['price']:.2f})" for o in mine))

    # 2) Status `active` deyilsə rəqib siyahısına ETİBAR ETMƏ.
    if (status or "").lower() != ACTIVE_STATUS:
        return _skip(R_STATUS_NOT_ACTIVE, f"status={status or 'naməlum'}")

    # 3) Normal qiymətləndirmə
    if not offers:
        endirimli = endirimli_no_seller(maya, markup)
        res.update(reason_code=R_NO_SELLER,
                   reason=f"{REASON_TEXT[R_NO_SELLER]} → maya×{money(markup):g}")
    else:
        cheapest = min(offers, key=lambda o: o["price"])
        margin = q2(cheapest["price"] - maya)
        res.update(min_price=cheapest["price"], min_seller=cheapest["seller"],
                   margin=margin)
        if margin < min_margin:
            return _skip(R_LOW_MARGIN, f"{margin:.2f} < {min_margin:.2f} ₼")
        endirimli = q2(cheapest["price"] - Decimal("0.01"))
        res["reason"] = REASON_TEXT[R_COMPETITOR]

    return recompute(res, endirimli, bot_low=bot_low, bot_high_plus=bot_high_plus)


def recompute(decision, endirimli, *, bot_low=DEFAULT_BOT_LOW,
              bot_high_plus=DEFAULT_BOT_HIGH_PLUS):
    """`endirimli` dəyişəndə TÖRƏMƏ sahələri yenidən hesabla.

    Köhnə sistemin səhvi: `/update` endirimli və köhnəni müstəqil yazırdı.
    İstifadəçi endirimlini 99.99 → 80.00 edəndə köhnə 205.60-da qalırdı
    (düzgünü 165.60) və «qəpik = maya» qaydası da pozulurdu.
    """
    maya = money(decision["maya"])
    endirimli = q2(endirimli)
    decision.update(
        endirimli=endirimli,
        kohne=kohne_qiymet(endirimli, maya),
        alt=q2(maya * money(bot_low)),
        ust=q2(endirimli + money(bot_high_plus)),
    )
    return decision


def limits_sane(alt, ust):
    """Bot limitləri məntiqli olmalıdır: alt < üst və hər ikisi müsbət.

    `executor` bunu yazmadan ƏVVƏL və oxuyandan SONRA çağırır — modal
    sahələrinin yeri dəyişsə belə tərs yazılma tutulur (tapıntı Y1).
    """
    try:
        alt, ust = money(alt), money(ust)
    except Exception:
        return False
    return alt > 0 and ust > 0 and alt < ust
