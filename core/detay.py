"""
Ürün kartı, cari kartı ve ay detayı: tek bir ürünün/müşterinin/ayın içine inen kırılımlar ve otomatik bulgular.

Doğrudan analiz deposundan (SQLite) yalnızca ilgili satırları okur; büyük veride de hızlıdır.
Tutarlar KDV hariç net, iadeler düşülmüş; miktar ana birim. Kâr tahminidir (son alış maliyetiyle).
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def ay_ad(yil_ay: str) -> str:
    """'2026-03' -> 'Mart 2026'"""
    try:
        return f"{AYLAR[int(yil_ay[5:7]) - 1]} {yil_ay[:4]}"
    except (ValueError, IndexError):
        return yil_ay


def _tl(v, ondalik=0):
    return f"{float(v):,.{ondalik}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _yz(v):
    return f"%{float(v) * 100:.0f}"


def _oku(con, sql, params=()):
    d = pd.read_sql_query(sql, con, params=params)
    if "tarih" in d.columns:
        d["tarih"] = pd.to_datetime(d["tarih"], errors="coerce")
        d = d[d["tarih"].notna()]
        d["yil_ay"] = d["tarih"].dt.strftime("%Y-%m")
    for c in ("miktar", "net_tutar", "maliyet_tutar"):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    return d


def _tum_aylar(con) -> list[str]:
    r = con.execute("SELECT MIN(tarih), MAX(tarih) FROM fact_satis").fetchone()
    if not r or not r[0]:
        return []
    return [p.strftime("%Y-%m") for p in pd.period_range(pd.Timestamp(r[0]), pd.Timestamp(r[1]), freq="M")]


def _aylik(s: pd.DataFrame, aylar: list[str], anahtar_say: str) -> pd.DataFrame:
    """Ay ay: miktar, net_tutar, kâr, ortalama net birim fiyat, farklı müşteri/ürün sayısı. Satış olmayan aylar 0."""
    g = s.groupby("yil_ay").agg(miktar=("miktar", "sum"), net_tutar=("net_tutar", "sum"),
                                maliyet=("maliyet_tutar", lambda x: x.sum(min_count=1)),
                                maliyetli_tutar=("_mt", "sum"), adet=(anahtar_say, "nunique"),
                                fatura=("fatura_no", "nunique"))
    g = g.reindex(aylar)
    for c in ("miktar", "net_tutar", "maliyetli_tutar", "adet", "fatura"):
        g[c] = g[c].fillna(0.0)
    g["kar"] = g["maliyetli_tutar"] - g["maliyet"]
    g["ort_fiyat"] = g["net_tutar"] / g["miktar"].where(g["miktar"] > 0)
    g = g.reset_index().rename(columns={"index": "yil_ay"})
    g["ay_ad"] = g["yil_ay"].map(ay_ad)
    return g


def _hazirla(s: pd.DataFrame) -> pd.DataFrame:
    s = s.copy()
    s["_mt"] = s["net_tutar"].where(s["maliyet_tutar"].notna(), 0.0)   # kâr hesabına giren ciro (maliyeti bilinen)
    return s


def _kirilim(s: pd.DataFrame, anahtar: list[str]) -> pd.DataFrame:
    g = s.groupby(anahtar, as_index=False).agg(
        miktar=("miktar", "sum"), net_tutar=("net_tutar", "sum"), maliyet=("maliyet_tutar", lambda x: x.sum(min_count=1)),
        maliyetli_tutar=("_mt", "sum"), fatura=("fatura_no", "nunique"), ilk=("tarih", "min"), son=("tarih", "max"))
    g["kar"] = g["maliyetli_tutar"] - g["maliyet"]
    g["marj"] = g["kar"] / g["maliyetli_tutar"].where(g["maliyetli_tutar"] > 0)
    g["ort_fiyat"] = g["net_tutar"] / g["miktar"].where(g["miktar"] > 0)
    toplam = g["net_tutar"].sum()
    g["pay"] = g["net_tutar"] / toplam if toplam else 0.0
    return g.sort_values("net_tutar", ascending=False, kind="mergesort").reset_index(drop=True)


def _mevsim(aylik: pd.DataFrame, olcu: str) -> list[int]:
    """En az iki yılda da o yılın ortalamasının belirgin üstünde (×1,3) kalan takvim ayları (1-12)."""
    d = aylik.assign(yil=aylik["yil_ay"].str[:4], ay=aylik["yil_ay"].str[5:7].astype(int))
    guclu: dict[int, int] = {}
    yil_say = 0
    for _, g in d.groupby("yil"):
        if len(g) < 6 or g[olcu].sum() <= 0:
            continue
        yil_say += 1
        ort = g[olcu].mean()
        for r in g.itertuples():
            if getattr(r, olcu) >= 1.3 * ort:
                guclu[r.ay] = guclu.get(r.ay, 0) + 1
    return sorted(a for a, n in guclu.items() if n >= 2) if yil_say >= 2 else []


def _donem_karsilastir(s: pd.DataFrame, aylar: list[str], anahtar: list[str], n: int = 6):
    """Son n ay ile ondan önceki n ayı kıyaslar: (toplam_son, toplam_once, en çok düşenler, en çok artanlar)."""
    if len(aylar) < 2 * n:
        n = len(aylar) // 2
    if n < 2:
        return None
    son, once = set(aylar[-n:]), set(aylar[-2 * n:-n])
    a = s[s["yil_ay"].isin(son)].groupby(anahtar)["net_tutar"].sum()
    b = s[s["yil_ay"].isin(once)].groupby(anahtar)["net_tutar"].sum()
    fark = a.sub(b, fill_value=0.0).rename("fark").reset_index()
    fark["son"] = fark.set_index(anahtar).index.map(a).fillna(0.0).values if len(fark) else []
    fark["once"] = fark.set_index(anahtar).index.map(b).fillna(0.0).values if len(fark) else []
    return {"n": n, "son": float(a.sum()), "once": float(b.sum()),
            "dusen": fark.sort_values("fark").head(5), "artan": fark.sort_values("fark", ascending=False).head(5)}


# ---------------------------------------------------------------------------- ürün kartı
def urun_karti(depo: str, kod: str, ay: str | None = None) -> dict | None:
    con = sqlite3.connect(depo)
    try:
        m = pd.read_sql_query("SELECT * FROM dim_malzeme WHERE malzeme_kod = ?", con, params=(kod,))
        if m.empty:
            return None
        ref = int(m.iloc[0]["malzeme_ref"])
        s = _hazirla(_oku(con, "SELECT s.tarih, s.cari_ref, s.miktar, s.net_tutar, s.maliyet_tutar, s.fatura_no, s.iade, "
                               "c.cari_kod, c.cari_ad, c.sehir FROM fact_satis s LEFT JOIN dim_cari c USING (cari_ref) "
                               "WHERE s.malzeme_ref = ?", (ref,)))
        al = _oku(con, "SELECT a.tarih, a.miktar, a.net_tutar, c.cari_kod, c.cari_ad FROM fact_alis a "
                       "LEFT JOIN dim_cari c USING (cari_ref) WHERE a.malzeme_ref = ?", (ref,))
        aylar = _tum_aylar(con)
        stok = con.execute("SELECT stok_miktar FROM stok_bakiye WHERE malzeme_ref = ?", (ref,)).fetchone()
    finally:
        con.close()
    for d in (s, al):
        for c in ("cari_kod", "cari_ad"):
            d[c] = d[c].fillna("")
    aylik = _aylik(s, aylar, "cari_ref")
    alis_ay = al.groupby("yil_ay").agg(alis_miktar=("miktar", "sum"), alis_tutar=("net_tutar", "sum"))
    aylik = aylik.merge(alis_ay, left_on="yil_ay", right_index=True, how="left")
    aylik["alis_fiyat"] = aylik["alis_tutar"] / aylik["alis_miktar"].where(aylik["alis_miktar"] > 0)
    musteri = _kirilim(s, ["cari_kod", "cari_ad", "sehir"])
    tedarikci = (al.groupby(["cari_kod", "cari_ad"], as_index=False).agg(miktar=("miktar", "sum"), net_tutar=("net_tutar", "sum"),
                                                                        son=("tarih", "max"))
                 .sort_values("net_tutar", ascending=False))
    tedarikci["ort_fiyat"] = tedarikci["net_tutar"] / tedarikci["miktar"].where(tedarikci["miktar"] > 0)
    toplam = {"miktar": float(s["miktar"].sum()), "net_tutar": float(s["net_tutar"].sum()),
              "kar": float(aylik["kar"].sum(min_count=1)) if aylik["kar"].notna().any() else None,
              "musteri": int(s["cari_ref"].nunique()), "fatura": int(s.loc[~s["iade"].astype(bool), "fatura_no"].nunique()),
              "stok": float(stok[0]) if stok and stok[0] is not None else None,
              "son_satis": s["tarih"].max() if len(s) else None}
    toplam["ort_fiyat"] = toplam["net_tutar"] / toplam["miktar"] if toplam["miktar"] > 0 else None
    mt = float(aylik["maliyetli_tutar"].sum())
    toplam["marj"] = toplam["kar"] / mt if toplam["kar"] is not None and mt > 0 else None

    ay_detay = None
    if ay and ay in set(aylik["yil_ay"]):
        ay_detay = _kirilim(s[s["yil_ay"] == ay], ["cari_kod", "cari_ad", "sehir"])
    return {"m": m.iloc[0].to_dict(), "aylik": aylik, "musteri": musteri, "tedarikci": tedarikci, "toplam": toplam,
            "ay": ay if ay_detay is not None else None, "ay_detay": ay_detay,
            "bulgular": _urun_bulgulari(s, aylik, musteri, toplam, aylar)}


def _urun_bulgulari(s, aylik, musteri, toplam, aylar) -> list[dict]:
    """Her bulgu: {'tur': 'bilgi'|'dikkat'|'iyi', 'metin': str, 'ay': 'YYYY-MM' ya da None}"""
    b = []
    if s.empty or toplam["miktar"] <= 0:
        return [{"tur": "bilgi", "metin": "Seçilen dönemde bu ürünün satışı yok.", "ay": None}]
    dolu = aylik[aylik["miktar"] > 0]
    # 1) en çok gittiği aylar ve nedeni
    for r in dolu.sort_values("miktar", ascending=False).head(3).itertuples():
        ay_s = s[s["yil_ay"] == r.yil_ay]
        mk = ay_s.groupby(["cari_kod", "cari_ad"])["miktar"].sum().sort_values(ascending=False)
        neden = []
        if len(mk) and r.miktar > 0:
            p1 = mk.iloc[0] / r.miktar
            if p1 >= 0.4:
                neden.append(f"tek müşterinin payı {_yz(p1)} ({mk.index[0][1] or mk.index[0][0]})")
            else:
                neden.append(f"{len(mk)} müşteriye yayılmış, en büyüğü {_yz(p1)}")
        if toplam["ort_fiyat"] and r.ort_fiyat == r.ort_fiyat:
            f = r.ort_fiyat / toplam["ort_fiyat"] - 1
            if f <= -0.05:
                neden.append(f"fiyat ortalamanın {_yz(-f)} altındaydı (iskonto/kampanya etkisi olabilir)")
            elif f >= 0.05:
                neden.append(f"fiyat ortalamanın {_yz(f)} üstünde olmasına rağmen")
        b.append({"tur": "bilgi", "ay": r.yil_ay,
                  "metin": f"{r.ay_ad}: {_tl(r.miktar)} birim, {_tl(r.net_tutar)} ₺ (toplam miktardaki payı {_yz(r.miktar / toplam['miktar'])})"
                           + (". " + "; ".join(neden) if neden else "") + "."})
    # 2) mevsimsellik
    mv = _mevsim(aylik, "miktar")
    if mv:
        b.append({"tur": "bilgi", "ay": None, "metin": "Mevsimsel: " + ", ".join(AYLAR[a - 1] for a in mv)
                  + " ayları her yıl ortalamanın belirgin üstünde. Bu aylardan önce stok hazırlamak gerekir."})
    # 3) müşteri yoğunlaşması
    if len(musteri):
        p1, p3 = musteri["pay"].iloc[0], musteri["pay"].head(3).sum()
        if p1 >= 0.5:
            b.append({"tur": "dikkat", "ay": None, "metin": f"Tek müşteriye bağlı (ciro payı {_yz(p1)}): "
                      f"{musteri['cari_ad'].iloc[0] or musteri['cari_kod'].iloc[0]}. Bu müşteri keserse ürünün cirosu yarıdan fazla düşer."})
        elif len(musteri) >= 5:
            b.append({"tur": "bilgi", "ay": None, "metin": f"{len(musteri)} müşteriye satılıyor; ilk 3 müşterinin ciro payı {_yz(p3)}."})
    # 4) son dönem eğilimi ve nedeni
    k = _donem_karsilastir(s, aylar, ["cari_kod", "cari_ad"])
    if k and k["once"] > 0:
        deg = k["son"] / k["once"] - 1
        if deg <= -0.2:
            d = k["dusen"][k["dusen"]["fark"] < 0].head(3)
            kim = ", ".join(f"{r.cari_ad or r.cari_kod} ({_tl(r.fark)} ₺)" for r in d.itertuples())
            kesen = int(((k["dusen"]["son"] <= 0) & (k["dusen"]["once"] > 0)).sum())
            b.append({"tur": "dikkat", "ay": None, "metin": f"Son {k['n']} ayda ciro önceki {k['n']} aya göre {_yz(-deg)} düştü "
                      f"({_tl(k['once'])} → {_tl(k['son'])} ₺). Düşüşün kaynağı: {kim}."
                      + (f" Bunlardan {kesen} müşteri bu ürünü almayı tamamen kesti." if kesen else "")})
        elif deg >= 0.2:
            d = k["artan"][k["artan"]["fark"] > 0].head(3)
            kim = ", ".join(f"{r.cari_ad or r.cari_kod} (+{_tl(r.fark)} ₺)" for r in d.itertuples())
            b.append({"tur": "iyi", "ay": None, "metin": f"Son {k['n']} ayda ciro önceki {k['n']} aya göre {_yz(deg)} arttı "
                      f"({_tl(k['once'])} → {_tl(k['son'])} ₺). Artışın kaynağı: {kim}."})
    elif k and k["once"] <= 0 < k["son"]:
        b.append({"tur": "iyi", "ay": None, "metin": f"Yeni hareketlenen ürün: önceki {k['n']} ayda satışı yokken son {k['n']} ayda {_tl(k['son'])} ₺."})
    # 5) uzun süredir satış yok
    bos = 0
    for v in reversed(aylik["miktar"].tolist()):
        if v > 0:
            break
        bos += 1
    if bos >= 3:
        b.append({"tur": "dikkat", "ay": None, "metin": f"Son {bos} aydır satışı yok (son satış {toplam['son_satis']:%d.%m.%Y})."
                  + (f" Elde {_tl(toplam['stok'])} birim stok duruyor." if toplam["stok"] and toplam["stok"] > 0 else "")})
    # 6) fiyat ve maliyet değişimi
    fy = dolu[dolu["ort_fiyat"].notna()]
    if len(fy) >= 6:
        ilk, son = fy["ort_fiyat"].head(3).mean(), fy["ort_fiyat"].tail(3).mean()
        fdeg = son / ilk - 1 if ilk > 0 else 0
        al = aylik[aylik["alis_fiyat"].notna()]
        mdeg = None
        if len(al) >= 2:
            a1, a2 = al["alis_fiyat"].head(max(1, len(al) // 3)).mean(), al["alis_fiyat"].tail(max(1, len(al) // 3)).mean()
            mdeg = a2 / a1 - 1 if a1 > 0 else None
        if mdeg is not None and mdeg - fdeg >= 0.08:
            b.append({"tur": "dikkat", "ay": None, "metin": f"Marj eriyor: alış fiyatı {_yz(mdeg)} artarken satış fiyatı "
                      f"{'%' + format(fdeg * 100, '.0f') + ' arttı' if fdeg >= 0 else _yz(-fdeg) + ' düştü'} "
                      f"({_tl(ilk, 2)} → {_tl(son, 2)} ₺). Satış fiyatı maliyet artışının gerisinde kalmış."})
        elif abs(fdeg) >= 0.1:
            b.append({"tur": "bilgi", "ay": None, "metin": f"Ortalama satış fiyatı dönem başından sonuna "
                      f"{_yz(abs(fdeg))} {'arttı' if fdeg > 0 else 'düştü'} ({_tl(ilk, 2)} → {_tl(son, 2)} ₺)."})
    # 7) fiyat farkı: en ucuza alan önemli müşteri
    onemli = musteri[(musteri["pay"] >= 0.03) & musteri["ort_fiyat"].notna()]
    if len(onemli) >= 3 and toplam["ort_fiyat"]:
        en = onemli.sort_values("ort_fiyat").iloc[0]
        f = en["ort_fiyat"] / toplam["ort_fiyat"] - 1
        if f <= -0.1:
            b.append({"tur": "dikkat", "ay": None, "metin": f"{en['cari_ad'] or en['cari_kod']} bu ürünü ortalamanın {_yz(-f)} altında alıyor "
                      f"({_tl(en['ort_fiyat'], 2)} ₺; genel ortalama {_tl(toplam['ort_fiyat'], 2)} ₺)."})
    if toplam["marj"] is not None and toplam["marj"] < 0:
        b.append({"tur": "dikkat", "ay": None, "metin": "Bu ürün dönem toplamında maliyetinin altında satılmış görünüyor (tahmini kâr eksi)."})
    return b


# ---------------------------------------------------------------------------- cari kartı
def cari_karti(depo: str, kod: str, ay: str | None = None) -> dict | None:
    con = sqlite3.connect(depo)
    try:
        c = pd.read_sql_query("SELECT * FROM dim_cari WHERE cari_kod = ?", con, params=(kod,))
        if c.empty:
            return None
        ref = int(c.iloc[0]["cari_ref"])
        s = _hazirla(_oku(con, "SELECT s.tarih, s.malzeme_ref, s.miktar, s.net_tutar, s.maliyet_tutar, s.fatura_no, s.iade, "
                               "m.malzeme_kod, m.malzeme_ad, m.grup_kod FROM fact_satis s LEFT JOIN dim_malzeme m USING (malzeme_ref) "
                               "WHERE s.cari_ref = ?", (ref,)))
        al = _oku(con, "SELECT a.tarih, a.miktar, a.net_tutar, a.fatura_no, m.malzeme_kod, m.malzeme_ad FROM fact_alis a "
                       "LEFT JOIN dim_malzeme m USING (malzeme_ref) WHERE a.cari_ref = ?", (ref,))
        aylar = _tum_aylar(con)
        # firma geneli ortalama fiyat: bu carinin aldığı ürünlerde, diğer müşterilere satış ortalaması
        genel = pd.read_sql_query(
            "SELECT malzeme_ref, SUM(net_tutar) t, SUM(miktar) q FROM fact_satis WHERE cari_ref <> ? AND malzeme_ref IN "
            "(SELECT DISTINCT malzeme_ref FROM fact_satis WHERE cari_ref = ?) GROUP BY malzeme_ref", con, params=(ref, ref))
        haric = False
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='haric'").fetchone():
            haric = con.execute("SELECT 1 FROM haric WHERE tur='cari' AND anahtar = ?", (kod,)).fetchone() is not None
        if haric and s.empty and con.execute("SELECT 1 FROM sqlite_master WHERE name='fact_satis_tum'").fetchone():
            pass   # hariç tutulan carinin kartında satış görünmez; uyarı gösterilir
    finally:
        con.close()
    for c2 in ("malzeme_kod", "malzeme_ad", "grup_kod"):
        if c2 in s.columns:
            s[c2] = s[c2].fillna("")
    aylik = _aylik(s, aylar, "malzeme_ref")
    urun = _kirilim(s, ["malzeme_ref", "malzeme_kod", "malzeme_ad", "grup_kod"])
    genel["genel_fiyat"] = genel["t"] / genel["q"].where(genel["q"] > 0)
    urun = urun.merge(genel[["malzeme_ref", "genel_fiyat"]], on="malzeme_ref", how="left")
    urun["fiyat_farki"] = urun["ort_fiyat"] / urun["genel_fiyat"].where(urun["genel_fiyat"] > 0) - 1
    grup = _kirilim(s.assign(grup_kod=s["grup_kod"].replace("", "(grupsuz)")), ["grup_kod"])
    alis_urun = (al.groupby(["malzeme_kod", "malzeme_ad"], as_index=False).agg(miktar=("miktar", "sum"), net_tutar=("net_tutar", "sum"))
                 .sort_values("net_tutar", ascending=False))
    mt = float(aylik["maliyetli_tutar"].sum())
    kar = float(aylik["kar"].sum(min_count=1)) if aylik["kar"].notna().any() else None
    toplam = {"net_tutar": float(s["net_tutar"].sum()), "kar": kar, "marj": kar / mt if kar is not None and mt > 0 else None,
              "urun": int(s["malzeme_ref"].nunique()), "fatura": int(s.loc[~s["iade"].astype(bool), "fatura_no"].nunique()),
              "iade": float(-s.loc[s["iade"].astype(bool), "net_tutar"].sum()), "alis": float(al["net_tutar"].sum()),
              "son_alim": s["tarih"].max() if len(s) else None, "ilk_alim": s["tarih"].min() if len(s) else None}
    toplam["ort_fatura"] = toplam["net_tutar"] / toplam["fatura"] if toplam["fatura"] else None
    ay_detay = None
    if ay and ay in set(aylik["yil_ay"]):
        ay_detay = _kirilim(s[s["yil_ay"] == ay], ["malzeme_ref", "malzeme_kod", "malzeme_ad", "grup_kod"])
    return {"c": c.iloc[0].to_dict(), "aylik": aylik, "urun": urun, "grup": grup, "alis_urun": alis_urun, "toplam": toplam,
            "ay": ay if ay_detay is not None else None, "ay_detay": ay_detay, "haric": haric,
            "bulgular": _cari_bulgulari(s, aylik, urun, toplam, aylar)}


def _cari_bulgulari(s, aylik, urun, toplam, aylar) -> list[dict]:
    b = []
    if s.empty:
        return [{"tur": "bilgi", "metin": "Seçilen dönemde bu cariye satış yok.", "ay": None}]
    dolu = aylik[aylik["net_tutar"] > 0]
    for r in dolu.sort_values("net_tutar", ascending=False).head(3).itertuples():
        ay_s = s[s["yil_ay"] == r.yil_ay]
        mk = ay_s.groupby(["malzeme_kod", "malzeme_ad"])["net_tutar"].sum().sort_values(ascending=False)
        ek = ""
        if len(mk) and r.net_tutar > 0:
            p1 = mk.iloc[0] / r.net_tutar
            ek = (f" Tek ürünün payı {_yz(p1)}: {mk.index[0][1] or mk.index[0][0]}." if p1 >= 0.4
                  else f" {len(mk)} ürün; en büyüğü {mk.index[0][1] or mk.index[0][0]} ({_yz(p1)}).")
        b.append({"tur": "bilgi", "ay": r.yil_ay, "metin": f"{r.ay_ad}: {_tl(r.net_tutar)} ₺ (dönem cirosundaki payı "
                  f"{_yz(r.net_tutar / toplam['net_tutar']) if toplam['net_tutar'] > 0 else '%0'}).{ek}"})
    mv = _mevsim(aylik, "net_tutar")
    if mv:
        b.append({"tur": "bilgi", "ay": None, "metin": "Mevsimsel alım: " + ", ".join(AYLAR[a - 1] for a in mv) + " ayları her yıl güçlü."})
    k = _donem_karsilastir(s, aylar, ["malzeme_kod", "malzeme_ad"])
    if k and k["once"] > 0:
        deg = k["son"] / k["once"] - 1
        if deg <= -0.2:
            d = k["dusen"][k["dusen"]["fark"] < 0].head(3)
            ne = ", ".join(f"{r.malzeme_ad or r.malzeme_kod} ({_tl(r.fark)} ₺)" for r in d.itertuples())
            b.append({"tur": "dikkat", "ay": None, "metin": f"Son {k['n']} ayda alımı önceki {k['n']} aya göre {_yz(-deg)} düştü "
                      f"({_tl(k['once'])} → {_tl(k['son'])} ₺). En çok düşen ürünler: {ne}."})
        elif deg >= 0.2:
            d = k["artan"][k["artan"]["fark"] > 0].head(3)
            ne = ", ".join(f"{r.malzeme_ad or r.malzeme_kod} (+{_tl(r.fark)} ₺)" for r in d.itertuples())
            b.append({"tur": "iyi", "ay": None, "metin": f"Son {k['n']} ayda alımı önceki {k['n']} aya göre {_yz(deg)} arttı "
                      f"({_tl(k['once'])} → {_tl(k['son'])} ₺). En çok artan ürünler: {ne}."})
    bos = 0
    for v in reversed(aylik["net_tutar"].tolist()):
        if v > 0:
            break
        bos += 1
    if bos >= 3:
        b.append({"tur": "dikkat", "ay": None, "metin": f"Son {bos} aydır alım yapmıyor (son alım {toplam['son_alim']:%d.%m.%Y})."})
    ucuz = urun[(urun["fiyat_farki"] <= -0.1) & (urun["pay"] >= 0.02)]
    if len(ucuz):
        kayip = float(((ucuz["genel_fiyat"] - ucuz["ort_fiyat"]) * ucuz["miktar"]).sum())
        b.append({"tur": "dikkat", "ay": None, "metin": f"{len(ucuz)} üründe diğer müşterilerin ortalamasından %10+ ucuza alıyor "
                  f"(ör. {ucuz.iloc[0]['malzeme_ad'] or ucuz.iloc[0]['malzeme_kod']}: {_yz(-ucuz.iloc[0]['fiyat_farki'])} altında). "
                  f"Ortalama fiyattan satılsaydı ciro yaklaşık {_tl(kayip)} ₺ daha fazla olurdu."})
    if toplam["marj"] is not None and toplam["marj"] < 0.05 and toplam["net_tutar"] > 0:
        b.append({"tur": "dikkat", "ay": None, "metin": f"Bu müşteriden tahmini kâr marjı çok düşük ({_yz(toplam['marj'])})."})
    if toplam["net_tutar"] > 0 and toplam["iade"] / (toplam["net_tutar"] + toplam["iade"]) >= 0.08:
        b.append({"tur": "dikkat", "ay": None, "metin": f"İade oranı yüksek: {_tl(toplam['iade'])} ₺ "
                  f"({_yz(toplam['iade'] / (toplam['net_tutar'] + toplam['iade']))})."})
    return b


# ---------------------------------------------------------------------------- ay detayı
def ay_detayi(depo: str, ay: str) -> dict | None:
    con = sqlite3.connect(depo)
    try:
        aylar = _tum_aylar(con)
        if ay not in aylar:
            return None
        i = aylar.index(ay)
        onceki = aylar[i - 1] if i > 0 else None
        gecen_yil = f"{int(ay[:4]) - 1}{ay[4:]}"
        gecen_yil = gecen_yil if gecen_yil in aylar else None
        istenen = [x for x in (ay, onceki, gecen_yil) if x]
        s = _hazirla(_oku(con, "SELECT s.tarih, s.cari_ref, s.malzeme_ref, s.miktar, s.net_tutar, s.maliyet_tutar, s.fatura_no, s.iade, "
                               "c.cari_kod, c.cari_ad, c.sehir, m.malzeme_kod, m.malzeme_ad, m.grup_kod FROM fact_satis s "
                               "LEFT JOIN dim_cari c USING (cari_ref) LEFT JOIN dim_malzeme m USING (malzeme_ref) "
                               f"WHERE substr(s.tarih, 1, 7) IN ({','.join('?' * len(istenen))})", tuple(istenen)))
    finally:
        con.close()
    for c in ("cari_kod", "cari_ad", "sehir", "malzeme_kod", "malzeme_ad", "grup_kod"):
        s[c] = s[c].fillna("")
    bu = s[s["yil_ay"] == ay]
    cari = _kirilim(bu, ["cari_kod", "cari_ad", "sehir"])
    urun = _kirilim(bu, ["malzeme_kod", "malzeme_ad", "grup_kod"])

    def kiyas(diger):
        if not diger:
            return None
        d = s[s["yil_ay"] == diger]
        a, b = bu.groupby(["cari_kod", "cari_ad"])["net_tutar"].sum(), d.groupby(["cari_kod", "cari_ad"])["net_tutar"].sum()
        f = a.sub(b, fill_value=0.0).rename("fark").reset_index()
        return {"ay": diger, "ay_ad": ay_ad(diger), "toplam": float(d["net_tutar"].sum()),
                "artan": f[f["fark"] > 0].sort_values("fark", ascending=False).head(8),
                "dusen": f[f["fark"] < 0].sort_values("fark").head(8)}
    mt = float(bu["_mt"].sum())
    kar = float(mt - bu["maliyet_tutar"].sum()) if bu["maliyet_tutar"].notna().any() else None
    toplam = {"net_tutar": float(bu["net_tutar"].sum()), "kar": kar, "marj": kar / mt if kar is not None and mt > 0 else None,
              "musteri": int(bu["cari_ref"].nunique()), "urun": int(bu["malzeme_ref"].nunique()),
              "fatura": int(bu.loc[~bu["iade"].astype(bool), "fatura_no"].nunique())}
    gun = bu.groupby(bu["tarih"].dt.day)["net_tutar"].sum()
    return {"ay": ay, "ay_ad": ay_ad(ay), "cari": cari, "urun": urun, "toplam": toplam, "onceki": kiyas(onceki),
            "gecen_yil": kiyas(gecen_yil), "aylar": aylar, "onceki_ay": onceki, "sonraki_ay": aylar[i + 1] if i + 1 < len(aylar) else None,
            "bas": f"{ay}-01", "bit": f"{(pd.Period(ay, freq='M').end_time):%Y-%m-%d}",
            "gunluk": [(int(g), float(v)) for g, v in gun.items()]}


def ara(depo: str, q: str, sinir: int = 30) -> dict:
    """Ürün ve cari arama (kod ya da ad içinde; Türkçe büyük/küçük harf duyarsız)."""
    con = sqlite3.connect(depo)
    try:
        m = pd.read_sql_query("SELECT malzeme_kod AS kod, malzeme_ad AS ad, grup_kod AS ek FROM dim_malzeme", con)
        c = pd.read_sql_query("SELECT cari_kod AS kod, cari_ad AS ad, sehir AS ek FROM dim_cari", con)
    finally:
        con.close()
    k = lambda x: x.fillna("").astype(str).str.replace("I", "ı").str.replace("İ", "i").str.lower()  # noqa: E731
    a = q.replace("I", "ı").replace("İ", "i").lower().strip()
    out = {}
    for ad, d in (("urun", m), ("cari", c)):
        d = d[d["kod"].fillna("") != ""]
        uy = d[k(d["kod"]).str.contains(a, regex=False) | k(d["ad"]).str.contains(a, regex=False)] if a else d.iloc[0:0]
        out[ad] = uy.head(sinir)
        out[ad + "_say"] = len(uy)
    return out
