"""
Otomatik güncelleme: GitHub'daki en son sürümü (Releases) kontrol eder, paketi arka planda indirir,
program bir sonraki açılışında (ya da "Şimdi güncelle" ile hemen) yeni sürüme geçer.

Akış (paketlenmiş program, Releases'tan indirilen):
  1. Açılışta en fazla 6 saatte bir GitHub API'den en son sürüm sorulur (internet yoksa sessizce geçilir).
  2. Daha yeni sürüm varsa ve otomatik güncelleme açıksa bu sisteme ait paket arka planda indirilir,
     GitHub'ın verdiği SHA-256 ile doğrulanır ve programın yanındaki gizli bir klasöre açılır.
  3. Program kapatılıp açıldığında (ya da paneldeki "Şimdi güncelle" ile) eski klasör yenisiyle değiştirilir
     ve yeni sürüm başlatılır. Değiştirme yarıda kalırsa eski sürüm geri konur.
     macOS/Linux: çalışan dosyalar yeniden adlandırılabildiği için aynı pencerede yeni sürüme geçilir (exec).
     Windows: çalışan .exe değiştirilemez; küçük bir .bat program kapanınca klasörü değiştirip yeni sürümü açar.
Analizler, ayarlar ve SQL Server kurulumu programın klasöründe değil ~/LogoAnaliz'de durur; güncellemeden etkilenmez.

Kaynak koddan çalışırken (git clone) program kendini değiştirmez; "git pull" ile güncelleme önerilir/yapılır.
Kapatmak için: Ayarlar sayfasından "Otomatik güncelle" kutusu ya da LOGO_ANALIZ_GUNCELLEME=0 ortam değişkeni.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile

from . import __version__

REPO = "mdrmz/Piksel_STDIO-LOGO-TIGER"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
SAYFA = f"https://github.com/{REPO}/releases/latest"
KONTROL_ARALIGI = 6 * 3600          # saniye: GitHub'a en fazla bu sıklıkla sorulur
PROGRAM_AD = "LogoTigerAnaliz"
WINDOWS = os.name == "nt"
MAC = sys.platform == "darwin"

_kilit = threading.Lock()
DURUM: dict = {"indiriliyor": False, "yuzde": 0, "hata": None, "uyguluyor": False}


class GuncellemeHatasi(Exception):
    pass


# ---------------------------------------------------------------------------- yardımcılar
def surum_tuple(s: str) -> tuple:
    """'v0.10.2' -> (0, 10, 2). Sayı olmayan ekler (-beta) yok sayılır."""
    return tuple(int(x) for x in re.findall(r"\d+", (s or "").split("-")[0])[:3]) or (0,)


def yeni_mi(uzak: str, yerel: str = __version__) -> bool:
    return surum_tuple(uzak) > surum_tuple(yerel)


def paket_adi(platform: str = sys.platform) -> str | None:
    if platform.startswith("win"):
        return f"{PROGRAM_AD}-windows-x64.zip"
    if platform == "darwin":
        return f"{PROGRAM_AD}-macos-apple-silicon.zip"
    if platform.startswith("linux"):
        return f"{PROGRAM_AD}-linux-x64.tar.gz"
    return None


def paketli_mi() -> bool:
    """Releases'tan indirilen tek klasörlük program mı (PyInstaller), yoksa kaynak koddan mı çalışıyor."""
    return bool(getattr(sys, "frozen", False))


def program_klasoru() -> str:
    if paketli_mi():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def program_dosyasi(klasor: str) -> str:
    return os.path.join(klasor, PROGRAM_AD + (".exe" if WINDOWS else ""))


def _calisma() -> str:
    from .kurulum import calisma_klasoru
    k = os.path.join(calisma_klasoru(), "_guncelleme")
    os.makedirs(k, exist_ok=True)
    return k


def _durum_yolu() -> str:
    return os.path.join(_calisma(), "durum.json")


def durum_oku() -> dict:
    try:
        with open(_durum_yolu(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def durum_yaz(d: dict):
    with open(_durum_yolu(), "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)


def otomatik_mi() -> bool:
    if os.environ.get("LOGO_ANALIZ_GUNCELLEME", "").strip() in ("0", "hayir", "kapali", "false"):
        return False
    return durum_oku().get("otomatik", True)


def otomatik_ayarla(acik: bool):
    d = durum_oku()
    d["otomatik"] = bool(acik)
    durum_yaz(d)


def _istek(url: str, zaman_asimi=10):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"{PROGRAM_AD}/{__version__}"})
    return urllib.request.urlopen(req, timeout=zaman_asimi)


# ---------------------------------------------------------------------------- 1. kontrol
def kontrol(zorla: bool = False) -> dict | None:
    """En son sürümü sorar (önbellekli). Döner: {surum, yeni, paket_url, boyut, sha256, notlar, tarih} ya da None."""
    d = durum_oku()
    son = d.get("son") or {}
    if not zorla and son and time.time() - d.get("son_kontrol", 0) < KONTROL_ARALIGI:
        son["yeni"] = yeni_mi(son.get("surum", ""))
        return son
    try:
        with _istek(os.environ.get("LOGO_ANALIZ_GUNCELLEME_API") or API) as r:   # ortam değişkeni: yalnızca testler için
            j = json.load(r)
    except Exception as e:   # internet yok, GitHub erişilemez, kurumsal ağ: sessizce geç
        DURUM["hata"] = f"Güncelleme kontrol edilemedi: {e}" if zorla else None
        return son or None
    ad = paket_adi()
    varlik = next((a for a in j.get("assets", []) if a.get("name") == ad), None)
    dig = (varlik or {}).get("digest") or ""
    son = {"surum": str(j.get("tag_name", "")).lstrip("v"), "notlar": (j.get("body") or "")[:4000],
           "tarih": j.get("published_at"), "sayfa": j.get("html_url") or SAYFA,
           "paket_url": (varlik or {}).get("browser_download_url"), "boyut": (varlik or {}).get("size"),
           "sha256": dig.split(":", 1)[1] if dig.startswith("sha256:") else None}
    d.update(son=son, son_kontrol=time.time())
    durum_yaz(d)
    DURUM["hata"] = None
    son["yeni"] = yeni_mi(son["surum"])
    return son


# ---------------------------------------------------------------------------- 2. indir ve hazırla
def _yeni_klasor_yolu(surum: str, ana: str | None = None) -> str:
    """Yeni sürüm, programın klasörünün yanına (aynı disk) açılır; klasör değiştirme böylece anlık olur."""
    ana = ana or program_klasoru()
    return os.path.join(os.path.dirname(ana), f".{PROGRAM_AD}_yeni_{surum}")


def yazilabilir_mi(ana: str | None = None) -> bool:
    ust = os.path.dirname(ana or program_klasoru())
    return os.access(ust, os.W_OK) and os.access(ana or program_klasoru(), os.W_OK)


def _ac(arsiv: str, hedef: str):
    if arsiv.endswith(".tar.gz"):
        with tarfile.open(arsiv, "r:gz") as t:
            try:
                t.extractall(hedef, filter="data")   # mutlak yol / klasör dışına çıkan girdileri reddeder
            except TypeError:                        # Python < 3.12
                t.extractall(hedef)
    elif MAC and shutil.which("ditto"):
        subprocess.run(["ditto", "-x", "-k", arsiv, hedef], check=True, capture_output=True)
    else:
        with zipfile.ZipFile(arsiv) as z:
            kok = os.path.realpath(hedef)
            for bilgi in z.infolist():
                yol = os.path.realpath(os.path.join(hedef, bilgi.filename))
                if not (yol == kok or yol.startswith(kok + os.sep)):
                    raise GuncellemeHatasi(f"Paket beklenmeyen bir yol içeriyor: {bilgi.filename}")
                z.extract(bilgi, hedef)
                mod = bilgi.external_attr >> 16
                if mod and not WINDOWS and not bilgi.is_dir():
                    os.chmod(yol, mod & 0o777)


def indir(son: dict, ana: str | None = None, ilerleme=None) -> str:
    """Paketi indirir, doğrular, açar; hazır yeni klasörün yolunu döndürür ve durum dosyasına yazar."""
    if not son.get("paket_url"):
        raise GuncellemeHatasi("Bu sistem için güncelleme paketi bulunamadı.")
    ana = ana or program_klasoru()
    d = durum_oku()
    if d.pop("hazir", None):   # önceki (uygulanmış ya da yarım) kayıt, yeni paket tamamen açılmadan "hazır" görünmesin
        durum_yaz(d)
    is_ = _calisma()
    arsiv = os.path.join(is_, paket_adi() or "paket")
    h = hashlib.sha256()
    alinan, toplam = 0, son.get("boyut") or 0
    with _istek(son["paket_url"], zaman_asimi=60) as r, open(arsiv + ".part", "wb") as fh:
        while True:
            parca = r.read(1 << 20)
            if not parca:
                break
            fh.write(parca)
            h.update(parca)
            alinan += len(parca)
            if toplam and ilerleme:
                ilerleme(int(alinan * 100 / toplam))
    if toplam and alinan != toplam:
        raise GuncellemeHatasi(f"İndirme yarıda kaldı ({alinan} / {toplam} bayt).")
    if son.get("sha256") and h.hexdigest() != son["sha256"].lower():
        raise GuncellemeHatasi("İndirilen paketin doğrulaması (SHA-256) tutmadı; güncelleme yapılmadı.")
    os.replace(arsiv + ".part", arsiv)
    hedef = _yeni_klasor_yolu(son["surum"], ana)
    gecici = hedef + "_aciliyor"   # önce geçici klasöre açılır, tamamı bitince tek adımda adı verilir
    shutil.rmtree(gecici, ignore_errors=True)
    os.makedirs(gecici)
    try:
        _ac(arsiv, gecici)
        if not os.path.isfile(program_dosyasi(os.path.join(gecici, PROGRAM_AD))):
            raise GuncellemeHatasi("Paketin içinde program bulunamadı.")
        shutil.rmtree(hedef, ignore_errors=True)
        os.rename(gecici, hedef)
        yeni = os.path.join(hedef, PROGRAM_AD)
    except Exception:
        shutil.rmtree(gecici, ignore_errors=True)
        raise
    finally:
        try:
            os.remove(arsiv)
        except OSError:
            pass
    d = durum_oku()
    d["hazir"] = {"surum": son["surum"], "klasor": yeni, "ana": ana}
    durum_yaz(d)
    return yeni


def hazir() -> dict | None:
    """İndirilmiş ve uygulanmayı bekleyen güncelleme (programın kendi klasörü için, hâlâ daha yeniyse)."""
    h = durum_oku().get("hazir")
    if not h or not yeni_mi(h.get("surum", "")) or not os.path.isfile(program_dosyasi(h.get("klasor", ""))):
        return None
    if paketli_mi() and os.path.normcase(os.path.abspath(h.get("ana", ""))) != os.path.normcase(program_klasoru()):
        return None   # başka bir kopya için indirilmiş
    return h


def acilista_uygulanacak() -> dict | None:
    """Açılışta kendiliğinden uygulanacak güncelleme: paketli, otomatik açık, en fazla 2 deneme."""
    if not (paketli_mi() and otomatik_mi()):
        return None
    h = hazir()
    return h if h and int(h.get("deneme", 0)) < 2 else None


def arka_planda(ilk_bekleme: float = 3.0):
    """Açılışta: kontrol et, yeni sürüm varsa ve uygunsa indir. Hiçbir hata programı etkilemez."""
    def calis():
        time.sleep(ilk_bekleme)
        try:
            son = kontrol()
            if not (son and son.get("yeni") and paketli_mi() and otomatik_mi() and yazilabilir_mi()):
                return
            if (hazir() or {}).get("surum") == son["surum"]:
                return
            baslat_indirme(son)
        except Exception as e:
            DURUM["hata"] = f"Güncelleme indirilemedi: {e}"
    threading.Thread(target=calis, daemon=True).start()


def baslat_indirme(son: dict) -> bool:
    with _kilit:
        if DURUM["indiriliyor"]:
            return False
        DURUM.update(indiriliyor=True, yuzde=0, hata=None)

    def calis():
        try:
            indir(son, ilerleme=lambda y: DURUM.__setitem__("yuzde", y))
        except Exception as e:
            DURUM["hata"] = f"Güncelleme indirilemedi: {e}"
        finally:
            DURUM["indiriliyor"] = False
    threading.Thread(target=calis, daemon=True).start()
    return True


# ---------------------------------------------------------------------------- 3. uygula
def _exec(exe: str, argv: list, env: dict):
    """Bu süreci yeni programla değiştirir. Web sunucusunun soketi devredilebilir açıldığından (werkzeug)
    exec'ten önce kapatılmazsa yeni sürüm aynı portu açamaz; 0-2 (konsol) dışındaki tüm tanıtıcılar kapatılır."""
    for akis in (sys.stdout, sys.stderr):
        try:
            akis.flush()
        except Exception:
            pass
    try:
        ust = os.sysconf("SC_OPEN_MAX")
    except (AttributeError, ValueError, OSError):
        ust = 4096
    os.closerange(3, ust if 0 < ust < 1 << 20 else 4096)
    os.execve(exe, argv, env)


def temiz_ortam() -> dict:
    """Yeni sürümü başlatırken PyInstaller'ın bu sürecin klasörüne işaret eden değişkenleri taşınmasın."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI_") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for ad in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        if ad + "_ORIG" in env:
            env[ad] = env.pop(ad + "_ORIG")
        elif paketli_mi():
            env.pop(ad, None)
    env["LOGO_ANALIZ_TARAYICI_ACMA"] = "1"   # açık sekme kendini yeniler; yeni sekme açılmasın
    return env


def eski_kopyalari_temizle(ana: str | None = None):
    """Önceki güncellemelerden kalan yedek klasörleri (…_eski_*) ve yarım kalmış yeni klasörleri siler."""
    ana = ana or program_klasoru()
    ust = os.path.dirname(ana)
    try:
        adlar = os.listdir(ust)
    except OSError:
        return
    h = (durum_oku().get("hazir") or {}).get("klasor", "")
    for ad in adlar:
        yol = os.path.join(ust, ad)
        if ad.startswith(f".{PROGRAM_AD}_eski_") or (ad.startswith(f".{PROGRAM_AD}_yeni_") and not h.startswith(yol)):
            shutil.rmtree(yol, ignore_errors=True)


def klasoru_degistir(ana: str, yeni: str) -> str:
    """POSIX: çalışan programın klasörü yenisiyle değiştirilir (dosyalar açıkken de yeniden adlandırılabilir).
    Eski klasör yanına '.LogoTigerAnaliz_eski_<zaman>' olarak taşınır; sonraki açılışta silinir."""
    eski = os.path.join(os.path.dirname(ana), f".{PROGRAM_AD}_eski_{dt.datetime.now():%Y%m%d%H%M%S}")
    os.rename(ana, eski)
    try:
        os.rename(yeni, ana)
    except OSError:
        os.rename(eski, ana)   # geri al
        raise
    shutil.rmtree(os.path.dirname(yeni), ignore_errors=True)   # boş kalan .LogoTigerAnaliz_yeni_x
    return eski


def windows_betigi(ana: str, yeni: str, pid: int) -> str:
    """Windows: program kapanınca klasörü değiştirip yeni sürümü açan .bat içeriği."""
    import ntpath   # betik yalnızca Windows'ta çalışır; yollar her sistemde Windows kuralıyla kurulur (test edilebilsin)
    eski = ntpath.join(ntpath.dirname(ana), f".{PROGRAM_AD}_eski_{dt.datetime.now():%Y%m%d%H%M%S}")
    exe = ntpath.join(ana, PROGRAM_AD + ".exe")
    return "\r\n".join([
        "@echo off", "chcp 65001 >nul", "title Logo Tiger Analiz guncelleniyor",
        "echo Logo Tiger Analiz guncelleniyor, lutfen bekleyin...",
        ":bekle",
        f'tasklist /NH /FI "PID eq {pid}" 2>nul | find " {pid} " >nul',
        "if not errorlevel 1 ( ping -n 2 127.0.0.1 >nul & goto bekle )",
        "set /a deneme=0",
        ":tasi",
        f'move "{ana}" "{eski}" >nul 2>&1',
        "if not errorlevel 1 goto yeni",
        "set /a deneme+=1",
        "if %deneme% geq 20 goto ac",
        "ping -n 2 127.0.0.1 >nul & goto tasi",
        ":yeni",
        f'move "{yeni}" "{ana}" >nul 2>&1',
        f'if errorlevel 1 ( move "{eski}" "{ana}" >nul 2>&1 & goto ac )',
        f'rmdir /s /q "{ntpath.dirname(yeni)}" >nul 2>&1',
        ":ac",
        "set LOGO_ANALIZ_TARAYICI_ACMA=1",
        f'start "" "{exe}"',
        f'rmdir /s /q "{eski}" >nul 2>&1',
        '(goto) 2>nul & del "%~f0"', ""])


def uygula(h: dict | None = None, cikis=os._exit):
    """Hazır güncellemeyi uygular ve yeni sürümü başlatır. Bu fonksiyon dönmez (program yeniden başlar)."""
    h = h or hazir()
    if not h:
        raise GuncellemeHatasi("Uygulanacak güncelleme yok.")
    ana, yeni = h["ana"], h["klasor"]
    DURUM["uyguluyor"] = True
    d = durum_oku()
    if d.get("hazir"):   # başarısız olursa açılışta sonsuz döngüye girmesin diye deneme sayılır
        d["hazir"]["deneme"] = int(d["hazir"].get("deneme", 0)) + 1
    d["son_guncelleme"] = {"surum": h["surum"], "onceki": __version__, "tarih": dt.datetime.now().isoformat(timespec="seconds")}
    durum_yaz(d)
    from . import tanilama
    tanilama.yaz(f"Güncelleme: {__version__} -> {h['surum']}")
    if WINDOWS:
        bat = os.path.join(_calisma(), "guncelle.bat")
        with open(bat, "w", encoding="utf-8") as fh:
            fh.write(windows_betigi(ana, yeni, os.getpid()))
        # kendi penceresinde çalışsın ("güncelleniyor" yazısı görünsün) ve program kapanınca yaşamaya devam etsin
        bayrak = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(["cmd", "/c", bat], creationflags=bayrak, close_fds=True, env=temiz_ortam())
        cikis(0)
        return
    klasoru_degistir(ana, yeni)
    d = durum_oku()
    d.pop("hazir", None)
    durum_yaz(d)
    exe = program_dosyasi(ana)
    _exec(exe, [exe] + [a for a in sys.argv[1:] if a != "--mcp"], temiz_ortam())


# ---------------------------------------------------------------------------- kaynak koddan çalışırken
def git_klasoru() -> str | None:
    k = program_klasoru()
    return k if (not paketli_mi() and os.path.isdir(os.path.join(k, ".git")) and shutil.which("git")) else None


def git_guncelle() -> str:
    """git pull --ff-only + gereken paketler. Yerel değişiklik varsa dokunmaz, hatayı döndürür."""
    k = git_klasoru()
    if not k:
        raise GuncellemeHatasi("Bu kopya git ile indirilmemiş; yeni sürümü Releases sayfasından indirin.")
    r = subprocess.run(["git", "-C", k, "pull", "--ff-only"], capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise GuncellemeHatasi("git pull yapılamadı (yerel değişiklik olabilir): " + (r.stderr or r.stdout).strip()[-400:])
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", os.path.join(k, "requirements.txt")],
                   capture_output=True, text=True, timeout=600)
    return r.stdout.strip()


def git_yeniden_baslat():
    env = dict(os.environ, LOGO_ANALIZ_TARAYICI_ACMA="1")
    if WINDOWS:   # Windows'ta exec yeni süreç açar; konsol penceresi korunsun diye aynı yolla başlatılır
        subprocess.Popen([sys.executable] + sys.argv, env=env)
        os._exit(0)
    _exec(sys.executable, [sys.executable] + sys.argv, env)


# ---------------------------------------------------------------------------- arayüz için özet
def ozet() -> dict:
    d = durum_oku()
    son = d.get("son") or {}
    h = hazir()
    return {"surum": __version__, "son": son.get("surum"), "yeni": yeni_mi(son.get("surum", "")),
            "sayfa": son.get("sayfa") or SAYFA, "notlar": son.get("notlar"),
            "son_kontrol": dt.datetime.fromtimestamp(d["son_kontrol"]).strftime("%d.%m.%Y %H:%M") if d.get("son_kontrol") else None,
            "otomatik": otomatik_mi(), "paketli": paketli_mi(), "git": bool(git_klasoru()),
            "yazilabilir": yazilabilir_mi() if paketli_mi() else True,
            "hazir": h["surum"] if h else None, "indiriliyor": DURUM["indiriliyor"], "yuzde": DURUM["yuzde"],
            "hata": DURUM["hata"], "son_guncelleme": d.get("son_guncelleme")}
