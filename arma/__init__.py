# -*- coding: utf-8 -*-
"""ARMA — Birmarket Marja Sistemi v4. Tətbiq fabriki."""
import os
import re
import secrets

from flask import Flask, render_template

from . import db as dbmod
from .security import install_csrf_guard, install_local_only

__version__ = "4.1"

DEFAULTS = {
    "threshold": 1.20,
    "wa_number": "994503377176",
    # 28.08.2026-da olculdu: R.209 ucun 3 sehife BES elan tapirdi,
    # 5 sehife YEDDI. Iki elan (89.99 Black ve 99.98) yalniz 4-5-ci
    # sehifelerde idi. `fetch_search` dolu olmayan sehifede onsuz da
    # dayanir (len(cards) < 24), ona gore dar sorgular bir istekde
    # bitir - derinlik yalniz gercekden genis sorgularda islenir.
    "max_pages": 5,
    "max_variants": 4,
    "request_delay": 1.0,
    "page_size": 25,
}


def _secret_key(base_dir):
    """Sabit gizli açar: ilk açılışda yaradılır, fayla yazılır.

    v2.4-də açar koda yazılmışdı ("birmarket-local") — sessiya kukisi hər
    quraşdırmada eyni açarla imzalanırdı. Lokal tətbiqdir, amma açarı
    təsadüfi etmək bədavadır.
    """
    path = os.path.join(base_dir, "data", ".secret_key")
    try:
        if os.path.exists(path):
            key = open(path, encoding="utf-8").read().strip()
            if key:
                return key
        key = secrets.token_hex(32)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(key)
        return key
    except OSError:
        return secrets.token_hex(32)      # yazıla bilmirsə yaddaşda qalsın


def _install_file_log(app):
    """Diskə log yaz — `data/arma.log`, 5 fayl × 1 MB.

    NİYƏ (28.08.2026 üretim raporu, 4.2): tətbiq HTTP 500 verib sonra tamamilə
    dayanmışdı və geriyə baxmaq üçün HEÇ BİR iz yox idi — log faylı yoxdu,
    yalnız bağlanmış cmd pəncərəsi vardı. İndi çökmə diskdə qalır.
    """
    import logging
    from logging.handlers import RotatingFileHandler

    path = os.path.join(app.config["BASE_DIR"], "data", "arma.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5,
                                      encoding="utf-8")
    except OSError:
        return                      # log yazıla bilmirsə tətbiq yenə işləsin
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    logging.getLogger("werkzeug").addHandler(handler)


def create_app(base_dir=None, db_path=None):
    base = base_dir or os.path.dirname(os.path.abspath(__file__)).rsplit(os.sep, 1)[0]
    app = Flask(__name__,
                template_folder=os.path.join(base, "templates"),
                static_folder=os.path.join(base, "static"))

    app.config.update(
        BASE_DIR=base,
        DB_PATH=db_path or os.path.join(base, "data", "birmarket.db"),
        UPLOAD_DIR=os.path.join(base, "data", "uploads"),
        MAX_CONTENT_LENGTH=64 * 1024 * 1024,      # yüklənən şəkillərin ümumi həddi
        SECRET_KEY=_secret_key(base),
        VERSION=__version__,
        # Şəbəkəyə açılsa (ARMA_HOST=0.0.0.0) kənar IP-ləri rədd et.
        REQUIRE_LOCAL=bool(os.environ.get("ARMA_REQUIRE_LOCAL")),
    )
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    # v4: baza tətbiq açılışında qurulur — `flask run`/WSGI altında da işləyir
    # (v2.4-də init_db yalnız `python app.py` yolunda çağırılırdı)
    dbmod.init_db(app.config["DB_PATH"])
    app.teardown_appcontext(dbmod.close_db)

    _install_file_log(app)

    # v4.1: CSRF qoruması. Auditdə (27.08.2026) köhnə alətdə kənar saytdan
    # gələn `text/plain` POST canlı yazı başlada bilirdi — HTTP 200 alınmışdı.
    # Bu qat bütün POST/PUT/PATCH/DELETE sorğularının mənbəyini yoxlayır.
    install_csrf_guard(app)
    install_local_only(app)

    from .views import bp as views_bp
    from .api import bp as api_bp
    from .views_publish import bp as publish_bp
    app.register_blueprint(views_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(publish_bp)

    # Giriş qatı. Lokalda (parol qurulmayıbsa) heç nə dəyişmir; serverdə
    # bütün səhifələri bağlayır. Detal: arma/auth.py
    from . import auth
    auth.configure(app)

    @app.context_processor
    def _globals():
        """Bütün şablonlara: WhatsApp nömrəsi + versiya."""
        try:
            raw = dbmod.get_setting(dbmod.db(), "wa_number", DEFAULTS["wa_number"])
            wa = re.sub(r"\D", "", raw or "") or DEFAULTS["wa_number"]
        except Exception:
            wa = DEFAULTS["wa_number"]
        return {"wa_number_g": wa, "version_g": __version__}

    @app.errorhandler(404)
    def _404(_e):
        return render_template("error.html", code=404,
                               title="Səhifə tapılmadı",
                               detail="Bu ünvanda heç nə yoxdur. "
                                      "Silinmiş bir yoxlamaya keçid vermiş ola bilərsiniz."), 404

    @app.errorhandler(413)
    def _413(_e):
        return render_template("error.html", code=413,
                               title="Fayl çox böyükdür",
                               detail="Yüklənən şəkillərin ümumi ölçüsü 64 MB həddini keçdi. "
                                      "Daha az şəkil seçib yenidən cəhd edin."), 413

    @app.errorhandler(500)
    def _500(e):
        app.logger.exception("Server xətası: %s", e)
        return render_template("error.html", code=500,
                               title="Sistemdə xəta oldu",
                               detail="Əməliyyat tamamlanmadı. Konsol pəncərəsində "
                                      "texniki detal var — məlumatlarınız itmədi."), 500

    return app
