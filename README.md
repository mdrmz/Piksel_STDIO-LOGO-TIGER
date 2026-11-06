# Logo Tiger Analiz

**Logo Tiger yedeğinden (`.bak`) dakikalar içinde satış, stok, müşteri ve tedarikçi analizi.**
Firmaya gidin, programı açın, yedeği seçin, Excel raporunu ya da Power BI paketini alın.

Kurulum gerektirmeyen, kendi bilgisayarınızda çalışan bir araçtır. Hiçbir veri internete gönderilmez.

![Analiz paneli](docs/panel.png)

---

## İçindekiler
- [Hızlı başlangıç (fabrikada kullanım)](#hızlı-başlangıç-fabrikada-kullanım)
- [Gereken tek şey: SQL Server](#gereken-tek-şey-sql-server)
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
3. **Yedeği seçin.** *Yedek dosyası (.bak)* sekmesinde Logo yedeğini seçin, SQL Server bilgilerini girin
   (Logo kurulu bilgisayarlarda genelde sunucu `BILGISAYARADI\SQLEXPRESS` veya `BILGISAYARADI\LOGO`, kullanıcı `sa`).
4. **Firma ve dönem seçin.** Program yedekteki tüm Logo firmalarını ve dönemlerini kendisi bulur.
5. **Raporu alın.** Panelde özet görünür; **Excel raporu** ya da **Power BI paketi** düğmesiyle indirin.

> Program yedeği geçici bir veritabanına (`LOGOANALIZ_XXXXXXXX`) geri yükler, verileri okur ve bu geçici veritabanını
> **siler**. Firmanın kendi Logo veritabanına dokunulmaz, yazılmaz.

Önce denemek isterseniz **Demo veri** sekmesi, Logo tablo yapısında üretilmiş örnek bir firmayla çalışır.

![Ana ekran](docs/ana.png)

## Gereken tek şey: SQL Server

`.bak` dosyası bir SQL Server yedeğidir ve yalnızca SQL Server tarafından açılabilir.

| Durum | Ne yapılır |
|---|---|
| **Logo kurulu Windows bilgisayar** (en sık durum) | SQL Server zaten var. Ek kurulum gerekmez. |
| **Kendi Windows laptopunuz** | Ücretsiz [SQL Server Express](https://www.microsoft.com/sql-server/sql-server-downloads)'i bir kez kurun. Kurulumda *Mixed Mode* seçip `sa` şifresi belirleyin. |
| **macOS / Linux** | [Docker](https://www.docker.com/products/docker-desktop/) kurun, bu repodaki `docker-compose.yml` ile SQL Server'ı başlatın (aşağıda). |

**macOS / Linux için SQL Server (Docker):**
```bash
docker compose up -d          # docker-compose.yml'nin olduğu klasörde
# .bak dosyasını ./yedekler klasörüne koyun
```
Uygulamada: Sunucu `localhost`, kullanıcı `sa`, şifre `LogoAnaliz!2026`,
*Gelişmiş → "SQL Server'ın dosyayı gördüğü yol"* = `/var/opt/mssql/backup/DOSYA.bak`.
Apple Silicon Mac'lerde Docker Desktop ayarlarında *Use Rosetta for x86_64/amd64 emulation* açık olmalı.

> Yedek, bilgisayardaki SQL Server'dan **daha yeni** bir sürümle alınmışsa geri yüklenemez
> (ör. SQL Server 2022 yedeği 2019'a açılmaz). Bu durumda bilgisayardaki SQL Server'ı güncelleyin.

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
- Firmanın Logo veritabanında yalnızca `SELECT` çalıştırılır. Yedekten açılan geçici veritabanı iş bitince silinir;
  tarayıcıdan yüklenen `.bak` kopyası da silinir.
- Analiz klasörü (`~/LogoAnaliz/`) cari adları ve satış tutarları içerir. İş bittiğinde **Önceki analizler → Sil**
  ile kaldırabilir, ya da yalnızca rapor dosyalarını firmaya teslim edebilirsiniz.
- Repo `.gitignore`'u `.bak`, `.mdf`, `.xlsx` gibi dosyaların yanlışlıkla GitHub'a gönderilmesini engeller.

## Sorun giderme

| Mesaj | Çözüm |
|---|---|
| *Yerel SQL Server'a bağlanılamadı* | SQL Server servisi çalışıyor mu? Sunucu adı doğru mu? Adlandırılmış örneklerde (`PC\SQLEXPRESS`) *SQL Server Browser* servisi açık olmalı. Kullanıcı/şifre (genelde `sa`) doğru mu? |
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

**Yeni sürüm yayınlama:** `core/__init__.py` içindeki sürümü artırın, sonra
```bash
git tag v0.1.1 && git push origin v0.1.1
```
GitHub Actions testleri çalıştırır, üç sistem için paketleri derler ve Releases sayfasına ekler.

**Klasör yapısı**
```
app.py                 yerel web arayüzü (Flask, sadece 127.0.0.1)
core/kaynak.py         .bak geri yükleme, SQL Server ve SQLite bağlantıları
core/logo.py           firma/dönem tarama, Logo tablolarından temiz modele aktarım
core/analiz.py         ABC, trend, ölü stok, stok devir
core/rapor.py          Excel raporu ve Power BI paketi
demo/                  Logo yapısında örnek veri üretici
templates/             arayüz sayfaları
tests/                 uçtan uca testler
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
