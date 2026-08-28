# -*- coding: utf-8 -*-
"""Biznes əməliyyatları: yoxlama yaratmaq, avtomatik axtarış, link toplusu.

Buradakı funksiyalar Flask-dan asılı DEYİL — hamısı açıq bir `con` bağlantısı
alır. Beləcə həm sorğu içindən, həm arxa fon thread-indən eyni kod işləyir
(v2.4-də bu məntiq route-ların içinə yayılmışdı və iki nüsxəsi vardı).
"""
from datetime import datetime

from .codes import evaluate
from .db import PICKED_COND, get_float_setting, get_int_setting
from .fetcher import auto_search, product_id


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def to_float(val, default=None):
    """Vergüllü/boşluqlu rəqəm mətnini təhlükəsiz float-a çevir; alınmasa default."""
    try:
        return float(str(val).replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


def fetch_options(con):
    """Axtarış parametrləri — Ayarlar səhifəsindən idarə olunur.

    Defolt dəyərlər BURADA YAZILMIR, `DEFAULTS`-dan gəlir. 28.08.2026-da
    `DEFAULTS["max_pages"]` 3-dən 5-ə qaldırıldı, amma bu funksiyada eyni
    rəqəm ikinci dəfə əl ilə yazılmışdı və axtarış yolu köhnə dəyəri
    işlətməyə davam etdi: istifadəçi R.209 üçün yenə 5 elan gördü,
    halbuki 7 var idi. `views.py` onsuz da `DEFAULTS`-a baxırdı - fərqli
    olan yalnız bu funksiya idi.
    """
    from . import DEFAULTS
    return {
        "max_pages": get_int_setting(con, "max_pages", DEFAULTS["max_pages"]),
        "max_variants": get_int_setting(con, "max_variants", DEFAULTS["max_variants"]),
        "delay": get_float_setting(con, "request_delay", DEFAULTS["request_delay"]),
    }


def create_check(con, code, cost, threshold, ptype="", note="", brands="", image=None):
    """Yeni yoxlama yaz və id qaytar."""
    cur = con.execute(
        "INSERT INTO checks(created_at,code,cost,ptype,note,threshold,image,brands) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (now_str(), code, cost, ptype or "", note or "", threshold, image, brands or ""))
    con.commit()
    return cur.lastrowid


def run_autosearch(con, check_id, fast=False, should_stop=None):
    """Birmarket-i avtomatik axtar; yeniləri yaz, mövcudların qiymətini yenilə,
    dəyişənləri price_history-yə qeyd et.

    fast — toplu rejim: az variant + yalnız 1 səhifə (böyük partiyalar üçün sürət).
    Qaytarır: {"added","updated","unchanged","skipped","errors","message"}
    """
    c = con.execute("SELECT * FROM checks WHERE id=?", (check_id,)).fetchone()
    if not c:
        raise LookupError(f"Yoxlama tapılmadı: {check_id}")

    opts = fetch_options(con)
    offers, errors = auto_search(
        c["code"], c["brands"] or "", c["ptype"] or "",
        max_pages=1 if fast else opts["max_pages"],
        max_variants=3 if fast else opts["max_variants"],
        delay=opts["delay"], should_stop=should_stop)

    existing = {product_id(r["url"]): dict(r) for r in con.execute(
        "SELECT * FROM offers WHERE check_id=? AND url IS NOT NULL", (check_id,))}
    stamp = now_str()
    added = updated = unchanged = skipped = 0

    for o in offers:
        level, margin, ok = evaluate(c["cost"], c["threshold"], o["price"],
                                     o["name"], c["code"])
        if level == 0:
            skipped += 1        # kod uyğun deyil — səs-küy, bazaya yazılmır
            continue
        pid = product_id(o["url"])
        row = existing.get(pid)
        if row:
            if abs((row["price"] or 0) - o["price"]) >= 0.01:
                con.execute(
                    "INSERT INTO price_history(offer_id,changed_at,old_price,new_price) "
                    "VALUES (?,?,?,?)", (row["id"], stamp, row["price"], o["price"]))
                con.execute(
                    "UPDATE offers SET price=?, old_price=?, margin=?, is_match=? WHERE id=?",
                    (o["price"], o.get("old_price"), margin, int(ok), row["id"]))
                updated += 1
            else:
                unchanged += 1
            continue
        con.execute(
            "INSERT INTO offers(check_id,name,seller,price,old_price,url,"
            "code_match,margin,is_match) VALUES (?,?,?,?,?,?,?,?,?)",
            (check_id, o["name"], o.get("seller"), o["price"],
             o.get("old_price"), o["url"], level, margin, int(ok)))
        added += 1
    con.commit()

    msg = (f"🤖 Axtarış bitdi: {added} yeni elan, {updated} qiymət yeniləndi, "
           f"{unchanged} dəyişməz, {skipped} kod-uyğunsuz atıldı.")
    if errors:
        msg += (f" ⚠️ {len(errors)} sorğu alınmadı — şəbəkə problemi varsa, "
                "kopyala-yapışdır üsulu ehtiyatdadır.")
    return {"added": added, "updated": updated, "unchanged": unchanged,
            "skipped": skipped, "errors": errors, "message": msg}


def check_summary(con, check_id):
    """Bir yoxlamanın nəticə xülasəsi (toplu siyahılar üçün)."""
    row = con.execute(
        "SELECT COUNT(*) AS found, "
        " SUM(CASE WHEN is_match=1 THEN 1 ELSE 0 END) AS hits, "
        " MAX(CASE WHEN is_match=1 THEN margin END) AS best "
        "FROM offers WHERE check_id=?", (check_id,)).fetchone()
    return {"found": row["found"] or 0, "hits": row["hits"] or 0, "best": row["best"]}


def active_links(con, check_id):
    """Bir yoxlamanın aktiv (seçilmiş və ya avtomatik uyğun) linkləri, marjaya görə."""
    return [r["url"] for r in con.execute(
        "SELECT o.url, MAX(o.margin) AS m FROM offers o "
        f"WHERE o.check_id=? AND o.url IS NOT NULL AND {PICKED_COND} "
        "GROUP BY o.url ORDER BY m DESC", (check_id,))]


def wa_block(links, cost):
    """WhatsApp-a hazır blok: hər linkin ALTINDA maya qiyməti, aralarda boş sətir."""
    money = f"{round(float(cost), 2):g} ₼"
    return "\n\n".join(f"{u}\n{money}" for u in links if u)
