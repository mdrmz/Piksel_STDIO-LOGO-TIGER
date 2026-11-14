"""
Veri kontrolü: Logo ile farklı çıkan faturalar (nedeniyle) ve ekranda boş / "nan" görünmesine yol açan eksik veriler.

- logo_farklari(): her faturada Logo'nun başlık toplamı (NETTOTAL - TOTALVAT) ile satırlardan hesaplanan KDV hariç
  tutar arasındaki farkı, faturanın satır tiplerine bakarak açıklar (masraf satırı, iptal satırı, dağıtılmamış
  indirim, KDV tevkifatı, ek vergi, satırı olmayan fatura, yuvarlama…).
- eksik_veriler(): carisi seçilmemiş satışlar, kartı bulunamayan cari/malzeme, maliyeti bulunamayan satışlar, boş
  grup/şehir, tarihi okunamayan satırlar… Her biri için kaç kayıt, ne kadar tutar ve ekranda neye yol açtığı.
- kartsiz_onar(): satışta/alışta geçen ama kartı yedekte olmayan cari ve malzemeler için analiz deposuna açıklayıcı
  yer tutucu kart ekler. Böylece listelerde "nan" yerine "(Cari seçilmemiş — perakende/kasa satışı)" gibi bir ad görünür.
"""
from __future__ import annotations

import itertools
import sqlite3

import numpy as np
import pandas as pd

CARISIZ_KOD = "CARİSİZ"
KARTSIZ_ONEK = "KARTSIZ-"


def _tablolar(con) -> set:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _kolonlar(con, tablo) -> list[tuple[str, str]]:
    return [(r[1], (r[2] or "").upper()) for r in con.execute(f"PRAGMA table_info({tablo})")]


# ---------------------------------------------------------------------------- kartı olmayan cari / malzeme
def kartsiz_onar(depo: str) -> dict:
    """Satış/alış/fatura/stok tablolarında geçip dim_cari / dim_malzeme'de olmayan referanslar için yer tutucu kart
    ekler (bir kez; tekrar çağrılınca bir şey yapmaz). Dönen: {'cari': eklenen sayı, 'malzeme': eklenen sayı}."""
    con = sqlite3.connect(depo, timeout=30)
    eklenen = {"cari": 0, "malzeme": 0}
    try:
        var = _tablolar(con)
        if not {"dim_cari", "dim_malzeme"} <= var:
            return eklenen
        hedefler = {
            "cari": ("dim_cari", "cari_ref", "cari_kod", "cari_ad",
                     [t for t in ("fact_satis", "fact_alis", "dim_fatura", "fact_cari_hareket", "fact_satis_tum", "fact_alis_tum")
                      if t in var]),
            "malzeme": ("dim_malzeme", "malzeme_ref", "malzeme_kod", "malzeme_ad",
                        [t for t in ("fact_satis", "fact_alis", "stok_bakiye", "stok_ambar", "fact_satis_tum", "fact_alis_tum")
                         if t in var]),
        }
        for tur, (dim, ref, kod, ad, kaynaklar) in hedefler.items():
            if not kaynaklar:
                continue
            birlesik = " UNION ".join(f"SELECT DISTINCT {ref} AS r FROM {t}" for t in kaynaklar)
            eksik = [r[0] for r in con.execute(
                f"SELECT r FROM ({birlesik}) WHERE r IS NOT NULL AND r NOT IN (SELECT {ref} FROM {dim} WHERE {ref} IS NOT NULL)")]
            if not eksik:
                continue
            kolonlar = _kolonlar(con, dim)
            satirlar = []
            for r in eksik:
                r = int(r)
                if tur == "cari":
                    k = CARISIZ_KOD if r == 0 else f"{KARTSIZ_ONEK}C{r}"
                    a = "(Cari seçilmemiş — perakende/kasa satışı)" if r == 0 else f"(Cari kartı yedekte yok — Logo ref {r})"
                else:
                    k = f"{KARTSIZ_ONEK}M{r}"
                    a = "(Malzeme seçilmemiş satır)" if r == 0 else f"(Malzeme kartı yedekte yok — Logo ref {r})"
                deger = {}
                for c, tip in kolonlar:
                    if c == ref:
                        deger[c] = r
                    elif c == kod:
                        deger[c] = k
                    elif c == ad:
                        deger[c] = a
                    elif "INT" in tip or "REAL" in tip or "NUM" in tip:
                        deger[c] = 0
                    else:
                        deger[c] = ""
                satirlar.append(tuple(deger[c] for c, _ in kolonlar))
            con.executemany(f"INSERT INTO {dim} ({', '.join(c for c, _ in kolonlar)}) VALUES ({', '.join('?' * len(kolonlar))})",
                            satirlar)
            eklenen[tur] = len(satirlar)
        if eklenen["cari"] or eklenen["malzeme"]:
            con.commit()
    except sqlite3.Error:
        con.rollback()   # salt-okur ya da kilitli depo: onarım yapılmaz, ekranda yine boş görünür
    finally:
        con.close()
    return eklenen


# ---------------------------------------------------------------------------- Logo ile farklı faturalar
NEDEN_ACIKLAMA = {
    "satirsiz": ("Faturanın satırı yedekte yok",
                 "Logo'da fatura başlığı var ama bu yedekte bağlı hiçbir stok satırı yok (satırları silinmiş, başka "
                 "dönemde ya da hizmet/masraf kartıyla girilmiş). Program bu faturayı ciroya 0 ₺ katıyor.", "Ciroda eksik"),
    "masraf": ("Masraf satırı",
               "Faturada masraf satırı (nakliye, ambalaj vb.) var. Logo bunu fatura toplamına katar; program ürün satışı "
               "olmadığı için ciroya saymaz.", "Bilerek dışarıda"),
    "iptal_satir": ("İptal edilmiş satır",
                    "Faturanın bir ya da birkaç satırı iptal edilmiş ama Logo'nun başlık toplamı güncellenmemiş. Program "
                    "iptal satırı saymıyor; doğru olan programdaki tutar.", "Program doğru"),
    "indirim": ("Dağıtılmamış dip indirim",
                "Faturadaki indirim satırı ürün satırlarına dağıtılmamış (satır KDV matrahları indirimsiz). Logo toplamı "
                "indirimli, program satırları indirimsiz topluyor. Logo'da faturayı açıp kaydedince genelde düzelir.",
                "Ciro fazla"),
    "tevkifat": ("KDV tevkifatı",
                 "Tevkifatlı fatura: Logo genel toplamından tevkif edilen KDV düşülmüş, KDV alanı ise tam yazılmış. "
                 "Bu yüzden 'genel toplam − KDV' eksik çıkıyor; KDV hariç tutar aslında aynı.", "Etkisi yok"),
    "ek_vergi": ("Ek vergi (ÖTV vb.)",
                 "Faturada ek vergi var; Logo genel toplamına giriyor, KDV hariç ürün tutarına girmiyor.", "Etkisi yok"),
    "logo_masraf": ("Başlıkta masraf",
                    "Faturanın başlığında (satır değil) masraf tutarı var; Logo toplamına girer, satırlarda yok.",
                    "Bilerek dışarıda"),
    "depozito": ("Depozito satırı",
                 "Faturada depozito satırı var; Logo ile program bunu farklı topluyor.", "Kontrol edin"),
    "promosyon": ("Promosyon satırı",
                  "Faturada promosyon satırı var; Logo ile program bunu farklı topluyor.", "Kontrol edin"),
    "yuvarlama": ("Yuvarlama",
                  "Çok satırlı faturada satır satır kuruş yuvarlamaları birikmiş. Önemsiz.", "Önemsiz"),
    "bilinmiyor": ("Açıklanamadı",
                   "Fark satır tiplerinden açıklanamadı. Logo'da faturayı açıp (tablo + LOGICALREF) satır toplamını "
                   "başlıkla karşılaştırın: döviz/kur, elle değiştirilmiş toplam ya da sonradan düzenlenmiş satır olabilir.",
                   "Kontrol edin"),
}


def _bilesenler(v) -> pd.DataFrame:
    """Fatura başına satır tiplerine göre tutarlar (iptal faturalar hariç)."""
    s = v.satirlar
    iptal = s["durum"] == "İptal"
    d = s.assign(
        _masraf=s["net_tutar"].where((s["satir_tipi"] == "Masraf") & ~iptal, 0.0),
        _indirim=s["net_tutar"].where((s["satir_tipi"] == "İndirim") & ~iptal, 0.0),
        _iptal=s["net_tutar"].where(iptal & ~s["satir_tipi"].isin(["İndirim", "Masraf"]), 0.0),
        _depozito=s["net_tutar"].where((s["satir_tipi"] == "Depozito") & ~iptal, 0.0),
        _promosyon=s["net_tutar"].where((s["satir_tipi"] == "Promosyon") & ~iptal, 0.0))
    return d.groupby(["donem", "fatura_ref"], as_index=False)[
        ["_masraf", "_indirim", "_iptal", "_depozito", "_promosyon"]].sum()


def _neden(r) -> tuple[str, str]:
    """(neden kodu, hesap açıklaması). r: fatura satırı + bileşenler."""
    fark = float(r["fark"])
    tol = max(1.0, 0.01 * float(r["satir_say"] or 0))
    if int(r["satir_say"] or 0) == 0:
        return "satirsiz", "satır yok"
    aday = []
    if abs(r["_masraf"]) > 0.005:
        aday.append(("masraf", r["_masraf"], f"masraf satırı {r['_masraf']:,.2f}"))
    if abs(r["_iptal"]) > 0.005:
        aday.append(("iptal_satir", r["_iptal"], f"iptal satırları {r['_iptal']:,.2f}"))
    if abs(r["_indirim"]) > 0.005:
        aday.append(("indirim", -abs(r["_indirim"]), f"indirim satırı −{abs(r['_indirim']):,.2f}"))
    if abs(r["_depozito"]) > 0.005:
        aday.append(("depozito", -r["_depozito"], f"depozito satırı {r['_depozito']:,.2f}"))
    if abs(r["_promosyon"]) > 0.005:
        aday.append(("promosyon", -r["_promosyon"], f"promosyon satırı {r['_promosyon']:,.2f}"))
    li = r.get("logo_indirim")
    if li is not None and pd.notna(li) and abs(li) > 0.005 and abs(abs(li) - abs(r["_indirim"])) > 0.005:
        aday.append(("indirim", -abs(float(li)), f"Logo toplam indirimi −{abs(float(li)):,.2f}"))
    for kolon, kod, ad in (("logo_ek_vergi", "ek_vergi", "ek vergi"), ("logo_masraf", "logo_masraf", "başlık masrafı")):
        x = r.get(kolon)
        if x is not None and pd.notna(x) and abs(x) > 0.005:
            aday.append((kod, float(x), f"{ad} {float(x):,.2f}"))
    kdv = r.get("logo_kdv")
    if kdv is not None and pd.notna(kdv) and kdv > 0.005:
        for pay in range(2, 11):
            aday.append(("tevkifat", -kdv * pay / 10, f"KDV {kdv:,.2f} × {pay}/10 tevkifat"))
    for n in (1, 2):
        for k in itertools.combinations(aday, n):
            if len({x[0] for x in k}) < n:
                continue
            if abs(fark - sum(x[1] for x in k)) <= tol:
                return k[0][0] if n == 1 else "+".join(x[0] for x in k), " + ".join(x[2] for x in k)
    matrah = abs(float(r["logo_matrah"] or 0))
    if abs(fark) <= 5 and (matrah == 0 or abs(fark) / matrah < 0.001):
        return "yuvarlama", f"{fark:,.2f} ₺ / {matrah:,.0f} ₺"
    return "bilinmiyor", ""


def logo_farklari(v, bas: str | None = None, bit: str | None = None, esik: float | None = None) -> pd.DataFrame:
    """Logo başlık toplamı ile satır toplamı farklı olan faturalar; her biri için neden kodu, adı, açıklaması ve
    analize etkisi. Tarih filtresi verilirse yalnızca o aralıktaki faturalar."""
    from .analiz import tarih_maskesi
    from .fatura import FARK_ESIK
    esik = FARK_ESIK if esik is None else esik
    f = v.faturalar
    f = f[(f["fark"].abs() > esik) & ~f["iptal"]]
    if bas or bit:
        f = f[tarih_maskesi(f["tarih"], bas, bit)]
    if f.empty:
        return f.assign(neden_kod=pd.Series(dtype=str), neden=pd.Series(dtype=str), neden_aciklama=pd.Series(dtype=str),
                        etki=pd.Series(dtype=str), hesap=pd.Series(dtype=str))
    f = f.merge(_bilesenler(v), on=["donem", "fatura_ref"], how="left")
    for c in ("_masraf", "_indirim", "_iptal", "_depozito", "_promosyon"):
        f[c] = f[c].fillna(0.0)
    sonuc = [_neden(r) for _, r in f.iterrows()]
    f["neden_kod"] = [x[0] for x in sonuc]
    f["hesap"] = [x[1].replace(",", "X").replace(".", ",").replace("X", ".") for x in sonuc]
    ana = f["neden_kod"].str.split("+").str[0]
    f["neden"] = [" + ".join(NEDEN_ACIKLAMA[k][0] for k in kod.split("+")) for kod in f["neden_kod"]]
    f["neden_aciklama"] = ana.map(lambda k: NEDEN_ACIKLAMA[k][1])
    f["etki"] = ana.map(lambda k: NEDEN_ACIKLAMA[k][2])
    return f.sort_values("fark", key=lambda x: -x.abs(), kind="mergesort").reset_index(drop=True)


def fatura_nedeni(v, bas: dict) -> dict | None:
    """Tek bir faturanın (fatura detayı ekranı) Logo farkının nedeni; farkı yoksa None."""
    from .fatura import FARK_ESIK
    if bas.get("iptal") or not abs(float(bas.get("fark") or 0)) > FARK_ESIK:
        return None
    s = v.satirlar
    alt = type(v)(v.faturalar.iloc[0:0], s[(s["donem"].astype(str) == str(bas["donem"])) & (s["fatura_ref"] == bas["fatura_ref"])], v.malzeme)
    b = _bilesenler(alt)
    r = pd.Series(bas)
    for c in ("_masraf", "_indirim", "_iptal", "_depozito", "_promosyon"):
        r[c] = float(b[c].sum()) if len(b) else 0.0
    kod, hesap = _neden(r)
    ana = kod.split("+")[0]
    return {"neden": " + ".join(NEDEN_ACIKLAMA[x][0] for x in kod.split("+")), "aciklama": NEDEN_ACIKLAMA[ana][1],
            "etki": NEDEN_ACIKLAMA[ana][2], "hesap": hesap.replace(",", "X").replace(".", ",").replace("X", ".")}


def fark_ozeti(farklar: pd.DataFrame) -> pd.DataFrame:
    """Neden başına fatura sayısı ve toplam fark."""
    if farklar.empty:
        return pd.DataFrame(columns=["neden", "etki", "fatura", "toplam_fark", "mutlak_fark", "neden_aciklama"])
    g = (farklar.groupby(["neden", "etki", "neden_aciklama"], as_index=False)
         .agg(fatura=("fark", "size"), toplam_fark=("fark", "sum"), mutlak_fark=("fark", lambda x: x.abs().sum())))
    return g.sort_values(["fatura", "mutlak_fark"], ascending=False, kind="mergesort").reset_index(drop=True)


def disarida_ozeti(v, bas: str | None = None, bit: str | None = None) -> pd.DataFrame:
    """Analize alınmayan fatura satırları, nedenine göre (iptal, indirim/masraf satırı, konsinye, transfer…)."""
    from .analiz import tarih_maskesi
    f = v.faturalar
    if bas or bit:
        f = f[tarih_maskesi(f["tarih"], bas, bit)]
    s = v.satirlar
    s = s[(s["durum"] != "Analizde") & (s["donem"].astype(str) + ":" + s["fatura_ref"].astype(str)).isin(set(f["anahtar"]))]
    if s.empty:
        return pd.DataFrame(columns=["durum", "satir", "fatura", "tutar"])
    g = s.assign(_f=s["donem"].astype(str) + ":" + s["fatura_ref"].astype(str)).groupby("durum", as_index=False).agg(
        satir=("satir_ref", "size"), fatura=("_f", "nunique"), tutar=("net_tutar", "sum"))
    return g.sort_values("satir", ascending=False, kind="mergesort").reset_index(drop=True)


def sagla(v, bas: str | None = None, bit: str | None = None) -> dict:
    """Satış faturalarında Logo başlık toplamı (KDV hariç) ile programın tutarı: toplam köprüsü."""
    from .analiz import tarih_maskesi
    from .fatura import SATIS_TR
    f = v.faturalar
    if bas or bit:
        f = f[tarih_maskesi(f["tarih"], bas, bit)]
    f = f[f["trcode"].isin(SATIS_TR) & ~f["iptal"]]
    isaret = f["isaret"]
    # Logo toplamı boş faturada sağlama yapılamaz: o faturalar için satır toplamı alınır (fark 0 sayılır, ayrıca belirtilir)
    logo = float((f["logo_matrah"].where(f["logo_net"].notna(), f["kontrol_tutar"]) * isaret).sum())
    satir = float((f["kontrol_tutar"] * isaret).sum())
    analiz = float((f["analiz_tutar"] * isaret).where(~f["haric"], 0.0).sum())
    return {"fatura": int(len(f)), "logo": logo, "satir": satir, "analiz": analiz,
            "fark_logo_satir": logo - satir, "fark_toplam": float((f["fark"] * isaret).sum()), "haric": float((f["analiz_tutar"] * isaret).where(f["haric"], 0.0).sum()),
            "disarida": satir - float((f["analiz_tutar"] * isaret).sum()),
            "logo_bos": int(f["logo_net"].isna().sum())}


# ---------------------------------------------------------------------------- eksik / boş veriler
def eksik_veriler(depo: str, bas: str | None = None, bit: str | None = None, ornek: int = 200) -> list[dict]:
    """Ekranda boş, '–' ya da (eski sürümlerde) 'nan' görünmesine yol açan veri eksikleri. Yalnızca var olanlar döner.
    Her öğe: kod, baslik, aciklama, etki, adet, birim, tutar (varsa), ornek (DataFrame)."""
    from .detay import _tarih_kosulu
    con = sqlite3.connect(depo)
    out = []
    try:
        var = _tablolar(con)
        k, kp = _tarih_kosulu(bas, bit, "s.tarih")
        q = lambda sql, p=(): pd.read_sql_query(sql, con, params=p)  # noqa: E731
        kolon_s = {c for c, _ in _kolonlar(con, "fact_satis")}
        toplam_ciro = con.execute(f"SELECT COALESCE(SUM(net_tutar), 0) FROM fact_satis s WHERE 1=1{k}", kp).fetchone()[0] or 0.0

        def ekle(kod, baslik, aciklama, etki, df, adet=None, tutar=None, birim="satır"):
            n = int(adet if adet is not None else len(df))
            if n <= 0:
                return
            out.append({"kod": kod, "baslik": baslik, "aciklama": aciklama, "etki": etki, "adet": n, "birim": birim,
                        "tutar": None if tutar is None else float(tutar),
                        "pay": (float(tutar) / toplam_ciro) if tutar is not None and toplam_ciro else None,
                        "ornek": df.head(ornek).reset_index(drop=True)})

        # 1) carisi seçilmemiş satış (perakende / kasa)
        d = q(f"SELECT s.fatura_no, substr(s.tarih,1,10) AS tarih, COUNT(*) AS satir, SUM(s.net_tutar) AS tutar FROM fact_satis s "
              f"WHERE (s.cari_ref = 0 OR s.cari_ref IS NULL){k} GROUP BY 1, 2 ORDER BY tutar DESC", kp)
        ekle("carisiz", "Carisi seçilmemiş satışlar",
             "Logo'da faturada cari seçilmemiş (genelde perakende/kasa satışı). Müşteri listelerinde tek bir "
             "\"(Cari seçilmemiş — perakende/kasa satışı)\" satırı olarak toplanır.",
             "Müşteri ABC, müşteri sayısı ve müşteri kaybında tek müşteri gibi sayılır.", d,
             adet=d["satir"].sum() if len(d) else 0, tutar=d["tutar"].sum() if len(d) else 0)

        # 2) kartı bulunamayan cari
        d = q(f"SELECT c.cari_kod, c.cari_ad, COUNT(*) AS satir, SUM(s.net_tutar) AS tutar FROM fact_satis s "
              f"JOIN dim_cari c USING (cari_ref) WHERE c.cari_kod LIKE '{KARTSIZ_ONEK}%'{k} GROUP BY 1, 2 ORDER BY tutar DESC", kp)
        ekle("kartsiz_cari", "Cari kartı yedekte olmayan satışlar",
             "Satış satırındaki cari referansı Logo'nun cari kartları tablosunda (CLCARD) yok: kart silinmiş ya da "
             "başka firmada. Adı bilinmediği için \"(Cari kartı yedekte yok — Logo ref N)\" yazılır.",
             "Önceki sürümde bu satırlarda ad ve şehir \"nan\" görünüyordu.", d,
             adet=d["satir"].sum() if len(d) else 0, tutar=d["tutar"].sum() if len(d) else 0)

        # 3) kartı bulunamayan malzeme
        d = q(f"SELECT m.malzeme_kod, m.malzeme_ad, COUNT(*) AS satir, SUM(s.net_tutar) AS tutar FROM fact_satis s "
              f"JOIN dim_malzeme m USING (malzeme_ref) WHERE m.malzeme_kod LIKE '{KARTSIZ_ONEK}%'{k} GROUP BY 1, 2 ORDER BY tutar DESC", kp)
        ekle("kartsiz_malzeme", "Malzeme kartı yedekte olmayan satışlar",
             "Satış satırındaki malzeme referansı Logo'nun malzeme kartlarında (ITEMS) yok.",
             "Önceki sürümde ürün adı ve grubu \"nan\" görünüyordu.", d,
             adet=d["satir"].sum() if len(d) else 0, tutar=d["tutar"].sum() if len(d) else 0)

        # 4) maliyeti bulunamayan satışlar
        if "maliyet_tutar" in kolon_s:
            d = q(f"SELECT m.malzeme_kod, m.malzeme_ad, COUNT(*) AS satir, SUM(s.miktar) AS miktar, SUM(s.net_tutar) AS tutar "
                  f"FROM fact_satis s LEFT JOIN dim_malzeme m USING (malzeme_ref) WHERE s.maliyet_tutar IS NULL{k} "
                  "GROUP BY 1, 2 ORDER BY tutar DESC", kp)
            ekle("maliyetsiz", "Maliyeti bulunamayan satışlar",
                 "Bu ürünlerin satış tarihinden önce hiç alışı, devri ya da üretim girişi yok; son alış fiyatı "
                 "bilinmediği için kâr hesaplanamaz.",
                 "Kâr ve marj sütunları boş görünür (önceden \"nan\"); kâr ABC'sinde yer almazlar, toplam kâra katılmazlar.",
                 d.rename(columns={}), adet=d["satir"].sum() if len(d) else 0, tutar=d["tutar"].sum() if len(d) else 0)

        # 5) miktarı sıfır ama tutarı olan satış (birim fiyat hesaplanamaz)
        d = q(f"SELECT s.fatura_no, substr(s.tarih,1,10) AS tarih, m.malzeme_kod, m.malzeme_ad, s.net_tutar AS tutar "
              f"FROM fact_satis s LEFT JOIN dim_malzeme m USING (malzeme_ref) WHERE ABS(COALESCE(s.miktar,0)) < 1e-9 "
              f"AND ABS(COALESCE(s.net_tutar,0)) > 0.005{k} ORDER BY ABS(s.net_tutar) DESC", kp)
        ekle("miktarsiz", "Miktarı 0, tutarı olan satış satırları",
             "Fiyat farkı ya da tutar düzeltmesi için miktarsız girilmiş satırlar.",
             "Ortalama birim fiyat bu satırlarda hesaplanamaz (boş görünür); ciroya dahildir.", d,
             tutar=d["tutar"].sum() if len(d) else 0)

        # 6) tarihi okunamayan satırlar
        n = con.execute("SELECT COUNT(*), COALESCE(SUM(net_tutar),0) FROM fact_satis WHERE tarih IS NULL").fetchone()
        if n[0]:
            d = q("SELECT fatura_no, satir_ref, net_tutar AS tutar FROM fact_satis WHERE tarih IS NULL ORDER BY ABS(net_tutar) DESC")
            ekle("tarihsiz", "Tarihi okunamayan satış satırları",
                 "Logo'da DATE_ alanı boş ya da geçersiz.",
                 "Toplam ciroda var ama aylık grafikte, ay detayında ve tarih filtresi seçiliyken görünmez.", d,
                 adet=n[0], tutar=n[1])

        # 7) malzeme grubu boş
        d = q(f"SELECT m.malzeme_kod, m.malzeme_ad, SUM(s.net_tutar) AS tutar FROM fact_satis s JOIN dim_malzeme m USING (malzeme_ref) "
              f"WHERE COALESCE(TRIM(m.grup_kod), '') = ''{k} GROUP BY 1, 2 ORDER BY tutar DESC", kp)
        ekle("grupsuz", "Malzeme grubu (STGRPCODE) boş ürünler",
             "Logo'da malzeme kartında grup kodu girilmemiş.",
             "Grup satışında \"(grupsuz)\" altında toplanır; listelerde grup sütunu boştur.", d, birim="ürün",
             tutar=d["tutar"].sum() if len(d) else 0)

        # 8) şehri boş müşteriler
        d = q(f"SELECT c.cari_kod, c.cari_ad, SUM(s.net_tutar) AS tutar FROM fact_satis s JOIN dim_cari c USING (cari_ref) "
              f"WHERE COALESCE(TRIM(c.sehir), '') = '' AND c.cari_kod NOT LIKE '{KARTSIZ_ONEK}%' AND c.cari_kod <> '{CARISIZ_KOD}'{k} "
              "GROUP BY 1, 2 ORDER BY tutar DESC", kp)
        ekle("sehirsiz", "Şehri boş müşteriler", "Logo'da cari kartında şehir (CITY) girilmemiş.",
             "Müşteri listelerinde şehir sütunu boştur.", d, birim="müşteri", tutar=d["tutar"].sum() if len(d) else 0)

        # 9) temsilcisi atanmamış satış (temsilci kullanılan firmalarda)
        if "satis_temsilci_ref" in kolon_s and con.execute(
                "SELECT COUNT(DISTINCT satis_temsilci_ref) FROM fact_satis WHERE COALESCE(satis_temsilci_ref,0) <> 0").fetchone()[0]:
            d = q(f"SELECT s.fatura_no, substr(s.tarih,1,10) AS tarih, SUM(s.net_tutar) AS tutar FROM fact_satis s "
                  f"WHERE COALESCE(s.satis_temsilci_ref, 0) = 0{k} GROUP BY 1, 2 ORDER BY tutar DESC", kp)
            ekle("temsilcisiz", "Satış temsilcisi atanmamış satışlar",
                 "Ne satırda ne fatura başlığında satış elemanı (SALESMANREF) seçilmiş.",
                 "Temsilci tablosunda \"(temsilci atanmamış)\" satırında toplanır.", d, birim="fatura",
                 tutar=d["tutar"].sum() if len(d) else 0)

        # 10) Logo genel toplamı boş faturalar (sağlama yapılamaz)
        if "dim_fatura" in var:
            kf, kfp = _tarih_kosulu(bas, bit, "tarih")
            d = q(f"SELECT fatura_no, substr(tarih,1,10) AS tarih, tur FROM dim_fatura WHERE logo_net IS NULL AND iptal = 0{kf} "
                  "ORDER BY tarih", kfp)
            ekle("logo_bos", "Logo genel toplamı boş faturalar",
                 "Fatura başlığında NETTOTAL boş; Logo ile sağlama yapılamaz.",
                 "Faturalar ekranında Logo tutarı ve fark sütunu boş görünür (önceden \"nan\").", d, birim="fatura")
            d = q(f"SELECT donem, fatura_ref, fatura_no, belge_no, tur FROM dim_fatura WHERE tarih IS NULL")
            ekle("fatura_tarihsiz", "Tarihi boş faturalar", "Logo'da fatura tarihi (DATE_) boş ya da geçersiz.",
                 "Faturalar ekranında tarih boş görünür; tarih filtresi seçiliyken listelenmez.", d, birim="fatura")

        # 11) eksi stok
        d = q("SELECT m.malzeme_kod, m.malzeme_ad, b.stok_miktar FROM stok_bakiye b LEFT JOIN dim_malzeme m USING (malzeme_ref) "
              "WHERE b.stok_miktar < -0.0001 ORDER BY b.stok_miktar")
        ekle("eksi_stok", "Stok bakiyesi eksi olan malzemeler",
             "Satılan miktar girişlerden fazla: Logo'da girilmemiş alış, üretim ya da sayım fişi olabilir.",
             "Stok devir hızı ve stok yeterlilik süresi bu ürünlerde hesaplanamaz (boş görünür).", d, birim="ürün")

        # 12) adı tanımsız ambar
        if "dim_ambar" in var:
            d = q(f"SELECT s.ambar, COUNT(*) AS satir, SUM(s.net_tutar) AS tutar FROM fact_satis s "
                  f"WHERE s.ambar NOT IN (SELECT ambar FROM dim_ambar){k} GROUP BY 1 ORDER BY tutar DESC", kp)
            if len(d) and con.execute("SELECT COUNT(*) FROM dim_ambar").fetchone()[0]:
                ekle("ambarsiz", "Adı tanımsız ambarlar", "Ambar numarası Logo'nun ambar listesinde (L_CAPIWHOUSE) yok.",
                     "Depo kırılımında \"Ambar N\" olarak görünür.", d, birim="ambar", tutar=d["tutar"].sum())
    finally:
        con.close()
    return out


# ---------------------------------------------------------------------------- Excel
def excel(yol: str, farklar: pd.DataFrame, ozet: pd.DataFrame, eksikler: list[dict], disarida: pd.DataFrame,
          baslik_not: str = "") -> str:
    import xlsxwriter
    wb = xlsxwriter.Workbook(yol, {"nan_inf_to_errors": False})
    bf = wb.add_format({"bold": True, "font_color": "white", "bg_color": "#1F3864", "border": 1, "text_wrap": True, "valign": "vcenter"})
    para = wb.add_format({"num_format": "#,##0.00"})
    tarih = wb.add_format({"num_format": "dd.mm.yyyy"})
    notf = wb.add_format({"italic": True, "font_color": "#666666"})
    h1 = wb.add_format({"bold": True, "font_size": 14})
    sar = wb.add_format({"text_wrap": True, "valign": "top"})

    def yaz(ws, satir, df, kolonlar, paralar=()):
        for j, (_, b) in enumerate(kolonlar):
            ws.write(satir, j, b, bf)
        for i, row in enumerate(df[[k for k, _ in kolonlar]].itertuples(index=False), start=satir + 1):
            for j, ((k, _), x) in enumerate(zip(kolonlar, row)):
                if x is None or x is pd.NaT or (isinstance(x, float) and np.isnan(x)):
                    continue
                if k == "tarih" and not isinstance(x, str):
                    ws.write_datetime(i, j, pd.Timestamp(x).to_pydatetime(), tarih)
                elif k in paralar:
                    ws.write_number(i, j, float(x), para)
                elif isinstance(x, (int, float, np.integer, np.floating)) and not isinstance(x, bool):
                    ws.write_number(i, j, float(x))
                else:
                    ws.write(i, j, str(x))
        ws.freeze_panes(satir + 1, 0)
        ws.autofilter(satir, 0, max(satir + 1, satir + len(df)), len(kolonlar) - 1)

    ws = wb.add_worksheet("Özet")
    ws.set_column(0, 0, 40); ws.set_column(1, 4, 16); ws.set_column(5, 5, 90)
    ws.write(0, 0, "Veri kontrolü — Logo ile farklar ve eksik veriler", h1)
    ws.write(1, 0, baslik_not, notf)
    ws.write(3, 0, "Logo ile farklı faturalar, nedenine göre", wb.add_format({"bold": True}))
    yaz(ws, 4, ozet, [("neden", "Neden"), ("etki", "Analize etkisi"), ("fatura", "Fatura"), ("toplam_fark", "Toplam fark (TL)"),
                      ("mutlak_fark", "Mutlak fark (TL)"), ("neden_aciklama", "Açıklama")], paralar=("toplam_fark", "mutlak_fark"))
    r = 6 + len(ozet)
    ws.write(r, 0, "Eksik / boş veriler", wb.add_format({"bold": True}))
    ek = pd.DataFrame([{k: e[k] for k in ("baslik", "adet", "birim", "tutar", "etki", "aciklama")} for e in eksikler]) if eksikler else \
        pd.DataFrame(columns=["baslik", "adet", "birim", "tutar", "etki", "aciklama"])
    yaz(ws, r + 1, ek, [("baslik", "Konu"), ("adet", "Adet"), ("birim", "Birim"), ("tutar", "Satış tutarı (TL)"),
                        ("etki", "Ekranda ne olur"), ("aciklama", "Neden")], paralar=("tutar",))
    ws.freeze_panes(0, 0)

    ws = wb.add_worksheet("Logo farkları")
    ws.write(0, 0, "Fark = Logo başlığındaki KDV hariç tutar (NETTOTAL − TOTALVAT) − satırların toplamı. "
                   "Logo'da bulmak için: Logo tablosu + LOGICALREF.", notf)
    yaz(ws, 1, farklar, [("tarih", "Tarih"), ("fatura_no", "Fatura no"), ("belge_no", "Belge no"), ("tur", "Tür"),
                         ("cari_kod", "Cari kodu"), ("cari_ad", "Cari adı"), ("logo_matrah", "Logo KDV hariç"),
                         ("kontrol_tutar", "Satırların toplamı"), ("analiz_tutar", "Analizdeki tutar"), ("fark", "Fark"),
                         ("neden", "Neden"), ("hesap", "Hesap"), ("etki", "Analize etkisi"), ("logo_tablo", "Logo tablosu"),
                         ("fatura_ref", "LOGICALREF")],
        paralar=("logo_matrah", "kontrol_tutar", "analiz_tutar", "fark"))
    ws.set_column(0, 0, 11); ws.set_column(1, 5, 14); ws.set_column(5, 5, 30); ws.set_column(6, 9, 14)
    ws.set_column(10, 11, 34); ws.set_column(12, 14, 16)

    ws = wb.add_worksheet("Analiz dışı satırlar")
    yaz(ws, 0, disarida, [("durum", "Durum"), ("satir", "Satır"), ("fatura", "Fatura"), ("tutar", "Tutar (TL)")], paralar=("tutar",))
    ws.set_column(0, 0, 44); ws.set_column(1, 3, 14)

    ws = wb.add_worksheet("Eksik veri ayrıntısı")
    r = 0
    for e in eksikler:
        ws.write(r, 0, f"{e['baslik']} — {e['adet']:,} {e['birim']}".replace(",", "."), wb.add_format({"bold": True}))
        r += 1
        d = e["ornek"]
        kol = [(c, c.replace("_", " ").capitalize()) for c in d.columns]
        for j, (_, b) in enumerate(kol):
            ws.write(r, j, b, bf)
        for i, row in enumerate(d.itertuples(index=False), start=r + 1):
            for j, x in enumerate(row):
                if x is None or (isinstance(x, float) and np.isnan(x)):
                    continue
                if isinstance(x, (int, float, np.integer, np.floating)) and not isinstance(x, bool):
                    ws.write_number(i, j, float(x), para if kol[j][0] in ("tutar", "stok_miktar") else None)
                else:
                    ws.write(i, j, str(x))
        r += len(d) + 3
    ws.set_column(0, 6, 18); ws.set_column(1, 1, 36)
    wb.close()
    return yol
