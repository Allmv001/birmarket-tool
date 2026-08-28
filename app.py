# -*- coding: utf-8 -*-
"""
ARMA — Birmarket Marja Sistemi v4
İşə salmaq:  python app.py   ->  http://localhost:5000

Tətbiqin özü `arma/` paketindədir; bu fayl yalnız başlatma nöqtəsidir.
WSGI altında işlətmək üçün:  gunicorn "arma:create_app()"
"""
import os
import webbrowser

from arma import __version__, create_app

app = create_app(base_dir=os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    port = int(os.environ.get("ARMA_PORT", 5000))
    url = "http://localhost:%d" % port
    print()
    print("  ARMA Marja Sistemi v%s  ->  %s" % (__version__, url))
    print("  Dayandirmaq: Ctrl+C")
    print()
    # Brauzeri yalnız reloader-in əsas prosesində aç (debug rejimində iki dəfə açılmasın)
    if not os.environ.get("WERKZEUG_RUN_MAIN"):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    # threaded=True: uzun axtarış gedərkən digər səhifələr açıq qalır
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
