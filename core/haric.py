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
    """Hariç tutulanlar: tur, anahtar, aciklama, zaman, ad (cari adı ya da fatura no), ek (fatura: tarih · cari),
    tutar (düşülen net satış + alış). Çok sayıda faturada da hızlıdır (toplu sorgu)."""
    bos = pd.DataFrame(columns=["tur", "anahtar", "aciklama", "zaman", "ad", "ek", "tutar"])
    if "haric" not in _tablolar(con):
        return bos
    h = pd.read_sql_query("SELECT tur, anahtar, aciklama, zaman FROM haric ORDER BY zaman DESC, anahtar", con)
    if h.empty:
        return bos
    birlesik = " UNION ALL ".join(f"SELECT cari_ref, donem, fatura_ref, net_tutar FROM {f}_tum" for f in FAKTLAR)
    c = pd.read_sql_query(
        "SELECT c.cari_kod AS anahtar, MAX(c.cari_ad) AS ad, '' AS ek, COALESCE(SUM(t.net_tutar), 0) AS tutar FROM dim_cari c "
        f"LEFT JOIN ({birlesik}) t ON t.cari_ref = c.cari_ref "
        "WHERE c.cari_kod IN (SELECT anahtar FROM haric WHERE tur = 'cari') GROUP BY c.cari_kod", con).assign(tur="cari")
    f = pd.read_sql_query(
        "SELECT CAST(d.donem AS TEXT) || ':' || CAST(d.fatura_ref AS TEXT) AS anahtar, d.fatura_no AS ad, "
        "substr(d.tarih, 1, 10) || ' · ' || COALESCE(c.cari_ad, '') AS ek FROM dim_fatura d "
        "LEFT JOIN dim_cari c ON c.cari_ref = d.cari_ref "
        "WHERE CAST(d.donem AS TEXT) || ':' || CAST(d.fatura_ref AS TEXT) IN (SELECT anahtar FROM haric WHERE tur = 'fatura')", con)
    ft = pd.read_sql_query(
        f"SELECT CAST(donem AS TEXT) || ':' || CAST(fatura_ref AS TEXT) AS anahtar, SUM(net_tutar) AS tutar FROM ({birlesik}) "
        "WHERE CAST(donem AS TEXT) || ':' || CAST(fatura_ref AS TEXT) IN (SELECT anahtar FROM haric WHERE tur = 'fatura') "
        "GROUP BY 1", con)
    f = f.merge(ft, on="anahtar", how="left").assign(tur="fatura")
    h = h.merge(pd.concat([c, f], ignore_index=True), on=["tur", "anahtar"], how="left")
    h["ad"], h["ek"], h["tutar"] = h["ad"].fillna(""), h["ek"].fillna(""), h["tutar"].fillna(0.0).astype(float)
    return h


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


def toplu(depo: str, islem: str, tur: str, anahtarlar, aciklama: str = "") -> int:
    """Birçok kaydı tek seferde hariç tutar ('ekle') ya da geri alır ('cikar'); tablolar bir kez yeniden yazılır.
    Var olmayan anahtarlar atlanır. Dönen değer: işlenen kayıt sayısı."""
    if tur not in TURLER or islem not in ("ekle", "cikar"):
        return 0
    anahtarlar = sorted({str(a).strip() for a in anahtarlar if str(a).strip()})
    if not anahtarlar:
        return 0
    con = sqlite3.connect(depo)
    try:
        if not destekli_mi(con):
            return 0
        _hazirla(con)
        if tur == "cari":
            gecerli = {r[0] for r in con.execute("SELECT cari_kod FROM dim_cari WHERE cari_kod <> ''")}
        else:
            gecerli = {r[0] for r in con.execute("SELECT CAST(donem AS TEXT) || ':' || CAST(fatura_ref AS TEXT) FROM dim_fatura")}
        anahtarlar = [a for a in anahtarlar if a in gecerli]
        zaman = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}"
        if islem == "ekle":
            con.executemany("INSERT OR REPLACE INTO haric (tur, anahtar, aciklama, zaman) VALUES (?, ?, ?, ?)",
                            [(tur, a, (aciklama or "").strip()[:200], zaman) for a in anahtarlar])
        else:
            con.executemany("DELETE FROM haric WHERE tur = ? AND anahtar = ?", [(tur, a) for a in anahtarlar])
        uygula(con)
        return len(anahtarlar)
    finally:
        con.close()


def temizle(depo: str, tur: str) -> int:
    """Bir türdeki (cari / fatura) tüm hariç kayıtlarını geri alır."""
    con = sqlite3.connect(depo)
    try:
        if "haric" not in _tablolar(con) or tur not in TURLER:
            return 0
        n = con.execute("DELETE FROM haric WHERE tur = ?", (tur,)).rowcount
        uygula(con)
        return n
    finally:
        con.close()
