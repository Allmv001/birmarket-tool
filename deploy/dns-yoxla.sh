#!/usr/bin/env bash
# DNS qeydinin Caddy ucun hazir olub-olmadigini yoxlayir.
#
#   bash deploy/dns-yoxla.sh                      # arma.biraddim.com
#   bash deploy/dns-yoxla.sh www.biraddim.com     # basqa ad
#   bash deploy/dns-yoxla.sh --gozle              # hazir olana qeder gozle
#
# NIYE LAZIMDIR: Caddy blokunu DNS hazir olmadan elave etseniz, Caddy
# sertifikat almaga calisir, Let's Encrypt "bu ad hell olunmur" deyir ve
# ugursuz cehdler limite sayilir. Limite dusen domen saatlarla kilidlenir.
#
# NIYE YETKILI SERVERLERE SORUR: Let's Encrypt genel hell edicilere
# (8.8.8.8 ve s.) yox, domenin YETKILI ad serverlerine sorusur. Genel hell
# ediciler kohne "bele ad yoxdur" cavabini bir muddet kesde saxlayir
# (negativ kes) - qeyd artiq hazir olsa da onlar "yoxdur" deyir. Ona gore
# qerar yetkili serverlerin cavabina gore verilir; genel hell ediciler
# yalniz melumat ucun gosterilir.
set -uo pipefail

AD=arma.biraddim.com
GOZLE=0
for a in "$@"; do
  case "$a" in
    --gozle) GOZLE=1 ;;
    -*)      ;;
    *)       AD="$a" ;;
  esac
done

IP=${ARMA_VPS_IP:-2.24.77.33}
DOMEN=${AD#*.}                       # arma.biraddim.com -> biraddim.com
[ "$DOMEN" = "$AD" ] && DOMEN="$AD"

hell() {   # hell <ad> <server> -> IP ve ya bos
  if command -v dig >/dev/null 2>&1; then
    dig +short +time=4 +tries=2 "@$2" A "$1" 2>/dev/null \
      | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -1
  else
    nslookup -type=A "$1" "$2" 2>/dev/null \
      | awk '/^Name:/{n=1} n&&/^Address/{print $NF; exit}'
  fi
}

# Domenin oz ad serverleri (Hostinger-de dns-parking.com, amma sabit yazmiriq)
yetkili_serverler() {
  local out
  if command -v dig >/dev/null 2>&1; then
    out=$(dig +short NS "$DOMEN" 2>/dev/null | sed 's/\.$//')
  else
    out=$(nslookup -type=NS "$DOMEN" 8.8.8.8 2>/dev/null \
          | awk '/nameserver =/{print $NF}' | sed 's/\.$//')
  fi
  [ -n "$out" ] && echo "$out"
}

printf '\n  %s  ->  %s ?\n' "$AD" "$IP"

NS_LIST=$(yetkili_serverler)
if [ -z "$NS_LIST" ]; then
  printf '\n  %s ucun ad serveri tapilmadi - domen konfiqurasiyasini yoxlayin.\n\n' "$DOMEN"
  exit 1
fi

yoxla() {
  local ok=0 hamisi=0
  printf '\n  Yetkili ad serverleri (qerar bunlara gore verilir):\n'
  for ns in $NS_LIST; do
    hamisi=$((hamisi+1))
    local got; got=$(hell "$AD" "$ns")
    if [ "$got" = "$IP" ]; then
      printf '    %-30s %s  ✓\n' "$ns" "$got"; ok=$((ok+1))
    elif [ -n "$got" ]; then
      printf '    %-30s %s  ✗ (gozlenilen %s)\n' "$ns" "$got" "$IP"
    else
      printf '    %-30s -            cavab yoxdur\n' "$ns"
    fi
  done

  printf '\n  Genel hell ediciler (yalniz melumat - kohne cavabi kesde saxlaya bilerler):\n'
  for r in 8.8.8.8 1.1.1.1 9.9.9.9; do
    local got; got=$(hell "$AD" "$r")
    printf '    %-30s %s\n' "$r" "${got:-hele yayilmayib}"
  done

  [ "$ok" -gt 0 ] && [ "$ok" -eq "$hamisi" ]
}

if [ "$GOZLE" = "1" ]; then
  for i in $(seq 1 60); do
    if yoxla; then
      printf '\n  HAZIR. Caddy bloku elave edile biler:\n'
      printf '    deploy/Caddyfile.arma  ->  /etc/caddy/Caddyfile SONUNA\n\n'
      exit 0
    fi
    printf '\n  ... %d/60, 30 saniye sonra yeniden\n' "$i"
    sleep 30
  done
  printf '\n  30 deqiqedir hazir olmadi. hPanel-de qeydi yoxlayin.\n\n'
  exit 1
fi

if yoxla; then
  printf '\n  HAZIR. Caddy bloku elave edile biler.\n\n'
  exit 0
fi
printf '\n  Hele hazir deyil. Hostinger hPanel -> Domains -> %s -> DNS:\n' "$DOMEN"
printf '    Tip: A   Ad: %s   Deyer: %s   TTL: 3600\n' "${AD%.$DOMEN}" "$IP"
printf '  Gozlemek ucun:  bash deploy/dns-yoxla.sh %s --gozle\n\n' "$AD"
exit 1
