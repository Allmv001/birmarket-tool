# -*- coding: utf-8 -*-
"""API kaydedicisi — DOM sürməkdən HTTP çağırmağa keçid üçün.

NİYƏ: hazırda kabinetə məhsul əlavə etmək və botda limit qoymaq brauzerdə
düymələrə basmaqla olur. Hər ikisi web tətbiqidir, yəni arxalarında XHR uçları
var. Bir dəfə qeyd alsaq, o uçları birbaşa çağıra bilərik:

    məhsul başına ~40 HTTP sorğu (səhifə + JS + CSS + şəkil)  ->  ~3 JSON sorğu
    ~20 saniyə                                                 ->  <1 saniyə

Bu modul TAHMİN ETMİR — yalnız qeyd alır. Uçları uydurmaq, K2-də düzəltdiyimiz
«bilmədiyini bilirmiş kimi davran» səhvinin eynisidir.

SIRLAR YAZILMIR
    Başlıq və gövdə sahələri iki süzgəcdən keçir:
      1. Ad süzgəci — cookie/authorization/token/secret/key/password/session...
      2. Şəkil süzgəci — JWT və uzun base64/hex blokları (adı nə olursa olsun)
    Tutulan hər şey `<gizli>` olur. Auth-un ÇEREZLƏ yoxsa BEARER ilə
    daşındığını bilmək bizə bəsdir; dəyərini bilmək lazım deyil.
"""
import json
import re
from datetime import datetime
from pathlib import Path

__all__ = ["Recorder", "redact_headers", "redact_value", "summarize"]

# Ad müqayisəsindən əvvəl ayırıcılar atılır: `api_key`, `api-key`, `apiKey`
# hamısı «apikey» olur. Bu testdə tutuldu (28.08.2026): `api_key` siyahıda
# olmadığı üçün MASKALANMIRDI — halbuki ARMA-nın öz ayar açarı elə odur.
_NORM_RE = re.compile(r"[^a-z0-9]")


def _norm(name):
    return _NORM_RE.sub("", (name or "").lower())


#: Adın İÇİNDƏ keçsə maskalanır — bunlar birmənalıdır.
SECRET_SUBSTRINGS = (
    "cookie", "authorization", "token", "secret", "password", "passwd",
    "session", "csrf", "xsrf", "apikey", "credential", "signature", "bearer",
    "accesskey", "privatekey",
)

#: Yalnız TAM bərabərlikdə maskalanır. Qısa sözlərdir; alt sətir kimi
#: axtarsaq «shipping» -> «pin», «author» -> «auth» kimi iş verilərini
#: nahaq gizlədərik.
SECRET_EXACT = frozenset({"auth", "key", "pin", "otp", "sid", "sessid",
                          "jwt", "pass", "pwd"})

#: Dəyərin ÖZÜ sirr kimi görünürsə (adı nə olursa olsun) yenə maskalanır.
JWT_RE = re.compile(r"^[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}$")
LONG_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=_-]{40,}$")

MASK = "<gizli>"
PRESENT = "<var>"

#: Bunların dəyəri təhlükəsizdir və uçları anlamaq üçün lazımdır.
SAFE_HEADERS = {"content-type", "accept", "accept-language", "x-requested-with"}

#: Gövdədə saxlanan sətirin maksimum uzunluğu (şəkil base64-ü tutmasın).
MAX_STR = 200
#: Massivdən neçə element saxlanılsın (şəkil lazımdır, tam siyahı yox).
MAX_ITEMS = 2
#: Cavab gövdəsinin maksimum ölçüsü.
MAX_BODY = 200_000


def _is_secret_name(name):
    low = _norm(name)
    if low in SECRET_EXACT:
        return True
    return any(s in low for s in SECRET_SUBSTRINGS)


def _looks_secret(value):
    if not isinstance(value, str) or len(value) < 20:
        return False
    return bool(JWT_RE.match(value) or LONG_BLOB_RE.match(value))


def redact_value(value, key=""):
    """Bir dəyəri təhlükəsiz hala gətir. Struktur qalır, sirr getmir."""
    if _is_secret_name(key):
        return MASK
    if isinstance(value, dict):
        return {k: redact_value(v, k) for k, v in value.items()}
    if isinstance(value, list):
        out = [redact_value(v, key) for v in value[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            out.append(f"<...{len(value) - MAX_ITEMS} element daha>")
        return out
    if isinstance(value, str):
        if _looks_secret(value):
            return MASK
        return value if len(value) <= MAX_STR else value[:MAX_STR] + "<...kəsildi>"
    return value


def redact_headers(headers):
    """Başlıqlar: adlar HƏMİŞƏ qalır, dəyər yalnız sirr olmayanlarda."""
    out = {}
    for name, value in (headers or {}).items():
        low = name.lower()
        if _is_secret_name(low):
            out[name] = PRESENT          # varlığı vacib, dəyəri yox
        else:
            out[name] = redact_value(value, name)
    return out


def _parse_body(text):
    """Gövdəni JSON kimi oxumağa çalış; alınmasa xam mətn (kəsilmiş)."""
    if not text:
        return None
    if len(text) > MAX_BODY:
        return f"<{len(text)} baytlıq gövdə — kəsildi>"
    try:
        return redact_value(json.loads(text))
    except (ValueError, TypeError):
        return redact_value(text)


class Recorder:
    """Playwright kontekstinə bağlanır, XHR/fetch trafikini yığır.

    İstifadə:
        rec = Recorder()
        rec.attach(context)
        rec.mark("kabinet: axtarış")
        ...  # DOM addımları
        rec.save(Path("data/api-capture"))
    """

    def __init__(self, *, only_xhr=True):
        self.only_xhr = only_xhr
        self.entries = []
        self._marker = "başlanğıc"

    # ------------------------------------------------------------ işarələmə
    def mark(self, label):
        """Növbəti sorğular bu etiketlə qeyd olunsun.

        Beləcə «Seçmək düyməsindən sonra hansı sorğu getdi?» sualı cavablanır —
        uçları AD ilə yox, HƏRƏKƏTLƏ eşləşdiririk.
        """
        self._marker = label
        self.entries.append({"kind": "marker", "label": label,
                             "at": datetime.now().isoformat(timespec="seconds")})

    # ------------------------------------------------------------ bağlanma
    def attach(self, context):
        context.on("request", self._on_request)
        context.on("response", self._on_response)
        return self

    def _keep(self, request):
        if not self.only_xhr:
            return True
        return request.resource_type in ("xhr", "fetch")

    def _on_request(self, request):
        try:
            if not self._keep(request):
                return
            entry = {
                "kind": "request",
                "marker": self._marker,
                "at": datetime.now().isoformat(timespec="seconds"),
                "method": request.method,
                "url": request.url,
                "resource_type": request.resource_type,
                "headers": redact_headers(request.headers),
                "body": _parse_body(request.post_data),
            }
        except Exception as e:                              # noqa: BLE001
            entry = {"kind": "request", "marker": self._marker,
                     "error": f"sorğu oxunmadı: {e}"}
        self.entries.append(entry)

    def _on_response(self, response):
        try:
            request = response.request
            if not self._keep(request):
                return
            method, status = request.method, response.status
            headers = redact_headers(response.headers)
        except Exception as e:                              # noqa: BLE001
            self.entries.append({"kind": "response", "marker": self._marker,
                                 "error": f"cavab oxunmadı: {e}"})
            return
        try:
            body = _parse_body(response.text())
        except Exception:                                   # noqa: BLE001
            # Gövdə oxunmursa qeydi ATMIRIQ — sükutla itirmək olmaz.
            body = "<gövdə oxunmadı>"
        self.entries.append({
            "kind": "response", "marker": self._marker,
            "at": datetime.now().isoformat(timespec="seconds"),
            "url": response.url, "method": method, "status": status,
            "headers": headers, "body": body,
        })

    # ------------------------------------------------------------ çıxış
    def save(self, out_dir, stamp=None):
        """JSON (tam) + Markdown (oxunaqlı) yaz. İki yolu qaytarır."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = stamp or datetime.now().strftime("%Y-%m-%d_%H%M")
        js = out_dir / f"{stamp}.json"
        md = out_dir / f"{stamp}.md"
        js.write_text(json.dumps(self.entries, ensure_ascii=False, indent=1),
                      encoding="utf-8")
        md.write_text(summarize(self.entries), encoding="utf-8")
        return js, md


def summarize(entries):
    """Qeydi insan oxuya bilən hesabata çevir, işarələrə görə qruplaşdır."""
    lines = ["# API qeydi", "", f"Tarix: {datetime.now():%Y-%m-%d %H:%M}", ""]
    reqs = [e for e in entries if e.get("kind") == "request" and "error" not in e]
    resps = {(e.get("method"), e.get("url")): e
             for e in entries if e.get("kind") == "response" and "error" not in e}

    if not reqs:
        lines += ["**Heç bir XHR/fetch sorğusu tutulmadı.**", "",
                  "Ehtimallar: səhifə tam server-render-dir (API yoxdur), "
                  "sorğular `document` kimi gedir, ya da girişdə problem var.", ""]
        return "\n".join(lines)

    hosts = {}
    for r in reqs:
        host = r["url"].split("/")[2] if "://" in r["url"] else "?"
        hosts[host] = hosts.get(host, 0) + 1
    lines += ["## Hostlar", ""]
    lines += [f"- `{h}` — {n} sorğu"
              for h, n in sorted(hosts.items(), key=lambda x: -x[1])]
    lines += ["", f"Cəmi **{len(reqs)}** XHR/fetch sorğusu.", ""]

    by_marker = {}
    for r in reqs:
        by_marker.setdefault(r.get("marker", "?"), []).append(r)

    for marker, items in by_marker.items():
        lines += [f"## {marker}", ""]
        for r in items:
            resp = resps.get((r["method"], r["url"]))
            status = resp["status"] if resp else "?"
            lines += [f"### `{r['method']} {r['url']}`", "", f"- Status: **{status}**"]
            auth = [n for n in r["headers"]
                    if n.lower() in ("cookie", "authorization")]
            if auth:
                lines.append(f"- Auth başlığı: {', '.join(auth)} (dəyər yazılmadı)")
            if r.get("body") is not None:
                lines += ["", "İstək gövdəsi:", "```json",
                          json.dumps(r["body"], ensure_ascii=False, indent=1),
                          "```"]
            if resp and resp.get("body") is not None:
                lines += ["", "Cavab:", "```json",
                          json.dumps(resp["body"], ensure_ascii=False,
                                     indent=1)[:2000],
                          "```"]
            lines.append("")
    return "\n".join(lines)
