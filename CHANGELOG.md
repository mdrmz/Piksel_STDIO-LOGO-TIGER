# Değişiklik günlüğü

## v0.6.5 — 2026-11-13
- **Düzeltme:** tarayıcıdan seçilen yedek v0.6.2'den beri SQL Server'ın klasörüne kopyalanmak yerine taşınıyordu.
  Windows aynı diskte taşınan dosyanın eski (kullanıcı klasörü) izinlerini koruduğu için SQL Server dosyayı
  okuyamıyor, "Operating system error 5 (Erişim engellendi)" veriliyordu. Taşınan ve kopyalanan yedeğin izinleri
  artık yeni klasörden yeniden alınır ve SQL Server servisine açıkça okuma izni verilir; izin verilemezse taşıma geri
  alınıp kopyalanır.
- Yine de erişim engellenirse izin düzeltilip bir kez daha denenir. Olmazsa "dosya bozuk olabilir" yerine erişim
  sorunu olduğu ve ne yapılacağı söylenir.

## v0.6.4 — 2026-11-13
- **Düzeltme:** v0.6.3'teki "C: doluysa başka diske geç" kontrolü SQL Server'ın `DATA` klasörünün boş alanını
  ölçmeye çalışıyordu. Bu klasöre normal kullanıcı erişemediği için ölçüm sessizce başarısız oluyor ve kontrol
  atlanıyordu; geri yükleme SQL Server'ın 3257 "insufficient free space" hatasıyla düşüyordu. Boş alan artık sürücü
  kökünden (`C:\`) ölçülür.
- **Ek güvence:** SQL Server yine de 3257 hatası verirse program geçici veritabanını yeri olan diğer diske
  (`D:\LogoAnalizVeri\veri`) açıp bir kez daha dener. Hiçbir diskte yer yoksa gereken alanı ve diğer disklerin boş
  alanını söyler.
- README'deki sorun giderme satırında yol düzeltildi.

## v0.6.3 — 2026-11-13
- **C: dolu, başka disk boşsa kendiliğinden oraya geçer.** Yedeğin SQL Server için kopyası ve geri yüklenen
  geçici veritabanı, C: sürücüsünde yer yetmiyorsa yeterli boş alanı olan diğer sabit diske
  (`D:\LogoAnalizVeri\yedek` ve `D:\LogoAnalizVeri\veri`) konur; SQL Server servisine o klasör için izin verilir.
  Ayar gerekmez; iş bitince oradaki geçici dosyalar da silinir. Firmanın kendi yedek dosyasına dokunulmaz.
- Kural: C: hem kopyayı hem açılmış veritabanını rahatça alıyorsa her şey eskisi gibi C:'de kalır; almıyorsa en boş
  disk seçilir. Hiçbir diskte yer yoksa ne kadar yer açılması gerektiği söylenir.

## v0.6.2 — 2026-11-13
Disk alanı: "No space left on device" (Errno 28) hatasına yol açan durumlar giderildi.
- **Tarayıcıdan yüklenen yedek artık diskte bir kez yer kaplar.** Önce Windows'un geçici klasörüne, sonra programın
  klasörüne, sonra da SQL Server'ın klasörüne kopyalanıyordu (yedeğin 3 katı). Artık doğrudan programın klasörüne
  yazılır ve SQL Server'ın klasörüne kopyalanmadan taşınır (aynı sürücüde anında).
- **Geri yüklemeden önce yer kontrolü:** yedeğin açılmış hâlinin boyutu (sıkıştırılmış yedeklerde `.bak`'tan kat kat
  büyük olabilir) boş alanla karşılaştırılır; yetmiyorsa "X GB gerekiyor, C: sürücüsünde Y GB boş" denir.
- Kopya sığmıyorsa ya da yarıda kalırsa yarım dosya silinir ve yedek bulunduğu yerden okunur (önceden yarım kopya
  diskte kalıyor, ardından bir klasörde daha deneniyordu).
- **Önceki denemelerden kalan artıklar temizlenir:** program kapatıldığında ya da çöktüğünde silinemeyen
  `LOGOANALIZ_XXXXXXXX` geçici veritabanları, yedek kopyaları ve yarım yüklemeler bir sonraki yedek açılışında silinir.
- Geri yükleme yarıda kalırsa geçici veritabanı ve kopya hemen silinir.
- Yükleme sırasında disk dolarsa anlaşılır mesaj (507) çıkar ve yarım dosya silinir.
- Tarayıcı yüklemeyi yarıda keserse (Werkzeug `ClientDisconnected`) de v0.6.1'deki açıklayıcı mesaj gösterilir ve
  yarım dosya hemen silinir (v0.6.1 bu durumu yakalamıyordu).

## v0.6.1 — 2026-11-13
- Yedek tarayıcıdan yüklenirken yarıda kesilirse (dosya seçildikten sonra değişti/kilitlendi, USB/ağ sürücüsü
  okunamadı, sayfa yenilendi) artık boş "Bad Request" sayfası yerine nedenini ve çözümünü anlatan bir hata sayfası
  çıkar: dosyanın tam yolunu yazmak. Olay tanılama günlüğüne de yazılır.
- Ana ekranda Başlat'a basılınca düğme kilitlenir ve "Yedek yükleniyor (… MB)" yazar; ikinci tıklama yüklemeyi kesmez.

## v0.6.0 — 2026-11-13
- **İki yedek birlikte:** Logo'nun firma tanımlarını ayrı bir sistem veritabanında (`LOGODB`) tuttuğu kurulumlarda
  firmadan iki `.bak` gelir. Ana ekranda **İkinci yedek var mı?** alanından ikisi birlikte seçilir; program ikisini de
  geri yükler ve tek kaynak gibi okur. Hangisinin firma verisi, hangisinin LOGODB olduğunu içeriğinden kendisi anlar,
  sıra önemli değildir. Firma adları (`L_CAPIFIRM`), dönem tarihleri (`L_CAPIPERIOD`) ve ambar adları (`L_CAPIWHOUSE`)
  LOGODB'den gelir.
- **Yıl yıl ayrı açılmış firmalar tek analizde:** firma seçim ekranında birden fazla firma işaretlenebilir
  (ör. 001 = 2024, 002 = 2025). Seçilen firmalar birleştirilir: müşteri ve malzemeler **koduna** göre eşleştirilir
  (her firmada iç numaralar farklıdır), satış/alış/kâr tüm yıllardan toplanır, geçen yıla göre karşılaştırma yıllar
  arasında çalışır, maliyet önceki yılın alışlarından devam eder. Stok ve cari bakiye en yeni firmanın son döneminden
  alınır. Tarih aralıkları çakışırsa (çift sayım riski) ya da firmalar neredeyse hiç ortak cari kodu paylaşmıyorsa
  (farklı şirket olabilir) panelde ve veri notlarında uyarı çıkar. Tek firma seçildiğinde sonuçlar değişmez.
- **Ambar (depo) bazında görünüm:** panelin üstünde ambar seçici (Tüm ambarlar / MERKEZ DEPO / …). Bir ambar seçilince
  bütün panel — ciro, fatura, müşteri, ürün ABC, kâr, aylık grafik, alış, stok, ölü stok, sipariş önerisi — yalnızca
  o ambardan çıkan/giren hareketlerle hesaplanır; Excel raporu ve PDF özet de o ambar için iner. Cari bakiye/alacak
  ambara bağlı olmadığından tüm firmayı gösterir.
- Depo tablosu genişledi: ambar başına fatura sayısı, ortalama fatura, müşteri, ürün çeşidi, iade, kâr marjı, stok.
  Panelde her ambarın en çok çıkan 10 ürünü.
- Excel: **Ambar-Ürün** (hangi ambardan hangi ürün ne kadar çıktı, tam liste) ve **Ambar Aylık** sayfaları.
- Power BI raporuna **Ambar** sayfası: ambar dilimleyicisi, ambar bazında aylık satış, fatura özeti ve ambar × ürün tablosu.
- Yapay zekâ anonimleştirmesi ve tanılama paketi birleştirilen tüm firmaların adlarını gizler.
- Yalnızca LOGODB seçilirse program "firma verilerinin olduğu yedeği de ekleyin" der.
- **Firma seçim ekranı:** çok firmalı yedeklerde arama kutusu; dönemlerin tarih aralığı; birden çok firma varsa hiçbiri
  önceden seçili gelmez, seçilen firma vurgulanır ve "sadece bu firmanın verileri analiz edilir" yazar. LOGODB'de tanımlı
  olup yedekte verisi olmayan firmalar ayrıca listelenir.
- İkinci yedek açılamazsa ilk yedekten oluşturulan geçici veritabanı da silinir.
- Entegrasyon testine gerçek SQL Server'da iki ayrı `.bak` ile uçtan uca adım eklendi.

## v0.5.0 — 2026-11-11
- **Hazır Power BI raporu:** panelde **Power BI raporu aç** düğmesi. Program beş sayfalık raporu (Özet, Kârlılık,
  Alacak, Müşteri ve Temsilci, Stok) model, ilişkiler ve ölçüleriyle birlikte kendisi üretir ve Power BI Desktop'ta
  açar; veriler açılışta kendiliğinden yüklenir, elle tablo bağlamak gerekmez. Windows'ta Power BI Desktop
  (ücretsiz) kurulu olmalıdır; kurulu değilse program bunu söyler.
- Power BI paketi (zip) artık bu hazır raporu (`LogoTigerAnaliz_Rapor.pbit`) da içerir.

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
