# -*- coding: utf-8 -*-
"""SQLite qatı: sxem, miqrasiya, bağlantı idarəsi.

v2.4-dən fərqlər:
  * WAL rejimi + busy_timeout — arxa fon axtarışı gedərkən səhifə açmaq artıq
    "database is locked" vermir (v2.4-də toplu yoxlama zamanı real problem idi).
  * init_db() tətbiq fabrikində çağırılır — `flask run` və ya WSGI altında da
    baza qurulur (v2.4-də yalnız `python app.py` yolunda idi).
  * jobs cədvəli: toplu yoxlama vəziyyəti yaddaşda deyil, bazada — server
    yenidən başlayanda proqres itmir.
"""
import os
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  code TEXT NOT NULL,
  cost REAL NOT NULL,
  ptype TEXT, note TEXT,
  threshold REAL NOT NULL,
  image TEXT,
  brands TEXT
);

CREATE TABLE IF NOT EXISTS offers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  check_id INTEGER NOT NULL REFERENCES checks(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  seller TEXT,
  price REAL NOT NULL,
  old_price REAL,
  url TEXT,
  code_match INTEGER NOT NULL,
  margin REAL NOT NULL,
  is_match INTEGER NOT NULL,
  picked INTEGER
);

CREATE TABLE IF NOT EXISTS wa_groups (
  name TEXT PRIMARY KEY,
  text TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  offer_id INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
  changed_at TEXT NOT NULL,
  old_price REAL,
  new_price REAL
);

-- v4: toplu iş vəziyyəti bazada saxlanır (v2.4-də yaddaşdakı dict idi və
-- server restartında bütün proqres itirdi)
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  state TEXT NOT NULL,              -- queued | running | done | error | cancelled
  current TEXT,
  total INTEGER NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0,
  img_total INTEGER NOT NULL DEFAULT 0,
  img_done INTEGER NOT NULL DEFAULT 0,
  payload TEXT,                     -- JSON: nəticələr + xətalar
  cancel INTEGER NOT NULL DEFAULT 0
);

-- v4.1: YAYIN DƏFTƏRİ — hansı məhsul, hansı qiymətlə, nə vaxt yazıldı.
-- Dörd problemi birdən həll edir (27.08.2026 auditi):
--   * idempotentlik  — UNIQUE(product_id, store): eyni məhsul iki dəfə açılmır
--   * təkrar cəhd    — attempts/last_error: uğursuzlar növbədə qalır
--   * denetim izi    — hansı qiymət nə vaxt yazıldı, geriyə oxunur
--   * «artıq var» halı — köhnə sistem bunu uğur kimi loglayıb qiyməti
--     düzəltmirdi (tapıntı Y2); indi dəftər gözlənilən qiyməti bilir
CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id TEXT NOT NULL,
  store TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  url TEXT, name TEXT,
  cost REAL NOT NULL,
  price REAL, discount REAL, bot_low REAL, bot_high REAL, qty INTEGER,
  verdict TEXT, reason_code TEXT, reason TEXT,
  -- planned | dry_run | live | failed | skipped | needs_review
  state TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  job_id TEXT,
  UNIQUE(product_id, store)
);

CREATE INDEX IF NOT EXISTS idx_pub_state ON publications(state);
CREATE INDEX IF NOT EXISTS idx_pub_job ON publications(job_id);
CREATE INDEX IF NOT EXISTS idx_offers_check ON offers(check_id);
CREATE INDEX IF NOT EXISTS idx_offers_match ON offers(check_id, is_match);
CREATE INDEX IF NOT EXISTS idx_checks_created ON checks(created_at);
CREATE INDEX IF NOT EXISTS idx_checks_code ON checks(code);
CREATE INDEX IF NOT EXISTS idx_ph_offer ON price_history(offer_id);
"""

# "aktiv link" şərti — istifadəçi işarələyibsə onun seçimi, yoxsa avtomatik uyğunluq
PICKED_COND = "(o.picked=1 OR (o.picked IS NULL AND o.is_match=1))"


def _tune(con: sqlite3.Connection) -> sqlite3.Connection:
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    # WAL: oxucular yazıcını, yazıcı oxucuları bloklamır — arxa fon axtarışı
    # gedərkən panel/səhifə açmaq üçün şərtdir
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=10000")
    return con


def connect(db_path: str) -> sqlite3.Connection:
    """Flask konteksti olmadan bağlantı (arxa fon thread-ləri üçün)."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return _tune(sqlite3.connect(db_path, timeout=10))


def db() -> sqlite3.Connection:
    """Cari sorğunun bağlantısı."""
    if "db" not in g:
        g.db = connect(current_app.config["DB_PATH"])
    return g.db


def close_db(_exc=None):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def _migrate(con: sqlite3.Connection):
    """Köhnə bazanı yeni versiyaya yüksəlt (v1.x → v2.4 → v4)."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(checks)")]
    if cols and "brands" not in cols:
        con.execute("ALTER TABLE checks ADD COLUMN brands TEXT")
    ocols = [r[1] for r in con.execute("PRAGMA table_info(offers)")]
    if ocols and "picked" not in ocols:
        con.execute("ALTER TABLE offers ADD COLUMN picked INTEGER")


def init_db(db_path: str):
    """Sxemi qur, miqrasiyanı işlət. Hər tətbiq açılışında təhlükəsiz çağırılır."""
    con = connect(db_path)
    try:
        con.executescript(SCHEMA)
        _migrate(con)
        # yarımçıq qalmış işlər: server çökübsə "running" işləri təmizlə
        con.execute("UPDATE jobs SET state='error', "
                    "current='server yenidən başladı' WHERE state IN ('queued','running')")
        con.commit()
    finally:
        con.close()


# ------------------------------------------------------------------ settings
def get_setting(con, key, default=None):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(con, key, value):
    con.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))


def get_float_setting(con, key, default):
    try:
        return float(get_setting(con, key, default))
    except (TypeError, ValueError):
        return float(default)


def get_int_setting(con, key, default):
    try:
        return int(float(get_setting(con, key, default)))
    except (TypeError, ValueError):
        return int(default)
