# -*- coding: utf-8 -*-
"""Giriş qatı: ARMA lokal işləyəndə açıqdır, internetdə parolla bağlıdır.

Niyə iki rejim var:

  * `run.bat` ilə lokal işləyəndə tətbiq yalnız `127.0.0.1`-i dinləyir və
    parol soruşmaq iş axınını yavaşladır. v4-ə qədər heç vaxt giriş olmayıb;
    lokal davranış dəyişmir.
  * `arma.biraddim.com` üzərində tətbiq açıq internetdədir. Orada systemd
    `ARMA_REQUIRE_AUTH=1` verir. Bu dəyişən qoyulubsa, amma parol
    qurulmayıbsa, tətbiq HEÇ NƏ servis etmir (503) — yanlış qurulmuş server
    açıq qalmır, bağlı qalır. BirAddim layihəsindəki `AP_ADMIN_PASSWORD`
    qaydası ilə eyni məntiq.

Parol koda yazılmır. Serverdə yalnız hash saxlanır. Hash yaratmaq:

    python -m arma.auth

Parol argument kimi verilmir (shell tarixçəsinə düşməsin), gizli soruşulur.
"""
import os
import threading
import time
from datetime import timedelta

from flask import (Blueprint, current_app, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

bp = Blueprint("auth", __name__)

# Giriş tələb olunmayan endpoint-lər. `static` olmasa giriş səhifəsi stilsiz
# görünür, `health` olmasa dağıtım sağlamlıq yoxlaması 302 alıb uğursuz sayır.
OPEN_ENDPOINTS = {"auth.login", "auth.health", "static"}

# Parol sınaqlarının sayğacı. Sistem açıq internetdə olduğu üçün kobud güc
# cəhdi realdır; sadə, yaddaşda saxlanan tıxac kifayətdir (tək proses, tək
# istifadəçi). Açar = IP, dəyər = (uğursuz sayı, blokun bitmə vaxtı).
_ATTEMPTS = {}
_ATTEMPTS_LOCK = threading.Lock()
MAX_ATTEMPTS = 6
LOCK_SECONDS = 300


def _client_ip():
    """İstifadəçinin IP-si. Proksi arxasında `X-Forwarded-For` oxunur, amma
    yalnız `ARMA_TRUST_PROXY=1` olanda — əks halda başlığı hər kəs uydura
    bilər və yuxarıdakı tıxac mənasız olur."""
    if current_app.config.get("TRUST_PROXY"):
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote_addr or "?"


def _locked_for(ip):
    """Bu IP bloklanıbsa qalan saniyə, yoxsa 0."""
    with _ATTEMPTS_LOCK:
        _, until = _ATTEMPTS.get(ip, (0, 0.0))
        if until > time.time():
            return int(until - time.time()) + 1
        if until:
            _ATTEMPTS.pop(ip, None)          # blok bitdi, sayğacı sıfırla
        return 0


def _note_failure(ip):
    with _ATTEMPTS_LOCK:
        count, _ = _ATTEMPTS.get(ip, (0, 0.0))
        count += 1
        until = time.time() + LOCK_SECONDS if count >= MAX_ATTEMPTS else 0.0
        _ATTEMPTS[ip] = (count, until)


def _note_success(ip):
    with _ATTEMPTS_LOCK:
        _ATTEMPTS.pop(ip, None)


def _password_hash():
    """Konfiqurasiyadan parol hash-i. İki mənbə, bu sıra ilə:

    1. `ARMA_ADMIN_PASSWORD_HASH` — serverdə istifadə olunan yol.
    2. `ARMA_ADMIN_PASSWORD` — düz mətn; açılışda hash-lənir. Rahatdır, amma
       parol unit faylında və proses mühitində görünür, server üçün tövsiyə
       olunmur.

    Heç biri yoxdursa boş qayıdır: giriş qurulmayıb.
    """
    h = (os.environ.get("ARMA_ADMIN_PASSWORD_HASH") or "").strip()
    if h:
        return h
    plain = os.environ.get("ARMA_ADMIN_PASSWORD") or ""
    if plain:
        return generate_password_hash(plain)
    return ""


def _flag(name):
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _is_publish_path(path):
    """`/publish`, `/publish/plan`, `/api/publish/...` - hamisi yazma terefi."""
    return path.startswith("/publish") or path.startswith("/api/publish")


def configure(app):
    """Giriş qatını tətbiqə bağla. `create_app` bir dəfə çağırır."""
    pw_hash = _password_hash()

    app.config.update(
        AUTH_REQUIRED=_flag("ARMA_REQUIRE_AUTH"),
        AUTH_USER=(os.environ.get("ARMA_ADMIN_USER") or "Admin").strip(),
        AUTH_HASH=pw_hash,
        # Giriş yalnız parol qurulubsa işləyir. AUTH_REQUIRED isə "parol
        # olmalıdır" deməkdir: qurulmayıbsa tətbiq bağlanır (aşağıdakı 503).
        AUTH_ENABLED=bool(pw_hash),
        TRUST_PROXY=_flag("ARMA_TRUST_PROXY"),
        # Yayin (yazma) terefi: /publish ve /api/publish/*.
        # Defolt LOKALDA aciq, SERVERDE bagli. Sebeb texnikidir, sonra
        # tehlukesizlik:
        #   * executor.py `headless=False` ile Chrome acir ve `wait_for_login`
        #     ile 300 saniye INSAN girisi gozleyir. Basssiz VPS-de ekran da,
        #     Chrome da, insan da yoxdur - axin orada onsuz da islemir.
        #   * `launch_persistent_context` birmarket Business kabinetinin
        #     GIRIS ETMIS sessiyasini diskde saxlayir. `/api/publish/live`
        #     real magazaya yazir ve geri alinmir. Bir parolun arxasinda
        #     acik internetde durmasi ucun cox agir bir dugmedir.
        # Serverde acmaq isteseniz: ARMA_PUBLISH=1 (bilerek, elle).
        PUBLISH_ENABLED=(_flag("ARMA_PUBLISH") if os.environ.get("ARMA_PUBLISH")
                         else not _flag("ARMA_REQUIRE_AUTH")),
    )

    if app.config["AUTH_ENABLED"]:
        app.config.update(
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE="Lax",
            # HTTPS-i yalnız proksi arxasında tələb et; lokal HTTP-də belə
            # kuki heç vaxt göndərilməzdi və giriş sonsuz dövrəyə düşərdi.
            SESSION_COOKIE_SECURE=app.config["TRUST_PROXY"],
            # Bir iş günü. Uzun toplu yoxlama gedərkən sessiya bitməsin,
            # amma açıq qalmış brauzer də sonsuza qədər açıq qalmasın.
            PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        )

    app.register_blueprint(bp)

    @app.context_processor
    def _auth_globals():
        """base.html-in çıxış düyməsini göstərməsi üçün."""
        return {"auth_on_g": app.config["AUTH_ENABLED"],
                "auth_user_g": session.get("arma_user") if app.config["AUTH_ENABLED"] else None,
                "publish_on_g": app.config["PUBLISH_ENABLED"]}

    @app.before_request
    def _guard():
        # Parol tələb olunur, amma qurulmayıb: tətbiq açıq qalmır.
        if app.config["AUTH_REQUIRED"] and not app.config["AUTH_ENABLED"]:
            if request.endpoint == "auth.health":
                return None
            return ("ARMA qurulmayib: ARMA_ADMIN_PASSWORD_HASH verilmeyib. "
                    "Server baglidir.", 503)

        # Yayin terefi baglidirsa route-lar sanki yoxdur.
        if not app.config["PUBLISH_ENABLED"] and _is_publish_path(request.path):
            msg = ("Yayin (yazma) terefi bu qurulusda baglidir. Kabinete yazmaq "
                   "ucun Chrome ve elle giris lazimdir - o is lokal komputerde "
                   "gorulur.")
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": msg}), 404
            return msg, 404

        if not app.config["AUTH_ENABLED"]:
            return None                                   # lokal rejim
        if request.endpoint in OPEN_ENDPOINTS:
            return None
        if session.get("arma_user"):
            g.arma_user = session["arma_user"]
            return None

        # `fetch` ilə gedən API sorğusuna HTML giriş səhifəsi qaytarmaq
        # brauzerdə anlaşılmaz JSON parse xətası verir — 401 daha dürüstdür.
        if request.path.startswith("/api/"):
            return jsonify({"ok": False,
                            "error": "Sessiya bitib. Yenidən giriş edin."}), 401
        return redirect(url_for("auth.login", next=request.full_path))


@bp.route("/saglamliq")
def health():
    """Dağıtım sağlamlıq yoxlaması. Giriş tələb etmir, məlumat açmır."""
    return jsonify({"ok": True, "version": current_app.config.get("VERSION")})


@bp.route("/giris", methods=["GET", "POST"])
def login():
    if not current_app.config.get("AUTH_ENABLED"):
        return redirect(url_for("views.dashboard"))
    if session.get("arma_user"):
        return redirect(url_for("views.dashboard"))

    ip = _client_ip()
    error = None
    locked = _locked_for(ip)

    if request.method == "POST":
        if locked:
            error = "Çox sayda yanlış cəhd. %d saniyə gözləyin." % locked
        else:
            user = (request.form.get("user") or "").strip()
            password = request.form.get("password") or ""
            ok_user = user.lower() == current_app.config["AUTH_USER"].lower()
            ok_pass = check_password_hash(current_app.config["AUTH_HASH"], password)
            if ok_user and ok_pass:
                _note_success(ip)
                session.clear()
                session["arma_user"] = current_app.config["AUTH_USER"]
                session.permanent = True
                nxt = request.args.get("next") or ""
                # Yalnız daxili ünvana yönləndir: `next=https://...` ilə
                # istifadəçini kənar sayta atmaq mümkün olmasın.
                if nxt.startswith("/") and not nxt.startswith("//"):
                    return redirect(nxt)
                return redirect(url_for("views.dashboard"))
            _note_failure(ip)
            locked = _locked_for(ip)
            error = ("Çox sayda yanlış cəhd. %d saniyə gözləyin." % locked) if locked \
                else "İstifadəçi adı və ya parol yanlışdır."

    return render_template("login.html", error=error, locked=locked,
                           user_hint=current_app.config["AUTH_USER"]), \
        (401 if error else 200)


# Yalnız POST: GET olsaydı brauzerin link öncədən yükləməsi və ya kənar
# saytdakı bir <img src="/cixis"> istifadəçini gözlənilmədən çıxarardı.
@bp.route("/cixis", methods=["POST"])
def logout():
    session.clear()
    flash("Çıxış edildi.", "info")
    return redirect(url_for("auth.login"))


def _cli():
    """`python -m arma.auth` — parol hash-i yarat, ekrana yalnız hash düşür."""
    import getpass
    print()
    print("  ARMA admin parolu ucun hash yaradilir.")
    print("  Parol ekranda gorunmeyecek ve hec bir fayla yazilmayacaq.")
    print()
    p1 = getpass.getpass("  Parol: ")
    p2 = getpass.getpass("  Tekrar: ")
    if not p1:
        print("\n  Parol bosdur, hec ne edilmedi.\n")
        return 1
    if p1 != p2:
        print("\n  Parollar uygun gelmir, hec ne edilmedi.\n")
        return 1
    print()
    print("  systemd unit-e bu setri yazin (parolun ozunu YOX):")
    print()
    print('  Environment="ARMA_ADMIN_PASSWORD_HASH=%s"' % generate_password_hash(p1))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
