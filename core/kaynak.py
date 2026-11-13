"""
Veri kaynakları. Hepsi aynı arayüzü sunar:
    tablolar() -> list[str]
    kolonlar(tablo) -> list[str]
    tablo_oku(tablo, kolonlar) -> pandas.DataFrame
    kapat()

Kaynaklar:
  BakKaynak     : .bak dosyasını yerel SQL Server'a geçici veritabanı olarak geri yükler, okur, işi bitince siler.
  MssqlKaynak   : Zaten çalışan bir SQL Server veritabanına (Logo'nun canlı veritabanı) salt-okur bağlanır.
  SqliteKaynak  : Demo / test veritabanı.
  BirlesikKaynak: Birbirini tamamlayan birden fazla yedeği (ör. LOGODB + firma verileri) tek kaynak gibi gösterir.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import time
import uuid

import pandas as pd


class KaynakHatasi(Exception):
    pass


def _guvenli_ad(ad: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", ad):
        raise KaynakHatasi(f"Geçersiz tablo/kolon adı: {ad!r}")
    return ad


# ---------------------------------------------------------------------------- SQLite (demo)
class SqliteKaynak:
    tur = "sqlite"

    def __init__(self, yol: str):
        if not os.path.exists(yol):
            raise KaynakHatasi(f"Dosya bulunamadı: {yol}")
        self.yol = yol
        self.con = sqlite3.connect(yol, check_same_thread=False)
        self.ad = os.path.basename(yol)

    def tablolar(self):
        return [r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

    def kolonlar(self, tablo):
        return [r[1].upper() for r in self.con.execute(f"PRAGMA table_info({_guvenli_ad(tablo)})")]

    def tablo_oku(self, tablo, kolonlar):
        cols = ", ".join(_guvenli_ad(c) for c in kolonlar)
        return pd.read_sql_query(f"SELECT {cols} FROM {_guvenli_ad(tablo)}", self.con)

    def kapat(self):
        self.con.close()


# ---------------------------------------------------------------------------- SQL Server
def _pymssql():
    try:
        import pymssql  # noqa
        return pymssql
    except ImportError as e:
        raise KaynakHatasi("SQL Server bağlantısı için 'pymssql' paketi gerekli: pip install pymssql") from e


def _baglanti_ayari(sunucu, veritabani, port):
    kw = dict(server=sunucu, database=veritabani, login_timeout=15, charset="UTF-8")
    # "BILGISAYAR\\SQLEXPRESS" gibi adlandırılmış örneklerde port SQL Browser'dan bulunur; port verilmez
    if "\\" not in sunucu:
        kw["port"] = port
    return kw


class MssqlKaynak:
    """Var olan bir SQL Server veritabanına bağlanır. Sadece SELECT çalıştırır."""
    tur = "mssql"

    def __init__(self, sunucu: str, veritabani: str, kullanici: str = "", sifre: str = "", port: int = 1433):
        pymssql = _pymssql()
        self.ad = veritabani
        kw = _baglanti_ayari(sunucu, veritabani, port)
        if kullanici:
            kw.update(user=kullanici, password=sifre)
        try:
            self.con = pymssql.connect(**kw)
        except Exception as e:  # pymssql hata mesajları uzun; kısaltıp veriyoruz
            raise KaynakHatasi(f"SQL Server'a bağlanılamadı ({sunucu}/{veritabani}): {str(e)[:300]}") from e

    def _sorgu(self, sql, params=None):
        cur = self.con.cursor()
        cur.execute(sql, params or ())
        return cur.fetchall()

    def tablolar(self):
        return [r[0] for r in self._sorgu(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME")]

    def _tipler(self, tablo):
        if not hasattr(self, "_tip_onbellek"):
            self._tip_onbellek = {}
        if tablo not in self._tip_onbellek:
            self._tip_onbellek[tablo] = {r[0].upper(): (r[1].lower(), r[2]) for r in self._sorgu(
                "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (tablo,))}
        return self._tip_onbellek[tablo]

    def kolonlar(self, tablo):
        return list(self._tipler(tablo))

    def tablo_oku(self, tablo, kolonlar):
        # varchar/char kolonlar sunucuda nvarchar'a çevrilir: dönüşümü veritabanının kendi harmanlaması
        # (Logo'da Turkish_CI_AS → cp1254) yapar; sürücünün yanlış kod sayfası seçmesi Türkçe karakterleri bozmaz.
        tipler = self._tipler(tablo)
        secim = []
        for c in kolonlar:
            ad = _guvenli_ad(c)
            tip, uz = tipler.get(c.upper(), (None, None))
            if tip in ("varchar", "char", "text"):
                hedef = "nvarchar(max)" if tip == "text" or uz in (None, -1) or uz > 4000 else f"nvarchar({max(int(uz), 1)})"
                secim.append(f"CAST([{ad}] AS {hedef}) AS [{ad}]")
            else:
                secim.append(f"[{ad}]")
        cols = ", ".join(secim)
        cur = self.con.cursor()
        cur.execute(f"SELECT {cols} FROM [dbo].[{_guvenli_ad(tablo)}] WITH (NOLOCK)")
        parcalar = []
        while True:
            rows = cur.fetchmany(50000)
            if not rows:
                break
            parcalar.append(pd.DataFrame.from_records(rows, columns=list(kolonlar)))
        return pd.concat(parcalar, ignore_index=True) if parcalar else pd.DataFrame(columns=list(kolonlar))

    def kapat(self):
        try:
            self.con.close()
        except Exception:
            pass


_LG_RE = re.compile(r"^LG_\d{3}_", re.I)
SISTEM_TABLOLARI = ("L_CAPIFIRM", "L_CAPIPERIOD", "L_CAPIWHOUSE")


def rol(kaynak) -> str:
    """Yedeğin ne içerdiği: 'veri' (LG_XXX_ firma tabloları), 'sistem' (sadece L_CAPIFIRM vb. — LOGODB) ya da 'bos'."""
    ts = {t.upper() for t in kaynak.tablolar()}
    if any(_LG_RE.match(t) for t in ts):
        return "veri"
    if ts & set(SISTEM_TABLOLARI):
        return "sistem"
    return "bos"


class BirlesikKaynak:
    """
    Birbirini tamamlayan birden fazla veritabanını tek kaynak gibi gösterir.

    Logo Tiger'da firma tanımları (L_CAPIFIRM: firma adları, L_CAPIPERIOD: dönem tarihleri, L_CAPIWHOUSE: ambarlar)
    çoğu kurulumda ayrı bir sistem veritabanında (LOGODB) durur; firma verileri (LG_XXX_…) başka bir veritabanındadır.
    Firmadan iki ayrı .bak gelir. Her tablo, onu içeren ilk kaynaktan okunur.
    """

    def __init__(self, kaynaklar: list):
        if not kaynaklar:
            raise KaynakHatasi("Kaynak yok.")
        self.kaynaklar = list(kaynaklar)
        self.tur = self.kaynaklar[0].tur
        self.ad = " + ".join(k.ad for k in self.kaynaklar)
        self._yer: dict[str, object] = {}
        self._adlar: dict[str, str] = {}
        self.cakisan: list[str] = []
        for k in self.kaynaklar:
            for t in k.tablolar():
                u = t.upper()
                if u in self._yer:
                    self.cakisan.append(u)
                    continue
                self._yer[u] = k
                self._adlar[u] = t

    def _kaynak(self, tablo):
        try:
            return self._yer[tablo.upper()]
        except KeyError:
            raise KaynakHatasi(f"Tablo hiçbir yedekte yok: {tablo}") from None

    def tablolar(self):
        return sorted(self._adlar.values())

    def kolonlar(self, tablo):
        return self._kaynak(tablo).kolonlar(self._adlar[tablo.upper()])

    def tablo_oku(self, tablo, kolonlar):
        return self._kaynak(tablo).tablo_oku(self._adlar[tablo.upper()], kolonlar)

    def kapat(self):
        for k in self.kaynaklar:
            try:
                k.kapat()
            except Exception:
                pass


def birlestir(kaynaklar: list, ilerleme=None):
    """Açılmış kaynakları rollerine göre sıralayıp birleştirir. Tek kaynak verilirse onu olduğu gibi döner.
    Firma verisi içeren hiçbir yedek yoksa anlaşılır bir hata verir."""
    say = ilerleme or (lambda m: None)
    roller = [(k, rol(k)) for k in kaynaklar]
    for k, r in roller:
        say(f"{k.ad}: " + {"veri": "firma verileri (LG_ tabloları) bulundu.",
                           "sistem": "Logo sistem veritabanı (firma adları, dönem tarihleri, ambarlar) bulundu.",
                           "bos": "Logo tablosu bulunamadı."}[r])
    if not any(r == "veri" for _, r in roller):
        if any(r == "sistem" for _, r in roller):
            raise KaynakHatasi("Seçilen yedek(ler) yalnızca Logo'nun sistem veritabanı (LOGODB: firma adları ve "
                               "dönemler). Firma verilerinin olduğu yedeği de ekleyin (genelde daha büyük olan, "
                               "adı TIGER, TIGERDB, GO3 vb.).")
        raise KaynakHatasi("Seçilen yedek(ler)de Logo firma tabloları (LG_XXX_CLCARD, LG_XXX_PP_STLINE…) bulunamadı.")
    if len(kaynaklar) == 1:
        return kaynaklar[0]
    # firma verileri önce: aynı tablo iki yedekte de varsa veri yedeğindeki kullanılır
    sirali = [k for k, r in roller if r == "veri"] + [k for k, r in roller if r != "veri"]
    b = BirlesikKaynak(sirali)
    lg_cakisan = sorted({t for t in b.cakisan if _LG_RE.match(t)})
    if lg_cakisan:
        firmalar = sorted({t[3:6] for t in lg_cakisan})
        say(f"Uyarı: {', '.join(firmalar)} numaralı firmanın tabloları birden fazla yedekte var; "
            f"ilk yedekteki ({sirali[0].ad}) kullanılıyor.")
    return b


_GECICI_AD = re.compile(r"LOGOANALIZ_[0-9A-F]{8}")
_ACIK: set[str] = set()  # bu programın şu an açık tuttuğu geçici veritabanları (temizlikte dokunulmaz)


def _gb(bayt):
    return f"{bayt / 1024 ** 3:,.1f} GB".replace(",", "X").replace(".", ",").replace("X", ".")


def _bos_alan(yol):
    try:
        return shutil.disk_usage(yol).free
    except (OSError, ValueError):
        return None


ALT_KLASOR = "LogoAnalizVeri"  # C: dolduğunda başka sürücüde kullanılan klasör: <sürücü>:\\LogoAnalizVeri\\{yedek,veri}
_PAY = 1024 ** 3  # sürücüyü ağzına kadar doldurmamak için bırakılan pay


def _yerel_suruculer() -> list[str]:
    """Windows'ta bu bilgisayarın sabit diskleri (C:\\, D:\\ …); diğer sistemlerde boş liste."""
    if os.name != "nt":
        return []
    import ctypes
    import string
    k32 = ctypes.windll.kernel32
    maske = k32.GetLogicalDrives()
    return [f"{h}:\\" for i, h in enumerate(string.ascii_uppercase)
            if maske >> i & 1 and k32.GetDriveTypeW(f"{h}:\\") == 3]  # 3 = DRIVE_FIXED


def _surucu(yol: str) -> str:
    return os.path.splitdrive(os.path.abspath(yol))[0].upper() or "/"


def _yer_kontrolu(dosyalar, veri_kl, log_kl):
    """Geri yüklenecek veritabanının diske sığıp sığmadığını baştan söyler (SQL Server aynı bilgisayardaysa).
    Sıkıştırılmış yedekler açılınca .bak dosyasından kat kat büyük olabilir."""
    gerek: dict[str, list] = {}  # sürücü -> [örnek klasör, gereken bayt]
    for f in dosyalar:
        klasor = log_kl if f.get("Type") == "L" else veri_kl
        if not klasor or not os.path.isdir(klasor):
            return  # Docker ya da uzak sunucu: diskini buradan göremeyiz
        try:
            boyut = int(f.get("Size") or 0)
        except (TypeError, ValueError):
            boyut = 0
        surucu = os.path.splitdrive(os.path.abspath(klasor))[0] or "/"
        gerek.setdefault(surucu, [klasor, 0])[1] += boyut
    for surucu, (klasor, gerekli) in gerek.items():
        bos = _bos_alan(klasor)
        if bos is not None and gerekli > bos:
            raise KaynakHatasi(
                f"Diskte yer yetmiyor: yedeğin açılmış hali {_gb(gerekli)} tutuyor, {surucu} sürücüsünde {_gb(bos)} boş. "
                f"En az {_gb(gerekli - bos + 1024 ** 3)} yer açın (Geri Dönüşüm Kutusu, İndirilenler, eski yedekler) "
                f"ve tekrar deneyin.")


class BakKaynak(MssqlKaynak):
    """
    .bak dosyasını yerel SQL Server'a geçici bir veritabanı olarak geri yükler.

    Gereken: o bilgisayarda çalışan bir SQL Server (Windows: SQL Server Express; Mac: Docker'da SQL Server)
    ve restore yetkisi olan bir kullanıcı (Windows'ta genelde Windows oturumu yeterli, kullanici boş bırakılır).

    Not: SQL Server servisi .bak dosyasını kendi hesabıyla okur. Dosya masaüstündeyse "erişim engellendi"
    hatası alınabilir; bu yüzden dosya önce SQL Server'ın yedek klasörüne (veya yedek_klasoru'ne) kopyalanır.
    """
    tur = "bak"

    def __init__(self, bak_yolu: str, sunucu: str = r"localhost\SQLEXPRESS", kullanici: str = "", sifre: str = "",
                 port: int = 1433, yedek_klasoru: str | None = None, sunucudaki_yol: str | None = None,
                 sunucu_yedek_klasoru: str | None = None, isi_bitince_sil: bool = True, ilerleme=None,
                 tasi: bool = False, artiklari_temizle: bool = False):
        """
        sunucudaki_yol: SQL Server dosyayı farklı bir yoldan görüyorsa (ör. Docker'da /var/opt/mssql/backup/x.bak)
                        o yol. Verilmezse bak_yolu (veya kopyalandığı yer) kullanılır.
        tasi: bak_yolu programın kendi geçici kopyasıysa (tarayıcıdan yüklenen) kopyalamak yerine taşınır.
        artiklari_temizle: önceki yarıda kalmış işlerden kalan LOGOANALIZ_XXXXXXXX veritabanları ve yedek kopyaları
                           silinir (yalnızca programın kendi SQL Server'ında kullanılır).
        """
        self._ilerleme = ilerleme or (lambda m: None)
        self.isi_bitince_sil = isi_bitince_sil
        pymssql = _pymssql()
        self.ad = os.path.splitext(os.path.basename(bak_yolu))[0]
        self.gecici_db = f"LOGOANALIZ_{uuid.uuid4().hex[:8].upper()}"
        kw = _baglanti_ayari(sunucu, "master", port)
        kw["autocommit"] = True
        if kullanici:
            kw.update(user=kullanici, password=sifre)
        try:
            self.master = pymssql.connect(**kw)
        except Exception as e:
            raise KaynakHatasi(
                f"Yerel SQL Server'a bağlanılamadı ({sunucu}). SQL Server Express kurulu ve çalışıyor mu? "
                f"Ayrıntı: {str(e)[:300]}") from e

        self._kopya = None
        if artiklari_temizle:
            self._artiklari_temizle(yedek_klasoru)
        _ACIK.add(self.gecici_db)
        try:
            yol = sunucudaki_yol or self._dosyayi_hazirla(bak_yolu, yedek_klasoru, sunucu_yedek_klasoru, tasi)
            self._geri_yukle(yol)
        except Exception:
            self._yarim_kalani_sil()
            raise
        kw["database"] = self.gecici_db
        kw.pop("autocommit")
        self.con = pymssql.connect(**kw)

    def _sorgu_m(self, sql, params=None):
        cur = self.master.cursor(as_dict=True)
        cur.execute(sql, params or ())
        try:
            sonuc = cur.fetchall()
        except Exception:
            sonuc = []
        # RESTORE gibi komutlar ilerleme mesajları üretir; hepsi tüketilmeden komut bitmiş sayılmaz
        try:
            while cur.nextset():
                pass
        except Exception:
            pass
        return sonuc

    def _artiklari_temizle(self, yedek_klasoru):
        """Program kapatıldığında ya da çöktüğünde silinemeyen geçici veritabanlarını ve yedek kopyalarını siler.
        Bunlar her denemede yedeğin boyutu kadar (ve fazlası) disk kaplar."""
        try:
            adlar = [r["name"] for r in self._sorgu_m("SELECT name FROM sys.databases WHERE name LIKE 'LOGOANALIZ[_]%'")]
        except Exception:
            adlar = []
        for ad in adlar:
            if not _GECICI_AD.fullmatch(ad) or ad in _ACIK:
                continue
            try:
                self._sorgu_m(f"ALTER DATABASE [{ad}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
                self._sorgu_m(f"DROP DATABASE [{ad}]")
                self._ilerleme(f"Önceki yarım kalmış denemeden kalan geçici veritabanı silindi ({ad}).")
            except Exception:
                pass
        klasorler = [yedek_klasoru] + [os.path.join(k, ALT_KLASOR, "yedek") for k in _yerel_suruculer()]
        for klasor in klasorler:
            if not klasor or not os.path.isdir(klasor):
                continue
            for dosya in os.listdir(klasor):
                kok = os.path.splitext(dosya)[0]
                if dosya.lower().endswith(".bak") and _GECICI_AD.fullmatch(kok) and kok not in _ACIK:
                    try:
                        os.remove(os.path.join(klasor, dosya))
                        self._ilerleme(f"Önceki denemeden kalan yedek kopyası silindi ({dosya}).")
                    except OSError:
                        pass

    def _servis_hesabi(self):
        """SQL Server servisinin Windows hesabı (ör. NT Service\\MSSQL$LOGOANALIZ); bulunamazsa None."""
        if not hasattr(self, "_hesap"):
            try:
                r = self._sorgu_m("SELECT service_account FROM sys.dm_server_services "
                                  "WHERE servicename LIKE 'SQL Server (%'")
                self._hesap = (r[0]["service_account"] or None) if r else None
            except Exception:
                self._hesap = None
        return self._hesap

    def _klasor_ac(self, yol) -> bool:
        """Başka sürücüde klasör açar ve SQL Server servisine yazma izni verir. Olmazsa False."""
        try:
            os.makedirs(yol, exist_ok=True)
        except OSError:
            return False
        if os.name == "nt":
            import subprocess
            hesap = self._servis_hesabi()
            kim = f"{hesap}:(OI)(CI)F" if hesap else "*S-1-1-0:(OI)(CI)M"  # bulunamazsa kurulumdaki gibi Everyone
            try:
                r = subprocess.run(["icacls", yol, "/grant", kim], capture_output=True,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=60)
                if r.returncode != 0 and hesap:
                    r = subprocess.run(["icacls", yol, "/grant", "*S-1-1-0:(OI)(CI)M"], capture_output=True,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=60)
                return r.returncode == 0
            except (OSError, subprocess.SubprocessError):
                return False
        return True

    def _diger_suruculer(self, alt, gerekli, haric=()):
        """Yeterli boş alanı olan diğer sabit disklerde <sürücü>:\\LogoAnalizVeri\\<alt> klasörleri (en boşu önce)."""
        adaylar = []
        for kok in _yerel_suruculer():
            if _surucu(kok) in haric:
                continue
            bos = _bos_alan(kok)
            if bos is not None and bos >= gerekli + _PAY:
                adaylar.append((bos, os.path.join(kok, ALT_KLASOR, alt)))
        return [k for _, k in sorted(adaylar, reverse=True)]

    def _hedef_klasorler(self, dosyalar, veri_kl, log_kl):
        """Veritabanı varsayılan klasöre (genelde C:) sığmıyorsa yeterli yeri olan başka bir sürücüye açılır."""
        try:
            _yer_kontrolu(dosyalar, veri_kl, log_kl)
            return veri_kl, log_kl
        except KaynakHatasi as ilk:
            gerekli = 0
            for f in dosyalar:
                try:
                    gerekli += int(f.get("Size") or 0)
                except (TypeError, ValueError):
                    pass
            for klasor in self._diger_suruculer("veri", gerekli, haric={_surucu(veri_kl)}):
                if self._klasor_ac(klasor):
                    self._ilerleme(f"{_surucu(veri_kl)} sürücüsünde yer yok; veritabanı {klasor} klasörüne açılıyor "
                                   f"({_gb(gerekli)}). İş bitince silinir.")
                    return klasor, klasor
            raise ilk

    def _yarim_kalani_sil(self):
        """Geri yükleme yarıda kalırsa geçici veritabanını ve kopyayı hemen siler (disk dolu kalmasın)."""
        _ACIK.discard(self.gecici_db)
        try:
            self._sorgu_m(f"IF DB_ID('{self.gecici_db}') IS NOT NULL DROP DATABASE [{self.gecici_db}]")
        except Exception:
            pass
        if self._kopya:
            try:
                os.remove(self._kopya)
            except OSError:
                pass
        try:
            self.master.close()
        except Exception:
            pass

    def _dosyayi_hazirla(self, bak_yolu, yedek_klasoru, sunucu_yedek_klasoru, tasi=False):
        """SQL Server servisi .bak'ı kendi hesabıyla okur; dosyayı onun erişebildiği bir klasöre kopyalar
        (tasi=True ise ve aynı sürücüdeyse taşır: ek disk alanı gerekmez).
        Dönen değer: SQL Server'ın dosyayı gördüğü yol."""
        import errno
        import shutil
        adaylar = []
        if yedek_klasoru:
            adaylar.append((yedek_klasoru, sunucu_yedek_klasoru or yedek_klasoru))
        else:
            r = self._sorgu_m("SELECT CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS nvarchar(4000)) AS p")
            if r and r[0]["p"]:
                adaylar.append((r[0]["p"], r[0]["p"]))
            if os.name == "nt":
                ortak = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "LogoAnaliz", "yedek")
                adaylar.append((ortak, ortak))
        ad = f"{self.gecici_db}.bak"
        boyut = os.path.getsize(bak_yolu)
        if _yerel_suruculer() and all(y == st for y, st in adaylar):  # Windows ve SQL Server dosyayı aynı yoldan görüyor (Docker değil)
            # Varsayılan klasör (C:) hem kopyayı hem açılacak veritabanını rahat almıyorsa en boş diğer sürücü önce
            mevcut = {_surucu(y) for y, _ in adaylar}
            diger = [(k, k) for k in self._diger_suruculer("yedek", boyut, haric=mevcut)]
            rahat = [a for a in adaylar if os.path.isdir(a[0]) and (_bos_alan(a[0]) or 0) >= 3 * boyut + _PAY]
            adaylar = rahat + diger + [a for a in adaylar if a not in rahat]
        for yerel, sunucu_tarafi in adaylar:
            if not os.path.isdir(yerel):
                if os.path.basename(os.path.dirname(yerel)) != ALT_KLASOR or not self._klasor_ac(yerel):
                    continue
            hedef = os.path.join(yerel, ad)
            if tasi:
                try:
                    os.replace(bak_yolu, hedef)  # aynı sürücüde anında, ek alan gerektirmez
                    self._kopya = hedef
                    ayrac = "/" if sunucu_tarafi.startswith("/") else "\\"
                    return sunucu_tarafi.rstrip("/\\") + ayrac + ad
                except OSError:
                    pass  # farklı sürücü ya da izin yok: kopyalamayı dene
            bos = _bos_alan(yerel)
            if bos is not None and bos < boyut + _PAY:
                continue  # sığmıyor; sıradaki klasörü dene (yoksa dosya yerinden okunur)
            try:
                self._ilerleme("Yedek dosyası SQL Server'ın okuyabileceği klasöre kopyalanıyor…")
                shutil.copyfile(bak_yolu, hedef)
            except OSError as e:
                try:
                    os.remove(hedef)  # yarım kopya diskte kalmasın
                except OSError:
                    pass
                if e.errno == errno.ENOSPC:
                    self._ilerleme("Kopya için yer yok; yedek bulunduğu yerden okunacak.")
                continue  # yazma izni yok ya da yer yok; sıradaki klasörü dene
            self._kopya = hedef
            ayrac = "/" if sunucu_tarafi.startswith("/") else "\\"
            return sunucu_tarafi.rstrip("/\\") + ayrac + ad
        return os.path.abspath(bak_yolu)

    def _geri_yukle(self, yol):
        self._ilerleme("Yedeğin içeriği okunuyor (FILELISTONLY)…")
        try:
            dosyalar = self._sorgu_m("RESTORE FILELISTONLY FROM DISK = %s", (yol,))
        except Exception as e:
            raise KaynakHatasi(f"Yedek okunamadı. Dosya bozuk, şifreli ya da daha yeni bir SQL Server sürümüyle "
                               f"alınmış olabilir. Ayrıntı: {str(e)[:400]}") from e
        r = self._sorgu_m("SELECT CAST(SERVERPROPERTY('InstanceDefaultDataPath') AS nvarchar(4000)) AS d, "
                          "CAST(SERVERPROPERTY('InstanceDefaultLogPath') AS nvarchar(4000)) AS l")
        veri_kl, log_kl = r[0]["d"], r[0]["l"]
        veri_kl, log_kl = self._hedef_klasorler(dosyalar, veri_kl, log_kl)
        ayrac = "\\" if "\\" in (veri_kl or "") else "/"
        moves, params = [], []
        for i, f in enumerate(dosyalar):
            uzanti = ".ldf" if f["Type"] == "L" else (".mdf" if i == 0 else f".ndf{i}")
            klasor = (log_kl if f["Type"] == "L" else veri_kl).rstrip("\\/")
            moves.append("MOVE %s TO %s")
            params += [f["LogicalName"], f"{klasor}{ayrac}{self.gecici_db}_{i}{uzanti}"]
        self._ilerleme(f"Yedek geçici veritabanına geri yükleniyor ({self.gecici_db})… Büyük yedeklerde birkaç dakika sürebilir.")
        t0 = time.time()
        try:
            self._sorgu_m(f"RESTORE DATABASE [{self.gecici_db}] FROM DISK = %s WITH {', '.join(moves)}, RECOVERY, STATS = 10",
                          tuple([yol] + params))
        except Exception as e:
            raise KaynakHatasi(f"Geri yükleme başarısız: {str(e)[:400]}") from e
        self._ilerleme(f"Geri yükleme tamam ({time.time() - t0:.0f} sn).")

    def kapat(self):
        super().kapat()
        _ACIK.discard(self.gecici_db)
        if self.isi_bitince_sil:
            try:
                self._sorgu_m(f"ALTER DATABASE [{self.gecici_db}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
                self._sorgu_m(f"DROP DATABASE [{self.gecici_db}]")
            except Exception:
                pass
            if getattr(self, "_kopya", None):
                try:
                    os.remove(self._kopya)
                except OSError:
                    pass
        try:
            self.master.close()
        except Exception:
            pass
