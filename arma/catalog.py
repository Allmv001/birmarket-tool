# -*- coding: utf-8 -*-
"""birmarket.az katalog API-si (mp-catalog) — məhsul və satıcı qiymətləri.

Mənbə: `birmarket-tool/birmarket_api.py`. Fərqlər (audit tapıntıları):

  * O4 — SÜRƏT LİMİTİ QORUYUCUSU əlavə olundu. Köhnə modul 6 thread ilə
    kataloqa girirdi, 429/503 emal olunmurdu, geri çəkilmə yox idi.
    `fetcher.py` bu dərsi 20.08.2026-da artıq ödəmişdi («~100 ardıcıl
    sorğudan sonra birmarket cavab verməyi dayandırır») — burada eyni
    qoruyucu var, çünki eyni satıcının hostlarıdır.

  * Qiymətlər `Decimal` qaytarır (float yuvarlaqlaşma qalığı olmasın).

  * `status` HƏR ZAMAN qaytarılır və heç vaxt «yoxdur» kimi susdurulmur —
    qiymət qərarı ona baxır (bax `pricing.evaluate`, tapıntı K2).

  * Maya parsleyicisi ayrıca modula (`money.py`) verildi: linkin içindəki
    rəqəmlər maya sayılmır, minlik ayırıcısı düzgün oxunur (tapıntı K1).
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from .money import AmbiguousMoney, money, parse_money

API = "https://mp-catalog.birmarket.az/api/v1/products/{id}"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json",
    "Origin": "https://birmarket.az",
    "Referer": "https://birmarket.az/",
}

# Linkdən məhsul ID-si. Linkin QALAN hissəsindəki rəqəmlər maya sayılmır.
URL_RE = re.compile(r"(?:https?://)?\S*?/product/(\d+)\S*")

# --- sürət limiti qoruyucusu (fetcher.py ilə eyni ölçülmüş sabitlər) ---
FAIL_TRIP = 4
COOLDOWN = 90.0
_lock = threading.Lock()
_fail_streak = 0
_cooldown_until = 0.0


def breaker_state():
    """(açıqdır?, qalan_saniyə) — UI xəbərdarlığı üçün."""
    with _lock:
        remaining = max(0.0, _cooldown_until - time.time())
    return remaining > 0, round(remaining)


def reset_breaker():
    """Testlər və əl ilə bərpa üçün."""
    global _fail_streak, _cooldown_until
    with _lock:
        _fail_streak = 0
        _cooldown_until = 0.0


def _note_success():
    global _fail_streak
    with _lock:
        _fail_streak = 0


def _note_failure():
    global _fail_streak, _cooldown_until
    with _lock:
        _fail_streak += 1
        if _fail_streak >= FAIL_TRIP:
            _cooldown_until = time.time() + COOLDOWN


def _tripped():
    with _lock:
        return _fail_streak >= FAIL_TRIP and time.time() < _cooldown_until


def parse_input(text, *, allow_ambiguous=False):
    """«Link + maya» siyahısını oxu.

    İki format işləyir (ARMA-nın `wa_block()` çıxışı ilə eynidir):
        https://birmarket.az/product/2579394-raf
        275 ₼
    və ya eyni sətirdə:
        https://birmarket.az/product/2579394-raf — 275

    DİQQƏT: maya YALNIZ linkdən SONRA gələn mətndən oxunur — linkin öz
    içindəki rəqəmlər (məs. .../raf-r-611-g-...) maya kimi qəbul edilmir.

    Birmənalı olmayan maya («1.234») sətri XƏTA ilə qaytarılır, susdurulmur
    (tapıntı K1: köhnə sistem bunu 1.234 oxuyub məhsulu 2.99 ₼-ə elan edirdi).

    Qaytarır: (items, errors) — errors: [(xam_sətir, mesaj)]
    """
    items, errors, pending = [], [], None

    def _flush(cur):
        if cur is not None:
            items.append(cur)

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = URL_RE.search(line)
        if m:
            _flush(pending)
            pending = {"id": int(m.group(1)), "url": m.group(0), "maya": None}
            rest = line[m.end():].replace("₼", " ").replace("—", " ").replace("-", " ")
            if rest.strip():
                try:
                    pending["maya"] = parse_money(rest, allow_ambiguous=allow_ambiguous)
                except AmbiguousMoney as e:
                    errors.append((line, str(e)))
                except ValueError:
                    pass                     # maya növbəti sətirdə ola bilər
            continue
        if pending is not None and pending["maya"] is None:
            try:
                pending["maya"] = parse_money(line, allow_ambiguous=allow_ambiguous)
            except AmbiguousMoney as e:
                errors.append((line, str(e)))
            except ValueError:
                pass
    _flush(pending)

    # Eyni məhsul iki dəfə yazılıbsa: İLK maya qalır, istifadəçi xəbərdar olur.
    seen, out = {}, []
    for it in items:
        if it["id"] in seen:
            if seen[it["id"]]["maya"] != it["maya"]:
                errors.append((it["url"],
                               f"Məhsul {it['id']} iki dəfə yazılıb, fərqli maya ilə — "
                               f"birincisi ({seen[it['id']]['maya']}) götürüldü."))
            continue
        seen[it["id"]] = it
        out.append(it)

    for it in out:
        if it["maya"] is None:
            errors.append((it["url"], f"Məhsul {it['id']} üçün maya qiyməti yoxdur."))
    return out, errors


def _offers_from(payload):
    out = []
    for x in (payload or {}).get("offers", []) or []:
        seller = (((x.get("seller") or {}).get("marketing_name") or {}).get("name") or "")
        try:
            price = money(x.get("retail_price") or 0)
            old = money(x.get("old_price") or 0)
        except Exception:
            continue
        out.append({"seller": seller, "price": price, "old": old})
    return out


def fetch_product(pid, *, timeout=20, session=None):
    """Bir məhsulun məlumatı + satıcı təklifləri.

    `status` heç vaxt uydurulmur: sorğu alınmasa `error` dolur və `status`
    None qalır — qiymət motoru bunu SKIP kimi görür, «satıcı yoxdur» kimi yox.
    """
    if _tripped():
        _, remaining = breaker_state()
        return {"id": pid, "name": "", "status": None, "category": "", "offers": [],
                "error": f"sürət limiti — {remaining} saniyə gözlənilir"}

    s = session or requests.Session()
    blank = {"id": pid, "name": "", "status": None, "category": "", "offers": []}
    try:
        p = s.get(API.format(id=pid), headers=HEADERS, timeout=timeout)
        if p.status_code in (429, 503):
            _note_failure()
            return dict(blank, error=f"sürət limiti ({p.status_code})")
        if p.status_code != 200:
            _note_failure()
            return dict(blank, error=f"product HTTP {p.status_code}")
        pj = p.json()

        offers, offers_error = [], None
        o = s.get(API.format(id=pid) + "/offers", headers=HEADERS, timeout=timeout)
        if o.status_code == 200:
            offers = _offers_from(o.json())
        elif o.status_code in (429, 503):
            _note_failure()
            offers_error = f"təkliflər alınmadı: sürət limiti ({o.status_code})"
        else:
            offers_error = f"təkliflər alınmadı: HTTP {o.status_code}"

        _note_success()
        return {
            "id": pid,
            "name": pj.get("name", "") or "",
            "status": pj.get("status") or None,
            "category": ((pj.get("category") or {}).get("name") or ""),
            "offers": offers,
            # Təkliflər alınmayıbsa bunu SUSDURMA: boş siyahı «rəqib yoxdur»
            # kimi oxunmamalıdır (tapıntı K2 ilə eyni sinif səhv).
            "error": offers_error,
        }
    except Exception as e:
        _note_failure()
        return dict(blank, error=str(e))


def fetch_many(ids, *, workers=4, timeout=20):
    """Bir neçə məhsulu paralel çək. Thread sayı 6-dan 4-ə salındı —
    sürət limitinə dəyməmək üçün (tapıntı O4)."""
    ids = list(ids)
    if not ids:
        return []
    session = requests.Session()
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(ids)))) as ex:
        return list(ex.map(lambda i: fetch_product(i, timeout=timeout,
                                                   session=session), ids))
