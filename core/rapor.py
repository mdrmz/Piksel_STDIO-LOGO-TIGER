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
SINIF_RENK = {"A": "#C6EFCE", "B": "#FFEB9C", "C": "#F4CCCC"}

BASLIK = {
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
}
PARA = {"net_tutar", "net_satis", "net_alis", "gecen_yil_ayni_ay", "ort_birim_maliyet", "tahmini_deger", "toplam"}
YUZDE = {"pay", "kumulatif_pay", "yillik_degisim", "kalem_payi", "deger_payi"}
SAYI = {"toplam_miktar", "stok_miktar", "yillik_satis", "stok_gun"}
ONDALIK = {"devir_hizi"}


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


def _abc_sayfa(wb, f, ad, baslik, tablo: pd.DataFrame, deger="net_tutar", esik=None):
    ws = wb.add_worksheet(ad)
    ws.write(0, 0, baslik, f["h1"])
    e = esik or AbcEsik()
    ws.write(1, 0, f"A: tutarın ilk %{e.a * 100:.0f}'i · B: %{e.b * 100:.0f}'e kadar · C: kalanı", f["not"])
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


def excel_raporu(a: Analiz, yol: str, olu_stok_gun: int = 90, esik=None):
    with pd.ExcelWriter(yol, engine="xlsxwriter") as xw:
        wb = xw.book
        f = _fmt(wb)
        o = a.ozet()
        m = a.meta

        # ---- Özet
        ws = wb.add_worksheet("Özet")
        ws.set_column(0, 0, 34); ws.set_column(1, 1, 22)
        ws.write(0, 0, f"{m.get('firma_ad') or 'Firma ' + m.get('firma', '')} — Satış ve Stok Analizi", f["h1"])
        ws.write(1, 0, f"Dönem: {o['tarih_bas']} – {o['tarih_bit']}   ·   Rapor tarihi: {dt.date.today():%d.%m.%Y}", f["not"])
        satirlar = [
            ("Net satış (KDV hariç)", o["net_satis"], "para"), ("Brüt satış", o["brut_satis"], "para"),
            ("Satış iadesi", o["iade_tutar"], "para"), ("İade oranı", o["iade_orani"], "yuzde"),
            ("Fatura sayısı", o["fatura_sayisi"], "sayi"), ("Ortalama fatura tutarı", o["ortalama_fatura"], "para"),
            ("Satış yapılan müşteri", o["musteri_sayisi"], "sayi"), ("Satılan ürün çeşidi", o["urun_sayisi"], "sayi"),
            ("Net alış (KDV hariç)", o["net_alis"], "para"), ("Alış yapılan tedarikçi", o["tedarikci_sayisi"], "sayi"),
        ]
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

        # ---- ABC sayfaları
        _abc_sayfa(wb, f, "Ürün ABC", "Ürün ABC analizi (net satış tutarına göre)", a.urun_abc(esik=esik), esik=esik)
        _abc_sayfa(wb, f, "Müşteri ABC", "Müşteri ABC analizi (net satış tutarına göre)", a.musteri_abc(esik), esik=esik)
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

        for sayfa in wb.worksheets():   # yazdırma: yatay, sayfa genişliğine sığdır
            sayfa.set_landscape()
            sayfa.set_paper(9)
            sayfa.fit_to_pages(1, 0)
            sayfa.set_margins(0.4, 0.4, 0.5, 0.5)
    return yol


POWERBI_OKUBENI = """Power BI'a bağlama
==================

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
4) abc_urun / abc_musteri / abc_tedarikci tabloları uygulamanın hesapladığı ABC sınıflarıdır;
   malzeme_kod / cari_kod üzerinden dim tablolarına bağlayıp görsellerde "sinif" alanını kullanabilirsiniz.

Tutarlar KDV hariç nettir (Logo VATMATRAH). Miktarlar ana birimdedir. İptal faturalar ve
faturalanmamış irsaliyeler dışlanmıştır; iadeler eksi tutarlıdır.
"""


def powerbi_disa_aktar(a: Analiz, depo: str, klasor: str) -> str:
    os.makedirs(klasor, exist_ok=True)
    con = sqlite3.connect(depo)
    for t in ["dim_cari", "dim_malzeme", "dim_tarih", "fact_satis", "fact_alis", "stok_bakiye"]:
        df = pd.read_sql_query(f"SELECT * FROM {t}", con)
        for c in ("iade", "faturali"):
            if c in df.columns:
                df[c] = df[c].astype(bool)
        if "tarih" in df.columns:
            df["tarih"] = pd.to_datetime(df["tarih"]).dt.strftime("%Y-%m-%d")
        df.to_csv(os.path.join(klasor, f"{t}.csv"), index=False, encoding="utf-8-sig")
    con.close()
    a.urun_abc().to_csv(os.path.join(klasor, "abc_urun.csv"), index=False, encoding="utf-8-sig")
    a.musteri_abc().to_csv(os.path.join(klasor, "abc_musteri.csv"), index=False, encoding="utf-8-sig")
    a.tedarikci_abc().to_csv(os.path.join(klasor, "abc_tedarikci.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(klasor, "OKUBENI_PowerBI.txt"), "w", encoding="utf-8") as fh:
        fh.write(POWERBI_OKUBENI)
    zip_yol = klasor.rstrip("/\\") + ".zip"
    with zipfile.ZipFile(zip_yol, "w", zipfile.ZIP_DEFLATED) as z:
        for ad in sorted(os.listdir(klasor)):
            z.write(os.path.join(klasor, ad), ad)
    return zip_yol
