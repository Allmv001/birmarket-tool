#!/usr/bin/env bash
# ARMA -> arma.biraddim.com yayimlama. OZ MASININIZDAN isledilir:
#     bash deploy/yayimla.sh
#
# Ne edir: testleri isledir -> kodu paketleyir -> serverde ehtiyat nusxe
# goturur -> acir -> asililiqlari qurur -> servisi yeniden baslayir ->
# saglamliq yoxlayir. Yoxlama tutmasa OZU kohne versiyaya qaytarir.
set -euo pipefail

HOST=${ARMA_HOST_SSH:-root@2.24.77.33}
KEY=${ARMA_SSH_KEY:-$HOME/.ssh/biraddim_deploy}
APP=/opt/arma
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TAG=$(date +%Y%m%d-%H%M%S)

say() { printf '\n== %s\n' "$1"; }
sshx() { ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "$HOST" "$@"; }

# ------------------------------------------------------------ 1. testler
say "testler"
if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then
  PY="$ROOT/.venv/Scripts/python.exe"                 # Windows
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"                         # Linux/mac
else
  PY=python3
fi
# 28.08.2026: EVVEL burada yalniz `tests/test_arma.py` islenirdi - 37 test,
# hamisi oxuma terefi. Bu oturumda yazilan 100 test (qiymet motoru, pul
# parseri, CSRF, yayin defteri, API qeydi maskalanmasi) pytest teleb edir ve
# QAPIDAN KECMIRDI: qiymet motorunu sindiran deyisiklik "testler kecdi"
# deyib yayimlana bilerdi. Indi butun paket islenir.
if "$PY" -m pytest --version >/dev/null 2>&1; then
  "$PY" -m pytest "$ROOT/tests" -q || {
    echo "testler kecmedi - yayimlanmadi"; exit 1; }
else
  echo "   XEBERDARLIQ: pytest yoxdur - yalniz test_arma.py islenir (natamam)"
  "$PY" "$ROOT/tests/test_arma.py" >/dev/null || {
    echo "testler kecmedi - yayimlanmadi"; exit 1; }
fi
echo "   kecdi"

# ------------------------------------------------------------ 2. paket
# data/ PAKETE GIRMIR. Serverdeki baza, yuklenen sekiller ve gizli acar
# oradadir; paketde olsa her yayimlamada canli melumatin uzerine yazardiq.
# .venv de girmir: serverde Linux ucun ayrica qurulur.
say "paket"
TARBALL=$(mktemp -t arma-XXXX.tar.gz)
tar -czf "$TARBALL" -C "$ROOT" \
  --exclude='data' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='*.pyc' \
  app.py requirements.txt requirements-server.txt README.md \
  arma templates static deploy
echo "   $(du -h "$TARBALL" | cut -f1)"

# ------------------------------------------------------------ 3. gonder
say "gonder"
scp -i "$KEY" -q "$TARBALL" "$HOST:/tmp/arma-$TAG.tar.gz"
rm -f "$TARBALL"

# ------------------------------------------------------------ 4. ac
# Ehtiyat nusxe: yalniz kod qovluqlari, data/ deyil (o yerinde qalir).
say "serverde ac"
sshx bash -s <<REMOTE
set -euo pipefail
APP=$APP
TAG=$TAG
mkdir -p "\$APP"
if [ -d "\$APP/arma" ]; then
  mkdir -p "\$APP/.geri/\$TAG"
  for d in arma templates static deploy app.py requirements.txt requirements-server.txt; do
    [ -e "\$APP/\$d" ] && cp -a "\$APP/\$d" "\$APP/.geri/\$TAG/" || true
  done
fi
tar -xzf "/tmp/arma-\$TAG.tar.gz" -C "\$APP"
rm -f "/tmp/arma-\$TAG.tar.gz"
if [ -d "\$APP/.venv" ]; then
  "\$APP/.venv/bin/pip" install -q -r "\$APP/requirements.txt" -r "\$APP/requirements-server.txt"
fi
chmod +x "\$APP/deploy/"*.sh
# Windows-dan gelen fayllarda setir sonu CRLF ola biler (Python faylı
# yazanda cevirir). Linux-da bash bunu "set: pipefail: invalid option"
# kimi gorur ve qurulum anlasilmaz sekilde cokur - 28.08.2026-da oldu.
# Burada bir defe temizlenir, menbede ne olmasindan asili olmayaraq.
sed -i "s/$//" "\$APP/deploy/"*.sh "\$APP/deploy/arma.service" 2>/dev/null || true
# .geri altinda yalniz son 5 versiya qalsin
ls -1dt "\$APP/.geri/"*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf
REMOTE

# ------------------------------------------------------------ 5. yeniden basla
# Ilk yayimlamada servis hele qurulmayib - bu xeta deyil, novbeti addimdir.
if ! sshx "systemctl list-unit-files arma.service --no-legend | grep -q arma"; then
  printf '\n  Kod serverdedir, amma servis hele qurulmayib.\n'
  printf '  Novbeti addim (serverde, bir defe):\n'
  printf '    ssh -i %s %s\n' "$KEY" "$HOST"
  printf '    bash %s/deploy/kur.sh\n\n' "$APP"
  exit 0
fi

say "yeniden basla"
sshx "systemctl restart arma && sleep 2"

# ------------------------------------------------------------ 6. saglamliq
say "saglamliq"
code=$(sshx "curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:4300/saglamliq" || echo 000)
if [ "$code" != "200" ]; then
  echo "   UGURSUZ ($code) - kohne versiyaya qaytarilir"
  sshx bash -s <<REMOTE
set -euo pipefail
APP=$APP
TAG=$TAG
if [ -d "\$APP/.geri/\$TAG" ]; then
  cp -a "\$APP/.geri/\$TAG/." "\$APP/"
  systemctl restart arma
fi
journalctl -u arma -n 40 --no-pager
REMOTE
  exit 1
fi
echo "   200 OK"

# Giris sehifesi acilirmi (tetbiq REQUIRE_AUTH altinda 503 vermirmi)
code=$(sshx "curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:4300/giris" || echo 000)
if [ "$code" = "503" ]; then
  echo "   XEBERDARLIQ: tetbiq baglidir - parol drop-in-i yoxdur."
  echo "   Serverde: bash $APP/deploy/kur.sh"
  exit 1
fi
[ "$code" = "200" ] || { echo "   giris sehifesi acilmadi: $code"; exit 1; }

printf '\n  Yayimlandi.  https://arma.biraddim.com\n\n'
