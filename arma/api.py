# -*- coding: utf-8 -*-
"""JSON API — yeni UI bunun üzərində işləyir.

v2.4-də hər əməliyyat tam səhifə yeniləməsi idi: axtarış düyməsinə basanda
səhifə donurdu, toplu proqres üçün `location.reload()` hər 4 saniyədən bir
bütün HTML-i yenidən çəkirdi. Burada eyni əməliyyatlar JSON qaytarır —
səhifə yerində qalır, yalnız dəyişən hissə yenilənir.
"""
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from . import jobs
from .db import PICKED_COND, db
from .fetcher import breaker_state
from .services import active_links, check_summary, run_autosearch, wa_block

bp = Blueprint("api", __name__)


def _err(message, status=400):
    return jsonify({"ok": False, "error": message}), status


@bp.errorhandler(Exception)
def _unhandled(e):
    current_app.logger.exception("API xətası")
    return jsonify({"ok": False, "error": str(e)}), 500


# ------------------------------------------------------------------ seçim
@bp.route("/offer/<int:offer_id>/pick", methods=["POST"])
def pick_offer(offer_id):
    """Checkbox seçimi DB-də saxlanır — geri qayıdıb girəndə itmir."""
    con = db()
    row = con.execute("SELECT check_id FROM offers WHERE id=?", (offer_id,)).fetchone()
    if not row:
        return _err("Təklif tapılmadı.", 404)
    data = request.get_json(silent=True) or request.form
    value = 1 if str(data.get("v", "")).lower() in ("1", "true", "on", "yes") else 0
    con.execute("UPDATE offers SET picked=? WHERE id=?", (value, offer_id))
    con.commit()
    cid = row["check_id"]
    return jsonify({"ok": True, "picked": value, "check_id": cid,
                    "active_links": len(active_links(con, cid))})


@bp.route("/check/<int:check_id>/pick-all", methods=["POST"])
def pick_all(check_id):
    """Bir yoxlamanın bütün linkli təkliflərini birdən seç / seçimi ləğv et."""
    con = db()
    if not con.execute("SELECT 1 FROM checks WHERE id=?", (check_id,)).fetchone():
        return _err("Yoxlama tapılmadı.", 404)
    data = request.get_json(silent=True) or request.form
    value = 1 if str(data.get("v", "")).lower() in ("1", "true", "on", "yes") else 0
    con.execute("UPDATE offers SET picked=? WHERE check_id=? AND url IS NOT NULL",
                (value, check_id))
    con.commit()
    return jsonify({"ok": True, "picked": value,
                    "active_links": len(active_links(con, check_id))})


# ------------------------------------------------------------------ axtarış
@bp.route("/check/<int:check_id>/autosearch", methods=["POST"])
def autosearch(check_id):
    """Axtarışı işlət və nəticəni JSON qaytar — səhifə yenilənmir."""
    con = db()
    if not con.execute("SELECT 1 FROM checks WHERE id=?", (check_id,)).fetchone():
        return _err("Yoxlama tapılmadı.", 404)
    try:
        result = run_autosearch(con, check_id)
    except Exception as e:
        return _err(str(e), 502)
    tripped, remaining = breaker_state()
    return jsonify({"ok": True, "message": result["message"],
                    "added": result["added"], "updated": result["updated"],
                    "unchanged": result["unchanged"], "skipped": result["skipped"],
                    "errors": [{"query": q, "error": m} for q, m in result["errors"]],
                    "summary": check_summary(con, check_id),
                    "rate_limited": tripped, "cooldown_seconds": remaining})


# ------------------------------------------------------------------ linklər
@bp.route("/check/<int:check_id>/links")
def check_links(check_id):
    """Bir yoxlamanın aktiv linkləri + WhatsApp-a hazır mətn bloku."""
    con = db()
    c = con.execute("SELECT * FROM checks WHERE id=?", (check_id,)).fetchone()
    if not c:
        return _err("Yoxlama tapılmadı.", 404)
    links = active_links(con, check_id)
    return jsonify({"ok": True, "code": c["code"], "cost": c["cost"],
                    "links": links, "text": wa_block(links, c["cost"])})


@bp.route("/links/bundle", methods=["POST"])
def links_bundle():
    """Bir neçə yoxlamanın linklərini tək WhatsApp mətninə yığ.

    Gövdə: {"ids": [1,2,3]}  ->  {"text": "...", "links": 12, "codes": 3}
    """
    data = request.get_json(silent=True) or {}
    ids = [int(x) for x in data.get("ids", []) if str(x).isdigit()]
    if not ids:
        return _err("Heç bir yoxlama seçilməyib.")
    con = db()
    blocks, total = [], 0
    for cid in ids:
        c = con.execute("SELECT code, cost FROM checks WHERE id=?", (cid,)).fetchone()
        if not c:
            continue
        links = active_links(con, cid)
        if links:
            blocks.append(wa_block(links, c["cost"]))
            total += len(links)
    return jsonify({"ok": True, "text": "\n\n".join(blocks),
                    "links": total, "codes": len(blocks)})


# ------------------------------------------------------------------ iş
@bp.route("/job/<job_id>")
def job_status(job_id):
    """Toplu işin proqresi — səhifə tam yenilənmədən oxunur.

    v2.4-də bu məlumat üçün hər 4 saniyədən bir `location.reload()` çağırılırdı.
    """
    con = db()
    job = jobs.get_job(con, job_id)
    if not job:
        return _err("Tapşırıq tapılmadı.", 404)
    tripped, remaining = breaker_state()
    return jsonify({"ok": True, "id": job["id"], "state": job["state"],
                    "finished": job["finished"], "current": job["current"],
                    "done": job["done"], "total": job["total"],
                    "img_done": job["img_done"], "img_total": job["img_total"],
                    "percent": job["percent"], "results": job["results"],
                    "errors": job["errors"],
                    "rate_limited": tripped, "cooldown_seconds": remaining})


@bp.route("/job/<job_id>/cancel", methods=["POST"])
def job_cancel(job_id):
    con = db()
    if not jobs.get_job(con, job_id):
        return _err("Tapşırıq tapılmadı.", 404)
    jobs.cancel_job(con, job_id)
    return jsonify({"ok": True})


# ------------------------------------------------------------------ vəziyyət
@bp.route("/stats")
def stats():
    """Panel plitələri üçün canlı rəqəmlər."""
    con = db()
    row = con.execute(
        "SELECT COUNT(DISTINCT c.code) AS codes, "
        " SUM(CASE WHEN o.is_match=1 THEN 1 ELSE 0 END) AS hits, "
        " MAX(CASE WHEN o.is_match=1 THEN o.margin END) AS best "
        "FROM checks c LEFT JOIN offers o ON o.check_id=c.id").fetchone()
    today = datetime.now().strftime("%Y-%m-%d")
    today_n = con.execute(
        "SELECT COUNT(*) n FROM checks WHERE created_at LIKE ?", (today + "%",)
    ).fetchone()["n"]
    active = con.execute(
        "SELECT COUNT(*) n FROM offers o WHERE o.url IS NOT NULL AND " + PICKED_COND
    ).fetchone()["n"]
    tripped, remaining = breaker_state()
    return jsonify({"ok": True, "codes": row["codes"] or 0, "hits": row["hits"] or 0,
                    "best": row["best"], "today": today_n, "active_links": active,
                    "rate_limited": tripped, "cooldown_seconds": remaining})


@bp.route("/price-history/<int:offer_id>")
def price_history(offer_id):
    """Bir elanın qiymət tarixçəsi — detal səhifəsindəki ↕N işarəsi üçün."""
    con = db()
    row = con.execute("SELECT name, price FROM offers WHERE id=?", (offer_id,)).fetchone()
    if not row:
        return _err("Təklif tapılmadı.", 404)
    history = [dict(r) for r in con.execute(
        "SELECT changed_at, old_price, new_price FROM price_history "
        "WHERE offer_id=? ORDER BY id", (offer_id,))]
    return jsonify({"ok": True, "name": row["name"], "current": row["price"],
                    "history": history})
