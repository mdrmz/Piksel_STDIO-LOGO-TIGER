"""
Tarih filtresi (tüm ekranlar), veri kontrolü (Logo farklarının nedeni, eksik veriler) ve ekranda "nan" görünmemesi.
Çalıştırma:  python demo/demo_db_olustur.py demo/logo_demo.sqlite && pytest -q tests
"""
import html
import os
import re
import shutil
import sqlite3
import sys

import pandas as pd
import pytest

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)
DEMO = os.path.join(KOK, "demo", "logo_demo.sqlite")

from core import detay, fatura, kontrol  # noqa: E402
from core.analiz import Analiz  # noqa: E402
from core.kaynak import SqliteKaynak  # noqa: E402
from core.logo import aktar, firmalari_tara  # noqa: E402


@pytest.fixture(scope="module")
def temiz(tmp_path_factory):
    if not os.path.exists(DEMO):
        pytest.skip("Önce demo veritabanını üretin: python demo/demo_db_olustur.py demo/logo_demo.sqlite")
    yol = str(tmp_path_factory.mktemp("tk") / "analiz.sqlite")
    k = SqliteKaynak(DEMO)
    f = next(f for f in firmalari_tara(k) if f.no == "001")
    aktar(k, f, f.donemler, yol)
    k.kapat()
    return yol


def _fatura(con, sira):
    """Satış faturalarından sıradaki (iptal olmayan, satırı olan) birinin (donem, ref, kdv, net) bilgisi."""
    return con.execute("SELECT d.donem, d.fatura_ref, d.logo_kdv, d.logo_net FROM dim_fatura d WHERE d.trcode = 8 AND d.iptal = 0 "
                       "AND d.logo_kdv > 10 AND EXISTS (SELECT 1 FROM fatura_satir s WHERE s.donem = d.donem AND s.fatura_ref = d.fatura_ref) "
                       "ORDER BY d.fatura_ref LIMIT 1 OFFSET ?", (sira,)).fetchone()


@pytest.fixture(scope="module")
def kirli(temiz, tmp_path_factory):
    """Sahada görülen bozuklukları taşıyan analiz deposu ve hangi faturaya ne yapıldığı."""
    yol = str(tmp_path_factory.mktemp("kirli") / "analiz.sqlite")
    shutil.copy(temiz, yol)
    con = sqlite3.connect(yol)
    beklenen = {}
    # carisi seçilmemiş (perakende) ve kartı silinmiş cari, kartı silinmiş malzeme, maliyeti olmayan satış
    con.execute("UPDATE fact_satis SET cari_ref = 0 WHERE rowid IN (SELECT rowid FROM fact_satis ORDER BY rowid LIMIT 40)")
    con.execute("UPDATE fact_satis SET cari_ref = 999999 WHERE rowid IN (SELECT rowid FROM fact_satis ORDER BY rowid LIMIT 25 OFFSET 40)")
    con.execute("UPDATE fact_satis SET malzeme_ref = 888888 WHERE rowid IN (SELECT rowid FROM fact_satis ORDER BY rowid LIMIT 15 OFFSET 80)")
    con.execute("UPDATE fact_satis SET maliyet_tutar = NULL WHERE rowid IN (SELECT rowid FROM fact_satis ORDER BY rowid LIMIT 30 OFFSET 100)")
    con.execute("UPDATE dim_malzeme SET grup_kod = NULL WHERE rowid IN (SELECT rowid FROM dim_malzeme LIMIT 5)")
    con.execute("UPDATE dim_cari SET sehir = NULL, ozel_kod = NULL WHERE rowid IN (SELECT rowid FROM dim_cari LIMIT 5)")

    def satir_ekle(d, r, tip, durum, tutar):
        ref = con.execute("SELECT MAX(satir_ref) FROM fatura_satir").fetchone()[0] + 1
        con.execute("INSERT INTO fatura_satir (donem, fatura_ref, satir_ref, satir_tipi, trcode, malzeme_ref, miktar, birim_fiyat, "
                    "net_tutar, ambar, durum) VALUES (?, ?, ?, ?, 8, 0, 0, 0, ?, 0, ?)", (d, r, ref, tip, tutar, durum))

    d, r, kdv, net = _fatura(con, 0)   # masraf satırı: Logo toplamında var, ciroda yok
    satir_ekle(d, r, "Masraf", "Analiz dışı: Masraf satırı", 150.0)
    con.execute("UPDATE dim_fatura SET logo_net = ?, logo_kdv = ? WHERE donem = ? AND fatura_ref = ?", (net + 180, kdv + 30, d, r))
    beklenen[(d, r)] = "masraf"
    d, r, kdv, net = _fatura(con, 1)   # 5/10 KDV tevkifatı
    con.execute("UPDATE dim_fatura SET logo_net = ? WHERE donem = ? AND fatura_ref = ?", (net - kdv * 0.5, d, r))
    beklenen[(d, r)] = "tevkifat"
    d, r, kdv, net = _fatura(con, 2)   # iptal edilmiş satır başlıkta duruyor
    satir_ekle(d, r, "Malzeme", "İptal", 200.0)
    con.execute("UPDATE dim_fatura SET logo_net = ?, logo_kdv = ? WHERE donem = ? AND fatura_ref = ?", (net + 240, kdv + 40, d, r))
    beklenen[(d, r)] = "iptal_satir"
    d, r, kdv, net = _fatura(con, 3)   # açıklanamayan fark
    con.execute("UPDATE dim_fatura SET logo_net = ? WHERE donem = ? AND fatura_ref = ?", (net + 777.77, d, r))
    beklenen[(d, r)] = "bilinmiyor"
    d, r, kdv, net = _fatura(con, 4)   # Logo genel toplamı boş: farka girmez, eksik veride listelenir
    con.execute("UPDATE dim_fatura SET logo_net = NULL WHERE donem = ? AND fatura_ref = ?", (d, r))
    # satırı yedekte olmayan fatura
    yeni = con.execute("SELECT MAX(fatura_ref) FROM dim_fatura").fetchone()[0] + 1
    con.execute("INSERT INTO dim_fatura (donem, firma, logo_donem, fatura_ref, fatura_no, belge_no, tarih, trcode, tur, cari_ref, ambar, "
                "iptal, logo_brut, logo_indirim, logo_kdv, logo_net, temsilci_ref, aciklama) "
                "SELECT donem, firma, logo_donem, ?, 'SATIRSIZ-1', '', tarih, trcode, tur, cari_ref, ambar, 0, 1000, 0, 200, 1200, 0, '' "
                "FROM dim_fatura WHERE donem = ? AND fatura_ref = ?", (yeni, d, r))
    beklenen[(d, yeni)] = "satirsiz"
    con.commit()
    con.close()
    return yol, beklenen


# ---------------------------------------------------------------------------- tarih filtresi
@pytest.mark.parametrize("bas,bit", [("2026-01-01", "2026-06-30"), ("2025-03-15", "2025-03-15"), ("2025-07-01", None), (None, "2025-02-28")])
def test_tarih_filtresi_net_satis_sql_ve_faturalarla_ayni(temiz, bas, bit):
    con = sqlite3.connect(temiz)
    k, p = "", []
    if bas:
        k += " AND tarih >= ?"; p.append(bas)
    if bit:
        k += " AND tarih < date(?, '+1 day')"; p.append(bit)
    beklenen = con.execute(f"SELECT COALESCE(SUM(net_tutar), 0) FROM fact_satis WHERE 1=1{k}", p).fetchone()[0]
    alis = con.execute(f"SELECT COALESCE(SUM(net_tutar), 0) FROM fact_alis WHERE 1=1{k}", p).fetchone()[0]
    a = Analiz(temiz, bas=bas, bit=bit)
    assert a.ozet()["net_satis"] == pytest.approx(beklenen, abs=0.01)
    assert a.ozet()["net_alis"] == pytest.approx(alis, abs=0.01)
    assert a.urun_abc()["net_tutar"].sum() == pytest.approx(a.satis.loc[a.satis.groupby("malzeme_ref")["net_tutar"].transform("sum") > 0, "net_tutar"].sum(), abs=0.01)
    # Faturalar ekranı aynı aralıkta aynı toplamı verir (paneldeki kutuya tıklayınca açılan liste)
    v = fatura.veri(temiz)
    f, _ = fatura.suz(v, fatura.Filtre(bas=bas or "", bit=bit or "", tur=["satis", "satis_iade"], durum="gecerli"))
    assert fatura.ozet(f, None)["satis_net"] == pytest.approx(beklenen, abs=0.01)
    if bas:
        assert a.satis["tarih"].min() >= pd.Timestamp(bas)
    if bit:
        assert a.satis["tarih"].max() < pd.Timestamp(bit) + pd.Timedelta(days=1)


def test_tarih_filtresi_kartlarda(temiz):
    a = Analiz(temiz, bas="2026-01-01", bit="2026-03-31")
    u = a.urun_abc().iloc[0]
    k = detay.urun_karti(temiz, u["malzeme_kod"], None, "2026-01-01", "2026-03-31")
    assert k["toplam"]["net_tutar"] == pytest.approx(u["net_tutar"], abs=0.01)
    assert list(k["aylik"]["yil_ay"]) == ["2026-01", "2026-02", "2026-03"]
    m = a.musteri_abc().iloc[0]
    c = detay.cari_karti(temiz, m["cari_kod"], None, "2026-01-01", "2026-03-31")
    assert c["toplam"]["net_tutar"] == pytest.approx(m["net_tutar"], abs=0.01)
    # filtresiz kart daha uzun dönemi gösterir
    assert len(detay.urun_karti(temiz, u["malzeme_kod"])["aylik"]) > 3


def test_gecen_yil_kiyasi_veri_kapsamiyorsa_yapilmaz(temiz):
    a = Analiz(temiz)
    ilk = a.satis["tarih"].min()
    # verinin ilk yılından başlayan aralığın geçen yılı veride yok
    assert Analiz(temiz, bas=str(ilk.date())).donem_karsilastirma("cari") is None
    son = a.satis["tarih"].max()
    b = Analiz(temiz, bas=f"{son.year}-01-01", bit=str(son.date()))
    k = b.donem_karsilastirma("cari")
    if a.satis["tarih"].min() <= pd.Timestamp(f"{son.year - 1}-01-07"):
        assert k is not None and k["bu_yil"].sum() == pytest.approx(b.ozet()["net_satis"], abs=0.01)


def test_tarih_filtresi_yasli_analizlerde_gecmise_bakar(temiz):
    """Müşteri kaybı ve ölü stok, bitiş tarihine kadarki geçmişe bakar; filtre başlangıcından önceki alımlar da sayılır."""
    a = Analiz(temiz)
    son = a.satis["tarih"].max()
    b = Analiz(temiz, bas=str((son - pd.Timedelta(days=30)).date()), bit=str(son.date()))
    assert b.musteri_kaybi()["cari_kod"].tolist() == a.musteri_kaybi()["cari_kod"].tolist()
    assert len(b.olu_stok()) == len(a.olu_stok())


# ---------------------------------------------------------------------------- veri kontrolü
def test_logo_fark_nedenleri(kirli):
    yol, beklenen = kirli
    v = fatura.yukle(sqlite3.connect(yol))
    fk = kontrol.logo_farklari(v)
    bulunan = {(str(r.donem), int(r.fatura_ref)): r.neden_kod for r in fk.itertuples()}
    for anahtar, kod in beklenen.items():
        assert bulunan.get((str(anahtar[0]), int(anahtar[1]))) == kod, (anahtar, kod, bulunan.get(anahtar))
    oz = kontrol.fark_ozeti(fk)
    assert oz["fatura"].sum() == len(fk)
    # sağlama köprüsü: Logo − satırlar farkı, tek tek fatura farklarının toplamıdır (Logo toplamı boş fatura farka girmez)
    sg = kontrol.sagla(v)
    satis = fk[fk["trcode"].isin((7, 8, 2, 3))]
    assert sg["fark_logo_satir"] == pytest.approx(float((satis["fark"] * satis["isaret"]).sum()), abs=0.01)
    assert sg["logo_bos"] == 1
    # tek fatura ekranındaki neden listeyle aynı
    r = fk.iloc[0]
    d = kontrol.fatura_nedeni(v, v.faturalar[(v.faturalar["donem"] == r["donem"]) & (v.faturalar["fatura_ref"] == r["fatura_ref"])].iloc[0].to_dict())
    assert d["neden"] == r["neden"]


def test_eksik_veriler_listelenir(kirli):
    yol, _ = kirli
    kontrol.kartsiz_onar(yol)
    e = {x["kod"]: x for x in kontrol.eksik_veriler(yol)}
    for kod in ("carisiz", "kartsiz_cari", "kartsiz_malzeme", "maliyetsiz", "grupsuz", "logo_bos"):
        assert kod in e, kod
    con = sqlite3.connect(yol)
    assert e["carisiz"]["adet"] == con.execute("SELECT COUNT(*) FROM fact_satis WHERE cari_ref = 0").fetchone()[0]
    assert e["kartsiz_cari"]["tutar"] == pytest.approx(
        con.execute("SELECT SUM(net_tutar) FROM fact_satis WHERE cari_ref = 999999").fetchone()[0], abs=0.01)


def test_kartsiz_onarim_bir_kez_ve_adlandirir(kirli):
    yol, _ = kirli
    kontrol.kartsiz_onar(yol)
    assert kontrol.kartsiz_onar(yol) == {"cari": 0, "malzeme": 0}   # ikinci çağrı bir şey eklemez
    a = Analiz(yol)
    m = a.musteri_abc()
    assert not m["cari_ad"].isna().any() and not m["cari_kod"].isna().any()
    assert (m["cari_kod"] == kontrol.CARISIZ_KOD).any()
    assert m.loc[m["cari_kod"] == "KARTSIZ-C999999", "cari_ad"].iloc[0].startswith("(Cari kartı yedekte yok")
    assert not a.urun_abc()["malzeme_ad"].isna().any()


def test_sayfalarda_nan_yazmaz(kirli, tmp_path, monkeypatch):
    """Kirli veriyle bütün ekranlar açılır ve hiçbirinde 'nan' / 'None' yazmaz; tarih filtresi her ekranda görünür."""
    yol, _ = kirli
    calisma = tmp_path / "calisma"
    aid = "20260101_000000_F001"
    (calisma / aid).mkdir(parents=True)
    shutil.copy(yol, calisma / aid / "analiz.sqlite")
    a = Analiz(yol)
    (calisma / aid / "bilgi.json").write_text(
        '{"firma": "001", "firma_ad": "DEMO", "donemler": ["01", "02"], "kaynak": "test", "olusturma": "", '
        f'"tarih_bas": "{a.meta["tarih_bas"]}", "tarih_bit": "{a.meta["tarih_bit"]}", "olu_stok_gun": 90}}', encoding="utf-8")
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(calisma))
    import importlib
    import app as uygulama
    uygulama = importlib.reload(uygulama)
    c = uygulama.app.test_client()
    con = sqlite3.connect(calisma / aid / "analiz.sqlite")
    ay = con.execute("SELECT substr(MAX(tarih), 1, 7) FROM fact_satis").fetchone()[0]
    sayfalar = [f"/panel/{aid}", f"/panel/{aid}?abc=ciro", f"/abc/{aid}/urun", f"/abc/{aid}/musteri", f"/abc/{aid}/tedarikci",
                f"/urun/{aid}?kod=KARTSIZ-M888888", f"/cari/{aid}?kod={kontrol.CARISIZ_KOD}", f"/cari/{aid}?kod=KARTSIZ-C999999",
                f"/ay/{aid}?ay={ay}", f"/faturalar/{aid}", f"/faturalar/{aid}?durum=fark", f"/kontrol/{aid}", f"/ara/{aid}?q=a"]
    for etiket, aralik in (("filtresiz", None), ("filtreli", "2025-01-01|2025-12-31")):
        if aralik:
            r = c.post(f"/tarih/{aid}", data={"aralik": aralik, "donus": f"/panel/{aid}"})
            assert r.status_code == 302
        for u in sayfalar:
            r = c.get(u)
            assert r.status_code == 200, (etiket, u, r.status_code)
            t = r.get_data(as_text=True)
            metin = re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S))
            metin = html.unescape(metin).replace('"nan"', "")   # açıklama metinlerindeki tırnaklı "nan" sözcüğü
            assert not re.search(r"\b(nan|NaN|NaT|None)\b", metin), (etiket, u, re.findall(r".{60}\b(?:nan|None|NaT)\b.{20}", metin)[:3])
            assert "Tarih filtresi" in t, (etiket, u)
        if aralik:
            assert "01.01.2025 – 31.12.2025" in c.get(f"/panel/{aid}").get_data(as_text=True)
    for tur in ("excel", "pdf", "yzpaket", "powerbi"):
        assert c.get(f"/indir/{aid}/{tur}").status_code == 200, tur
    assert c.get(f"/kontrol/{aid}/excel").status_code == 200
    # Power BI paketindeki satışlar da seçilen aralıkta
    import zipfile
    z = sorted((calisma / aid).glob("PowerBI_*.zip"))[-1]
    with zipfile.ZipFile(z) as zf, zf.open("fact_satis.csv") as fh:
        t = pd.read_csv(fh)["tarih"]
    assert t.min() >= "2025-01-01" and t.max() <= "2025-12-31"
    # filtreyi kaldır
    c.post(f"/tarih/{aid}", data={"temizle": "1", "donus": f"/panel/{aid}"})
    assert not (calisma / aid / "tarih_filtresi.json").exists()


def test_tarih_adresi_faturalar_kendi_tarihini_ezmez(kirli, tmp_path, monkeypatch):
    """Genel aralık değişince Faturalar ekranının adresindeki eski bas/bit atılır; diğer filtreler kalır."""
    calisma = tmp_path / "c2"
    aid = "20260101_000001_F001"
    (calisma / aid).mkdir(parents=True)
    shutil.copy(kirli[0], calisma / aid / "analiz.sqlite")
    (calisma / aid / "bilgi.json").write_text('{"firma": "001", "firma_ad": "D", "donemler": ["01"], "tarih_bas": "2025-01-01", '
                                              '"tarih_bit": "2026-10-31"}', encoding="utf-8")
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(calisma))
    import importlib
    import app as uygulama
    uygulama = importlib.reload(uygulama)
    c = uygulama.app.test_client()
    r = c.post(f"/tarih/{aid}", data={"bas": "2026-02-01", "bit": "2026-01-01",
                                      "donus": f"/faturalar/{aid}?bas=2025-01-01&cari=abc&sayfa=3"})
    assert r.headers["Location"].endswith(f"/faturalar/{aid}?cari=abc")
    import json
    assert json.loads((calisma / aid / "tarih_filtresi.json").read_text()) == {"bas": "2026-01-01", "bit": "2026-02-01"}
    t = c.get(f"/faturalar/{aid}").get_data(as_text=True)
    assert 'value="2026-01-01"' in t and 'value="2026-02-01"' in t
