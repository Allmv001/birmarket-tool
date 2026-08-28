# -*- coding: utf-8 -*-
"""Yayın axını: HTML səhifəsi + JSON API.

Axın QƏSDƏN üç addımdır (tapıntı K4 — köhnə alətdə tək düymə birbaşa canlıya
yazırdı):

    1. Planla     — mətn → katalog → qərar → dəftər (heç nə yazılmır)
    2. Quru koşu  — «nə yazılacaq» hesabatı (brauzer də açılmır)
    3. Canlı yazı — YALNIZ quru koşudan keçmiş sətirlər, açıq təsdiqlə

`live=True` yalnız gövdədə `confirm == "CANLI"` göndəriləndə qəbul olunur.
Ümumi «ok» kifayət etmir — Autopricer qaydası ilə eynidir:
«Canlı fiyatlandırma geri alınamaz... açık insan onayı gerekir.»
"""
import threading
from pathlib import Path

from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, url_for)

from . import jobs, publish
from .db import connect, db, get_float_setting, get_int_setting, get_setting
from .money import AmbiguousMoney, parse_money
from .security import json_body

bp = Blueprint("publish", __name__)

#: Canlı yazı üçün gövdədə tələb olunan açar söz.
LIVE_CONFIRM = "CANLI"


def _opts(con):
    return publish.settings_from(con, get_setting, get_float_setting, get_int_setting)


def _err(message, status=400):
    return jsonify({"ok": False, "error": message}), status


def _profile_dir():
    return Path(current_app.config["BASE_DIR"]) / "data" / "chrome-profile"


# ------------------------------------------------------------------ səhifə
@bp.route("/publish")
def page():
    con = db()
    return render_template(
        "publish.html",
        rows=publish.rows(con, limit=300),
        counts=publish.counts(con),
        pending=publish.pending(con),
        opts=_opts(con),
        live_confirm=LIVE_CONFIRM,
    )


@bp.route("/publish/plan", methods=["POST"])
def plan():
    """Mətndən plan qur. Birmənalı olmayan maya varsa XƏBƏRDARLIQ verir (K1)."""
    con = db()
    text = request.form.get("links", "")
    allow = bool(request.form.get("allow_ambiguous"))
    if not text.strip():
        flash("⚠️ Link siyahısı boşdur.", "warn")
        return redirect(url_for("publish.page"))
    result = publish.plan(con, text, _opts(con), allow_ambiguous=allow)
    for _line, msg in result["errors"]:
        flash(f"⚠️ {msg}", "warn")
    if result["planned"] or result["skipped"]:
        flash(f"Plan hazır: {result['planned']} yazılacaq, "
              f"{result['skipped']} keçilir.", "ok")
    elif not result["errors"]:
        flash("⚠️ Heç bir məhsul tapılmadı.", "warn")
    return redirect(url_for("publish.page"))


# ------------------------------------------------------------------ JSON API
@bp.route("/api/publish/state")
def state():
    con = db()
    return jsonify({"ok": True, "counts": publish.counts(con),
                    "pending": publish.pending(con),
                    "rows": publish.rows(con, limit=300)})


@bp.route("/api/publish/<int:pub_id>/discount", methods=["POST"])
def set_discount(pub_id):
    """Endirimli qiyməti dəyiş — köhnə/alt/üst ÖZÜ yenidən hesablanır (Y3)."""
    con = db()
    if not publish.get(con, pub_id):
        return _err("Sətir tapılmadı.", 404)
    raw = str(json_body().get("endirimli", "")).strip()
    if not raw:
        return _err("Endirimli qiymət boşdur.")
    try:
        value = parse_money(raw)
    except AmbiguousMoney as e:
        return _err(str(e))
    except ValueError as e:
        return _err(str(e))
    if value <= 0:
        return _err("Endirimli qiymət müsbət olmalıdır.")
    row = publish.set_discount(con, pub_id, value, _opts(con))
    return jsonify({"ok": True, "row": row})


@bp.route("/api/publish/dry-run", methods=["POST"])
def dry_run():
    """Quru koşu: heç nəyə toxunmur, «nə yazılacaq» hesabatı verir."""
    con = db()
    body = json_body()
    ids = [int(x) for x in body.get("ids", []) if str(x).isdigit()]
    if not ids:
        return _err("Heç bir sətir seçilməyib.")
    allow = bool(body.get("allow_no_seller"))
    return jsonify({"ok": True,
                    **publish.dry_run(con, ids, _opts(con), allow_no_seller=allow)})


@bp.route("/api/publish/retry", methods=["POST"])
def retry():
    con = db()
    return jsonify({"ok": True, "requeued": publish.retry_failed(con)})


@bp.route("/api/publish/live", methods=["POST"])
def live():
    """CANLI yazı — arxa fonda iş kimi başlayır.

    Təsdiq açarı olmadan RƏDD edilir. Sözün özü gövdədə gəlməlidir ki, təsadüfi
    və ya avtomatik sorğu canlıya yaza bilməsin.
    """
    con = db()
    body = json_body()
    if str(body.get("confirm", "")).strip() != LIVE_CONFIRM:
        return _err(f"Canlı yazı üçün təsdiq lazımdır (confirm=«{LIVE_CONFIRM}»).", 428)
    ids = [int(x) for x in body.get("ids", []) if str(x).isdigit()]
    if not ids:
        return _err("Heç bir sətir seçilməyib.")

    ready = [r for r in publish.rows(con, ids=ids) if r["state"] == publish.DRY_RUN]
    if not ready:
        return _err("Seçilənlərin heç biri quru koşudan keçməyib. Əvvəlcə «Quru koşu».")

    opts = _opts(con)
    if not opts.get("bot_url"):
        return _err("Bot ünvanı təyin olunmayıb (Ayarlar → bot_url).")

    db_path = current_app.config["DB_PATH"]
    profile = str(_profile_dir())
    job_id = jobs.create_job(con, "publish", total=len(ready))
    ready_ids = [r["id"] for r in ready]
    sample = ready[0]["product_id"]

    def worker():
        from .executor import Runner
        wcon = connect(db_path)

        def log(msg):
            jobs._push(wcon, job_id, result={"line": msg})        # noqa: SLF001

        try:
            jobs._update(wcon, job_id, state="running")           # noqa: SLF001
            with Runner(opts["bot_url"], profile, log=log) as runner:
                ok, msg = runner.session_ready()
                if not ok:
                    ok, msg = runner.wait_for_login()
                if not ok:
                    raise RuntimeError(msg)
                good, why = runner.preflight(sample)
                if not good:
                    raise RuntimeError(f"UI müqaviləsi pozulub: {why}")
                res = publish.execute(
                    wcon, ready_ids, opts, runner, live=True, log=log,
                    stop_flag=lambda: jobs._cancelled(wcon, job_id))   # noqa: SLF001
                log(f"Nəticə: {res['written']} yazıldı, {res['failed']} alınmadı, "
                    f"{res['skipped']} keçildi.")
            jobs._update(wcon, job_id, state="done")              # noqa: SLF001
        except Exception as e:                                    # noqa: BLE001
            jobs._push(wcon, job_id, error=str(e))                # noqa: SLF001
            jobs._update(wcon, job_id, state="error")             # noqa: SLF001
        finally:
            wcon.close()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job": job_id, "total": len(ready)})
