# Yayın axını (v4.1)

ARMA-nın **yazma tərəfi**. Əvvəl bu iş ayrıca `birmarket-tool/` qovluğunda idi;
28.08.2026-da buraya köçürüldü, çünki ARMA onsuz da oxuma tərəfini (rəqib
axtarışı, WhatsApp parseri, marja hesabı) saxlayır və iki ayrı Flask tətbiqi
eyni problemləri iki dəfə həll edirdi.

## Nə üçündür

WhatsApp-dan gələn «link + maya» siyahısını götürür, rəqib qiymətlərini çəkir,
qaydaya görə qiymət hesablayır, kabinetə məhsul açır və botda alt/üst limitləri
qoyur.

Köhnə alətdən əsas fərq: **üç addım, tək düymə deyil.**

```
1. Planla      mətn → katalog → qərar → dəftər        (heç nə yazılmır)
2. Quru koşu   «nə yazılacaq» hesabatı                (brauzer də açılmır)
3. Canlı yazı  yalnız quru koşudan keçənlər, təsdiqlə (geri alınmır)
```

Bu bölgü qəsdəndir. Autopricer layihəsinin öz qaydası ilə eynidir:
«Canlı fiyatlandırma geri alınamaz. Varsayılan dry-run; bir mağaza canlıya
alınmadan önce açık insan onayı gerekir.»

## İstifadə

1. Sol menyuda **🚀 Yayın**.
2. Linkləri yapışdır. «🔗 Linklər» səhifəsinin WhatsApp çıxışı birbaşa işləyir —
   format eynidir, əl ilə çevirmək lazım deyil.
3. **Planla** → dəftər dolur. Keçilən sətirlər səbəbi ilə görünür.
4. Lazım olsa endirimli qiyməti cədvəldə dəyiş — köhnə, bot alt və bot üst
   **özü yenidən hesablanır**.
5. **🧪 Quru koşu** → nə yazılacağını göstərir, problemli sətirləri bloklayır.
6. **🚀 Canlı yazı** → «CANLI» sözünü yazmaq tələb olunur. Brauzer açılır;
   ilk dəfə kabinetə və bota özün daxil olursan, sessiya
   `data/chrome-profile/` içində qalır.

Uğursuzlar `failed` vəziyyətində qalır; **↻ Uğursuzları növbəyə qoy** onları
geri qaytarır (3 cəhddən sonra `needs_review` olur və əl ilə baxılmalıdır).

## Dəftər (`publications` cədvəli)

Hər məhsul üçün bir sətir. `UNIQUE(product_id, store)` — eyni siyahını iki dəfə
planlasan da ikinci sətir yaranmır.

| Vəziyyət | Mənası |
|---|---|
| `planned` | qərar verilib, hələ heç nə yazılmayıb |
| `dry_run` | quru koşudan keçib, canlıya hazırdır |
| `live` | pazaryerinə yazılıb |
| `failed` | cəhd olundu, alınmadı (təkrar cəhd mümkündür) |
| `skipped` | qayda ilə keçilir (öz mağaza / aşağı marja / status) |
| `needs_review` | insan baxmalıdır (artıq var, qiymət dəyişib, limit məntiqsiz) |

Dəftər dörd işi görür: **idempotentlik** (iki dəfə açılmır), **təkrar cəhd**
(uğursuz növbədə qalır), **denetim izi** (hansı qiymət nə vaxt yazıldı) və
**«artıq var» halının düzgün emalı**.

## Qiymət qaydaları (`arma/pricing.py`)

```
endirimli = ən ucuz rəqib − 0.01      (rəqib yoxdursa: ROUNDUP(maya×1.70) − 0.01)
köhnə     = ROUNDUP(endirimli×2) + ~5 ₼ (500+ olanda +2; ...0 ilə bitməsin),
            qəpik = mayanın tam hissəsinin mod 100-ü
bot alt   = maya × 1.25
bot üst   = endirimli + 20

keç:  öz mağazan satıcılar arasındadırsa
      və ya (ən ucuz − maya) < 4 ₼
      və ya katalog statusu «active» deyil
```

Düsturlar dəyişməyib. `tests/test_publish_system.py::test_golden_values_unchanged`
üç qızıl dəyəri kilidləyir — kimsə hesabı dəyişsə test qırılır.

## 28.08.2026 auditində bağlanan açıqlar

Hər birinin adına uyğun testi var (`tests/test_publish_system.py`).

| Kod | Nə idi | İndi |
|---|---|---|
| K1 | «1.234,56 manat» → maya 1.234 → məhsul **2.99 ₼**-ə elan olunurdu | düzgün oxunur; «1.234» kimi birmənalı olmayan sətir **rədd edilir**, təxmin edilmir |
| K2 | status `active` deyilsə rəqiblər «yoxdur» sayılırdı → rəqibin **iki qatı** qiymət, səbəb sütununda «Satıcı yoxdur» yazırdı | ayrıca qərar: `SKIP` + `STATUS_NOT_ACTIVE` |
| K3 | kənar saytdan `text/plain` POST canlı koşu başlada bilirdi (**HTTP 200 alınmışdı**) | Origin/Referer yoxlaması → **403**; `force=True` heç yerdə yoxdur |
| K4 | tək düymə birbaşa canlıya yazırdı | quru koşu məcburi + «CANLI» təsdiqi (**428**) |
| Y1 | bot limitləri mövqeyə görə yazılır və **eyni mövqe fərziyyəsi ilə** yoxlanırdı → tərs yazılma «✅» görünürdü | etiketə görə tapılır; yazmadan əvvəl və oxuduqdan sonra `alt < üst` yoxlanılır |
| Y2 | «məhsul artıq var» **uğur** kimi loglanırdı, qiymət yanlış qalırdı | `needs_review` |
| Y3 | əl ilə endirimli dəyişəndə köhnə köhnə dəyərdə qalırdı (205.60 ↔ 165.60) | törəmə sahələr yenidən hesablanır |
| Y4 | sessiya yoxdursa `/update` və `/run` **HTTP 500** verirdi | 404 / 400 |
| O3 | analizlə yazı arasında rəqib qiyməti dəyişə bilərdi | yazmadan dərhal əvvəl yenidən yoxlama |
| O4 | katalogda sürət limiti qoruyucusu yox idi | circuit breaker (4 uğursuzluq → 90 san fasilə) |
| O5 | təkrar cəhd yox idi | `attempts` + `retry_failed()` + 3 cəhd həddi |
| O7 | 12 yerdə sabit `sleep` (~15-20 san/məhsul ölü vaxt) | şərt gözləməsi (`waitFor`) |
| O8 | UI seçiciləri kodun içinə səpələnmişdi | hamısı `executor.UI` lüğətində + partiyadan əvvəl `preflight()` |

Auditdən sonra testlərin özü bir səhv də tapdı: mənfi işarə parserdə düşürdü,
yəni birmarket səhifəsindən kopyalanan endirim sətri («-22 %») linkdən sonra
gəlsə maya 22 kimi oxunurdu. İndi rədd edilir.

## 28.08.2026 ÜRETİM RAPORU ilə bağlananlar

Aracın köhnə nüsxəsini işlədən başqa bir oturum üretim davranışını qeydə aldı.
Raporun ən dəyərli hissəsi kod deyil, **real arıza məlumatı** idi.

| Nə oldu (üretim) | Burada nə var |
|---|---|
| 246 məhsulun HAMISINDA `TypeError: ... (reading 'click')`. Səbəb: `find(...)` nəticəsi yoxlanmadan `.click()`. Köhnə `automation.py`-də üç belə yer: `cr.click()`, `pen().click()`, `.find(...).click()` | Bütün klik nöqtələri null-yoxlamalıdır. İki test bunu kilidləyir: biri çağırış nəticəsinə birbaşa klikləməyi qadağan edir, digəri hər klikləNƏN dəyişənin yoxlandığını təsdiqləyir |
| **Xəta yutulurdu**, döngü davam edirdi, iş «bitdi» görünürdü — halbuki heç nə yazılmamışdı | Koşunun sonunda HƏMİŞƏ `XÜLASƏ` sətri yazılır və uğursuzlar **adı ilə** sadalanır (`❌ Alınmayanlar: 111, 222`) |
| 246-lıq iş kəsildi, harada qaldığı **proqramdan öyrənilə bilmədi**; istifadəçi ~173 rəqəmini əl ilə təxmin etdi, işi baştan başlatdı və ilk ~173 məhsul **İKİNCİ DƏFƏ** işləndi | Hər məhsulun vəziyyəti yazıldığı anda SQLite-a commit olunur. Yenidən koşuda yalnız `dry_run` sətirlər götürülür. `pending()` «harada qaldım?» sualının cavabını verir və səhifədə banner kimi görünür |
| Tətbiq HTTP 500 verib dayandı, geriyə baxmaq üçün **heç bir iz yox idi** (log faylı yoxdu) | `data/arma.log` — 5 fayl × 1 MB, dövri |
| İncelenen 173 sətirin **13-ü (%7.5) rəqibsiz** idi; qiymət tamamilə `maya × markup`-dan gəlirdi və səhv görünmürdü | Rəqibsiz sətirlər quru koşuda **AÇIQ təsdiq** istəyir. Təsdiqlənməsə `needs_review` olur. Səbəb: müqayisə ediləcək rəqib yoxdursa, səhv katsayını heç nə tutmur |

Rapor həm də **öz səhvimi** üzə çıxardı: səkkiz yayın ayarının (ən əsası
`bot_url`) UI qarşılığı yox idi, ona görə canlı yazı HƏMİŞƏ «Bot ünvanı təyin
olunmayıb» qaytarırdı — yəni **əlçatmaz idi**. İndi Ayarlar səhifəsində
«Yayın qaydaları» kartı var. Testi yazarkən ikinci bir səhv də çıxdı: blok
`margin_pct` yoxlamasının önünə keçirildikdən sonra `commit()`-siz qalmışdı,
yəni səhv marja yazanda ayarlar səssizcə itirdi.

**Raporun bir iddiası isə səhv idi.** Orada `/excel`, `/update`, `/stop`
«yeni uç nokta» kimi göstərilmişdi; əslində sizin nüsxədə də vardı. Rapor
qara-qutu müşahidəsinə əsaslanırdı (kaynak kod tapılmamışdı), ona görə
strukturla bağlı hissələri təxminidir. Ölçülmüş arıza məlumatı isə etibarlıdır.

## Bilərəkdən BAĞLANMAYAN iki şey

**O9 — köhnə qiymətin qəpik hanəsi mayanı sızdırır.** `qəpik = maya mod 100`
olduğu üçün qaydanı bilən rəqib maya haqqında məlumat çıxara bilər. Düstur
sahibin qərarıdır, dəyişdirilmədi — yalnız `pricing.kohne_qiymet()` içində
qeyd olundu.

**`data/chrome-profile/` hələ də OneDrive içindədir.** `.gitignore` onu depoya
girməkdən saxlayır, amma **OneDrive buluta sinxronlaşdırmağa davam edir** və
orada canlı pazaryeri sessiya kukiləri var. Həqiqi həll: profili OneDrive-dan
kənara çıxarmaq (məs. `C:\ARMA\chrome-profile`) və yolu ayarlardan vermək.
Bu, qovluq strukturuna toxunduğu üçün sahibin qərarını gözləyir.

## Hələ edilməyən: DOM → API keçidi

Sistem hazırda kabineti və botu **brauzerlə sürür**. İkisi də web tətbiqidir,
yəni arxalarında XHR uçları var. Bir dəfə network qeydi alıb o uçları birbaşa
çağırmaq ən yüksək gətirili addımdır:

| | İndi (DOM) | Sonra (API) |
|---|---|---|
| Məhsul başına | ~20 san | <1 san |
| Paralellik | 1 | 5-10 |
| UI dəyişəndə | dayanır | təsirlənmir |

Ondan sonra Playwright yalnız **sessiya yeniləmə** üçün qalır və zamanlanmış
(headless) koşu real olur.

## Testlər

```
python -m pytest tests/ -q          # 117 test
```

Brauzer lazım deyil: `publish.execute()` yalnız `runner.publish_one(row)`
metodunu tanıyır, testdə `FakeRunner` ilə əvəz olunur.

**Diqqət:** yazma axını canlı saytda hələ sınanmayıb. Bütün qorumalar və
qərar məntiqi test altındadır, amma kabinet/bot DOM-u ilə real qarşılaşma
ilk canlı koşuda olacaq — ona görə birinci dəfə **2-3 məhsulla** başla.
