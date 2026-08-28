# ARMA serverde: arma.biraddim.com

ARMA lokal alet olaraq qalir. Bu sened onu ELAVE OLARAQ BirAddim-in VPS-inde
ayrica xidmet kimi qaldirmagi anladir. Lokal isleme (`run.bat`) hec ne itirmir
ve parol sorusmur - giris yalniz serverde acilir.

## Niye ayri xidmet, panelin icinde deyil

BirAddim Node + TypeScript-dir, ARMA Python + Flask. Python xidmeti Node
panelinin icine sehife kimi girmir. Ona gore ARMA oz prosesinde qalir
(`127.0.0.1:4300`), eyni Caddy onu ayri alt domende servis edir. Ayni sxem
BirAddim-in oz uc xidmeti ucun de isleyir: :4000 backend, :4100 platform,
:4200 AI. ARMA dorduncudur: :4300.

## Giris nece isleyir

| Vəziyyət | Davranış |
|---|---|
| Parol qurulmayib (lokal) | Giris yoxdur, v4-deki kimi |
| Parol qurulub | Butun sehifeler bagli, `/giris` acilir |
| `ARMA_REQUIRE_AUTH=1`, parol YOXDUR | Tetbiq hec ne servis etmir (503) |

Ucuncu setir vacibdir: **yanlis qurulmus server aciq qalmir, bagli qalir.**
systemd unit-i `ARMA_REQUIRE_AUTH=1` verir, ona gore parol drop-in-i olmadan
ARMA-nin canliya sizmasi mumkun deyil.

Parol koda yazilmir, unit fayilinda da durmur. Yalniz root oxuya bilen
`/etc/systemd/system/arma.service.d/10-parol.conf` iceresinde HASH kimi durur.

Elave qorumalar: sehv parolun 6 cehdinden sonra hemin IP 5 deqiqe bloklanir,
`/api/*` sorgulari giris sehifesi yerine 401 JSON alir, `?next=` ile kenar
sayta yonlendirmek olmur, sessiya 12 saatliq.

## Serverde NE VAR, NE YOXDUR

| Teref | Serverde | Lokalda |
|--------|----------|---------|
| Oxuma: marja yoxlamasi, WhatsApp parseri, linkler, Excel | var | var |
| Yazma: `/publish`, kabinete mehsul acmaq, bot limiti | **YOX (404)** | var |

Yazma terefi serverde qesden baglidir (`ARMA_PUBLISH=0`). Iki sebeb:

1. **Texniki.** `executor.py` Chrome-u `headless=False` ile acir ve
   `wait_for_login()` ile 300 saniye insan girisi gozleyir. Bassiz VPS-de
   ekran, Chrome ve insan yoxdur - axin orada onsuz da isleye bilmez.
2. **Tehlukesizlik.** `launch_persistent_context` birmarket Business
   kabinetinin GIRIS ETMIS sessiyasini diskde saxlayir, `/api/publish/live`
   ise real magazaya geri alinmayan yazi edir. Autopricer-in oz qaydasi:
   «Canli fiyatlandirma geri alinamaz.» Bele bir dugme tek parolun arxasinda
   acig internetde durmamalidir.

Menyudakı «Yayin» linki serverde gorunmur, route-lar 404 verir. Yazma isi
lokal komputerde gorulur - Chrome da, sahib de oradadir.

Fikrinizi deyisseniz: unit faylinda `ARMA_PUBLISH=1`. Amma evvel Chrome
qurmaq (`playwright install chrome`) ve kabinete girisin bassiz serverde
nece isleyecəyini hell etmek lazimdir.

## Qurulum (bir defe)

Ardicilliq vacibdir.

**1. Kodu gonder** (oz masinizdan):

```
bash deploy/yayimla.sh
```

Ilk defe servis hele yoxdur, ona gore "yeniden basla" addimi xeta verecek -
normaldir, novbeti addim onu qurur.

**2. Serverde qur:**

```
ssh -i ~/.ssh/biraddim_deploy root@2.24.77.33
bash /opt/arma/deploy/kur.sh
```

Betik python muhitini qurur, `data/` qovlugunu 700 icaze ile acir, systemd
unit-ini yazir ve **parolu gizli sorusur** (ekranda gorunmur, hash-lenib
drop-in-e yazilir). Sonunda `127.0.0.1:4300/saglamliq` yoxlayir.

**3. DNS** - Hostinger hPanel.

`biraddim.com` Hostinger-in ad serverlerindedir
(`hermes.dns-parking.com`, `artemis.dns-parking.com`), ona gore qeyd hPanel-de
elave olunur:

> hPanel -> Domains -> biraddim.com -> DNS / Nameservers -> Manage DNS records

| Tip | Ad (Name) | Deyer (Points to) | TTL |
|-----|-----------|-------------------|-----|
| A   | `arma`    | `2.24.77.33`      | 3600 |

Ad xanasina yalniz **`arma`** yazilir, tam `arma.biraddim.com` yox - Hostinger
domeni ozu elave edir. Yazsaniz `arma.biraddim.com.biraddim.com` alinir.

Bu, hazirda duran qeydlerin yaninda dorduncu olur:

| Ad | Deyer | Vəziyyət |
|----|-------|----------|
| `@` (biraddim.com) | 2.24.77.33 | var |
| `app` | 2.24.77.33 | var |
| `admin` | - | **qesden yoxdur** (operator paneli ictimai deyil) |
| `arma` | 2.24.77.33 | **elave edilecek** |

Yayilmasini yoxlamaq (uc mustəqil hell ediciden sorusur):

```
bash deploy/dns-yoxla.sh            # bir defe
bash deploy/dns-yoxla.sh --gozle    # hazir olana qeder gozle
```

Betik "HAZIR" deyene qeder **4-cu addima kecmeyin**.

**4. Caddy** - DNS yayilandan SONRA:

```
nano /etc/caddy/Caddyfile        # deploy/Caddyfile.arma icindeki bloku SONA elave et
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

> `deploy/Caddyfile.arma` faylini `/etc/caddy/Caddyfile` uzerine **kopyalamayin**.
> Eyni Caddy `biraddim.com`, `app.biraddim.com`, `arvhome.ca` ve
> `licencebase.com` saytlarina xidmet edir; uzerine yazmaq hamisini salir.

> DNS gostermeden Caddy bloku elave etmeyin - Caddy sertifikat almaga calisir
> ve Let's Encrypt limitine dusur.

## Sonraki yayimlamalar

```
bash deploy/yayimla.sh
```

Testler kecmese yayimlanmir. Serverde kohne versiya `.geri/<tarix>/` altinda
saxlanir (son 5-i), saglamliq yoxlamasi tutmasa betik ozu geri qaytarir.

## data/ hec vaxt gonderilmir

`yayimla.sh` paketde `data/` qovlugunu **istisna edir**. Serverdeki baza,
yuklenen sekiller ve gizli acar oradadir; paketde olsa her yayimlama canli
melumatin uzerine yazardi. Bu istisna setirini silmeyin.

Ehtiyat nusxe: `scp -r root@2.24.77.33:/opt/arma/data ./ehtiyat-<tarix>`

## Yoxlama

```
systemctl status arma
journalctl -u arma -f
curl -s localhost:4300/saglamliq
```

## Iki qurulusun ferqi

Lokal `data/birmarket.db` ile serverdeki **ayri bazalardir**, ozleri
sinxronlasmir. Lokal isinizi servere gecirmek isteyirsinizse fayli elle
kopyalayin (servisi dayandirib, sonra baslatmaqla).
