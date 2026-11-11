"""
Uçtan uca testler — demo veritabanı üzerinde.
Çalıştırma:  python demo/demo_db_olustur.py demo/logo_demo.sqlite && pytest -q tests
"""
import os
import sqlite3
import sys
import time

import pandas as pd
import pytest

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)
DEMO = os.path.join(KOK, "demo", "logo_demo.sqlite")

from core.analiz import Analiz, AbcEsik, abc  # noqa: E402
from core.kaynak import SqliteKaynak  # noqa: E402
from core.logo import aktar, firmalari_tara  # noqa: E402


@pytest.fixture(scope="module")
def depo(tmp_path_factory):
    if not os.path.exists(DEMO):
        pytest.skip("Önce demo veritabanını üretin: python demo/demo_db_olustur.py demo/logo_demo.sqlite")
    yol = str(tmp_path_factory.mktemp("la") / "analiz.sqlite")
    k = SqliteKaynak(DEMO)
    f = next(f for f in firmalari_tara(k) if f.no == "001")
    aktar(k, f, f.donemler, yol)
    k.kapat()
    return yol


def test_firma_tarama():
    k = SqliteKaynak(DEMO)
    fs = {f.no: f for f in firmalari_tara(k)}
    assert set(fs) == {"001", "002"}
    assert fs["001"].donemler == ["01", "02"]
    assert fs["001"].ad == "DEMO YAPI MARKET"


def test_net_satis_ham_sql_ile_ayni(depo):
    """Uygulamanın net satışı, ham Logo tablolarından düz SQL ile hesaplananla kuruşu kuruşuna aynı olmalı."""
    con = sqlite3.connect(DEMO)
    toplam = 0.0
    for p in ("01", "02"):
        toplam += con.execute(f"""
            SELECT SUM(CASE WHEN S.TRCODE IN (7,8) THEN S.VATMATRAH ELSE -S.VATMATRAH END)
            FROM LG_001_{p}_STLINE S LEFT JOIN LG_001_{p}_INVOICE I ON I.LOGICALREF = S.INVOICEREF
            WHERE S.TRCODE IN (2,3,7,8) AND S.LINETYPE = 0 AND S.CANCELLED = 0 AND S.INVOICEREF > 0
              AND COALESCE(I.CANCELLED, 0) = 0""").fetchone()[0]
    assert Analiz(depo).ozet()["net_satis"] == pytest.approx(toplam, abs=0.01)


def test_stok_bakiyesi_birim_cevrimli(depo):
    con = sqlite3.connect(DEMO)
    beklenen = pd.read_sql("""
        SELECT STOCKREF AS malzeme_ref,
               SUM(CASE WHEN IOCODE IN (1,2) THEN 1 WHEN IOCODE IN (3,4) THEN -1 ELSE 0 END * AMOUNT * UINFO2 / UINFO1) AS s
        FROM LG_001_02_STLINE WHERE LINETYPE = 0 AND CANCELLED = 0 GROUP BY STOCKREF""", con).set_index("malzeme_ref")["s"]
    gercek = Analiz(depo).stok.set_index("malzeme_ref")["stok_miktar"]
    assert (beklenen.reindex(gercek.index).fillna(0) - gercek).abs().max() < 1e-6


def test_abc_siniflari():
    df = pd.DataFrame({"k": list("abcde"), "v": [50, 30, 10, 6, 4]})
    t = abc(df, "k", "v", AbcEsik(0.8, 0.95)).set_index("k")["sinif"].to_dict()
    # a:%50, b:%80'e kadar, c:%90, d:%96, e:%100
    assert t == {"a": "A", "b": "A", "c": "B", "d": "B", "e": "C"}


def test_abc_tek_kalem():
    t = abc(pd.DataFrame({"k": ["x"], "v": [10]}), "k", "v")
    assert t["sinif"].tolist() == ["A"]


def test_olu_stok_bulunur(depo):
    o = Analiz(depo).olu_stok(90)
    assert len(o) >= 30  # demo veride ~32 ürün hiç satılmıyor
    assert (o["stok_miktar"] > 0).all()


# ---------------------------------------------------------------------------- v0.4 analizleri
def _ham_satirlar(con, kosul):
    """Firma 001'in iki dönemindeki iptal edilmemiş malzeme satırları (ham Logo tablolarından)."""
    out = []
    for p in ("01", "02"):
        out += con.execute(f"""
            SELECT S.STOCKREF, substr(S.DATE_, 1, 10), S.TRCODE, S.AMOUNT * S.UINFO2 / S.UINFO1, S.VATMATRAH, S.TOTAL,
                   S.INVOICEREF, COALESCE(I.CANCELLED, 0)
            FROM LG_001_{p}_STLINE S LEFT JOIN LG_001_{p}_INVOICE I ON I.LOGICALREF = S.INVOICEREF
            WHERE S.LINETYPE = 0 AND S.CANCELLED = 0 AND {kosul}""").fetchall()
    return [r for r in out if not r[7]]


def test_karlilik_bagimsiz_hesapla_ayni(depo):
    """Maliyet = satış miktarı × o tarihten önceki en son giriş (alış/devir) günlük ortalama birim fiyatı.
    Aynı kural burada düz Python döngüsüyle ham tablolardan yeniden hesaplanır."""
    con = sqlite3.connect(DEMO)
    girisler, satislar = {}, {}
    for ref, tarih, _, miktar, matrah, total, _, _ in _ham_satirlar(con, "S.TRCODE IN (1, 13, 14, 50)"):
        tutar = matrah if matrah and matrah > 0 else total
        if miktar > 0 and tutar > 0:
            g = girisler.setdefault(ref, {}).setdefault(tarih, [0.0, 0.0])
            g[0] += tutar; g[1] += miktar
    for ref, tarih, trcode, miktar, matrah, _, invref, _ in _ham_satirlar(con, "S.TRCODE IN (2, 3, 7, 8) AND S.INVOICEREF > 0"):
        isaret = 1 if trcode in (7, 8) else -1
        satislar.setdefault(ref, []).append((tarih, isaret * miktar, isaret * matrah))

    def birim(ref, tarih):
        gunler = sorted(girisler.get(ref, {}))
        once = [g for g in gunler if g <= tarih]
        g = once[-1] if once else (gunler[0] if gunler else None)
        return None if g is None else girisler[ref][g][0] / girisler[ref][g][1]

    a = Analiz(depo)
    k = a.karlilik("malzeme").merge(a.malzeme[["malzeme_kod", "malzeme_ref"]], on="malzeme_kod").set_index("malzeme_ref")
    assert len(k) == len(satislar)
    for ref in list(satislar)[::7]:                       # her 7 üründen biri
        maliyet = sum(m * birim(ref, t) for t, m, _ in satislar[ref])
        ciro = sum(n for _, _, n in satislar[ref])
        assert k.loc[ref, "maliyet"] == pytest.approx(maliyet, rel=1e-9, abs=0.01)
        assert k.loc[ref, "brut_kar"] == pytest.approx(ciro - maliyet, rel=1e-9, abs=0.01)
    o = a.ozet()
    assert o["maliyet_kapsama"] == pytest.approx(1.0)
    assert o["brut_kar"] == pytest.approx(k["brut_kar"].sum(), abs=0.01)
    assert 0.20 < o["kar_marji"] < 0.40                    # demo: alış ≈ satış fiyatının %62–75'i
    for seviye in ("cari", "grup"):                        # her kırılımda toplam aynı
        assert a.karlilik(seviye)["brut_kar"].sum() == pytest.approx(o["brut_kar"], abs=0.01)


def test_cari_bakiye_ham_sql_ile_ayni(depo):
    con = sqlite3.connect(DEMO)
    ham = pd.read_sql("""SELECT C.CODE AS cari_kod, SUM(CASE WHEN F.SIGN = 0 THEN F.AMOUNT ELSE -F.AMOUNT END) AS b
                         FROM LG_001_02_CLFLINE F JOIN LG_001_CLCARD C ON C.LOGICALREF = F.CLIENTREF
                         WHERE F.CANCELLED = 0 GROUP BY C.CODE""", con).set_index("cari_kod")["b"]
    a = Analiz(depo)
    al, bo = a.cari_yaslandirma("alacak"), a.cari_yaslandirma("borc")
    dilim = ["g0_30", "g31_60", "g61_90", "g91_180", "g180p"]
    for t, isaret in ((al, 1), (bo, -1)):
        beklenen = (ham * isaret)[lambda x: x > 0.005]
        assert set(t["cari_kod"]) == set(beklenen.index)
        assert (t.set_index("cari_kod")["bakiye"] - beklenen).abs().max() < 0.01
        assert (t[dilim].sum(axis=1) - t["bakiye"]).abs().max() < 0.01      # dilimlerin toplamı bakiyeye eşit
    co = a.cari_ozet()
    assert co["alacak"] == pytest.approx(ham[ham > 0.005].sum(), abs=0.01)
    assert co["borc"] == pytest.approx(-ham[ham < -0.005].sum(), abs=0.01)
    assert 20 < co["tahsilat_gun"] < 150
    # tek bir müşteri için FIFO'yu elle doğrula: ödemeler en eski borçtan düşülür
    kod = al.iloc[0]["cari_kod"]
    h = pd.read_sql("""SELECT substr(F.DATE_, 1, 10) AS t, F.SIGN, F.AMOUNT FROM LG_001_02_CLFLINE F
                       JOIN LG_001_CLCARD C ON C.LOGICALREF = F.CLIENTREF
                       WHERE F.CANCELLED = 0 AND C.CODE = ? ORDER BY F.DATE_, F.LOGICALREF""", con, params=(kod,))
    kalan_odeme = h.loc[h["SIGN"] == 1, "AMOUNT"].sum()
    yas = dict.fromkeys(dilim, 0.0)
    for r in h[h["SIGN"] == 0].itertuples():
        dusulen = min(kalan_odeme, r.AMOUNT)
        kalan_odeme -= dusulen
        gun = (pd.Timestamp("2026-10-31") - pd.Timestamp(r.t)).days
        d = dilim[0 if gun <= 30 else 1 if gun <= 60 else 2 if gun <= 90 else 3 if gun <= 180 else 4]
        yas[d] += r.AMOUNT - dusulen
    for d in dilim:
        assert al.iloc[0][d] == pytest.approx(yas[d], abs=0.01), d


def test_temsilci_depo_toplamlari(depo):
    a = Analiz(depo)
    net = a.ozet()["net_satis"]
    t = a.temsilci_performans()
    assert t["net_tutar"].sum() == pytest.approx(net, abs=0.01)
    assert set(t["temsilci_ad"]) == {"AHMET YILMAZ", "AYŞE KAYA", "MURAT DEMİR", "ZEYNEP ÇELİK"}   # diğer firmanınkiler yok
    assert t["musteri_sayisi"].sum() == 150 and t["pay"].sum() == pytest.approx(1.0)
    d = a.depo_kirilim()
    assert d["ambar_ad"].tolist() == ["MERKEZ DEPO", "ŞUBE DEPO"]
    assert d["net_tutar"].sum() == pytest.approx(net, abs=0.01)
    assert d["stok_miktar"].sum() == pytest.approx(a.stok["stok_miktar"].sum(), abs=1e-6)   # ambar toplamı = genel stok


def test_musteri_kaybi_ve_siparis_onerisi(depo):
    a = Analiz(depo)
    k = a.musteri_kaybi()
    assert len(k) and set(k["durum"]) <= {"Kayıp", "Risk"}
    kayip, risk = k[k["durum"] == "Kayıp"], k[k["durum"] == "Risk"]
    assert (kayip["sessiz_gun"] >= (3 * kayip["ort_aralik_gun"]).clip(lower=60)).all()
    assert (risk["sessiz_gun"] >= (2 * risk["ort_aralik_gun"]).clip(lower=30)).all()
    assert (k["alim_sayisi"] >= 3).all()
    s = a.siparis_onerisi()
    assert len(s) and (s["stok_gun"] < 15).all() and (s["oneri_miktar"] > 0).all()
    assert (s.loc[s["durum"] == "Acil", "stok_gun"] < 7).all()
    # öneri stoğu 45 günlük ihtiyaca tamamlar
    hedef = s["stok_miktar"].clip(lower=0) + s["oneri_miktar"]
    assert ((hedef - s["gunluk_satis"] * 45).between(0, 1)).all()
    assert a.siparis_onerisi(tedarik_gun=1000, hedef_gun=1000)["malzeme_kod"].nunique() >= len(s)
    assert len(a.one_cikanlar()) >= 5


def test_eski_surum_analizi_acilir(tmp_path):
    """v0.3 ile oluşturulmuş analiz klasörleri (yeni tablolar/kolonlar yok) açılmaya ve rapor üretmeye devam etmeli."""
    yol, bilgi = _gecici_analiz(tmp_path)
    con = sqlite3.connect(yol)
    for t in ("fact_cari_hareket", "dim_temsilci", "dim_ambar", "stok_ambar", "malzeme_maliyet"):
        con.execute(f"DROP TABLE {t}")
    con.execute("ALTER TABLE fact_satis DROP COLUMN maliyet_tutar")
    con.execute("ALTER TABLE fact_satis DROP COLUMN birim_maliyet")
    con.commit(); con.close()
    from core import yz
    from core.pdf_ozet import pdf_ozeti
    from core.rapor import excel_raporu, powerbi_disa_aktar
    a = Analiz(yol)
    assert a.eski_surum and a.karlilik() is None and a.cari_ozet() is None and a.cari_yaslandirma() is None
    assert a.ozet()["brut_kar"] is None and len(a.musteri_kaybi()) and len(a.siparis_onerisi())
    excel_raporu(a, str(tmp_path / "r.xlsx"))
    powerbi_disa_aktar(a, yol, str(tmp_path / "pbi"))
    pdf_ozeti(a, bilgi, str(tmp_path / "o.pdf"))
    assert "Kârlılık" not in yz.baglam(a, bilgi, yz.Maske(a, bilgi["firma_ad"], True))


def test_pdf_ve_excel_yeni_sayfalar(tmp_path):
    import openpyxl
    from core.pdf_ozet import pdf_ozeti
    from core.rapor import excel_raporu
    yol, bilgi = _gecici_analiz(tmp_path)
    a = Analiz(yol)
    pdf = pdf_ozeti(a, bilgi, str(tmp_path / "ozet.pdf"))
    with open(pdf, "rb") as fh:
        assert fh.read(5) == b"%PDF-"
    excel_raporu(a, str(tmp_path / "r.xlsx"))
    wb = openpyxl.load_workbook(str(tmp_path / "r.xlsx"))
    assert {"Ürün Kârlılık", "Müşteri Kârlılık", "Grup Kârlılık", "Alacak Yaşlandırma", "Borç Yaşlandırma", "Temsilci",
            "Müşteri Kaybı", "Sipariş Önerisi", "Depo"} <= set(wb.sheetnames)
    ws = wb["Alacak Yaşlandırma"]
    assert ws["A4"].value == "Cari kodu" and ws["D5"].value == pytest.approx(a.cari_yaslandirma().iloc[0]["bakiye"])


def test_powerbi_hazir_rapor(tmp_path):
    """Hazır Power BI raporu (.pbit): model ile rapor sayfaları ve CSV dosyaları birbiriyle tutarlı olmalı.
    (Power BI Desktop'ta açılış ayrıca elle denenir; burada dosyanın iç tutarlılığı doğrulanır.)"""
    import json
    import zipfile
    from core import powerbi
    from core.rapor import powerbi_disa_aktar
    yol, bilgi = _gecici_analiz(tmp_path)
    klasor = str(tmp_path / "PowerBI_test")
    zip_yol = powerbi_disa_aktar(Analiz(yol), yol, klasor)
    assert powerbi.RAPOR_ADI in zipfile.ZipFile(zip_yol).namelist()
    z = zipfile.ZipFile(os.path.join(klasor, powerbi.RAPOR_ADI))
    assert {"Version", "[Content_Types].xml", "DataModelSchema", "Report/Layout", "Settings", "Metadata"} <= set(z.namelist())
    model = json.loads(z.read("DataModelSchema").decode("utf-16-le"))["model"]
    rapor = json.loads(z.read("Report/Layout").decode("utf-16-le"))
    assert json.loads(model["expressions"][0]["expression"]).rstrip("/\\") == os.path.abspath(klasor)   # veri yolu
    kucuk = lambda x: x.replace("İ", "i").replace("I", "ı").lower()
    alanlar, olculer = set(), []
    for t in model["tables"]:
        adlar = [kucuk(c["name"]) for c in t["columns"]] + [kucuk(o["name"]) for o in t.get("measures", [])]
        assert len(adlar) == len(set(adlar)), f"{t['name']}: ad çakışması (Power BI büyük/küçük harf ayırmaz)"
        alanlar |= {(t["name"], c["name"]) for c in t["columns"]} | {(t["name"], o["name"]) for o in t.get("measures", [])}
        olculer += [kucuk(o["name"]) for o in t.get("measures", [])]
        # sorgunun okuduğu CSV var ve istenen kolonları içeriyor
        dosya, kolonlar = powerbi.T[t["name"]]
        baslik = pd.read_csv(os.path.join(klasor, dosya), nrows=0, encoding="utf-8-sig").columns
        assert not [c for _, c, _, _ in kolonlar if c not in baslik], dosya
    assert len(olculer) == len(set(olculer))
    for r in model["relationships"]:
        assert (r["fromTable"], r["fromColumn"]) in alanlar and (r["toTable"], r["toColumn"]) in alanlar
    assert [s["displayName"] for s in rapor["sections"]] == ["Özet", "Kârlılık", "Alacak", "Müşteri ve Temsilci", "Stok"]
    for s in rapor["sections"]:
        for v in s["visualContainers"]:
            q = json.loads(v["config"])["singleVisual"]["prototypeQuery"]
            takma = {f["Name"]: f["Entity"] for f in q["From"]}
            for sec in q["Select"]:
                x = sec.get("Measure") or sec.get("Column")
                assert (takma[x["Expression"]["SourceRef"]["Source"]], x["Property"]) in alanlar, sec["Name"]


def test_arayuz_ve_raporlar(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(tmp_path))
    import importlib
    import app as A
    importlib.reload(A)
    c = A.app.test_client()

    def bekle():
        for _ in range(300):
            d = c.get("/durum").json
            if not d["calisiyor"]:
                return d
            time.sleep(0.1)
        raise TimeoutError

    assert c.get("/").status_code == 200
    c.post("/kaynak", data={"tur": "demo"})
    d = bekle()
    assert d["hata"] is None and d["sonraki"] == "/firmalar"
    c.post("/aktar", data={"firma": "001", "donem_001": ["01", "02"], "sadece_faturali": "1"})
    d = bekle()
    assert d["hata"] is None
    assert c.get(d["sonraki"]).status_code == 200
    aid = d["sonraki"].rsplit("/", 1)[-1]
    r = c.get(f"/indir/{aid}/excel")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    r = c.get(f"/indir/{aid}/powerbi")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    import io
    import zipfile
    adlar = set(zipfile.ZipFile(io.BytesIO(r.data)).namelist())
    assert {"fact_cari_hareket.csv", "dim_temsilci.csv", "stok_ambar.csv", "alacak_yaslandirma.csv", "karlilik_urun.csv"} <= adlar
    assert "LogoTigerAnaliz_Rapor.pbit" in adlar
    if sys.platform != "win32":      # Windows dışında rapor hazırlanır ama açılmaz; açıklayıcı mesaj döner
        r = c.post(f"/powerbi-ac/{aid}").json
        assert r["ok"] is False and "Windows" in r["mesaj"]
    r = c.get(f"/indir/{aid}/pdf")
    assert r.status_code == 200 and r.data[:5] == b"%PDF-"
    r = c.get(f"/indir/{aid}/yzpaket")
    assert r.status_code == 200 and "AHMET YILMAZ" not in "".join(
        zipfile.ZipFile(io.BytesIO(r.data)).read(n).decode("utf-8") for n in zipfile.ZipFile(io.BytesIO(r.data)).namelist())
    panel = c.get(f"/panel/{aid}").get_data(as_text=True)
    for baslik in ("Öne çıkanlar", "Kârlılık", "Alacak yaşlandırma", "Müşteri kaybı", "Sipariş önerisi",
                   "Satış temsilcileri", "Depo (ambar) kırılımı"):
        assert baslik in panel, baslik
    assert "<script>" not in panel.split("</title>")[0]      # sekme başlığına betik karışmamalı


def test_kurulum_ayar_sifre_saklama(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(tmp_path))
    from core import kurulum
    kurulum.ayar_yaz({"sql": {"tur": "docker", "sunucu": "127.0.0.1", "port": 14333, "kullanici": "sa", "sifre": "Gizli!123"}})
    ham = (tmp_path / "ayarlar.json").read_text(encoding="utf-8")
    assert "Gizli!123" not in ham          # düz metin olarak yazılmamalı
    assert kurulum.ayar_oku()["sql"]["sifre"] == "Gizli!123"


def test_kurulum_sifre_karmasikligi():
    from core import kurulum
    for _ in range(50):
        s = kurulum._guclu_sifre()
        assert len(s) == 24 and any(c.isupper() for c in s) and any(c.islower() for c in s)
        assert any(c.isdigit() for c in s) and any(c in "!#%+-=" for c in s)
        assert not any(c in s for c in "\"'`$;&|<> ")  # ini / komut satırı için güvenli


def test_turkce_onarim():
    from core.logo import turkce_onar
    assert turkce_onar("MÜÞTERÝ 150 TÝC. LTD. ÞTÝ.") == "MÜŞTERİ 150 TİC. LTD. ŞTİ."
    assert turkce_onar("DAÐITIM ÇÖZÜMLERÝ") == "DAĞITIM ÇÖZÜMLERİ"
    assert turkce_onar("MÜŞTERİ A.Ş.") == "MÜŞTERİ A.Ş."      # doğru metne dokunulmaz
    assert turkce_onar("ABC Ltd") == "ABC Ltd"
    assert turkce_onar(None) is None


# ---------------------------------------------------------------------------- yapay zekâ
def _gecici_analiz(tmp_path):
    yol = str(tmp_path / "analiz.sqlite")
    k = SqliteKaynak(DEMO)
    f = next(f for f in firmalari_tara(k) if f.no == "001")
    aktar(k, f, f.donemler, yol)
    return yol, {"firma": "001", "firma_ad": f.ad, "donemler": f.donemler}


def test_yz_maske_ve_sql(tmp_path):
    from core import yz
    yol, bilgi = _gecici_analiz(tmp_path)
    a = Analiz(yol)
    m = yz.Maske(a, bilgi["firma_ad"], True)
    baglam = yz.baglam(a, bilgi, m)
    assert "MÜŞTERİ 1" not in baglam and "DEMO YAPI" not in baglam and "Cari-0150" in baglam
    assert "YILMAZ" not in baglam and "Temsilci-01" in baglam        # satış temsilcisi adları da gizlenir
    for bolum in ("## Kârlılık", "## Cari bakiyeler", "## Satış temsilcisi performansı", "## Müşteri kaybı",
                  "## Sipariş önerisi", "## Depo (ambar) kırılımı"):
        assert bolum in baglam, bolum
    assert m.ac(m.gizle("MÜŞTERİ 150 TİC. LTD. ŞTİ.")) == "MÜŞTERİ 150 TİC. LTD. ŞTİ."
    # takma adla yazılan sorgu gerçek adla çalışır, sonuç yine takma adla döner
    r = yz.sql_calistir(yol, "SELECT cari_ad FROM dim_cari WHERE cari_ad = 'Cari-0150'", m)
    assert "Cari-0150" in r and "MÜŞTERİ" not in r and r.startswith("1 satır")
    for kotu in ("DELETE FROM dim_cari", "SELECT 1; DROP TABLE dim_cari", "UPDATE dim_cari SET cari_ad='x'"):
        assert yz.sql_calistir(yol, kotu, m).startswith("HATA")


def test_yz_sor_sahte_model(tmp_path, monkeypatch):
    from core import yz
    yol, bilgi = _gecici_analiz(tmp_path)
    giden = []

    def sahte(sag, anahtar, model, sistem, mesajlar, azami=2500):
        giden.append(sistem + json_dumps(mesajlar))
        if len(mesajlar) == 1:
            return "```sql\nSELECT c.cari_ad, SUM(net_tutar) t FROM fact_satis JOIN dim_cari c USING(cari_ref) GROUP BY 1 ORDER BY t DESC LIMIT 1\n```"
        assert "Cari-0150" in mesajlar[-1]["content"]
        return "En büyük müşteri **Cari-0150**."

    monkeypatch.setattr(yz, "sohbet", sahte)
    r = yz.sor(Analiz(yol), yol, bilgi, "MÜŞTERİ 150 TİC. LTD. ŞTİ. en büyük mü?",
               a={"saglayici": "gemini", "anahtar": "x", "model": "m", "anonim": True})
    assert r["cevap"] == "En büyük müşteri **MÜŞTERİ 150 TİC. LTD. ŞTİ.**."
    assert len(r["sorgular"]) == 1
    assert not any("MÜŞTERİ 1" in g or "DEMO YAPI" in g for g in giden), "gerçek ad yapay zekâya gitmemeli"


def json_dumps(x):
    import json
    return json.dumps(x, ensure_ascii=False)


def test_model_secimi():
    from core import yz
    assert yz.model_sec("gemini", ["gemini-2.5-pro", "gemini-3.6-flash", "gemini-3.6-flash-lite"]) == "gemini-3.6-flash"
    assert yz.model_sec("gemini", ["gemini-flash-latest", "gemini-3.6-flash"]) == "gemini-flash-latest"
    assert yz.model_sec("openai", ["gpt-4o", "gpt-6.1-mini", "gpt-5.4-mini"]) == "gpt-5.4-mini"  # tercih listesi önce
    assert yz.model_sec("openai", ["gpt-4o", "gpt-6.1-mini", "gpt-7-mini"]) == "gpt-7-mini"  # yoksa en yeni
    assert yz.model_sec("deepseek", ["deepseek-pro"]) is None


def test_md_html():
    from core import yz
    h = yz.md_html("## Başlık\n- **kalın** <script>\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<h4>Başlık</h4>" in h and "<b>kalın</b>" in h and "&lt;script&gt;" in h and "<td>1</td>" in h


def test_mcp_protokol(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(tmp_path))
    import json
    from core import mcp_sunucu
    klasor = tmp_path / "20260101_000000_F001"
    klasor.mkdir()
    k = SqliteKaynak(DEMO)
    f = next(f for f in firmalari_tara(k) if f.no == "001")
    aktar(k, f, f.donemler, str(klasor / "analiz.sqlite"))
    (klasor / "bilgi.json").write_text(json.dumps({"firma": "001", "firma_ad": f.ad, "donemler": f.donemler}), encoding="utf-8")
    r = mcp_sunucu.isle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert r["result"]["protocolVersion"] == "2025-06-18" and "tools" in r["result"]["capabilities"]
    assert mcp_sunucu.isle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    araclar = mcp_sunucu.isle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in araclar} == {"analizleri_listele", "analiz_ozeti", "sql_sorgula"}
    oz = mcp_sunucu.isle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "analiz_ozeti", "arguments": {}}})
    assert not oz["result"]["isError"] and "DEMO YAPI" not in oz["result"]["content"][0]["text"]
    kotu = mcp_sunucu.isle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                            "params": {"name": "sql_sorgula", "arguments": {"sql": "DROP TABLE dim_cari"}}})
    assert kotu["result"]["isError"]


def test_tanilama_paketi_musteri_verisi_icermez(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(tmp_path))
    import importlib
    import io
    import zipfile
    import app as A
    importlib.reload(A)
    c = A.app.test_client()

    def bekle():
        for _ in range(300):
            d = c.get("/durum").json
            if not d["calisiyor"]:
                return d
            time.sleep(0.1)

    c.post("/kaynak", data={"tur": "demo"}); bekle()
    c.post("/aktar", data={"firma": "001", "donem_001": ["01", "02"], "sadece_faturali": "1"}); bekle()
    assert "DEMO YAPI MARKET" in (tmp_path / "gunluk.txt").read_text(encoding="utf-8")  # yerel günlükte var
    r = c.get("/tanilama")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.data))
    assert {"sistem.json", "gunluk.txt", "OKUBENI.txt"} <= set(z.namelist())
    hepsi = "".join(z.read(n).decode("utf-8") for n in z.namelist())
    for yasak in ("DEMO YAPI", "DEMO ŞUBE", "MÜŞTERİ", "TEDARİKÇİ", "YILMAZ", "MERKEZ DEPO"):
        assert yasak not in hepsi, yasak
    assert '"fact_satis":' in hepsi and "Aktarım tamamlandı" in hepsi
