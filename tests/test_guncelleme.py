"""
Otomatik güncelleme: sürüm karşılaştırma, GitHub'dan kontrol (sahte), indirme + doğrulama, klasör değiştirme,
Windows betiği ve arayüz bildirimi. Ağa çıkılmaz.
"""
import hashlib
import io
import json
import os
import sys
import tarfile
import time

import pytest

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGO_ANALIZ_KLASOR", str(tmp_path / "la"))
    monkeypatch.delenv("LOGO_ANALIZ_GUNCELLEME", raising=False)
    from core import guncelleme
    guncelleme.DURUM.update(indiriliyor=False, yuzde=0, hata=None, uyguluyor=False)
    return guncelleme


def _paket(tmp_path, surum="99.0.0"):
    """Releases'taki Linux paketi gibi: LogoTigerAnaliz/LogoTigerAnaliz (çalıştırılabilir) içeren tar.gz."""
    kaynak = tmp_path / "kaynak" / "LogoTigerAnaliz"
    kaynak.mkdir(parents=True)
    exe = kaynak / ("LogoTigerAnaliz.exe" if os.name == "nt" else "LogoTigerAnaliz")
    exe.write_text(f"surum {surum}")
    exe.chmod(0o755)
    (kaynak / "_internal").mkdir()
    (kaynak / "_internal" / "veri.txt").write_text("x")
    yol = tmp_path / "paket.tar.gz"
    with tarfile.open(yol, "w:gz") as t:
        t.add(kaynak, arcname="LogoTigerAnaliz")
    return yol.read_bytes()


class _Cevap(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _sahte_github(monkeypatch, g, paket: bytes, surum="99.0.0", sha=None, sayac=None):
    def istek(url, zaman_asimi=10):
        if sayac is not None:
            sayac.append(url)
        if url == g.API:
            return _Cevap(json.dumps({
                "tag_name": f"v{surum}", "body": "Yenilikler", "html_url": "https://github.com/x/releases/tag/v" + surum,
                "published_at": "2026-11-16T10:00:00Z",
                "assets": [{"name": g.paket_adi(), "browser_download_url": "https://indir/paket", "size": len(paket),
                            "digest": "sha256:" + (sha or hashlib.sha256(paket).hexdigest())},
                           {"name": "baska-sistem.zip", "browser_download_url": "https://indir/baska", "size": 1}]}).encode())
        if url == "https://indir/paket":
            return _Cevap(paket)
        raise AssertionError(url)
    monkeypatch.setattr(g, "_istek", istek)
    monkeypatch.setattr(g, "paket_adi", lambda platform=None: "LogoTigerAnaliz-linux-x64.tar.gz")


def test_surum_karsilastirma(g):
    assert g.yeni_mi("v0.10.0", "0.9.0") and g.yeni_mi("1.0.0", "0.99.9")
    assert not g.yeni_mi("0.9.0", "0.9.0") and not g.yeni_mi("v0.8.5", "0.9.0") and not g.yeni_mi("", "0.9.0")
    assert g.paket_adi("win32").endswith("windows-x64.zip") and g.paket_adi("darwin").endswith("apple-silicon.zip")
    assert g.paket_adi("linux").endswith("linux-x64.tar.gz")


def test_kontrol_onbellekli(g, monkeypatch, tmp_path):
    istekler = []
    _sahte_github(monkeypatch, g, b"x", sayac=istekler)
    son = g.kontrol()
    assert son["yeni"] and son["surum"] == "99.0.0" and son["paket_url"] == "https://indir/paket"
    assert g.kontrol()["surum"] == "99.0.0" and len(istekler) == 1      # 6 saat içinde tekrar sorulmaz
    g.kontrol(zorla=True)
    assert len(istekler) == 2


def test_internet_yoksa_sessiz(g, monkeypatch):
    def hata(url, zaman_asimi=10):
        raise OSError("ağ yok")
    monkeypatch.setattr(g, "_istek", hata)
    assert g.kontrol() is None and g.DURUM["hata"] is None
    g.kontrol(zorla=True)
    assert "kontrol edilemedi" in g.DURUM["hata"]


def test_indir_dogrula_ac(g, monkeypatch, tmp_path):
    paket = _paket(tmp_path)
    _sahte_github(monkeypatch, g, paket)
    ana = tmp_path / "Programlar" / "LogoTigerAnaliz"
    ana.mkdir(parents=True)
    yeni = g.indir(g.kontrol(), ana=str(ana))
    assert os.path.isfile(g.program_dosyasi(yeni)) and os.path.dirname(os.path.dirname(yeni)) == str(ana.parent)
    if os.name != "nt":
        assert os.access(g.program_dosyasi(yeni), os.X_OK)
    assert g.hazir()["surum"] == "99.0.0"
    assert not any(n.endswith((".tar.gz", ".part")) for n in os.listdir(g._calisma()))   # arşiv silindi


def test_yeniden_indirirken_eski_kayit_hazir_gorunmez(g, monkeypatch, tmp_path):
    """Aynı sürüm yeniden indirilirken (önceki kayıt duruyorken) paket tamamen açılmadan "hazır" görünmemeli."""
    paket = _paket(tmp_path)
    _sahte_github(monkeypatch, g, paket)
    ana = tmp_path / "LogoTigerAnaliz"
    ana.mkdir()
    g.indir(g.kontrol(), ana=str(ana))
    gorulen = []
    asil_ac = g._ac

    def yavas_ac(arsiv, hedef):
        gorulen.append(g.hazir())          # açma sürerken
        asil_ac(arsiv, hedef)
        gorulen.append(g.hazir())          # açıldı ama henüz adı verilmedi
    monkeypatch.setattr(g, "_ac", yavas_ac)
    g.indir(g.kontrol(), ana=str(ana))
    assert gorulen == [None, None] and g.hazir()["surum"] == "99.0.0"
    assert not any(n.endswith("_aciliyor") for n in os.listdir(tmp_path))


def test_bozuk_paket_uygulanmaz(g, monkeypatch, tmp_path):
    paket = _paket(tmp_path)
    _sahte_github(monkeypatch, g, paket, sha="0" * 64)
    ana = tmp_path / "LogoTigerAnaliz"
    ana.mkdir()
    with pytest.raises(g.GuncellemeHatasi, match="SHA-256"):
        g.indir(g.kontrol(), ana=str(ana))
    assert g.hazir() is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX klasör değiştirme")
def test_uygula_posix_klasoru_degistirir_ve_yeniden_baslar(g, monkeypatch, tmp_path):
    paket = _paket(tmp_path)
    _sahte_github(monkeypatch, g, paket)
    ana = tmp_path / "LogoTigerAnaliz"
    ana.mkdir()
    (ana / "LogoTigerAnaliz").write_text("surum eski")
    monkeypatch.setattr(g, "paketli_mi", lambda: True)
    monkeypatch.setattr(g, "program_klasoru", lambda: str(ana))
    g.indir(g.kontrol())
    assert g.acilista_uygulanacak()["surum"] == "99.0.0"
    cagri = {}
    monkeypatch.setattr(g, "_exec", lambda exe, argv, env: cagri.update(exe=exe, argv=argv, env=env))
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "/eski/yer")
    g.uygula()
    assert (ana / "LogoTigerAnaliz").read_text() == "surum 99.0.0"
    assert cagri["exe"] == str(ana / "LogoTigerAnaliz")
    assert cagri["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1" and "_PYI_APPLICATION_HOME_DIR" not in cagri["env"]
    assert cagri["env"]["LOGO_ANALIZ_TARAYICI_ACMA"] == "1"
    eskiler = [n for n in os.listdir(tmp_path) if n.startswith(".LogoTigerAnaliz_eski_")]
    assert len(eskiler) == 1 and not any(n.startswith(".LogoTigerAnaliz_yeni_") for n in os.listdir(tmp_path))
    assert g.durum_oku()["son_guncelleme"]["surum"] == "99.0.0" and "hazir" not in g.durum_oku()
    g.eski_kopyalari_temizle()
    assert not any(n.startswith(".LogoTigerAnaliz_eski_") for n in os.listdir(tmp_path))


def test_acilista_en_fazla_iki_deneme(g, monkeypatch, tmp_path):
    paket = _paket(tmp_path)
    _sahte_github(monkeypatch, g, paket)
    ana = tmp_path / "LogoTigerAnaliz"
    ana.mkdir()
    monkeypatch.setattr(g, "paketli_mi", lambda: True)
    monkeypatch.setattr(g, "program_klasoru", lambda: str(ana))
    g.indir(g.kontrol())
    d = g.durum_oku()
    d["hazir"]["deneme"] = 2
    g.durum_yaz(d)
    assert g.hazir() and g.acilista_uygulanacak() is None      # elle "Şimdi güncelle" hâlâ mümkün
    g.otomatik_ayarla(False)
    d["hazir"]["deneme"] = 0
    g.durum_yaz(dict(d, otomatik=False))
    assert g.acilista_uygulanacak() is None
    monkeypatch.setenv("LOGO_ANALIZ_GUNCELLEME", "0")
    g.otomatik_ayarla(True)
    assert not g.otomatik_mi()


def test_windows_betigi(g):
    b = g.windows_betigi(r"C:\Users\Ömer\Downloads\LogoTigerAnaliz", r"C:\Users\Ömer\Downloads\.LogoTigerAnaliz_yeni_1.0.0\LogoTigerAnaliz", 4242)
    assert 'PID eq 4242' in b and "chcp 65001" in b
    assert r'move "C:\Users\Ömer\Downloads\LogoTigerAnaliz" "C:\Users\Ömer\Downloads\.LogoTigerAnaliz_eski_' in b
    assert r'start "" "C:\Users\Ömer\Downloads\LogoTigerAnaliz\LogoTigerAnaliz.exe"' in b
    assert r'rmdir /s /q "C:\Users\Ömer\Downloads\.LogoTigerAnaliz_yeni_1.0.0"' in b
    assert b.index(":bekle") < b.index(":tasi") < b.index(":yeni") < b.index(":ac")
    assert "\r\n" in b and not b.startswith("\ufeff")


def test_arayuz_bildirimi(g, monkeypatch, tmp_path):
    import importlib
    import app as A
    importlib.reload(A)
    c = A.app.test_client()
    h = c.get("/ayarlar").get_data(as_text=True)
    assert "Program güncellemesi" in h and f"v{A.__version__}" in h and "Yeni sürüm" not in h
    g.durum_yaz({"son": {"surum": "99.0.0", "sayfa": "https://github.com/x"}, "son_kontrol": time.time()})
    h = c.get("/").get_data(as_text=True)
    assert "Yeni sürüm" in h and "v99.0.0" in h
    d = c.get("/guncelleme/durum").json
    assert d["yeni"] and d["son"] == "99.0.0" and d["surum"] == A.__version__
    c.post("/guncelleme/otomatik", data={})
    assert not g.otomatik_mi()
    c.post("/guncelleme/otomatik", data={"otomatik": "1"})
    assert g.otomatik_mi()
    # paketli program, indirilmiş güncelleme: "Şimdi güncelle" düğmesi ve güncelleniyor sayfası
    monkeypatch.setattr(g, "paketli_mi", lambda: True)
    monkeypatch.setattr(g, "hazir", lambda: {"surum": "99.0.0", "klasor": "x", "ana": "y"})
    assert "Şimdi güncelle" in c.get("/").get_data(as_text=True)
    cagrildi = []
    monkeypatch.setattr(g, "uygula", lambda h=None, cikis=None: cagrildi.append(h))
    r = c.post("/guncelleme/uygula")
    assert "Güncelleniyor" in r.get_data(as_text=True)
    time.sleep(1.5)
    assert cagrildi and cagrildi[0]["surum"] == "99.0.0"
