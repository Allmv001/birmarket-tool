# -*- coding: utf-8 -*-
"""HTML route-ları.

v2.4-də düzəldilən çökmələr (hamısı real test client ilə təsdiqlənib):
  * /check/<id>/export.xlsx silinmiş yoxlama üçün 500 verirdi  -> indi 404
  * POST /settings rəqəm olmayan hədd üçün 500 verirdi         -> indi xəbərdarlıq
  * POST /offer/<id>/delete olmayan təklif üçün 500 verirdi    -> indi 404
"""
import os
import re
from datetime import datetime
from urllib.parse import quote

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, send_file, send_from_directory,
                   url_for)

from . import DEFAULTS, exports, jobs
from .codes import evaluate, search_variants
from .db import (PICKED_COND, db, get_float_setting, get_int_setting,
                 get_setting, set_setting)
from .parsing import parse_batch_lines, parse_pasted_text
from .services import (active_links, check_summary, create_check, fetch_options,
                       now_str, run_autosearch, to_float)
from .wa_parser import parse_whatsapp

bp = Blueprint("views", __name__)


# ------------------------------------------------------------------ köməkçi
def threshold_of(con):
    return get_float_setting(con, "threshold", DEFAULTS["threshold"])


def wa_number_of(con):
    raw = get_setting(con, "wa_number", DEFAULTS["wa_number"])
    return re.sub(r"\D", "", raw or "") or DEFAULTS["wa_number"]


def get_check_or_404(con, check_id):
    row = con.execute("SELECT * FROM checks WHERE id=?", (check_id,)).fetchone()
    if not row:
        abort(404)
    return row


def _parse_ids(s):
    return [int(x) for x in re.findall(r"\d+", s or "")]


def _safe_filename(name):
    return re.sub(r"[^\w.\-]", "_", os.path.basename(name or "")) or "file"


def _session_groups(checks, links_map):
    """Yoxlamaları sessiyalara qruplaşdır: eyni mənbə + <=30 dəq fasilə = bir axtarış.
    Panel bu qrupları 'tarix · saat' başlığı ilə göstərir."""
    def src_of(c):
        n = (c["note"] or "").strip()
        if n.startswith("WhatsApp"):
            return n
        if n.startswith("Toplu"):
            return "Toplu yoxlama"
        return "Tək yoxlama"

    groups = []
    for c in checks:                     # id DESC — ən yenidən köhnəyə
        try:
            t = datetime.strptime(c["created_at"][:16], "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            t = None
        src = src_of(c)
        g = groups[-1] if groups else None
        if (g and g["src"] == src and t and g["last_t"]
                and abs((g["last_t"] - t).total_seconds()) <= 1800):
            g["items"].append(c)
            g["last_t"] = t
        else:
            groups.append({"src": src, "title": c["created_at"], "items": [c], "last_t": t})
    for g in groups:
        g["links"] = sum(len(links_map.get(c["id"], [])) for c in g["items"])
        g["hits"] = sum(1 for c in g["items"] if links_map.get(c["id"]))
    return groups


# ------------------------------------------------------------------ panel
@bp.route("/")
def dashboard():
    con = db()
    q = (request.args.get("q") or "").strip()
    page = max(1, int(to_float(request.args.get("page"), 1) or 1))
    per = get_int_setting(con, "page_size", DEFAULTS["page_size"])

    where, params = "", []
    if q:
        where = "WHERE c.code LIKE ? COLLATE NOCASE OR c.ptype LIKE ? COLLATE NOCASE"
        params = ["%" + q + "%", "%" + q + "%"]

    total_rows = con.execute(
        "SELECT COUNT(*) n FROM checks c " + where, params).fetchone()["n"]
    pages = max(1, -(-total_rows // per))
    page = min(page, pages)

    checks = con.execute(
        "SELECT c.*, "
        " (SELECT COUNT(*) FROM offers o WHERE o.check_id=c.id AND o.is_match=1) AS hits, "
        " (SELECT COUNT(*) FROM offers o WHERE o.check_id=c.id) AS total, "
        " (SELECT MAX(margin) FROM offers o WHERE o.check_id=c.id AND o.is_match=1) AS best "
        "FROM checks c " + where + " ORDER BY c.id DESC LIMIT ? OFFSET ?",
        (*params, per, (page - 1) * per)).fetchall()

    stats = con.execute(
        "SELECT COUNT(DISTINCT c.code) AS codes, "
        " SUM(CASE WHEN o.is_match=1 THEN 1 ELSE 0 END) AS hits, "
        " MAX(CASE WHEN o.is_match=1 THEN o.margin END) AS best "
        "FROM checks c LEFT JOIN offers o ON o.check_id=c.id").fetchone()
    last_row = con.execute(
        "SELECT created_at FROM checks ORDER BY id DESC LIMIT 1").fetchone()
    last = last_row["created_at"][:10] if last_row else "—"

    ids = [c["id"] for c in checks]
    links_map = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in con.execute(
                "SELECT o.check_id, o.url, MAX(o.margin) AS m FROM offers o "
                "WHERE o.check_id IN (" + marks + ") AND o.url IS NOT NULL "
                "AND " + PICKED_COND + " "
                "GROUP BY o.check_id, o.url ORDER BY m DESC", ids):
            links_map.setdefault(r["check_id"], []).append(r["url"])

    return render_template(
        "dashboard.html", groups=_session_groups(checks, links_map), stats=stats,
        last=last, threshold=threshold_of(con), links_map=links_map,
        q=q, page=page, pages=pages, total_rows=total_rows,
        running=jobs.recent_jobs(con, 3))


# ------------------------------------------------------------------ yoxlama
@bp.route("/check/new", methods=["GET", "POST"])
def new_check():
    con = db()
    if request.method == "GET":
        return render_template("new_check.html", threshold=threshold_of(con))

    code = (request.form.get("code") or "").strip()
    cost = to_float(request.form.get("cost"))
    thr = to_float(request.form.get("threshold"), threshold_of(con))
    if not code or cost is None or cost <= 0:
        flash("⚠️ Kod və düzgün maya qiyməti daxil edin (məs. R.209 və 68).", "warn")
        return redirect(url_for("views.new_check"))
    if thr is None or thr <= 0:
        thr = threshold_of(con)

    prev = con.execute(
        "SELECT id FROM checks WHERE code=? COLLATE NOCASE ORDER BY id DESC LIMIT 1",
        (code,)).fetchone()
    if prev:
        flash("ℹ️ Bu kod əvvəl də yoxlanıb — köhnə nəticələr: /check/%d" % prev["id"], "info")

    img_name = None
    f = request.files.get("image")
    if f and f.filename:
        img_name = datetime.now().strftime("%Y%m%d%H%M%S_") + _safe_filename(f.filename)
        f.save(os.path.join(current_app.config["UPLOAD_DIR"], img_name))

    new_id = create_check(con, code, cost, thr,
                          ptype=(request.form.get("ptype") or "").strip(),
                          note=(request.form.get("note") or "").strip(),
                          brands=(request.form.get("brands") or "").strip(),
                          image=img_name)
    try:
        flash(run_autosearch(con, new_id)["message"], "ok")
    except Exception as e:
        flash("⚠️ Avtomatik axtarış alınmadı (%s) — düymə ilə yenidən cəhd edin "
              "və ya kopyala-yapışdır üsulunu işlədin." % e, "warn")
    return redirect(url_for("views.check_detail", check_id=new_id))


@bp.route("/check/<int:check_id>")
def check_detail(check_id):
    con = db()
    c = get_check_or_404(con, check_id)
    offers = con.execute(
        "SELECT o.*, (SELECT COUNT(*) FROM price_history ph WHERE ph.offer_id=o.id) "
        "AS ph_count FROM offers o WHERE o.check_id=? "
        "ORDER BY o.is_match DESC, o.code_match DESC, o.margin DESC",
        (check_id,)).fetchall()
    variants = search_variants(c["code"], c["brands"] or "", c["ptype"] or "")
    searches = [(v, "https://birmarket.az/search/" + quote(v)) for v in variants]

    # Link siyahıları burada hesablanır, şablonda YOX: Jinja-da {% set %} ilə
    # qurulan dəyişən başqa {% block %} içindən görünmür və <script> bloku
    # onları Undefined kimi alırdı (tojson filtri çökürdü).
    matched_links, all_links = [], []
    for o in offers:
        if not o["url"] or o["url"] in all_links:
            continue
        all_links.append(o["url"])
        if o["is_match"]:
            matched_links.append(o["url"])

    return render_template("check_detail.html", c=c, offers=offers, searches=searches,
                           wa_number=wa_number_of(con),
                           matched_links=matched_links, all_links=all_links,
                           limit=round(c["cost"] * c["threshold"], 2))


@bp.route("/check/<int:check_id>/autosearch", methods=["POST"])
def autosearch(check_id):
    con = db()
    get_check_or_404(con, check_id)
    try:
        flash(run_autosearch(con, check_id)["message"], "ok")
    except Exception as e:
        flash("⚠️ Avtomatik axtarış alınmadı (%s) — yenidən cəhd edin "
              "və ya kopyala-yapışdır üsulunu işlədin." % e, "warn")
    return redirect(url_for("views.check_detail", check_id=check_id))


@bp.route("/check/<int:check_id>/offer", methods=["POST"])
def add_offer(check_id):
    con = db()
    c = get_check_or_404(con, check_id)
    name = (request.form.get("name") or "").strip()
    price = to_float(request.form.get("price"))
    if not name or price is None or price <= 0:
        flash("⚠️ Məhsul adı və düzgün qiymət daxil edin.", "warn")
        return redirect(url_for("views.check_detail", check_id=check_id))
    level, margin, ok = evaluate(c["cost"], c["threshold"], price, name, c["code"])
    con.execute(
        "INSERT INTO offers(check_id,name,seller,price,old_price,url,"
        "code_match,margin,is_match) VALUES (?,?,?,?,?,?,?,?,?)",
        (check_id, name, (request.form.get("seller") or "").strip() or None, price,
         to_float(request.form.get("old_price")),
         (request.form.get("url") or "").strip() or None, level, margin, int(ok)))
    con.commit()
    if level == 0:
        flash("⚠️ '%s' adında %s kodu tapılmadı — sətir 'kod uyğun deyil' kimi "
              "qeyd olundu." % (name, c["code"]), "warn")
    elif level == 1:
        flash("≈ '%s' — kod dəqiq deyil (ehtimal). Linki açıb yoxlayın." % name, "info")
    return redirect(url_for("views.check_detail", check_id=check_id))


@bp.route("/check/<int:check_id>/paste", methods=["POST"])
def paste_offers(check_id):
    con = db()
    c = get_check_or_404(con, check_id)
    url = (request.form.get("url") or "").strip() or None
    found = parse_pasted_text(request.form.get("pasted", ""), c["code"])
    existing = {(r["name"], r["price"]) for r in con.execute(
        "SELECT name, price FROM offers WHERE check_id=?", (check_id,))}
    found = [o for o in found if (o["name"], o["price"]) not in existing]
    for o in found:
        level, margin, ok = evaluate(c["cost"], c["threshold"], o["price"],
                                     o["name"], c["code"])
        con.execute(
            "INSERT INTO offers(check_id,name,seller,price,old_price,url,"
            "code_match,margin,is_match) VALUES (?,?,?,?,?,?,?,?,?)",
            (check_id, o["name"], o.get("seller"), o["price"],
             o.get("old_price"), url, level, margin, int(ok)))
    con.commit()
    flash("%d yeni təklif əlavə olundu (dublikatlar atıldı)." % len(found) if found
          else "Yeni təklif tapılmadı — ya hamısı artıq mövcuddur, "
               "ya da sətirləri manual əlavə edin.", "ok" if found else "info")
    return redirect(url_for("views.check_detail", check_id=check_id))


@bp.route("/check/<int:check_id>/delete", methods=["POST"])
def delete_check(check_id):
    con = db()
    get_check_or_404(con, check_id)
    con.execute("DELETE FROM checks WHERE id=?", (check_id,))
    con.commit()
    flash("Yoxlama silindi.", "ok")
    return redirect(url_for("views.dashboard"))


@bp.route("/offer/<int:offer_id>/delete", methods=["POST"])
def delete_offer(offer_id):
    con = db()
    # DÜZƏLİŞ: v2.4 olmayan təklif üçün 500 verirdi (row None -> TypeError)
    row = con.execute("SELECT check_id FROM offers WHERE id=?", (offer_id,)).fetchone()
    if not row:
        abort(404)
    con.execute("DELETE FROM offers WHERE id=?", (offer_id,))
    con.commit()
    return redirect(url_for("views.check_detail", check_id=row["check_id"]))


@bp.route("/offer/<int:offer_id>/exclude", methods=["POST"])
def exclude_offer(offer_id):
    con = db()
    if not con.execute("SELECT 1 FROM offers WHERE id=?", (offer_id,)).fetchone():
        abort(404)
    con.execute("UPDATE offers SET picked=0 WHERE id=?", (offer_id,))
    con.commit()
    return redirect(request.form.get("back") or url_for("views.links_page"))


@bp.route("/check/<int:check_id>/export.xlsx")
def export_xlsx(check_id):
    con = db()
    # DÜZƏLİŞ: v2.4 silinmiş yoxlama üçün 500 verirdi
    c = get_check_or_404(con, check_id)
    offers = con.execute(
        "SELECT * FROM offers WHERE check_id=? ORDER BY is_match DESC, margin DESC",
        (check_id,)).fetchall()
    buf, filename = exports.check_report(c, offers)
    return send_file(
        buf, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------ toplu
@bp.route("/batch", methods=["GET", "POST"])
def batch():
    con = db()
    if request.method == "GET":
        return render_template("batch.html", threshold=threshold_of(con),
                               has_key=bool(get_setting(con, "api_key")),
                               history=jobs.recent_jobs(con, 8))

    thr = to_float(request.form.get("threshold"), threshold_of(con)) or threshold_of(con)
    brands = (request.form.get("brands") or "").strip()
    api_key = get_setting(con, "api_key", "")

    txt_items, bad = parse_batch_lines(request.form.get("lines", ""))
    errors = ["Sətir oxunmadı: %s (format: KOD MAYA, məs. R.209 68)" % b for b in bad]
    txt_items = [dict(it, image=None) for it in txt_items]

    image_files = []
    for f in request.files.getlist("images"):
        if not f or not f.filename:
            continue
        if not api_key:
            errors.append("%s: şəkil oxumaq üçün Ayarlarda Claude API açarı yoxdur."
                          % f.filename)
            continue
        img_name = datetime.now().strftime("%Y%m%d%H%M%S%f_") + _safe_filename(f.filename)
        f.save(os.path.join(current_app.config["UPLOAD_DIR"], img_name))
        image_files.append((img_name, f.filename))

    if not txt_items and not image_files:
        for e in errors:
            flash(e, "warn")
        if not errors:
            flash("⚠️ Heç nə daxil edilməyib — şəkil seçin və ya KOD MAYA sətirləri yazın.",
                  "warn")
        return redirect(url_for("views.batch"))

    job_id = jobs.create_job(con, "batch", total=len(txt_items) + len(image_files),
                             img_total=len(image_files), errors=errors)
    jobs.start_batch(current_app.config["DB_PATH"], job_id, txt_items, image_files,
                     thr, brands, current_app.config["UPLOAD_DIR"])
    return redirect(url_for("views.batch_job", job_id=job_id))


@bp.route("/batch/job/<job_id>")
def batch_job(job_id):
    con = db()
    job = jobs.get_job(con, job_id)
    if not job:
        flash("ℹ️ Bu tapşırıq tapılmadı — tamamlanmış nəticələr Paneldədir.", "info")
        return redirect(url_for("views.batch"))
    return render_template("batch.html", threshold=threshold_of(con),
                           has_key=bool(get_setting(con, "api_key")),
                           job=job, results=job["results"], errors=job["errors"],
                           history=jobs.recent_jobs(con, 8))


@bp.route("/batch/job/<job_id>/cancel", methods=["POST"])
def batch_cancel(job_id):
    con = db()
    if not jobs.get_job(con, job_id):
        abort(404)
    jobs.cancel_job(con, job_id)
    flash("Tapşırıq dayandırılır — cari kod bitəndən sonra duracaq.", "info")
    return redirect(url_for("views.batch_job", job_id=job_id))


# ------------------------------------------------------------------ whatsapp
@bp.route("/whatsapp", methods=["GET", "POST"])
def whatsapp():
    """WhatsApp elan mətnindən toplu yoxlama.

    Qrup hər gün dəyişə bilər: mətni hansı qrupdan kopyalasanız, o qrupun adını
    'Mənbə' sahəsinə yazın — hər yoxlamanın qeydində saxlanılır.
    Kodu oxunmayan sətirlər ayrıca 'Google Lens' siyahısında göstərilir.
    """
    con = db()
    known = [r["name"] for r in con.execute(
        "SELECT name FROM wa_groups ORDER BY updated_at DESC")]
    if request.method == "GET":
        return render_template("whatsapp.html", threshold=threshold_of(con),
                               wa_number=wa_number_of(con), known_groups=known)

    thr = to_float(request.form.get("threshold"), threshold_of(con)) or threshold_of(con)
    brands = (request.form.get("brands") or "").strip()
    source = (request.form.get("source") or "").strip() or "WhatsApp"
    limit = int(to_float(request.form.get("limit"), 0) or 0)
    text = request.form.get("text", "")

    # qrup arxivi — mətn yapışdırılıbsa arxivə əlavə olunur; mətn BOŞdursa,
    # qrupun adını yazmaq kifayətdir: saxlanmış arxivdən oxunur
    use_text = text
    if source and source != "WhatsApp":
        row = con.execute("SELECT text FROM wa_groups WHERE name=?", (source,)).fetchone()
        if text.strip():
            merged = ((row["text"] + "\n") if row and row["text"] else "") + text
            if len(merged) > 800000:            # arxiv həddi ~800KB
                merged = merged[-800000:]
            con.execute("INSERT INTO wa_groups(name,text,updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(name) DO UPDATE SET text=excluded.text, "
                        "updated_at=excluded.updated_at", (source, merged, now_str()))
            con.commit()
            use_text = merged
        elif row and row["text"]:
            use_text = row["text"]

    items, unknown = parse_whatsapp(use_text)
    if limit > 0:
        items = items[-limit:]               # SON N məhsul (ən yenilər)
        unknown = unknown[-limit:]

    if not items and not unknown:
        flash("⚠️ Mətndən heç bir kod/qiymət oxunmadı — mesajları olduğu kimi "
              "yapışdırdığınızdan əmin olun.", "warn")
        return render_template("whatsapp.html", threshold=thr, source=source,
                               wa_number=wa_number_of(con), known_groups=known)

    results, errors = [], []
    for it in items:
        cid = create_check(con, it["code"], it["cost"], thr,
                           ptype=it.get("type", ""), note="WhatsApp: " + source,
                           brands=brands)
        try:
            run_autosearch(con, cid, fast=True)
        except Exception as e:
            errors.append("%s: axtarış alınmadı (%s)" % (it["code"], e))
        results.append({"id": cid, "code": it["code"], "cost": it["cost"],
                        "type": it.get("type", ""), "links": active_links(con, cid),
                        **check_summary(con, cid)})

    known = [r["name"] for r in con.execute(
        "SELECT name FROM wa_groups ORDER BY updated_at DESC")]
    return render_template("whatsapp.html", threshold=thr, results=results,
                           unknown=unknown, errors=errors, source=source,
                           wa_number=wa_number_of(con), known_groups=known)


# ------------------------------------------------------------------ linklər
def links_data(con, ids, day):
    """Seçilmiş yoxlamaların (və ya bir günün) aktiv linklərini qruplu qaytar."""
    if ids:
        marks = ",".join("?" * len(ids))
        checks = con.execute(
            "SELECT * FROM checks WHERE id IN (" + marks + ") ORDER BY id", ids).fetchall()
    else:
        checks = con.execute(
            "SELECT * FROM checks WHERE created_at LIKE ? ORDER BY id",
            (day + "%",)).fetchall()

    # DÜZƏLİŞ: v2.4 "GROUP BY o.url" işlədirdi və SQLite qrupdan TƏSADÜFİ sətri
    # götürürdü — cədvəldə görünən sətir ilə ✕ düyməsinin sildiyi sətir fərqli
    # ola bilirdi. İndi hər url üçün ən yüksək marjalı sətir müəyyən seçilir.
    picked2 = PICKED_COND.replace("o.", "o2.")
    groups, total = [], 0
    for c in checks:
        offers = con.execute(
            "SELECT o.* FROM offers o "
            "WHERE o.check_id=? AND o.url IS NOT NULL AND " + PICKED_COND + " "
            "  AND o.id = (SELECT o2.id FROM offers o2 "
            "              WHERE o2.check_id=o.check_id AND o2.url=o.url "
            "                AND " + picked2 + " "
            "              ORDER BY o2.margin DESC, o2.id ASC LIMIT 1) "
            "ORDER BY o.margin DESC", (c["id"],)).fetchall()
        if offers:
            groups.append({"check": c, "offers": offers})
            total += len(offers)
    return groups, total


@bp.route("/links")
def links_page():
    con = db()
    ids = _parse_ids(request.args.get("ids", ""))
    day = (request.args.get("day") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    groups, total = links_data(con, ids, day)

    lines = []
    for grp in groups:
        maya = "%g ₼" % grp["check"]["cost"]
        for o in grp["offers"]:
            lines += [o["url"], maya, ""]
    return render_template("links.html", groups=groups, total=total,
                           copy_text="\n".join(lines).strip(), day=day,
                           ids=",".join(map(str, ids)))


@bp.route("/links/export.xlsx")
def links_export():
    con = db()
    ids = _parse_ids(request.args.get("ids", ""))
    day = (request.args.get("day") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    groups, _ = links_data(con, ids, day)
    label = ("%d kod" % len(groups)) if ids else day
    buf, filename = exports.links_report(groups, label)
    return send_file(
        buf, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------ ayarlar
@bp.route("/settings", methods=["GET", "POST"])
def settings():
    con = db()
    if request.method == "POST":
        # v4.1 — YAYIN QAYDALARI. Köhnə `birmarket-tool`-da bu dörd sabit koda
        # yazılmışdı (1.70 / 1.25 / +20 / 4 ₼). Burada ayardır, amma UI-siz
        # qalmışdı: `bot_url` heç yerdən verilə bilmirdi, ona görə canlı yazı
        # HƏMİŞƏ «Bot ünvanı təyin olunmayıb» qaytarırdı. 28.08.2026-da tapıldı.
        for key in ("own_stores", "publish_store"):
            raw = request.form.get(key)
            if raw is not None:
                set_setting(con, key, raw.strip())
        bot_url = (request.form.get("bot_url") or "").strip()
        if bot_url or request.form.get("clear_bot_url"):
            set_setting(con, "bot_url", bot_url)
        for name, lo, hi in (("min_margin", 0.0, 1000.0), ("markup", 1.0, 10.0),
                             ("bot_low", 0.1, 10.0), ("bot_high_plus", 0.0, 1000.0)):
            v = to_float(request.form.get(name))
            if v is not None:
                set_setting(con, name, min(max(v, lo), hi))
        pq = to_float(request.form.get("publish_qty"))
        if pq is not None:
            set_setting(con, "publish_qty", int(min(max(pq, 1), 9999)))
        # DƏRHAL commit: aşağıdakı marja yoxlaması `return` edərsə bu yazılar
        # commit-siz qalıb geri alınırdı. Testdə tutuldu (28.08.2026).
        con.commit()

        # DÜZƏLİŞ: v2.4 float("abc") ilə 500 verirdi
        pct = to_float(request.form.get("margin_pct"))
        if pct is None or pct < 0:
            flash("⚠️ Marja həddi rəqəm olmalıdır (məs. 20). Dəyişiklik edilmədi.", "warn")
            return redirect(url_for("views.settings"))
        set_setting(con, "threshold", 1 + pct / 100)

        wa = re.sub(r"\D", "", request.form.get("wa_number", ""))
        if wa:
            set_setting(con, "wa_number", wa)

        new_key = (request.form.get("api_key") or "").strip()
        if new_key:                       # boş buraxılsa mövcud açar qalır
            set_setting(con, "api_key", new_key)
        if request.form.get("clear_key"):
            set_setting(con, "api_key", "")

        for name, lo, hi in (("max_pages", 1, 10), ("max_variants", 1, 12),
                             ("page_size", 5, 200)):
            v = to_float(request.form.get(name))
            if v is not None:
                set_setting(con, name, int(min(max(v, lo), hi)))
        delay = to_float(request.form.get("request_delay"))
        if delay is not None:
            set_setting(con, "request_delay", min(max(delay, 0.2), 10.0))

        con.commit()
        flash("Yadda saxlanıldı.", "ok")
        return redirect(url_for("views.settings"))

    key = get_setting(con, "api_key", "") or ""
    opts = fetch_options(con)
    return render_template(
        "settings.html",
        margin_pct=round((threshold_of(con) - 1) * 100, 1),
        has_key=bool(key), wa_number=wa_number_of(con),
        key_hint=(key[:10] + "…" + key[-4:]) if len(key) > 16 else "",
        page_size=get_int_setting(con, "page_size", DEFAULTS["page_size"]),
        pub=_publish_settings(con), **opts)


def _publish_settings(con):
    """Yayın səhifəsinin istifadə etdiyi qaydalar — Ayarlar formu üçün xam dəyərlər."""
    from . import pricing
    return {
        "own_stores": get_setting(con, "own_stores",
                                  ", ".join(pricing.DEFAULT_OWN_STORES)),
        "publish_store": get_setting(con, "publish_store", "") or "",
        "bot_url": get_setting(con, "bot_url", "") or "",
        "min_margin": get_float_setting(con, "min_margin",
                                        float(pricing.DEFAULT_MIN_MARGIN)),
        "markup": get_float_setting(con, "markup", float(pricing.DEFAULT_MARKUP)),
        "bot_low": get_float_setting(con, "bot_low", float(pricing.DEFAULT_BOT_LOW)),
        "bot_high_plus": get_float_setting(con, "bot_high_plus",
                                           float(pricing.DEFAULT_BOT_HIGH_PLUS)),
        "publish_qty": get_int_setting(con, "publish_qty", pricing.DEFAULT_QTY),
    }


@bp.route("/uploads/<path:name>")
def uploaded(name):
    # send_from_directory qovluqdan kənara çıxışı (path traversal) bloklayır
    return send_from_directory(current_app.config["UPLOAD_DIR"], name)
