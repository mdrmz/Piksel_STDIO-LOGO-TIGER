"""
Raporlar: Excel (grafikli) ve Power BI için yıldız şema (CSV klasörü + zip).
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import zipfile

import pandas as pd

from .analiz import AbcEsik, Analiz, abc_ozet

LACIVERT = "#1F3864"
KIRILIM_SINIRI = 50_000        # Ürün-Cari / aylık pivot sayfalarındaki en fazla satır
RAPOR_SATIR_SINIRI = 200_000   # Excel raporundaki fatura satırı sayfası (fazlası Faturalar ekranından indirilir)
SINIF_RENK = {"A": "#C6EFCE", "B": "#FFEB9C", "C": "#F4CCCC"}

BASLIK = {
    "ort_fiyat": "Ort. net birim fiyat", "fatura_sayisi": "Fatura sayısı",
    "ciro_sinif": "Ciroya göre sınıf", "zarar": "Zarar",
    "sira": "Sıra", "sinif": "Sınıf", "malzeme_kod": "Malzeme kodu", "malzeme_ad": "Malzeme adı",
    "grup_kod": "Grup", "net_tutar": "Net tutar (TL)", "toplam_miktar": "Miktar", "pay": "Pay",
    "kumulatif_pay": "Kümülatif pay", "cari_kod": "Cari kodu", "cari_ad": "Cari adı", "sehir": "Şehir",
    "ozel_kod": "Özel kod", "fatura_sayisi": "Fatura sayısı", "son_alim": "Son alım", "yil_ay": "Ay",
    "net_satis": "Net satış (TL)", "net_alis": "Net alış (TL)", "musteri_sayisi": "Müşteri sayısı",
    "gecen_yil_ayni_ay": "Geçen yıl aynı ay (TL)", "yillik_degisim": "Yıllık değişim",
    "stok_miktar": "Stok miktarı", "son_satis": "Son satış", "satissiz_gun": "Satışsız gün",
    "son_alis": "Son alış", "ort_birim_maliyet": "Ort. alış maliyeti", "tahmini_deger": "Tahmini stok değeri (TL)",
    "yillik_satis": "Yıllık satış miktarı", "devir_hizi": "Devir hızı (yıl)", "stok_gun": "Stok yeterlilik (gün)",
    "kalem_sayisi": "Kalem sayısı", "toplam": "Toplam (TL)", "kalem_payi": "Kalem payı", "deger_payi": "Tutar payı",
    "maliyet": "Satılan malın maliyeti (TL)", "brut_kar": "Tahmini brüt kâr (TL)", "kar_marji": "Kâr marjı",
    "kar_payi": "Kârdaki payı", "bakiye": "Bakiye (TL)", "g0_30": "0-30 gün", "g31_60": "31-60 gün",
    "g61_90": "61-90 gün", "g91_180": "91-180 gün", "g180p": "180+ gün", "en_eski_gun": "En eski açık kayıt (gün)",
    "gecikmis_pay": "60 günü aşan pay", "temsilci_kod": "Temsilci kodu", "temsilci_ad": "Satış temsilcisi",
    "ortalama_fatura": "Ortalama fatura (TL)", "iade_tutar": "İade (TL)", "iade_orani": "İade oranı",
    "durum": "Durum", "alim_sayisi": "Alım sayısı", "ort_aralik_gun": "Ort. alım aralığı (gün)",
    "sessiz_gun": "Son alımdan beri (gün)", "son_12_ay": "Son 12 ay (TL)", "gunluk_satis": "Günlük satış",
    "oneri_miktar": "Önerilen sipariş", "son_birim_maliyet": "Son alış fiyatı", "oneri_tutar": "Sipariş tutarı (TL)",
    "son_tedarikci": "Son tedarikçi", "ambar": "Ambar no", "ambar_ad": "Ambar", "urun_sayisi": "Satılan ürün çeşidi",
    "stok_kalem": "Stoktaki kalem", "stok_deger": "Stok değeri (TL)", "negatif_kalem": "Eksi stoklu kalem",
    "satis_miktar": "Satış miktarı", "miktar": "Miktar", "ambar_ici_pay": "Ambar içindeki payı",
}
PARA = {"net_tutar", "net_satis", "net_alis", "gecen_yil_ayni_ay", "ort_birim_maliyet", "tahmini_deger", "toplam",
        "maliyet", "brut_kar", "bakiye", "g0_30", "g31_60", "g61_90", "g91_180", "g180p", "ortalama_fatura",
        "iade_tutar", "son_12_ay", "son_birim_maliyet", "oneri_tutar", "stok_deger"}
YUZDE = {"pay", "kumulatif_pay", "yillik_degisim", "kalem_payi", "deger_payi", "kar_marji", "kar_payi",
         "gecikmis_pay", "iade_orani", "ambar_ici_pay"}
SAYI = {"toplam_miktar", "satis_miktar", "miktar", "stok_miktar", "yillik_satis", "stok_gun", "oneri_miktar", "sessiz_gun", "en_eski_gun"}
ONDALIK = {"devir_hizi", "gunluk_satis", "ort_aralik_gun"}
PARA.add("ort_fiyat")
DURUM_RENK = {"Kayıp": "sC", "Risk": "sB", "Acil": "sC", "Sipariş ver": "sB"}
MALIYET_NOTU = ("Tahmini brüt kâr = net satış − (satış miktarı × o tarihteki son alış fiyatı). "
                "Yönetim amaçlı tahmindir; muhasebe kârı değildir.")


def tl_yaz(v, ondalik=0) -> str:
    """1234567.8 -> '1.234.568' (Türkçe biçim)."""
    try:
        return f"{float(v):,.{ondalik}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return ""


def _fmt(wb):
    return {
        "baslik": wb.add_format({"bold": True, "font_color": "white", "bg_color": LACIVERT, "border": 1,
                                 "text_wrap": True, "valign": "vcenter"}),
        "para": wb.add_format({"num_format": "#,##0.00"}),
        "yuzde": wb.add_format({"num_format": "0.0%"}),
        "sayi": wb.add_format({"num_format": "#,##0"}),
        "ondalik": wb.add_format({"num_format": "0.0"}),
        "tarih": wb.add_format({"num_format": "dd.mm.yyyy"}),
        "h1": wb.add_format({"bold": True, "font_size": 16, "font_color": LACIVERT}),
        "h2": wb.add_format({"bold": True, "font_size": 12, "font_color": LACIVERT}),
        "etiket": wb.add_format({"bold": True}),
        "not": wb.add_format({"italic": True, "font_color": "#666666"}),
        **{f"s{k}": wb.add_format({"bg_color": v, "bold": True, "align": "center"}) for k, v in SINIF_RENK.items()},
    }


def _tablo_yaz(ws, f, df: pd.DataFrame, satir: int, sutun: int = 0, sinif_renkle=True):
    for j, c in enumerate(df.columns):
        ws.write(satir, sutun + j, BASLIK.get(c, c), f["baslik"])
    for i, row in enumerate(df.itertuples(index=False), start=1):
        for j, (c, v) in enumerate(zip(df.columns, row)):
            if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
                ws.write_blank(satir + i, sutun + j, None)
            elif c == "sinif" and sinif_renkle:
                ws.write(satir + i, sutun + j, v, f.get(f"s{v}"))
            elif c == "durum" and v in DURUM_RENK:
                ws.write(satir + i, sutun + j, v, f.get(DURUM_RENK[v]))
            elif c in PARA:
                ws.write_number(satir + i, sutun + j, float(v), f["para"])
            elif c in YUZDE:
                ws.write_number(satir + i, sutun + j, float(v), f["yuzde"])
            elif c in SAYI:
                ws.write_number(satir + i, sutun + j, float(v), f["sayi"])
            elif c in ONDALIK:
                ws.write_number(satir + i, sutun + j, float(v), f["ondalik"])
            elif isinstance(v, (dt.date, dt.datetime)):
                ws.write_datetime(satir + i, sutun + j, dt.datetime(v.year, v.month, v.day), f["tarih"])
            else:
                ws.write(satir + i, sutun + j, v)
    return satir + len(df)


def _genislik(ws, df, bas=0):
    for j, c in enumerate(df.columns):
        uz = max([len(BASLIK.get(c, c))] + [len(str(x)) for x in df[c].head(200)])
        ws.set_column(bas + j, bas + j, min(45, max(9, uz + 2)))


def _abc_sayfa(wb, f, ad, baslik, tablo: pd.DataFrame, deger="net_tutar", esik=None, ek_not=""):
    ws = wb.add_worksheet(ad)
    ws.write(0, 0, baslik, f["h1"])
    e = esik or AbcEsik()
    ws.write(1, 0, f"A: tutarın ilk %{e.a * 100:.0f}'i · B: %{e.b * 100:.0f}'e kadar · C: kalanı. {ek_not}", f["not"])
    oz = abc_ozet(tablo, deger)
    son = _tablo_yaz(ws, f, oz, 3)
    bas = max(son + 3, 21)  # Pareto grafiği tablonun üstünde kalsın
    _tablo_yaz(ws, f, tablo, bas)
    _genislik(ws, tablo)
    ws.freeze_panes(bas + 1, 0)
    ws.autofilter(bas, 0, bas + len(tablo), len(tablo.columns) - 1)
    # Pareto grafiği (ilk 50 kalem)
    n = min(50, len(tablo))
    if n:
        ci = list(tablo.columns).index(deger)
        ki = list(tablo.columns).index("kumulatif_pay")
        ch = wb.add_chart({"type": "column"})
        ch.add_series({"name": BASLIK[deger], "values": [ad, bas + 1, ci, bas + n, ci],
                       "fill": {"color": LACIVERT}, "gap": 40})
        ln = wb.add_chart({"type": "line"})
        ln.add_series({"name": "Kümülatif pay", "values": [ad, bas + 1, ki, bas + n, ki], "y2_axis": True,
                       "line": {"color": "#C00000", "width": 2}})
        ch.combine(ln)
        ch.set_title({"name": f"Pareto — ilk {n} kalem"})
        ch.set_legend({"position": "bottom"})
        ch.set_x_axis({"visible": False})
        ln.set_y2_axis({"num_format": "0%", "max": 1})
        ch.set_size({"width": 620, "height": 300})
        ws.insert_chart(2, 6, ch)
    return ws


def _liste_sayfa(wb, f, ad, baslik, aciklama, df: pd.DataFrame, bos_mesaj="Kayıt yok."):
    """Başlık + açıklama + filtreli tablo içeren düz liste sayfası."""
    ws = wb.add_worksheet(ad)
    ws.write(0, 0, baslik, f["h1"])
    ws.write(1, 0, aciklama, f["not"])
    if df is None or df.empty:
        ws.write(3, 0, bos_mesaj, f["not"])
        ws.set_column(0, 0, 40)
        return ws
    _tablo_yaz(ws, f, df, 3)
    _genislik(ws, df)
    ws.freeze_panes(4, 0)
    ws.autofilter(3, 0, 3 + len(df), len(df.columns) - 1)
    return ws


def _yaslandirma_sayfa(wb, f, ad, baslik, df, ozet_satir):
    ws = _liste_sayfa(wb, f, ad, baslik, ozet_satir, df, "Açık bakiye yok.")
    if df is not None and len(df):
        dilimler = ["g0_30", "g31_60", "g61_90", "g91_180", "g180p"]
        r = 3 + len(df) + 2
        ws.write(r, 0, "Toplam", f["etiket"])
        for c in ["bakiye"] + dilimler:
            j = list(df.columns).index(c)
            ws.write_number(r, j, float(df[c].sum()), f["para"])
    return ws


def excel_raporu(a: Analiz, yol: str, olu_stok_gun: int = 90, esik=None, yorum_md: str | None = None):
    with pd.ExcelWriter(yol, engine="xlsxwriter") as xw:
        wb = xw.book
        f = _fmt(wb)
        o = a.ozet()
        m = a.meta

        # ---- Özet
        ws = wb.add_worksheet("Özet")
        ws.set_column(0, 0, 34); ws.set_column(1, 1, 22)
        ambar = f" — yalnızca {a.ambar_secili_ad}" if getattr(a, "ambar_secili", None) is not None else ""
        if getattr(a, "tarih_filtre", None):
            ambar += f" — {a.tarih_etiketi()}"
        ws.write(0, 0, f"{m.get('firma_ad') or 'Firma ' + m.get('firma', '')}{ambar} — Satış ve Stok Analizi", f["h1"])
        ws.write(1, 0, f"Dönem: {o['tarih_bas']} – {o['tarih_bit']}   ·   Rapor tarihi: {dt.date.today():%d.%m.%Y}", f["not"])
        satirlar = [
            ("Net satış (KDV hariç)", o["net_satis"], "para"), ("Brüt satış", o["brut_satis"], "para"),
            ("Satış iadesi", o["iade_tutar"], "para"), ("İade oranı", o["iade_orani"], "yuzde"),
            ("Fatura sayısı", o["fatura_sayisi"], "sayi"), ("Ortalama fatura tutarı", o["ortalama_fatura"], "para"),
            ("Satış yapılan müşteri", o["musteri_sayisi"], "sayi"), ("Satılan ürün çeşidi", o["urun_sayisi"], "sayi"),
            ("Net alış (KDV hariç)", o["net_alis"], "para"), ("Alış yapılan tedarikçi", o["tedarikci_sayisi"], "sayi"),
        ]
        if o.get("brut_kar") is not None:
            satirlar += [("Tahmini brüt kâr (son alış maliyetiyle)", o["brut_kar"], "para"),
                         ("Tahmini brüt kâr marjı", o["kar_marji"] or 0.0, "yuzde")]
        co = a.cari_ozet()
        if co:
            satirlar += [("Müşterilerden alacak (KDV dahil)", co["alacak"], "para"),
                         ("  bunun 60 günü aşan kısmı", co["gecikmis"], "para"),
                         ("Tedarikçilere borç (KDV dahil)", co["borc"], "para")]
            if co["tahsilat_gun"] is not None:
                satirlar.append(("Ortalama tahsilat süresi (gün)", co["tahsilat_gun"], "sayi"))
        for i, (k, v, t) in enumerate(satirlar, start=3):
            ws.write(i, 0, k, f["etiket"]); ws.write_number(i, 1, v, f[t])

        r = 3 + len(satirlar) + 2
        ws.write(r, 0, "ABC dağılımı", f["h2"])
        ozetler = [("Ürün", abc_ozet(a.urun_abc(esik=esik), "net_tutar")), ("Müşteri", abc_ozet(a.musteri_abc(esik), "net_tutar")),
                   ("Tedarikçi", abc_ozet(a.tedarikci_abc(esik), "net_tutar"))]
        ws.write(r + 1, 0, "", f["baslik"])
        for j, (ad, _) in enumerate(ozetler):
            ws.write(r + 1, 1 + j, ad, f["baslik"])
        for i, s in enumerate("ABC"):
            ws.write(r + 2 + i, 0, f"{s} sınıfı: kalem sayısı / tutar payı", f[f"s{s}"])
            for j, (_, oz) in enumerate(ozetler):
                x = oz[oz["sinif"] == s].iloc[0]
                ws.write(r + 2 + i, 1 + j, f"{int(x['kalem_sayisi'])} kalem / %{x['deger_payi'] * 100:.1f}")
        ws.set_column(2, 3, 22)

        r += 7
        ws.write(r, 0, "Veri notları", f["h2"])
        for i, (k, v) in enumerate(a.veri_notlari(), start=1):
            ws.write(r + i, 0, k, f["etiket"]); ws.write(r + i, 1, str(v))

        # ---- Yapay zekâ yorumu (oluşturulduysa)
        if yorum_md:
            import re as _re
            ws = wb.add_worksheet("YZ Yorumu")
            ws.set_column(0, 0, 120)
            kaynak = _re.search(r"<!--\s*(.*?)\s*-->", yorum_md)
            ws.write(0, 0, "Yönetici yorumu (yapay zekâ)", f["h1"])
            ws.write(1, 0, "Yapay zekâ tarafından üretilmiştir; kararlardan önce rakamları diğer sayfalardan doğrulayın."
                     + (f"  ·  {kaynak.group(1)}" if kaynak else ""), f["not"])
            sarili = wb.add_format({"text_wrap": True, "valign": "top"})
            r = 3
            for satir in _re.sub(r"<!--.*?-->", "", yorum_md, flags=_re.S).strip().splitlines():
                if not satir.strip():
                    continue
                h = _re.match(r"^#{1,4}\s+(.*)", satir)
                if h:
                    r += 1
                    ws.write(r, 0, h.group(1), f["h2"])
                else:
                    ws.write(r, 0, _re.sub(r"\*\*(.+?)\*\*", r"\1", satir.strip()), sarili)
                r += 1

        # ---- ABC sayfaları
        for seviye, sayfa_ad, ne in (("malzeme", "Ürün ABC (Kâr)", "Ürün"), ("cari", "Müşteri ABC (Kâr)", "Müşteri")):
            kt = a.kar_abc(seviye, esik)
            if kt is not None and len(kt):
                kt = kt.assign(zarar=kt["zarar"].map({True: "Zarar", False: ""}))
                _abc_sayfa(wb, f, sayfa_ad, f"{ne} ABC analizi — kârlılığa göre (tahmini brüt kâr)", kt, deger="brut_kar",
                           esik=esik, ek_not="A = kârın çoğunu getirenler. " + MALIYET_NOTU)
        _abc_sayfa(wb, f, "Ürün ABC (Ciro)", "Ürün ABC analizi (net satış tutarına göre)", a.urun_abc(esik=esik), esik=esik)
        _abc_sayfa(wb, f, "Müşteri ABC (Ciro)", "Müşteri ABC analizi (net satış tutarına göre)", a.musteri_abc(esik), esik=esik)
        _abc_sayfa(wb, f, "Tedarikçi ABC", "Tedarikçi ABC analizi (net alış tutarına göre)", a.tedarikci_abc(esik), esik=esik)

        # ---- Aylık trend
        t = a.aylik_trend()
        ws = wb.add_worksheet("Aylık Trend")
        ws.write(0, 0, "Aylık satış ve alış", f["h1"])
        _tablo_yaz(ws, f, t, 2)
        _genislik(ws, t)
        ch = wb.add_chart({"type": "column"})
        ch.add_series({"name": "Net satış", "categories": ["Aylık Trend", 3, 0, 2 + len(t), 0],
                       "values": ["Aylık Trend", 3, 1, 2 + len(t), 1], "fill": {"color": LACIVERT}})
        ch.add_series({"name": "Net alış", "categories": ["Aylık Trend", 3, 0, 2 + len(t), 0],
                       "values": ["Aylık Trend", 3, 4, 2 + len(t), 4], "fill": {"color": "#A6A6A6"}})
        ch.set_title({"name": "Aylık net satış / alış (TL)"}); ch.set_legend({"position": "bottom"})
        ch.set_size({"width": 760, "height": 320})
        ws.insert_chart(3 + len(t) + 2, 0, ch)

        # ---- Grup satış
        g = a.grup_satis()
        ws = wb.add_worksheet("Grup Satış")
        ws.write(0, 0, "Malzeme grubuna göre satış", f["h1"])
        _tablo_yaz(ws, f, g, 2); _genislik(ws, g)

        # ---- Ölü stok
        d = a.olu_stok(olu_stok_gun)
        ws = wb.add_worksheet("Ölü Stok")
        ws.write(0, 0, f"Ölü stok — elde olup son {olu_stok_gun} gündür satılmayan malzemeler", f["h1"])
        ws.write(1, 0, f"{len(d)} kalem · tahmini değer {d['tahmini_deger'].sum():,.2f} TL (ortalama alış maliyetiyle)"
                 .replace(",", "X").replace(".", ",").replace("X", "."), f["not"])
        _tablo_yaz(ws, f, d, 3); _genislik(ws, d); ws.freeze_panes(4, 0)

        # ---- Stok devir
        s = a.stok_devir().sort_values("devir_hizi", na_position="first")
        ws = wb.add_worksheet("Stok Devir")
        ws.write(0, 0, "Stok devir hızı ve yeterlilik süresi", f["h1"])
        ws.write(1, 0, "Devir hızı = yıllık satış miktarı / mevcut stok. Negatif stoklar Logo'daki kayıt hatalarına işaret eder.", f["not"])
        _tablo_yaz(ws, f, s, 3); _genislik(ws, s); ws.freeze_panes(4, 0)
        ws.autofilter(3, 0, 3 + len(s), len(s.columns) - 1)

        # ---- Kârlılık
        ku, km, kg = a.karlilik("malzeme"), a.karlilik("cari"), a.karlilik("grup")
        if ku is not None:
            _liste_sayfa(wb, f, "Ürün Kârlılık", "Ürün kârlılığı (tahmini brüt kâra göre sıralı)", MALIYET_NOTU, ku)
            _liste_sayfa(wb, f, "Müşteri Kârlılık", "Müşteri kârlılığı (tahmini brüt kâra göre sıralı)", MALIYET_NOTU, km)
            _liste_sayfa(wb, f, "Grup Kârlılık", "Malzeme grubu kârlılığı", MALIYET_NOTU, kg)

        # ---- Cari bakiye yaşlandırma
        if co:
            not_ = (f"{co['ref_tarih']} itibarıyla · dönem {co['donem']} cari hareketlerinden · tutarlar KDV dahil · "
                    "ödemeler en eski kayıttan düşülür (FIFO), kalan açık tutar hareket tarihine göre yaşlandırılır")
            _yaslandirma_sayfa(wb, f, "Alacak Yaşlandırma", "Müşterilerden alacaklar — yaşlandırma",
                               a.cari_yaslandirma("alacak"), not_)
            _yaslandirma_sayfa(wb, f, "Borç Yaşlandırma", "Tedarikçilere borçlar — yaşlandırma",
                               a.cari_yaslandirma("borc"), not_)

        # ---- Satış temsilcisi
        tp = a.temsilci_performans()
        if tp is not None:
            _liste_sayfa(wb, f, "Temsilci", "Satış temsilcisi performansı",
                         "Net satış iadeler düşülmüş tutardır. " + MALIYET_NOTU, tp)

        # ---- Müşteri kaybı
        _liste_sayfa(wb, f, "Müşteri Kaybı", "Alımı kesilen ya da seyrekleşen müşteriler",
                     "Kayıp: son alımından bu yana kendi ortalama alım aralığının 3 katından (en az 60 gün) uzun süre geçmiş · "
                     "Risk: 2 katından (en az 30 gün) uzun süre geçmiş. En az 3 ayrı günde alım yapmış müşteriler.",
                     a.musteri_kaybi(), "Kayıp ya da risk altında müşteri görünmüyor.")

        # ---- Sipariş önerisi
        _liste_sayfa(wb, f, "Sipariş Önerisi", "Sipariş önerisi — stoğu bitmek üzere olan malzemeler",
                     "Son 90 günün satış hızına göre stoğu 15 günden az yetecek malzemeler; öneri stoğu 45 günlük ihtiyaca tamamlar. "
                     "Acil: stok bitmiş ya da 7 günden az. Tutar son alış fiyatıyla hesaplanır.",
                     a.siparis_onerisi(), "Sipariş gerektiren malzeme görünmüyor.")

        # ---- Depo
        dk = a.depo_kirilim()
        if dk is not None:
            _liste_sayfa(wb, f, "Depo", "Ambar (depo) bazında satış, fatura ve stok",
                         "Fatura sayısı: o ambardan mal çıkan fatura sayısı (iki ambardan mal çıkan fatura ikisinde de sayılır). "
                         "Stok değeri son alış fiyatıyla hesaplanır. Eksi stoklu kalemler Logo'da eksik giriş/transfer kaydına işaret eder.", dk)
            _liste_sayfa(wb, f, "Ambar-Ürün", "Hangi ambardan hangi ürün ne kadar çıktı",
                         "Her ambar için ürünler net satışa göre sıralı. Filtre düğmesiyle tek bir ambarı ya da ürünü seçebilirsiniz. "
                         "Stok: o ambardaki bakiye.", a.ambar_urun())
            aa = a.ambar_aylik()
            ws_aa = _liste_sayfa(wb, f, "Ambar Aylık", "Aylık net satış — ambar ambar (TL, KDV hariç)",
                                 "Satırlar ay, sütunlar ambar.", aa)
            for j in range(1, len(aa.columns)):
                ws_aa.set_column(j, j, max(16, len(str(aa.columns[j])) + 2), f["para"])

        # ---- ayrıntılı kırılımlar: ürün × cari, ürün ay ay, cari ay ay
        _liste_sayfa(wb, f, "Ürün-Cari", "Hangi ürün hangi firmaya ne kadar satıldı",
                     f"Net tutara göre sıralı; en büyük {KIRILIM_SINIRI:,} satır. Filtre düğmesiyle tek bir ürünü ya da cariyi seçin. "
                     "Tek ürünün/carinin tamamı için programdaki ürün ve cari kartlarına bakın.".replace(",", "."),
                     a.urun_cari(KIRILIM_SINIRI))
        for seviye, deger, sayfa_ad, bas, acik in (
                ("malzeme", "miktar", "Ürün Aylık", "Ürünlerin ay ay satış miktarı", "Satırlar ürün, sütunlar ay (ana birim miktar)."),
                ("cari", "net_tutar", "Cari Aylık", "Carilerin ay ay net satışı (TL, KDV hariç)", "Satırlar cari, sütunlar ay.")):
            pv = a.aylik_pivot(seviye, deger).head(KIRILIM_SINIRI)
            ws_p = _liste_sayfa(wb, f, sayfa_ad, bas, acik + " Hangi ayda ne gitmiş, mevsimi olan ürünler buradan görülür.", pv)
            for j in range(3, len(pv.columns)):
                ws_p.set_column(j, j, 12, f["para" if deger == "net_tutar" else "sayi"])

        # ---- Faturalar (Logo ile sağlama: her fatura, tablo adı ve LOGICALREF ile)
        from . import fatura as fat
        if fat.var_mi(a.con):
            v = fat.yukle(a.con)
            tf = getattr(a, "tarih_filtre", None) or (None, None)
            fl = fat.Filtre(ambar=a.ambar_secili, bas=tf[0] or "", bit=tf[1] or "")
            fs, _ = fat.suz(v, fl)
            not_ = (f"{a.ambar_secili_ad} ambarı. " if a.ambar_secili is not None else "") + \
                   (f"Tarih: {a.tarih_etiketi()}." if getattr(a, "tarih_filtre", None) else "")
            fat.excel_sayfalari(wb, v, fs, not_.strip(), satir_siniri=RAPOR_SATIR_SINIRI)

        for sayfa in wb.worksheets():   # yazdırma: yatay, sayfa genişliğine sığdır
            sayfa.set_landscape()
            sayfa.set_paper(9)
            sayfa.fit_to_pages(1, 0)
            sayfa.set_margins(0.4, 0.4, 0.5, 0.5)
    return yol


POWERBI_OKUBENI = """Hazır rapor
===========

LogoTigerAnaliz_Rapor.pbit dosyasına çift tıklayın: Power BI Desktop açılır, veriler kendiliğinden yüklenir ve
beş sayfalık rapor (Özet, Kârlılık, Alacak, Müşteri ve Temsilci, Stok) hazır gelir. İlişkiler ve ölçüler kuruludur.
Power BI Desktop ücretsizdir ve yalnızca Windows'ta çalışır (Microsoft Store'da "Power BI Desktop").

Rapor verileri, bu dosyaların programın oluşturduğu ilk klasöründen okur. Klasörü taşıdıysanız ya da başka bir
bilgisayara kopyaladıysanız: Power BI'da Giriş > Veri dönüştürme > "Klasor" sorgusundaki yolu yeni klasörle
değiştirin (sonunda \\ olmalı) ve Kapat ve Uygula'ya basın.

Aşağıdaki bölüm, raporu sıfırdan kendiniz kurmak isterseniz gereklidir.


Power BI'a bağlama (elle)
=========================

Bu klasördeki CSV dosyaları yıldız şemadır (UTF-8, ayraç: virgül, ondalık: nokta).

1) Power BI Desktop > Veri al > Klasör (veya her CSV için "Metin/CSV") ile dosyaları yükleyin.
   CSV açılırken "Dosya kaynağı: 65001 Unicode (UTF-8)" seçili olsun.
2) Model görünümünde ilişkileri kurun (tek yönlü, 1'e çok):
     dim_malzeme[malzeme_ref]  ->  fact_satis[malzeme_ref], fact_alis[malzeme_ref], stok_bakiye[malzeme_ref]
     dim_cari[cari_ref]        ->  fact_satis[cari_ref],    fact_alis[cari_ref]
     dim_tarih[tarih]          ->  fact_satis[tarih],       fact_alis[tarih]
   dim_tarih tablosunu "Tarih tablosu olarak işaretle" deyin.
3) Hazır ölçüler (DAX):
     Net Satış     = SUM(fact_satis[net_tutar])
     Net Alış      = SUM(fact_alis[net_tutar])
     İade Tutarı   = -CALCULATE(SUM(fact_satis[net_tutar]), fact_satis[iade] = TRUE())
     Fatura Sayısı = DISTINCTCOUNT(fact_satis[fatura_no])
     Geçen Yıl     = CALCULATE([Net Satış], SAMEPERIODLASTYEAR(dim_tarih[tarih]))
     Maliyet       = SUM(fact_satis[maliyet_tutar])
     Brüt Kâr      = CALCULATE([Net Satış] - [Maliyet], NOT ISBLANK(fact_satis[maliyet_tutar]))
     Kâr Marjı     = DIVIDE([Brüt Kâr], CALCULATE([Net Satış], NOT ISBLANK(fact_satis[maliyet_tutar])))
     Cari Bakiye   = SUM(fact_cari_hareket[borc]) - SUM(fact_cari_hareket[alacak])
   Ek ilişkiler:
     dim_temsilci[temsilci_ref] ->  fact_satis[satis_temsilci_ref]
     dim_ambar[ambar]           ->  fact_satis[ambar], fact_alis[ambar], stok_ambar[ambar]
     dim_cari[cari_ref]         ->  fact_cari_hareket[cari_ref]
     dim_malzeme[malzeme_ref]   ->  stok_ambar[malzeme_ref], malzeme_maliyet[malzeme_ref]
   maliyet_tutar: satış miktarı × o tarihteki son alış fiyatı (tahmini; muhasebe kârı değildir).
   fact_cari_hareket: en son dönemin cari hareketleri (borc/alacak, KDV dahil; devir fişi önceki bakiyeyi taşır).
4) Hazır analiz tabloları (uygulamanın hesapladığı sonuçlar; doğrudan tablo/görsel olarak kullanılabilir):
     alacak_yaslandirma, borc_yaslandirma, temsilci_performans, musteri_kaybi, siparis_onerisi, depo_kirilim,
     karlilik_urun, karlilik_musteri
5) abc_urun / abc_musteri / abc_tedarikci tabloları uygulamanın hesapladığı ABC sınıflarıdır;
   malzeme_kod / cari_kod üzerinden dim tablolarına bağlayıp görsellerde "sinif" alanını kullanabilirsiniz.

Tutarlar KDV hariç nettir (Logo VATMATRAH). Miktarlar ana birimdedir. İptal faturalar ve
faturalanmamış irsaliyeler dışlanmıştır; iadeler eksi tutarlıdır.
"""


def powerbi_disa_aktar(a: Analiz, depo: str, klasor: str, olu_stok_gun: int = 90) -> str:
    os.makedirs(klasor, exist_ok=True)
    con = sqlite3.connect(depo)
    var = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ["dim_cari", "dim_malzeme", "dim_tarih", "fact_satis", "fact_alis", "stok_bakiye",
              "dim_temsilci", "dim_ambar", "stok_ambar", "malzeme_maliyet", "fact_cari_hareket"]:
        if t not in var:   # eski sürümle oluşturulmuş analiz
            continue
        df = pd.read_sql_query(f"SELECT * FROM {t}", con)
        tf = getattr(a, "tarih_filtre", None)
        if tf and t in ("fact_satis", "fact_alis", "dim_tarih"):   # panelde seçilen tarih aralığı Power BI'a da uygulanır
            from .analiz import tarih_maskesi
            df = df[tarih_maskesi(pd.to_datetime(df["tarih"], errors="coerce"), tf[0], tf[1])]
        for c in ("tarih", "son_giris_tarih"):
            if c in df.columns:
                df[c] = pd.to_datetime(df[c]).dt.strftime("%Y-%m-%d")
        for c in ("iade", "faturali"):
            if c in df.columns:
                df[c] = df[c].astype(bool)
        df.to_csv(os.path.join(klasor, f"{t}.csv"), index=False, encoding="utf-8-sig")
    con.close()
    a.urun_abc().to_csv(os.path.join(klasor, "abc_urun.csv"), index=False, encoding="utf-8-sig")
    a.musteri_abc().to_csv(os.path.join(klasor, "abc_musteri.csv"), index=False, encoding="utf-8-sig")
    a.tedarikci_abc().to_csv(os.path.join(klasor, "abc_tedarikci.csv"), index=False, encoding="utf-8-sig")
    hazir = {"alacak_yaslandirma": a.cari_yaslandirma("alacak"), "borc_yaslandirma": a.cari_yaslandirma("borc"),
             "temsilci_performans": a.temsilci_performans(), "musteri_kaybi": a.musteri_kaybi(),
             "siparis_onerisi": a.siparis_onerisi(), "depo_kirilim": a.depo_kirilim(),
             "karlilik_urun": a.karlilik("malzeme"), "karlilik_musteri": a.karlilik("cari")}
    for ad, df in hazir.items():
        if df is not None:
            df.to_csv(os.path.join(klasor, f"{ad}.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(klasor, "OKUBENI_PowerBI.txt"), "w", encoding="utf-8") as fh:
        fh.write(POWERBI_OKUBENI)
    from .powerbi import pbit_olustur
    pbit_olustur(a, klasor, olu_stok_gun)   # hazır rapor: çift tıklayınca verilerle açılır
    zip_yol = klasor.rstrip("/\\") + ".zip"
    with zipfile.ZipFile(zip_yol, "w", zipfile.ZIP_DEFLATED) as z:
        for ad in sorted(os.listdir(klasor)):
            z.write(os.path.join(klasor, ad), ad)
    return zip_yol
