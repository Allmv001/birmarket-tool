#!/usr/bin/env bash
# ARMA serverde ilk qurulum. SERVERDE isledilir, bir defe:
#     ssh root@2.24.77.33
#     bash /opt/arma/deploy/kur.sh
#
# Ondan evvel kod serverde olmalidir - yayimla.sh onu ozu gonderir.
set -euo pipefail

APP=/opt/arma
say() { printf '\n== %s\n' "$1"; }

[ -d "$APP" ] || { echo "yoxdur: $APP - once yayimla.sh isledin"; exit 1; }

say "python muhiti"
command -v python3 >/dev/null || { apt-get update -qq && apt-get install -y -qq python3; }

# python3 olsa da `venv` modulu ayrica paketdir. Ubuntu 24.04-de python3 var
# idi, python3-venv yox idi ve `python3 -m venv` "ensurepip is not available"
# deyib yarimciq qovluq qoyub cixdi (28.08.2026). Modulu yoxlayiriq, komandani
# yox.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo "   python3-venv qurulur"
  apt-get update -qq && apt-get install -y -qq python3-venv
fi

# Yarimciq qalmis muhit: qovluq var, amma pip yoxdur. Sade "[ -d ]" yoxlamasi
# bunu isleyen muhit sanar ve novbeti addim anlasilmaz sekilde cokerdi.
if [ -d "$APP/.venv" ] && [ ! -x "$APP/.venv/bin/pip" ]; then
  echo "   yarimciq .venv tapildi, yenidən qurulur"
  rm -rf "$APP/.venv"
fi
[ -d "$APP/.venv" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --upgrade pip --quiet
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt" --quiet
"$APP/.venv/bin/pip" install -r "$APP/requirements-server.txt" --quiet

say "data qovlugu"
mkdir -p "$APP/data/uploads"
chmod 700 "$APP/data"          # baza, yuklenen sekiller, gizli acar

say "systemd"
cp "$APP/deploy/arma.service" /etc/systemd/system/arma.service
systemctl daemon-reload
systemctl enable arma >/dev/null

say "parol"
if [ -f /etc/systemd/system/arma.service.d/10-parol.conf ]; then
  echo "   parol drop-in artiq var, toxunulmadi"
else
  mkdir -p /etc/systemd/system/arma.service.d
  # ARMA_PW_HASH evvelceden verilibse sorusmuruq (avtomatlasdirilmis
  # qurulum). Bu deyisen PAROLU yox, HASH-i dasiyir - parolun ozu
  # buraya, unit fayla ve ya loga hec vaxt gelmir.
  if [ -n "${ARMA_PW_HASH:-}" ]; then
    HASH="$ARMA_PW_HASH"
    echo "   hazir hash istifade olundu"
  else
  echo "   indi parol hash-i yaradilir (parol ekranda gorunmeyecek)"
  HASH=$("$APP/.venv/bin/python" - <<'PY'
import getpass, sys
from werkzeug.security import generate_password_hash
p1 = getpass.getpass("   Parol: ")
p2 = getpass.getpass("   Tekrar: ")
if not p1 or p1 != p2:
    sys.stderr.write("   parollar uygun gelmedi\n"); sys.exit(1)
print(generate_password_hash(p1))
PY
)
  fi
  printf '[Service]\nEnvironment="ARMA_ADMIN_PASSWORD_HASH=%s"\n' "$HASH" \
    > /etc/systemd/system/arma.service.d/10-parol.conf
  chmod 600 /etc/systemd/system/arma.service.d/10-parol.conf
  systemctl daemon-reload
  echo "   parol yazildi (yalniz root oxuya bilir)"
fi

say "servis"
systemctl restart arma
sleep 2
systemctl is-active --quiet arma || { journalctl -u arma -n 30 --no-pager; exit 1; }

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:4300/saglamliq || echo 000)
[ "$code" = "200" ] || { echo "saglamliq yoxlamasi ugursuz: $code"; journalctl -u arma -n 30 --no-pager; exit 1; }

cat <<'SON'

  ARMA isleyir: 127.0.0.1:4300

  Qalan iki addim ELLE edilir:
    1. DNS:  arma.biraddim.com  ->  A  ->  2.24.77.33
    2. DNS yayilandan sonra deploy/Caddyfile.arma icindeki bloku
       /etc/caddy/Caddyfile faylinin SONUNA elave edin (uzerine YAZMAYIN),
       sonra:  caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy

SON
