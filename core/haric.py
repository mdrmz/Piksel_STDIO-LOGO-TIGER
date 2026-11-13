"""
Hesaplama dışı bırakma: seçilen cariler ve faturalar tüm analizlerden çıkarılır.

Örnek: grup içi satışlar, ortak carisi, tek seferlik demirbaş satışı, hatalı kesilip iadesi sonraki yıla kalan fatura.

Nasıl çalışır: özgün satırlar fact_satis_tum / fact_alis_tum tablolarında saklanır; fact_satis / fact_alis her
değişiklikte bu tablolardan hariç tutulanlar düşülerek yeniden yazılır. Böylece panel, ABC, kârlılık, Excel,
Power BI paketi ve yapay zekâ sorguları aynı (süzülmüş) veriyi görür. Hiçbir şey silinmez; listeden çıkarınca geri gelir.
Stok bakiyesi ve cari bakiye/alacak (Logo'daki gerçek durum) bundan etkilenmez.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pandas as pd

TURLER = ("cari", "fatura")
FAKTLAR = ("fact_satis", "fact_alis")


def _tablolar(con) -> set:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def destekli_mi(con) -> bool:
    """Fatura referansları olan (v0.7.0+) analizlerde çalışır."""
    return {"dim_fatura", "fatura_satir"} <= _tablolar(con)


def _hazirla(con):
    var = _tablolar(con)
    con.execute("CREATE TABLE IF NOT EXISTS haric (tur TEXT NOT NULL, anahtar TEXT NOT NULL, aciklama TEXT, "
                "zaman TEXT, PRIMARY KEY (tur, anahtar))")
    for t in FAKTLAR:
        if f"{t}_tum" not in var:
            con.execute(f"CREATE TABLE {t}_tum AS SELECT * FROM {t}")


def liste(con) -> pd.DataFrame:
    """Hariç tutulanlar: tur, anahtar, aciklama, zaman, ad (cari adı ya da fatura no), tutar (düşülen net satış + alış)."""
    bos = pd.DataFrame(columns=["tur", "anahtar", "aciklama", "zaman", "ad", "tutar"])
    if "haric" not in _tablolar(con):
        return bos
    h = pd.read_sql_query("SELECT tur, anahtar, aciklama, zaman FROM haric ORDER BY zaman DESC", con)
    if h.empty:
        return bos
    adlar, tutarlar = [], []
    for r in h.itertuples():
        if r.tur == "cari":
            x = con.execute("SELECT cari_ad FROM dim_cari WHERE cari_kod = ?", (r.anahtar,)).fetchone()
            t = sum(con.execute(f"SELECT COALESCE(SUM(net_tutar), 0) FROM {f}_tum WHERE cari_ref IN "
                                "(SELECT cari_ref FROM dim_cari WHERE cari_kod = ?)", (r.anahtar,)).fetchone()[0]
                    for f in FAKTLAR)
        else:
            donem, _, ref = r.anahtar.partition(":")
            x = con.execute("SELECT fatura_no FROM dim_fatura WHERE CAST(donem AS TEXT) = ? AND fatura_ref = ?",
                            (donem, int(ref))).fetchone()
            t = sum(con.execute(f"SELECT COALESCE(SUM(net_tutar), 0) FROM {f}_tum WHERE CAST(donem AS TEXT) = ? "
                                "AND fatura_ref = ?", (donem, int(ref))).fetchone()[0] for f in FAKTLAR)
        adlar.append(x[0] if x else "")
        tutarlar.append(float(t or 0))
    return h.assign(ad=adlar, tutar=tutarlar)


def anahtarlar(con) -> tuple[set, set]:
    """(hariç cari kodları, hariç fatura anahtarları 'donem:ref')"""
    if "haric" not in _tablolar(con):
        return set(), set()
    c, f = set(), set()
    for tur, anahtar in con.execute("SELECT tur, anahtar FROM haric"):
        (c if tur == "cari" else f).add(anahtar)
    return c, f


def uygula(con):
    """fact_satis / fact_alis tablolarını hariç listesine göre yeniden yazar ve özetini meta'ya işler."""
    _hazirla(con)
    kosul = ("cari_ref NOT IN (SELECT c.cari_ref FROM dim_cari c JOIN haric h ON h.tur = 'cari' AND h.anahtar = c.cari_kod) "
             "AND (CAST(donem AS TEXT) || ':' || CAST(fatura_ref AS TEXT)) NOT IN (SELECT anahtar FROM haric WHERE tur = 'fatura')")
    dusen = {}
    for t in FAKTLAR:
        con.execute(f"DROP TABLE IF EXISTS {t}")
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM {t}_tum WHERE {kosul}")
        tum = con.execute(f"SELECT COUNT(*), COALESCE(SUM(net_tutar), 0) FROM {t}_tum").fetchone()
        kalan = con.execute(f"SELECT COUNT(*), COALESCE(SUM(net_tutar), 0) FROM {t}").fetchone()
        dusen[t] = (tum[0] - kalan[0], tum[1] - kalan[1])
    n = dict(con.execute("SELECT tur, COUNT(*) FROM haric GROUP BY tur").fetchall())
    for k, v in {"haric_cari": n.get("cari", 0), "haric_fatura": n.get("fatura", 0),
                 "haric_satis_tutar": round(dusen["fact_satis"][1], 2), "haric_alis_tutar": round(dusen["fact_alis"][1], 2),
                 "haric_satir": dusen["fact_satis"][0] + dusen["fact_alis"][0]}.items():
        con.execute("DELETE FROM meta WHERE anahtar = ?", (k,))
        con.execute("INSERT INTO meta (anahtar, deger) VALUES (?, ?)", (k, str(v)))
    con.commit()


def ekle(depo: str, tur: str, anahtar: str, aciklama: str = "") -> str | None:
    """Hata varsa mesajını döner."""
    if tur not in TURLER:
        return "Geçersiz tür."
    anahtar = (anahtar or "").strip()
    con = sqlite3.connect(depo)
    try:
        if not destekli_mi(con):
            return "Bu analiz eski sürümle oluşturulmuş; yedeği yeniden açıp analizi baştan oluşturun."
        if tur == "cari":
            if not con.execute("SELECT 1 FROM dim_cari WHERE cari_kod = ? AND cari_kod <> ''", (anahtar,)).fetchone():
                return f"Bu kodda cari yok: {anahtar}"
        else:
            donem, _, ref = anahtar.partition(":")
            if not ref.lstrip("-").isdigit() or not con.execute(
                    "SELECT 1 FROM dim_fatura WHERE CAST(donem AS TEXT) = ? AND fatura_ref = ?", (donem, int(ref))).fetchone():
                return "Fatura bulunamadı."
        _hazirla(con)
        con.execute("INSERT OR REPLACE INTO haric (tur, anahtar, aciklama, zaman) VALUES (?, ?, ?, ?)",
                    (tur, anahtar, (aciklama or "").strip()[:200], f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}"))
        uygula(con)
    finally:
        con.close()
    return None


def cikar(depo: str, tur: str, anahtar: str):
    con = sqlite3.connect(depo)
    try:
        if "haric" in _tablolar(con):
            con.execute("DELETE FROM haric WHERE tur = ? AND anahtar = ?", (tur, anahtar))
            uygula(con)
    finally:
        con.close()
