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
"""
from __future__ import annotations

import os
import re
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
                 sunucu_yedek_klasoru: str | None = None, isi_bitince_sil: bool = True, ilerleme=None):
        """
        sunucudaki_yol: SQL Server dosyayı farklı bir yoldan görüyorsa (ör. Docker'da /var/opt/mssql/backup/x.bak)
                        o yol. Verilmezse bak_yolu (veya kopyalandığı yer) kullanılır.
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
        yol = sunucudaki_yol or self._dosyayi_hazirla(bak_yolu, yedek_klasoru, sunucu_yedek_klasoru)
        self._geri_yukle(yol)
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

    def _dosyayi_hazirla(self, bak_yolu, yedek_klasoru, sunucu_yedek_klasoru):
        """SQL Server servisi .bak'ı kendi hesabıyla okur; dosyayı onun erişebildiği bir klasöre kopyalar.
        Dönen değer: SQL Server'ın dosyayı gördüğü yol."""
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
        for yerel, sunucu_tarafi in adaylar:
            if not os.path.isdir(yerel):
                continue
            hedef = os.path.join(yerel, ad)
            try:
                self._ilerleme("Yedek dosyası SQL Server'ın okuyabileceği klasöre kopyalanıyor…")
                shutil.copyfile(bak_yolu, hedef)
            except OSError:
                continue  # yazma izni yok; sıradaki klasörü dene
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
