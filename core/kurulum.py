"""
Otomatik SQL Server hazırlığı — kullanıcının elle hiçbir şey kurmaması için.

Windows : Programın kendi SQL Server Express örneğini ("LOGOANALIZ") Microsoft'tan indirip sessizce kurar.
          Kullanıcı sadece Windows'un yönetici izni (UAC) penceresine "Evet" der.
          sa şifresi rastgele üretilir ve Windows DPAPI ile şifrelenerek saklanır.
macOS / : Docker kuruluysa SQL Server konteynerini ("logo-analiz-mssql") kendisi oluşturur/başlatır.
Linux     Docker yoksa kullanıcıya indirme bağlantısı gösterilir.

Ana giriş noktası:
    baglanti = hazirla(ilerleme=print)   # gerekirse kurar, çalıştırır, bağlantıyı test eder
    -> {"sunucu", "port", "kullanici", "sifre", "yedek_klasoru", "sunucu_yedek_klasoru"}
"""
from __future__ import annotations

import base64
import json
import os
import platform
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
import time
import urllib.request

ORNEK = "LOGOANALIZ"
WINDOWS_PORT = 14330
KONTEYNER = "logo-analiz-mssql"
DOCKER_IMAJ = "mcr.microsoft.com/mssql/server:2022-latest"
DOCKER_PORT = 14333
# Microsoft SQL Server 2022 Express önyükleyicisi (SSEI). Değişirse winget yedek yol olarak denenir.
EXPRESS_URL = "https://download.microsoft.com/download/29654887-7cde-4397-bba3-d7f087970845/SQL2022-SSEI-Expr.exe"
EXPRESS_SAYFA = "https://www.microsoft.com/sql-server/sql-server-downloads"
DOCKER_SAYFA = "https://www.docker.com/products/docker-desktop/"

WINDOWS = os.name == "nt"
MAC = sys.platform == "darwin"


class KurulumHatasi(Exception):
    pass


def calisma_klasoru():
    k = os.environ.get("LOGO_ANALIZ_KLASOR", os.path.join(os.path.expanduser("~"), "LogoAnaliz"))
    os.makedirs(k, exist_ok=True)
    return k


def _ayar_yolu():
    return os.path.join(calisma_klasoru(), "ayarlar.json")


def windows_yedek_klasoru():
    # SQL Server servis hesabının okuyabildiği, kullanıcının yazabildiği ortak klasör (kurulumda izin verilir)
    return os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "LogoAnaliz", "yedek")


# ---------------------------------------------------------------------------- şifre saklama
def _dpapi(veri: bytes, koru: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    giris = BLOB(len(veri), ctypes.cast(ctypes.create_string_buffer(veri, len(veri)), ctypes.POINTER(ctypes.c_char)))
    cikis = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if koru else ctypes.windll.crypt32.CryptUnprotectData
    if koru:
        ok = fn(ctypes.byref(giris), "LogoAnaliz", None, None, None, 0, ctypes.byref(cikis))
    else:
        ok = fn(ctypes.byref(giris), None, None, None, None, 0, ctypes.byref(cikis))
    if not ok:
        raise KurulumHatasi("Şifre çözülemedi (DPAPI). Ayar dosyası başka bir kullanıcıya ait olabilir.")
    sonuc = ctypes.string_at(cikis.pbData, cikis.cbData)
    ctypes.windll.kernel32.LocalFree(cikis.pbData)
    return sonuc


def _sifre_sakla(s: str) -> str:
    if WINDOWS:
        return "dpapi:" + base64.b64encode(_dpapi(s.encode(), True)).decode()
    return "b64:" + base64.b64encode(s.encode()).decode()


def _sifre_ac(s: str) -> str:
    tur, _, deger = s.partition(":")
    ham = base64.b64decode(deger)
    return _dpapi(ham, False).decode() if tur == "dpapi" else ham.decode()


SIFRELI = (("sql", "sifre"), ("yz", "anahtar"))  # diske şifreli yazılan alanlar


def ayar_oku() -> dict:
    try:
        with open(_ayar_yolu(), encoding="utf-8") as fh:
            a = json.load(fh)
    except (OSError, ValueError):
        return {}
    for bolum, alan in SIFRELI:
        if (a.get(bolum) or {}).get(alan):
            try:
                a[bolum][alan] = _sifre_ac(a[bolum][alan])
            except Exception:
                a[bolum].pop(alan, None)
                if bolum == "sql":
                    a.pop("sql", None)
    return a


def ayar_yaz(a: dict):
    a = json.loads(json.dumps(a))
    for bolum, alan in SIFRELI:
        if (a.get(bolum) or {}).get(alan):
            a[bolum][alan] = _sifre_sakla(a[bolum][alan])
    yol = _ayar_yolu()
    with open(yol, "w", encoding="utf-8") as fh:
        json.dump(a, fh, ensure_ascii=False, indent=1)
    if not WINDOWS:
        os.chmod(yol, 0o600)


def _guclu_sifre(n=24):
    # SQL Server karmaşıklık kuralı: büyük, küçük, rakam, sembol. Komut satırı/ini için sorunsuz semboller.
    harf = string.ascii_letters + string.digits
    s = [secrets.choice(string.ascii_uppercase), secrets.choice(string.ascii_lowercase),
         secrets.choice(string.digits), secrets.choice("!#%+-=")]
    s += [secrets.choice(harf + "!#%+-=") for _ in range(n - 4)]
    secrets.SystemRandom().shuffle(s)
    return "".join(s)


# ---------------------------------------------------------------------------- bağlantı testi
def baglanti_dene(sunucu, port, kullanici, sifre, zaman_asimi=8):
    try:
        import pymssql
    except ImportError:
        return False, "pymssql paketi yok"
    kw = dict(server=sunucu, user=kullanici, password=sifre, database="master", login_timeout=zaman_asimi)
    if "\\" not in sunucu:
        kw["port"] = port
    try:
        con = pymssql.connect(**kw)
        cur = con.cursor()
        cur.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(50))")
        surum = cur.fetchone()[0]
        con.close()
        return True, surum
    except Exception as e:
        return False, str(e)[:300]


# ---------------------------------------------------------------------------- Windows
def windows_ornekleri() -> list[dict]:
    """Bu bilgisayardaki SQL Server örnekleri (Logo'nun kendi örneği dahil)."""
    if not WINDOWS:
        return []
    import winreg
    sonuc = []
    for gorunum in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Microsoft SQL Server\Instance Names\SQL",
                               0, winreg.KEY_READ | gorunum)
        except OSError:
            continue
        i = 0
        while True:
            try:
                ad, kimlik, _ = winreg.EnumValue(k, i)
            except OSError:
                break
            i += 1
            if any(o["ad"] == ad for o in sonuc):
                continue
            port = _windows_tcp_portu(kimlik)
            servis = "MSSQLSERVER" if ad == "MSSQLSERVER" else f"MSSQL${ad}"
            sonuc.append({"ad": ad, "kimlik": kimlik, "port": port, "servis": servis,
                          "calisiyor": _servis_calisiyor(servis),
                          "sunucu": "localhost" if ad == "MSSQLSERVER" else f"localhost\\{ad}"})
    return sonuc


def _windows_tcp_portu(kimlik):
    import winreg
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           rf"SOFTWARE\Microsoft\Microsoft SQL Server\{kimlik}\MSSQLServer\SuperSocketNetLib\Tcp\IPAll",
                           0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except OSError:
        return None
    for ad in ("TcpPort", "TcpDynamicPorts"):
        try:
            v = str(winreg.QueryValueEx(k, ad)[0]).split(",")[0].strip()
            if v and v != "0":
                return int(v)
        except (OSError, ValueError):
            pass
    return None


def _servis_calisiyor(servis):
    try:
        r = subprocess.run(["sc", "query", servis], capture_output=True, text=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "RUNNING" in r.stdout
    except Exception:
        return False


def _yonetici_olarak(ps1_yolu, ilerleme, zaman_asimi=3600):
    """PowerShell betiğini yönetici olarak çalıştırır (UAC penceresi çıkar). Çıkış kodunu döndürür."""
    sonuc = ps1_yolu + ".sonuc"
    if os.path.exists(sonuc):
        os.remove(sonuc)
    komut = (f"$p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
             f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"{ps1_yolu}\"'; "
             f"exit $p.ExitCode")
    ilerleme("Windows yönetici izni istiyor — açılan pencerede 'Evet' deyin.")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", komut],
                           capture_output=True, text=True, timeout=zaman_asimi,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        raise KurulumHatasi("Kurulum zaman aşımına uğradı.") from e
    if "canceled by the user" in (r.stderr or "") or "iptal" in (r.stderr or "").lower():
        raise KurulumHatasi("Yönetici izni verilmedi. Kurulum için izin gerekiyor; tekrar deneyin.")
    if os.path.exists(sonuc):
        with open(sonuc, encoding="utf-8", errors="replace") as fh:
            return int((fh.read().strip() or "1").split()[0])
    return r.returncode


def _indir(url, hedef, ilerleme):
    ilerleme(f"İndiriliyor: {url.rsplit('/', 1)[-1]}")
    with urllib.request.urlopen(url, timeout=60) as r, open(hedef, "wb") as fh:
        toplam = int(r.headers.get("Content-Length") or 0)
        alinan, son = 0, 0
        while True:
            parca = r.read(1 << 20)
            if not parca:
                break
            fh.write(parca)
            alinan += len(parca)
            if toplam and alinan * 100 // toplam >= son + 20:
                son = alinan * 100 // toplam
                ilerleme(f"  %{son}")


def windows_express_kur(ilerleme=print) -> dict:
    if not WINDOWS:
        raise KurulumHatasi("Bu kurulum sadece Windows içindir.")
    if platform.machine().upper() not in ("AMD64", "X86_64"):
        raise KurulumHatasi("SQL Server Express sadece 64 bit Intel/AMD Windows'ta çalışır.")
    klasor = os.path.join(calisma_klasoru(), "_kurulum")
    os.makedirs(klasor, exist_ok=True)
    sifre = _guclu_sifre()

    # 1) Önyükleyici (yönetici izni gerekmez). Önyükleyicinin kendisi ise yönetici ister
    #    (manifest: requireAdministrator; normal kullanıcıyla çalıştırınca WinError 740), bu yüzden
    #    medya indirme ve dosya açma adımları aşağıdaki yönetici betiğinin içinde yapılır: tek UAC penceresi.
    ssei = os.path.join(klasor, "SQL2022-SSEI-Expr.exe")
    medya = os.path.join(klasor, "medya")
    ac = os.path.join(klasor, "ac")
    hazir = os.path.exists(os.path.join(ac, "SETUP.EXE"))
    medya_var = os.path.isdir(medya) and any(f.lower().endswith(".exe") for f in os.listdir(medya))
    if not hazir and not medya_var and not os.path.exists(ssei):
        try:
            _indir(EXPRESS_URL, ssei, ilerleme)
        except Exception as e:
            raise KurulumHatasi(f"SQL Server Express indirilemedi (internet bağlantısı?). Ayrıntı: {e}. "
                                f"Elle kurmak için: {EXPRESS_SAYFA}") from e

    # 2) Yönetici olarak: medya indirme (~250 MB) + dosyaları açma + sessiz kurulum
    kullanici = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\")
    ini = os.path.join(klasor, "ayar.ini")
    with open(ini, "w", encoding="utf-8") as fh:
        fh.write(f"""[OPTIONS]
ACTION="Install"
ENU="True"
QUIET="True"
SUPPRESSPRIVACYSTATEMENTNOTICE="True"
IACCEPTSQLSERVERLICENSETERMS="True"
UPDATEENABLED="False"
USEMICROSOFTUPDATE="False"
FEATURES=SQLENGINE
INSTANCENAME="{ORNEK}"
INSTANCEID="{ORNEK}"
SQLSVCSTARTUPTYPE="Automatic"
BROWSERSVCSTARTUPTYPE="Automatic"
TCPENABLED="1"
NPENABLED="0"
SQLCOLLATION="Turkish_CI_AS"
SECURITYMODE="SQL"
SAPWD="{sifre}"
SQLSYSADMINACCOUNTS="{kullanici}"
""")
    yedek = windows_yedek_klasoru()
    ps1 = os.path.join(klasor, "kur.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as fh:
        fh.write(rf"""$ErrorActionPreference = 'Continue'
$sonuc = '{ps1}.sonuc'
try {{
  New-Item -ItemType Directory -Force -Path '{yedek}' | Out-Null
  icacls '{yedek}' /grant '*S-1-1-0:(OI)(CI)M' /T | Out-Null
  $kurulum = '{os.path.join(ac, "SETUP.EXE")}'
  if (-not (Test-Path $kurulum)) {{
    $pk = Get-ChildItem -Path '{medya}' -Filter *.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $pk) {{
      Start-Process -FilePath '{ssei}' -ArgumentList '/ACTION=Download','"MEDIAPATH={medya}"','/MEDIATYPE=Core','/QUIET' -Wait -NoNewWindow
      $pk = Get-ChildItem -Path '{medya}' -Filter *.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    }}
    if (-not $pk) {{ Set-Content -Path $sonuc -Value '2'; exit 2 }}
    Start-Process -FilePath $pk.FullName -ArgumentList '/q','"/x:{ac}"' -Wait -NoNewWindow
    if (-not (Test-Path $kurulum)) {{ Set-Content -Path $sonuc -Value '3'; exit 3 }}
  }}
  $p = Start-Process -FilePath '{os.path.join(ac, "SETUP.EXE")}' -ArgumentList '/ConfigurationFile="{ini}"' -Wait -PassThru -NoNewWindow
  $kod = $p.ExitCode
  # 3010 = kurulum tamam, yeniden başlatma önerilir
  if ($kod -eq 3010) {{ $kod = 0 }}
  if ($kod -eq 0) {{
    # sabit TCP portu: SQL Browser'a ve değişen dinamik porta bağımlı kalmayalım
    $id = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\Instance Names\SQL').{ORNEK}
    $k = "HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\$id\MSSQLServer\SuperSocketNetLib\Tcp\IPAll"
    Set-ItemProperty -Path $k -Name TcpDynamicPorts -Value ''
    Set-ItemProperty -Path $k -Name TcpPort -Value '{WINDOWS_PORT}'
    Restart-Service -Name 'MSSQL${ORNEK}' -Force
  }}
  Set-Content -Path $sonuc -Value $kod
  exit $kod
}} catch {{
  Set-Content -Path $sonuc -Value "1 $_"
  exit 1
}}
""")
    ilerleme("SQL Server Express indirilip kuruluyor (10–15 dakika sürebilir, bilgisayarı kapatmayın)…")
    try:
        kod = _yonetici_olarak(ps1, ilerleme)
    finally:
        try:
            os.remove(ini)  # şifre içeriyor
        except OSError:
            pass
    if kod == 2:
        raise KurulumHatasi(f"SQL Server Express kurulum dosyası (~250 MB) indirilemedi. İnternet bağlantısını kontrol edin. "
                            f"Elle kurmak için: {EXPRESS_SAYFA}")
    if kod == 3:
        raise KurulumHatasi("SQL Server kurulum dosyaları açılamadı. Diskte yer var mı? Tekrar deneyin.")
    if kod != 0:
        log = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Microsoft SQL Server")
        raise KurulumHatasi(f"SQL Server kurulumu başarısız (kod {kod}). Kurulum günlükleri: {log}\\…\\Setup Bootstrap\\Log")

    ilerleme("Kurulum tamam, SQL Server başlatılıyor…")
    ornek = next((o for o in windows_ornekleri() if o["ad"] == ORNEK), None)
    port = (ornek or {}).get("port") or WINDOWS_PORT
    sql = {"tur": "windows", "ornek": ORNEK, "sunucu": "127.0.0.1", "port": port, "kullanici": "sa", "sifre": sifre,
           "yedek_klasoru": yedek, "sunucu_yedek_klasoru": yedek}
    _bekle_baglan(sql, ilerleme)
    a = ayar_oku()
    a["sql"] = sql
    ayar_yaz(a)
    shutil.rmtree(os.path.join(klasor, "ac"), ignore_errors=True)
    return sql


def _windows_baslat(sql, ilerleme):
    servis = f"MSSQL${sql.get('ornek', ORNEK)}"
    if _servis_calisiyor(servis):
        return
    ilerleme("SQL Server servisi başlatılıyor…")
    ps1 = os.path.join(tempfile.gettempdir(), "logoanaliz_baslat.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as fh:
        fh.write(f"Start-Service -Name '{servis}'; Set-Content -Path '{ps1}.sonuc' -Value 0\n")
    _yonetici_olarak(ps1, ilerleme, zaman_asimi=300)


# ---------------------------------------------------------------------------- Docker (macOS / Linux)
def _docker(*arg, zaman_asimi=120):
    return subprocess.run(["docker", *arg], capture_output=True, text=True, timeout=zaman_asimi)


def docker_durumu():
    """yok | izin_yok | kapali | calisiyor"""
    if not shutil.which("docker"):
        return "yok"
    try:
        r = _docker("info", zaman_asimi=20)
    except Exception:
        return "kapali"
    if r.returncode == 0:
        return "calisiyor"
    if "permission denied" in (r.stderr or "").lower():
        return "izin_yok"
    return "kapali"


LINUX_DOCKER_KURULUM = ("curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER  "
                        "(sonra oturumu kapatıp açın)")


def docker_hazirla(ilerleme=print) -> dict:
    if not MAC and platform.machine().lower() in ("aarch64", "arm64"):
        raise KurulumHatasi("SQL Server'ın Linux sürümü sadece 64 bit Intel/AMD işlemcilerde çalışır (bu bilgisayar ARM). "
                            "Bir Windows bilgisayar ya da Apple Silicon Mac kullanın.")
    d = docker_durumu()
    if d == "yok":
        if MAC:
            raise KurulumHatasi(f"Bu bilgisayarda Docker yok. Docker Desktop'ı kurup açın: {DOCKER_SAYFA} — sonra tekrar deneyin.")
        raise KurulumHatasi(f"Bu bilgisayarda Docker yok. Terminalde bir kez şunu çalıştırın: {LINUX_DOCKER_KURULUM}")
    if d == "izin_yok":
        raise KurulumHatasi("Docker kurulu ama bu kullanıcının Docker'ı kullanma yetkisi yok. Terminalde: "
                            "sudo usermod -aG docker $USER  — sonra oturumu kapatıp açın (ya da bilgisayarı yeniden başlatın).")
    if d == "kapali":
        if MAC:
            ilerleme("Docker Desktop açılıyor…")
            subprocess.run(["open", "-a", "Docker"], capture_output=True)
        else:
            ilerleme("Docker servisi kapalı; başlatılıyor (şifre sorulabilir)…")
            if shutil.which("pkexec") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                subprocess.run(["pkexec", "systemctl", "start", "docker"], capture_output=True, timeout=300)
            subprocess.run(["systemctl", "--user", "start", "docker-desktop"], capture_output=True)
        for _ in range(40):
            time.sleep(3)
            if docker_durumu() == "calisiyor":
                break
        else:
            if MAC:
                raise KurulumHatasi("Docker çalışmıyor. Docker Desktop'ı açıp tekrar deneyin.")
            raise KurulumHatasi("Docker servisi çalışmıyor. Terminalde: sudo systemctl enable --now docker  — sonra tekrar deneyin.")

    a = ayar_oku()
    sql = a.get("sql") if a.get("sql", {}).get("tur") == "docker" else None
    yedek = os.path.join(calisma_klasoru(), "_sqlyedek")
    os.makedirs(yedek, exist_ok=True)
    os.chmod(yedek, 0o777)
    var = _docker("ps", "-a", "--filter", f"name=^{KONTEYNER}$", "--format", "{{.Names}}").stdout.strip()
    if var and sql:
        ilerleme("SQL Server konteyneri başlatılıyor…")
        _docker("start", KONTEYNER)
    else:
        if var:  # şifresini bilmediğimiz eski konteyner
            _docker("rm", "-f", KONTEYNER)
        sifre = _guclu_sifre()
        ilerleme("SQL Server imajı indiriliyor ve başlatılıyor (ilk seferde birkaç dakika)…")
        r = _docker("run", "-d", "--name", KONTEYNER, "--platform", "linux/amd64",
                    "-e", "ACCEPT_EULA=Y", "-e", f"MSSQL_SA_PASSWORD={sifre}", "-e", "MSSQL_PID=Developer",
                    "-e", "MSSQL_COLLATION=Turkish_CI_AS", "-p", f"127.0.0.1:{DOCKER_PORT}:1433",
                    "-v", f"{yedek}:/var/opt/mssql/backup", "--restart", "unless-stopped", DOCKER_IMAJ,
                    zaman_asimi=1800)
        if r.returncode != 0:
            raise KurulumHatasi(f"SQL Server konteyneri başlatılamadı: {r.stderr[-400:]}")
        sql = {"tur": "docker", "sunucu": "127.0.0.1", "port": DOCKER_PORT, "kullanici": "sa", "sifre": sifre,
               "yedek_klasoru": yedek, "sunucu_yedek_klasoru": "/var/opt/mssql/backup"}
    _bekle_baglan(sql, ilerleme, deneme=60)
    a["sql"] = sql
    ayar_yaz(a)
    return sql


# ---------------------------------------------------------------------------- ortak
def _bekle_baglan(sql, ilerleme, deneme=30):
    son = ""
    for i in range(deneme):
        ok, msg = baglanti_dene(sql["sunucu"], sql["port"], sql["kullanici"], sql["sifre"], zaman_asimi=5)
        if ok:
            ilerleme(f"SQL Server hazır (sürüm {msg}).")
            return
        son = msg
        time.sleep(3)
    raise KurulumHatasi(f"SQL Server'a bağlanılamadı: {son}")


def durum() -> dict:
    """Arayüz için: programın SQL Server'ı hazır mı, bu bilgisayarda başka SQL Server var mı."""
    a = ayar_oku()
    sql = a.get("sql")
    d = {"platform": "windows" if WINDOWS else ("mac" if MAC else "linux"), "hazir": False, "sql": None,
         "ornekler": windows_ornekleri() if WINDOWS else [], "docker": None if WINDOWS else docker_durumu()}
    if sql:
        d["sql"] = {k: v for k, v in sql.items() if k != "sifre"}
        if sql["tur"] == "windows":
            d["hazir"] = _servis_calisiyor(f"MSSQL${sql.get('ornek', ORNEK)}")
        else:
            d["hazir"] = d["docker"] == "calisiyor" and KONTEYNER in _docker(
                "ps", "--filter", f"name=^{KONTEYNER}$", "--format", "{{.Names}}", zaman_asimi=20).stdout
    return d


def hazirla(ilerleme=print) -> dict:
    """Programın SQL Server'ını hazır hale getirir (gerekirse kurar). Bağlantı bilgilerini döner."""
    a = ayar_oku()
    sql = a.get("sql")
    if sql:
        ok, _ = baglanti_dene(sql["sunucu"], sql["port"], sql["kullanici"], sql["sifre"], zaman_asimi=5)
        if ok:
            return sql
        if sql["tur"] == "windows":
            _windows_baslat(sql, ilerleme)
            # dinamik port değişmiş olabilir
            o = next((o for o in windows_ornekleri() if o["ad"] == sql.get("ornek", ORNEK)), None)
            if o and o["port"]:
                sql["port"] = o["port"]
            _bekle_baglan(sql, ilerleme)
            a["sql"] = sql
            ayar_yaz(a)
            return sql
        return docker_hazirla(ilerleme)
    if WINDOWS:
        if any(o["ad"] == ORNEK for o in windows_ornekleri()):
            raise KurulumHatasi(f"'{ORNEK}' SQL Server örneği kurulu ama ayar dosyası yok (başka kullanıcı kurmuş olabilir). "
                                f"Gelişmiş bölümünden sunucu bilgilerini elle girin.")
        ilerleme("Bu bilgisayarda programın SQL Server'ı yok; otomatik kuruluyor.")
        return windows_express_kur(ilerleme)
    return docker_hazirla(ilerleme)
