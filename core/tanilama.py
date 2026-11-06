"""
Kalıcı işlem günlüğü ve tanılama paketi.

Günlük: <çalışma klasörü>/gunluk.txt (2 MB'ı geçince eskisi gunluk.1.txt olur).
Tanılama paketi: sorun bildirmek için tek zip — müşteri verisi İÇERMEZ:
  - günlük (firma adları "<firma adı>" ile değiştirilmiş)
  - sürüm, işletim sistemi, SQL Server / Docker durumu (şifresiz)
  - yapay zekâ ayarı (anahtarsız), Claude Desktop bağlantı durumu
  - analizlerin sadece meta bilgisi (firma no, dönem, tarih aralığı, satır sayıları)
  - Windows'ta SQL Server kurulum özet günlüğünün son kısmı
"""
from __future__ import annotations

import datetime as dt
import glob
import io
import json
import os
import platform
import re
import sqlite3
import sys
import threading
import zipfile

from . import __version__, kurulum

_KILIT = threading.Lock()
SINIR = 2 * 1024 * 1024


def gunluk_yolu():
    return os.path.join(kurulum.calisma_klasoru(), "gunluk.txt")


def yaz(mesaj: str):
    try:
        with _KILIT:
            yol = gunluk_yolu()
            if os.path.exists(yol) and os.path.getsize(yol) > SINIR:
                os.replace(yol, yol.replace(".txt", ".1.txt"))
            with open(yol, "a", encoding="utf-8") as fh:
                for satir in str(mesaj).splitlines() or [""]:
                    fh.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {satir}\n")
    except OSError:
        pass  # günlük yazılamazsa uygulama durmasın


def _temizle(metin: str, adlar: list[str]) -> str:
    metin = re.sub(r"(Firma \d{3}) [^:\n]+:", r"\1 <firma adı>:", metin)
    for ad in sorted({a for a in adlar if a and len(a) >= 3}, key=len, reverse=True):
        metin = metin.replace(ad, "<firma adı>")
    # e-posta ve kullanıcı klasörü adları
    metin = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<e-posta>", metin)
    metin = re.sub(r"(?i)(\\Users\\|/home/|/Users/)[^\\/\s]+", r"\1<kullanıcı>", metin)
    return metin


def _analiz_ozetleri():
    k = kurulum.calisma_klasoru()
    out, adlar = [], []
    for b in sorted(glob.glob(os.path.join(k, "*", "bilgi.json"))):
        try:
            with open(b, encoding="utf-8") as fh:
                bilgi = json.load(fh)
        except (OSError, ValueError):
            continue
        adlar.append(bilgi.get("firma_ad", ""))
        satir = {"id": os.path.basename(os.path.dirname(b)), "firma": bilgi.get("firma"), "donemler": bilgi.get("donemler"),
                 "tarih": [bilgi.get("tarih_bas"), bilgi.get("tarih_bit")], "olusturma": bilgi.get("olusturma")}
        depo = os.path.join(os.path.dirname(b), "analiz.sqlite")
        try:
            con = sqlite3.connect(f"file:{depo}?mode=ro", uri=True)
            satir["satirlar"] = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                                 for t in ("fact_satis", "fact_alis", "dim_cari", "dim_malzeme", "stok_bakiye")}
            satir["meta"] = {k_: v for k_, v in con.execute("SELECT anahtar, deger FROM meta") if k_ != "firma_ad"}
            con.close()
        except sqlite3.Error as e:
            satir["hata"] = str(e)
        out.append(satir)
    return out, adlar


def paket(hedef: str, ek_log: list[str] | None = None) -> str:
    analizler, adlar = _analiz_ozetleri()
    try:
        sql = kurulum.durum()
    except Exception as e:
        sql = {"hata": str(e)}
    try:
        from . import mcp_sunucu, yz
        yz_ayar = {k: v for k, v in yz.ayar().items() if k != "anahtar"}
        yz_ayar["anahtar_var"] = bool(yz.ayar().get("anahtar"))
        claude = mcp_sunucu.claude_durumu()
    except Exception as e:
        yz_ayar, claude = {"hata": str(e)}, {}
    sistem = {
        "surum": __version__, "olusturma": dt.datetime.now().isoformat(timespec="seconds"),
        "isletim_sistemi": platform.platform(), "makine": platform.machine(), "python": sys.version.split()[0],
        "paketlenmis": bool(getattr(sys, "frozen", False)), "sql": sql, "yz": yz_ayar, "claude_desktop": claude,
        "analizler": analizler,
    }
    with zipfile.ZipFile(hedef, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("sistem.json", _temizle(json.dumps(sistem, ensure_ascii=False, indent=1, default=str), adlar))
        for yol in (gunluk_yolu().replace(".txt", ".1.txt"), gunluk_yolu()):
            if os.path.exists(yol):
                with open(yol, encoding="utf-8", errors="replace") as fh:
                    z.writestr(os.path.basename(yol), _temizle(fh.read(), adlar))
        if ek_log:
            z.writestr("son_islem.txt", _temizle("\n".join(ek_log), adlar))
        if os.name == "nt":
            for ozet in glob.glob(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                                               "Microsoft SQL Server", "*", "Setup Bootstrap", "Log", "Summary.txt")):
                with open(ozet, encoding="utf-8", errors="replace") as fh:
                    z.writestr("sql_kurulum_ozeti.txt", _temizle(fh.read()[-20000:], adlar))
        z.writestr("OKUBENI.txt", "Bu paket sorun bildirmek içindir. Müşteri verisi (cari, ürün, tutar) içermez; "
                                  "firma adları ve kullanıcı adları gizlenmiştir.\n")
    return hedef
