# birmarket-tool

**birmarket.az** üçün marja yoxlama və yayın sistemi. Lokal işləyir, məlumat bu
kompüterdən çıxmır.

İki tərəfi var:

| Tərəf | Nə edir | Səhifə |
|---|---|---|
| **Oxuma** (marja) | WhatsApp elanlarından kod + maya götürür, birmarket-də həmin kodu tapır, marjanı hesablayır | `/`, `/batch`, `/whatsapp`, `/links` |
| **Yazma** (yayın) | Qiyməti qaydaya görə hesablayır, kabinetə məhsul açır, botda alt/üst limit qoyur | `/publish` — detal: [YAYIN.md](YAYIN.md) |

Yazma tərəfi **üç addımlıdır** (planla → quru koşu → canlı yazı) və canlı yazı
üçün açıq təsdiq istəyir: pazaryerinə gedən qiymət geri alınmır.

## Quraşdırma

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\playwright install chrome
```

Python 3.10+ lazımdır («Add to PATH» işarələnmiş olsun).

**Yoxla:** `.venv\Scripts\python.exe -m pytest tests -q` → 139 test keçməlidir.

**İşə sal:** `.venv\Scripts\python.exe app.py` → http://localhost:5000
(Windows-da **run.bat**-a iki dəfə klikləmək bəsdir.)

`data/` qovluğu ilk açılışda ÖZÜ yaranır — boş baza, yeni gizli açar. Bu qovluq
depoya girmir və iki maşın arasında köçürülməməlidir: içində maya qiymətləri və
girişdən sonra canlı pazaryeri sessiyası olur.

## API qeydi (DOM → HTTP keçidi üçün)

```bat
.venv\Scripts\python.exe capture_api.py <məhsul_kodu> <bot_url>
```

Heç nə yazmır — yalnız kabinetdə axtarır, botda siyahını açır və gedən XHR
sorğularını qeyd edir. Sirlər (çerez, token) qeydə yazılmır. Çıxış:
`data/api-capture/*.md`.

---

```
python app.py     ->  http://localhost:5000
```
Windows-da: **run.bat** faylına iki dəfə klikləyin.

---

## v4-də nə dəyişdi

### Düzəldilən çökmələr (hamısı v2.4-də REAL çökürdü)

| Hal | v2.4 davranışı | v4 |
|---|---|---|
| Silinmiş yoxlamanın Excel exportu | `TypeError` → **500 səhifəsi** | 404, anlaşılan mesaj |
| Ayarlarda hədd sahəsinə hərf yazmaq | `ValueError` → **500 səhifəsi** | xəbərdarlıq, dəyər dəyişmir |
| Olmayan təklifi silmək | `TypeError` → **500 səhifəsi** | 404 |
| `logo.svg` faylı | **ümumiyyətlə yox idi** — hər səhifədə sınmış şəkil + 404 favicon | loqo var, testlə qorunur |

### Düzəldilən məntiq xətaları

- **Uydurma "kod"lar.** `Toster 1600 vatt 600 Vt 40 cm 35 manat` sətrindən v2.4
  **`VATT-600`** kodunu çıxarıb birmarket-də axtarırdı. Ölçü qoruyucusu yalnız
  brendli koda (RAF 2603) tətbiq olunurdu, hərf+rəqəm koduna yox. README bunun
  işlədiyini yazırdı — işləmirdi.
- **Qiymət kodu bloklayırdı.** `manat` / `azn` / `₼` ölçü vahidləri siyahısında idi,
  ona görə hər sətirdəki qiymət kod tapılmasını əngəlləyirdi. Nəticədə
  **SF / LORD / SONIFER brendləri praktikada heç vaxt tanınmırdı**
  (`Fen SF-401 45 manat` → kod tapılmırdı).
- **Maya 0 = hər şey uyğun.** Hədd `0 × 1.20 = 0` olurdu və istənilən qiymət həddi
  keçirdi. Toplu rejimdə qiyməti səhv oxunmuş bir sətir bütün nəticəni zibilləyirdi.
  İndi maya ≤ 0 heç vaxt "uyğun" vermir.
- **Linklər səhifəsində yanlış sətir.** `GROUP BY o.url` işlədilirdi və SQLite
  qrupdan **təsadüfi** sətri götürürdü — cədvəldə görünən elan ilə ✕ düyməsinin
  sildiyi elan fərqli ola bilirdi. İndi hər link üçün ən yüksək marjalı sətir
  müəyyən seçilir.

### Sistem sabitliyi

- **WAL rejimi + `busy_timeout`.** Arxa fon axtarışı gedərkən səhifə açmaq artıq
  "database is locked" vermir.
- **Toplu iş vəziyyəti bazadadır.** v2.4-də yaddaşdakı `JOBS = {}` lüğəti idi:
  server yenidən başlayanda bütün proqres itir və "tapşırıq tapılmadı" çıxırdı.
  İndi `jobs` cədvəlindədir, tarixçə qalır, iş **dayandırıla bilir**.
- **Sürət limiti sayğacı thread-safe oldu** (v2.4-də kilidsiz `global` idi;
  toplu yoxlama ilə əl ilə axtarış eyni anda işləyəndə sayğac pozulurdu).
- **Baza tətbiq açılışında qurulur** — `flask run` və WSGI altında da işləyir
  (v2.4-də yalnız `python app.py` yolunda).
- **Gizli açar** koda yazılmır, ilk açılışda yaradılıb `data/.secret_key`-ə yazılır.

### Yeni UI sistemi

- **Dizayn token qatı** (`static/css/tokens.css`) — rəng, ölçü, künc, kölgə,
  tipografiya bir yerdə; komponentlər (`components.css`) yalnız token işlədir.
- **İşıqlı / qaranlıq / sistem** rejimi **əl ilə seçilir** (sol aşağı ☀ ☾ ◐),
  seçim brauzerdə yadda qalır. v2.4 yalnız sistem parametrini izləyirdi.
- **Səhifə donmur.** Axtarış və seçim `fetch` ilə gedir; toplu proqres
  `/api/job/<id>`-dən oxunur. v2.4 hər 4 saniyədən bir `location.reload()`
  çağırıb bütün səhifəni yenidən çəkirdi.
- **`alert()` yoxdur** — toast bildirişləri iş axınını kəsmir.
- Panel **axtarış + səhifələmə**, hər siyahıda **boş vəziyyət** mətni,
  mobil menyu, klaviatura fokusu, çap üçün ayrıca stil.

### Loqo

v2.4 `static/logo.svg` faylına istinad edirdi, **amma o fayl mövcud deyildi** —
hər səhifədə sınmış şəkil və 404 favicon. v4-də:

| Fayl | Harada |
|---|---|
| `static/img/logo-mark.svg` | sol paneldəki nişan (teal kafel, ağ "A", mis tir) |
| `static/img/favicon.svg` | brauzer nişanı |
| `static/img/logo.svg` | tam lokot (nişan + söz nişanı) — sənəd və çap üçün |

Söz nişanı sol paneldə **HTML mətnidir**, SVG deyil: `<img>` içindəki
`currentColor` səhifənin rəngini miras almır, ona görə qaranlıq rejimdə
görünməzdi. `test_every_static_file_referenced_by_base_exists` testi bu sinif
xətanın (şablon fayla istinad edir, fayl yoxdur) təkrarlanmasını bloklayır.

---

## Quruluş

```
arma-sistem-v4/
├─ app.py                 # başlatma nöqtəsi (create_app + run)
├─ run.bat / test.bat     # Windows: işə sal / testləri işlət
├─ arma/                  # tətbiq paketi
│   ├─ __init__.py        #   tətbiq fabriki, xəta səhifələri
│   ├─ db.py              #   sxem, miqrasiya, WAL, ayarlar
│   ├─ codes.py           #   kod uyğunluğu, marja hesabı, axtarış variantları
│   ├─ parsing.py         #   kopyala-yapışdır + "KOD MAYA" parseri
│   ├─ wa_parser.py       #   WhatsApp elan mətni parseri
│   ├─ fetcher.py         #   birmarket.az axtarışı + sürət limiti qoruyucusu
│   ├─ vision.py          #   şəkildən kod/qiymət (Claude API)
│   ├─ services.py        #   axtarış axını, link toplusu
│   ├─ jobs.py            #   arxa fon işləri (bazada saxlanır)
│   ├─ exports.py         #   Excel hesabatları
│   ├─ auth.py            #   giriş (lokalda yoxdur, serverdə məcburi)
│   ├─ views.py           #   HTML route-ları
│   └─ api.py             #   JSON API (UI bunun üzərində işləyir)
├─ templates/             # Jinja şablonları
├─ static/css|js|img/     # UI sistemi + loqo
├─ deploy/                # arma.biraddim.com: systemd, Caddy, yayımlama
├─ tests/test_arma.py     # reqressiya testləri
└─ data/                  # SQLite bazası + yüklənən şəkillər (avtomatik yaranır)
```

## Testlər

```
.venv\Scripts\python.exe tests\test_arma.py      (və ya: test.bat)
```

31 test. Böyük hissəsi v2.4-də real çökən halları qoruyur — hər biri
`v2.4: ...` şərhi ilə işarələnib ki, düzəliş təsadüfən geri alınmasın.
Testlər istehsal bazasına toxunmur: hər biri müvəqqəti boş SQLite yaradır.

## v2.4 məlumatını gətirmək

v4 boş baza ilə başlayır. Köhnə yoxlamalarınızı gətirmək üçün:

```
copy ..\arma-sistem-v2.4\arma-sistem-v2.4\data\birmarket.db  data\birmarket.db
```

Baza açılışda avtomatik miqrasiya olunur (yeni `jobs` cədvəli, indekslər, WAL).
**v2.4 qovluğu olduğu kimi qalır** — geri qayıtmaq lazım olsa işlək vəziyyətdədir.

---

## İş axını

1. **💬 WhatsApp** — qrup mesajlarını olduğu kimi yapışdırın. Sistem hər sətirdən
   kodu və maya qiymətini çıxarır, birmarket-də axtarır.
   *Qrup adı yazsanız mətn arxivə düşür; növbəti dəfə yalnız qrup adı + "son N
   məhsul" yazmaq kifayətdir.*
2. **🔗 Linklər** — həddi keçən bütün elanlar bir səhifədə. 📋 kopyala / ➤ göndər /
   ⬇ Excel. ✕ ilə istəmədiyiniz linki çıxarın — seçim yadda qalır.
3. **➤ WhatsApp-a göndər** — mesaj formatı: link → Enter → maya qiyməti, bloklar
   arasında boş sətir.

> ℹ️ WhatsApp təhlükəsizlik səbəbindən mesajı **özü göndərmir** — düymə WhatsApp-ı
> mesaj hazır yazılmış halda açır, siz yalnız **Göndər**-ə basırsınız. Bu, WhatsApp-ın
> öz məhdudiyyətidir.

**"Tapılan 0" = boş niş**: kod birmarket-də ümumiyyətlə yoxdursa, o məhsulu ilk siz
qoya bilərsiniz.

## Ayarlar

Marja həddi · WhatsApp nömrəsi · Claude API açarı (yalnız şəkildən oxuma üçün;
mətn rejimi pulsuzdur və daha dəqiqdir) · axtarış davranışı (variant sayı, səhifə
sayı, sorğular arası fasilə, paneldə səhifə ölçüsü).

Sürət limitinə tez-tez düşürsünüzsə **fasiləni artırın**. Real dərs (20.08.2026):
~100 ardıcıl sorğudan sonra birmarket cavab verməyi dayandırır; sistem 4 ardıcıl
uğursuzluqdan sonra 90 saniyə gözləyib özü davam edir.

## Məlumat və ehtiyat nüsxə

Hər şey `data/birmarket.db` (SQLite) faylındadır, şəkillər `data/uploads/`
altındadır. Ehtiyat nüsxə üçün **`data/` qovluğunu kopyalayın**.
API açarı yalnız bu kompüterdəki lokal bazada saxlanır və yalnız şəkil oxunarkən
Anthropic API-yə göndərilir.

---

## Serverdə: arma.biraddim.com

ARMA lokal alət olaraq qalır. Əlavə olaraq BirAddim-in VPS-ində ayrıca xidmət
kimi qaldırıla bilir (`127.0.0.1:4300`, Caddy arxasında, öz alt domenində).
Lokal işləmə heç nə itirmir və **parol soruşmur** — giriş yalnız serverdə açılır.

| Vəziyyət | Davranış |
|---|---|
| Parol qurulmayıb (lokal, `run.bat`) | Giriş yoxdur, dəyişən heç nə |
| Parol qurulub | Bütün səhifələr bağlı, `/giris` açılır |
| `ARMA_REQUIRE_AUTH=1`, parol YOXDUR | Tətbiq heç nə servis etmir (503) |

Üçüncü sətir qəsdəndir: **yanlış qurulmuş server açıq qalmır, bağlı qalır.**
Parol koda da, systemd unit faylına da yazılmır — yalnız root oxuya bilən
drop-in içərisində hash kimi durur.

Qurulum, DNS və Caddy addımları: **`deploy/QURULUM.md`**.
Yayımlamaq: `bash deploy/yayimla.sh` (testlər keçməsə yayımlamır, sağlamlıq
yoxlaması tutmasa özü geri qaytarır).
