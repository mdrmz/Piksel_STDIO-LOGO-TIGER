# Logo Tiger Analiz

[![Son sürüm](https://img.shields.io/github/v/release/mdrmz/Piksel_STDIO-LOGO-TIGER?label=s%C3%BCr%C3%BCm)](../../releases/latest)
[![Windows entegrasyon testi](https://github.com/mdrmz/Piksel_STDIO-LOGO-TIGER/actions/workflows/entegrasyon.yml/badge.svg)](../../actions/workflows/entegrasyon.yml)
[![Lisans: MIT](https://img.shields.io/badge/lisans-MIT-blue)](LICENSE)

**Logo Tiger yedeğinden (`.bak`) dakikalar içinde satış, stok, müşteri ve tedarikçi analizi.**
Firmaya gidin, programı açın, yedeği seçin, Excel raporunu ya da Power BI paketini alın.

Elle hiçbir şey kurmanız gerekmez: yedeği açmak için gereken SQL Server bilgisayarda yoksa program onu kendisi kurar.
Her şey kendi bilgisayarınızda çalışır; hiçbir veri internete gönderilmez.

![Analiz paneli](docs/panel.png)

---

## İçindekiler
- [Hızlı başlangıç (fabrikada kullanım)](#hızlı-başlangıç-fabrikada-kullanım)
- [SQL Server: otomatik kurulum](#sql-server-otomatik-kurulum)
- [Analizler](#analizler)
- [Çıktılar](#çıktılar)
- [Logo verisi nasıl yorumlanıyor](#logo-verisi-nasıl-yorumlanıyor)
- [Güvenlik ve KVKK](#güvenlik-ve-kvkk)
- [Sorun giderme](#sorun-giderme)
- [Kaynaktan çalıştırma ve geliştirme](#kaynaktan-çalıştırma-ve-geliştirme)
- [Lisans](#lisans)

---

## Hızlı başlangıç (fabrikada kullanım)

1. **İndirin.** [Releases](../../releases/latest) sayfasından sisteminize uygun dosyayı indirin:

   | Sistem | Dosya |
   |---|---|
   | Windows 10/11 (64 bit) | `LogoTigerAnaliz-windows-x64.zip` |
   | macOS (M1/M2/M3/M4) | `LogoTigerAnaliz-macos-apple-silicon.zip` |
   | Linux (64 bit) | `LogoTigerAnaliz-linux-x64.tar.gz` |

2. **Açın.** Arşivi çıkarın ve `LogoTigerAnaliz` klasöründeki programı çalıştırın
   (Windows'ta `LogoTigerAnaliz.exe`). Tarayıcıda **http://127.0.0.1:5055** kendiliğinden açılır.
   Siyah pencere programın kendisidir; iş bitene kadar kapatmayın.
   - Windows "Windows kişisel bilgisayarınızı korudu" derse: **Ek bilgi → Yine de çalıştır**.
   - macOS "açılamıyor" derse, Terminal'de bir kez: `xattr -dr com.apple.quarantine ~/Downloads/LogoTigerAnaliz`
3. **Yedeği seçin ve Başlat'a basın.** İlk seferde program kendi SQL Server'ını kurar
   (internet gerekir, ~5 dakika; Windows "yönetici izni" sorduğunda **Evet** deyin). Sonraki açılışlarda bu adım atlanır.
4. **Firma ve dönem seçin.** Program yedekteki tüm Logo firmalarını ve dönemlerini kendisi bulur.
5. **Raporu alın.** Panelde özet görünür; **Excel raporu** ya da **Power BI paketi** düğmesiyle indirin.

> Program yedeği geçici bir veritabanına (`LOGOANALIZ_XXXXXXXX`) geri yükler, verileri okur ve bu geçici veritabanını
> **siler**. Firmanın kendi Logo veritabanına dokunulmaz, yazılmaz.

> İnternet olmayan bir yere gidecekseniz programı önceden kendi laptopunuzda bir kez çalıştırıp SQL Server'ı kurdurun;
> sahada internet gerekmez.

Önce denemek isterseniz **Demo veri** sekmesi, Logo tablo yapısında üretilmiş örnek bir firmayla çalışır.

![Ana ekran](docs/ana.png)

## SQL Server: otomatik kurulum

`.bak` dosyası bir SQL Server yedeğidir ve yalnızca SQL Server tarafından açılabilir. Bunu program halleder:

| Sistem | Program ne yapar |
|---|---|
| **Windows** | Microsoft'un ücretsiz **SQL Server 2022 Express** sürümünü indirir ve `LOGOANALIZ` adıyla sessizce kurar. Sizden sadece Windows'un yönetici izni penceresine "Evet" demeniz istenir. Rastgele üretilen `sa` şifresi Windows DPAPI ile şifrelenip `LogoAnaliz\ayarlar.json`'da saklanır. Port `14330`, yedek klasörü `C:\ProgramData\LogoAnaliz\yedek`. Firmanın kendi Logo SQL Server'ına dokunulmaz. |
| **macOS / Linux** | [Docker Desktop](https://www.docker.com/products/docker-desktop/) kuruluysa SQL Server konteynerini (`logo-analiz-mssql`) kendisi oluşturur ve başlatır. Docker yoksa indirme bağlantısını gösterir. Apple Silicon Mac'lerde Docker ayarlarında *Use Rosetta for x86_64/amd64 emulation* açık olmalı. |

İsterseniz *Bu bilgisayardaki başka bir SQL Server* seçeneğiyle mevcut bir sunucuyu (ör. Logo'nun kendi örneği, kullanıcı `sa`)
da kullanabilirsiniz; program bilgisayardaki örnekleri listeler.

Bu akış her kod değişikliğinde GitHub Actions'ta **gerçek bir Windows makinesinde** baştan sona test edilir:
SQL Server Express programın kendi koduyla kurulur, Logo yapısında bir veritabanından `.bak` alınır, uygulama bu yedeği
geri yükleyip analiz eder ve sonuçlar kuruşu kuruşuna karşılaştırılır ([tests/entegrasyon_windows.py](tests/entegrasyon_windows.py)).

> Yedek, kurulu SQL Server'dan **daha yeni** bir sürümle alınmışsa geri yüklenemez. Program SQL Server 2022 kurar;
> Logo'nun kullandığı 2012–2022 sürümlerinin yedeklerini açar.

## Analizler

| Analiz | İçerik |
|---|---|
| **Özet** | Net satış, iade tutarı ve oranı, fatura sayısı, ortalama fatura, müşteri/ürün sayısı, net alış |
| **Ürün ABC** | Net satış tutarına göre A/B/C sınıfları, Pareto grafiği. Sınırlar ayarlanabilir (varsayılan %80 / %95) |
| **Müşteri ABC** | Net satış, fatura sayısı, son alım tarihi |
| **Tedarikçi ABC** | Net alış tutarına göre |
| **Aylık trend** | Aylık satış/alış ve geçen yılın aynı ayına göre değişim |
| **Grup satış** | Malzeme grubu (`STGRPCODE`) kırılımı |
| **Ölü stok** | Elde olup N gündür satılmayan malzemeler ve ortalama alış maliyetiyle tahmini değeri |
| **Stok devir** | Devir hızı, stok yeterlilik süresi (gün), negatif stok uyarısı |

## Çıktılar

- **Panel**: tarayıcıda özet, grafikler, ilk 15 ürün/müşteri, ölü stok.
- **Excel raporu** (`.xlsx`): Özet, Ürün/Müşteri/Tedarikçi ABC (Pareto grafiğiyle), Aylık Trend, Grup Satış,
  Ölü Stok, Stok Devir sayfaları. Filtreli, dondurulmuş başlıklı, A4 yatay yazdırmaya hazır.
- **Power BI paketi** (`.zip`): yıldız şema CSV'leri (`fact_satis`, `fact_alis`, `dim_cari`, `dim_malzeme`, `dim_tarih`,
  `stok_bakiye`, ABC tabloları) ve ilişkileri/DAX ölçülerini anlatan `OKUBENI_PowerBI.txt`.
  Power BI Desktop yalnızca Windows'ta çalışır.

Her analiz `~/LogoAnaliz/` (Windows: `C:\Users\<kullanıcı>\LogoAnaliz\`) altında saklanır;
firmadan ayrıldıktan sonra programı açıp **Önceki analizler** listesinden tekrar açabilirsiniz.

## Logo verisi nasıl yorumlanıyor

Ham Logo tabloları doğrudan toplandığında ciro yanlış çıkar. Program şu kuralları uygular:

| Konu | Kural |
|---|---|
| Tablolar | `LG_{firma}_CLCARD` (cari), `LG_{firma}_ITEMS` (malzeme), `LG_{firma}_{dönem}_INVOICE`, `LG_{firma}_{dönem}_STLINE` |
| Satış | `TRCODE` 7 (perakende) ve 8 (toptan); iadeler 2 ve 3 **eksi** olarak düşülür |
| Alış | `TRCODE` 1; alış iadesi 6 eksi |
| İptal | `STLINE.CANCELLED=1` veya bağlı faturası iptal olan satırlar dışlanır |
| Satır tipi | Sadece malzeme satırları (`LINETYPE=0`); indirim/masraf satırları dışlanır (hizmetler isteğe bağlı) |
| Tutar | **`VATMATRAH`**: KDV hariç net, dip indirimler satırlara dağıtılmış hali |
| İrsaliye | Faturalanmamış irsaliyeler varsayılan olarak satışa sayılmaz (ayarlanabilir) |
| Birim | Koli/paket gibi alt birimler ana birime çevrilir: `AMOUNT × UINFO2 / UINFO1` |
| Stok | En son seçilen dönemden hesaplanır (dönem başı devir fişi önceki bakiyeyi taşır) |

Panelin altındaki **Veri notları** kaç satırın neden dışlandığını gösterir.

## Güvenlik ve KVKK

- Program sadece `127.0.0.1` üzerinden çalışır; ağdaki başka bilgisayarlar erişemez. Veri internete gönderilmez.
- Programın kurduğu SQL Server sadece bu bilgisayardan erişilir; `sa` şifresi rastgele üretilir ve şifreli saklanır.
- Firmanın Logo veritabanında yalnızca `SELECT` çalıştırılır. Yedekten açılan geçici veritabanı iş bitince silinir;
  tarayıcıdan yüklenen `.bak` kopyası da silinir.
- Analiz klasörü (`~/LogoAnaliz/`) cari adları ve satış tutarları içerir. İş bittiğinde **Önceki analizler → Sil**
  ile kaldırabilir, ya da yalnızca rapor dosyalarını firmaya teslim edebilirsiniz.
- Repo `.gitignore`'u `.bak`, `.mdf`, `.xlsx` gibi dosyaların yanlışlıkla GitHub'a gönderilmesini engeller.

## Sorun giderme

| Mesaj | Çözüm |
|---|---|
| *SQL Server Express indirilemedi* | İnternet bağlantısını kontrol edin. Kurumsal ağlarda Microsoft indirmeleri engelli olabilir; programı önce başka bir ağda bir kez çalıştırıp kurulumu tamamlayın. |
| *Yönetici izni verilmedi* | Kurulum için Windows'un izin penceresinde "Evet" gerekir. Yönetici hesabınız yoksa BT'den yardım isteyin ya da "başka bir SQL Server" seçeneğiyle firmanın mevcut sunucusunu kullanın. |
| *SQL Server kurulumu başarısız (kod …)* | Ayrıntılar `C:\Program Files\Microsoft SQL Server\…\Setup Bootstrap\Log\Summary.txt` içinde. Bekleyen Windows güncellemesi varsa bilgisayarı yeniden başlatıp tekrar deneyin. |
| *Yerel SQL Server'a bağlanılamadı* (elle seçilen sunucu) | Servis çalışıyor mu, sunucu adı doğru mu? Adlandırılmış örneklerde (`PC\SQLEXPRESS`) *SQL Server Browser* servisi açık ve TCP/IP etkin olmalı. Kullanıcı/şifre (genelde `sa`) doğru mu? |
| *Yedek okunamadı… Operating system error 5* | SQL Server servisinin dosyayı okuma izni yok. `.bak`'ı SQL Server'ın `Backup` klasörüne (ör. `C:\Program Files\Microsoft SQL Server\MSSQL15.SQLEXPRESS\MSSQL\Backup`) koyup tam yolunu yazın. |
| *…is incompatible with this server* | Yedek daha yeni bir SQL Server sürümüyle alınmış; bilgisayardaki SQL Server'ı güncelleyin. |
| *Logo firma tabloları bulunamadı* | Yedek Logo Tiger/Go veritabanı değil, ya da sadece sistem veritabanı (`LOGODB`) yedeği alınmış. |
| Port 5055 dolu | `LOGO_ANALIZ_PORT=5056` ortam değişkeniyle başka port kullanın. |

## Kaynaktan çalıştırma ve geliştirme

Python 3.11+ gerekir.

```bash
git clone https://github.com/mdrmz/Piksel_STDIO-LOGO-TIGER.git
cd Piksel_STDIO-LOGO-TIGER
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python demo/demo_db_olustur.py demo/logo_demo.sqlite
python app.py
```
Ya da hazır betikler: `calistir_windows.bat`, `calistir_mac.command`, `calistir_linux.sh`.

**Testler:** `pip install pytest && pytest -q tests`. Testler, uygulamanın sonuçlarını ham Logo tablolarından
düz SQL ile hesaplanan değerlerle karşılaştırır.

**Tek klasörlük paket derleme:** `pip install pyinstaller && pyinstaller LogoTigerAnaliz.spec --noconfirm`
→ `dist/LogoTigerAnaliz/`

**Yeni sürüm yayınlama:** `core/__init__.py` içindeki `__version__` değerini artırıp (ör. `"0.1.1"`) `main`'e gönderin.
GitHub Actions testleri çalıştırır, üç sistem için paketleri derler, `v0.1.1` etiketini ve Release'i kendisi oluşturur.

**Klasör yapısı**
```
app.py                 yerel web arayüzü (Flask, sadece 127.0.0.1)
core/kurulum.py        SQL Server'ın otomatik kurulumu (Windows: Express, macOS/Linux: Docker)
core/kaynak.py         .bak geri yükleme, SQL Server ve SQLite bağlantıları
core/logo.py           firma/dönem tarama, Logo tablolarından temiz modele aktarım
core/analiz.py         ABC, trend, ölü stok, stok devir
core/rapor.py          Excel raporu ve Power BI paketi
demo/                  Logo yapısında örnek veri üretici
templates/             arayüz sayfaları
tests/                 uçtan uca testler + gerçek Windows/SQL Server entegrasyon testi
.github/workflows/     otomatik test, derleme ve sürüm yayınlama
```

Katkılar ve hata bildirimleri için [Issues](../../issues) kullanabilirsiniz. Hata bildirirken bekleme ekranındaki
log çıktısını ekleyin (müşteri verisi içeren dosyaları **eklemeyin**).

---

### English summary
Local tool that restores a **Logo Tiger** ERP backup (`.bak`) into a temporary database on the local SQL Server,
extracts sales/purchase/stock data with Logo-specific cleaning rules, and produces ABC analysis (products, customers,
suppliers), monthly trends, dead-stock and stock-turnover reports as a web dashboard, an Excel workbook and a
Power BI-ready star schema. Runs entirely on `127.0.0.1`; no data leaves the machine.
Download a build from [Releases](../../releases/latest) (Windows, macOS, Linux).

## Lisans

[MIT](LICENSE) © 2026 Piksel Analitik. Logo, Logo Tiger ve Logo Go, Logo Yazılım'ın markalarıdır; bu proje Logo Yazılım ile
bağlantılı değildir.
