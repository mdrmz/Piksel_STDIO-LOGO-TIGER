"""
Karşılaştırmalar.

- Dönem (yıl) karşılaştırma: aynı analizde iki tarih aralığı (ör. 2026 Oca–Eki ile 2025'in aynı dönemi) yan yana:
  özet göstergeler, aylık, ürün / cari / ambar / temsilci / grup kırılımı. Her kırılımda kim yeni geldi, kim kaybedildi,
  kim arttı ya da azaldı ve farkı kim oluşturdu (köprü). Ürünlerde fark miktar etkisi + fiyat etkisi olarak ayrılır.
  Yıl yıl ayrı açılmış Logo firmaları tek analizde birleştirildiğinde yıllar burada karşılaştırılır.
- Cari karşılaştırma: aynı analizde seçilen 2–4 cari yan yana (özet, aylık, ürünler ve ortalama fiyatları).
- Firma karşılaştırma: ayrı analizler (farklı Logo firmaları) yan yana: özet, aylık, en çok satan ürünler ve
  müşteriler, ambar ve temsilciler, ortak müşteriler (ada göre) ve ortak ürünler (koda göre).

Tüm rakamlar panelle aynı kaynaktan (fact_satis / fact_alis, hesaplama dışı bırakılanlar düşülmüş) hesaplanır:
filtresiz bir dönemin özeti, panelde o tarih aralığı seçildiğinde görünen özetin aynısıdır.
"""
from __future__ import annotations

import copy
import re
import unicodedata

import numpy as np
import pandas as pd

from .analiz import Analiz, tarih_maskesi

AY = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
SIFIR = 0.005   # bu tutarın altı "satış yok" sayılır (kuruş yuvarlamaları)

SEVIYELER = {   # kırılım: (başlık, anahtar sütun)
    "urun": ("Ürün", "malzeme_ref"),
    "cari": ("Cari", "cari_ref"),
    "grup": ("Malzeme grubu", "grup_kod"),
    "ambar": ("Ambar", "ambar"),
    "temsilci": ("Temsilci", "temsilci_ref"),
}
DURUMLAR = ("Yeni", "Kaybedildi", "Arttı", "Azaldı", "Aynı")

OZET_SATIR = [   # (anahtar, ad, tür)  tür: para | sayi | oran
    ("net_satis", "Net satış", "para"), ("brut_satis", "Brüt satış (iade öncesi)", "para"),
    ("iade_tutar", "İade tutarı", "para"), ("iade_orani", "İade oranı", "oran"),
    ("brut_kar", "Tahmini brüt kâr", "para"), ("kar_marji", "Brüt kâr marjı", "oran"),
    ("fatura_sayisi", "Satış faturası", "sayi"), ("ortalama_fatura", "Ortalama fatura", "para"),
    ("musteri_sayisi", "Müşteri sayısı", "sayi"), ("urun_sayisi", "Satılan ürün çeşidi", "sayi"),
    ("net_alis", "Net alış", "para"), ("tedarikci_sayisi", "Tedarikçi sayısı", "sayi"),
]


# ---------------------------------------------------------------------------- yardımcılar
def _ts(v) -> pd.Timestamp | None:
    t = pd.to_datetime(v, errors="coerce") if v else None
    return None if t is None or pd.isna(t) else t.normalize()


def _g(t) -> str:
    return f"{pd.Timestamp(t):%d.%m.%Y}"


def donem_etiketi(bas, bit) -> str:
    b, s = _ts(bas), _ts(bit)
    if b is not None and s is not None:
        if b.month == 1 and b.day == 1 and s.month == 12 and s.day == 31 and b.year == s.year:
            return str(b.year)
        if b.day == 1 and b.year == s.year and s == s + pd.offsets.MonthEnd(0):
            return f"{b.year} {AY[b.month - 1]}–{AY[s.month - 1]}" if b.month != s.month else f"{AY[b.month - 1]} {b.year}"
    return f"{_g(b) if b is not None else '…'} – {_g(s) if s is not None else '…'}"


def dilim(a: Analiz, bas, bit) -> Analiz:
    """Aynı analizin [bas, bit] aralığına süzülmüş hafif kopyası (veri yeniden okunmaz). a tarihe göre süzülmemiş olmalı."""
    k = copy.copy(a)
    k._onbellek = {}
    k.satis = a.satis[tarih_maskesi(a.satis["tarih"], bas, bit)].reset_index(drop=True)
    k.alis = a.alis[tarih_maskesi(a.alis["tarih"], bas, bit)].reset_index(drop=True)
    k.tarih_filtre = (bas, bit)
    return k


def _oran(a, b):
    a, b = float(a or 0), float(b or 0)
    return (b / a - 1) if abs(a) > SIFIR else None


def _ozet_tablo(oa: dict, ob: dict) -> list[dict]:
    out = []
    for k, ad, tur in OZET_SATIR:
        x, y = oa.get(k), ob.get(k)
        if x is None and y is None:
            continue
        fark = None if x is None or y is None else float(y) - float(x)
        out.append({"anahtar": k, "ad": ad, "tur": tur, "a": x, "b": y, "fark": fark,
                    "oran": None if tur == "oran" or fark is None else _oran(x, y)})
    return out


# ---------------------------------------------------------------------------- dönem önerileri
def oneriler(a: Analiz) -> list[dict]:
    """Verinin kapsadığı tarihlere göre hazır karşılaştırmalar. A = önceki (baz), B = yeni."""
    t = a.satis["tarih"].dropna()
    if t.empty:
        return []
    ilk, son = t.min().normalize(), t.max().normalize()
    out = []
    kapsar = lambda b: ilk <= b + pd.Timedelta(days=15)   # noqa: E731  (verinin ilk günleri tatil olabilir)
    y = son.year
    gecen_bas, gecen_bit = pd.Timestamp(y - 1, 1, 1), son - pd.DateOffset(years=1)
    if kapsar(gecen_bas) and not (son.month == 12 and son.day == 31):
        out.append({"kod": f"ytd{y}", "ad": f"{y} yıl başından bugüne ({_g(son)}) ↔ {y - 1} aynı dönem",
                    "A": (str(gecen_bas.date()), str(gecen_bit.date())), "B": (f"{y}-01-01", str(son.date()))})
    for yy in range(son.year, ilk.year, -1):   # tam yıllar: yy-1 ile yy (yy tam değilse yukarıdaki kapsar)
        if kapsar(pd.Timestamp(yy - 1, 1, 1)) and son >= pd.Timestamp(yy, 12, 31) - pd.Timedelta(days=15):
            out.append({"kod": f"yil{yy}", "ad": f"{yy} ↔ {yy - 1} (tam yıl)",
                        "A": (f"{yy - 1}-01-01", f"{yy - 1}-12-31"), "B": (f"{yy}-01-01", f"{yy}-12-31")})
    b12 = son - pd.DateOffset(years=1) + pd.Timedelta(days=1)
    a12 = b12 - pd.DateOffset(years=1)
    if kapsar(a12):
        out.append({"kod": "son12", "ad": f"Son 12 ay ↔ önceki 12 ay",
                    "A": (str(a12.date()), str((b12 - pd.Timedelta(days=1)).date())), "B": (str(b12.date()), str(son.date()))})
    ay_b = son.replace(day=1)
    if ay_b - pd.DateOffset(years=1) >= ilk - pd.Timedelta(days=15):
        ay_a = ay_b - pd.DateOffset(years=1)
        out.append({"kod": "ay", "ad": f"{AY[son.month - 1]} {son.year} ↔ {AY[son.month - 1]} {son.year - 1}",
                    "A": (str(ay_a.date()), str((ay_a + pd.offsets.MonthEnd(0)).date())), "B": (str(ay_b.date()), str(son.date()))})
    return out


# ---------------------------------------------------------------------------- kırılım
def _hazirla(a: Analiz, s: pd.DataFrame) -> pd.DataFrame:
    s = s.assign(temsilci_ref=s["satis_temsilci_ref"].fillna(0).astype(int) if "satis_temsilci_ref" in s.columns else 0)
    g = a.malzeme[["malzeme_ref", "grup_kod"]].drop_duplicates("malzeme_ref")
    s = s.merge(g, on="malzeme_ref", how="left")
    s["grup_kod"] = s["grup_kod"].fillna("").replace("", "(Grupsuz)")
    if "maliyet_tutar" in s.columns:
        bilinen = s["maliyet_tutar"].notna()
        s["_kar"] = (s["net_tutar"] - s["maliyet_tutar"]).where(bilinen)
        s["_kar_ciro"] = s["net_tutar"].where(bilinen)
    return s


def _adlar(a: Analiz, seviye: str) -> pd.DataFrame:
    if seviye == "urun":
        return a.malzeme[["malzeme_ref", "malzeme_kod", "malzeme_ad", "grup_kod"]].drop_duplicates("malzeme_ref").rename(
            columns={"malzeme_kod": "kod", "malzeme_ad": "ad", "grup_kod": "ek"})
    if seviye == "cari":
        return a.cari[["cari_ref", "cari_kod", "cari_ad", "sehir"]].drop_duplicates("cari_ref").rename(
            columns={"cari_kod": "kod", "cari_ad": "ad", "sehir": "ek"})
    if seviye == "ambar":
        d = a.ambar[["ambar", "ambar_ad"]].drop_duplicates("ambar").rename(columns={"ambar_ad": "ad"})
        return d.assign(kod=d["ambar"].astype(str), ek="")
    if seviye == "temsilci":
        d = a.temsilci.rename(columns={"temsilci_kod": "kod", "temsilci_ad": "ad"})[["temsilci_ref", "kod", "ad"]]
        d["temsilci_ref"] = pd.to_numeric(d["temsilci_ref"], errors="coerce").fillna(0).astype(int)
        return d.drop_duplicates("temsilci_ref").assign(ek="")
    return pd.DataFrame(columns=["grup_kod", "kod", "ad", "ek"])


def _bos_ad(seviye, deger) -> str:
    if seviye == "temsilci" and int(deger or 0) == 0:
        return "(Temsilci atanmamış)"
    if seviye == "ambar":
        return f"Ambar {deger}"
    if seviye == "grup":
        return str(deger)
    return f"(Kartı yok — Logo ref {deger})"


def kirilim(a: Analiz, sa: pd.DataFrame, sb: pd.DataFrame, seviye: str) -> pd.DataFrame:
    """sa / sb: _hazirla'dan geçmiş A ve B satışları. Sütunlar: kod, ad, ek, a, b, fark, oran, durum, a_miktar,
    b_miktar, a_fiyat, b_fiyat, miktar_etkisi, fiyat_etkisi, a_kar, b_kar, a_marj, b_marj, a_fatura, b_fatura."""
    _, key = SEVIYELER[seviye]

    def top(s, p):
        if s.empty:
            return pd.DataFrame(columns=[f"{p}", f"{p}_miktar", f"{p}_kar", f"{p}_kar_ciro", f"{p}_fatura"])
        g = s.groupby(key).agg(**{p: ("net_tutar", "sum"), f"{p}_miktar": ("miktar", "sum")})
        if "_kar" in s.columns:
            g[f"{p}_kar"] = s.groupby(key)["_kar"].sum(min_count=1)
            g[f"{p}_kar_ciro"] = s.groupby(key)["_kar_ciro"].sum(min_count=1)
        g[f"{p}_fatura"] = s[~s["iade"]].groupby(key)["fatura_no"].nunique()
        return g
    t = top(sa, "a").join(top(sb, "b"), how="outer")
    for k in ("a", "b", "a_miktar", "b_miktar", "a_fatura", "b_fatura"):
        t[k] = pd.to_numeric(t.get(k), errors="coerce").fillna(0.0)
    t["fark"] = t["b"] - t["a"]
    t["oran"] = np.where(t["a"].abs() > SIFIR, t["b"] / t["a"].where(t["a"].abs() > SIFIR) - 1, np.nan)
    var_a, var_b = t["a"].abs() > SIFIR, t["b"].abs() > SIFIR
    t["durum"] = np.select([~var_a & var_b, var_a & ~var_b, t["fark"] > SIFIR, t["fark"] < -SIFIR],
                           ["Yeni", "Kaybedildi", "Arttı", "Azaldı"], "Aynı")
    for p in ("a", "b"):
        t[f"{p}_fiyat"] = t[p] / t[f"{p}_miktar"].where(t[f"{p}_miktar"].abs() > 1e-9)
        if f"{p}_kar" in t.columns:
            t[f"{p}_marj"] = t[f"{p}_kar"] / t[f"{p}_kar_ciro"].where(t[f"{p}_kar_ciro"].abs() > SIFIR)
        else:
            t[f"{p}_kar"], t[f"{p}_marj"] = np.nan, np.nan
    if seviye == "urun":   # fark = miktar etkisi + fiyat etkisi (iki dönemde de satılan ürünlerde)
        iki = t["a_fiyat"].notna() & t["b_fiyat"].notna() & var_a & var_b
        t["miktar_etkisi"] = ((t["b_miktar"] - t["a_miktar"]) * t["a_fiyat"]).where(iki)
        t["fiyat_etkisi"] = ((t["b_fiyat"] - t["a_fiyat"]) * t["b_miktar"]).where(iki)
    else:
        t["miktar_etkisi"], t["fiyat_etkisi"] = np.nan, np.nan
    t = t.reset_index().rename(columns={"index": key})
    ad = _adlar(a, seviye)
    if seviye == "grup":
        t = t.assign(kod=t[key], ad=t[key], ek="")
    else:
        t[key] = pd.to_numeric(t[key], errors="coerce").fillna(0).astype(int)
        t = t.merge(ad, on=key, how="left")
        bos = t["ad"].isna() | (t["ad"].astype(str).str.strip() == "")
        t.loc[bos, "ad"] = t.loc[bos, key].map(lambda v: _bos_ad(seviye, v))
        t["kod"] = t["kod"].fillna(t[key].astype(str))
        t["ek"] = t["ek"].fillna("")
    t = t.assign(_m=t["fark"].abs()).sort_values(["_m", "b"], ascending=False).drop(columns="_m")
    kolon = ["kod", "ad", "ek", "a", "b", "fark", "oran", "durum", "a_miktar", "b_miktar", "a_fiyat", "b_fiyat",
             "miktar_etkisi", "fiyat_etkisi", "a_kar", "b_kar", "a_marj", "b_marj", "a_fatura", "b_fatura"]
    return t[kolon].reset_index(drop=True)


def kopru(t: pd.DataFrame) -> dict:
    """A'dan B'ye: yeni gelenler + artanlar − azalanlar − kaybedilenler. Toplamı B − A'ya eşittir."""
    d = {k: {"tutar": float(t.loc[t["durum"] == k, "fark"].sum()), "adet": int((t["durum"] == k).sum())} for k in DURUMLAR}
    d["a"], d["b"] = float(t["a"].sum()), float(t["b"].sum())
    d["fark"] = d["b"] - d["a"]
    if "miktar_etkisi" in t.columns and t["miktar_etkisi"].notna().any():
        d["miktar_etkisi"] = float(t["miktar_etkisi"].sum())
        d["fiyat_etkisi"] = float(t["fiyat_etkisi"].sum())
    return d


# ---------------------------------------------------------------------------- aylık
def _aylar(bas, bit, s: pd.DataFrame) -> list[pd.Period]:
    b, e = _ts(bas), _ts(bit)
    if b is None or e is None:
        t = s["tarih"].dropna()
        if t.empty:
            return []
        b, e = (b or t.min()), (e or t.max())
    return list(pd.period_range(b, e, freq="M"))


def aylik(sa, sb, A, B) -> pd.DataFrame:
    pa, pb = _aylar(*A, sa), _aylar(*B, sb)
    ta = sa.groupby(sa["tarih"].dt.to_period("M"))["net_tutar"].sum()
    tb = sb.groupby(sb["tarih"].dt.to_period("M"))["net_tutar"].sum()
    satir = []
    for i in range(max(len(pa), len(pb))):
        ma, mb = (pa[i] if i < len(pa) else None), (pb[i] if i < len(pb) else None)
        if ma is not None and mb is not None and ma.month == mb.month:
            et = AY[ma.month - 1]
        else:
            et = " / ".join(f"{AY[m.month - 1]} {m.year}" for m in (ma, mb) if m is not None)
        x = float(ta.get(ma, 0.0)) if ma is not None else None
        y = float(tb.get(mb, 0.0)) if mb is not None else None
        satir.append({"etiket": et, "a_ay": str(ma) if ma is not None else "", "b_ay": str(mb) if mb is not None else "",
                      "a": x, "b": y, "fark": (y - x) if x is not None and y is not None else None,
                      "oran": _oran(x, y) if x is not None and y is not None else None})
    return pd.DataFrame(satir)


def grafik_iki(t: pd.DataFrame, ad_a: str, ad_b: str) -> str:
    import base64
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    fig, ax = plt.subplots(figsize=(9.5, 3.2), dpi=110)
    x = np.arange(len(t))
    ax.bar(x - 0.2, t["a"].fillna(0), width=0.4, color="#A6B4CC", label=ad_a)
    ax.bar(x + 0.2, t["b"].fillna(0), width=0.4, color="#1F3864", label=ad_b)
    ax.set_xticks(x, t["etiket"], rotation=45 if len(t) > 12 else 0, ha="right" if len(t) > 12 else "center", fontsize=8)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1e6:,.1f} M".replace(".", ",")))
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def grafik_cok(etiketler: list, seriler: list[tuple[str, list]]) -> str:
    """Birden çok seri (cari ya da firma) için aylık çizgi grafik."""
    import base64
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    renk = ["#1F3864", "#C55A11", "#548235", "#7F6000", "#7030A0"]
    fig, ax = plt.subplots(figsize=(9.5, 3.2), dpi=110)
    for i, (ad, deger) in enumerate(seriler):
        ax.plot(range(len(etiketler)), deger, marker="o", ms=3, lw=1.8, color=renk[i % len(renk)], label=ad[:40])
    ax.set_xticks(range(len(etiketler)), etiketler, rotation=45, ha="right", fontsize=8)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1e6:,.2f} M".replace(".", ",") if abs(v) >= 1e5 else f"{v:,.0f}".replace(",", ".")))
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------- 1. dönem karşılaştırma
def donem(a: Analiz, A: tuple, B: tuple) -> dict:
    """a: tarihe göre süzülmemiş analiz (ambar süzülmüş olabilir). A, B: (bas, bit) 'YYYY-MM-DD'."""
    da, db = dilim(a, *A), dilim(a, *B)
    oa, ob = da.ozet(), db.ozet()
    sa, sb = _hazirla(a, da.satis), _hazirla(a, db.satis)
    ad_a, ad_b = donem_etiketi(*A), donem_etiketi(*B)
    if ad_a == ad_b:
        ad_a, ad_b = f"A: {ad_a}", f"B: {ad_b}"
    k = {sev: kirilim(a, sa, sb, sev) for sev in SEVIYELER}
    gun = lambda d: ((_ts(d[1]) - _ts(d[0])).days + 1) if _ts(d[0]) is not None and _ts(d[1]) is not None else None  # noqa: E731
    uyari = []
    if gun(A) and gun(B) and abs(gun(A) - gun(B)) > 3:
        uyari.append(f"Dönemlerin uzunluğu farklı ({gun(A)} gün ↔ {gun(B)} gün); toplamlar doğrudan kıyaslanmamalı.")
    for ad, d, o in ((ad_a, A, oa), (ad_b, B, ob)):
        t = a.satis["tarih"]
        if _ts(d[0]) is not None and t.min() > _ts(d[0]) + pd.Timedelta(days=15):
            uyari.append(f"{ad}: veri {_g(t.min())} tarihinde başlıyor; dönemin başı yedekte yok.")
        if _ts(d[1]) is not None and t.max() < _ts(d[1]) - pd.Timedelta(days=15):
            uyari.append(f"{ad}: veri {_g(t.max())} tarihinde bitiyor; dönemin sonu yedekte yok.")
    ay = aylik(sa, sb, A, B)
    return {"A": A, "B": B, "ad_a": ad_a, "ad_b": ad_b, "ozet": _ozet_tablo(oa, ob), "oa": oa, "ob": ob,
            "aylik": ay, "grafik": grafik_iki(ay, ad_a, ad_b) if len(ay) else None, "kirilim": k,
            "kopru": {sev: kopru(k[sev]) for sev in ("urun", "cari")}, "uyari": uyari, "kar_var": a.maliyet_var}


# ---------------------------------------------------------------------------- 2. cari karşılaştırma
def cari(a: Analiz, kodlar: list[str], urun_say: int = 15) -> dict:
    """a: ekrandaki tarih/ambar filtresiyle açılmış analiz. kodlar: cari kodları (2–4)."""
    c = a.cari.drop_duplicates("cari_ref")
    secili = []
    for kod in kodlar:
        r = c[c["cari_kod"].astype(str) == str(kod)]
        if len(r) and int(r.iloc[0]["cari_ref"]) not in [x["ref"] for x in secili]:
            secili.append({"ref": int(r.iloc[0]["cari_ref"]), "kod": str(kod), "ad": r.iloc[0]["cari_ad"] or str(kod),
                           "sehir": r.iloc[0].get("sehir") or ""})
    s = _hazirla(a, a.satis)
    toplam = float(s["net_tutar"].sum())
    ozet = []
    for x in secili:
        d = s[s["cari_ref"] == x["ref"]]
        sat = d[~d["iade"]]
        fat = sat.groupby("fatura_no")["net_tutar"].sum()
        kar = float(d["_kar"].sum()) if "_kar" in d.columns and d["_kar"].notna().any() else None
        kc = float(d["_kar_ciro"].sum()) if kar is not None else 0.0
        ozet.append({**x, "net_satis": float(d["net_tutar"].sum()), "iade": float(-d.loc[d["iade"], "net_tutar"].sum()),
                     "fatura": int(fat.shape[0]), "ort_fatura": float(fat.mean()) if len(fat) else 0.0,
                     "urun_cesidi": int(sat["malzeme_ref"].nunique()), "brut_kar": kar,
                     "marj": (kar / kc) if kar is not None and abs(kc) > SIFIR else None,
                     "ilk": d["tarih"].min(), "son": d["tarih"].max(), "pay": float(d["net_tutar"].sum()) / toplam if toplam else 0.0})
    # aylık
    aylar = sorted(s["tarih"].dropna().dt.to_period("M").unique())
    ay_t = s[s["cari_ref"].isin([x["ref"] for x in secili])].groupby([s["tarih"].dt.to_period("M"), "cari_ref"])["net_tutar"].sum()
    aylik_t = pd.DataFrame([{"ay": f"{AY[p.month - 1]} {p.year}", **{x["kod"]: float(ay_t.get((p, x["ref"]), 0.0)) for x in secili}}
                            for p in aylar])
    graf = grafik_cok(list(aylik_t["ay"]), [(x["ad"], list(aylik_t[x["kod"]])) for x in secili]) if len(aylik_t) and secili else None
    # ürünler: her carinin en çok aldığı ürünlerin birleşimi
    u = s[s["cari_ref"].isin([x["ref"] for x in secili])]
    g = u.groupby(["malzeme_ref", "cari_ref"]).agg(tutar=("net_tutar", "sum"), miktar=("miktar", "sum"))
    secilen_urun = []
    for x in secili:
        if (x["ref"] in g.index.get_level_values(1)):
            secilen_urun += list(g.xs(x["ref"], level=1).sort_values("tutar", ascending=False).head(urun_say).index)
    secilen_urun = list(dict.fromkeys(secilen_urun))
    m = a.malzeme.drop_duplicates("malzeme_ref").set_index("malzeme_ref")
    urunler = []
    for mr in secilen_urun:
        satir = {"kod": m["malzeme_kod"].get(mr, str(mr)), "ad": m["malzeme_ad"].get(mr, f"(Kartı yok — ref {mr})")}
        fiyatlar = []
        for x in secili:
            t = g.loc[(mr, x["ref"])] if (mr, x["ref"]) in g.index else None
            tutar = float(t["tutar"]) if t is not None else 0.0
            miktar = float(t["miktar"]) if t is not None else 0.0
            fiyat = tutar / miktar if abs(miktar) > 1e-9 and abs(tutar) > SIFIR else None
            satir[x["kod"]] = {"tutar": tutar, "miktar": miktar, "fiyat": fiyat}
            if fiyat is not None and fiyat > 0:
                fiyatlar.append(fiyat)
        satir["fiyat_farki"] = (max(fiyatlar) / min(fiyatlar) - 1) if len(fiyatlar) >= 2 else None
        satir["ortak"] = len(fiyatlar)
        satir["_top"] = sum(satir[x["kod"]]["tutar"] for x in secili)
        urunler.append(satir)
    urunler.sort(key=lambda r: -r["_top"])
    return {"secili": secili, "ozet": ozet, "aylik": aylik_t, "grafik": graf, "urunler": urunler,
            "kar_var": a.maliyet_var}


# ---------------------------------------------------------------------------- 3. firma karşılaştırma
def _normal_ad(s) -> str:
    """Firma adı eşleştirme: büyük harf, Türkçe karakter ve noktalama farkı yok, şirket türü ekleri atılır."""
    s = unicodedata.normalize("NFKD", str(s or "").replace("ı", "i").replace("İ", "I")).encode("ascii", "ignore").decode().upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\b(LTD|STI|SIRKETI|SIRK|AS|A S|TIC|TICARET|SAN|SANAYI|VE|LIMITED|ANONIM|INS|IN?SAAT|PAZ|PAZARLAMA)\b", " ", s)
    return " ".join(s.split())


def firma(analizler: list[tuple[str, dict, Analiz]], urun_say: int = 10) -> dict:
    """analizler: [(aid, bilgi, Analiz — ortak tarih aralığıyla açılmış)]. 2–4 firma."""
    firmalar, aylar_hepsi = [], set()
    for aid, bilgi, a in analizler:
        s = _hazirla(a, a.satis)
        o = a.ozet()
        ad = f"{bilgi.get('firma', '')} {bilgi.get('firma_ad', '')}".strip()
        aylar_hepsi |= set(s["tarih"].dropna().dt.to_period("M").unique())
        firmalar.append({"aid": aid, "ad": ad, "kisa": (bilgi.get("firma_ad") or ad)[:28], "o": o, "s": s, "a": a})
    ozet = []
    for k, ad, tur in OZET_SATIR:
        deg = [f["o"].get(k) for f in firmalar]
        if all(v is None for v in deg):
            continue
        ozet.append({"ad": ad, "tur": tur, "degerler": deg})
    aylar = sorted(aylar_hepsi)
    aylik_t = pd.DataFrame([{"ay": f"{AY[p.month - 1]} {p.year}",
                             **{f["aid"]: float(f["s"].loc[f["s"]["tarih"].dt.to_period("M") == p, "net_tutar"].sum()) for f in firmalar}}
                            for p in aylar])
    graf = grafik_cok(list(aylik_t["ay"]), [(f["kisa"], list(aylik_t[f["aid"]])) for f in firmalar]) if len(aylik_t) else None

    def en_cok(f, sev, n):
        t = kirilim(f["a"], f["s"].iloc[0:0], f["s"], sev)
        t = t.sort_values("b", ascending=False).head(n)
        toplam = float(f["s"]["net_tutar"].sum())
        return [{"kod": r.kod, "ad": r.ad, "tutar": float(r.b), "pay": float(r.b) / toplam if toplam else 0.0,
                 "marj": None if pd.isna(r.b_marj) else float(r.b_marj)} for r in t.itertuples()]
    listeler = {sev: [en_cok(f, sev, urun_say if sev in ("urun", "cari") else 8) for f in firmalar]
                for sev in ("urun", "cari", "ambar", "temsilci", "grup")}

    # ortak müşteriler (ada göre) ve ortak ürünler (koda göre)
    def ortak(sev):
        tablolar = []
        for f in firmalar:
            t = kirilim(f["a"], f["s"].iloc[0:0], f["s"], sev)[["kod", "ad", "b", "b_miktar"]]
            t = t[t["b"].abs() > SIFIR]
            t["anahtar"] = t["ad"].map(_normal_ad) if sev == "cari" else t["kod"].astype(str).str.strip().str.upper()
            t = t[(t["anahtar"] != "") & ~t["ad"].astype(str).str.startswith("(")]
            tablolar.append(t.groupby("anahtar").agg(ad=("ad", "first"), tutar=("b", "sum"), miktar=("b_miktar", "sum")))
        anahtarlar = set.union(*[set(t.index) for t in tablolar]) if tablolar else set()
        satir = []
        for k in anahtarlar:
            var = [k in t.index for t in tablolar]
            if sum(var) < 2:
                continue
            ad = next(t.loc[k, "ad"] for t, v in zip(tablolar, var) if v)
            deg = []
            for t, v in zip(tablolar, var):
                if v:
                    tut, mik = float(t.loc[k, "tutar"]), float(t.loc[k, "miktar"])
                    deg.append({"tutar": tut, "fiyat": tut / mik if abs(mik) > 1e-9 else None})
                else:
                    deg.append(None)
            satir.append({"anahtar": k, "ad": ad, "degerler": deg, "firma_say": sum(var),
                          "_top": sum(d["tutar"] for d in deg if d)})
        satir.sort(key=lambda r: -r["_top"])
        return satir
    o_cari, o_urun = ortak("cari"), ortak("urun")
    for f in firmalar:
        f.pop("s"), f.pop("a")
    return {"firmalar": firmalar, "ozet": ozet, "aylik": aylik_t, "grafik": graf, "listeler": listeler,
            "ortak_cari": o_cari, "ortak_urun": o_urun}


def ortak_aralik(bilgiler: list[dict]) -> tuple[str | None, str | None]:
    """Seçilen analizlerin tarih aralıklarının kesişimi (yoksa None, None)."""
    bas = [_ts(b.get("tarih_bas")) for b in bilgiler]
    bit = [_ts(b.get("tarih_bit")) for b in bilgiler]
    if any(x is None for x in bas + bit):
        return None, None
    b, s = max(bas), min(bit)
    return (str(b.date()), str(s.date())) if b <= s else (None, None)


# ---------------------------------------------------------------------------- Excel
def _yazici(yol):
    import xlsxwriter
    wb = xlsxwriter.Workbook(yol, {"nan_inf_to_errors": True})
    f = {"bas": wb.add_format({"bold": True, "font_color": "white", "bg_color": "#1F3864", "border": 1, "text_wrap": True, "valign": "vcenter"}),
         "para": wb.add_format({"num_format": "#,##0.00"}), "sayi": wb.add_format({"num_format": "#,##0.##"}),
         "oran": wb.add_format({"num_format": "0.0%"}), "not": wb.add_format({"italic": True, "font_color": "#666666"}),
         "baslik": wb.add_format({"bold": True, "font_size": 13})}

    def sayfa(ad, df, kolonlar, not_=""):
        ws = wb.add_worksheet(ad[:31])
        ws.write(0, 0, not_, f["not"])
        for j, (_, b, _) in enumerate(kolonlar):
            ws.write(1, j, b, f["bas"])
        for i, row in enumerate(df.itertuples(index=False), start=2):
            r = row._asdict()
            for j, (k, _, tur) in enumerate(kolonlar):
                v = r.get(k)
                if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
                    continue
                if tur in ("para", "sayi", "oran") and isinstance(v, (int, float, np.integer, np.floating)):
                    ws.write_number(i, j, float(v), f[tur])
                else:
                    ws.write(i, j, str(v))
        ws.freeze_panes(2, 0)
        if len(df):
            ws.autofilter(1, 0, len(df) + 1, len(kolonlar) - 1)
        for j, (k, b, tur) in enumerate(kolonlar):
            ws.set_column(j, j, 15 if tur in ("para", "sayi", "oran") else (40 if k == "ad" else 14))
        return ws
    return wb, sayfa


def donem_excel(k: dict, yol: str, baslik: str):
    wb, sayfa = _yazici(yol)
    a, b = k["ad_a"], k["ad_b"]
    oz = pd.DataFrame(k["ozet"])
    sayfa("Özet", oz.assign(oran=oz["oran"]), [("ad", "Gösterge", "metin"), ("a", a, "para"), ("b", b, "para"),
                                               ("fark", "Fark", "para"), ("oran", "Değişim %", "oran")],
          f"{baslik} — {a} ↔ {b}. Oran göstergelerinde fark puan olarak verilir.")
    sayfa("Aylık", k["aylik"], [("etiket", "Ay", "metin"), ("a_ay", f"{a} ayı", "metin"), ("a", a, "para"),
                                ("b_ay", f"{b} ayı", "metin"), ("b", b, "para"), ("fark", "Fark", "para"), ("oran", "Değişim %", "oran")])
    for sev, (ad, _) in SEVIYELER.items():
        t = k["kirilim"][sev]
        kol = [("kod", "Kod", "metin"), ("ad", ad, "metin"), ("ek", "Grup / şehir" if sev in ("urun", "cari") else "", "metin"),
               ("a", a, "para"), ("b", b, "para"), ("fark", "Fark", "para"), ("oran", "Değişim %", "oran"), ("durum", "Durum", "metin")]
        if sev == "urun":
            kol += [("a_miktar", f"Miktar {a}", "sayi"), ("b_miktar", f"Miktar {b}", "sayi"), ("a_fiyat", f"Ort. fiyat {a}", "para"),
                    ("b_fiyat", f"Ort. fiyat {b}", "para"), ("miktar_etkisi", "Miktar etkisi", "para"), ("fiyat_etkisi", "Fiyat etkisi", "para")]
        if k["kar_var"]:
            kol += [("a_kar", f"Brüt kâr {a}", "para"), ("b_kar", f"Brüt kâr {b}", "para"), ("a_marj", f"Marj {a}", "oran"), ("b_marj", f"Marj {b}", "oran")]
        kol += [("a_fatura", f"Fatura {a}", "sayi"), ("b_fatura", f"Fatura {b}", "sayi")]
        if sev == "urun":
            kol[2] = ("ek", "Grup", "metin")
        sayfa({"urun": "Ürün", "cari": "Cari", "grup": "Grup", "ambar": "Ambar", "temsilci": "Temsilci"}[sev], t, kol,
              "Farkın büyüklüğüne göre sıralı. Durum: Yeni = önceki dönemde yok, Kaybedildi = yeni dönemde yok.")
    wb.close()
    return yol


def cari_excel(k: dict, yol: str, baslik: str):
    wb, sayfa = _yazici(yol)
    oz = pd.DataFrame(k["ozet"])
    if len(oz):
        oz["ilk"], oz["son"] = oz["ilk"].dt.strftime("%d.%m.%Y"), oz["son"].dt.strftime("%d.%m.%Y")
    sayfa("Özet", oz, [("kod", "Cari kodu", "metin"), ("ad", "Cari", "metin"), ("sehir", "Şehir", "metin"),
                       ("net_satis", "Net satış", "para"), ("pay", "Ciro payı", "oran"), ("iade", "İade", "para"),
                       ("fatura", "Fatura", "sayi"), ("ort_fatura", "Ort. fatura", "para"), ("urun_cesidi", "Ürün çeşidi", "sayi"),
                       ("brut_kar", "Brüt kâr", "para"), ("marj", "Marj", "oran"), ("ilk", "İlk alım", "metin"), ("son", "Son alım", "metin")], baslik)
    sayfa("Aylık", k["aylik"], [("ay", "Ay", "metin")] + [(x["kod"], x["ad"], "para") for x in k["secili"]])
    rows = []
    for u in k["urunler"]:
        r = {"kod": u["kod"], "ad": u["ad"], "fiyat_farki": u["fiyat_farki"]}
        for x in k["secili"]:
            d = u[x["kod"]]
            r[f"{x['kod']}_t"], r[f"{x['kod']}_m"], r[f"{x['kod']}_f"] = d["tutar"], d["miktar"], d["fiyat"]
        rows.append(r)
    kol = [("kod", "Malzeme kodu", "metin"), ("ad", "Malzeme", "metin")]
    for x in k["secili"]:
        kol += [(f"{x['kod']}_t", f"{x['ad'][:20]} tutar", "para"), (f"{x['kod']}_m", f"{x['ad'][:20]} miktar", "sayi"),
                (f"{x['kod']}_f", f"{x['ad'][:20]} ort. fiyat", "para")]
    kol.append(("fiyat_farki", "En yüksek / en düşük fiyat farkı", "oran"))
    sayfa("Ürünler", pd.DataFrame(rows, columns=[c for c, _, _ in kol]), kol)
    wb.close()
    return yol


def firma_excel(k: dict, yol: str, baslik: str):
    wb, sayfa = _yazici(yol)
    ff = k["firmalar"]
    oz = pd.DataFrame([{"ad": r["ad"], **{f"f{i}": v for i, v in enumerate(r["degerler"])}, "tur": r["tur"]} for r in k["ozet"]])
    # oranlar ayrı biçimlenir: iki sayfa yerine tür sütunuyla tek sayfa, oranlar 0-1 arası
    sayfa("Özet", oz, [("ad", "Gösterge", "metin")] + [(f"f{i}", f["ad"], "para") for i, f in enumerate(ff)], baslik + " — oranlar 0–1 arası (0,25 = %25).")
    sayfa("Aylık", k["aylik"], [("ay", "Ay", "metin")] + [(f["aid"], f["ad"], "para") for f in ff])
    for sev, ad in (("cari", "Ortak müşteriler"), ("urun", "Ortak ürünler")):
        rows = []
        for r in k[f"ortak_{sev}"]:
            d = {"ad": r["ad"], "firma_say": r["firma_say"]}
            for i, v in enumerate(r["degerler"]):
                d[f"t{i}"] = v["tutar"] if v else None
                d[f"p{i}"] = v["fiyat"] if v else None
            rows.append(d)
        kol = [("ad", "Müşteri" if sev == "cari" else "Ürün", "metin"), ("firma_say", "Kaç firmada", "sayi")]
        for i, f in enumerate(ff):
            kol.append((f"t{i}", f"{f['kisa']} tutar", "para"))
            if sev == "urun":
                kol.append((f"p{i}", f"{f['kisa']} ort. fiyat", "para"))
        sayfa(ad, pd.DataFrame(rows, columns=[c for c, _, _ in kol]), kol,
              "Müşteriler ada göre (şirket türü ekleri ve Türkçe karakter farkı yok sayılarak), ürünler koda göre eşleştirilir.")
    for sev, ad in (("urun", "En çok satan ürünler"), ("cari", "En büyük müşteriler"), ("ambar", "Ambarlar"), ("temsilci", "Temsilciler")):
        rows = []
        for i, f in enumerate(ff):
            for j, r in enumerate(k["listeler"][sev][i], start=1):
                rows.append({"firma": f["ad"], "sira": j, "ad": r["ad"], "kod": r["kod"], "tutar": r["tutar"], "pay": r["pay"], "marj": r["marj"]})
        sayfa(ad, pd.DataFrame(rows, columns=["firma", "sira", "kod", "ad", "tutar", "pay", "marj"]),
              [("firma", "Firma", "metin"), ("sira", "Sıra", "sayi"), ("kod", "Kod", "metin"), ("ad", "Ad", "metin"),
               ("tutar", "Net satış", "para"), ("pay", "Firma içi pay", "oran"), ("marj", "Marj", "oran")])
    wb.close()
    return yol
