# -*- coding: utf-8 -*-
"""ARMA v4 reqressiya testləri.

İşə salmaq:  .venv\\Scripts\\python.exe tests/test_arma.py
   (pytest varsa:  .venv\\Scripts\\python.exe -m pytest tests -q)

Testlərin əsas hissəsi v2.4-də REAL çökən halları qoruyur — hər biri
"v2.4: ..." şərhi ilə işarələnib ki, düzəliş təsadüfən geri alınmasın.
Testlər istehsal bazasına toxunmur: hər biri müvəqqəti boş SQLite yaradır.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arma import create_app                                            # noqa: E402
from arma.codes import (evaluate, match_level, search_variants,      # noqa: E402
                        widen_queries)
from arma.parsing import parse_batch_lines, parse_pasted_text          # noqa: E402
from arma.wa_parser import (extract_code, extract_price,               # noqa: E402
                            parse_whatsapp)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_client():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    app = create_app(base_dir=BASE, db_path=path)
    app.config["TESTING"] = True
    return app, app.test_client(), path


# ------------------------------------------------------------------ kod
def test_match_level():
    cases = [("Vafli cihazi RAF R.209", "R.209", 2),
             ("Sendvic RAF R209 800W", "R.209", 2),
             ("Sendvic r-209", "R.209", 2),
             ("Sendvic R.2809", "R.209", 0),
             ("Sendvic R.2090", "R.209", 0),
             ("RAF209 10in1", "R.209", 1),
             ("M.A.C Styler MC-22", "MAC MC-22", 2)]
    for name, code, want in cases:
        assert match_level(name, code) == want, (name, code)


def test_evaluate_zero_cost_is_never_a_match():
    """v2.4: maya=0 olanda hedd 0 x 1.20 = 0 idi, ona gore HER elan 'uygun'
    gorunurdu. Toplu rejimde bir sehv setir butun neticeni zibilleyirdi."""
    assert evaluate(0.0, 1.20, 83.99, "Vafli RAF R.209", "R.209")[2] is False
    assert evaluate(-5.0, 1.20, 83.99, "Vafli RAF R.209", "R.209")[2] is False
    assert evaluate(68.0, 1.20, 83.99, "Vafli RAF R.209", "R.209") == (2, 23.5, True)
    assert evaluate("68", "1.20", "83.99", "Vafli RAF R.209", "R.209")[2] is True


def test_evaluate_bad_input_does_not_raise():
    assert evaluate("abc", 1.2, 10, "x", "R.209") == (0, 0.0, False)


def test_widen_queries_from_a_found_listing():
    """Brend/tip BOS olanda sistem tapdigi elanin adindan sorgu duzeltmelidir.

    28.08.2026: istifadeci R.209-u sahelər bos halda axtardi ve BIR elan
    gordu; saytda YEDDI var idi. Tapilan o bir elanin adi (`Vafli cihazi
    RAF R.209`) qalan altisini tapan sorgunu ozunde dasiyirdi - `raf r.209`
    tek basina 118 yeni kart verir."""
    qs = widen_queries("Vafli cihazı RAF R.209", "R.209")
    assert "raf r.209" in qs, qs            # en mehsuldar sorgu

    # Tek sozluk prefiks daxil edilmir: «fen» saytin yarisini qaytarir,
    # hec ne elave etmeden suret limitini yeyir.
    assert "fen" not in widen_queries("Fen SF-401 2000 vatt", "SF 401")

    # Kod adin en evvelindedirse genisletmek ucun soz yoxdur
    assert widen_queries("R.209", "R.209") == []
    assert widen_queries("", "R.209") == []


def test_search_options_come_from_defaults():
    """Ayarlar bos olanda axtaris parametrleri DEFAULTS-dan gelmelidir.

    28.08.2026: services.fetch_options icinde defoltlar IKINCI DEFE el ile
    yazilmisdi ("max_pages", 3). DEFAULTS 5-e qaldirildi, axtaris yolu ise
    3-de qaldi - canlida istifadeci yene 5 elan gordu, 7 evezine. Bu test
    hemin iki menbenin ayrilmasini bloklayir."""
    from arma import DEFAULTS
    from arma.services import fetch_options
    app, client, path = make_client()
    try:
        with app.app_context():
            from arma.db import db
            opts = fetch_options(db())        # settings cedveli bosdur
        assert opts["max_pages"] == DEFAULTS["max_pages"], opts
        assert opts["max_variants"] == DEFAULTS["max_variants"], opts
        assert opts["delay"] == DEFAULTS["request_delay"], opts
    finally:
        os.unlink(path)


def test_page_depth_default_is_deep_enough():
    """Sehife derinliyi 5-den asagi dusmemelidir.

    28.08.2026-da birmarket-de R.209 ucun olculdu: `raf r.209` sorgusu
    tukendiyi yere qeder (171 elan) YEDDI kesin R.209 verir. Bunlardan
    ikisi - "R.209 800W Black" (89.99) ve "R.209 800 Vt paslanmayan polad"
    (99.98) - yalniz 4-5-ci sehifelerdedir. Defolt 3 ile sistem BESINI
    tapirdi, istifadeci ise ilk denemede BIRINI gordu.

    Bahalasma azdir: fetch_search dolu olmayan sehifede dayanir
    (len(cards) < 24), ona gore dar sorgular yene bir istekde bitir."""
    from arma import DEFAULTS
    assert DEFAULTS["max_pages"] >= 5, DEFAULTS["max_pages"]


def test_productive_variants_survive_the_cap():
    """auto_search siyahini max_variants (defolt 4) ile kesir. Brend/tip ile
    zenginlesdirilmis sorgular EN mehsuldardir - kesilen hissede qalmamalidir.

    28.08.2026: kohne sirada ilk dord yalniz ciplaq kod formalari idi. R.209
    ucun olculdu: `raf r.209` 71 yeni elan, `vafli cihazi r.209` 70 yeni,
    ciplaq dord variant cemi 3. birmarket-de BES gercek R.209 elani var idi,
    sistem BIRINI gosterirdi. Formdakı «tip + kod en genis neticeni verir»
    ipucu de bos vede olurdu."""
    ilk4 = search_variants("R.209", "raf", "vafli cihazi")[:4]
    assert any(v.startswith("raf ") for v in ilk4), ilk4
    assert any(v.startswith("vafli cihazi ") for v in ilk4), ilk4
    assert "r.209" in ilk4, ilk4
    # Brend/tip bos olanda davranis deyismir - ciplaq formalar qalir
    assert search_variants("R.209", "", "") == ["r.209", "r209", "r 209", "r-209"]


def test_search_variants():
    v = search_variants("R.209", "raf", "sendvic cihazi")
    assert "r.209" in v and "r209" in v and "raf r.209" in v
    assert "sendvic cihazi r.209" in v


# ------------------------------------------------------------- wa parser
def test_extract_code_ignores_units():
    """v2.4: 'Toster 1600 vatt 600 Vt' setrinden uydurma 'VATT-600' kodu
    cixirdi (olcu qoruyucusu yalniz brendli koda tetbiq olunurdu).
    README bunun isledigini yazirdi - islemirdi."""
    assert extract_code("Toster 1600 vatt 600 Vt 40 cm 35 manat") is None
    assert extract_code("Blender 500 W gucunde 25 manat") is None
    assert extract_code("Powerbank 20000 mAh 30 manat") is None
    assert extract_code("RAF 1600 vatt toster 48 manat") is None


def test_extract_code_finds_real_codes():
    assert extract_code("Raf 8111 Portativ qaz sobasi 22 manat") == "R.8111"
    assert extract_code("R.1345 Ceramic soleplate 58 manat") == "R.1345"
    assert extract_code("Teze geldi CF001B qas uz aparati 3.50 AZN") == "CF-001B"
    assert extract_code("Mikser MC-22 nabor 40 manat") == "MC-22"
    assert extract_code("Utu RAF 2603 1600 vatt 48 manat") == "R.2603"
    # v2.4: qiymet ("45 manat") kod tapilmasini bloklayirdi, cunki 'manat'
    # olcu vahidleri siyahisinda idi -> SF/LORD/SONIFER brendleri tanimirdi
    assert extract_code("Fen SF-401 2000 vatt 45 manat") == "SF 401"


def test_extract_price():
    assert extract_price("Boyuk toster 48 manat") == 48.0
    assert extract_price("qas uz aparati dest 3.50 AZN") == 3.5
    assert extract_price("Qas aparati 2.50 qepik") == 2.5
    assert extract_price("Toster 28 AZN") == 28.0


def test_parse_whatsapp_end_to_end():
    text = "\n".join([
        "Raf 8111 Portativ qaz sobasi 22 manat",
        "[18:05, 20.08.2026] +994 50 851 81 84: RAF 2603 Boyuk toster 48 manat",
        "Qas aparati Zaryatka ile isleyir 2.50 qepik",
        "Toster 1600 vatt 600 Vt 40 cm 35 manat",
    ])
    items, unknown = parse_whatsapp(text)
    codes = [i["code"] for i in items]
    assert "R.8111" in codes and "R.2603" in codes
    assert not any(c.startswith("VATT") for c in codes)
    assert len(unknown) == 2                       # kodsuz iki setir


def test_parse_whatsapp_dedup_keeps_latest_price():
    text = "RAF 2603 toster 48 manat\nRAF 2603 toster 44 manat"
    items, _ = parse_whatsapp(text)
    assert len(items) == 1 and items[0]["cost"] == 44.0


# --------------------------------------------------------------- parsing
def test_parse_batch_lines():
    items, bad = parse_batch_lines("R.209 68\nR.224, 48\nzibil setir\nR.300 0")
    assert [i["code"] for i in items] == ["R.209", "R.224"]
    assert len(bad) == 2                           # zibil + sifir maya


def test_parse_pasted_text():
    pasted = "\n".join(["-22 %", "83.99 ₼", "108.00 ₼",
                        "4.31 ₼ x 24 ay", "Vafli cihazi RAF R.209"])
    offers = parse_pasted_text(pasted, "R.209")
    assert len(offers) == 1
    assert offers[0]["price"] == 83.99 and offers[0]["old_price"] == 108.00


# ----------------------------------------------------------------- HTTP
def test_all_pages_render():
    app, client, path = make_client()
    try:
        for url in ["/", "/check/new", "/batch", "/whatsapp", "/links", "/settings"]:
            r = client.get(url)
            assert r.status_code == 200, (url, r.status_code)
            assert b"ARMA" in r.data
    finally:
        os.unlink(path)


def test_missing_check_returns_404_not_500():
    """v2.4: /check/<id>/export.xlsx silinmis yoxlama ucun
    TypeError -> 500 verirdi."""
    app, client, path = make_client()
    try:
        assert client.get("/check/99999").status_code == 404
        assert client.get("/check/99999/export.xlsx").status_code == 404
        assert client.post("/check/99999/autosearch").status_code == 404
        assert client.post("/check/99999/delete").status_code == 404
    finally:
        os.unlink(path)


def test_missing_offer_delete_returns_404_not_500():
    """v2.4: olmayan teklif silinende row None -> TypeError -> 500."""
    app, client, path = make_client()
    try:
        assert client.post("/offer/99999/delete").status_code == 404
        assert client.post("/offer/99999/exclude").status_code == 404
    finally:
        os.unlink(path)


def test_settings_rejects_bad_number_without_crashing():
    """v2.4: float('abc') -> ValueError -> 500."""
    app, client, path = make_client()
    try:
        r = client.post("/settings", data={"margin_pct": "abc"}, follow_redirects=True)
        assert r.status_code == 200
        assert "rəqəm olmalıdır".encode("utf-8") in r.data
        r = client.post("/settings", data={"margin_pct": "25"}, follow_redirects=True)
        assert r.status_code == 200
    finally:
        os.unlink(path)


def test_upload_path_traversal_blocked():
    app, client, path = make_client()
    try:
        assert client.get("/uploads/../app.py").status_code == 404
        assert client.get("/uploads/..%2Fapp.py").status_code == 404
    finally:
        os.unlink(path)


def test_check_lifecycle_and_api():
    """Yoxlama yarat -> teklif elave et -> sec -> link bloku al."""
    app, client, path = make_client()
    try:
        from arma.db import connect
        from arma.services import create_check
        con = connect(path)
        cid = create_check(con, "R.209", 68.0, 1.20, ptype="sendvic cihazi")
        con.execute(
            "INSERT INTO offers(check_id,name,seller,price,old_price,url,"
            "code_match,margin,is_match) VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, "Vafli cihazi RAF R.209", None, 83.99, 108.0,
             "https://birmarket.az/product/989764", 2, 23.5, 1))
        con.commit()
        oid = con.execute("SELECT id FROM offers WHERE check_id=?",
                          (cid,)).fetchone()["id"]
        con.close()

        assert client.get("/check/%d" % cid).status_code == 200
        assert client.get("/check/%d/export.xlsx" % cid).status_code == 200

        j = client.get("/api/check/%d/links" % cid).get_json()
        assert j["ok"] and len(j["links"]) == 1
        assert j["text"] == "https://birmarket.az/product/989764\n68 ₼"

        # secimi legv et -> link siyahidan cixir
        assert client.post("/api/offer/%d/pick" % oid, json={"v": 0}).get_json()["ok"]
        assert client.get("/api/check/%d/links" % cid).get_json()["links"] == []

        # yeniden sec
        client.post("/api/offer/%d/pick" % oid, json={"v": 1})
        assert len(client.get("/api/check/%d/links" % cid).get_json()["links"]) == 1

        b = client.post("/api/links/bundle", json={"ids": [cid]}).get_json()
        assert b["ok"] and b["links"] == 1 and b["codes"] == 1

        assert client.get("/api/stats").get_json()["ok"]
        assert client.get("/api/price-history/%d" % oid).get_json()["ok"]
        assert client.post("/api/offer/99999/pick", json={"v": 1}).status_code == 404
    finally:
        os.unlink(path)


def test_add_offer_rejects_bad_price():
    app, client, path = make_client()
    try:
        from arma.db import connect
        from arma.services import create_check
        con = connect(path)
        cid = create_check(con, "R.209", 68.0, 1.20)
        con.close()
        r = client.post("/check/%d/offer" % cid,
                        data={"name": "test", "price": "abc"}, follow_redirects=True)
        assert r.status_code == 200
        assert "düzgün qiymət".encode("utf-8") in r.data
    finally:
        os.unlink(path)


def test_links_page_picks_deterministic_row():
    """v2.4: 'GROUP BY o.url' istifade edirdi ve SQLite qrupdan TESADUFI
    setri gotururdu - cedvelde gorunen setir ile X duymesinin sildiyi setir
    ferqli ola bilirdi."""
    app, client, path = make_client()
    try:
        from arma.db import connect
        from arma.services import create_check
        from arma.views import links_data
        con = connect(path)
        cid = create_check(con, "R.209", 68.0, 1.20)
        url = "https://birmarket.az/product/989764"
        for margin, price in [(10.0, 74.8), (23.5, 83.99), (5.0, 71.4)]:
            con.execute(
                "INSERT INTO offers(check_id,name,seller,price,old_price,url,"
                "code_match,margin,is_match) VALUES (?,?,?,?,?,?,?,?,?)",
                (cid, "Vafli RAF R.209", None, price, None, url, 2, margin, 1))
        con.commit()
        groups, total = links_data(con, [cid], "")
        con.close()
        assert total == 1                       # eyni url bir defe
        assert groups[0]["offers"][0]["margin"] == 23.5   # en yuksek marja
    finally:
        os.unlink(path)


def test_static_assets_exist():
    """v2.4: base.html static/logo.svg-e istinad edirdi, amma o fayl YOX idi -
    her sehifede sinmis sekil ve 404 favicon."""
    for rel in ["static/img/logo.svg", "static/img/logo-mark.svg",
                "static/img/favicon.svg", "static/css/tokens.css",
                "static/css/components.css", "static/js/arma.js"]:
        p = os.path.join(BASE, rel)
        assert os.path.exists(p), "fayl yoxdur: " + rel
        assert os.path.getsize(p) > 0, "fayl bosdur: " + rel


def test_every_static_file_referenced_by_base_exists():
    """Sablonun istediyi her static fayl diskde olmalidir."""
    import re
    html = open(os.path.join(BASE, "templates", "base.html"), encoding="utf-8").read()
    names = re.findall(r"filename='([^']+)'", html)
    assert names, "base.html-de static istinad tapilmadi"
    for name in names:
        p = os.path.join(BASE, "static", name)
        assert os.path.exists(p), "base.html istinad edir, fayl yoxdur: " + name


def test_all_templates_compile():
    app, client, path = make_client()
    try:
        import glob
        for f in glob.glob(os.path.join(BASE, "templates", "*.html")):
            app.jinja_env.get_template(os.path.basename(f))
    finally:
        os.unlink(path)


# ------------------------------------------------------------------ giris
# v4.1: sistem `arma.biraddim.com` uzerinde acik internete cixdi. Bu testler
# girisin ACIQ INTERNETDE bagli, LOKALDA acik qaldigini qoruyur.

# Tetbiqin oxudugu BUTUN ARMA_* deyisenleri burada olmalidir. Biri unudulsa
# bir testin qoydugu deyer novbetiye sizir: ARMA_PUBLISH ilk yazilanda mehz
# bu olmusdu ve "server yazma terefini baglayir" testi yalanci PASS verirdi.
AUTH_ENV = ("ARMA_ADMIN_PASSWORD", "ARMA_ADMIN_PASSWORD_HASH",
            "ARMA_ADMIN_USER", "ARMA_REQUIRE_AUTH", "ARMA_TRUST_PROXY",
            "ARMA_PUBLISH")

TEST_PASSWORD = "test-parol-123"          # sintetik, real parol deyil


def make_auth_client(**env):
    """Giris qurulmus tetbiq. Muhit deyisenleri yalniz create_app anina
    qoyulur ve derhal berpa olunur - testlerin sirasi neticeni deyismesin."""
    from arma import auth
    old = {k: os.environ.get(k) for k in AUTH_ENV}
    try:
        for k in AUTH_ENV:
            os.environ.pop(k, None)
        os.environ.update(env)
        auth._ATTEMPTS.clear()            # sayğac testler arasinda dasinmasin
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        app = create_app(base_dir=BASE, db_path=path)
        app.config["TESTING"] = True
        return app, app.test_client(), path
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_local_run_has_no_login():
    """Parol qurulmayibsa lokal davranis deyismir: giris ekrani cixmir."""
    app, client, path = make_client()
    try:
        assert app.config["AUTH_ENABLED"] is False
        assert client.get("/").status_code == 200
        assert client.get("/giris").status_code == 302     # panele qaytarir
    finally:
        os.unlink(path)


def test_password_closes_every_page():
    """Parol qurulubsa BUTUN sehifeler baglidir - xususile /settings,
    cunki orada Claude API acari saxlanir."""
    app, client, path = make_auth_client(ARMA_ADMIN_PASSWORD=TEST_PASSWORD)
    try:
        for url in ["/", "/check/new", "/batch", "/whatsapp", "/links", "/settings"]:
            r = client.get(url)
            assert r.status_code == 302, (url, r.status_code)
            assert "/giris" in r.headers["Location"], url
        assert client.get("/giris").status_code == 200
    finally:
        os.unlink(path)


def test_api_returns_401_json_not_login_html():
    """`fetch` ile geden sorguya HTML giris sehifesi qaytarmaq brauzerde
    anlasilmaz JSON parse xetasi verir - 401 JSON daha durustdur."""
    app, client, path = make_auth_client(ARMA_ADMIN_PASSWORD=TEST_PASSWORD)
    try:
        r = client.get("/api/stats")
        assert r.status_code == 401
        assert r.get_json()["ok"] is False
    finally:
        os.unlink(path)


def test_login_then_logout():
    app, client, path = make_auth_client(ARMA_ADMIN_PASSWORD=TEST_PASSWORD)
    try:
        r = client.post("/giris", data={"user": "Admin", "password": TEST_PASSWORD})
        assert r.status_code == 302
        assert client.get("/").status_code == 200
        assert client.post("/cixis").status_code == 302
        assert client.get("/").status_code == 302          # cixisdan sonra bagli
    finally:
        os.unlink(path)


def test_login_rejects_wrong_password():
    app, client, path = make_auth_client(ARMA_ADMIN_PASSWORD=TEST_PASSWORD)
    try:
        assert client.post("/giris",
                           data={"user": "Admin", "password": "yanlis"}).status_code == 401
        assert client.get("/").status_code == 302
    finally:
        os.unlink(path)


def test_login_locks_after_repeated_failures():
    """Sistem acik internetdedir: kobud guc cehdi bloklanmalidir. Blok
    qalxana qeder DOGRU parol da islememelidir."""
    app, client, path = make_auth_client(ARMA_ADMIN_PASSWORD=TEST_PASSWORD)
    try:
        for _ in range(6):
            client.post("/giris", data={"user": "Admin", "password": "yanlis"})
        r = client.post("/giris", data={"user": "Admin", "password": TEST_PASSWORD})
        assert r.status_code == 401
        assert client.get("/").status_code == 302
    finally:
        from arma import auth
        auth._ATTEMPTS.clear()
        os.unlink(path)


def test_login_next_cannot_send_user_offsite():
    """`?next=https://kenar...` ile girisden sonra kenar sayta atmaq olmaz."""
    app, client, path = make_auth_client(ARMA_ADMIN_PASSWORD=TEST_PASSWORD)
    try:
        for bad in ("https://kenar.example/oyun", "//kenar.example/oyun"):
            c = app.test_client()          # her defe teze sessiya
            r = c.post("/giris?next=" + bad,
                       data={"user": "Admin", "password": TEST_PASSWORD})
            assert r.status_code == 302
            assert "kenar.example" not in r.headers.get("Location", ""), bad
    finally:
        os.unlink(path)


def test_custom_admin_user_name():
    app, client, path = make_auth_client(ARMA_ADMIN_PASSWORD=TEST_PASSWORD,
                                         ARMA_ADMIN_USER="Sahib")
    try:
        assert client.post("/giris",
                           data={"user": "Admin", "password": TEST_PASSWORD}).status_code == 401
        assert client.post("/giris",
                           data={"user": "Sahib", "password": TEST_PASSWORD}).status_code == 302
    finally:
        from arma import auth
        auth._ATTEMPTS.clear()
        os.unlink(path)


def test_require_auth_without_password_closes_the_app():
    """Yanlis qurulmus server ACIQ qalmir, BAGLI qalir: ARMA_REQUIRE_AUTH=1
    verilib amma parol yoxdursa tetbiq hec ne servis etmir."""
    app, client, path = make_auth_client(ARMA_REQUIRE_AUTH="1")
    try:
        assert client.get("/").status_code == 503
        assert client.get("/giris").status_code == 503
        assert client.get("/api/stats").status_code == 503
        # Yalniz saglamliq ucu aciq qalir ki, dagitim skripti sebebi gorsun
        assert client.get("/saglamliq").status_code == 200
    finally:
        os.unlink(path)


def test_publish_is_open_locally():
    """Lokalda yazma terefi acig qalir - Chrome ve insan burada."""
    app, client, path = make_client()
    try:
        assert app.config["PUBLISH_ENABLED"] is True
        assert client.get("/publish").status_code == 200
    finally:
        os.unlink(path)


def test_publish_is_closed_on_the_server():
    """ARMA_REQUIRE_AUTH=1 (server) yazma terefini defolt baglayir.

    Sebeb: executor.py `headless=False` ile Chrome acir ve `wait_for_login`
    ile insan girisi gozleyir - bassiz VPS-de onsuz da islemir. Ustelik
    `/api/publish/live` real magazaya GERI ALINMAYAN yazi edir; tek parolun
    arxasinda acik internetde durmamalidir."""
    app, client, path = make_auth_client(ARMA_ADMIN_PASSWORD=TEST_PASSWORD,
                                         ARMA_REQUIRE_AUTH="1")
    try:
        assert app.config["PUBLISH_ENABLED"] is False, "PUBLISH_ENABLED"
        c = app.test_client()
        r = c.post("/giris", data={"user": "Admin", "password": TEST_PASSWORD})
        assert r.status_code == 302, "giris: %s" % r.status_code
        # Giris etmis olsa da yazma terefi yoxdur
        for url in ["/publish", "/api/publish/state"]:
            assert c.get(url).status_code == 404, url
        assert c.post("/api/publish/live", json={}).status_code == 404, "live"
        # Oxuma terefi ise normal isleyir
        assert c.get("/").status_code == 200, "/ -> %s" % c.get("/").status_code
        assert c.get("/links").status_code == 200, "/links"
    finally:
        from arma import auth
        auth._ATTEMPTS.clear()
        os.unlink(path)


def test_publish_can_be_opened_on_server_deliberately():
    """ARMA_PUBLISH=1 elle verilse server de yazma terefini acir."""
    app, client, path = make_auth_client(ARMA_ADMIN_PASSWORD=TEST_PASSWORD,
                                         ARMA_REQUIRE_AUTH="1",
                                         ARMA_PUBLISH="1")
    try:
        assert app.config["PUBLISH_ENABLED"] is True
        c = app.test_client()
        c.post("/giris", data={"user": "Admin", "password": TEST_PASSWORD})
        assert c.get("/publish").status_code == 200
    finally:
        from arma import auth
        auth._ATTEMPTS.clear()
        os.unlink(path)


# ------------------------------------------------------------------ main
if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in fns:
        try:
            fn()
            passed += 1
            print("  PASS  " + name)
        except Exception as e:
            failed += 1
            print("  FAIL  %s -> %s: %s" % (name, type(e).__name__, e))
    print("\n  %d passed, %d failed (%d total)" % (passed, failed, len(fns)))
    sys.exit(1 if failed else 0)
