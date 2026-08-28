# -*- coding: utf-8 -*-
"""Yayın dəftəri: planla → quru koşu → canlı yazı.

Bu modul `birmarket-tool`-un «Seçilənləri işlə» düyməsinin yerini tutur, amma
dörd fərqlə (27.08.2026 auditi):

  K4 — QURU KOŞU DEFOLTDUR. Köhnə sistem düyməyə basanda birbaşa canlı
       pazaryerinə yazırdı. Autopricer-in öz qaydası isə belədir:
       «Canlı fiyatlandırma geri alınamaz. Varsayılan dry-run; bir mağaza
       canlıya alınmadan önce açık insan onayı gerekir.» Eyni iş, eyni
       pazaryeri, eyni sahib — indi eyni qayda.

  Y2 — «MƏHSUL ARTIQ VAR» ARTIQ UĞUR SAYILMIR. Köhnə axın `{exists:true}`
       görəndə «✅» yazıb bot limitlərinə keçirdi; məhsul mağazada YANLIŞ
       qiymətlə qalırdı. İndi bu hal `needs_review` vəziyyətidir.

  O3 — YAZMADAN ƏVVƏL YENİDƏN YOXLAMA. Analiz səhər, koşu günorta olsa
       rəqib qiyməti dəyişmiş ola bilər. Hər məhsul üçün yazmadan dərhal
       əvvəl katalog yenidən oxunur; qərar dəyişibsə yazılmır.

  O5 — TƏKRAR CƏHD. `attempts` sayğacı dəftərdədir; uğursuz məhsul növbədə
       qalır və `retry_failed()` ilə yenidən götürülür.
"""
from decimal import Decimal

from . import pricing
from .catalog import fetch_many, fetch_product, parse_input
from .money import money, to_float
from .services import now_str

# ------------------------------------------------------------------ vəziyyətlər
PLANNED = "planned"            # qərar verilib, hələ heç nə yazılmayıb
DRY_RUN = "dry_run"            # quru koşudan keçib, canlıya hazırdır
LIVE = "live"                  # pazaryerinə yazılıb
FAILED = "failed"              # cəhd olundu, alınmadı
SKIPPED = "skipped"            # qayda ilə keçilir (öz mağaza, aşağı marja, status)
NEEDS_REVIEW = "needs_review"  # insan baxmalıdır (artıq var / qiymət dəyişib)

OPEN_STATES = (PLANNED, DRY_RUN, FAILED)

#: Yazmadan əvvəl yenidən yoxlamada bu qədər fərq «dəyişməyib» sayılır.
RECHECK_TOLERANCE = Decimal("0.01")

MAX_ATTEMPTS = 3


# ------------------------------------------------------------------ dəftər
def _pub_fields(decision, item, name=""):
    return {
        "url": item.get("url") or "",
        "name": name or "",
        "cost": to_float(decision["maya"]),
        "price": to_float(decision.get("kohne")),
        "discount": to_float(decision.get("endirimli")),
        "bot_low": to_float(decision.get("alt")),
        "bot_high": to_float(decision.get("ust")),
        "qty": int(decision.get("qty") or pricing.DEFAULT_QTY),
        "verdict": decision["verdict"],
        "reason_code": decision["reason_code"],
        "reason": decision["reason"],
    }


def upsert(con, product_id, store, fields, state, job_id=None):
    """Dəftərə yaz. Eyni (məhsul, mağaza) varsa YENİLƏ — ikinci sətir yaratma.

    `UNIQUE(product_id, store)` idempotentliyin təməlidir: eyni siyahını iki
    dəfə planlasan da bir sətir qalır.
    """
    stamp = now_str()
    cols = dict(fields, state=state, updated_at=stamp, job_id=job_id)
    existing = con.execute(
        "SELECT id FROM publications WHERE product_id=? AND store=?",
        (str(product_id), store)).fetchone()
    if existing:
        sets = ", ".join(f"{k}=?" for k in cols)
        con.execute(f"UPDATE publications SET {sets} WHERE id=?",
                    (*cols.values(), existing["id"]))
        return existing["id"]
    cols["created_at"] = stamp
    cols["product_id"] = str(product_id)
    cols["store"] = store
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    cur = con.execute(f"INSERT INTO publications ({names}) VALUES ({marks})",
                      tuple(cols.values()))
    return cur.lastrowid


def get(con, pub_id):
    row = con.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone()
    return dict(row) if row else None


def rows(con, *, states=None, ids=None, store=None, limit=500):
    sql = "SELECT * FROM publications"
    where, args = [], []
    if states:
        where.append("state IN (%s)" % ", ".join("?" for _ in states))
        args += list(states)
    if ids:
        where.append("id IN (%s)" % ", ".join("?" for _ in ids))
        args += list(ids)
    if store is not None:
        where.append("store=?")
        args.append(store)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in con.execute(sql, args)]


def mark(con, pub_id, state, *, error=None, bump_attempt=False, **fields):
    """Vəziyyəti dəyiş və dəftəri möhürlə."""
    cols = dict(fields, state=state, updated_at=now_str())
    if error is not None:
        cols["last_error"] = str(error)[:500]
    sets = ", ".join(f"{k}=?" for k in cols)
    extra = ", attempts=attempts+1" if bump_attempt else ""
    con.execute(f"UPDATE publications SET {sets}{extra} WHERE id=?",
                (*cols.values(), pub_id))
    con.commit()


def counts(con):
    out = {s: 0 for s in (PLANNED, DRY_RUN, LIVE, FAILED, SKIPPED, NEEDS_REVIEW)}
    for r in con.execute("SELECT state, COUNT(*) n FROM publications GROUP BY state"):
        out[r["state"]] = r["n"]
    return out


# ------------------------------------------------------------------ qaydalar
def settings_from(con, get_setting, get_float_setting, get_int_setting):
    """Ayarlar səhifəsindən qiymət qaydalarını oxu."""
    own = get_setting(con, "own_stores", ", ".join(pricing.DEFAULT_OWN_STORES)) or ""
    return {
        "own_stores": [x.strip() for x in own.split(",") if x.strip()],
        "min_margin": money(get_float_setting(con, "min_margin",
                                              float(pricing.DEFAULT_MIN_MARGIN))),
        "markup": money(get_float_setting(con, "markup", float(pricing.DEFAULT_MARKUP))),
        "bot_low": money(get_float_setting(con, "bot_low",
                                           float(pricing.DEFAULT_BOT_LOW))),
        "bot_high_plus": money(get_float_setting(con, "bot_high_plus",
                                                 float(pricing.DEFAULT_BOT_HIGH_PLUS))),
        "qty": get_int_setting(con, "publish_qty", pricing.DEFAULT_QTY),
        "store": get_setting(con, "publish_store", "") or "",
        "bot_url": get_setting(con, "bot_url", "") or "",
    }


def decide(item, fetched, opts):
    """Bir məhsul üçün qərar — katalog cavabı + qaydalar."""
    return pricing.evaluate(
        fetched.get("offers") or [], item["maya"],
        status=fetched.get("status"),
        fetch_error=fetched.get("error"),
        own_stores=opts["own_stores"], min_margin=opts["min_margin"],
        markup=opts["markup"], bot_low=opts["bot_low"],
        bot_high_plus=opts["bot_high_plus"], qty=opts["qty"])


def plan(con, text, opts, *, job_id=None, allow_ambiguous=False):
    """Mətndən planı qur: parse → katalog → qərar → dəftər.

    Qaytarır: {"planned": n, "skipped": n, "errors": [(sətir, mesaj)], "ids": [...]}
    """
    items, errors = parse_input(text, allow_ambiguous=allow_ambiguous)
    items = [it for it in items if it.get("maya") is not None]
    if not items:
        return {"planned": 0, "skipped": 0, "errors": errors, "ids": []}

    fetched = {f["id"]: f for f in fetch_many([it["id"] for it in items])}
    store = opts["store"]
    planned = skipped = 0
    ids = []

    for it in items:
        f = fetched.get(it["id"]) or {"id": it["id"], "error": "katalog cavabı yoxdur"}
        decision = decide(it, f, opts)
        state = PLANNED if decision["verdict"] == pricing.OK else SKIPPED
        pub_id = upsert(con, it["id"], store,
                        _pub_fields(decision, it, f.get("name", "")),
                        state, job_id=job_id)
        ids.append(pub_id)
        if state == PLANNED:
            planned += 1
        else:
            skipped += 1
    con.commit()
    return {"planned": planned, "skipped": skipped, "errors": errors, "ids": ids}


def set_discount(con, pub_id, endirimli, opts):
    """İstifadəçi endirimli qiyməti əl ilə dəyişdi — TÖRƏMƏLƏRİ yenidən hesabla.

    Tapıntı Y3: köhnə `/update` endirimli və köhnəni müstəqil yazırdı, ona görə
    əl ilə düzəliş «köhnə = 2×endirimli» və «qəpik = maya» qaydalarını pozurdu.
    """
    row = get(con, pub_id)
    if not row:
        return None
    decision = {"maya": money(row["cost"]), "qty": row["qty"] or opts["qty"]}
    pricing.recompute(decision, endirimli,
                      bot_low=opts["bot_low"], bot_high_plus=opts["bot_high_plus"])
    mark(con, pub_id, row["state"],
         discount=to_float(decision["endirimli"]), price=to_float(decision["kohne"]),
         bot_low=to_float(decision["alt"]), bot_high=to_float(decision["ust"]))
    return get(con, pub_id)


# ------------------------------------------------------------------ quru koşu
def dry_run(con, ids, opts, *, allow_no_seller=False):
    """Heç nəyə toxunmadan «nə yazılacaq» hesabatı çıxar və vəziyyəti möhürlə.

    Brauzer AÇILMIR. Məqsəd: canlı yazıdan əvvəl insanın son mətni görməsi.

    `allow_no_seller` — rəqibsiz məhsullar üçün AÇIQ təsdiq. Defolt bağlıdır.
    Səbəb (28.08.2026, üretim verisi): incelenen 173 sətirin 13-ü (%7.5)
    rəqibsiz idi və qiymət TAMAMİLƏ `maya × markup` düsturundan gəlirdi.
    Bu daldakı səhv görünmür — müqayisə ediləcək rəqib yoxdur. Ona görə
    bu sətirlər ayrıca təsdiq istəyir, sükutla keçmir.
    """
    report, ok, no_seller = [], 0, 0
    for row in rows(con, ids=ids):
        problems = []
        if row["verdict"] != pricing.OK:
            problems.append(f"qərar {row['verdict']} ({row['reason']})")
        if row["reason_code"] == pricing.R_NO_SELLER:
            no_seller += 1
            if not allow_no_seller:
                problems.append(
                    "rəqibsiz qiymət (maya×markup) — çarpaz yoxlama yoxdur, "
                    "açıq təsdiq lazımdır")
        if not pricing.limits_sane(row["bot_low"], row["bot_high"]):
            problems.append(f"bot limitləri məntiqsiz: alt={row['bot_low']} "
                            f"üst={row['bot_high']}")
        if (row["discount"] or 0) <= 0 or (row["price"] or 0) <= 0:
            problems.append("qiymət boşdur")
        elif (row["discount"] or 0) <= (row["cost"] or 0):
            problems.append(f"endirimli ({row['discount']}) mayadan ({row['cost']}) "
                            f"aşağıdır — zərərinə satış")
        if row["attempts"] >= MAX_ATTEMPTS:
            problems.append(f"{row['attempts']} uğursuz cəhd — əl ilə baxılmalıdır")

        report.append({
            "id": row["id"], "product_id": row["product_id"], "name": row["name"],
            "cost": row["cost"], "discount": row["discount"], "price": row["price"],
            "bot_low": row["bot_low"], "bot_high": row["bot_high"], "qty": row["qty"],
            "problems": problems,
            "action": ("YAZILACAQ" if not problems else "YAZILMAYACAQ"),
        })
        if problems:
            mark(con, row["id"], NEEDS_REVIEW, error="; ".join(problems))
        else:
            mark(con, row["id"], DRY_RUN)
            ok += 1
    con.commit()
    return {"ready": ok, "blocked": len(report) - ok, "rows": report,
            "no_seller": no_seller,
            "blocked_ids": [r["id"] for r in report if r["problems"]]}


# ------------------------------------------------------------------ canlı yazı
def _recheck(row, opts):
    """Yazmadan dərhal əvvəl kataloqu yenidən oxu (tapıntı O3).

    Qaytarır: (icazə_var, mesaj, yeni_qərar)
    """
    fresh = fetch_product(int(row["product_id"]))
    decision = decide({"maya": money(row["cost"]), "url": row["url"]}, fresh, opts)
    if decision["verdict"] != pricing.OK:
        return False, f"yenidən yoxlama: {decision['reason']}", decision
    new_disc = decision["endirimli"]
    old_disc = money(row["discount"] or 0)
    if abs(new_disc - old_disc) > RECHECK_TOLERANCE:
        return False, (f"rəqib qiyməti dəyişib: planlanan {old_disc:.2f} → "
                       f"indiki {new_disc:.2f}. Yenidən planla."), decision
    return True, "", decision


def execute(con, ids, opts, runner, *, live=False, log=print,
            stop_flag=lambda: False, recheck=True, allow_no_seller=False):
    """Dəftərdəki sətirləri pazaryerinə yaz.

    `live=False` (DEFOLT) — heç nə yazılmır, yalnız quru koşu hesabatı. Canlı
    yazı üçün çağıran tərəf açıq şəkildə `live=True` verməlidir (tapıntı K4).

    `runner` — `executor.Runner` nümunəsi (testdə saxta obyekt ola bilər).
    """
    if not live:
        return dry_run(con, ids, opts, allow_no_seller=allow_no_seller)

    eligible = [r for r in rows(con, ids=ids) if r["state"] == DRY_RUN]
    if not eligible:
        log("⚠️ Canlı yazı üçün hazır sətir yoxdur — əvvəlcə quru koşu edin.")
        return {"written": 0, "failed": 0, "skipped": 0}

    written = failed = skipped = 0
    failed_ids, review_ids = [], []
    for i, row in enumerate(eligible, 1):
        if stop_flag():
            log("⛔ Dayandırıldı")
            break
        code = row["product_id"]
        log(f"[{i}/{len(eligible)}] {code} {(row['name'] or '')[:40]}")

        if row["attempts"] >= MAX_ATTEMPTS:
            mark(con, row["id"], NEEDS_REVIEW, error="maksimum cəhd sayı keçildi")
            review_ids.append(row["product_id"])
            skipped += 1
            log("   ⏭️ maksimum cəhd sayı keçildi")
            continue

        if recheck:
            allowed, why, _ = _recheck(row, opts)
            if not allowed:
                mark(con, row["id"], NEEDS_REVIEW, error=why)
                review_ids.append(row["product_id"])
                skipped += 1
                log(f"   ⏭️ {why}")
                continue

        # Bot limitləri hər yazıdan ƏVVƏL məntiq yoxlamasından keçir (tapıntı Y1)
        if not pricing.limits_sane(row["bot_low"], row["bot_high"]):
            mark(con, row["id"], NEEDS_REVIEW,
                 error=f"bot limitləri məntiqsiz: {row['bot_low']} / {row['bot_high']}")
            review_ids.append(row["product_id"])
            skipped += 1
            log("   ⏭️ bot limitləri məntiqsiz")
            continue

        try:
            result = runner.publish_one(row)
        except Exception as e:                              # noqa: BLE001
            mark(con, row["id"], FAILED, error=e, bump_attempt=True)
            failed_ids.append(row["product_id"])
            failed += 1
            log(f"   ❌ {e}")
            continue

        if result.get("needs_review"):
            mark(con, row["id"], NEEDS_REVIEW, error=result.get("message"),
                 bump_attempt=True)
            review_ids.append(row["product_id"])
            skipped += 1
            log(f"   ⚠️ {result.get('message')}")
        elif result.get("ok"):
            mark(con, row["id"], LIVE, error=None, bump_attempt=True)
            written += 1
            log(f"   ✅ yazıldı {result.get('detail', '')}")
        else:
            mark(con, row["id"], FAILED, error=result.get("message"), bump_attempt=True)
            failed_ids.append(row["product_id"])
            failed += 1
            log(f"   ❌ {result.get('message')}")

    # Tapıntı (28.08.2026 üretim raporu): köhnə alətdə hər məhsulda xəta
    # yutulurdu və iş «bitdi» görünürdü — 246 məhsulun heç biri yazılmamışdı.
    # Ona görə xülasə HƏMİŞƏ yazılır və uğursuzlar ADI İLƏ sadalanır.
    log(f"XÜLASƏ: {written} yazıldı · {failed} alınmadı · {skipped} keçildi "
        f"(cəmi {len(eligible)})")
    if failed_ids:
        log(f"❌ Alınmayanlar: {', '.join(failed_ids)}")
    if review_ids:
        log(f"⚠️ Baxılmalı: {', '.join(review_ids)}")
    remaining = [r["id"] for r in rows(con, ids=ids) if r["state"] == DRY_RUN]
    if remaining:
        log(f"⏸️ {len(remaining)} sətir hələ növbədədir — «Canlı yazı» ilə davam et.")
    con.commit()
    return {"written": written, "failed": failed, "skipped": skipped,
            "failed_ids": failed_ids, "review_ids": review_ids,
            "remaining": len(remaining)}


def retry_failed(con, store=None):
    """Uğursuz sətirləri yenidən növbəyə qoy (cəhd limiti aşılmayıbsa)."""
    back = 0
    for row in rows(con, states=[FAILED], store=store):
        if row["attempts"] < MAX_ATTEMPTS:
            mark(con, row["id"], DRY_RUN, error=None)
            back += 1
    con.commit()
    return back


def pending(con, store=None):
    """Yarımçıq qalan iş — kəsilmiş koşudan sonra «harada qaldım?» sualının cavabı.

    Tapıntı (28.08.2026 üretim raporu): köhnə alətdə ilerləmə yalnız yaddaşda
    idi. 246 məhsulluq iş kəsildi, istifadəçi harada qaldığını PROQRAMDAN
    öyrənə bilmədi, ~173 rəqəmini əl ilə təxmin etdi və işi baştan başlatdı —
    ilk ~173 məhsul İKİNCİ DƏFƏ işləndi.

    Burada belə bir şey mümkün deyil: hər məhsulun vəziyyəti yazıldığı anda
    SQLite-a commit olunur. Yenidən koşuda yalnız `dry_run` sətirlər götürülür,
    `live` olanlar toxunulmur.
    """
    open_rows = rows(con, states=[DRY_RUN], store=store)
    return {
        "ready": len(open_rows),
        "ids": [r["id"] for r in open_rows],
        "failed": len(rows(con, states=[FAILED], store=store)),
        "review": len(rows(con, states=[NEEDS_REVIEW], store=store)),
    }
