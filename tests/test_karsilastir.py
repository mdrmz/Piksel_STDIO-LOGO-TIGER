"""
Karşılaştırmalar: dönem (yıl), cari, firma. Rakamlar panelle aynı kaynaktan; toplamlar tutarlı olmalı.
Çalıştırma:  python demo/demo_db_olustur.py demo/logo_demo.sqlite && pytest -q tests
"""
import io
import os
import re
import sys
import time

import pytest

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)
DEMO = os.path.join(KOK, "demo", "logo_demo.sqlite")

from core import karsilastir as K  # noqa: E402
from core.analiz import Analiz  # noqa: E402
from core.kaynak import SqliteKaynak  # noqa: E402
from core.logo import aktar, firmalari_tara  # noqa: E402


@pytest.fixture(scope="module")
def depolar(tmp_path_factory):
    if not os.path.exists(DEMO):
        pytest.skip("Önce demo veritabanını üretin: python demo/demo_db_olustur.py demo/logo_demo.sqlite")
    k = SqliteKaynak(DEMO)
    out = {}
    for f in firmalari_tara(k):
        yol = str(tmp_path_factory.mktemp(f"k{f.no}") / "analiz.sqlite")
        aktar(k, f, f.donemler, yol)
        out[f.no] = yol
    k.kapat()
    return out


def test_donem_paneldeki_rakamlarla_ayni(depolar):
    a = Analiz(depolar["001"])
    oner = K.oneriler(a)
    assert oner and oner[0]["kod"].startswith("ytd")
    o = oner[0]
    k = K.donem(a, o["A"], o["B"])
    for d, oz in ((o["A"], k["oa"]), (o["B"], k["ob"])):
        ref = Analiz(depolar["001"], bas=d[0], bit=d[1]).ozet()
        for anahtar in ("net_satis", "brut_kar", "fatura_sayisi", "musteri_sayisi", "net_alis"):
            assert oz[anahtar] == pytest.approx(ref[anahtar], abs=0.01), anahtar
    toplam_fark = k["ob"]["net_satis"] - k["oa"]["net_satis"]
    for sev in K.SEVIYELER:
        t = k["kirilim"][sev]
        assert t["fark"].sum() == pytest.approx(toplam_fark, abs=0.05), sev
        assert t["a"].sum() == pytest.approx(k["oa"]["net_satis"], abs=0.05)
    for sev in ("urun", "cari"):
        p = k["kopru"][sev]
        assert sum(p[d]["tutar"] for d in K.DURUMLAR) == pytest.approx(toplam_fark, abs=0.05)
    u = k["kirilim"]["urun"]
    iki = u["miktar_etkisi"].notna()
    assert iki.any()
    assert (u.loc[iki, "miktar_etkisi"] + u.loc[iki, "fiyat_etkisi"] - u.loc[iki, "fark"]).abs().max() < 0.01
    assert len(k["aylik"]) == 10 and list(k["aylik"]["etiket"])[:2] == ["Oca", "Şub"]
    assert k["aylik"]["b"].sum() == pytest.approx(k["ob"]["net_satis"], abs=0.05)


def test_durumlar_yeni_ve_kaybedilen(depolar):
    """Bir dönemde hiç satılmayan kayıt 'Yeni' / 'Kaybedildi' olur ve tutarı farkın tamamıdır."""
    a = Analiz(depolar["001"])
    k = K.donem(a, ("2025-01-01", "2025-01-31"), ("2026-10-01", "2026-10-31"))
    t = k["kirilim"]["cari"]
    yeni, kayip = t[t["durum"] == "Yeni"], t[t["durum"] == "Kaybedildi"]
    assert len(yeni) + len(kayip) > 0
    assert (yeni["a"].abs() < K.SIFIR).all() and (yeni["fark"] == yeni["b"]).all()
    assert (kayip["b"].abs() < K.SIFIR).all() and (kayip["fark"] == -kayip["a"]).all()
    assert not t["ad"].isna().any() and not t["ad"].astype(str).str.contains("nan").any()


def test_cari_karsilastirma(depolar):
    a = Analiz(depolar["001"], bas="2026-01-01", bit="2026-10-31")
    m = a.musteri_abc()
    kodlar = list(m["cari_kod"].head(3))
    k = K.cari(a, kodlar + ["YOK-BOYLE-BIR-KOD"])
    assert [x["kod"] for x in k["secili"]] == kodlar
    for x in k["ozet"]:
        assert x["net_satis"] == pytest.approx(float(m.loc[m["cari_kod"] == x["kod"], "net_tutar"].iloc[0]), abs=0.01)
    for x in k["secili"]:
        assert k["aylik"][x["kod"]].sum() == pytest.approx(next(o["net_satis"] for o in k["ozet"] if o["kod"] == x["kod"]), abs=0.05)
    assert k["urunler"] and all(u["fiyat_farki"] is None or u["fiyat_farki"] >= 0 for u in k["urunler"])


def test_firma_karsilastirma(depolar):
    from core.analiz import Analiz as An
    bilgi = [{"firma": n, "firma_ad": n, "tarih_bas": An(y).meta["tarih_bas"], "tarih_bit": An(y).meta["tarih_bit"]}
             for n, y in sorted(depolar.items())]
    bas, bit = K.ortak_aralik(bilgi)
    assert bas and bit and bas <= bit
    an = [(n, b, An(y, bas=bas, bit=bit)) for (n, y), b in zip(sorted(depolar.items()), bilgi)]
    k = K.firma(an)
    net = next(r for r in k["ozet"] if r["ad"] == "Net satış")["degerler"]
    for (n, y), v in zip(sorted(depolar.items()), net):
        assert v == pytest.approx(An(y, bas=bas, bit=bit).ozet()["net_satis"], abs=0.01)
    for f in k["firmalar"]:
        assert k["aylik"][f["aid"]].sum() == pytest.approx(f["o"]["net_satis"], abs=0.05)
    assert k["ortak_urun"] and all(r["firma_say"] >= 2 for r in k["ortak_urun"] + k["ortak_cari"])
    assert K._normal_ad("ABC İnşaat Tic. Ltd. Şti.") == K._normal_ad("ABC INSAAT TICARET LIMITED SIRKETI")


def test_ekranlar_ve_excel(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(tmp_path))
    import importlib
    import openpyxl
    import app as A
    importlib.reload(A)
    c = A.app.test_client()

    def bekle():
        for _ in range(900):
            d = c.get("/durum").json
            if not d["calisiyor"]:
                return d
            time.sleep(0.1)
    c.post("/kaynak", data={"tur": "demo"}); bekle()
    aids = []
    for f in ("001", "002"):
        donem = re.findall(r'name="donem_%s" value="(\d+)"' % f, c.get("/firmalar").get_data(as_text=True))
        c.post("/aktar", data={"firma": f, f"donem_{f}": donem, "sadece_faturali": "1", "baska_firma": "1"})
        aids.append(bekle()["sonraki"].rsplit("/", 1)[-1])
    a1 = aids[0]
    assert f"/karsilastir/{a1}" in c.get(f"/panel/{a1}").get_data(as_text=True)
    assert "Firmaları karşılaştır" in c.get("/").get_data(as_text=True)
    h = c.get(f"/karsilastir/{a1}").get_data(as_text=True)
    assert "Farkı kim oluşturdu?" in h and "2026 Oca–Eki" in h and "Miktar etkisi" in h
    h = c.get(f"/karsilastir/{a1}?sev=cari&durum=Azaldı&n=tumu").get_data(as_text=True)
    assert "Azaldı" in h and "Arttı</span>" not in h
    h = c.get(f"/karsilastir/{a1}?a_bas=2025-03-01&a_bit=2025-03-31&b_bas=2026-03-01&b_bit=2026-03-31").get_data(as_text=True)
    assert "Mar 2025" in h and "Mar 2026" in h
    kod = A.Analiz(os.path.join(str(tmp_path), a1, "analiz.sqlite")).musteri_abc()["cari_kod"].head(2).tolist()
    q = "&".join(f"c={x}" for x in kod)
    assert "Başka carilerle karşılaştır" in c.get(f"/cari/{a1}?kod={kod[0]}").get_data(as_text=True)
    h = c.get(f"/karsilastir/{a1}/cari?{q}").get_data(as_text=True)
    assert "Ürünler ve fiyatlar" in h and "Fiyat farkı" in h
    h = c.get("/karsilastir-firma?" + "&".join(f"a={x}" for x in aids)).get_data(as_text=True)
    assert "Ortak müşteriler" in h and "Ortak ürünler" in h and "ortak dönemi" in h
    assert c.get("/karsilastir-firma?a=../../etc").status_code == 404
    for u, sayfalar in ((f"/karsilastir/{a1}/excel", {"Özet", "Aylık", "Ürün", "Cari", "Grup", "Ambar", "Temsilci"}),
                        (f"/karsilastir/{a1}/cari/excel?{q}", {"Özet", "Aylık", "Ürünler"}),
                        ("/karsilastir-firma/excel?" + "&".join(f"a={x}" for x in aids), {"Özet", "Aylık", "Ortak müşteriler", "Ortak ürünler"})):
        r = c.get(u)
        assert r.status_code == 200, u
        wb = openpyxl.load_workbook(io.BytesIO(r.data), read_only=True)
        assert sayfalar <= set(wb.sheetnames), (u, wb.sheetnames)
        for ws in wb.worksheets:
            for satir in ws.iter_rows(values_only=True):
                assert not any(isinstance(v, str) and v.strip().lower() in ("nan", "none", "nat") for v in satir), (u, ws.title)
