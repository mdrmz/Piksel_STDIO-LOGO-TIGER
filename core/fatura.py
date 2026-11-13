"""
Fatura listesi ve fatura detayı: Logo'daki faturalar tek tek, filtrelenebilir, Logo ile sağlanabilir.

Her fatura için Logo'nun kendi başlık toplamları (INVOICE: NETTOTAL, TOTALVAT…) ile programın satırlardan
hesapladığı KDV hariç tutar yan yana gösterilir; fark varsa nedeni satır durumlarından görülür.
Her kayıt, Logo'da bulunabilmesi için tablo adı (LG_001_01_INVOICE) ve LOGICALREF ile verilir.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field

import pandas as pd

# fatura türü grupları (filtre)
TUR_GRUP = {
    "satis": ("Satış (perakende + toptan)", (7, 8)),
    "satis_iade": ("Satış iadesi", (2, 3)),
    "alis": ("Satınalma", (1,)),
    "alis_iade": ("Satınalma iadesi", (6,)),
    "hizmet": ("Hizmet faturası", (4, 9)),
    "diger": ("Diğer (proforma, fiyat farkı…)", ()),
}
ISARET = {7: 1, 8: 1, 2: -1, 3: -1, 1: 1, 6: -1}   # iadeler eksi
SATIS_TR, ALIS_TR = (7, 8, 2, 3), (1, 6)
FARK_ESIK = 1.0   # TL: bu kadarın altı yuvarlama sayılır
SIRALANABILIR = {"tarih", "fatura_no", "tur", "cari_ad", "ambar_ad", "analiz_tutar", "logo_matrah", "logo_net", "fark"}


def var_mi(con) -> bool:
    return con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                       "AND name IN ('dim_fatura', 'fatura_satir')").fetchone()[0] == 2


def _kucuk(s: pd.Series) -> pd.Series:
    """Türkçe büyük/küçük harf duyarsız arama için."""
    return (s.fillna("").astype(str).str.replace("I", "ı").str.replace("İ", "i").str.lower())


def _ara(metin: str) -> str:
    return metin.replace("I", "ı").replace("İ", "i").lower().strip()


@dataclass
class Veri:
    faturalar: pd.DataFrame
    satirlar: pd.DataFrame
    malzeme: pd.DataFrame
    ambarlar: list = field(default_factory=list)


def yukle(con) -> Veri:
    """Analiz deposundaki faturaları satır özetleri, cari ve ambar adlarıyla birlikte okur."""
    q = lambda s: pd.read_sql_query(s, con)  # noqa: E731
    f = q("SELECT * FROM dim_fatura")
    s = q("SELECT donem, fatura_ref, satir_ref, satir_tipi, trcode, malzeme_ref, miktar, birim_fiyat, net_tutar, "
          "ambar, durum FROM fatura_satir")
    cari = q("SELECT cari_ref, cari_kod, cari_ad FROM dim_cari")
    mal = q("SELECT malzeme_ref, malzeme_kod, malzeme_ad, grup_kod FROM dim_malzeme")
    try:
        ambar = q("SELECT ambar, ambar_ad FROM dim_ambar")
    except Exception:  # çok eski analiz: ambar adları yok
        ambar = pd.DataFrame({"ambar": pd.Series(dtype=int), "ambar_ad": pd.Series(dtype=str)})
    f["tarih"] = pd.to_datetime(f["tarih"], errors="coerce")
    f["iptal"] = f["iptal"].astype(bool)
    for c in ("logo_brut", "logo_indirim", "logo_kdv", "logo_net"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    s["net_tutar"] = pd.to_numeric(s["net_tutar"], errors="coerce").fillna(0.0)
    s["miktar"] = pd.to_numeric(s["miktar"], errors="coerce").fillna(0.0)

    analizde = s["durum"] == "Analizde"
    kontrol = (s["durum"] != "İptal") & ~s["satir_tipi"].isin(["İndirim", "Masraf"])
    oz = (s.assign(_a=s["net_tutar"].where(analizde, 0.0), _k=s["net_tutar"].where(kontrol, 0.0),
                   _m=s["miktar"].where(analizde, 0.0), _d=(~analizde).astype(int))
          .groupby(["donem", "fatura_ref"], as_index=False)
          .agg(satir_say=("satir_ref", "size"), analiz_tutar=("_a", "sum"), kontrol_tutar=("_k", "sum"),
               miktar=("_m", "sum"), disarida_satir=("_d", "sum")))
    f = f.merge(oz, on=["donem", "fatura_ref"], how="left")
    for c in ("satir_say", "disarida_satir"):
        f[c] = f[c].fillna(0).astype(int)
    for c in ("analiz_tutar", "kontrol_tutar", "miktar"):
        f[c] = f[c].fillna(0.0)
    f = f.merge(cari, on="cari_ref", how="left").merge(ambar, on="ambar", how="left")
    f["cari_kod"], f["cari_ad"] = f["cari_kod"].fillna(""), f["cari_ad"].fillna("")
    f["ambar_ad"] = f["ambar_ad"].fillna(f["ambar"].map(lambda n: f"Ambar {n}"))
    f["logo_matrah"] = f["logo_net"] - f["logo_kdv"]
    f["fark"] = (f["logo_matrah"] - f["kontrol_tutar"]).where(~f["iptal"] & f["logo_net"].notna(), 0.0).round(2)
    f["isaret"] = f["trcode"].map(ISARET).fillna(0).astype(int)
    f["logo_tablo"] = "LG_" + f["firma"].astype(str) + "_" + f["logo_donem"].astype(str) + "_INVOICE"
    f["anahtar"] = f["donem"].astype(str) + ":" + f["fatura_ref"].astype(str)
    # hesaplama dışı bırakılanlar (core/haric.py): listede görünür, toplamlara ve analize girmez
    from . import haric as _haric
    hc, hf = _haric.anahtarlar(con)
    f["haric_neden"] = ""
    f.loc[f["cari_kod"].isin(hc) & (f["cari_kod"] != ""), "haric_neden"] = "Cari hariç tutuldu"
    f.loc[f["anahtar"].isin(hf), "haric_neden"] = "Fatura hariç tutuldu"
    f["haric"] = f["haric_neden"] != ""
    hs = (s["donem"].astype(str) + ":" + s["fatura_ref"].astype(str)).isin(set(f.loc[f["haric"], "anahtar"]))
    s.loc[hs & (s["durum"] == "Analizde"), "durum"] = "Hariç tutuldu"
    s = s.merge(mal, on="malzeme_ref", how="left")
    s["net_birim_fiyat"] = (s["net_tutar"] / s["miktar"].where(s["miktar"] != 0)).round(4)
    return Veri(f, s, mal, ambarlar=ambar.sort_values("ambar").to_dict("records"))


_ONBELLEK: dict = {}


def veri(depo: str) -> Veri | None:
    """Analiz başına önbellekli (sayfa her filtrelendiğinde depo yeniden okunmasın)."""
    anahtar = (depo, os.path.getmtime(depo))
    if anahtar not in _ONBELLEK:
        con = sqlite3.connect(depo)
        try:
            if not var_mi(con):
                return None
            _ONBELLEK.clear()   # tek kullanıcılı yerel uygulama: yalnızca son açılan analiz tutulur
            _ONBELLEK[anahtar] = yukle(con)
        finally:
            con.close()
    return _ONBELLEK[anahtar]


@dataclass
class Filtre:
    bas: str = ""            # YYYY-MM-DD
    bit: str = ""
    tur: list = field(default_factory=list)   # TUR_GRUP anahtarları; boş = hepsi
    cari: str = ""           # kod ya da ad içinde arama
    cari_kod: str = ""       # tam kod (ABC'den gelir)
    urun: str = ""           # faturada geçen ürün (kod ya da ad içinde arama)
    urun_kod: str = ""       # tam kod (ABC'den gelir)
    ambar: int | None = None
    no: str = ""             # fatura no / belge no içinde arama
    durum: str = "tum"       # tum | gecerli | iptal | fark | disarida | haric
    sirala: str = "tarih"
    yon: str = "azalan"

    @classmethod
    def istekten(cls, args) -> "Filtre":
        amb = args.get("ambar", "")
        sirala = args.get("sirala", "tarih")
        return cls(bas=args.get("bas", "").strip(), bit=args.get("bit", "").strip(),
                   tur=[t for t in args.getlist("tur") if t in TUR_GRUP], cari=args.get("cari", "").strip(),
                   cari_kod=args.get("cari_kod", "").strip(), urun=args.get("urun", "").strip(),
                   urun_kod=args.get("urun_kod", "").strip(),
                   ambar=int(amb) if amb.lstrip("-").isdigit() else None, no=args.get("no", "").strip(),
                   durum=args.get("durum", "tum") if args.get("durum") in ("tum", "gecerli", "iptal", "fark", "disarida", "haric") else "tum",
                   sirala=sirala if sirala in SIRALANABILIR else "tarih",
                   yon="artan" if args.get("yon") == "artan" else "azalan")

    def aktif_mi(self) -> bool:
        return any([self.bas, self.bit, self.tur, self.cari, self.cari_kod, self.urun, self.urun_kod,
                    self.ambar is not None, self.no, self.durum != "tum"])


def _urun_satirlari(v: Veri, fl: Filtre) -> pd.DataFrame | None:
    """Ürün filtresine uyan fatura satırları (filtre yoksa None)."""
    if not (fl.urun or fl.urun_kod):
        return None
    s = v.satirlar
    m = pd.Series(True, index=s.index)
    if fl.urun_kod:
        m &= s["malzeme_kod"].fillna("") == fl.urun_kod
    if fl.urun:
        a = _ara(fl.urun)
        m &= _kucuk(s["malzeme_kod"]).str.contains(a, regex=False) | _kucuk(s["malzeme_ad"]).str.contains(a, regex=False)
    if fl.ambar is not None:
        m &= s["ambar"] == fl.ambar
    return s[m]


def suz(v: Veri, fl: Filtre) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Filtreye uyan faturalar (sıralı) ve ürün filtresi varsa eşleşen satırlar."""
    f = v.faturalar
    m = pd.Series(True, index=f.index)
    if fl.bas:
        m &= f["tarih"] >= pd.Timestamp(fl.bas)
    if fl.bit:
        m &= f["tarih"] < pd.Timestamp(fl.bit) + pd.Timedelta(days=1)
    if fl.tur:
        bilinen = {t for _, (_, ts) in TUR_GRUP.items() for t in ts}
        secili = {t for k in fl.tur for t in TUR_GRUP[k][1]}
        mt = f["trcode"].isin(secili)
        if "diger" in fl.tur:
            mt |= ~f["trcode"].isin(bilinen)
        m &= mt
    if fl.cari_kod:
        m &= f["cari_kod"] == fl.cari_kod
    if fl.cari:
        a = _ara(fl.cari)
        m &= _kucuk(f["cari_kod"]).str.contains(a, regex=False) | _kucuk(f["cari_ad"]).str.contains(a, regex=False)
    if fl.no:
        a = _ara(fl.no)
        m &= _kucuk(f["fatura_no"]).str.contains(a, regex=False) | _kucuk(f["belge_no"]).str.contains(a, regex=False)
    urun_s = _urun_satirlari(v, fl)
    if urun_s is not None:
        m &= f["anahtar"].isin(set(urun_s["donem"].astype(str) + ":" + urun_s["fatura_ref"].astype(str)))
    if fl.ambar is not None:
        satirda = v.satirlar.loc[v.satirlar["ambar"] == fl.ambar]
        m &= (f["ambar"] == fl.ambar) | f["anahtar"].isin(set(satirda["donem"].astype(str) + ":" + satirda["fatura_ref"].astype(str)))
    if fl.durum == "gecerli":
        m &= ~f["iptal"]
    elif fl.durum == "iptal":
        m &= f["iptal"]
    elif fl.durum == "fark":
        m &= f["fark"].abs() > FARK_ESIK
    elif fl.durum == "disarida":
        m &= f["disarida_satir"] > 0
    elif fl.durum == "haric":
        m &= f["haric"]
    sonuc = f[m].sort_values([fl.sirala, "fatura_ref"], ascending=fl.yon == "artan", kind="mergesort")
    if urun_s is not None:
        urun_s = urun_s[(urun_s["donem"].astype(str) + ":" + urun_s["fatura_ref"].astype(str)).isin(set(sonuc["anahtar"]))]
        a = urun_s[urun_s["durum"] == "Analizde"]
        ut = a.groupby(["donem", "fatura_ref"], as_index=False).agg(urun_tutar=("net_tutar", "sum"), urun_miktar=("miktar", "sum"))
        sonuc = sonuc.merge(ut, on=["donem", "fatura_ref"], how="left")
        sonuc[["urun_tutar", "urun_miktar"]] = sonuc[["urun_tutar", "urun_miktar"]].fillna(0.0)
    return sonuc, urun_s


def ozet(f: pd.DataFrame, urun_s: pd.DataFrame | None) -> dict:
    """Filtreye uyan faturaların toplamları (iptaller hariç). Tutarlar KDV hariç, iadeler eksi."""
    g = f[~f["iptal"] & ~f["haric"]]
    tutar = g["analiz_tutar"] * g["isaret"]
    o = {
        "fatura": int(len(f)), "iptal": int(f["iptal"].sum()), "haric": int((f["haric"] & ~f["iptal"]).sum()), "fark": int((f["fark"].abs() > FARK_ESIK).sum()),
        "satis_net": float(tutar[g["trcode"].isin(SATIS_TR)].sum()),
        "satis_brut": float(g.loc[g["trcode"].isin((7, 8)), "analiz_tutar"].sum()),
        "satis_iade": float(g.loc[g["trcode"].isin((2, 3)), "analiz_tutar"].sum()),
        "alis_net": float(tutar[g["trcode"].isin(ALIS_TR)].sum()),
        "logo_net": float((g["logo_net"].fillna(0) * g["isaret"].where(g["isaret"] != 0, 1)).sum()),
        "urun": None,
    }
    if urun_s is not None:
        a = urun_s[urun_s["durum"] == "Analizde"]
        isaret = a["trcode"].map(ISARET).fillna(0)
        sat = a["trcode"].isin(SATIS_TR)
        o["urun"] = {"satis_net": float((a["net_tutar"] * isaret)[sat].sum()),
                     "satis_miktar": float((a["miktar"] * isaret)[sat].sum()),
                     "alis_net": float((a["net_tutar"] * isaret)[a["trcode"].isin(ALIS_TR)].sum()),
                     "satir": int(len(a)), "urunler": sorted(set(urun_s["malzeme_kod"].dropna()))[:5]}
    return o


def detay(v: Veri, donem: str, ref: int) -> tuple[dict, pd.DataFrame] | None:
    f = v.faturalar[(v.faturalar["donem"].astype(str) == donem) & (v.faturalar["fatura_ref"] == ref)]
    if f.empty:
        return None
    s = v.satirlar[(v.satirlar["donem"].astype(str) == donem) & (v.satirlar["fatura_ref"] == ref)].sort_values("satir_ref")
    return f.iloc[0].to_dict(), s


# ---------------------------------------------------------------------------- Excel
FATURA_KOLON = [
    ("tarih", "Tarih"), ("fatura_no", "Fatura no"), ("belge_no", "Belge no"), ("tur", "Fatura türü"),
    ("cari_kod", "Cari kodu"), ("cari_ad", "Cari adı"), ("ambar_ad", "Ambar"), ("satir_say", "Satır"),
    ("miktar", "Miktar"), ("analiz_tutar", "KDV hariç tutar (analiz, TL)"),
    ("logo_matrah", "Logo KDV hariç (NETTOTAL-TOTALVAT)"), ("logo_kdv", "Logo KDV"),
    ("logo_net", "Logo genel toplam (NETTOTAL)"), ("fark", "Fark (TL)"), ("disarida_satir", "Analiz dışı satır"),
    ("iptal", "İptal"), ("haric_neden", "Hesaplama dışı"), ("aciklama", "Açıklama"), ("logo_tablo", "Logo tablosu"), ("fatura_ref", "LOGICALREF"),
]
SATIR_KOLON = [
    ("tarih", "Tarih"), ("fatura_no", "Fatura no"), ("tur", "Fatura türü"), ("cari_kod", "Cari kodu"),
    ("cari_ad", "Cari adı"), ("malzeme_kod", "Malzeme kodu"), ("malzeme_ad", "Malzeme adı"), ("grup_kod", "Grup"),
    ("satir_tipi", "Satır tipi"), ("miktar", "Miktar (ana birim)"), ("birim_fiyat", "Birim fiyat (Logo PRICE)"),
    ("net_birim_fiyat", "Net birim fiyat (indirimli, KDV hariç)"),
    ("net_tutar", "KDV hariç tutar (TL)"), ("durum", "Durum"), ("satir_tablo", "Logo tablosu"),
    ("satir_ref", "LOGICALREF"), ("fatura_ref", "Fatura LOGICALREF"),
]
PARA_K = {"analiz_tutar", "logo_matrah", "logo_kdv", "logo_net", "fark", "birim_fiyat", "net_birim_fiyat", "net_tutar",
          "urun_tutar"}
EXCEL_SATIR_SINIR = 1_000_000


def satirlar_faturayla(v: Veri, f: pd.DataFrame) -> pd.DataFrame:
    s = v.satirlar.merge(f[["donem", "fatura_ref", "tarih", "fatura_no", "tur", "cari_kod", "cari_ad", "firma",
                            "logo_donem"]], on=["donem", "fatura_ref"], how="inner")
    s["satir_tablo"] = "LG_" + s["firma"].astype(str) + "_" + s["logo_donem"].astype(str) + "_STLINE"
    return s.sort_values(["tarih", "fatura_ref", "satir_ref"], kind="mergesort")


def excel_sayfalari(wb, v: Veri, f: pd.DataFrame, baslik_not: str = "", satir_siniri: int = EXCEL_SATIR_SINIR):
    """Faturalar ve Fatura Satırları sayfalarını (filtreli, dondurulmuş başlıklı) çalışma kitabına ekler."""
    bf = wb.add_format({"bold": True, "font_color": "white", "bg_color": "#1F3864", "border": 1, "text_wrap": True,
                        "valign": "vcenter"})
    para = wb.add_format({"num_format": "#,##0.00"})
    sayi = wb.add_format({"num_format": "#,##0.##"})
    tarih = wb.add_format({"num_format": "dd.mm.yyyy"})
    kirmizi = wb.add_format({"num_format": "#,##0.00", "font_color": "#B42318", "bold": True})
    notf = wb.add_format({"italic": True, "font_color": "#666666"})

    def yaz(ad, df, kolonlar, not_):
        ws = wb.add_worksheet(ad)
        ws.write(0, 0, not_, notf)
        for j, (_, b) in enumerate(kolonlar):
            ws.write(1, j, b, bf)
        for i, row in enumerate(df[[k for k, _ in kolonlar]].itertuples(index=False), start=2):
            for j, ((k, _), x) in enumerate(zip(kolonlar, row)):
                if x is None or (isinstance(x, float) and pd.isna(x)) or x is pd.NaT:
                    continue
                if k == "tarih":
                    ws.write_datetime(i, j, pd.Timestamp(x).to_pydatetime(), tarih)
                elif k == "fark":
                    ws.write_number(i, j, float(x), kirmizi if abs(x) > FARK_ESIK else para)
                elif k in PARA_K:
                    ws.write_number(i, j, float(x), para)
                elif k in ("miktar", "satir_say", "disarida_satir", "urun_miktar"):
                    ws.write_number(i, j, float(x), sayi)
                elif k == "iptal":
                    ws.write(i, j, "İptal" if x else "")
                elif isinstance(x, (int, float)):
                    ws.write_number(i, j, x)
                else:
                    ws.write(i, j, str(x))
        ws.freeze_panes(2, 0)
        ws.autofilter(1, 0, max(1, len(df) + 1), len(kolonlar) - 1)
        for j, (k, b) in enumerate(kolonlar):
            ws.set_column(j, j, 12 if k in PARA_K or k in ("tarih", "miktar") else min(40, max(10, len(b) + 2)))
        return ws

    kolonlar = FATURA_KOLON + ([("urun_miktar", "Ürün miktarı"), ("urun_tutar", "Ürün tutarı (KDV hariç)")]
                               if "urun_tutar" in f.columns else [])
    yaz("Faturalar", f.head(satir_siniri), kolonlar,
        ((baslik_not + " " if baslik_not else "") + f"{len(f):,} fatura"
        + (f" (ilk {satir_siniri:,}; tamamı için Faturalar ekranından filtreleyip indirin)" if len(f) > satir_siniri else "")
        + ". Tutarlar KDV hariç. Fark: Logo'nun fatura "
        "başlığındaki KDV hariç tutar ile satırların toplamı arasındaki fark (indirim/masraf satırları hariç). "
        "Logo'da bulmak için: Logo tablosu + LOGICALREF.").replace(",", "."))
    s = satirlar_faturayla(v, f)
    kesik = len(s) > satir_siniri
    yaz("Fatura Satırları", s.head(satir_siniri), SATIR_KOLON,
        f"{len(s):,} satır.".replace(",", ".")
        + (f" Çok büyük olduğu için ilk {satir_siniri:,} satır; tamamı için programdaki Faturalar ekranından "
           "filtreleyip indirin.".replace(",", ".") if kesik else "")
        + " Durum 'Analizde' olmayan satırlar ciroya katılmaz (iptal, indirim/masraf satırı, transfer vb.).")


def excel_indir(v: Veri, f: pd.DataFrame, yol: str, baslik_not: str = ""):
    import xlsxwriter
    wb = xlsxwriter.Workbook(yol, {"nan_inf_to_errors": True})
    excel_sayfalari(wb, v, f, baslik_not)
    wb.close()
    return yol
