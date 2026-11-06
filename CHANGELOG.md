# Değişiklik günlüğü

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
