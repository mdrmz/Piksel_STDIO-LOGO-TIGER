# Değişiklik günlüğü

## v0.4.0 — 2026-11-11
Altı yeni analiz ve PDF yönetici özeti. Hepsi `.bak` içindeki Logo tablolarından çıkar; ek veri gerekmez.
- **Kârlılık:** ürün, müşteri ve malzeme grubu bazında tahmini brüt kâr ve marj. Maliyet, her satışa o tarihten
  önceki en son alış (yoksa devir/üretim girişi) birim fiyatı atanarak hesaplanır ("son alış maliyeti").
  Logo'nun maliyetlendirme servisinin çalıştırılmış olması gerekmez; sonuç yönetim amaçlı tahmindir, muhasebe kârı değildir.
- **Alacak / borç yaşlandırma:** cari hareketlerden (`CLFLINE`) bakiye ve 0-30 / 31-60 / 61-90 / 91-180 / 180+ gün
  dilimleri (ödemeler en eski kayıttan düşülür — FIFO), ortalama tahsilat süresi.
- **Satış temsilcisi performansı:** temsilci bazında net satış, pay, fatura, müşteri, iade, tahmini kâr (`LG_SLSMAN`).
- **Müşteri kaybı:** kendi ortalama alım aralığının 3 katı (Kayıp) ya da 2 katı (Risk) süredir alım yapmayan müşteriler.
- **Sipariş önerisi:** son 90 günün satış hızına göre stoğu 15 günden az yetecek ürünler ve 45 günlük ihtiyaca
  tamamlayan miktar; son tedarikçi ve tahmini tutar.
- **Depo (ambar) kırılımı:** ambar bazında satış, stok kalemi ve stok değeri (`L_CAPIWHOUSE`).
- **PDF yönetici özeti:** tek sayfa A4; temel göstergeler, aylık grafik, ilk 5 ürün/müşteri, alacak yaşlandırma ve
  kural tabanlı "Öne çıkanlar".
- **Panel:** yeni göstergeler, "Öne çıkanlar" ve yukarıdaki analizlerin tabloları. **Excel raporu:** 9 yeni sayfa.
  **Power BI paketi:** `fact_cari_hareket`, `dim_temsilci`, `dim_ambar`, `stok_ambar`, `malzeme_maliyet` ve hazır
  analiz tabloları; brüt kâr ve cari bakiye DAX ölçüleri. **Yapay zekâ:** yeni analizler özetine ve şemaya eklendi;
  satış temsilcisi adları da takma adla (`Temsilci-01`) gider.
- Önceki sürümle oluşturulmuş analizler açılmaya devam eder; yeni bölümler için yedeğin yeniden analiz edilmesi gerekir.
- **Düzeltme:** panel sayfasının sekme başlığına betik kodu karışıyordu.
- Demo veriye cari hareketler, satış elemanları ve ikinci ambar eklendi (satış rakamları değişmedi).

## v0.3.2 — 2026-11-10
- **Düzeltme (Windows):** SQL Server Express otomatik kurulumu, program yönetici olmadan açıldığında
  `WinError 740 — İstenen işlem için yükseltme gerekiyor` hatasıyla duruyordu. Microsoft'un önyükleyicisi
  (`SQL2022-SSEI-Expr.exe`) yönetici izni istiyor; medya indirme ve dosya açma adımları artık kurulumla birlikte
  yönetici betiğinde çalışıyor. Sonuç: tek bir UAC penceresi ("Evet"), programı yönetici olarak açmaya gerek yok.

## v0.3.1 — 2026-11-06
- **Tanılama paketi:** hata ekranında ve sayfa altında "Tanılama paketi" düğmesi. Günlükler, sürüm, SQL Server/Docker
  durumu ve analizlerin sadece meta bilgisi; müşteri verisi, firma ve kullanıcı adları içermez.
- İşlem günlüğü artık diske de yazılıyor (`LogoAnaliz/gunluk.txt`); beklenmeyen hatalar ayrıntısıyla kaydediliyor.
- Test: entegrasyon testi artık paketlenmiş programı (exe) da derleyip Windows ve Linux'ta
  `.bak → analiz → Excel / Power BI / YZ paketi` akışını baştan sona sınıyor.

## v0.3.0 — 2026-11-06
- **Yapay zekâ:** Gemini, DeepSeek, OpenAI ve Claude API desteği; model güncel listeden otomatik seçilir.
  - Panelde "Yapay zekâya sor": gerekirse salt-okur SQL ile veriye bakan çok turlu soru-cevap.
  - "Yönetici yorumu": Excel raporuna "YZ Yorumu" sayfası.
  - **Claude Desktop bağlantısı (MCP):** Claude Pro aboneliğiyle ek ücret olmadan; tek tıkla kurulum.
  - **Yapay zekâ paketi:** ChatGPT Plus vb. sohbetlere sürüklenecek özet + CSV + hazır ilk mesaj.
  - **Anonimleştirme** (varsayılan açık): cari/firma adları yapay zekâya takma adla gider.
  - API anahtarı diskte şifreli.
- Geçen yılın aynı dönemine göre müşteri/ürün karşılaştırması.

## v0.2.1 — 2026-11-06
- Linux/Docker: Docker yok / kapalı / yetki yok durumları için açık yönlendirmeler; ARM Linux uyarısı.
- Entegrasyon testi Ubuntu + Docker üzerinde de çalışıyor.

## v0.2.0 — 2026-11-06
- **SQL Server otomatik kurulum:** Windows'ta SQL Server 2022 Express sessiz kurulum (yönetici izniyle),
  sabit port, şifreli `sa` şifresi; macOS/Linux'ta Docker konteyneri.
- Türkçe karakterler: `varchar` kolonlar sunucuda Unicode'a çevriliyor; bozuk kodlama onarılıyor.
- Gerçek Windows'ta entegrasyon testi: kurulum + Logo yapısında veritabanı + BACKUP + geri yükleme + karşılaştırma.

## v0.1.0 — 2026-11-06
- İlk sürüm: `.bak` geri yükleme, firma/dönem tarama, ABC (ürün/müşteri/tedarikçi), aylık trend, grup satış,
  ölü stok, stok devir; panel, Excel raporu, Power BI paketi; Windows/macOS/Linux paketleri.
