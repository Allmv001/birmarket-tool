# -*- coding: utf-8 -*-
"""
Şəkildən kod + maya qiyməti çıxarışı — Claude API (vision) ilə.

API açarı Ayarlar səhifəsində saxlanır (data/birmarket.db, yalnız lokal).
QEYD: topdan qruplarında kod və qiymət adətən şəklin ALTINDAKI mətndədir —
əvvəlcə 💬 WhatsApp səhifəsini yoxlayın, o pulsuzdur və daha dəqiqdir.
Bu modul yalnız mətn olmayan hallar üçündür.
"""
import base64
import json
import mimetypes
import os
import re

import requests

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5"
MAX_IMAGE_BYTES = 5 * 1024 * 1024      # Anthropic API şəkil həddi

PROMPT = """Bu şəkil WhatsApp topdan satış qrupundan bir məhsul elanıdır (Azərbaycan bazarı).
Şəkildən bu məlumatları çıxar və YALNIZ bir JSON obyekti qaytar, başqa heç nə yazma:
{"code": "<model kodu, məs. R.209>", "cost": <maya qiyməti manatla, ədəd>, "type": "<məhsul tipi, məs. sendviç cihazı>"}

Qaydalar:
- Kod adətən şəkildə ən böyük/qabarıq yazılan model nömrəsidir (R.209, R.2890, SF-401 kimi).
- Qiymət elan mətnindəki manat dəyəridir ("68 manat", "🔥68₼🔥" → 68). Taksit/kredit rəqəmlərini götürmə.
- "1600 vatt", "40 cm" kimi ölçü/güc rəqəmlərini NƏ kod, NƏ qiymət sayma.
- Hər hansı sahəni tapa bilmirsənsə ora null yaz."""


def extract_from_image(path: str, api_key: str, model: str = DEFAULT_MODEL,
                       timeout: int = 90) -> dict:
    """Şəkildən {"code","cost","type"} çıxar. Xətada exception atır."""
    if not api_key:
        raise RuntimeError("Claude API açarı təyin olunmayıb (Ayarlar səhifəsi).")
    size = os.path.getsize(path)
    if size > MAX_IMAGE_BYTES:
        raise RuntimeError(
            f"Şəkil çox böyükdür ({size // 1024} KB, hədd {MAX_IMAGE_BYTES // 1024} KB) — "
            "kiçildib yenidən cəhd edin.")
    mime = mimetypes.guess_type(path)[0] or "image/png"
    if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        mime = "image/png"
    with open(path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()
    payload = {
        "model": model,
        "max_tokens": 300,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": mime, "data": b64}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    }
    r = requests.post(API_URL, json=payload, timeout=timeout, headers={
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    if r.status_code == 401:
        raise RuntimeError("API açarı yanlışdır (401). Ayarlarda yoxlayın.")
    if r.status_code == 429:
        raise RuntimeError("API sürət limiti (429) — bir az sonra yenidən cəhd edin.")
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"Modeldən JSON alınmadı: {text[:120]}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Modelin JSON cavabı oxunmadı: {e}")
    code = (data.get("code") or "").strip()
    cost = data.get("cost")
    if not code or cost in (None, ""):
        raise RuntimeError(f"Şəkildən kod/qiymət oxunmadı: {data}")
    cost = float(cost)
    if cost <= 0:
        raise RuntimeError(f"Şəkildən oxunan maya qiyməti düzgün deyil: {cost}")
    return {"code": code, "cost": cost, "type": (data.get("type") or "").strip()}
