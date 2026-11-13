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
    assert [s["displayName"] for s in rapor["sections"]] == ["Özet", "Kârlılık", "Alacak", "Müşteri ve Temsilci", "Stok",
                                                               "Ambar"]
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


# ---------------------------------------------------------------------------- iki yedek: LOGODB + firma verileri
def _bol(tmp_path, ek_firma=True):
    """Demo veritabanını Logo'daki gibi ikiye böler: sistem (L_CAPI… = LOGODB) ve firma verileri (LG_…)."""
    src = sqlite3.connect(DEMO)
    sistem, veri = str(tmp_path / "LOGODB.sqlite"), str(tmp_path / "TIGERDB.sqlite")
    for hedef, kosul in ((sistem, "name LIKE 'L\\_CAPI%' ESCAPE '\\'"), (veri, "name NOT LIKE 'L\\_CAPI%' ESCAPE '\\'")):
        con = sqlite3.connect(hedef)
        for ad, ddl in src.execute(f"SELECT name, sql FROM sqlite_master WHERE type='table' AND {kosul}").fetchall():
            con.execute(ddl)
            rows = src.execute(f"SELECT * FROM {ad}").fetchall()
            if rows:
                con.executemany(f"INSERT INTO {ad} VALUES ({','.join('?' * len(rows[0]))})", rows)
        if ek_firma and hedef == sistem:  # LOGODB'de tanımlı ama veri yedeğinde olmayan firma
            con.execute("INSERT INTO L_CAPIFIRM VALUES (3, 'BAŞKA FİRMA', 'BAŞKA FİRMA A.Ş.')")
        con.commit()
        con.close()
    return sistem, veri


def test_rol_tespiti(tmp_path):
    from core.kaynak import rol
    sistem, veri = _bol(tmp_path)
    assert rol(SqliteKaynak(sistem)) == "sistem"
    assert rol(SqliteKaynak(veri)) == "veri"
    assert rol(SqliteKaynak(DEMO)) == "veri"


def test_sadece_logodb_anlasilir_hata(tmp_path):
    from core.kaynak import KaynakHatasi, birlestir
    sistem, _ = _bol(tmp_path)
    with pytest.raises(KaynakHatasi, match="LOGODB"):
        birlestir([SqliteKaynak(sistem)])


@pytest.mark.parametrize("sira", ["sistem_once", "veri_once"])
def test_iki_yedek_birlesir(tmp_path, sira):
    from core.kaynak import BirlesikKaynak, birlestir
    from core.logo import firma_tanimlari
    sistem, veri = _bol(tmp_path)
    ks = [SqliteKaynak(sistem), SqliteKaynak(veri)]
    if sira == "veri_once":
        ks.reverse()
    log = []
    k = birlestir(ks, log.append)
    assert isinstance(k, BirlesikKaynak) and k.kaynaklar[0].ad == "TIGERDB.sqlite"  # veri yedeği her zaman önce
    assert any("sistem veritabanı" in m for m in log)
    fs = {f.no: f for f in firmalari_tara(k)}
    assert set(fs) == {"001", "002"}                       # 003 tanımlı ama verisi yok -> listede değil
    assert fs["001"].ad == "DEMO YAPI MARKET" and fs["002"].ad == "DEMO ŞUBE"
    assert fs["001"].donem_tarih["01"] == "01.01.2025 – 31.12.2025"
    assert firma_tanimlari(k)["003"] == "BAŞKA FİRMA"
    # analiz sonucu tek veritabanıyla aynı; ambar adları LOGODB'den gelir
    yol = str(tmp_path / "a.sqlite")
    aktar(k, fs["001"], fs["001"].donemler, yol)
    k.kapat()
    ref = str(tmp_path / "ref.sqlite")
    r = SqliteKaynak(DEMO)
    aktar(r, next(f for f in firmalari_tara(r) if f.no == "001"), ["01", "02"], ref)
    a, b = Analiz(yol), Analiz(ref)
    for anahtar in ("net_satis", "net_alis", "brut_kar", "musteri_sayisi"):
        assert a.ozet()[anahtar] == pytest.approx(b.ozet()[anahtar], abs=0.01), anahtar
    assert a.depo_kirilim()["ambar_ad"].tolist() == ["MERKEZ DEPO", "ŞUBE DEPO"]


def test_logodb_olmadan_firma_adi_bos_ama_calisir(tmp_path):
    _, veri = _bol(tmp_path)
    fs = {f.no: f for f in firmalari_tara(SqliteKaynak(veri))}
    assert set(fs) == {"001", "002"} and fs["001"].ad == "" and fs["001"].donem_tarih == {}


def test_firma_secim_ekrani_iki_yedek(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(tmp_path / "calisma"))
    import importlib
    import app as A
    from core.kaynak import birlestir
    importlib.reload(A)
    sistem, veri = _bol(tmp_path)
    A._tara(birlestir([SqliteKaynak(sistem), SqliteKaynak(veri)]), "LOGODB.bak + TIGERDB.bak")
    h = A.app.test_client().get("/firmalar").get_data(as_text=True)
    assert "DEMO YAPI MARKET" in h and "DEMO ŞUBE" in h and "01.01.2025 – 31.12.2025" in h
    assert "verisi olmayan 1 firma" in h and "BAŞKA FİRMA" in h
    assert 'class="firma-sec" name="firma" value="001" id="r001" >' in h   # birden çok firmada hiçbiri önceden seçili değil
    A._kaynagi_kapat()


def test_ana_sayfa_ikinci_yedek_alani():
    import app as A
    h = A.app.test_client().get("/").get_data(as_text=True)
    assert 'name="bak_dosya2"' in h and 'name="bak_yol2"' in h


def test_iki_yedek_arayuz_akisi(tmp_path, monkeypatch):
    """/kaynak iki yolu alır, ikisini de açar, birleştirir; hata olursa açılanları kapatır (geçici veritabanı silinir)."""
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(tmp_path / "calisma"))
    import importlib
    import app as A
    importlib.reload(A)
    sistem, veri = _bol(tmp_path)
    acilan, kapanan = [], []

    class SahteBak(SqliteKaynak):
        tur = "bak"

        def __init__(self, yol, **kw):
            if "BOZUK" in yol:
                raise A.KaynakHatasi("Yedek okunamadı")
            super().__init__(yol)
            acilan.append(self.ad)

        def kapat(self):
            kapanan.append(self.ad)
            super().kapat()

    monkeypatch.setattr(A, "BakKaynak", SahteBak)
    monkeypatch.setattr(A.kurulum, "hazirla", lambda say: {"sunucu": "x", "port": 1, "kullanici": "", "sifre": ""})
    c = A.app.test_client()

    def bekle():
        for _ in range(300):
            d = c.get("/durum").json
            if not d["calisiyor"]:
                return d
            time.sleep(0.05)

    c.post("/kaynak", data={"tur": "bak", "bak_yol": sistem, "bak_yol2": f'"{veri}"', "sql_kaynak": "otomatik"})
    d = bekle()
    assert d["hata"] is None and d["sonraki"] == "/firmalar", d
    assert acilan == ["LOGODB.sqlite", "TIGERDB.sqlite"]
    assert "DEMO YAPI MARKET" in c.get("/firmalar").get_data(as_text=True)
    c.post("/aktar", data={"firma": "002", "donem_002": ["01"], "sadece_faturali": "1"})
    d = bekle()
    assert d["hata"] is None, d
    assert sorted(kapanan) == ["LOGODB.sqlite", "TIGERDB.sqlite"]       # iş bitince ikisi de kapatıldı

    # ikinci yedek açılamazsa birincinin geçici veritabanı da silinmeli
    bozuk = tmp_path / "BOZUK.bak"
    bozuk.write_bytes(b"x")
    acilan.clear(); kapanan.clear()
    c.post("/kaynak", data={"tur": "bak", "bak_yol": veri, "bak_yol2": str(bozuk), "sql_kaynak": "otomatik"})
    d = bekle()
    assert "okunamadı" in d["hata"] and acilan == ["TIGERDB.sqlite"] and kapanan == ["TIGERDB.sqlite"]

    # olmayan dosya ve üçten fazla yedek reddedilir
    assert "bulunamadı" in c.post("/kaynak", data={"tur": "bak", "bak_yol2": str(tmp_path / "yok.bak")}).get_data(as_text=True)
    r = c.post("/kaynak", data={"tur": "bak", "bak_yol": f"{sistem}\n{veri}", "bak_yol2": str(bozuk)})
    assert "En fazla iki" in r.get_data(as_text=True)


# ---------------------------------------------------------------------------- yıl yıl ayrı firma: birleşik analiz
KAYDIR = 5000  # yeni yılın firmasında kartların LOGICALREF'i farklı: eşleştirme koda göre yapılmalı


def _yillik_firmalar(tmp_path, kod_on=""):
    """Demo firma 001'i Logo'da her yıl ayrı firma açılmış gibi böler: 010 = 2025 (dönem 01), 011 = 2026 (dönem 01).
    011'de cari/malzeme LOGICALREF'leri kaydırılır; kod_on verilirse 011'in cari kodları değiştirilir (farklı şirket)."""
    yol = str(tmp_path / "yillik.sqlite")
    src = sqlite3.connect(DEMO)
    con = sqlite3.connect(yol)

    def kopya(eski, yeni, donustur=None):
        ddl = src.execute("SELECT sql FROM sqlite_master WHERE name=?", (eski,)).fetchone()[0]
        con.execute(ddl.replace(eski, yeni, 1))
        kolon = [r[1] for r in src.execute(f"PRAGMA table_info({eski})")]
        rows = [dict(zip(kolon, r)) for r in src.execute(f"SELECT * FROM {eski}")]
        if donustur:
            rows = [donustur(r) for r in rows]
        con.executemany(f"INSERT INTO {yeni} VALUES ({','.join('?' * len(kolon))})", [[r[k] for k in kolon] for r in rows])

    def kart(r):
        r["LOGICALREF"] += KAYDIR
        if kod_on and "DEFINITION_" in r:
            r["CODE"] = kod_on + r["CODE"]
        return r

    def hareket(r):
        for k in ("STOCKREF", "CLIENTREF"):
            if k in r and r[k]:
                r[k] += KAYDIR
        return r

    for t in ("CLCARD", "ITEMS"):
        kopya(f"LG_001_{t}", f"LG_010_{t}")
        kopya(f"LG_001_{t}", f"LG_011_{t}", kart)
    for t in ("INVOICE", "STLINE", "CLFLINE"):
        kopya(f"LG_001_01_{t}", f"LG_010_01_{t}")
        kopya(f"LG_001_02_{t}", f"LG_011_01_{t}", hareket)
    for t in ("LG_SLSMAN", "L_CAPIWHOUSE"):
        kopya(t, t, (lambda r: {**r, "FIRMNR": 10}) if t == "L_CAPIWHOUSE" else None)
    con.execute("INSERT INTO L_CAPIWHOUSE SELECT 11, NR, NAME FROM L_CAPIWHOUSE WHERE FIRMNR = 10")
    con.execute("CREATE TABLE L_CAPIFIRM (NR INTEGER, NAME TEXT, TITLE TEXT)")
    con.executemany("INSERT INTO L_CAPIFIRM VALUES (?,?,?)", [(10, "DEMO YAPI 2025", ""), (11, "DEMO YAPI 2026", "")])
    con.commit()
    con.close()
    return yol


def _ref_analiz(tmp_path):
    ref = str(tmp_path / "ref.sqlite")
    k = SqliteKaynak(DEMO)
    aktar(k, next(f for f in firmalari_tara(k) if f.no == "001"), ["01", "02"], ref)
    return Analiz(ref)


def test_yillik_firmalar_birlesik_analiz(tmp_path):
    from core.logo import aktar_coklu
    k = SqliteKaynak(_yillik_firmalar(tmp_path))
    fs = {f.no: f for f in firmalari_tara(k)}
    assert set(fs) == {"010", "011"}
    yol = str(tmp_path / "birlesik.sqlite")
    log = []
    # seçim sırası önemsiz: tarihe göre sıralanır, stok/cari bakiye en yeni firmadan (011) alınır
    q = aktar_coklu(k, [(fs["011"], ["01"]), (fs["010"], ["01"])], yol, ilerleme=log.append)
    assert q["firma"] == "010-011" and q["firma_ad"] == "DEMO YAPI 2026" and q["donemler"] == "010/01, 011/01"
    assert "uyari" not in q, q.get("uyari")
    a, b = Analiz(yol), _ref_analiz(tmp_path)
    oa, ob = a.ozet(), b.ozet()
    for anahtar in ("net_satis", "net_alis", "iade_tutar", "brut_kar", "fatura_sayisi", "musteri_sayisi",
                    "urun_sayisi", "tedarikci_sayisi"):
        assert oa[anahtar] == pytest.approx(ob[anahtar], abs=0.01), anahtar
    # aynı müşteri iki yılda tek kişi sayılır (kod eşleşmesi): müşteri ABC ilk 20 aynı
    assert a.musteri_abc().head(20)["cari_kod"].tolist() == b.musteri_abc().head(20)["cari_kod"].tolist()
    assert len(a.cari) == len(b.cari) and len(a.malzeme) == len(b.malzeme)
    # stok ve alacak en yeni firmanın son döneminden: tek firmalı analizle aynı
    sa = a.stok.merge(a.malzeme[["malzeme_ref", "malzeme_kod"]]).set_index("malzeme_kod")["stok_miktar"].sort_index()
    sb = b.stok.merge(b.malzeme[["malzeme_ref", "malzeme_kod"]]).set_index("malzeme_kod")["stok_miktar"].sort_index()
    assert (sa - sb).abs().max() < 1e-6
    for anahtar in ("alacak", "borc"):
        assert a.cari_ozet()[anahtar] == pytest.approx(b.cari_ozet()[anahtar], abs=0.01)
    assert a.depo_kirilim()["ambar_ad"].tolist() == ["MERKEZ DEPO", "ŞUBE DEPO"]
    # geçen yıla göre karşılaştırma iki firmanın verisini birlikte kullanır
    assert len(a.donem_karsilastirma("cari")) == len(b.donem_karsilastirma("cari"))
    notlar = dict(a.veri_notlari())
    assert "Birleştirilen firmalar" in notlar and notlar["Stok bakiyesinin alındığı dönem"] == "011/01"


def test_yillik_firmalar_uyarilari(tmp_path):
    from core.kaynak import BirlesikKaynak
    from core.logo import aktar_coklu
    # tarihleri çakışan firmalar (aynı yıl iki firmada seçilmiş): çift sayım uyarısı
    (tmp_path / "a").mkdir()
    k = SqliteKaynak(_yillik_firmalar(tmp_path / "a"))
    fs = {f.no: f for f in firmalari_tara(k)}
    demo = SqliteKaynak(DEMO)
    f001 = next(f for f in firmalari_tara(demo) if f.no == "001")
    q = aktar_coklu(BirlesikKaynak([k, demo]), [(fs["010"], ["01"]), (f001, ["01"])], str(tmp_path / "c.sqlite"))
    assert "çakışıyor" in q["uyari"]
    # ortak cari kodu olmayan firmalar: farklı şirket uyarısı
    (tmp_path / "b").mkdir()
    k3 = SqliteKaynak(_yillik_firmalar(tmp_path / "b", kod_on="X"))
    fs3 = {f.no: f for f in firmalari_tara(k3)}
    q = aktar_coklu(k3, [(fs3["010"], ["01"]), (fs3["011"], ["01"])], str(tmp_path / "d.sqlite"))
    assert "farklı şirketler olabilir" in q["uyari"] and "çakışıyor" not in q["uyari"]


def test_yillik_firmalar_arayuz_ve_yz_maskesi(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(tmp_path / "calisma"))
    import importlib
    import json
    import app as A
    from core import yz
    importlib.reload(A)
    A._tara(SqliteKaynak(_yillik_firmalar(tmp_path)), "yillik.bak")
    c = A.app.test_client()
    assert 'type="checkbox" class="firma-sec" name="firma" value="010"' in c.get("/firmalar").get_data(as_text=True)
    # seçilen firmalardan birinin dönemi yoksa reddedilir
    r = c.post("/aktar", data={"firma": ["010", "011"], "donem_010": ["01"]}).get_data(as_text=True)
    assert "Firma 011 için en az bir dönem" in r
    c.post("/aktar", data={"firma": ["010", "011"], "donem_010": ["01"], "donem_011": ["01"], "sadece_faturali": "1"})
    for _ in range(600):
        d = c.get("/durum").json
        if not d["calisiyor"]:
            break
        time.sleep(0.05)
    assert d["hata"] is None, d
    aid = d["sonraki"].rsplit("/", 1)[-1]
    assert aid.endswith("_F010-011")
    klasor = tmp_path / "calisma" / aid
    bilgi = json.loads((klasor / "bilgi.json").read_text(encoding="utf-8"))
    assert bilgi["firma"] == "010-011" and bilgi["donemler"] == ["010/01", "011/01"]
    assert [f["no"] for f in bilgi["firmalar"]] == ["010", "011"] and "uyari" not in bilgi
    p = c.get(d["sonraki"]).get_data(as_text=True)
    assert "2 firma birleştirildi" in p and "DEMO YAPI 2025" in p
    for tur in ("excel", "pdf", "powerbi"):
        assert c.get(f"/indir/{aid}/{tur}").status_code == 200, tur
    # yapay zekâya giden bağlamda iki yılın firma adı da gizli
    a = Analiz(str(klasor / "analiz.sqlite"))
    m = yz.Maske(a, bilgi["firma_ad"], True)
    metin = m.gizle("DEMO YAPI 2025 ve DEMO YAPI 2026 karşılaştırması")
    assert "DEMO YAPI" not in metin and "FİRMA" in metin


# ---------------------------------------------------------------------------- ambar (depo) süzme ve kırılım
def test_ambar_suzme_ham_sql_ile_ayni(depo):
    """Yalnızca bir ambar seçildiğinde net satış, ham Logo tablosundan o ambar (SOURCEINDEX) için düz SQL ile aynı."""
    con = sqlite3.connect(DEMO)
    for ambar in (0, 1):
        beklenen = 0.0
        for p in ("01", "02"):
            beklenen += con.execute(f"""
                SELECT COALESCE(SUM(CASE WHEN S.TRCODE IN (7,8) THEN S.VATMATRAH ELSE -S.VATMATRAH END), 0)
                FROM LG_001_{p}_STLINE S LEFT JOIN LG_001_{p}_INVOICE I ON I.LOGICALREF = S.INVOICEREF
                WHERE S.TRCODE IN (2,3,7,8) AND S.LINETYPE = 0 AND S.CANCELLED = 0 AND S.INVOICEREF > 0
                  AND COALESCE(I.CANCELLED, 0) = 0 AND S.SOURCEINDEX = ?""", (ambar,)).fetchone()[0]
        assert Analiz(depo, ambar=ambar).ozet()["net_satis"] == pytest.approx(beklenen, abs=0.01)


def test_ambar_kirilimi_toplamlari_tutar(depo):
    tum = Analiz(depo)
    dk = tum.depo_kirilim().set_index("ambar")
    assert [x["ad"] for x in tum.ambarlar] == ["MERKEZ DEPO", "ŞUBE DEPO"]   # ciroya göre sıralı
    assert dk["net_tutar"].sum() == pytest.approx(tum.ozet()["net_satis"], abs=0.01)
    stok_top = 0.0
    for n in (0, 1):
        a = Analiz(depo, ambar=n)
        o = a.ozet()
        assert a.ambar_secili == n and a.ambar_secili_ad == dk.loc[n, "ambar_ad"]
        # depo tablosundaki satır = o ambar seçilince panelin gösterdiği rakamlar
        assert dk.loc[n, "net_tutar"] == pytest.approx(o["net_satis"], abs=0.01)
        assert dk.loc[n, "fatura_sayisi"] == o["fatura_sayisi"]
        assert dk.loc[n, "musteri_sayisi"] == o["musteri_sayisi"]
        assert dk.loc[n, "iade_tutar"] == pytest.approx(o["iade_tutar"], abs=0.01)
        assert dk.loc[n, "ortalama_fatura"] == pytest.approx(o["ortalama_fatura"], abs=0.01)
        assert (a.satis["ambar"] == n).all() and (a.alis["ambar"] == n).all()
        assert a.depo_kirilim() is None and a.ambar_urun() is None   # tek ambar seçiliyken kırılım yok
        a.urun_abc(); a.musteri_abc(); a.aylik_trend(); a.olu_stok(90); a.siparis_onerisi(); a.karlilik("malzeme")
        stok_top += a.stok["stok_miktar"].sum()
    assert stok_top == pytest.approx(tum.stok["stok_miktar"].sum(), abs=1e-6)
    au = tum.ambar_urun()
    assert au.groupby("ambar")["net_tutar"].sum().to_dict() == pytest.approx(dk["net_tutar"].to_dict(), abs=0.01)
    assert au.groupby("ambar")["sira"].min().tolist() == [1, 1]
    aa = tum.ambar_aylik()
    assert list(aa.columns) == ["yil_ay", "MERKEZ DEPO", "ŞUBE DEPO", "Toplam"]
    assert aa["Toplam"].sum() == pytest.approx(tum.ozet()["net_satis"], abs=0.01)


def test_ambar_panel_ve_excel(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(tmp_path))
    import importlib
    import io
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
    c.post("/aktar", data={"firma": "001", "donem_001": ["01", "02"], "sadece_faturali": "1"})
    aid = bekle()["sonraki"].rsplit("/", 1)[-1]
    h = c.get(f"/panel/{aid}").get_data(as_text=True)
    assert "Tüm ambarlar" in h and f"/panel/{aid}?ambar=1" in h and "Ambar bazında en çok çıkan ürünler" in h
    h = c.get(f"/panel/{aid}?ambar=1").get_data(as_text=True)
    assert "Yalnızca <b>ŞUBE DEPO</b> gösteriliyor" in h and f"/indir/{aid}/excel?ambar=1" in h
    assert c.get(f"/panel/{aid}?ambar=99").status_code == 404
    assert c.get(f"/panel/{aid}?ambar=x").status_code == 400
    r = c.get(f"/indir/{aid}/excel?ambar=1")
    assert r.status_code == 200 and "UBE_DEPO" in r.headers["Content-Disposition"]
    sayfalar = pd.ExcelFile(io.BytesIO(r.data)).sheet_names
    assert "Ambar-Ürün" not in sayfalar   # tek ambar seçiliyken kırılım sayfaları yok
    tam = pd.ExcelFile(io.BytesIO(c.get(f"/indir/{aid}/excel").data))
    assert {"Depo", "Ambar-Ürün", "Ambar Aylık"} <= set(tam.sheet_names)
    au = tam.parse("Ambar-Ürün", header=3)
    assert set(au["Ambar"]) == {"MERKEZ DEPO", "ŞUBE DEPO"} and "Ambar içindeki payı" in au.columns
    assert c.get(f"/indir/{aid}/pdf?ambar=0").status_code == 200
    A._kaynagi_kapat()


# ---------------------------------------------------------------------------- disk alanı: yükleme ve geri yükleme
def test_yuklenen_yedek_kopyalanmadan_tasinir(tmp_path, monkeypatch):
    """Tarayıcıdan yüklenen yedek _yukleme'ye bir kez yazılır, kopyalanmaz; BakKaynak'a taşınabilir diye bildirilir.
    Boş dosya alanı ya da yarım yüklemeden geçici dosya kalmaz."""
    import errno
    import importlib
    import io
    import app as A
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(tmp_path / "calisma"))
    importlib.reload(A)
    gelen = []

    class SahteBak(SqliteKaynak):
        tur = "bak"

        def __init__(self, yol, tasi=False, **kw):
            gelen.append((os.path.basename(yol), tasi, open(yol, "rb").read()))
            super().__init__(DEMO)

    monkeypatch.setattr(A, "BakKaynak", SahteBak)
    monkeypatch.setattr(A.kurulum, "hazirla", lambda say: {"sunucu": "x", "port": 1, "kullanici": "", "sifre": ""})
    kopyalandi = []
    monkeypatch.setattr(A.shutil, "copyfileobj", lambda *a, **k: kopyalandi.append(1))
    c = A.app.test_client()
    os.makedirs(A.YUKLEME, exist_ok=True)
    (tmp_path / "calisma" / "_yukleme" / "yukleniyor_eski.tmp").write_bytes(b"yarim")  # önceki yarım yükleme

    c.post("/kaynak", data={"tur": "bak", "sql_kaynak": "otomatik",
                            "bak_dosya": (io.BytesIO(b"YEDEK" * 1000), "TIGER.bak"),
                            "bak_dosya2": (io.BytesIO(b""), "")}, content_type="multipart/form-data")
    for _ in range(300):
        d = c.get("/durum").json
        if not d["calisiyor"]:
            break
        time.sleep(0.05)
    assert d["hata"] is None, d
    assert gelen == [("1_TIGER.bak", True, b"YEDEK" * 1000)] and not kopyalandi
    assert os.listdir(A.YUKLEME) == []                     # eski yarım yükleme ve yüklenen kopya temizlendi
    A._kaynagi_kapat()

    # disk dolunca: anlaşılır mesaj, yarım dosya kalmaz
    def dolu(dosya, hedef):
        open(hedef, "wb").write(b"yarim")
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(A, "_yuklemeyi_kaydet", dolu)
    r = c.post("/kaynak", data={"tur": "bak", "bak_dosya": (io.BytesIO(b"x" * 10), "TIGER.bak")},
               content_type="multipart/form-data")
    h = r.get_data(as_text=True)
    assert r.status_code == 507 and "Diskte yer kalmadı" in h and "tam yolunu" in h
    assert os.listdir(A.YUKLEME) == []


def _bak_kaynak_yalin(tmp_path, sorgular=None):
    from core.kaynak import BakKaynak
    b = BakKaynak.__new__(BakKaynak)
    b._ilerleme, b._kopya, b.gecici_db = (lambda m: None), None, "LOGOANALIZ_ABCDEF12"
    b._sorgu_m = lambda sql, p=None: (sorgular.append(sql) if sorgular is not None else None) or []
    return b


def test_yarim_kopya_silinir_ve_tasima(tmp_path, monkeypatch):
    import errno
    import shutil
    from core import kaynak
    kaynak_dosya = tmp_path / "TIGER.bak"
    kaynak_dosya.write_bytes(b"x" * 100)
    yedek = tmp_path / "yedek"
    yedek.mkdir()
    b = _bak_kaynak_yalin(tmp_path)

    def dolu(a, hedef):
        open(hedef, "wb").write(b"yarim")
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(shutil, "copyfile", dolu)
    yol = b._dosyayi_hazirla(str(kaynak_dosya), str(yedek), None)
    assert yol == str(kaynak_dosya) and os.listdir(yedek) == []    # yarım kopya silindi, yedek yerinden okunur
    monkeypatch.undo()

    # yer yoksa kopyalamaya hiç girişmez
    monkeypatch.setattr(kaynak, "_bos_alan", lambda y: 10)
    assert b._dosyayi_hazirla(str(kaynak_dosya), str(yedek), None) == str(kaynak_dosya) and os.listdir(yedek) == []
    monkeypatch.undo()

    # taşıma: kopya yok, dosya yedek klasörüne geçer
    b._dosyayi_hazirla(str(kaynak_dosya), str(yedek), None, tasi=True)
    assert not kaynak_dosya.exists() and os.listdir(yedek) == ["LOGOANALIZ_ABCDEF12.bak"]


def test_geri_yukleme_yer_kontrolu(tmp_path, monkeypatch):
    from core import kaynak
    monkeypatch.setattr(kaynak, "_bos_alan", lambda y: 5 * 1024 ** 3)
    dosyalar = [{"Type": "D", "Size": 8 * 1024 ** 3}, {"Type": "L", "Size": 1024 ** 3}]
    with pytest.raises(kaynak.KaynakHatasi, match="yer yetmiyor.*9,0 GB"):
        kaynak._yer_kontrolu(dosyalar, str(tmp_path), str(tmp_path))
    kaynak._yer_kontrolu([{"Type": "D", "Size": 1024 ** 3}], str(tmp_path), str(tmp_path))   # sığıyor
    kaynak._yer_kontrolu(dosyalar, "/var/opt/mssql/data-yok", "/var/opt/mssql/data-yok")      # Docker: kontrol edilemez


def test_onceki_denemelerin_artiklari_temizlenir(tmp_path):
    from core import kaynak
    yedek = tmp_path / "yedek"
    yedek.mkdir()
    for ad in ("LOGOANALIZ_00000001.bak", "LOGOANALIZ_ABCDEF12.bak", "musteri.bak"):
        (yedek / ad).write_bytes(b"x")
    sorgular = []
    b = _bak_kaynak_yalin(tmp_path, sorgular)
    tum = ["LOGOANALIZ_00000001", "LOGOANALIZ_ABCDEF12", "LOGOANALIZ_elle", "TIGERDB"]
    b._sorgu_m = lambda sql, p=None: (sorgular.append(sql) or ([{"name": n} for n in tum] if "sys.databases" in sql else []))
    kaynak._ACIK.add("LOGOANALIZ_ABCDEF12")   # bu programın açık tuttuğu: dokunulmaz
    try:
        b._artiklari_temizle(str(yedek))
    finally:
        kaynak._ACIK.discard("LOGOANALIZ_ABCDEF12")
    dusenler = [s for s in sorgular if s.startswith("DROP")]
    assert dusenler == ["DROP DATABASE [LOGOANALIZ_00000001]"]
    assert sorted(os.listdir(yedek)) == ["LOGOANALIZ_ABCDEF12.bak", "musteri.bak"]


def test_yarida_kesilen_yukleme_temizlenir(tmp_path, monkeypatch):
    """Tarayıcı yüklemeyi yarıda keserse boş 'Bad Request' yerine açıklama gelir ve yarım dosya diskte kalmaz."""
    import importlib
    import io
    import app as A
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(tmp_path / "calisma"))
    importlib.reload(A)
    b = "XyZ"
    govde = (f"--{b}\r\nContent-Disposition: form-data; name=\"tur\"\r\n\r\nbak\r\n--{b}\r\n"
             f"Content-Disposition: form-data; name=\"bak_dosya\"; filename=\"TIGER.bak\"\r\n\r\n").encode() + b"\0" * 50000
    r = A.app.test_client().post("/kaynak", input_stream=io.BytesIO(govde), content_length=len(govde) * 10,
                                 content_type=f"multipart/form-data; boundary={b}")
    assert r.status_code == 400 and "yarıda kesildi" in r.get_data(as_text=True)
    assert os.listdir(A.YUKLEME) == []


def test_c_dolunca_baska_surucu_kullanilir(tmp_path, monkeypatch):
    """C: dolu, D: boşsa yedeğin kopyası ve açılan veritabanı D:\\LogoAnalizVeri altına konur."""
    from core import kaynak
    C, D = tmp_path / "C", tmp_path / "D"
    (C / "ProgramData" / "yedek").mkdir(parents=True)
    (C / "DATA").mkdir()
    (D / "Yedekler").mkdir(parents=True)
    bak = D / "Yedekler" / "TIGER.bak"
    bak.write_bytes(b"x" * 1000)
    GB = 1024 ** 3
    bos = {"C": GB + 2000, "D": 40 * GB}   # C: 1000 baytlık kopyayı alır ama kopya + veritabanını rahat almaz
    monkeypatch.setattr(kaynak, "_yerel_suruculer", lambda: [str(C), str(D)])
    monkeypatch.setattr(kaynak, "_surucu", lambda y: "D" if str(y).startswith(str(D)) else "C")
    monkeypatch.setattr(kaynak, "_bos_alan", lambda y: bos[kaynak._surucu(y)])
    b = _bak_kaynak_yalin(tmp_path)
    acilan = []
    b._klasor_ac = lambda y: acilan.append(y) or os.makedirs(y, exist_ok=True) or True

    # kopya: C:'de kopya + veritabanı için rahat yer yok -> D:
    yol = b._dosyayi_hazirla(str(bak), str(C / "ProgramData" / "yedek"), None)
    assert yol == str(D / "LogoAnalizVeri" / "yedek" / "LOGOANALIZ_ABCDEF12.bak") and os.path.exists(yol)
    assert bak.exists()                                   # firmanın yedeğine dokunulmaz (kopyalanır)

    # geri yükleme: 8 GB'lık veritabanı C:'ye sığmıyor -> D:\LogoAnalizVeri\veri
    dosyalar = [{"Type": "D", "Size": 7 * GB}, {"Type": "L", "Size": GB}]
    veri, log = b._hedef_klasorler(dosyalar, str(C / "DATA"), str(C / "DATA"))
    assert veri == log == str(D / "LogoAnalizVeri" / "veri") and acilan[-1] == veri

    # C:'de yer varsa her şey eskisi gibi C:'de
    bos["C"] = 100 * GB
    assert b._hedef_klasorler(dosyalar, str(C / "DATA"), str(C / "DATA")) == (str(C / "DATA"), str(C / "DATA"))
    assert b._dosyayi_hazirla(str(bak), str(C / "ProgramData" / "yedek"), None).startswith(str(C))

    # hiçbir yerde yer yoksa anlaşılır hata
    bos.update(C=GB, D=GB)
    with pytest.raises(kaynak.KaynakHatasi, match="yer yetmiyor"):
        b._hedef_klasorler(dosyalar, str(C / "DATA"), str(C / "DATA"))


def test_sql_yer_yok_hatasinda_baska_surucuye_gecer(tmp_path, monkeypatch):
    """Ön kontrol yeri ölçemese de (SQL Server'ın DATA klasörüne erişim yok) SQL Server 3257 'yer yok' derse
    veritabanı yeri olan diğer diske açılır ve bir kez daha denenir."""
    from core import kaynak
    GB = 1024 ** 3
    monkeypatch.setattr(kaynak, "_yerel_suruculer", lambda: ["C:\\", "D:\\"])
    monkeypatch.setattr(kaynak, "_surucu", lambda y: str(y)[:2].upper())
    bos = {"C:": 10383212544, "D:": 35 * GB}
    monkeypatch.setattr(kaynak, "_bos_alan", lambda y: bos[str(y)[:2].upper()])
    sorgular = []
    hata = Exception(3257, b"There is insufficient free space on disk volume 'C:\\' to create the database. The database "
                           b"requires 22059548672 additional free bytes, while only 10383212544 bytes are available."
                           b"DB-Lib error message 20018, severity 16:\nGeneral SQL Server error")

    def sorgu(sql, p=None):
        sorgular.append((sql, p))
        if sql.startswith("RESTORE FILELISTONLY"):
            return [{"LogicalName": "TIGER", "Type": "D", "Size": None}, {"LogicalName": "TIGER_log", "Type": "L", "Size": None}]
        if "InstanceDefaultDataPath" in sql:
            return [{"d": "C:\\Program Files\\Microsoft SQL Server\\MSSQL16.LOGOANALIZ\\MSSQL\\DATA\\", "l": "C:\\Program Files\\Microsoft SQL Server\\MSSQL16.LOGOANALIZ\\MSSQL\\DATA\\"}]
        if sql.startswith("RESTORE DATABASE") and "C:\\" in p[2]:
            raise hata
        return []
    b = _bak_kaynak_yalin(tmp_path)
    b._sorgu_m = sorgu
    acilan = []
    b._klasor_ac = lambda y: acilan.append(y) or True
    b._geri_yukle("D:\\LogoAnalizVeri\\yedek\\LOGOANALIZ_ABCDEF12.bak")
    restore = [p for s, p in sorgular if s.startswith("RESTORE DATABASE")]
    duz = lambda y: y.replace("/", "\\").replace("\\\\", "\\")  # noqa: E731  (testler Linux'ta da koşar)
    assert len(restore) == 2 and duz(restore[1][2]) == "D:\\LogoAnalizVeri\\veri\\LOGOANALIZ_ABCDEF12_0.mdf"
    assert duz(restore[1][4]) == "D:\\LogoAnalizVeri\\veri\\LOGOANALIZ_ABCDEF12_1.ldf"
    assert [duz(a) for a in acilan] == ["D:\\LogoAnalizVeri\\veri"]

    # D: de yetmiyorsa ne kadar yer gerektiği ve diskler söylenir
    bos["D:"] = 5 * GB
    sorgular.clear(); acilan.clear()
    with pytest.raises(kaynak.KaynakHatasi, match=r"20,5 GB tutuyor, C: sürücüsünde 9,7 GB.*D: 5,0 GB boş"):
        b._geri_yukle("D:\\x.bak")


def test_tasinan_yedege_sql_okuma_izni_verilir(tmp_path, monkeypatch):
    """Tarayıcıdan yüklenip taşınan yedek kullanıcı klasörünün izinlerini taşır; SQL Server okuyamaz (error 5).
    Taşımadan sonra izinler sıfırlanıp servise okuma izni verilir; yine de error 5 gelirse bir kez daha denenir."""
    from core import kaynak
    cagri = []
    monkeypatch.setattr(kaynak, "_WINDOWS", True)
    monkeypatch.setattr(kaynak, "_icacls", lambda *a: cagri.append(a) or True)
    yukleme, yedek = tmp_path / "_yukleme", tmp_path / "yedek"
    yukleme.mkdir(); yedek.mkdir()
    bak = yukleme / "1_TIGER.bak"
    bak.write_bytes(b"x" * 100)
    b = _bak_kaynak_yalin(tmp_path)
    b._sorgu_m = lambda sql, p=None: [{"service_account": "NT Service\\MSSQL$LOGOANALIZ"}] if "dm_server_services" in sql else []
    hedef = str(yedek / "LOGOANALIZ_ABCDEF12.bak")
    assert b._dosyayi_hazirla(str(bak), str(yedek), None, tasi=True).endswith("LOGOANALIZ_ABCDEF12.bak")
    assert cagri == [(hedef, "/reset"), (hedef, "/grant", "NT Service\\MSSQL$LOGOANALIZ:R")]

    # izin verilemezse taşıma geri alınır ve kopyalanır (kopya da izin alır)
    cagri.clear()
    os.replace(hedef, bak)
    sonuc = iter([True, False, False, True, True, True])   # /reset, grant hesap ✗, grant Everyone ✗ -> geri al; kopyada izinler
    monkeypatch.setattr(kaynak, "_icacls", lambda *a: cagri.append(a) or next(sonuc))
    del b._hesap
    b._dosyayi_hazirla(str(bak), str(yedek), None, tasi=True)
    assert bak.exists() and os.path.exists(hedef) and len(cagri) == 5

    # FILELISTONLY error 5 -> izin verip bir kez daha dene
    monkeypatch.setattr(kaynak, "_icacls", lambda *a: True)
    b._kopya = hedef
    hata = Exception(3201, "Cannot open backup device 'C:\\ProgramData\\LogoAnaliz\\yedek\\LOGOANALIZ_D66332AA.bak'. "
                           "Operating system error 5(Erişim engellendi.).DB-Lib error message 20018")
    deneme = []

    def sorgu(sql, p=None):
        if sql.startswith("RESTORE FILELISTONLY"):
            deneme.append(1)
            if len(deneme) == 1:
                raise hata
            return [{"LogicalName": "T", "Type": "D", "Size": 1}]
        if "InstanceDefaultDataPath" in sql:
            return [{"d": str(tmp_path), "l": str(tmp_path)}]
        return []
    b._sorgu_m = sorgu
    b._geri_yukle(hedef)
    assert len(deneme) == 2

    # izin işe yaramazsa "bozuk" değil, erişim sorunu olduğu söylenir
    deneme.clear()
    b._sorgu_m = lambda sql, p=None: (_ for _ in ()).throw(hata) if sql.startswith("RESTORE FILELISTONLY") else []
    with pytest.raises(kaynak.KaynakHatasi, match="erişim engellendi.*tam yolunu"):
        b._geri_yukle(hedef)
