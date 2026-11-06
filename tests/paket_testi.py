"""
Paketlenmiş uygulama (PyInstaller çıktısı) ile uçtan uca test.
tests/entegrasyon.py'den SONRA çalışır: SQL Server hazır, test yedeği <temp>/Masaustu/TIGER_TEST.bak'ta duruyor.

Kullanıcının yapacağı gibi: programı (exe) başlatır, HTTP üzerinden yedeği "programın SQL Server'ı" ile açar,
firma 001'i aktarır, paneli ve Excel raporunu indirir, tanılama paketini alır, programı kapatır.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 5056
URL = f"http://127.0.0.1:{PORT}"
EXE = os.path.join(KOK, "dist", "LogoTigerAnaliz", "LogoTigerAnaliz.exe" if os.name == "nt" else "LogoTigerAnaliz")
BAK = os.path.join(tempfile.gettempdir(), "Masaustu", "TIGER_TEST.bak")


def istek(yol, veri=None):
    govde = urllib.parse.urlencode(veri, doseq=True).encode() if veri is not None else None
    with urllib.request.urlopen(urllib.request.Request(URL + yol, data=govde), timeout=120) as r:
        return r.status, r.read(), r.geturl()


def bekle():
    for _ in range(1200):
        d = json.loads(istek("/durum")[1])
        if not d["calisiyor"]:
            print("\n".join("  " + x for x in d["log"]), flush=True)
            return d
        time.sleep(0.5)
    raise TimeoutError


def main():
    assert os.path.exists(EXE), f"paket yok: {EXE}"
    assert os.path.exists(BAK), f"test yedeği yok: {BAK} (önce tests/entegrasyon.py çalışmalı)"
    env = dict(os.environ, LOGO_ANALIZ_PORT=str(PORT), LOGO_ANALIZ_TARAYICI_ACMA="1")
    log = open(os.path.join(tempfile.gettempdir(), "paket_app.log"), "w", encoding="utf-8")
    p = subprocess.Popen([EXE], stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        for _ in range(120):
            try:
                istek("/")
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError("paketlenmiş uygulama açılmadı")
        print("=== paketlenmiş uygulama açıldı", flush=True)

        istek("/kaynak", {"tur": "bak", "bak_yol": BAK, "sql_kaynak": "otomatik"})
        d = bekle()
        assert d["hata"] is None, d["hata"]
        _, sayfa, _ = istek("/firmalar")
        assert b"001" in sayfa

        istek("/aktar", {"firma": "001", "donem_001": ["01", "02"], "sadece_faturali": "1"})
        d = bekle()
        assert d["hata"] is None, d["hata"]
        aid = d["sonraki"].rsplit("/", 1)[-1]
        _, panel, _ = istek(d["sonraki"])
        assert "MÜŞTERİ 150 TİC. LTD. ŞTİ.".encode("utf-8") in panel, "panelde Türkçe müşteri adı yok"
        assert "45.995.309".encode() in panel, "net satış panelde beklenen değerde değil"

        for tur in ("excel", "powerbi", "yzpaket"):
            durum, veri, _ = istek(f"/indir/{aid}/{tur}")
            assert durum == 200 and veri[:2] == b"PK", tur
            print(f"  {tur}: {len(veri) / 1024:.0f} KB", flush=True)
        durum, veri, _ = istek("/tanilama")
        assert durum == 200 and veri[:2] == b"PK"
        print("PAKETLENMİŞ UYGULAMA TESTİ BAŞARILI", flush=True)
    finally:
        p.terminate()
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
        log.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        with open(os.path.join(tempfile.gettempdir(), "paket_app.log"), encoding="utf-8", errors="replace") as fh:
            print("---- uygulama çıktısı ----\n" + fh.read()[-6000:])
        raise
