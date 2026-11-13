"""
Hazır Power BI raporu (.pbit şablonu) üretir: model, ilişkiler, ölçüler ve rapor sayfaları kodla yazılır.
Kullanıcı dosyayı açtığında Power BI Desktop verileri CSV klasöründen kendiliğinden yükler; elle bir şey kurmak gerekmez.

.pbit bir zip'tir:  Version · [Content_Types].xml · DataModelSchema (model, TMSL JSON) · Report/Layout (sayfalar) ·
Settings · Metadata  — JSON parçaları UTF-16 LE kodludur.

CSV klasörünün yolu modeldeki "Klasor" sorgusuna yazılır. Klasör taşınırsa Power BI'da
Giriş > Veri dönüştürme > Klasor sorgusundaki yol değiştirilip Yenile'ye basılır.
"""
from __future__ import annotations

import json
import os
import uuid
import zipfile

import pandas as pd

from .analiz import Analiz

RAPOR_ADI = "LogoTigerAnaliz_Rapor.pbit"

# model kolon tanımı: (modeldeki ad, CSV'deki kolon, tip, gizli)   tip: metin | sayi | tam | tarih | mantik
T = {
    "Satış": ("fact_satis.csv", [
        ("Tarih", "tarih", "tarih", 1), ("malzeme_ref", "malzeme_ref", "tam", 1), ("cari_ref", "cari_ref", "tam", 1),
        ("ambar_no", "ambar", "tam", 1), ("temsilci_ref", "satis_temsilci_ref", "tam", 1), ("Fatura No", "fatura_no", "metin", 0),
        ("Miktar", "miktar", "sayi", 1), ("Net Tutar", "net_tutar", "sayi", 1), ("İade", "iade", "mantik", 0),
        ("Maliyet Tutar", "maliyet_tutar", "sayi", 1)]),
    "Alış": ("fact_alis.csv", [
        ("Tarih", "tarih", "tarih", 1), ("malzeme_ref", "malzeme_ref", "tam", 1), ("cari_ref", "cari_ref", "tam", 1),
        ("Miktar", "miktar", "sayi", 1), ("Net Tutar", "net_tutar", "sayi", 1)]),
    "Cari": ("dim_cari.csv", [
        ("cari_ref", "cari_ref", "tam", 1), ("Cari Kodu", "cari_kod", "metin", 0), ("Cari", "cari_ad", "metin", 0),
        ("Şehir", "sehir", "metin", 0), ("Cari Özel Kod", "ozel_kod", "metin", 0)]),
    "Malzeme": ("dim_malzeme.csv", [
        ("malzeme_ref", "malzeme_ref", "tam", 1), ("Malzeme Kodu", "malzeme_kod", "metin", 0),
        ("Malzeme", "malzeme_ad", "metin", 0), ("Grup", "grup_kod", "metin", 0)]),
    "Takvim": ("dim_tarih.csv", [
        ("Tarih", "tarih", "tarih", 0), ("Yıl", "yil", "tam", 0), ("Ay No", "ay", "tam", 1), ("Ay", "ay_ad", "metin", 0),
        ("Çeyrek", "ceyrek", "tam", 0), ("Yıl-Ay", "yil_ay", "metin", 0)]),
    "Temsilci": ("dim_temsilci.csv", [
        ("temsilci_ref", "temsilci_ref", "tam", 1), ("Temsilci Kodu", "temsilci_kod", "metin", 0),
        ("Temsilci", "temsilci_ad", "metin", 0)]),
    "Ambar": ("dim_ambar.csv", [("ambar_no", "ambar", "tam", 1), ("Ambar", "ambar_ad", "metin", 0)]),
    "Alacak": ("alacak_yaslandirma.csv", [
        ("Cari Kodu", "cari_kod", "metin", 0), ("Cari", "cari_ad", "metin", 0), ("Şehir", "sehir", "metin", 0),
        ("bakiye", "bakiye", "sayi", 1), ("g0_30", "g0_30", "sayi", 1), ("g31_60", "g31_60", "sayi", 1),
        ("g61_90", "g61_90", "sayi", 1), ("g91_180", "g91_180", "sayi", 1), ("g180p", "g180p", "sayi", 1),
        ("en_eski_gun", "en_eski_gun", "sayi", 1)]),
    "Müşteri Kaybı": ("musteri_kaybi.csv", [
        ("Durum", "durum", "metin", 0), ("Cari Kodu", "cari_kod", "metin", 0), ("Cari", "cari_ad", "metin", 0),
        ("Son Alım", "son_alim", "tarih", 0), ("alim_sayisi", "alim_sayisi", "sayi", 1),
        ("sessiz_gun", "sessiz_gun", "sayi", 1), ("son_12_ay", "son_12_ay", "sayi", 1)]),
    "Sipariş Önerisi": ("siparis_onerisi.csv", [
        ("Durum", "durum", "metin", 0), ("Malzeme Kodu", "malzeme_kod", "metin", 0), ("Malzeme", "malzeme_ad", "metin", 0),
        ("Son Tedarikçi", "son_tedarikci", "metin", 0), ("stok_miktar", "stok_miktar", "sayi", 1),
        ("stok_gun", "stok_gun", "sayi", 1), ("oneri_miktar", "oneri_miktar", "sayi", 1), ("oneri_tutar", "oneri_tutar", "sayi", 1)]),
    "Ölü Stok": ("olu_stok.csv", [
        ("Malzeme Kodu", "malzeme_kod", "metin", 0), ("Malzeme", "malzeme_ad", "metin", 0), ("Grup", "grup_kod", "metin", 0),
        ("Son Satış", "son_satis", "tarih", 0), ("stok_miktar", "stok_miktar", "sayi", 1),
        ("satissiz_gun", "satissiz_gun", "sayi", 1), ("tahmini_deger", "tahmini_deger", "sayi", 1)]),
}
ILISKI = [("Satış", "Tarih", "Takvim", "Tarih"), ("Satış", "malzeme_ref", "Malzeme", "malzeme_ref"),
          ("Satış", "cari_ref", "Cari", "cari_ref"), ("Satış", "temsilci_ref", "Temsilci", "temsilci_ref"),
          ("Satış", "ambar_no", "Ambar", "ambar_no"), ("Alış", "Tarih", "Takvim", "Tarih"),
          ("Alış", "malzeme_ref", "Malzeme", "malzeme_ref")]
TL, YZ, SAYI = "#,0", "0.0%", "#,0"
OLCU = {  # tablo -> [(ad, DAX, biçim)]
    "Satış": [
        ("Net Satış", "SUM('Satış'[Net Tutar])", TL),
        ("Maliyet", "SUM('Satış'[Maliyet Tutar])", TL),
        ("Brüt Kâr", "CALCULATE(SUM('Satış'[Net Tutar]) - SUM('Satış'[Maliyet Tutar]), NOT ISBLANK('Satış'[Maliyet Tutar]))", TL),
        ("Kâr Marjı", "DIVIDE([Brüt Kâr], CALCULATE(SUM('Satış'[Net Tutar]), NOT ISBLANK('Satış'[Maliyet Tutar])))", YZ),
        ("İade Tutarı", "-CALCULATE(SUM('Satış'[Net Tutar]), 'Satış'[İade] = TRUE())", TL),
        ("Fatura Sayısı", "CALCULATE(DISTINCTCOUNT('Satış'[Fatura No]), 'Satış'[İade] = FALSE())", SAYI),
        ("Müşteri Sayısı", "DISTINCTCOUNT('Satış'[cari_ref])", SAYI),
        ("Satış Miktarı", "SUM('Satış'[Miktar])", SAYI),
        ("Ortalama Birim Fiyat", "DIVIDE(SUM('Satış'[Net Tutar]), SUM('Satış'[Miktar]))", "#,0.00"),
        ("Ürün Çeşidi", "DISTINCTCOUNT('Satış'[malzeme_ref])", SAYI),
        ("Ortalama Fatura", "DIVIDE([Net Satış] + [İade Tutarı], [Fatura Sayısı])", TL)],
    "Alış": [("Net Alış", "SUM('Alış'[Net Tutar])", TL)],
    "Alacak": [("Alacak Bakiyesi", "SUM('Alacak'[bakiye])", TL), ("0-30 Gün", "SUM('Alacak'[g0_30])", TL),
               ("31-60 Gün", "SUM('Alacak'[g31_60])", TL), ("61-90 Gün", "SUM('Alacak'[g61_90])", TL),
               ("91-180 Gün", "SUM('Alacak'[g91_180])", TL), ("180+ Gün", "SUM('Alacak'[g180p])", TL),
               ("60 Günü Aşan", "SUM('Alacak'[g61_90]) + SUM('Alacak'[g91_180]) + SUM('Alacak'[g180p])", TL),
               ("En Eski Kayıt (gün)", "MAX('Alacak'[en_eski_gun])", SAYI)],
    "Müşteri Kaybı": [("Sessiz Gün", "MAX('Müşteri Kaybı'[sessiz_gun])", SAYI),
                      ("Alım Sayısı", "SUM('Müşteri Kaybı'[alim_sayisi])", SAYI),
                      ("Son 12 Ay Satış", "SUM('Müşteri Kaybı'[son_12_ay])", TL),
                      ("Kayıp/Risk Müşteri", "COUNTROWS('Müşteri Kaybı')", SAYI)],
    "Sipariş Önerisi": [("Stok", "SUM('Sipariş Önerisi'[stok_miktar])", SAYI),
                        ("Stok Yeter (gün)", "MAX('Sipariş Önerisi'[stok_gun])", SAYI),
                        ("Önerilen Sipariş", "SUM('Sipariş Önerisi'[oneri_miktar])", SAYI),
                        ("Sipariş Tutarı", "SUM('Sipariş Önerisi'[oneri_tutar])", TL),
                        ("Sipariş Gereken Ürün", "COUNTROWS('Sipariş Önerisi')", SAYI)],
    "Ölü Stok": [("Ölü Stok Miktarı", "SUM('Ölü Stok'[stok_miktar])", SAYI),
                 ("Satışsız Gün", "MAX('Ölü Stok'[satissiz_gun])", SAYI),
                 ("Ölü Stok Değeri", "SUM('Ölü Stok'[tahmini_deger])", TL),
                 ("Ölü Stok Kalemi", "COUNTROWS('Ölü Stok')", SAYI)],
}
_M_TIP = {"metin": "type text", "sayi": "type number", "tam": "Int64.Type", "tarih": "type date", "mantik": "type logical"}
_BIM_TIP = {"metin": "string", "sayi": "double", "tam": "int64", "tarih": "dateTime", "mantik": "boolean"}


# ---------------------------------------------------------------------------- model
def _m_sorgu(dosya, kolonlar):
    kaynak = ", ".join('"%s"' % c for _, c, _, _ in kolonlar)
    ad = ", ".join('{"%s", "%s"}' % (c, a) for a, c, _, _ in kolonlar if a != c)
    tip = ", ".join('{"%s", %s}' % (a, _M_TIP[t]) for a, _, t, _ in kolonlar)
    return ["let",
            f'    Kaynak = Csv.Document(File.Contents(Klasor & "{dosya}"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),',
            "    Baslik = Table.PromoteHeaders(Kaynak, [PromoteAllScalars = true]),",
            f"    Secim = Table.SelectColumns(Baslik, {{{kaynak}}}, MissingField.UseNull),",
            f"    Ad = Table.RenameColumns(Secim, {{{ad}}})," if ad else "    Ad = Secim,",
            f'    Tip = Table.TransformColumnTypes(Ad, {{{tip}}}, "en-US")',
            "in", "    Tip"]


def _model(klasor: str) -> dict:
    tablolar = []
    for tablo, (dosya, kolonlar) in T.items():
        cols = []
        for ad, _, tip, gizli in kolonlar:
            c = {"name": ad, "dataType": _BIM_TIP[tip], "sourceColumn": ad, "summarizeBy": "none"}
            if gizli:
                c["isHidden"] = True
            if tip == "tarih":
                c["formatString"] = "dd.mm.yyyy"
            if tablo == "Takvim" and ad == "Ay":
                c["sortByColumn"] = "Ay No"
            cols.append(c)
        t = {"name": tablo, "columns": cols,
             "partitions": [{"name": tablo, "mode": "import", "source": {"type": "m", "expression": _m_sorgu(dosya, kolonlar)}}]}
        if tablo in OLCU:
            t["measures"] = [{"name": a, "expression": d, "formatString": b} for a, d, b in OLCU[tablo]]
        tablolar.append(t)
    yol = klasor.rstrip("/\\") + ("\\" if "\\" in klasor or ":" in klasor[:3] else "/")
    return {"compatibilityLevel": 1567, "model": {
        "culture": "tr-TR", "defaultPowerBIDataSourceVersion": "powerBI_V3",
        "annotations": [{"name": "__PBI_TimeIntelligenceEnabled", "value": "0"}],
        "expressions": [{"name": "Klasor", "kind": "m", "expression": '"%s"' % yol.replace('"', '""')}],
        "tables": tablolar,
        "relationships": [{"name": str(uuid.uuid4()), "fromTable": ft, "fromColumn": fc, "toTable": tt, "toColumn": tc}
                          for ft, fc, tt, tc in ILISKI]}}


# ---------------------------------------------------------------------------- rapor sayfaları
def O(tablo, ad):   # ölçü
    return ("o", tablo, ad)


def K(tablo, ad):   # kolon
    return ("k", tablo, ad)


def _deger(v):
    return {"expr": {"Literal": {"Value": v}}}


ACILIR = {"data": [{"properties": {"mode": {"expr": {"Literal": {"Value": "'Dropdown'"}}}}}],
          "general": [{"properties": {"selfFilterEnabled": {"expr": {"Literal": {"Value": "true"}}}}}]}   # açılır liste + arama kutusu


def _gorsel(ad, tip, x, y, w, h, roller: dict, baslik=None, sirala=None, toplam=True, nesne=None):
    """roller: rol -> [alan]; sirala: (alan, "artan"|"azalan"); toplam: tabloda toplam satırı gösterilsin mi"""
    takma, secim, proj = {}, [], {}

    def ifade(alan):
        tur, tablo, isim = alan
        if tablo not in takma:
            takma[tablo] = f"t{len(takma)}"
        return {("Measure" if tur == "o" else "Column"): {"Expression": {"SourceRef": {"Source": takma[tablo]}}, "Property": isim}}

    for rol, alanlar in roller.items():
        for alan in alanlar:
            ref = f"{alan[1]}.{alan[2]}"
            if not any(s["Name"] == ref for s in secim):
                secim.append({**ifade(alan), "Name": ref})
            proj.setdefault(rol, []).append({"queryRef": ref})
    sorgu = {"Version": 2, "From": [], "Select": secim}
    if sirala:
        sorgu["OrderBy"] = [{"Direction": 1 if sirala[1] == "artan" else 2, "Expression": ifade(sirala[0])}]
    sorgu["From"] = [{"Name": v, "Entity": k, "Type": 0} for k, v in takma.items()]
    tek = {"visualType": tip, "projections": proj, "prototypeQuery": sorgu}
    if tip == "card":       # "46 M" / "12 B" yerine tam sayı: 45.995.309
        tek["objects"] = {"labels": [{"properties": {"labelDisplayUnits": _deger("1D"), "fontSize": _deger("24D")}}]}
    elif tip == "tableEx" and not toplam:
        tek["objects"] = {"total": [{"properties": {"totals": _deger("false")}}]}
    if nesne:
        tek["objects"] = {**tek.get("objects", {}), **nesne}
    if baslik:
        metin = "'" + baslik.replace("'", "''") + "'"
        tek["vcObjects"] = {"title": [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}},
                                                      "text": {"expr": {"Literal": {"Value": metin}}}}}]}
    cfg = {"name": ad, "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": 0, "width": w, "height": h}}], "singleVisual": tek}
    return {"x": x, "y": y, "z": 0, "width": w, "height": h, "config": json.dumps(cfg, ensure_ascii=False), "filters": "[]"}


def _kartlar(onek, y, olculer, h=110):
    """Sayfanın üstüne eşit genişlikte kart sırası."""
    n, bosluk = len(olculer), 16
    w = (1280 - 2 * 24 - (n - 1) * bosluk) / n
    return [_gorsel(f"{onek}k{i}", "card", round(24 + i * (w + bosluk)), y, round(w), h, {"Values": [o]}) for i, o in enumerate(olculer)]


def _sayfalar() -> list:
    S, A = "Satış", "Alacak"
    ozet = _kartlar("oz", 16, [O(S, "Net Satış"), O(S, "Brüt Kâr"), O(S, "Kâr Marjı"), O(S, "Fatura Sayısı"),
                               O(S, "Müşteri Sayısı"), O(A, "Alacak Bakiyesi")]) + [
        _gorsel("oz_aylik", "clusteredColumnChart", 24, 142, 860, 290,
                {"Category": [K("Takvim", "Yıl-Ay")], "Y": [O(S, "Net Satış"), O("Alış", "Net Alış")]},
                "Aylık net satış ve alış", (K("Takvim", "Yıl-Ay"), "artan")),
        _gorsel("oz_yil", "slicer", 900, 142, 356, 130, {"Values": [K("Takvim", "Yıl")]}, "Yıl"),
        _gorsel("oz_grup", "clusteredBarChart", 900, 288, 356, 416, {"Category": [K("Malzeme", "Grup")], "Y": [O(S, "Net Satış")]},
                "Malzeme grubuna göre satış", (O(S, "Net Satış"), "azalan")),
        _gorsel("oz_urun", "tableEx", 24, 448, 422, 256, {"Values": [K("Malzeme", "Malzeme"), O(S, "Net Satış")]},
                "En çok satan ürünler", (O(S, "Net Satış"), "azalan")),
        _gorsel("oz_musteri", "tableEx", 462, 448, 422, 256, {"Values": [K("Cari", "Cari"), O(S, "Net Satış")]},
                "En büyük müşteriler", (O(S, "Net Satış"), "azalan")),
    ]
    kar = _kartlar("kr", 16, [O(S, "Net Satış"), O(S, "Maliyet"), O(S, "Brüt Kâr"), O(S, "Kâr Marjı")]) + [
        _gorsel("kr_grup", "clusteredBarChart", 24, 142, 400, 562, {"Category": [K("Malzeme", "Grup")], "Y": [O(S, "Brüt Kâr")]},
                "Malzeme grubuna göre brüt kâr", (O(S, "Brüt Kâr"), "azalan")),
        _gorsel("kr_urun", "tableEx", 440, 142, 816, 273,
                {"Values": [K("Malzeme", "Malzeme"), K("Malzeme", "Grup"), O(S, "Net Satış"), O(S, "Brüt Kâr"), O(S, "Kâr Marjı")]},
                "Ürün kârlılığı (tahmini, son alış maliyetiyle)", (O(S, "Brüt Kâr"), "azalan")),
        _gorsel("kr_musteri", "tableEx", 440, 431, 816, 273,
                {"Values": [K("Cari", "Cari"), K("Cari", "Şehir"), O(S, "Net Satış"), O(S, "Brüt Kâr"), O(S, "Kâr Marjı")]},
                "Müşteri kârlılığı", (O(S, "Brüt Kâr"), "azalan")),
    ]
    alacak = _kartlar("al", 16, [O(A, "Alacak Bakiyesi"), O(A, "0-30 Gün"), O(A, "31-60 Gün"), O(A, "61-90 Gün"),
                                 O(A, "91-180 Gün"), O(A, "180+ Gün")]) + [
        _gorsel("al_sehir", "clusteredBarChart", 24, 142, 400, 562, {"Category": [K(A, "Şehir")], "Y": [O(A, "Alacak Bakiyesi"), O(A, "60 Günü Aşan")]},
                "Şehre göre alacak", (O(A, "Alacak Bakiyesi"), "azalan")),
        _gorsel("al_tablo", "tableEx", 440, 142, 816, 562,
                {"Values": [K(A, "Cari"), O(A, "Alacak Bakiyesi"), O(A, "0-30 Gün"), O(A, "31-60 Gün"), O(A, "61-90 Gün"),
                            O(A, "91-180 Gün"), O(A, "180+ Gün"), O(A, "En Eski Kayıt (gün)")]},
                "Alacak yaşlandırma (KDV dahil; ödemeler en eski kayıttan düşülür)", (O(A, "Alacak Bakiyesi"), "azalan")),
    ]
    M = "Müşteri Kaybı"
    musteri = [
        _gorsel("mu_temsilci", "clusteredBarChart", 24, 16, 400, 330, {"Category": [K("Temsilci", "Temsilci")], "Y": [O(S, "Net Satış")]},
                "Satış temsilcisine göre satış", (O(S, "Net Satış"), "azalan")),
        _gorsel("mu_ttablo", "tableEx", 440, 16, 816, 330,
                {"Values": [K("Temsilci", "Temsilci"), O(S, "Net Satış"), O(S, "Fatura Sayısı"), O(S, "Müşteri Sayısı"),
                            O(S, "Ortalama Fatura"), O(S, "İade Tutarı"), O(S, "Kâr Marjı")]},
                "Satış temsilcisi performansı", (O(S, "Net Satış"), "azalan")),
        _gorsel("mu_sehir", "clusteredBarChart", 24, 362, 400, 342, {"Category": [K("Cari", "Şehir")], "Y": [O(S, "Net Satış")]},
                "Şehre göre satış", (O(S, "Net Satış"), "azalan")),
        _gorsel("mu_kayip", "tableEx", 440, 362, 816, 342,
                {"Values": [K(M, "Durum"), K(M, "Cari"), K(M, "Son Alım"), O(M, "Sessiz Gün"), O(M, "Alım Sayısı"), O(M, "Son 12 Ay Satış")]},
                "Müşteri kaybı — alımı kesilen ya da seyrekleşen müşteriler", (O(M, "Son 12 Ay Satış"), "azalan"), toplam=False),
    ]
    P, U = "Sipariş Önerisi", "Ölü Stok"
    stok = _kartlar("st", 16, [O(P, "Sipariş Gereken Ürün"), O(P, "Sipariş Tutarı"), O(U, "Ölü Stok Kalemi"), O(U, "Ölü Stok Değeri")]) + [
        _gorsel("st_siparis", "tableEx", 24, 142, 720, 562,
                {"Values": [K(P, "Durum"), K(P, "Malzeme"), K(P, "Son Tedarikçi"), O(P, "Stok"), O(P, "Stok Yeter (gün)"),
                            O(P, "Önerilen Sipariş"), O(P, "Sipariş Tutarı")]},
                "Sipariş önerisi — stoğu 15 günden az yetecek ürünler", (O(P, "Sipariş Tutarı"), "azalan"), toplam=False),
        _gorsel("st_olu", "tableEx", 760, 142, 496, 340,
                {"Values": [K(U, "Malzeme"), O(U, "Ölü Stok Miktarı"), O(U, "Satışsız Gün"), O(U, "Ölü Stok Değeri")]},
                "Ölü stok", (O(U, "Ölü Stok Değeri"), "azalan"), toplam=False),
        _gorsel("st_ambar", "clusteredBarChart", 760, 498, 496, 206, {"Category": [K("Ambar", "Ambar")], "Y": [O(S, "Net Satış")]},
                "Ambara göre satış", (O(S, "Net Satış"), "azalan")),
    ]
    # Ambar sayfası: soldaki dilimleyiciden ambar seçilince sayfadaki her şey o ambara göre süzülür
    kart_w, x0 = (1280 - 24 - 264 - 5 * 16) / 6, 24 + 264
    ambar = [_gorsel("am_sec", "slicer", 24, 16, 248, 110, {"Values": [K("Ambar", "Ambar")]}, "Ambar seçin")] + [
        _gorsel(f"am_k{i}", "card", round(x0 + i * (kart_w + 16)), 16, round(kart_w), 110, {"Values": [o]})
        for i, o in enumerate([O(S, "Net Satış"), O(S, "Fatura Sayısı"), O(S, "Ortalama Fatura"), O(S, "Müşteri Sayısı"),
                               O(S, "Satış Miktarı"), O(S, "Brüt Kâr")])] + [
        _gorsel("am_aylik", "clusteredColumnChart", 24, 142, 760, 270,
                {"Category": [K("Takvim", "Yıl-Ay")], "Series": [K("Ambar", "Ambar")], "Y": [O(S, "Net Satış")]},
                "Aylık net satış — ambar ambar", (K("Takvim", "Yıl-Ay"), "artan")),
        _gorsel("am_ozet", "tableEx", 800, 142, 456, 270,
                {"Values": [K("Ambar", "Ambar"), O(S, "Net Satış"), O(S, "Fatura Sayısı"), O(S, "Ortalama Fatura"),
                            O(S, "Müşteri Sayısı"), O(S, "İade Tutarı")]},
                "Ambar bazında satış ve fatura", (O(S, "Net Satış"), "azalan")),
        _gorsel("am_urun", "tableEx", 24, 428, 1232, 276,
                {"Values": [K("Ambar", "Ambar"), K("Malzeme", "Malzeme"), K("Malzeme", "Grup"), O(S, "Satış Miktarı"),
                            O(S, "Net Satış"), O(S, "Fatura Sayısı"), O(S, "Kâr Marjı")]},
                "Hangi ambardan hangi ürün ne kadar çıktı", (O(S, "Net Satış"), "azalan")),
    ]
    # Detay sayfaları: soldan ürün/cari seçilir; grafikte bir aya tıklanınca alttaki tablolar o aya süzülür
    def detay(onek, secim_alan, secim_baslik, kartlar, tablo_baslik, tablo_alanlar):
        kw = (1280 - 24 - 316 - 5 * 16) / 6
        return [_gorsel(f"{onek}_sec", "slicer", 24, 16, 300, 110, {"Values": [secim_alan]}, secim_baslik, nesne=ACILIR)] + [
            _gorsel(f"{onek}_k{i}", "card", round(340 + i * (kw + 16)), 16, round(kw), 110, {"Values": [o]}) for i, o in enumerate(kartlar)] + [
            _gorsel(f"{onek}_yil", "slicer", 24, 142, 300, 110, {"Values": [K("Takvim", "Yıl")]}, "Yıl"),
            _gorsel(f"{onek}_mik", "clusteredColumnChart", 340, 142, 916, 250,
                    {"Category": [K("Takvim", "Yıl-Ay")], "Y": [O(S, "Net Satış")]},
                    "Aylık net satış — bir aya tıklayın, alttaki tablolar o aya süzülür", (K("Takvim", "Yıl-Ay"), "artan")),
            _gorsel(f"{onek}_ay", "tableEx", 24, 268, 300, 436,
                    {"Values": [K("Takvim", "Yıl-Ay"), O(S, "Satış Miktarı"), O(S, "Net Satış"), O(S, "Ortalama Birim Fiyat")]},
                    "Ay ay", (K("Takvim", "Yıl-Ay"), "artan")),
            _gorsel(f"{onek}_tablo", "tableEx", 340, 408, 916, 296, {"Values": tablo_alanlar}, tablo_baslik, (O(S, "Net Satış"), "azalan")),
        ]
    urun_detay = detay("ud", K("Malzeme", "Malzeme"), "Ürün seçin",
                       [O(S, "Net Satış"), O(S, "Satış Miktarı"), O(S, "Ortalama Birim Fiyat"), O(S, "Brüt Kâr"), O(S, "Kâr Marjı"), O(S, "Müşteri Sayısı")],
                       "Hangi firmaya ne kadar satıldı",
                       [K("Cari", "Cari"), K("Cari", "Şehir"), O(S, "Satış Miktarı"), O(S, "Net Satış"), O(S, "Ortalama Birim Fiyat"),
                        O(S, "Brüt Kâr"), O(S, "Kâr Marjı"), O(S, "Fatura Sayısı")])
    cari_detay = detay("cd", K("Cari", "Cari"), "Cari seçin",
                       [O(S, "Net Satış"), O(S, "Fatura Sayısı"), O(S, "Ortalama Fatura"), O(S, "Brüt Kâr"), O(S, "Kâr Marjı"), O(S, "Ürün Çeşidi")],
                       "Hangi ürünü ne kadar aldı",
                       [K("Malzeme", "Malzeme"), K("Malzeme", "Grup"), O(S, "Satış Miktarı"), O(S, "Net Satış"), O(S, "Ortalama Birim Fiyat"),
                        O(S, "Brüt Kâr"), O(S, "Kâr Marjı"), O(S, "Fatura Sayısı")])
    aylik_detay = _kartlar("ay", 16, [O(S, "Net Satış"), O(S, "Brüt Kâr"), O(S, "Kâr Marjı"), O(S, "Fatura Sayısı"),
                                      O(S, "Müşteri Sayısı"), O(S, "Ürün Çeşidi")]) + [
        _gorsel("ay_grafik", "clusteredColumnChart", 24, 142, 1000, 240, {"Category": [K("Takvim", "Yıl-Ay")], "Y": [O(S, "Net Satış")]},
                "Aylık net satış — bir aya tıklayın: o ayın carileri ve ürünleri aşağıda", (K("Takvim", "Yıl-Ay"), "artan")),
        _gorsel("ay_grup", "slicer", 1040, 142, 216, 240, {"Values": [K("Malzeme", "Grup")]}, "Malzeme grubu"),
        _gorsel("ay_cari", "tableEx", 24, 398, 500, 306,
                {"Values": [K("Cari", "Cari"), O(S, "Net Satış"), O(S, "Brüt Kâr"), O(S, "Fatura Sayısı")]},
                "Cariler", (O(S, "Net Satış"), "azalan")),
        _gorsel("ay_urun", "tableEx", 540, 398, 716, 306,
                {"Values": [K("Malzeme", "Malzeme"), K("Malzeme", "Grup"), O(S, "Satış Miktarı"), O(S, "Net Satış"), O(S, "Ortalama Birim Fiyat"), O(S, "Brüt Kâr")]},
                "Ürünler", (O(S, "Net Satış"), "azalan")),
    ]
    sayfalar = [("Özet", ozet), ("Kârlılık", kar), ("Alacak", alacak), ("Müşteri ve Temsilci", musteri), ("Stok", stok),
                ("Ambar", ambar), ("Ürün Detay", urun_detay), ("Cari Detay", cari_detay), ("Aylık Detay", aylik_detay)]
    return [{"name": f"ReportSection{i + 1:02d}", "displayName": ad, "displayOption": 1, "width": 1280, "height": 720,
             "ordinal": i, "config": "{}", "filters": "[]", "visualContainers": gorseller} for i, (ad, gorseller) in enumerate(sayfalar)]


def _rapor() -> dict:
    return {"config": json.dumps({"version": "5.37", "themeCollection": {}, "activeSectionIndex": 0}),
            "layoutOptimization": 0, "sections": _sayfalar()}


# ---------------------------------------------------------------------------- dışa aktarım
def _csv_yaz(klasor, dosya, df, kolonlar):
    """Hazır analiz tablosunu sabit kolonlarla yazar; analiz yoksa (None) sadece başlık satırı olan boş dosya."""
    kaynak = [c for _, c, _, _ in kolonlar]
    df = pd.DataFrame(columns=kaynak) if df is None else df.copy()
    for c in kaynak:
        if c not in df.columns:
            df[c] = None
    df.to_csv(os.path.join(klasor, dosya), index=False, encoding="utf-8-sig")


def pbit_olustur(a: Analiz, klasor: str, olu_stok_gun: int = 90, veri_yolu: str | None = None) -> str:
    """`klasor` içine (Power BI paketinin CSV'lerinin yanına) hazır raporu yazar. Model CSV'leri bu klasörden okur.
    veri_yolu: CSV'lerin Power BI'ın çalışacağı bilgisayardaki yolu `klasor`den farklıysa (varsayılan: klasor)."""
    os.makedirs(klasor, exist_ok=True)
    hazir = {"Alacak": a.cari_yaslandirma("alacak"), "Müşteri Kaybı": a.musteri_kaybi(),
             "Sipariş Önerisi": a.siparis_onerisi(), "Ölü Stok": a.olu_stok(olu_stok_gun)}
    for tablo, df in hazir.items():
        _csv_yaz(klasor, T[tablo][0], df, T[tablo][1])
    for tablo in ("Temsilci", "Ambar"):   # eski sürümle oluşturulmuş analizlerde bu dosyalar yoktur
        if not os.path.exists(os.path.join(klasor, T[tablo][0])):
            _csv_yaz(klasor, T[tablo][0], None, T[tablo][1])
    u = lambda nesne, **k: json.dumps(nesne, ensure_ascii=False, **k).encode("utf-16-le")
    turler = ('<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="json" ContentType="" /><Override PartName="/Version" ContentType="" />'
              '<Override PartName="/DataModelSchema" ContentType="" /><Override PartName="/Report/Layout" ContentType="" />'
              '<Override PartName="/Settings" ContentType="application/json" />'
              '<Override PartName="/Metadata" ContentType="application/json" /></Types>')
    yol = os.path.join(klasor, RAPOR_ADI)
    with zipfile.ZipFile(yol, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Version", "1.28".encode("utf-16-le"))
        z.writestr("[Content_Types].xml", ("\ufeff" + turler).encode("utf-8"))
        z.writestr("DataModelSchema", u(_model(veri_yolu or os.path.abspath(klasor)), indent=2))
        z.writestr("Report/Layout", u(_rapor()))
        z.writestr("Settings", u({"Version": 4, "ReportSettings": {}, "QueriesSettings": {
            "TypeDetectionEnabled": False, "RelationshipImportEnabled": False, "Version": "2.159.1231.0"}}))
        z.writestr("Metadata", u({"Version": 5, "AutoCreatedRelationships": [], "CreatedFrom": "Cloud", "CreatedFromRelease": "2026.10"}))
    return yol
