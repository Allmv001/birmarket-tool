# -*- coding: utf-8 -*-
"""Kopyala-yapışdır mətn parseri və "KOD MAYA" toplu siyahı parseri.

v2.4-də bu məntiq `marja.py` + `vision.py` arasında bölünmüşdü — burada birləşdi.
"""
import re

from .codes import DISCOUNT_RE, TAKSIT_RE, match_level, parse_price

# "R.209 68" / "R.224, 48" / "MC-22 30 ₼"
LINE_RE = re.compile(
    r"^\s*([A-Za-z][\w.\-]*)\s*[,;:\s]\s*(\d+(?:[.,]\d{1,2})?)\s*₼?\s*$")


def parse_pasted_text(text: str, code: str):
    """
    Birmarket səhifəsindən kopyalanan mətni sətirlərə ayırıb təkliflər çıxarır.
    İki format dəstəklənir:
      1) Axtarış səhifəsi: [-22 %] / 83.99 ₼ / [108.00 ₼] / 4.31 ₼ x 24 ay / AD (kod adda)
         -> qiymətlər AD-dan ƏVVƏL gəlir; cari qiymət qrupun İLK qiymətidir.
      2) Məhsul səhifəsi satıcı siyahısı: SATICI / [-22%] / [108.00 ₼] / 83.99 ₼ / Kreditlə...
         -> cari qiymət "Kreditlə"-dən əvvəlki SON qiymətdir.
    Nəticə: [{"name","price","old_price","seller"}, ...]
    """
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    offers = []

    # ---- Format 1: axtarış səhifəsi (ad sətri kod ilə uyğun gəlir) ----
    # Həm dəqiq (R.209), həm ehtimal (RAF209) uyğunluqlar götürülür —
    # səviyyə sonra evaluate() ilə təyin olunur.
    name_idxs = [i for i, l in enumerate(lines)
                 if match_level(l, code) >= 1 and parse_price(l) is None
                 and not TAKSIT_RE.search(l)]
    for i in name_idxs:
        prices = []
        for j in range(max(0, i - 6), i):
            if TAKSIT_RE.search(lines[j]) or DISCOUNT_RE.match(lines[j]):
                continue
            p = parse_price(lines[j])
            if p is not None:
                prices.append(p)
        if prices:
            offers.append({
                "name": lines[i],
                "price": prices[0],
                "old_price": prices[1] if len(prices) > 1 else None,
                "seller": None,
            })

    # ---- Format 2: satıcı siyahısı ("Kreditlə" markeri varsa) ----
    if not offers and any(l.lower().startswith("kreditlə") for l in lines):
        for i, line in enumerate(lines):
            if not line.lower().startswith("kreditlə"):
                continue
            prices, seller = [], None
            for j in range(max(0, i - 4), i):
                p = parse_price(lines[j])
                if p is not None:
                    prices.append(p)
                elif not DISCOUNT_RE.match(lines[j]) and not TAKSIT_RE.search(lines[j]):
                    seller = lines[j]
            if prices:
                offers.append({
                    "name": f"{code} — {seller}" if seller else code,
                    "price": prices[-1],
                    "old_price": prices[0] if len(prices) > 1 else None,
                    "seller": seller,
                })

    # dublikatları təmizlə
    seen, uniq = set(), []
    for o in offers:
        key = (o["name"], o["price"])
        if key not in seen:
            seen.add(key)
            uniq.append(o)
    return uniq


def parse_batch_lines(text: str):
    """Mətn siyahısını oxu: hər sətir "KOD MAYA" (məs. "R.209 68" / "R.224, 48").

    Qaytarır: (items, bad_lines).
    v4: maya <= 0 olan sətirlər də "bad" sayılır — əvvəl 0 maya bazaya düşürdü
    və o yoxlamada BÜTÜN elanlar "uyğun" görünürdü.
    """
    items, bad = [], []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        m = LINE_RE.match(line)
        if not m:
            bad.append(line.strip())
            continue
        cost = float(m.group(2).replace(",", "."))
        if cost <= 0:
            bad.append(line.strip())
            continue
        items.append({"code": m.group(1), "cost": cost, "type": ""})
    return items, bad
