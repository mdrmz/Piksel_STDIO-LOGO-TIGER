"""
Paketlenmiş programın kendini gerçekten güncellediğini sınar (Windows: .bat ile klasör değiştirme, Linux: exec).
tests/paket_testi.py'den sonra, dist/LogoTigerAnaliz derlenmişken çalışır. İnternete çıkmaz: GitHub API'si ve
paket bu betiğin açtığı yerel bir HTTP sunucusundan verilir (LOGO_ANALIZ_GUNCELLEME_API).

  1. dist/LogoTigerAnaliz "kurulu eski sürüm" olarak geçici bir klasöre kopyalanır.
  2. Aynı programın, içine YENI_SURUM.txt eklenmiş kopyası Releases'taki gibi paketlenir (v99.0.0).
  3. Kurulu program başlatılır; kendisi yeni sürümü görür, indirir, doğrular, hazırlar.
  4. "Şimdi güncelle"ye basılır; program kapanıp klasörünü değiştirir ve yeniden açılır.
"""
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.environ.get("LOGO_ANALIZ_DIST") or os.path.join(KOK, "dist", "LogoTigerAnaliz")
WINDOWS = os.name == "nt"
EXE_AD = "LogoTigerAnaliz.exe" if WINDOWS else "LogoTigerAnaliz"
PORT, SUNUCU_PORT = 5057, 5099
URL = f"http://127.0.0.1:{PORT}"


def istek(yol, veri=None):
    govde = urllib.parse.urlencode(veri).encode() if veri is not None else None
    with urllib.request.urlopen(urllib.request.Request(URL + yol, data=govde), timeout=30) as r:
        return r.read()


def main():
    assert os.path.isdir(DIST), f"paket yok: {DIST}"
    kok = tempfile.mkdtemp(prefix="guncelleme_")
    kurulu = os.path.join(kok, "Programlar", "LogoTigerAnaliz")
    shutil.copytree(DIST, kurulu, symlinks=True)

    # yeni sürüm paketi (Releases'taki adla ve yapıyla)
    hazirlik = os.path.join(kok, "hazirlik", "LogoTigerAnaliz")
    shutil.copytree(DIST, hazirlik, symlinks=True)
    with open(os.path.join(hazirlik, "YENI_SURUM.txt"), "w") as fh:
        fh.write("99.0.0")
    sunulan = os.path.join(kok, "sunulan")
    os.makedirs(sunulan)
    if WINDOWS:
        ad = "LogoTigerAnaliz-windows-x64.zip"
        with zipfile.ZipFile(os.path.join(sunulan, ad), "w", zipfile.ZIP_DEFLATED) as z:
            for d, _, dosyalar in os.walk(hazirlik):
                for f in dosyalar:
                    tam = os.path.join(d, f)
                    z.write(tam, os.path.relpath(tam, os.path.dirname(hazirlik)))
    else:
        ad = "LogoTigerAnaliz-linux-x64.tar.gz"
        with tarfile.open(os.path.join(sunulan, ad), "w:gz") as t:
            t.add(hazirlik, arcname="LogoTigerAnaliz")
    paket = open(os.path.join(sunulan, ad), "rb").read()
    with open(os.path.join(sunulan, "latest"), "w") as fh:
        json.dump({"tag_name": "v99.0.0", "body": "test", "html_url": "http://127.0.0.1/",
                   "assets": [{"name": ad, "size": len(paket), "digest": "sha256:" + hashlib.sha256(paket).hexdigest(),
                               "browser_download_url": f"http://127.0.0.1:{SUNUCU_PORT}/{ad}"}]}, fh)
    os.chdir(sunulan)
    sunucu = http.server.ThreadingHTTPServer(("127.0.0.1", SUNUCU_PORT), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=sunucu.serve_forever, daemon=True).start()

    env = dict(os.environ, LOGO_ANALIZ_PORT=str(PORT), LOGO_ANALIZ_TARAYICI_ACMA="1",
               LOGO_ANALIZ_KLASOR=os.path.join(kok, "LogoAnaliz"),
               LOGO_ANALIZ_GUNCELLEME_API=f"http://127.0.0.1:{SUNUCU_PORT}/latest")
    env.pop("LOGO_ANALIZ_GUNCELLEME", None)
    log = open(os.path.join(kok, "app.log"), "w", encoding="utf-8")
    p = subprocess.Popen([os.path.join(kurulu, EXE_AD)], stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        for _ in range(90):
            try:
                d = json.loads(istek("/guncelleme/durum"))
                if d.get("hazir") == "99.0.0":
                    break
                if d.get("hata"):
                    raise RuntimeError(d["hata"])
            except OSError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError(f"güncelleme indirilip hazırlanmadı: {d}")
        print("=== yeni sürüm indirildi ve hazırlandı", flush=True)
        assert b"99.0.0" in istek("/"), "panelde güncelleme bildirimi yok"
        assert "Güncelleniyor".encode() in istek("/guncelleme/uygula", {})
        print("=== 'Şimdi güncelle' basıldı", flush=True)
        isaret = os.path.join(kurulu, "YENI_SURUM.txt")
        for _ in range(120):
            try:
                if os.path.exists(isaret) and istek("/guncelleme/durum"):
                    break
            except OSError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("program yeni sürümle yeniden açılmadı")
        d = json.loads(istek("/guncelleme/durum"))
        assert d["son_guncelleme"]["surum"] == "99.0.0", d
        assert os.path.isfile(os.path.join(kurulu, EXE_AD))
        artik = [n for n in os.listdir(os.path.dirname(kurulu)) if n.startswith(".LogoTigerAnaliz_yeni_")]
        assert not artik, artik
        print("=== 1) 'Şimdi güncelle': klasör değişti, program yeniden açıldı", flush=True)

        # 2) kapat-aç: indirilen güncelleme açılışta kendiliğinden uygulanır
        os.remove(isaret)   # kurulu klasör "eski sürüm" gibi olsun; yeni sürüm yine 99.0.0 olarak görünür
        for _ in range(90):
            if json.loads(istek("/guncelleme/durum")).get("hazir") == "99.0.0":
                break
            time.sleep(1)
        else:
            raise RuntimeError("ikinci güncelleme hazırlanmadı")
        _kapat(p)
        time.sleep(2)
        p = subprocess.Popen([os.path.join(kurulu, EXE_AD)], stdout=log, stderr=subprocess.STDOUT, env=env)
        for _ in range(120):
            try:
                if os.path.exists(isaret) and istek("/guncelleme/durum"):
                    break
            except OSError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("açılışta güncelleme uygulanmadı")
        print("=== 2) kapat-aç: açılışta kendiliğinden güncellendi", flush=True)
        print("GÜNCELLEME TESTİ BAŞARILI", flush=True)
    except Exception:
        log.flush()
        print(open(os.path.join(kok, "app.log"), encoding="utf-8", errors="replace").read()[-4000:])
        raise
    finally:
        _kapat(p)
        sunucu.shutdown()


def _kapat(p):
    p.terminate()
    if WINDOWS:   # yeniden açılan program .bat'ın başlattığı ayrı bir süreç
        subprocess.run(["taskkill", "/F", "/IM", EXE_AD], capture_output=True)
    try:
        p.wait(timeout=20)
    except subprocess.TimeoutExpired:
        p.kill()


if __name__ == "__main__":
    sys.exit(main())
