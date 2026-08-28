# -*- coding: utf-8 -*-
"""CSRF qoruması — lokal tətbiq üçün Origin/Referer yoxlaması.

NİYƏ LAZIMDIR (27.08.2026 auditində CANLI TƏSDİQLƏNİB, tapıntı K3):
    Köhnə `birmarket-tool/app.py:148` belə idi:
        ids = set(request.get_json(force=True).get("ids", []))
    `force=True` `Content-Type: text/plain` gövdəni də qəbul edir. Bu, brauzerin
    CORS preflight yoxlamasını KEÇMƏYƏN «sadə sorğu» sinfinə düşür. Test:

        POST /run  Content-Type: text/plain  Origin: https://kotu-site.example
        -> HTTP 200  {"ok":true}          <-- QƏBUL EDİLDİ, koşu başladı

    Yəni alət açıq ikən başqa bir tabda açılan zərərli səhifə sənin adından
    kabinetə məhsul yazdıra bilərdi. `127.0.0.1`-ə bağlı olmaq qorumur —
    hücum sənin öz brauzerindən gəlir.

QAYDA (OWASP «Verifying Origin With Standard Headers»):
    * Origin varsa — host eyni olmalıdır.
    * Origin yoxdursa, Referer varsa — host eyni olmalıdır.
    * Hər ikisi yoxdursa — İCAZƏ VERİLİR. Bu bir boşluq deyil: CSRF üçün
      brauzer lazımdır, brauzer isə cross-origin POST-a HƏMİŞƏ Origin qoyur.
      Hər ikisinin olmaması `curl`/skript deməkdir, ki o da CSRF vektoru deyil.
      Bu qaydanı sərtləşdirmək lokal skriptləri sındırır, əvəzində heç nə
      qazandırmır.

Əlavə qat: `force=True` heç yerdə istifadə olunmur, ona görə `text/plain`
gövdə JSON kimi oxunmur (bax `json_body()`).
"""
from urllib.parse import urlparse

from flask import current_app, jsonify, request

#: Vəziyyəti dəyişən metodlar — yalnız bunlar yoxlanılır.
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: Yoxlamadan azad endpoint-lər (hazırda yoxdur). Gələcəkdə webhook əlavə
#: olunarsa bura yazılır ki, istisna GÖRÜNƏN yerdə dursun.
EXEMPT_ENDPOINTS = frozenset()


def _host_of(url):
    try:
        return (urlparse(url).netloc or "").lower()
    except ValueError:
        return ""


def origin_allowed(request_obj=None):
    """Sorğunun mənbəyi bu tətbiqin özüdürmü?

    Qaytarır: (icazə_var, səbəb) — səbəb log/test üçün.
    """
    req = request_obj if request_obj is not None else request
    target = (req.host or "").lower()

    origin = req.headers.get("Origin")
    if origin:
        # "null" — sandbox iframe / file:// mənbəyi. Etibar edilmir.
        if origin == "null":
            return False, "Origin: null"
        src = _host_of(origin)
        return (src == target), f"Origin={src or origin!r} target={target!r}"

    referer = req.headers.get("Referer")
    if referer:
        src = _host_of(referer)
        return (src == target), f"Referer={src or referer!r} target={target!r}"

    # Nə Origin, nə Referer: brauzer deyil -> CSRF vektoru deyil.
    return True, "başlıqsız (brauzer deyil)"


def install_csrf_guard(app):
    """Tətbiqə `before_request` qoruyucusu bağla.

    JSON istəyən müraciətə JSON, səhifə istəyənə mətn qaytarır ki, UI-də
    səbəb görünsün.
    """

    @app.before_request
    def _csrf_guard():
        if request.method not in UNSAFE_METHODS:
            return None
        if request.endpoint in EXEMPT_ENDPOINTS:
            return None
        allowed, why = origin_allowed()
        if allowed:
            return None
        app.logger.warning("CSRF qoruması sorğunu bloklandı: %s %s (%s)",
                           request.method, request.path, why)
        message = ("Sorğu bu səhifədən gəlmədiyi üçün rədd edildi (CSRF qoruması). "
                   "Səhifəni yeniləyib yenidən cəhd edin.")
        wants_json = (request.path.startswith("/api/")
                      or "application/json" in (request.headers.get("Accept") or ""))
        if wants_json:
            return jsonify({"ok": False, "error": message}), 403
        return message, 403

    return app


def json_body(silent=True):
    """Gövdəni JSON kimi oxu — `force=True` İSTİFADƏ ETMƏDƏN.

    `force=True` `text/plain` gövdəni qəbul edir və məhz bu, K3 hücumunu
    preflight-siz mümkün edirdi. Düzgün `Content-Type` göndərməyən sorğu
    boş lüğət alır və çağıran tərəf «məlumat yoxdur» kimi emal edir.
    """
    data = request.get_json(silent=silent)
    return data if isinstance(data, dict) else {}


def client_is_local():
    """Sorğu bu maşından gəlirmi? (`ARMA_REQUIRE_LOCAL` üçün)"""
    addr = (request.remote_addr or "").strip()
    return addr in ("127.0.0.1", "::1", "localhost")


def install_local_only(app):
    """`REQUIRE_LOCAL` konfiqurasiyası aktivdirsə kənar IP-ləri rədd et.

    Defolt bağlıdır, çünki tətbiq onsuz da `127.0.0.1`-ə bind olunur. Şəbəkəyə
    açılsa (məs. `ARMA_HOST=0.0.0.0`) bu qat lazım olur.
    """

    @app.before_request
    def _local_only():
        if not current_app.config.get("REQUIRE_LOCAL"):
            return None
        if client_is_local():
            return None
        return "Yalnız bu kompüterdən giriş var.", 403

    return app
