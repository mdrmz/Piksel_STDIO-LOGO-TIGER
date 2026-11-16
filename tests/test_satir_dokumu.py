"""
Satır satır fatura dökümü: paneldeki rakamların hangi Logo satırlarından geldiği.
Çalıştırma:  python demo/demo_db_olustur.py demo/logo_demo.sqlite && pytest -q tests
"""
import os
import re
import sqlite3
import sys
import time

import pytest

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)
DEMO = os.path.join(KOK, "demo", "logo_demo.sqlite")

from core.analiz import Analiz  # noqa: E402
from core.kaynak import SqliteKaynak  # noqa: E402
from core.logo import aktar, firmalari_tara  # noqa: E402


@pytest.fixture(scope="module")
def depo(tmp_path_factory):
    if not os.path.exists(DEMO):
        pytest.skip("Önce demo veritabanını üretin: python demo/demo_db_olustur.py demo/logo_demo.sqlite")
    yol = str(tmp_path_factory.mktemp("sd") / "analiz.sqlite")
    k = SqliteKaynak(DEMO)
    f = next(f for f in firmalari_tara(k) if f.no == "001")
    aktar(k, f, f.donemler, yol)
    k.kapat()
    return yol


def _dokum(depo, **kw):
    from werkzeug.datastructures import MultiDict
    from core import fatura
    v = fatura.veri(depo)
    fl = fatura.Filtre.istekten(MultiDict([(k, x) for k, val in kw.items() for x in (val if isinstance(val, list) else [val])]))
    f, us = fatura.suz(v, fl)
    return fatura.satir_dokumu(v, f, us, fl)


def test_satir_dokumu_paneli_tutar(depo):
    """Satırların 'hesaba giren' toplamı paneldeki net satış ve net alışın kendisidir."""
    from core import fatura
    s = _dokum(depo)
    h = fatura.satir_hesap(s)
    o = Analiz(depo).ozet()
    assert h["net_satis"] == pytest.approx(o["net_satis"], abs=0.01)
    assert h["net_alis"] == pytest.approx(o["net_alis"], abs=0.01)
    assert s["etki"].sum() == pytest.approx(o["net_satis"] + o["net_alis"], abs=0.01)
    # hesaba girmeyen satırların etkisi sıfır, nedenleri yazılı
    d = s[s["durum"] != "Analizde"]
    assert len(d) and (d["etki"] == 0).all() and (d["durum"].str.len() > 0).all()
    assert sum(x["satir"] for x in h["disarida"]) == len(d)


def test_satir_dokumu_ham_logo_ile_ayni(depo):
    """Döküm, faturaya bağlı tüm STLINE satırlarıdır; ham alanlar Logo tablosundakiyle aynıdır."""
    s = _dokum(depo)
    ham = sqlite3.connect(DEMO)
    n = sum(ham.execute(f"SELECT COUNT(*) FROM LG_001_{p}_STLINE WHERE INVOICEREF > 0").fetchone()[0] for p in ("01", "02"))
    assert len(s) == n
    for r in s.sample(25, random_state=1).itertuples():
        t = ham.execute(f"SELECT TOTAL, DISTDISC, VATMATRAH, AMOUNT, LINETYPE FROM {r.satir_tablo} WHERE LOGICALREF=?",
                        (int(r.satir_ref),)).fetchone()
        assert (r.logo_total, r.logo_distdisc, r.logo_vatmatrah, r.logo_miktar, r.linetype) == pytest.approx(t)
        assert r.tutar_kaynagi == "VATMATRAH" and r.net_tutar == pytest.approx(t[2])
        fno = ham.execute(f"SELECT FICHENO FROM {r.fatura_tablo} WHERE LOGICALREF=?", (int(r.fatura_ref),)).fetchone()[0]
        assert fno == r.fatura_no


def test_satir_dokumu_filtreleri(depo):
    from core import fatura
    s = _dokum(depo, tur=["satis", "satis_iade"])
    assert set(s["trcode"]) <= {2, 3, 7, 8} or set(s["fatura_tur"]) <= {"Perakende satış", "Toptan satış", "Perakende satış iade", "Toptan satış iade"}
    assert fatura.satir_hesap(s)["net_alis"] == 0
    kod = s.loc[s["durum"] == "Analizde", "malzeme_kod"].dropna().iloc[0]
    u = _dokum(depo, urun_kod=kod, tur=["satis", "satis_iade"])
    assert set(u["malzeme_kod"]) == {kod}
    a = Analiz(depo).urun_abc().set_index("malzeme_kod")
    assert fatura.satir_hesap(u)["net_satis"] == pytest.approx(a.loc[kod, "net_tutar"], abs=0.01)
    g = fatura.satir_sirala(s, "disarida", "net_tutar", "azalan")
    assert (g["durum"] != "Analizde").all() and g["net_tutar"].is_monotonic_decreasing


def test_satir_dokumu_eski_analizde_calisir(depo, tmp_path):
    """Ham alanları olmayan (v0.10.0 öncesi) analiz: döküm açılır, hesap aynı, ham alanlar boş."""
    import shutil
    from core import fatura
    eski = str(tmp_path / "analiz.sqlite")
    shutil.copy(depo, eski)
    con = sqlite3.connect(eski)
    for k in fatura.HAM_KOLON:
        con.execute(f"ALTER TABLE fatura_satir DROP COLUMN {k}")
    con.commit(), con.close()
    s = _dokum(eski)
    h = fatura.satir_hesap(s)
    assert not h["ham_var"] and s["logo_total"].isna().all()
    assert h["net_satis"] == pytest.approx(Analiz(depo).ozet()["net_satis"], abs=0.01)


def test_satir_ekrani_ve_excel(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(tmp_path))
    import importlib
    import openpyxl
    import app as A
    importlib.reload(A)
    c = A.app.test_client()

    def bekle():
        for _ in range(600):
            d = c.get("/durum").json
            if not d["calisiyor"]:
                return d
            time.sleep(0.1)
    c.post("/kaynak", data={"tur": "demo"}); bekle()
    c.post("/aktar", data={"firma": "001", "donem_001": ["01", "02"], "sadece_faturali": "1"})
    aid = bekle()["sonraki"].rsplit("/", 1)[-1]
    o = A.Analiz(os.path.join(str(tmp_path), aid, "analiz.sqlite")).ozet()
    p = c.get(f"/panel/{aid}").get_data(as_text=True)
    assert "Bu rakamlar nereden geliyor?" in p
    bag = re.search(r'href="(/satirlar/%s\?tur=satis&(?:amp;)?tur=satis_iade)"' % aid, p).group(1).replace("&amp;", "&")
    h = c.get(bag).get_data(as_text=True)
    assert "Net satış" in h and "VATMATRAH" in h and "LG_001_01_STLINE" in h and "Sonraki" in h
    assert A.tl(o["net_satis"], 2) in h
    h = c.get(f"/satirlar/{aid}?sdurum=disarida&ssirala=net_tutar&syon=azalan").get_data(as_text=True)
    assert "Hesaba girmeyen" in h
    assert "Satır satır döküm" in c.get(f"/faturalar/{aid}").get_data(as_text=True)
    x = c.get(f"/satirlar/{aid}/excel")
    yol = tmp_path / "s.xlsx"
    yol.write_bytes(x.data)
    wb = openpyxl.load_workbook(yol, read_only=True)
    assert wb.sheetnames == ["Hesap", "Satırlar"]
    satir = list(wb["Satırlar"].iter_rows(min_row=2, values_only=True))
    bas = list(satir[0])
    i = bas.index("Hesaba giren tutar (TL)")
    assert sum(r[i] or 0 for r in satir[1:]) == pytest.approx(o["net_satis"] + o["net_alis"], abs=0.05)
    assert "Dağıtılan indirim (DISTDISC)" in bas and "Satır LOGICALREF" in bas
