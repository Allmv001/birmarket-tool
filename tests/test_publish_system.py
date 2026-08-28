# -*- coding: utf-8 -*-
"""Yayın sisteminin testləri — 27.08.2026 auditindəki hər tapıntı üçün bir test.

Hər testin adında tapıntı kodu var (K1, K2, K3, K4, Y1, Y2, Y3, Y4, O3, O5).
Belə ki, gələcəkdə kimsə düzəlişi geri qaytarsa hansı zərərin qayıtdığı
dərhal görünsün.
"""
import os
import sys
import tempfile
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arma import create_app                                          # noqa: E402
from arma import catalog, money, pricing, publish                    # noqa: E402
from arma.db import connect                                          # noqa: E402
from arma.executor import Runner                                     # noqa: E402
from arma.security import origin_allowed                             # noqa: E402


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
    """Quru koşudan keçmiş, canlıya hazır bir sətir yarat."""
    pid = publish.upsert(con, product_id, "", _fields(), publish.PLANNED)
    con.commit()
    publish.dry_run(con, [pid], opts)
    assert publish.get(con, pid)["state"] == publish.DRY_RUN
    return pid


class FakeRunner:
    """Brauzersiz `Runner` əvəzi — `publish.execute()` yalnız bu metodu tanıyır."""

    def __init__(self, outcome=None):
        self.outcome = outcome or {"ok": True, "needs_review": False,
                                   "message": "", "detail": "test"}
        self.calls = []

    def publish_one(self, row):
        self.calls.append(row["product_id"])
        return self.outcome


# ------------------------------------------------------------------ fixtures
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


# ================================================================== money / K1
class TestMoney:
    @pytest.mark.parametrize("raw,expected", [
        ("275", "275"),
        ("275,50", "275.50"),
        ("275.50", "275.50"),
        ("1.234,56", "1234.56"),        # nöqtə minlik, vergül onluq
        ("1,234.56", "1234.56"),        # vergül minlik, nöqtə onluq
        ("1 234,56", "1234.56"),        # boşluq minlik
        ("1.234.567", "1234567"),       # təkrarlanan ayırıcı = minlik
        ("68 manat", "68"),
        ("12.5", "12.5"),
    ])
    def test_parses_known_formats(self, raw, expected):
        assert money.parse_money(raw) == Decimal(expected)

    @pytest.mark.parametrize("raw", ["1.234", "1,234", "12.500", "9,999"])
    def test_K1_ambiguous_is_refused_not_guessed(self, raw):
        """K1 — «1.234» 1234 yoxsa 1.234? Təxmin 12 000 ₼-a başa gəlmişdi."""
        with pytest.raises(money.AmbiguousMoney):
            money.parse_money(raw)

    def test_K1_ambiguous_can_be_forced_when_human_confirms(self):
        assert money.parse_money("1.234", allow_ambiguous=True) == Decimal("1234")

    def test_ambiguous_error_offers_both_readings(self):
        with pytest.raises(money.AmbiguousMoney) as e:
            money.parse_money("1.234")
        assert e.value.candidates == [Decimal("1.234"), Decimal("1234")]

    def test_rejects_garbage_and_negative(self):
        with pytest.raises(money.InvalidMoney):
            money.parse_money("filan")
        with pytest.raises(money.InvalidMoney):
            money.parse_money("-5")

    def test_roundup_matches_excel_semantics(self):
        assert money.roundup("897.98") == Decimal("898")
        assert money.roundup("898.00") == Decimal("898")
        assert money.roundup("898.01") == Decimal("899")

    def test_roundup_quantizes_before_ceiling_to_kill_float_residue(self):
        """QƏSDƏN belədir: əvvəl qəpiyə, sonra tam ədədə.

        Birbaşa ceil olsaydı float qalığı olan 898.0000001 -> 899 verərdi və
        qızıl dəyər kohne_qiymet(448.99, 275) = 902.75 pozulardı.
        """
        assert money.roundup("898.0000001") == Decimal("898")
        assert money.roundup("898.004") == Decimal("898")


# ================================================================== pricing
class TestPricing:
    def test_golden_values_unchanged(self):
        """Düsturlar Allam-ın qərarıdır — Decimal-a keçid dəyəri DƏYİŞMƏMƏLİDİR."""
        assert pricing.kohne_qiymet("448.99", "275") == Decimal("902.75")
        assert pricing.kohne_qiymet("27.99", "12") == Decimal("61.12")
        assert pricing.endirimli_no_seller("50") == Decimal("84.99")

    def test_undercuts_cheapest_competitor_by_one_qepik(self):
        d = pricing.evaluate([{"seller": "Rakip A", "price": "100.00"}], "60")
        assert d["verdict"] == pricing.OK
        assert d["endirimli"] == Decimal("99.99")

    @pytest.mark.parametrize("status", ["inactive", "blocked", "draft", None, ""])
    def test_K2_non_active_status_never_becomes_no_seller(self, status):
        """K2 — rəqib 100 ₼-ə satır, status active deyil.

        Köhnə sistem RƏQİB YOXDUR sanıb maya×1.70 = 101.99 qoyurdu (rəqibin iki
        qatı) və səbəb sütununda «Satıcı yoxdur» yazırdı — cədvəldə görünmürdü.
        """
        offers = [{"seller": "Rakip A", "price": "100.00"}]
        d = pricing.evaluate(offers, "60", status=status)
        assert d["verdict"] == pricing.SKIP
        assert d["reason_code"] == pricing.R_STATUS_NOT_ACTIVE
        assert d["endirimli"] is None

    def test_genuine_no_seller_still_uses_markup(self):
        d = pricing.evaluate([], "50", status="active")
        assert d["verdict"] == pricing.OK
        assert d["reason_code"] == pricing.R_NO_SELLER
        assert d["endirimli"] == Decimal("84.99")

    def test_fetch_error_is_error_not_no_seller(self):
        d = pricing.evaluate([], "50", fetch_error="HTTP 500")
        assert d["verdict"] == pricing.ERROR
        assert d["endirimli"] is None

    def test_own_store_wins_over_everything(self):
        offers = [{"seller": "Spark Tech", "price": "90.00"}]
        d = pricing.evaluate(offers, "60", status="inactive")
        assert d["reason_code"] == pricing.R_OWN_STORE

    def test_low_margin_is_skipped(self):
        d = pricing.evaluate([{"seller": "X", "price": "62.00"}], "60")
        assert d["verdict"] == pricing.SKIP
        assert d["reason_code"] == pricing.R_LOW_MARGIN

    def test_Y3_manual_discount_rederives_old_price(self):
        """Y3 — köhnə `/update` endirimli ilə köhnəni müstəqil yazırdı."""
        d = pricing.evaluate([{"seller": "X", "price": "100.00"}], "60")
        assert (d["endirimli"], d["kohne"]) == (Decimal("99.99"), Decimal("205.60"))
        pricing.recompute(d, "80.00")
        assert d["kohne"] == Decimal("165.60")
        assert d["ust"] == Decimal("100.00")

    def test_Y1_limits_sane_rejects_inverted_and_zero(self):
        assert pricing.limits_sane("75", "120")
        assert not pricing.limits_sane("120", "75")     # tərs
        assert not pricing.limits_sane("75", "75")      # bərabər
        assert not pricing.limits_sane("0", "75")
        assert not pricing.limits_sane(None, "75")

    def test_discount_never_lands_below_cost(self):
        d = pricing.evaluate([{"seller": "X", "price": "64.00"}], "60")
        assert d["verdict"] == pricing.OK
        assert d["endirimli"] > Decimal("60")


# ================================================================== catalog
class TestCatalogParsing:
    def test_link_internal_digits_are_not_cost(self):
        items, _ = catalog.parse_input(
            "https://birmarket.az/product/2579394-raf-r-611-g-toster\n275 ₼")
        assert items[0]["id"] == 2579394
        assert items[0]["maya"] == Decimal("275")

    def test_same_line_format(self):
        items, _ = catalog.parse_input("https://birmarket.az/product/123456-x — 48")
        assert items[0]["maya"] == Decimal("48")

    def test_reads_arma_wa_block_output_unchanged(self):
        """ARMA-nın `wa_block()` çıxışı birbaşa oxunmalıdır — insan kabel deyil."""
        from arma.services import wa_block
        text = wa_block(["https://birmarket.az/product/2579394-a",
                         "https://birmarket.az/product/2634681-b"], 275)
        items, errors = catalog.parse_input(text)
        assert [i["id"] for i in items] == [2579394, 2634681]
        assert all(i["maya"] == Decimal("275") for i in items)
        assert errors == []

    def test_K1_ambiguous_cost_is_reported_not_silently_wrong(self):
        items, errors = catalog.parse_input(
            "https://birmarket.az/product/999888-x\n1.234 ₼")
        assert errors, "birmənalı olmayan maya susdurulmamalıdır"
        assert items[0]["maya"] is None

    def test_missing_cost_is_reported(self):
        items, errors = catalog.parse_input("https://birmarket.az/product/999888-x")
        assert items[0]["maya"] is None
        assert any("maya" in m for _, m in errors)

    def test_duplicate_product_with_different_cost_warns(self):
        items, errors = catalog.parse_input(
            "https://birmarket.az/product/111-a\n10\n"
            "https://birmarket.az/product/111-a\n20")
        assert len(items) == 1
        assert any("iki dəfə" in m for _, m in errors)


# ================================================================== security K3
class TestCsrf:
    def test_K3_cross_origin_post_is_rejected(self, client):
        r = client.post("/api/publish/dry-run", json={"ids": [1]},
                        headers={"Origin": "https://kotu-site.example"})
        assert r.status_code == 403

    def test_K3_text_plain_body_is_not_parsed_as_json(self, client):
        """`get_json(force=True)` idi — məhz o, preflight-siz hücumu mümkün edirdi."""
        r = client.post("/api/publish/dry-run", data='{"ids":[1]}',
                        content_type="text/plain")
        assert r.status_code == 400          # gövdə oxunmur -> «seçilməyib»

    def test_same_origin_post_is_allowed(self, client):
        r = client.post("/api/publish/dry-run", json={"ids": []},
                        headers={"Origin": "http://localhost"})
        assert r.status_code == 400          # CSRF keçdi, məzmun boşdur

    def test_headerless_request_allowed_because_not_a_csrf_vector(self, client):
        r = client.post("/api/publish/retry", json={})
        assert r.status_code == 200

    def test_get_requests_are_never_blocked(self, client):
        r = client.get("/publish", headers={"Origin": "https://kotu-site.example"})
        assert r.status_code == 200

    def test_origin_null_is_rejected(self, app):
        with app.test_request_context("/x", method="POST",
                                      headers={"Origin": "null"}):
            allowed, _ = origin_allowed()
            assert not allowed


# ================================================================== dəftər
class TestLedger:
    def test_upsert_is_idempotent(self, con):
        fields = _fields()
        a = publish.upsert(con, "111", "", fields, publish.PLANNED)
        b = publish.upsert(con, "111", "", fields, publish.PLANNED)
        con.commit()
        assert a == b
        assert len(publish.rows(con)) == 1

    def test_K4_live_refuses_rows_that_skipped_dry_run(self, con, opts):
        pid = publish.upsert(con, "111", "", _fields(), publish.PLANNED)
        con.commit()
        runner = FakeRunner()
        res = publish.execute(con, [pid], opts, runner, live=True, log=lambda m: None)
        assert res["written"] == 0
        assert runner.calls == [], "quru koşusuz sətir canlıya getməməlidir"

    def test_K4_execute_defaults_to_dry_run(self, con, opts):
        pid = publish.upsert(con, "111", "", _fields(), publish.PLANNED)
        con.commit()
        runner = FakeRunner()
        publish.execute(con, [pid], opts, runner, log=lambda m: None)
        assert runner.calls == [], "live=False ikən heç nə yazılmamalıdır"
        assert publish.get(con, pid)["state"] == publish.DRY_RUN

    def test_dry_run_blocks_insane_limits(self, con, opts):
        pid = publish.upsert(con, "111", "", _fields(bot_low=200, bot_high=100),
                             publish.PLANNED)
        con.commit()
        report = publish.dry_run(con, [pid], opts)
        assert report["ready"] == 0
        assert publish.get(con, pid)["state"] == publish.NEEDS_REVIEW

    def test_dry_run_blocks_selling_below_cost(self, con, opts):
        pid = publish.upsert(con, "111", "", _fields(cost=100, discount=90),
                             publish.PLANNED)
        con.commit()
        publish.dry_run(con, [pid], opts)
        row = publish.get(con, pid)
        assert row["state"] == publish.NEEDS_REVIEW
        assert "zərərinə" in row["last_error"]

    def test_happy_path_writes_and_marks_live(self, con, opts):
        pid = _ready_row(con, opts)
        res = publish.execute(con, [pid], opts, FakeRunner(), live=True,
                              log=lambda m: None, recheck=False)
        assert res["written"] == 1
        assert publish.get(con, pid)["state"] == publish.LIVE

    def test_Y2_already_exists_is_needs_review_not_success(self, con, opts):
        """Y2 — köhnə axın bunu «✅» kimi loglayıb qiyməti düzəltmirdi."""
        pid = _ready_row(con, opts)
        runner = FakeRunner({"ok": False, "needs_review": True,
                             "message": "Məhsul artıq mağazadadır"})
        res = publish.execute(con, [pid], opts, runner, live=True,
                              log=lambda m: None, recheck=False)
        assert res["written"] == 0
        assert publish.get(con, pid)["state"] == publish.NEEDS_REVIEW

    def test_O5_failure_is_recorded_and_retryable(self, con, opts):
        pid = _ready_row(con, opts)
        runner = FakeRunner({"ok": False, "needs_review": False, "message": "şəbəkə"})
        publish.execute(con, [pid], opts, runner, live=True, log=lambda m: None,
                        recheck=False)
        row = publish.get(con, pid)
        assert row["state"] == publish.FAILED
        assert row["attempts"] == 1
        assert publish.retry_failed(con) == 1
        assert publish.get(con, pid)["state"] == publish.DRY_RUN

    def test_O5_gives_up_after_max_attempts(self, con, opts):
        pid = _ready_row(con, opts)
        con.execute("UPDATE publications SET attempts=? WHERE id=?",
                    (publish.MAX_ATTEMPTS, pid))
        con.commit()
        runner = FakeRunner()
        publish.execute(con, [pid], opts, runner, live=True, log=lambda m: None,
                        recheck=False)
        assert runner.calls == []
        assert publish.get(con, pid)["state"] == publish.NEEDS_REVIEW

    def test_O3_recheck_blocks_when_competitor_moved(self, con, opts, monkeypatch):
        """O3 — analiz səhər, koşu günorta. Rəqib qiyməti dəyişibsə yazılmır."""
        pid = _ready_row(con, opts)
        monkeypatch.setattr(publish, "fetch_product", lambda pid_: {
            "id": pid_, "name": "x", "status": "active", "category": "",
            "offers": [{"seller": "Rakip A", "price": Decimal("70.00")}],
            "error": None})
        res = publish.execute(con, [pid], opts, FakeRunner(), live=True,
                              log=lambda m: None, recheck=True)
        assert res["written"] == 0
        row = publish.get(con, pid)
        assert row["state"] == publish.NEEDS_REVIEW
        assert "dəyişib" in row["last_error"]

    def test_stop_flag_halts_batch(self, con, opts):
        ids = [_ready_row(con, opts, product_id=str(i)) for i in (1, 2, 3)]
        runner = FakeRunner()
        publish.execute(con, ids, opts, runner, live=True, log=lambda m: None,
                        recheck=False, stop_flag=lambda: True)
        assert runner.calls == []

    def test_set_discount_rederives_all_derived_fields(self, con, opts):
        pid = publish.upsert(con, "111", "", _fields(cost=60, discount=99.99),
                             publish.PLANNED)
        con.commit()
        row = publish.set_discount(con, pid, Decimal("80.00"), opts)
        assert row["discount"] == 80.00
        assert row["price"] == 165.60
        assert row["bot_high"] == 100.00


# ================================================================== executor Y1
class TestExecutorGuards:
    def test_Y1_refuses_inverted_limits_without_touching_browser(self):
        r = Runner("https://bot.example", tempfile.mkdtemp())
        out = r.set_bot_limits("111", 200, 100)      # alt > üst
        assert "error" in out and "məntiqsiz" in out["error"]

    def test_ui_contract_is_declared_in_one_place(self):
        from arma import executor
        for key in ("search_input", "select_button", "create_button",
                    "save_button", "bot_low_label", "bot_high_label"):
            assert key in executor.UI


# ================================================================== routes Y4
class TestRoutes:
    def test_publish_page_renders(self, client):
        assert client.get("/publish").status_code == 200

    def test_Y4_missing_row_returns_404_not_500(self, client):
        r = client.post("/api/publish/999999/discount", json={"endirimli": "5"})
        assert r.status_code == 404

    def test_empty_selection_returns_400_not_500(self, client):
        assert client.post("/api/publish/dry-run", json={"ids": []}).status_code == 400

    def test_K4_live_without_confirmation_is_refused(self, client):
        r = client.post("/api/publish/live", json={"ids": [1]})
        assert r.status_code == 428

    def test_K4_live_with_wrong_confirmation_is_refused(self, client):
        r = client.post("/api/publish/live", json={"ids": [1], "confirm": "ok"})
        assert r.status_code == 428

    def test_plan_with_empty_text_does_not_crash(self, client):
        assert client.post("/publish/plan", data={"links": ""},
                           follow_redirects=True).status_code == 200

    def test_ambiguous_discount_edit_is_refused(self, client, con, opts):
        pid = publish.upsert(con, "111", "", _fields(), publish.PLANNED)
        con.commit()
        r = client.post(f"/api/publish/{pid}/discount", json={"endirimli": "1.234"})
        assert r.status_code == 400
        assert "birmənalı" in r.get_json()["error"]

    def test_state_endpoint_returns_counts(self, client):
        body = client.get("/api/publish/state").get_json()
        assert body["ok"] and "counts" in body
