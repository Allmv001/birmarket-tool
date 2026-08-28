# -*- coding: utf-8 -*-
"""28.08.2026 ÜRETİM RAPORUNDAN çıxan tapıntıların testləri.

Raporu başqa bir oturum yazdı: köhnə `birmarket-tool` üretimdə 246 məhsulun
HAMISINDA `Page.evaluate: TypeError: ... (reading 'click')` verib, xəta
yutulub və iş «bitdi» görünüb — halbuki heç nə yazılmayıb.

Buradakı hər test o raporun doğruladığı bir arızanı kilidləyir.
"""
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arma import create_app                                          # noqa: E402
from arma import executor, pricing, publish                          # noqa: E402
from arma.db import connect, get_setting                             # noqa: E402


# ------------------------------------------------------------------ köməkçilər
def _fields(**over):
    base = {"url": "https://birmarket.az/product/111-x", "name": "Test",
            "cost": 60.0, "price": 205.60, "discount": 99.99,
            "bot_low": 75.0, "bot_high": 119.99, "qty": 10,
            "verdict": pricing.OK, "reason_code": pricing.R_COMPETITOR,
            "reason": "test"}
    base.update(over)
    return base


def _ready_row(con, opts, product_id="111"):
    pid = publish.upsert(con, product_id, "", _fields(), publish.PLANNED)
    con.commit()
    publish.dry_run(con, [pid], opts)
    assert publish.get(con, pid)["state"] == publish.DRY_RUN
    return pid


class FakeRunner:
    def __init__(self, outcome=None):
        self.outcome = outcome or {"ok": True, "needs_review": False,
                                   "message": "", "detail": "test"}
        self.calls = []

    def publish_one(self, row):
        self.calls.append(row["product_id"])
        return self.outcome


@pytest.fixture()
def app():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    application = create_app(base_dir=base, db_path=path)
    application.config.update(TESTING=True)
    yield application
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def con(app):
    c = connect(app.config["DB_PATH"])
    yield c
    c.close()


@pytest.fixture()
def opts():
    return {
        "own_stores": list(pricing.DEFAULT_OWN_STORES),
        "min_margin": pricing.DEFAULT_MIN_MARGIN,
        "markup": pricing.DEFAULT_MARKUP,
        "bot_low": pricing.DEFAULT_BOT_LOW,
        "bot_high_plus": pricing.DEFAULT_BOT_HIGH_PLUS,
        "qty": pricing.DEFAULT_QTY,
        "store": "",
        "bot_url": "https://bot.example",
    }


# ================================================================== R1
class TestUnguardedClick:
    """Üretim arızası: `.click()` undefined üzərində çağırılırdı.

    Köhnə `automation.py`-də üç belə yer vardı:
        cr.click()               <- find() undefined qaytara bilər
        pen().click()            <- funksiya nəticəsinə birbaşa
        .find(...).click()       <- zəncirin sonunda
    """

    #: Məhz sınan naxış: çağırış nəticəsinə birbaşa `.click()`
    BAD = re.compile(r"(?:find\([^;\n]*\)|\w+\(\))\.click\(\)")

    def _js(self):
        return "\n".join([executor.JS_PREFLIGHT, executor.JS_CREATE,
                          executor.JS_BOT_LIMITS])

    def test_no_call_result_is_clicked_directly(self):
        bad = self.BAD.findall(self._js())
        assert not bad, f"qorunmamış .click() var: {bad}"

    def test_every_clicked_variable_is_null_checked(self):
        js = self._js()
        for name in sorted(set(re.findall(r"\b(\w+)\.click\(\)", js))):
            guarded = re.search(rf"if \(!{name}\)", js) or re.search(rf"if \({name}\)", js)
            assert guarded, f"«{name}» yoxlanmadan klikləyir"

    def test_the_detector_itself_works(self):
        """Testin özü işləyirmi? Köhnə naxışlar TUTULMALIDIR."""
        assert self.BAD.findall("pen().click();")
        assert self.BAD.findall("btns.find(b => b.textContent === 'x').click();")
        assert not self.BAD.findall("if (!cr) return; cr.click();")


# ================================================================== R2
class TestResumeAfterInterruption:
    """Üretim arızası: 246-lıq iş kəsildi, harada qaldığı ÖYRƏNİLƏ BİLMƏDİ,
    istifadəçi ~173 rəqəmini əl ilə təxmin etdi, iş baştan başladı və ilk
    ~173 məhsul İKİNCİ DƏFƏ işləndi."""

    def test_written_rows_are_not_reprocessed(self, con, opts):
        ids = [_ready_row(con, opts, product_id=str(i)) for i in (1, 2, 3)]
        publish.execute(con, [ids[0]], opts, FakeRunner(), live=True,
                        log=lambda m: None, recheck=False)
        assert publish.get(con, ids[0])["state"] == publish.LIVE

        again = FakeRunner()
        res = publish.execute(con, ids, opts, again, live=True,
                              log=lambda m: None, recheck=False)
        assert res["written"] == 2, "yalnız qalan iki sətir yazılmalıdır"
        assert "1" not in again.calls, "yazılmış məhsul təkrar işləndi"
        assert sorted(again.calls) == ["2", "3"]

    def test_pending_answers_where_did_i_stop(self, con, opts):
        ids = [_ready_row(con, opts, product_id=str(i)) for i in (1, 2, 3)]
        publish.execute(con, [ids[0]], opts, FakeRunner(), live=True,
                        log=lambda m: None, recheck=False)
        assert publish.pending(con)["ready"] == 2

    def test_progress_survives_a_new_connection(self, app, opts):
        """Server yenidən başlasa da irəliləyiş qalmalıdır (yaddaşda deyil)."""
        c1 = connect(app.config["DB_PATH"])
        pid = _ready_row(c1, opts, product_id="42")
        publish.execute(c1, [pid], opts, FakeRunner(), live=True,
                        log=lambda m: None, recheck=False)
        c1.close()

        c2 = connect(app.config["DB_PATH"])          # «server yenidən başladı»
        try:
            assert publish.get(c2, pid)["state"] == publish.LIVE
            assert publish.pending(c2)["ready"] == 0
        finally:
            c2.close()


# ================================================================== R3
class TestFailuresAreLoud:
    """Üretim arızası: xəta yutulurdu, sonda xülasə yox idi, iş «bitdi» görünürdü."""

    def test_summary_is_always_logged(self, con, opts):
        pid = _ready_row(con, opts)
        lines = []
        publish.execute(con, [pid], opts,
                        FakeRunner({"ok": False, "needs_review": False,
                                    "message": "şəbəkə"}),
                        live=True, log=lines.append, recheck=False)
        assert any("XÜLASƏ" in x for x in lines)
        assert any("Alınmayanlar" in x for x in lines)

    def test_failed_ids_are_returned_not_only_counted(self, con, opts):
        pid = _ready_row(con, opts, product_id="777")
        res = publish.execute(con, [pid], opts,
                              FakeRunner({"ok": False, "needs_review": False,
                                          "message": "x"}),
                              live=True, log=lambda m: None, recheck=False)
        assert res["failed_ids"] == ["777"]

    def test_remaining_count_is_reported(self, con, opts):
        ids = [_ready_row(con, opts, product_id=str(i)) for i in (1, 2)]
        res = publish.execute(con, ids, opts, FakeRunner(), live=True,
                              log=lambda m: None, recheck=False)
        assert res["remaining"] == 0


# ================================================================== R4
class TestNoSellerBranch:
    """Rapor: incelenen 173 sətirin 13-ü (%7.5) rəqibsiz idi; qiymət tamamilə
    `maya × markup`-dan gəlirdi. Bu daldakı səhv görünmür — müqayisə ediləcək
    rəqib yoxdur."""

    def test_blocked_without_acknowledgement(self, con, opts):
        pid = publish.upsert(con, "111", "",
                             _fields(reason_code=pricing.R_NO_SELLER),
                             publish.PLANNED)
        con.commit()
        report = publish.dry_run(con, [pid], opts)
        assert report["ready"] == 0
        assert report["no_seller"] == 1
        assert publish.get(con, pid)["state"] == publish.NEEDS_REVIEW

    def test_passes_when_acknowledged(self, con, opts):
        pid = publish.upsert(con, "111", "",
                             _fields(reason_code=pricing.R_NO_SELLER),
                             publish.PLANNED)
        con.commit()
        assert publish.dry_run(con, [pid], opts, allow_no_seller=True)["ready"] == 1
        assert publish.get(con, pid)["state"] == publish.DRY_RUN

    def test_normal_rows_unaffected(self, con, opts):
        pid = publish.upsert(con, "111", "", _fields(), publish.PLANNED)
        con.commit()
        assert publish.dry_run(con, [pid], opts)["ready"] == 1


# ================================================================== öz səhvim
class TestPublishSettingsReachable:
    """28.08.2026: səkkiz yayın ayarının UI qarşılığı yox idi, ona görə
    `bot_url` heç yerdən verilə bilmirdi və canlı yazı ƏLÇATMAZ idi."""

    FORM = {"margin_pct": "20", "wa_number": "994503377176",
            "own_stores": "Spark Tech, Pro Tech", "min_margin": "5.50",
            "markup": "1.80", "bot_low": "1.30", "bot_high_plus": "25",
            "publish_qty": "7", "publish_store": "magaza-1",
            "bot_url": "https://bot.example"}

    def _opts_from_db(self, app):
        c = connect(app.config["DB_PATH"])
        try:
            return publish.settings_from(
                c, get_setting,
                lambda con, k, d: float(get_setting(con, k, d)),
                lambda con, k, d: int(float(get_setting(con, k, d))))
        finally:
            c.close()

    def test_settings_page_exposes_every_publish_field(self, client):
        html = client.get("/settings").get_data(as_text=True)
        for field in ("bot_url", "own_stores", "min_margin", "markup",
                      "bot_low", "bot_high_plus", "publish_qty", "publish_store"):
            assert f'name="{field}"' in html, f"{field} üçün sahə yoxdur"

    def test_roundtrip_reaches_the_pricing_engine(self, client, app):
        client.post("/settings", data=self.FORM, follow_redirects=True)
        opts = self._opts_from_db(app)
        assert opts["bot_url"] == "https://bot.example"
        assert opts["own_stores"] == ["Spark Tech", "Pro Tech"]
        assert float(opts["markup"]) == 1.80
        assert float(opts["min_margin"]) == 5.50
        assert opts["qty"] == 7

    def test_bad_margin_no_longer_silently_drops_publish_settings(self, client, app):
        """Blok əvvəl `margin_pct` yoxlamasının ARXASINDA idi: səhv marja
        yazanda yayın ayarları səssizcə itirdi."""
        bad = dict(self.FORM, margin_pct="abc", bot_url="https://kept.example")
        client.post("/settings", data=bad, follow_redirects=True)
        c = connect(app.config["DB_PATH"])
        try:
            assert get_setting(c, "bot_url", "") == "https://kept.example"
        finally:
            c.close()

    def test_live_publish_reachable_once_bot_url_is_set(self, client, con, opts):
        client.post("/settings", data=self.FORM, follow_redirects=True)
        pid = _ready_row(con, opts)
        r = client.post("/api/publish/live", json={"ids": [pid], "confirm": "CANLI"})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["ok"] is True


# ================================================================== log faylı
def test_crash_leaves_a_trace_on_disk(app):
    """Rapor 4.2: tətbiq çöküb, geriyə baxmaq üçün HEÇ BİR iz yox idi."""
    app.logger.error("test-yazısı")
    path = os.path.join(app.config["BASE_DIR"], "data", "arma.log")
    assert os.path.exists(path), "log faylı yaradılmadı"
