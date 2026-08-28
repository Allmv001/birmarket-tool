# -*- coding: utf-8 -*-
"""API qeydi — FAZ 1: heç nə yazılmır, yalnız oxunur.

    python capture_api.py 2579394
    python capture_api.py 2579394 https://bbu-umiko-bot.onrender.com

Nə edir:
  1. Mövcud `data/chrome-profile/` ilə brauzeri açır (giriş qalıbsa soruşmur)
  2. Kabinetdə verilən kodu AXTARIR (yalnız axtarış — «Seçmək» basılmır)
  3. Botda həmin kodun siyahı səhifəsini açır (yalnız siyahı — modal açılmır)
  4. Bu müddətdə gedən BÜTÜN XHR/fetch sorğularını qeyd edir

Nə ETMİR:
  * kabinetə məhsul əlavə etmir
  * botda limit dəyişmir
  * yazıya səbəb olan heç bir düyməyə basmır
  * sirləri (çerez, token) qeydə yazmır — yalnız «var/yox» bilgisi

FAZ 2 (ayrıca, sonra): gerçəkdən bir məhsul əlavə edərkən qeyd almaq. O,
geri alınmayan yazı olduğu üçün yalnız onsuz da əlavə edəcəyin məhsulla
edilməlidir.
"""
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

from arma.apicapture import Recorder                       # noqa: E402
from arma.executor import Runner                           # noqa: E402

OUT_DIR = BASE / "data" / "api-capture"
PROFILE = BASE / "data" / "chrome-profile"


def main(code, bot_url=""):
    print()
    print("  API qeydi — FAZ 1 (heç nə yazılmır)")
    print(f"  Nümunə kod: {code}")
    print(f"  Çıxış: {OUT_DIR}")
    print()

    rec = Recorder()
    with Runner(bot_url, PROFILE, log=lambda m: print("  " + m)) as runner:
        rec.attach(runner.ctx)

        rec.mark("sessiya yoxlaması")
        ok, msg = runner.session_ready()
        if not ok:
            print(f"  ⏳ {msg}")
            print("  Açılan brauzerdə kabinetə (və varsa bota) daxil ol.")
            ok, msg = runner.wait_for_login(timeout=600)
            if not ok:
                print(f"  ❌ {msg}")
                return 1

        # --- kabinet: yalnız axtarış -------------------------------------
        rec.mark("kabinet: kod axtarışı")
        print("  🔎 Kabinetdə axtarılır...")
        try:
            found, why = runner.preflight(code)
            print(f"  {'✅' if found else '⚠️'} {why}")
        except Exception as e:                              # noqa: BLE001
            print(f"  ⚠️ axtarış alınmadı: {e}")

        # --- bot: yalnız siyahı ------------------------------------------
        if bot_url:
            rec.mark("bot: məhsul siyahısı")
            print("  🔎 Botda siyahı açılır...")
            url = (f"{runner.bot_url}/dashboard/products"
                   f"?current=1&pageSize=10&search={code}&statusFilter=all")
            try:
                runner.bot.goto(url, wait_until="networkidle", timeout=45000)
                print("  ✅ bot siyahısı yükləndi")
            except Exception as e:                          # noqa: BLE001
                print(f"  ⚠️ bot siyahısı açılmadı: {e}")
        else:
            print("  ℹ️ Bot ünvanı verilmədi — bot tərəfi qeyd olunmadı.")

        rec.mark("son")

    js, md = rec.save(OUT_DIR)
    reqs = [e for e in rec.entries if e.get("kind") == "request"]
    print()
    print(f"  📄 {len(reqs)} XHR/fetch sorğusu qeyd olundu")
    print(f"     {md}")
    print(f"     {js}")
    print()
    if not reqs:
        print("  ⚠️ Heç nə tutulmadı. Səhifə tam server-render ola bilər —")
        print("     bu halda API keçidi mümkün olmaya bilər, DOM qalır.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        print("İstifadə: python capture_api.py <məhsul_kodu> [bot_url]")
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))
