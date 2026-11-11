"""
"Yapay zekâ paketi": ChatGPT, Claude, Gemini gibi herhangi bir sohbet uygulamasına sürükleyip bırakılacak zip.
Abonelikle (ChatGPT Plus vb.) API anahtarı olmadan analiz yaptırmanın en basit yolu.
"""
from __future__ import annotations

import io
import zipfile

from .analiz import AbcEsik
from .yz import Maske, baglam

TALIMAT = """Bu dosyalar bir firmanın Logo Tiger ERP verisinden çıkarılmış satış/stok analizidir.
Lütfen önce OZET.md dosyasını oku, gerekirse CSV dosyalarına bak, sonra Türkçe olarak:

1. Firmanın genel durumunu 5 maddede özetle (satış trendi, kârlılık, alacaklar, yoğunlaşma riski, stok).
2. ABC analizine göre hangi ürün ve müşterilere odaklanılması gerektiğini söyle.
3. Geçen yıla göre en çok düşen müşteriler için olası nedenleri ve atılacak adımları öner.
4. Ölü stok için somut bir eritme planı, gecikmiş alacaklar için tahsilat önceliği öner.
5. Yönetime sunulacak 5 maddelik aksiyon listesi çıkar.

Kurallar: Sadece bu verilere dayan, uydurma. Rakamları Türkçe biçimde yaz (1.234.567 TL).
{anonim_not}
Ardından sorularımı cevapla.
"""

ORNEK_SORULAR = """Örnek sorular:
- En çok ciro yaptığımız 10 müşterinin toplam satıştaki payı ne? Bu bir risk mi?
- Geçen yıla göre en çok düşen 5 müşteri kim, ne kadar düştü?
- Hangi malzeme grubunda büyüme var, hangisinde düşüş?
- A sınıfı ürünlerin kaçı ölü stokta ya da stoğu tükenmek üzere?
- Aylık satışta mevsimsellik var mı? Hangi aylar zayıf?
- Önümüzdeki çeyrek için satış tahmini yapabilir misin (kaba)?
"""


def paket_olustur(a, bilgi: dict, hedef: str, anonim: bool = True) -> str:
    maske = Maske(a, bilgi.get("firma_ad", ""), anonim)
    esik = AbcEsik(bilgi.get("abc_a", 0.8), bilgi.get("abc_b", 0.95))
    dosyalar = {
        "abc_urun.csv": a.urun_abc(esik=esik),
        "abc_musteri.csv": maske.tablo(a.musteri_abc(esik)),
        "abc_tedarikci.csv": maske.tablo(a.tedarikci_abc(esik)),
        "aylik_trend.csv": a.aylik_trend(),
        "grup_satis.csv": a.grup_satis(),
        "olu_stok.csv": a.olu_stok(bilgi.get("olu_stok_gun", 90)),
        "stok_devir.csv": a.stok_devir(),
    }
    ek = {"karlilik_urun.csv": a.karlilik("malzeme"), "karlilik_musteri.csv": a.karlilik("cari"),
          "alacak_yaslandirma.csv": a.cari_yaslandirma("alacak"), "borc_yaslandirma.csv": a.cari_yaslandirma("borc"),
          "temsilci_performans.csv": a.temsilci_performans(), "musteri_kaybi.csv": a.musteri_kaybi(),
          "siparis_onerisi.csv": a.siparis_onerisi(), "depo_kirilim.csv": a.depo_kirilim()}
    dosyalar.update({ad: maske.tablo(df) for ad, df in ek.items() if df is not None})
    for sev, ad in (("cari", "musteri_gecen_yil.csv"), ("malzeme", "urun_gecen_yil.csv")):
        k = a.donem_karsilastirma(sev)
        if k is not None:
            dosyalar[ad] = maske.tablo(k)
    anonim_not = ("Gizlilik için cari ve firma adları takma adlarla (Cari-0012, FİRMA) verilmiştir; bu adları aynen kullan."
                  if anonim else "")
    with zipfile.ZipFile(hedef, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("OZET.md", baglam(a, bilgi, maske))
        z.writestr("ILK_MESAJ_kopyala_yapistir.txt", TALIMAT.format(anonim_not=anonim_not) + "\n" + ORNEK_SORULAR)
        for ad, df in dosyalar.items():
            buf = io.StringIO()
            df.to_csv(buf, index=False)
            z.writestr(ad, "﻿" + buf.getvalue())
        z.writestr("NASIL_KULLANILIR.txt",
                   "1) Bu zip'i açın.\n2) ChatGPT / Claude / Gemini'de yeni sohbet açın, OZET.md ve CSV dosyalarını sürükleyip bırakın.\n"
                   "3) ILK_MESAJ_kopyala_yapistir.txt içeriğini yapıştırıp gönderin.\n\n"
                   + ("Cari adları gizlenmiştir. Takma adların gerçek karşılıkları sadece Logo Tiger Analiz programında görünür.\n"
                      if anonim else "DİKKAT: Anonimleştirme kapalı; cari adları açık olarak dosyalarda yer alıyor.\n"))
    return hedef
