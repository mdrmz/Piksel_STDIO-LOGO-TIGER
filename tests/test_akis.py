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
