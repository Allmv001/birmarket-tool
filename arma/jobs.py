# -*- coding: utf-8 -*-
"""Arxa fon işləri — vəziyyət BAZADA saxlanır.

v2.4-də toplu yoxlamanın proqresi `JOBS = {}` yaddaş lüğətində idi: server
yenidən başlayanda (və ya kod dəyişəndə auto-reload olanda) bütün proqres
itirdi və istifadəçi "tapşırıq tapılmadı" görürdü. İndi vəziyyət `jobs`
cədvəlindədir — səhifə yenilənsə də, server restart olsa da tarixçə qalır.

Ləğv etmə: `jobs.cancel=1` yazılır, işçi thread hər addımda oxuyur.
"""
import json
import threading
import traceback
import uuid
from datetime import datetime

from .db import connect, get_setting
from .services import check_summary, create_check, now_str, run_autosearch

_threads = {}
_threads_lock = threading.Lock()


# ------------------------------------------------------------------ CRUD
def create_job(con, kind, total=0, img_total=0, errors=None):
    job_id = uuid.uuid4().hex[:10]
    con.execute(
        "INSERT INTO jobs(id,kind,created_at,state,total,img_total,payload) "
        "VALUES (?,?,?,?,?,?,?)",
        (job_id, kind, now_str(), "queued", total, img_total,
         json.dumps({"results": [], "errors": list(errors or [])}, ensure_ascii=False)))
    con.commit()
    return job_id


def get_job(con, job_id):
    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    job = dict(row)
    try:
        payload = json.loads(job.get("payload") or "{}")
    except json.JSONDecodeError:
        payload = {}
    job["results"] = payload.get("results", [])
    job["errors"] = payload.get("errors", [])
    job["finished"] = job["state"] in ("done", "error", "cancelled")
    job["percent"] = round(100 * job["done"] / job["total"]) if job["total"] else 0
    return job


def recent_jobs(con, limit=10):
    return [get_job(con, r["id"]) for r in con.execute(
        "SELECT id FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))]


def cancel_job(con, job_id):
    con.execute("UPDATE jobs SET cancel=1 WHERE id=? AND state IN ('queued','running')",
                (job_id,))
    con.commit()


def _update(con, job_id, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))
    con.commit()


def _push(con, job_id, result=None, error=None):
    """Nəticə/xəta əlavə et (payload JSON-u oxu-yaz)."""
    row = con.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
    try:
        payload = json.loads(row["payload"] or "{}") if row else {}
    except json.JSONDecodeError:
        payload = {}
    payload.setdefault("results", [])
    payload.setdefault("errors", [])
    if result is not None:
        payload["results"].append(result)
    if error is not None:
        payload["errors"].append(error)
    con.execute("UPDATE jobs SET payload=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), job_id))
    con.commit()


def _cancelled(con, job_id):
    row = con.execute("SELECT cancel FROM jobs WHERE id=?", (job_id,)).fetchone()
    return bool(row and row["cancel"])


# ------------------------------------------------------------------ işçi
def _worker(db_path, job_id, txt_items, image_files, thr, brands, upload_dir, note):
    """Arxa fonda: şəkilləri oxu, sonra hər kodu bir-bir axtar."""
    from .vision import extract_from_image
    con = connect(db_path)
    api_key = get_setting(con, "api_key", "")
    try:
        _update(con, job_id, state="running")
        items = list(txt_items)

        for img_name, orig_name in image_files:
            if _cancelled(con, job_id):
                break
            _update(con, job_id, current=f"şəkil: {orig_name}")
            try:
                import os
                data = extract_from_image(os.path.join(upload_dir, img_name), api_key)
                items.append(dict(data, image=img_name))
            except Exception as e:
                _push(con, job_id, error=f"{orig_name}: {e}")
            con.execute("UPDATE jobs SET img_done=img_done+1 WHERE id=?", (job_id,))
            con.commit()

        _update(con, job_id, total=len(items))

        for it in items:
            if _cancelled(con, job_id):
                _update(con, job_id, state="cancelled", current=None,
                        finished_at=now_str())
                return
            _update(con, job_id, current=it["code"])
            cid = create_check(con, it["code"], it["cost"], thr,
                               ptype=it.get("type", ""), note=note,
                               brands=brands, image=it.get("image"))
            try:
                res = run_autosearch(con, cid, fast=True,
                                     should_stop=lambda: _cancelled(con, job_id))
                msg = res["message"]
            except Exception as e:
                msg = f"axtarış alınmadı: {e}"
                _push(con, job_id, error=f"{it['code']}: {e}")
            summary = check_summary(con, cid)
            _push(con, job_id, result={"id": cid, "code": it["code"],
                                       "cost": it["cost"], "msg": msg, **summary})
            con.execute("UPDATE jobs SET done=done+1 WHERE id=?", (job_id,))
            con.commit()

        _update(con, job_id, state="done", current=None, finished_at=now_str())
    except Exception as e:
        _push(con, job_id, error=f"Gözlənilməz xəta: {e}\n{traceback.format_exc(limit=3)}")
        _update(con, job_id, state="error", current=None, finished_at=now_str())
    finally:
        con.close()
        with _threads_lock:
            _threads.pop(job_id, None)


def start_batch(db_path, job_id, txt_items, image_files, thr, brands, upload_dir,
                note="Toplu yoxlama"):
    """İşçi thread-i başlat."""
    t = threading.Thread(
        target=_worker,
        args=(db_path, job_id, txt_items, image_files, thr, brands, upload_dir, note),
        daemon=True)
    with _threads_lock:
        _threads[job_id] = t
    t.start()
    return job_id
