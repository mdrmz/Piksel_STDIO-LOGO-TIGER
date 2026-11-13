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


def _bir_kez(fn):
    """Aynı parametrelerle tekrar çağrılan analizleri bir kez hesaplar (panel, Excel ve PDF aynı tabloları
    birkaç kez ister). Dönen tablo paylaşılır; çağıranlar yerinde değiştirmemelidir."""
    import functools

    @functools.wraps(fn)
    def sarili(self, *a, **k):
        anahtar = (fn.__name__, a, tuple(sorted(k.items())))
        if anahtar not in self._onbellek:
            self._onbellek[anahtar] = fn(self, *a, **k)
        return self._onbellek[anahtar]
    return sarili


class Analiz:
    def __init__(self, depo: str, ambar: int | None = None):
        """ambar: verilirse satış, alış ve stok yalnızca o ambara göre süzülür (ör. sadece MERKEZ DEPO).
        Cari bakiye/alacak ambara bağlı değildir; süzülmez."""
        self._onbellek: dict = {}
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
        # v0.4 ile gelen tablolar: eski sürümle oluşturulmuş analizlerde yoktur -> boş kabul edilir
        var = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        ops = lambda t, kolonlar: q(f"SELECT * FROM {t}") if t in var else pd.DataFrame(columns=kolonlar)
        self.cari_hareket = ops("fact_cari_hareket", ["hareket_ref", "cari_ref", "tarih", "trcode", "hareket", "borc", "alacak"])
        self.cari_hareket["tarih"] = pd.to_datetime(self.cari_hareket["tarih"])
        self.temsilci = ops("dim_temsilci", ["temsilci_ref", "temsilci_kod", "temsilci_ad"])
        self.ambar = ops("dim_ambar", ["ambar", "ambar_ad"])
        self.stok_ambar = ops("stok_ambar", ["malzeme_ref", "ambar", "stok_miktar"])
        self.maliyet = ops("malzeme_maliyet", ["malzeme_ref", "son_giris_tarih", "son_birim_maliyet", "son_tedarikci_ref"])
        self.maliyet_var = "maliyet_tutar" in self.satis.columns and self.satis["maliyet_tutar"].notna().any()
        self.eski_surum = "malzeme_maliyet" not in var   # v0.4 öncesi oluşturulmuş: kârlılık/cari/depo verisi yok
        for d in (self.satis, self.alis, self.stok_ambar, self.ambar):
            d["ambar"] = pd.to_numeric(d["ambar"], errors="coerce").fillna(0).astype(int)
        self.ref_tarih = self.satis["tarih"].max()   # "bugün": verideki son satış tarihi (süzmeden önce)
        # ambar listesi (süzmeden önce): seçici için
        adlar = dict(zip(self.ambar["ambar"], self.ambar["ambar_ad"]))
        ciro = self.satis.groupby("ambar")["net_tutar"].sum()
        nolar = sorted(set(ciro.index) | set(self.stok_ambar["ambar"]), key=lambda n: (-ciro.get(n, 0.0), n))
        self.ambarlar = [{"no": int(n), "ad": adlar.get(n) or f"Ambar {n}", "net_tutar": float(ciro.get(n, 0.0))}
                         for n in nolar]
        self.ambar_secili, self.ambar_secili_ad = None, None
        if ambar is not None:
            n = int(ambar)
            self.ambar_secili = n
            self.ambar_secili_ad = adlar.get(n) or f"Ambar {n}"
            self.satis = self.satis[self.satis["ambar"] == n].reset_index(drop=True)
            self.alis = self.alis[self.alis["ambar"] == n].reset_index(drop=True)
            self.stok_ambar = self.stok_ambar[self.stok_ambar["ambar"] == n].reset_index(drop=True)
            self.stok = self.stok_ambar.groupby("malzeme_ref", as_index=False)["stok_miktar"].sum()

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
            **self._kar_ozeti(),
        }

    def _kar_ozeti(self) -> dict:
        """Maliyeti bilinen satışlar üzerinden tahmini brüt kâr. Maliyet yoksa değerler None."""
        if not self.maliyet_var:
            return {"brut_kar": None, "kar_marji": None, "maliyet_kapsama": 0.0}
        s = self.satis[self.satis["maliyet_tutar"].notna()]
        ciro = float(s["net_tutar"].sum())
        kar = float(ciro - s["maliyet_tutar"].sum())
        toplam = float(self.satis["net_tutar"].sum())
        return {"brut_kar": kar, "kar_marji": kar / ciro if ciro else None,
                "maliyet_kapsama": ciro / toplam if toplam else 0.0}

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

    # ------------------------------------------------------------------ kârlılık
    @_bir_kez
    def karlilik(self, seviye: str = "malzeme"):
        """Tahmini brüt kâr = net satış − (satış miktarı × o tarihteki son alış birim maliyeti).
        seviye: "malzeme", "cari" veya "grup". Maliyet bilgisi yoksa None döner.
        Maliyeti bulunamayan satırlar (hiç alışı/devri olmayan malzemeler) hesaba girmez."""
        if not self.maliyet_var:
            return None
        s = self.satis[self.satis["maliyet_tutar"].notna()]
        if seviye == "grup":
            s = s.merge(self.malzeme[["malzeme_ref", "grup_kod"]], on="malzeme_ref", how="left")
            s["grup_kod"] = s["grup_kod"].replace("", "(grupsuz)").fillna("(grupsuz)")
        anahtar = {"malzeme": "malzeme_ref", "cari": "cari_ref", "grup": "grup_kod"}[seviye]
        t = s.groupby(anahtar, as_index=False).agg(net_tutar=("net_tutar", "sum"), maliyet=("maliyet_tutar", "sum"),
                                                   toplam_miktar=("miktar", "sum"))
        t["brut_kar"] = t["net_tutar"] - t["maliyet"]
        t["kar_marji"] = t["brut_kar"] / t["net_tutar"].where(t["net_tutar"] > 0)
        toplam_kar = t["brut_kar"].sum()
        t["kar_payi"] = t["brut_kar"] / toplam_kar if toplam_kar else 0.0
        t = t.sort_values("brut_kar", ascending=False, kind="mergesort").reset_index(drop=True)
        t.insert(0, "sira", np.arange(1, len(t) + 1))
        if seviye == "malzeme":
            t = t.merge(self.malzeme[["malzeme_ref", "malzeme_kod", "malzeme_ad", "grup_kod"]], on="malzeme_ref", how="left")
            cols = ["sira", "malzeme_kod", "malzeme_ad", "grup_kod", "toplam_miktar"]
        elif seviye == "cari":
            t = t.merge(self.cari[["cari_ref", "cari_kod", "cari_ad", "sehir"]], on="cari_ref", how="left")
            cols = ["sira", "cari_kod", "cari_ad", "sehir"]
        else:
            cols = ["sira", "grup_kod"]
        return t[cols + ["net_tutar", "maliyet", "brut_kar", "kar_marji", "kar_payi"]]

    # ------------------------------------------------------------------ cari bakiye ve yaşlandırma
    YAS_DILIM = [(0, 30, "g0_30"), (31, 60, "g31_60"), (61, 90, "g61_90"), (91, 180, "g91_180"), (181, 10 ** 6, "g180p")]

    @_bir_kez
    def cari_yaslandirma(self, yon: str = "alacak"):
        """Açık cari bakiyelerin yaşlandırması (FIFO): ödemeler en eski borçtan başlayarak düşülür,
        kalan açık tutarlar hareket tarihine göre 0-30 / 31-60 / 61-90 / 91-180 / 180+ gün dilimlerine dağıtılır.
        yon="alacak": bize borçlu cariler (alacaklarımız) · yon="borc": borçlu olduğumuz cariler.
        Cari hareket tablosu (CLFLINE) yedekte yoksa None döner.
        Not: dönem başı devir (açılış) fişi önceki bakiyeyi tek satırda taşır; o tutar dönem başı tarihiyle yaşlanır."""
        h = self.cari_hareket
        if h.empty:
            return None
        h = h[h["tarih"].notna()]
        ref = max(h["tarih"].max(), self.ref_tarih) if pd.notna(self.ref_tarih) else h["tarih"].max()
        # alacak yönünde "açan" taraf borç, "kapatan" taraf alacak kolonudur; borç yönünde tersi
        acan, kapatan = ("borc", "alacak") if yon == "alacak" else ("alacak", "borc")
        top = h.groupby("cari_ref").agg(_acan=(acan, "sum"), _kapatan=(kapatan, "sum"))
        top["bakiye"] = top["_acan"] - top["_kapatan"]
        top = top[top["bakiye"] > 0.005]
        if top.empty:
            return pd.DataFrame(columns=["cari_kod", "cari_ad", "sehir", "bakiye"] + [d[2] for d in self.YAS_DILIM]
                                + ["en_eski_gun", "gecikmis_pay"])
        a = h[h["cari_ref"].isin(top.index) & (h[acan] > 0)].sort_values(["cari_ref", "tarih", "hareket_ref"], kind="mergesort")
        a = a.assign(_kum=a.groupby("cari_ref")[acan].cumsum())
        a["_kapatan"] = a["cari_ref"].map(top["_kapatan"])
        a["acik"] = (a["_kum"] - a["_kapatan"]).clip(lower=0).clip(upper=a[acan])
        a = a[a["acik"] > 0.005].copy()
        a["gun"] = (ref - a["tarih"]).dt.days.clip(lower=0)
        for alt, ust, ad in self.YAS_DILIM:
            a[ad] = np.where(a["gun"].between(alt, ust), a["acik"], 0.0)
        t = a.groupby("cari_ref").agg(**{d[2]: (d[2], "sum") for d in self.YAS_DILIM}, en_eski_gun=("gun", "max"))
        t = t.join(top["bakiye"]).reset_index()
        t["gecikmis_pay"] = (t["g61_90"] + t["g91_180"] + t["g180p"]) / t["bakiye"]
        t = t.merge(self.cari[["cari_ref", "cari_kod", "cari_ad", "sehir"]], on="cari_ref", how="left")
        t = t.sort_values("bakiye", ascending=False, kind="mergesort").reset_index(drop=True)
        t.attrs["ref_tarih"] = str(ref.date())
        return t[["cari_kod", "cari_ad", "sehir", "bakiye"] + [d[2] for d in self.YAS_DILIM] + ["en_eski_gun", "gecikmis_pay"]]

    @_bir_kez
    def cari_ozet(self):
        """Toplam alacak/borç, dilim toplamları ve ortalama tahsilat süresi (gün). CLFLINE yoksa None."""
        al = self.cari_yaslandirma("alacak")
        if al is None:
            return None
        bo = self.cari_yaslandirma("borc")
        h = self.cari_hareket[self.cari_hareket["tarih"].notna()]
        # ortalama tahsilat süresi = alacak bakiyesi / günlük ortalama faturalama (devir hariç, KDV dahil borç kayıtları)
        musteri = set(self.satis["cari_ref"].unique())
        f = h[(h["trcode"] != 14) & h["cari_ref"].isin(musteri)]
        gun = max(1, (h["tarih"].max() - h.loc[h["trcode"] != 14, "tarih"].min()).days + 1) if len(f) else 1
        gunluk = f["borc"].sum() / gun
        toplam_al = float(al["bakiye"].sum())
        return {
            "alacak": toplam_al, "alacak_cari": int(len(al)),
            "borc": float(bo["bakiye"].sum()), "borc_cari": int(len(bo)),
            "dilim": {d[2]: float(al[d[2]].sum()) for d in self.YAS_DILIM},
            "gecikmis": float(al[["g61_90", "g91_180", "g180p"]].sum().sum()),
            "tahsilat_gun": float(toplam_al / gunluk) if gunluk > 0 else None,
            "ref_tarih": al.attrs.get("ref_tarih", ""), "donem": self.meta.get("cari_hareket_donemi", ""),
        }

    # ------------------------------------------------------------------ satış temsilcisi
    @_bir_kez
    def temsilci_performans(self):
        """Satış elemanı bazında satış, iade, müşteri ve tahmini kâr. Veride temsilci bilgisi yoksa None."""
        s = self.satis
        if "satis_temsilci_ref" not in s.columns or s["satis_temsilci_ref"].fillna(0).astype(int).nunique() < 2:
            return None
        s = s.assign(temsilci_ref=s["satis_temsilci_ref"].fillna(0).astype(int),
                     _iade=np.where(s["iade"], -s["net_tutar"], 0.0),
                     _mal=s["maliyet_tutar"] if "maliyet_tutar" in s.columns else np.nan)
        t = s.groupby("temsilci_ref", as_index=False).agg(
            net_tutar=("net_tutar", "sum"), iade_tutar=("_iade", "sum"), musteri_sayisi=("cari_ref", "nunique"),
            maliyet=("_mal", lambda x: x.sum(min_count=1)))
        fat = s[~s["iade"]].groupby("temsilci_ref")["fatura_no"].nunique().rename("fatura_sayisi")
        t = t.merge(fat, on="temsilci_ref", how="left").fillna({"fatura_sayisi": 0})
        t["ortalama_fatura"] = (t["net_tutar"] + t["iade_tutar"]) / t["fatura_sayisi"].where(t["fatura_sayisi"] > 0)
        t["iade_orani"] = t["iade_tutar"] / (t["net_tutar"] + t["iade_tutar"]).where((t["net_tutar"] + t["iade_tutar"]) > 0)
        t["pay"] = t["net_tutar"] / t["net_tutar"].sum()
        kms = s[s["_mal"].notna()].groupby("temsilci_ref")["net_tutar"].sum().rename("_km")
        t = t.merge(kms, on="temsilci_ref", how="left")
        t["brut_kar"] = t["_km"] - t["maliyet"]
        t["kar_marji"] = t["brut_kar"] / t["_km"].where(t["_km"] > 0)
        t = t.merge(self.temsilci, on="temsilci_ref", how="left")
        t["temsilci_ad"] = np.where(t["temsilci_ref"] == 0, "(temsilci atanmamış)",
                                    t["temsilci_ad"].fillna("").replace("", np.nan).fillna("Temsilci " + t["temsilci_ref"].astype(str)))
        t["temsilci_kod"] = t["temsilci_kod"].fillna("")
        t = t.sort_values("net_tutar", ascending=False, kind="mergesort").reset_index(drop=True)
        return t[["temsilci_kod", "temsilci_ad", "net_tutar", "pay", "fatura_sayisi", "ortalama_fatura", "musteri_sayisi",
                  "iade_tutar", "iade_orani", "brut_kar", "kar_marji"]]

    # ------------------------------------------------------------------ müşteri kaybı
    @_bir_kez
    def musteri_kaybi(self, en_az_alim: int = 3):
        """Düzenli alırken alımı kesilen/seyrekleşen müşteriler.
        Bir müşterinin kendi ortalama alım aralığına bakılır (en az `en_az_alim` ayrı alım günü olanlar):
          Kayıp: son alımından bu yana, ortalama aralığının 3 katından (en az 60 gün) uzun süre geçmiş
          Risk : 2 katından (en az 30 gün) uzun süre geçmiş
        Referans tarih verideki son satış tarihidir."""
        s = self.satis[~self.satis["iade"] & self.satis["tarih"].notna()]
        g = s.assign(gun=s["tarih"].dt.normalize()).groupby("cari_ref").agg(
            alim_sayisi=("gun", "nunique"), ilk_alim=("gun", "min"), son_alim=("gun", "max"))
        g = g[g["alim_sayisi"] >= en_az_alim].copy()
        g["ort_aralik_gun"] = (g["son_alim"] - g["ilk_alim"]).dt.days / (g["alim_sayisi"] - 1)
        g["sessiz_gun"] = (self.ref_tarih.normalize() - g["son_alim"]).dt.days
        kayip = g["sessiz_gun"] >= np.maximum(60, 3 * g["ort_aralik_gun"])
        risk = g["sessiz_gun"] >= np.maximum(30, 2 * g["ort_aralik_gun"])
        g["durum"] = np.where(kayip, "Kayıp", np.where(risk, "Risk", ""))
        g = g[g["durum"] != ""].reset_index()
        ciro = self.satis.groupby("cari_ref")["net_tutar"].sum().rename("net_tutar")
        son12 = self.satis[self.satis["tarih"] > self.ref_tarih - pd.DateOffset(years=1)].groupby("cari_ref")["net_tutar"].sum().rename("son_12_ay")
        g = g.merge(ciro, on="cari_ref", how="left").merge(son12, on="cari_ref", how="left").fillna({"son_12_ay": 0.0})
        g = g.merge(self.cari[["cari_ref", "cari_kod", "cari_ad", "sehir"]], on="cari_ref", how="left")
        g["son_alim"] = g["son_alim"].dt.date
        g["_s"] = (g["durum"] != "Kayıp").astype(int)
        g = g.sort_values(["_s", "net_tutar"], ascending=[True, False], kind="mergesort").reset_index(drop=True)
        return g[["durum", "cari_kod", "cari_ad", "sehir", "alim_sayisi", "ort_aralik_gun", "son_alim", "sessiz_gun",
                  "net_tutar", "son_12_ay"]]

    # ------------------------------------------------------------------ sipariş önerisi
    @_bir_kez
    def siparis_onerisi(self, tedarik_gun: int = 15, hedef_gun: int = 45, pencere_gun: int = 90):
        """Stoğu, tedarik süresinden (`tedarik_gun`) kısa sürede bitecek malzemeler için sipariş miktarı önerir.
        Günlük satış hızı son `pencere_gun` günün satışından hesaplanır; öneri stoğu `hedef_gun` günlük
        ihtiyaca tamamlar. Durum: "Acil" = stok bitmiş ya da 7 günden az · "Sipariş ver" = tedarik süresinden az."""
        bas = self.ref_tarih - pd.Timedelta(days=pencere_gun)
        son = self.satis[self.satis["tarih"] > bas]
        hiz = (son.groupby("malzeme_ref")["miktar"].sum() / pencere_gun).rename("gunluk_satis")
        t = hiz[hiz > 0].to_frame().join(self.stok.set_index("malzeme_ref")["stok_miktar"], how="left").fillna({"stok_miktar": 0.0})
        t["stok_gun"] = t["stok_miktar"].clip(lower=0) / t["gunluk_satis"]
        t = t[t["stok_gun"] < tedarik_gun].copy()
        t["oneri_miktar"] = np.ceil(t["gunluk_satis"] * hedef_gun - t["stok_miktar"].clip(lower=0))
        t = t[t["oneri_miktar"] > 0].reset_index()
        t["durum"] = np.where(t["stok_gun"] < 7, "Acil", "Sipariş ver")
        m = self.maliyet[["malzeme_ref", "son_birim_maliyet", "son_tedarikci_ref"]] if len(self.maliyet) else \
            pd.DataFrame(columns=["malzeme_ref", "son_birim_maliyet", "son_tedarikci_ref"])
        t = t.merge(m, on="malzeme_ref", how="left")
        t["oneri_tutar"] = t["oneri_miktar"] * pd.to_numeric(t["son_birim_maliyet"], errors="coerce")
        ted = self.cari[["cari_ref", "cari_ad"]].rename(columns={"cari_ref": "son_tedarikci_ref", "cari_ad": "son_tedarikci"})
        t = t.merge(ted, on="son_tedarikci_ref", how="left")
        t = t.merge(self.malzeme[["malzeme_ref", "malzeme_kod", "malzeme_ad", "grup_kod"]], on="malzeme_ref", how="left")
        t["_s"] = (t["durum"] != "Acil").astype(int)
        t = t.sort_values(["_s", "oneri_tutar"], ascending=[True, False], na_position="last", kind="mergesort").reset_index(drop=True)
        return t[["durum", "malzeme_kod", "malzeme_ad", "grup_kod", "stok_miktar", "gunluk_satis", "stok_gun",
                  "oneri_miktar", "son_birim_maliyet", "oneri_tutar", "son_tedarikci"]]

    # ------------------------------------------------------------------ depo (ambar) kırılımı
    @_bir_kez
    def depo_kirilim(self):
        """Ambar bazında satış (fatura, müşteri, iade, kâr) ve stok. Tek ambarla çalışan firmalarda None döner.
        Fatura sayısı: o ambardan mal çıkan fatura sayısı (bir fatura iki ambardan mal çıkarabilir; o zaman ikisinde de sayılır)."""
        s = self.satis
        ambarlar = set(s["ambar"]) | set(self.stok_ambar["ambar"])
        if len(ambarlar) < 2:
            return None
        satir = s[~s["iade"]]
        sat = s.groupby("ambar").agg(net_tutar=("net_tutar", "sum"), satis_miktar=("miktar", "sum"))
        sat = sat.join(satir.groupby("ambar").agg(
            brut_satis=("net_tutar", "sum"), fatura_sayisi=("fatura_no", "nunique"),
            musteri_sayisi=("cari_ref", "nunique"), urun_sayisi=("malzeme_ref", "nunique")), how="outer")
        sat["iade_tutar"] = -s[s["iade"]].groupby("ambar")["net_tutar"].sum()
        sat = sat.fillna({"iade_tutar": 0.0, "brut_satis": 0.0, "fatura_sayisi": 0, "musteri_sayisi": 0, "urun_sayisi": 0})
        sat["ortalama_fatura"] = np.where(sat["fatura_sayisi"] > 0, sat["brut_satis"] / sat["fatura_sayisi"].replace(0, np.nan), 0.0)
        if self.maliyet_var:
            m = s[s["maliyet_tutar"].notna()].groupby("ambar").agg(_c=("net_tutar", "sum"), _m=("maliyet_tutar", "sum"))
            sat = sat.join(m)
            sat["brut_kar"] = sat["_c"] - sat["_m"]
            sat["kar_marji"] = sat["brut_kar"] / sat["_c"].replace(0, np.nan)
            sat = sat.drop(columns=["_c", "_m"])
        else:
            sat["brut_kar"], sat["kar_marji"] = np.nan, np.nan
        sa = self.stok_ambar.copy()
        if len(self.maliyet):
            sa = sa.merge(self.maliyet[["malzeme_ref", "son_birim_maliyet"]], on="malzeme_ref", how="left")
        else:
            sa["son_birim_maliyet"] = np.nan
        sa["_deger"] = sa["stok_miktar"].clip(lower=0) * pd.to_numeric(sa["son_birim_maliyet"], errors="coerce")
        stk = sa.groupby("ambar").agg(stok_kalem=("stok_miktar", lambda x: int((x > 0.0001).sum())),
                                      stok_miktar=("stok_miktar", "sum"), stok_deger=("_deger", "sum"),
                                      negatif_kalem=("stok_miktar", lambda x: int((x < -0.0001).sum())))
        t = sat.join(stk, how="outer").reset_index()
        for c in t.columns:
            if c not in ("brut_kar", "kar_marji"):
                t[c] = t[c].fillna(0)
        for c in ("fatura_sayisi", "musteri_sayisi", "urun_sayisi", "stok_kalem", "negatif_kalem"):
            t[c] = t[c].astype(int)
        t["pay"] = t["net_tutar"] / t["net_tutar"].sum() if t["net_tutar"].sum() else 0.0
        t = t.merge(self.ambar[["ambar", "ambar_ad"]], on="ambar", how="left") if len(self.ambar) else t.assign(ambar_ad=np.nan)
        t["ambar_ad"] = t["ambar_ad"].fillna("Ambar " + t["ambar"].astype(str))
        t = t.sort_values("net_tutar", ascending=False, kind="mergesort").reset_index(drop=True)
        return t[["ambar", "ambar_ad", "net_tutar", "pay", "fatura_sayisi", "ortalama_fatura", "musteri_sayisi",
                  "urun_sayisi", "satis_miktar", "iade_tutar", "brut_kar", "kar_marji", "stok_kalem", "stok_miktar",
                  "stok_deger", "negatif_kalem"]]

    @_bir_kez
    def ambar_urun(self):
        """Ambar × ürün: hangi ambardan hangi üründen ne kadar çıktı. Ambarlar ciroya göre, ürünler ambar içinde
        ciroya göre sıralı. Tek ambarlı firmalarda None."""
        dk = self.depo_kirilim()
        if dk is None:
            return None
        s = self.satis
        t = s.groupby(["ambar", "malzeme_ref"], as_index=False).agg(net_tutar=("net_tutar", "sum"), miktar=("miktar", "sum"))
        f = s[~s["iade"]].groupby(["ambar", "malzeme_ref"]).agg(fatura_sayisi=("fatura_no", "nunique"),
                                                                 musteri_sayisi=("cari_ref", "nunique"))
        t = t.merge(f.reset_index(), on=["ambar", "malzeme_ref"], how="left").fillna({"fatura_sayisi": 0, "musteri_sayisi": 0})
        t["ambar_ici_pay"] = t["net_tutar"] / t.groupby("ambar")["net_tutar"].transform("sum").replace(0, np.nan)
        t = t.merge(self.stok_ambar, on=["ambar", "malzeme_ref"], how="left").fillna({"stok_miktar": 0.0})
        t = t.merge(self.malzeme[["malzeme_ref", "malzeme_kod", "malzeme_ad", "grup_kod"]], on="malzeme_ref", how="left")
        t = t.merge(dk[["ambar", "ambar_ad"]], on="ambar", how="left")
        sira = {n: i for i, n in enumerate(dk["ambar"])}
        t["_s"] = t["ambar"].map(sira)
        t = t.sort_values(["_s", "net_tutar"], ascending=[True, False], kind="mergesort").reset_index(drop=True)
        t["sira"] = t.groupby("ambar").cumcount() + 1
        for c in ("fatura_sayisi", "musteri_sayisi"):
            t[c] = t[c].astype(int)
        return t[["ambar", "ambar_ad", "sira", "malzeme_kod", "malzeme_ad", "grup_kod", "miktar", "net_tutar",
                  "ambar_ici_pay", "fatura_sayisi", "musteri_sayisi", "stok_miktar"]]

    @_bir_kez
    def ambar_aylik(self):
        """Aylık net satış, ambar ambar (satır: yıl-ay, kolon: ambar adı) + Toplam. Tek ambarlı firmalarda None."""
        dk = self.depo_kirilim()
        if dk is None:
            return None
        s = self.satis.assign(yil_ay=self.satis["tarih"].dt.strftime("%Y-%m"))
        p = s.pivot_table(index="yil_ay", columns="ambar", values="net_tutar", aggfunc="sum", fill_value=0.0)
        p = p.reindex(columns=[n for n in dk["ambar"] if n in p.columns])
        p.columns = [dk.set_index("ambar").loc[n, "ambar_ad"] for n in p.columns]
        p["Toplam"] = p.sum(axis=1)
        return p.reset_index()

    # ------------------------------------------------------------------ öne çıkanlar
    def one_cikanlar(self, olu_stok_gun: int = 90) -> list[tuple[str, str]]:
        """Yönetimin ilk bakması gereken noktalar: (seviye, cümle). seviye: "iyi", "bilgi", "dikkat".
        Kural tabanlıdır (yapay zekâ kullanmaz); rakamlar analizlerin kendisinden gelir."""
        tl = lambda v: f"{float(v):,.0f}".replace(",", ".")
        yz = lambda v: f"%{float(v) * 100:.1f}".replace(".", ",")
        o, out = self.ozet(), []
        k = self.donem_karsilastirma("cari")
        if k is not None:
            bu, gecen = k["bu_yil"].sum(), k["gecen_yil"].sum()
            if gecen > 0:
                d = bu / gecen - 1
                out.append(("iyi" if d >= 0 else "dikkat",
                            f"Satışlar geçen yılın aynı dönemine göre {yz(abs(d))} {'arttı' if d >= 0 else 'azaldı'} "
                            f"({tl(gecen)} ₺ → {tl(bu)} ₺)."))
        if o.get("brut_kar") is not None:
            out.append(("bilgi", f"Tahmini brüt kâr {tl(o['brut_kar'])} ₺, marj {yz(o['kar_marji'] or 0)} (son alış maliyetiyle)."))
            ku = self.karlilik("malzeme")
            zarar = ku[(ku["brut_kar"] < 0) & (ku["net_tutar"] > 0)]
            if len(zarar):
                out.append(("dikkat", f"{len(zarar)} ürün alış maliyetinin altında satılmış görünüyor "
                                      f"(toplam {tl(-zarar['brut_kar'].sum())} ₺ zarar); fiyatlarını kontrol edin."))
        m = self.musteri_abc()
        if len(m) >= 5 and o["net_satis"] > 0:
            p5 = m.head(5)["net_tutar"].sum() / m["net_tutar"].sum()
            if p5 >= 0.4:
                out.append(("dikkat", f"Satışların {yz(p5)} kadarı yalnızca 5 müşteriden geliyor; müşteri yoğunlaşma riski var."))
        co = self.cari_ozet()
        if co and co["alacak"] > 0:
            cumle = f"Müşterilerden {tl(co['alacak'])} ₺ alacak var"
            if co["tahsilat_gun"] is not None:
                cumle += f"; ortalama tahsilat süresi {co['tahsilat_gun']:.0f} gün"
            out.append(("bilgi", cumle + "."))
            if co["gecikmis"] / co["alacak"] >= 0.05:
                out.append(("dikkat", f"Alacakların {tl(co['gecikmis'])} ₺ tutarındaki kısmı ({yz(co['gecikmis'] / co['alacak'])}) 60 günü aşmış."))
        mk = self.musteri_kaybi()
        kayip = mk[mk["durum"] == "Kayıp"]
        if len(mk):
            out.append(("dikkat", f"{len(kayip)} müşteri alımı kesmiş, {len(mk) - len(kayip)} müşteri seyrekleşmiş görünüyor "
                                  f"(son 12 ayda bu müşterilerden {tl(mk['son_12_ay'].sum())} ₺ satış yapılmıştı)."))
        olu = self.olu_stok(olu_stok_gun)
        if len(olu):
            out.append(("dikkat", f"{len(olu)} kalem {olu_stok_gun} gündür satılmıyor; bağlı tahmini stok değeri "
                                  f"{tl(olu['tahmini_deger'].sum())} ₺."))
        so = self.siparis_onerisi()
        if len(so):
            acil = int((so["durum"] == "Acil").sum())
            tutar = so["oneri_tutar"].sum(min_count=1)
            out.append(("dikkat" if acil else "bilgi",
                        f"{len(so)} malzemenin stoğu 15 günden az yetecek ({acil} tanesi acil)"
                        + (f"; önerilen sipariş tutarı {tl(tutar)} ₺." if pd.notna(tutar) else ".")))
        neg = int((self.stok["stok_miktar"] < -0.0001).sum())
        if neg:
            out.append(("dikkat", f"{neg} malzemede stok bakiyesi eksi; Logo'da girilmemiş alış ya da sayım fişi olabilir "
                                  "(Excel raporunun Stok Devir sayfasında listelenir)."))
        if o["iade_orani"] >= 0.03:
            out.append(("dikkat", f"İade oranı {yz(o['iade_orani'])}; iade nedenlerine bakılmalı."))
        return out

    def veri_notlari(self):
        m = self.meta
        coklu = [("Birleştirilen firmalar", f"{m.get('firma_sayisi')} firma — {m.get('firma_araliklari', '')}. "
                                            "Müşteri ve malzemeler koduna göre eşleştirildi.")] if m.get("firma_sayisi") else []
        uyari = [("UYARI", m["uyari"])] if m.get("uyari") else []
        return uyari + [
            ("Firma", f"{m.get('firma')} {m.get('firma_ad', '')}"),
            *coklu,
            ("Dönemler", m.get("donemler", "")),
            ("Tarih aralığı", f"{m.get('tarih_bas')} – {m.get('tarih_bit')}"),
            ("Okunan ham satır", m.get("ham_satir")),
            ("İptal edildiği için dışlanan satır", m.get("iptal_satir")),
            ("İndirim/masraf vb. malzeme dışı satır", m.get("malzeme_disi_satir")),
            ("Faturalanmamış irsaliye satırı (satışa sayılmadı)", m.get("satis_faturasiz_dislanan")),
            ("Stok bakiyesinin alındığı dönem", m.get("stok_donemi")),
            ("Maliyet tanımı", "Son alış maliyeti: her satışa o tarihten önceki en son alış (yoksa devir) birim fiyatı; "
                               "brüt kâr tahminidir, muhasebe kârı değildir"),
            ("Maliyeti bulunan satışların ciro payı", f"%{float(m.get('maliyet_kapsama') or 0) * 100:.1f}"
             if m.get("maliyet_kapsama") is not None else "hesaplanmadı (eski sürümle oluşturulmuş analiz)"),
            ("Cari bakiyelerin alındığı dönem", m.get("cari_hareket_donemi") or "cari hareket tablosu yok"),
            ("Tutar tanımı", "KDV hariç net (VATMATRAH: satır + dağıtılmış dip indirim sonrası)"),
            ("Miktar tanımı", "Ana birim (koli/paket satırları ana birime çevrildi)"),
        ]
