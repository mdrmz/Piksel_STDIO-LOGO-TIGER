"""
Analizler — aktarım sonrası oluşan analiz deposu (SQLite) üzerinde çalışır.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class AbcEsik:
    a: float = 0.80   # kümülatif payın %80'ine kadar olanlar A
    b: float = 0.95   # %95'e kadar B, gerisi C


def abc(df: pd.DataFrame, anahtar: str, deger: str, esik: AbcEsik | None = None) -> pd.DataFrame:
    """Değere göre büyükten küçüğe sıralar, kümülatif payı hesaplar ve A/B/C sınıfı atar.
    Sınır kuralı: bir kalem, kendisinden ÖNCEKİ kümülatif pay eşiğin altındaysa o sınıfa girer
    (böylece %80'i aşan ilk kalem de A'da kalır; tek kalemin tüm ciroyu yaptığı durumda bile A doğru çıkar)."""
    esik = esik or AbcEsik()
    g = df.groupby(anahtar, as_index=False)[deger].sum()
    g = g[g[deger] > 0].sort_values(deger, ascending=False, kind="mergesort").reset_index(drop=True)
    toplam = g[deger].sum()
    g["pay"] = g[deger] / toplam if toplam else 0.0
    g["kumulatif_pay"] = g["pay"].cumsum()
    onceki = g["kumulatif_pay"] - g["pay"]
    g["sinif"] = np.where(onceki < esik.a, "A", np.where(onceki < esik.b, "B", "C"))
    g.insert(0, "sira", np.arange(1, len(g) + 1))
    return g


def abc_ozet(t: pd.DataFrame, deger: str) -> pd.DataFrame:
    o = t.groupby("sinif").agg(kalem_sayisi=("sira", "count"), toplam=(deger, "sum")).reindex(["A", "B", "C"]).fillna(0)
    o["kalem_payi"] = o["kalem_sayisi"] / max(1, o["kalem_sayisi"].sum())
    o["deger_payi"] = o["toplam"] / max(1e-9, o["toplam"].sum())
    return o.reset_index()


class Analiz:
    def __init__(self, depo: str):
        self.con = sqlite3.connect(depo)
        q = lambda s: pd.read_sql_query(s, self.con)
        self.meta = dict(q("SELECT anahtar, deger FROM meta").values)
        self.cari = q("SELECT * FROM dim_cari")
        self.malzeme = q("SELECT * FROM dim_malzeme")
        self.satis = q("SELECT * FROM fact_satis")
        self.alis = q("SELECT * FROM fact_alis")
        self.stok = q("SELECT * FROM stok_bakiye")
        for d in (self.satis, self.alis):
            d["tarih"] = pd.to_datetime(d["tarih"])
            d["iade"] = d["iade"].astype(bool)
            d["faturali"] = d["faturali"].astype(bool)

    # ------------------------------------------------------------------ özet
    def ozet(self) -> dict:
        s = self.satis
        brut = s.loc[~s["iade"], "net_tutar"].sum()
        iade = -s.loc[s["iade"], "net_tutar"].sum()
        fat = s.loc[~s["iade"]].groupby("fatura_no")["net_tutar"].sum()
        return {
            "net_satis": float(s["net_tutar"].sum()),
            "brut_satis": float(brut),
            "iade_tutar": float(iade),
            "iade_orani": float(iade / brut) if brut else 0.0,
            "fatura_sayisi": int(fat.shape[0]),
            "ortalama_fatura": float(fat.mean()) if len(fat) else 0.0,
            "musteri_sayisi": int(s.loc[~s["iade"], "cari_ref"].nunique()),
            "urun_sayisi": int(s.loc[~s["iade"], "malzeme_ref"].nunique()),
            "net_alis": float(self.alis["net_tutar"].sum()),
            "tedarikci_sayisi": int(self.alis["cari_ref"].nunique()),
            "tarih_bas": self.meta.get("tarih_bas", ""),
            "tarih_bit": self.meta.get("tarih_bit", ""),
        }

    # ------------------------------------------------------------------ ABC
    def urun_abc(self, olcut="net_tutar", esik=None):
        t = abc(self.satis, "malzeme_ref", olcut, esik)
        t = t.merge(self.malzeme[["malzeme_ref", "malzeme_kod", "malzeme_ad", "grup_kod"]], on="malzeme_ref", how="left")
        miktar = self.satis.groupby("malzeme_ref")["miktar"].sum().rename("toplam_miktar")
        t = t.merge(miktar, on="malzeme_ref", how="left")
        cols = ["sira", "sinif", "malzeme_kod", "malzeme_ad", "grup_kod", olcut, "toplam_miktar", "pay", "kumulatif_pay"]
        return t[[c for c in dict.fromkeys(cols)]]

    def musteri_abc(self, esik=None):
        t = abc(self.satis, "cari_ref", "net_tutar", esik)
        t = t.merge(self.cari[["cari_ref", "cari_kod", "cari_ad", "sehir", "ozel_kod"]], on="cari_ref", how="left")
        fat = (self.satis[~self.satis["iade"]].groupby("cari_ref")
               .agg(fatura_sayisi=("fatura_no", "nunique"), son_alim=("tarih", "max")))
        t = t.merge(fat, on="cari_ref", how="left")
        t["son_alim"] = t["son_alim"].dt.date
        return t[["sira", "sinif", "cari_kod", "cari_ad", "sehir", "ozel_kod", "net_tutar", "fatura_sayisi",
                  "son_alim", "pay", "kumulatif_pay"]]

    def tedarikci_abc(self, esik=None):
        t = abc(self.alis, "cari_ref", "net_tutar", esik)
        t = t.merge(self.cari[["cari_ref", "cari_kod", "cari_ad", "sehir"]], on="cari_ref", how="left")
        return t[["sira", "sinif", "cari_kod", "cari_ad", "sehir", "net_tutar", "pay", "kumulatif_pay"]]

    # ------------------------------------------------------------------ trend
    def aylik_trend(self):
        s = self.satis.assign(yil_ay=self.satis["tarih"].dt.strftime("%Y-%m"))
        a = self.alis.assign(yil_ay=self.alis["tarih"].dt.strftime("%Y-%m"))
        t = (s.groupby("yil_ay").agg(net_satis=("net_tutar", "sum"),
                                      fatura_sayisi=("fatura_no", "nunique"),
                                      musteri_sayisi=("cari_ref", "nunique"))
             .join(a.groupby("yil_ay").agg(net_alis=("net_tutar", "sum")), how="outer").fillna(0).reset_index())
        t["gecen_yil_ayni_ay"] = t["yil_ay"].map(
            lambda ym: t.set_index("yil_ay")["net_satis"].get(f"{int(ym[:4]) - 1}{ym[4:]}", np.nan))
        t["yillik_degisim"] = t["net_satis"] / t["gecen_yil_ayni_ay"] - 1
        return t

    def donem_karsilastirma(self, seviye: str = "cari"):
        """Bu yılın başından verideki son tarihe kadar olan satışı, geçen yılın aynı dönemiyle karşılaştırır.
        seviye: "cari" veya "malzeme". Geçen yıla ait veri yoksa None döner."""
        s = self.satis
        son = s["tarih"].max()
        bu = s[(s["tarih"].dt.year == son.year) & (s["tarih"] <= son)]
        gecen_son = son - pd.DateOffset(years=1)
        gecen = s[(s["tarih"].dt.year == son.year - 1) & (s["tarih"] <= gecen_son)]
        if gecen.empty or bu.empty:
            return None
        anahtar = "cari_ref" if seviye == "cari" else "malzeme_ref"
        t = (bu.groupby(anahtar)["net_tutar"].sum().rename("bu_yil").to_frame()
             .join(gecen.groupby(anahtar)["net_tutar"].sum().rename("gecen_yil"), how="outer").fillna(0).reset_index())
        t["fark"] = t["bu_yil"] - t["gecen_yil"]
        t["degisim"] = np.where(t["gecen_yil"] > 0, t["bu_yil"] / t["gecen_yil"].where(t["gecen_yil"] > 0) - 1, np.nan)
        if seviye == "cari":
            t = t.merge(self.cari[["cari_ref", "cari_kod", "cari_ad", "sehir"]], on="cari_ref", how="left")
            cols = ["cari_kod", "cari_ad", "sehir"]
        else:
            t = t.merge(self.malzeme[["malzeme_ref", "malzeme_kod", "malzeme_ad", "grup_kod"]], on="malzeme_ref", how="left")
            cols = ["malzeme_kod", "malzeme_ad", "grup_kod"]
        t = t.sort_values("fark").reset_index(drop=True)
        t.attrs["donem"] = (f"{son.year}-01-01 – {son.date()}", f"{son.year - 1}-01-01 – {gecen_son.date()}")
        return t[cols + ["bu_yil", "gecen_yil", "fark", "degisim"]]

    def grup_satis(self):
        s = self.satis.merge(self.malzeme[["malzeme_ref", "grup_kod"]], on="malzeme_ref", how="left")
        s["grup_kod"] = s["grup_kod"].replace("", "(grupsuz)").fillna("(grupsuz)")
        g = s.groupby("grup_kod", as_index=False)["net_tutar"].sum().sort_values("net_tutar", ascending=False)
        g["pay"] = g["net_tutar"] / g["net_tutar"].sum()
        return g

    # ------------------------------------------------------------------ stok
    def olu_stok(self, gun: int = 90):
        """Elinde stok olan ama son `gun` gündür hiç satılmamış malzemeler (referans: verideki son tarih)."""
        ref_tarih = self.satis["tarih"].max()
        son_satis = self.satis[~self.satis["iade"]].groupby("malzeme_ref")["tarih"].max().rename("son_satis")
        son_alis = self.alis[~self.alis["iade"]].groupby("malzeme_ref").agg(
            son_alis=("tarih", "max"), alis_tutar=("net_tutar", "sum"), _m=("miktar", "sum"))
        son_alis["ort_birim_maliyet"] = son_alis["alis_tutar"] / son_alis["_m"].replace(0, np.nan)
        t = self.stok[self.stok["stok_miktar"] > 0.0001].merge(son_satis, on="malzeme_ref", how="left") \
            .merge(son_alis[["son_alis", "ort_birim_maliyet"]], on="malzeme_ref", how="left")
        t["satissiz_gun"] = (ref_tarih - t["son_satis"]).dt.days
        t = t[t["son_satis"].isna() | (t["satissiz_gun"] >= gun)].copy()
        t["tahmini_deger"] = t["stok_miktar"] * t["ort_birim_maliyet"]
        t = t.merge(self.malzeme[["malzeme_ref", "malzeme_kod", "malzeme_ad", "grup_kod"]], on="malzeme_ref", how="left")
        t["son_satis"] = t["son_satis"].dt.date
        t["son_alis"] = t["son_alis"].dt.date
        t = t.sort_values("tahmini_deger", ascending=False, na_position="last")
        return t[["malzeme_kod", "malzeme_ad", "grup_kod", "stok_miktar", "son_satis", "satissiz_gun",
                  "son_alis", "ort_birim_maliyet", "tahmini_deger"]]

    def stok_devir(self):
        """Malzeme bazında yıllıklandırılmış satış miktarı / mevcut stok."""
        gun = max(1, (self.satis["tarih"].max() - self.satis["tarih"].min()).days + 1)
        sm = self.satis.groupby("malzeme_ref")["miktar"].sum().rename("satis_miktar")
        t = self.stok.merge(sm, on="malzeme_ref", how="left").fillna({"satis_miktar": 0})
        t["yillik_satis"] = t["satis_miktar"] * 365 / gun
        t["devir_hizi"] = t["yillik_satis"] / t["stok_miktar"].where(t["stok_miktar"] > 0)
        t["stok_gun"] = t["stok_miktar"] / (t["yillik_satis"] / 365).replace(0, np.nan)
        t = t.merge(self.malzeme[["malzeme_ref", "malzeme_kod", "malzeme_ad"]], on="malzeme_ref", how="left")
        return t[["malzeme_kod", "malzeme_ad", "stok_miktar", "yillik_satis", "devir_hizi", "stok_gun"]]

    def veri_notlari(self):
        m = self.meta
        return [
            ("Firma", f"{m.get('firma')} {m.get('firma_ad', '')}"),
            ("Dönemler", m.get("donemler", "")),
            ("Tarih aralığı", f"{m.get('tarih_bas')} – {m.get('tarih_bit')}"),
            ("Okunan ham satır", m.get("ham_satir")),
            ("İptal edildiği için dışlanan satır", m.get("iptal_satir")),
            ("İndirim/masraf vb. malzeme dışı satır", m.get("malzeme_disi_satir")),
            ("Faturalanmamış irsaliye satırı (satışa sayılmadı)", m.get("satis_faturasiz_dislanan")),
            ("Stok bakiyesinin alındığı dönem", m.get("stok_donemi")),
            ("Tutar tanımı", "KDV hariç net (VATMATRAH: satır + dağıtılmış dip indirim sonrası)"),
            ("Miktar tanımı", "Ana birim (koli/paket satırları ana birime çevrildi)"),
        ]
