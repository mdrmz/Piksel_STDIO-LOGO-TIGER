"""
Gerçek entegrasyon testi (GitHub Actions: windows-latest ve ubuntu-latest + Docker).

1. Programın kendi kurulum kodu ile SQL Server'ı hazırlar (Windows: Express kurulumu, Linux: Docker konteyneri).
2. Demo verisinden Logo yapısında bir veritabanı oluşturur (varchar + Turkish_CI_AS, Logo'daki gibi).
3. BACKUP DATABASE ile gerçek bir .bak dosyası alır, veritabanını siler.
4. Uygulamanın arayüz akışıyla .bak'ı "programın SQL Server'ı" seçeneğiyle açar, firma 001'i aktarır.
5. Sonuçları SQLite demo üzerinden yapılan aynı analizle karşılaştırır; geçici veritabanının silindiğini doğrular.

Çalıştırma: python demo/demo_db_olustur.py demo/logo_demo.sqlite && python tests/entegrasyon.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import time

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)
os.environ.setdefault("LOGO_ANALIZ_KLASOR", os.path.join(tempfile.gettempdir(), "LogoAnalizTest"))

import pymssql  # noqa: E402

from core import kurulum  # noqa: E402
from core.analiz import Analiz  # noqa: E402
from core.kaynak import SqliteKaynak  # noqa: E402
from core.logo import aktar, firmalari_tara  # noqa: E402

DEMO = os.path.join(KOK, "demo", "logo_demo.sqlite")
TIP = {"INTEGER": "int", "REAL": "float", "TEXT": "varchar(250)"}


def adim(m):
    print(f"\n=== {m}", flush=True)


def baglan(sql, db="master", autocommit=True):
    return pymssql.connect(server=sql["sunucu"], port=sql["port"], user=sql["kullanici"], password=sql["sifre"],
                           database=db, autocommit=autocommit, charset="UTF-8", login_timeout=30)


def logo_veritabani_olustur(sql, ad="LOGOTEST"):
    con = baglan(sql)
    cur = con.cursor()
    cur.execute(f"IF DB_ID('{ad}') IS NOT NULL DROP DATABASE [{ad}]")
    cur.execute(f"CREATE DATABASE [{ad}] COLLATE Turkish_CI_AS")
    con.close()
    con = baglan(sql, ad, autocommit=False)
    cur = con.cursor()
    src = sqlite3.connect(DEMO)
    for (tablo,) in src.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        kolonlar = [(r[1], r[2]) for r in src.execute(f"PRAGMA table_info({tablo})")]
        tanim = ", ".join(f"[{k}] {'datetime' if k == 'DATE_' else TIP.get(t.upper(), 'varchar(250)')}"
                          f"{' NOT NULL PRIMARY KEY' if k == 'LOGICALREF' else ''}" for k, t in kolonlar)
        cur.execute(f"CREATE TABLE [{tablo}] ({tanim})")
        satirlar = src.execute(f"SELECT * FROM {tablo}").fetchall()
        tek = "(" + ",".join(["%s"] * len(kolonlar)) + ")"
        for i in range(0, len(satirlar), 400):
            parca = satirlar[i:i + 400]
            cur.execute(f"INSERT INTO [{tablo}] VALUES " + ",".join([tek] * len(parca)),
                        tuple(v for r in parca for v in r))
        con.commit()
        print(f"  {tablo}: {len(satirlar)} satır", flush=True)
    con.close()


def main():
    t0 = time.time()
    adim("1) Programın SQL Server'ı hazırlanıyor (gerekirse kurulur)")
    sql = kurulum.hazirla(print)
    print("  ayar:", {k: v for k, v in sql.items() if k != "sifre"})
    print(f"  süre: {time.time() - t0:.0f} sn")

    adim("2) Kayıtlı ayarla ikinci kez hazırlama (kurulum tekrarlanmamalı)")
    t1 = time.time()
    sql2 = kurulum.hazirla(print)
    assert sql2["port"] == sql["port"] and time.time() - t1 < 60, "ikinci hazırlık hızlı olmalı"

    adim("3) Logo yapısında test veritabanı ve .bak yedeği")
    logo_veritabani_olustur(sql)
    yerel_bak = os.path.join(sql["yedek_klasoru"], "LOGOTEST_kaynak.bak")
    sk = sql.get("sunucu_yedek_klasoru") or sql["yedek_klasoru"]
    sunucu_tarafi = sk.rstrip("/\\") + ("/" if sk.startswith("/") else "\\") + "LOGOTEST_kaynak.bak"
    con = baglan(sql)
    cur = con.cursor()
    cur.execute("BACKUP DATABASE [LOGOTEST] TO DISK = %s WITH INIT, FORMAT", (sunucu_tarafi,))
    while cur.nextset():
        pass
    cur.execute("DROP DATABASE [LOGOTEST]")
    con.close()
    if sql["tur"] == "docker":  # konteynerin yazdığı dosyayı kullanıcının okuyabileceği hale getir
        import subprocess
        subprocess.run(["docker", "exec", "-u", "0", kurulum.KONTEYNER, "chmod", "644",
                        "/var/opt/mssql/backup/LOGOTEST_kaynak.bak"], check=True)
    sunucu_bak = yerel_bak
    kullanici_bak = os.path.join(tempfile.gettempdir(), "Masaustu", "TIGER_TEST.bak")
    os.makedirs(os.path.dirname(kullanici_bak), exist_ok=True)
    shutil.move(sunucu_bak, kullanici_bak)
    print(f"  yedek: {kullanici_bak} ({os.path.getsize(kullanici_bak) / 1024 ** 2:.1f} MB)")

    adim("4) Uygulama akışı: .bak → programın SQL Server'ı → firma 001 aktarımı")
    import app as A
    c = A.app.test_client()

    def bekle():
        for _ in range(1200):
            d = c.get("/durum").json
            if not d["calisiyor"]:
                print("\n".join("  " + x for x in d["log"]), flush=True)
                return d
            time.sleep(0.5)
        raise TimeoutError

    assert c.get("/").status_code == 200
    c.post("/kaynak", data={"tur": "bak", "bak_yol": kullanici_bak, "sql_kaynak": "otomatik"})
    d = bekle()
    assert d["hata"] is None, d["hata"]
    assert d["sonraki"] == "/firmalar"
    c.post("/aktar", data={"firma": "001", "donem_001": ["01", "02"], "sadece_faturali": "1"})
    d = bekle()
    assert d["hata"] is None, d["hata"]
    aid = d["sonraki"].rsplit("/", 1)[-1]
    assert c.get(d["sonraki"]).status_code == 200
    assert c.get(f"/indir/{aid}/excel").status_code == 200
    assert os.path.exists(kullanici_bak), "kullanıcının .bak dosyası silinmemeli"

    adim("5) Sonuç karşılaştırma (SQL Server'dan geri yüklenen vs. SQLite demo)")
    a_sql = Analiz(os.path.join(A.CALISMA, aid, "analiz.sqlite"))
    ref = os.path.join(tempfile.gettempdir(), "ref.sqlite")
    k = SqliteKaynak(DEMO)
    f = next(f for f in firmalari_tara(k) if f.no == "001")
    aktar(k, f, f.donemler, ref)
    a_ref = Analiz(ref)
    o1, o2 = a_sql.ozet(), a_ref.ozet()
    for anahtar in ("net_satis", "net_alis", "iade_tutar", "fatura_sayisi", "musteri_sayisi", "urun_sayisi"):
        print(f"  {anahtar:16} SQL Server: {o1[anahtar]:>16,.2f}   demo: {o2[anahtar]:>16,.2f}")
        assert abs(o1[anahtar] - o2[anahtar]) < 0.05, anahtar
    # v0.4: maliyet, cari hareket, temsilci ve ambar tabloları da SQL Server'dan aynı gelmeli
    assert abs(o1["brut_kar"] - o2["brut_kar"]) < 0.05, "brut_kar"
    c1, c2 = a_sql.cari_ozet(), a_ref.cari_ozet()
    for anahtar in ("alacak", "borc", "gecikmis"):
        print(f"  {anahtar:16} SQL Server: {c1[anahtar]:>16,.2f}   demo: {c2[anahtar]:>16,.2f}")
        assert abs(c1[anahtar] - c2[anahtar]) < 0.05, anahtar
    t1 = a_sql.temsilci_performans()
    assert t1["temsilci_ad"].tolist() == a_ref.temsilci_performans()["temsilci_ad"].tolist()
    assert "ŞAHİN" not in " ".join(t1["temsilci_ad"]) and "ÇELİK" in " ".join(t1["temsilci_ad"]), "temsilci adları bozuk"
    assert a_sql.depo_kirilim()["ambar_ad"].tolist() == ["MERKEZ DEPO", "ŞUBE DEPO"]
    assert c.get(f"/indir/{aid}/pdf").status_code == 200
    u1, u2 = a_sql.urun_abc(), a_ref.urun_abc()
    assert u1["sinif"].value_counts().to_dict() == u2["sinif"].value_counts().to_dict()
    m1 = a_sql.musteri_abc()
    assert "MÜŞTERİ" in m1.iloc[0]["cari_ad"], f"Türkçe karakterler bozuk: {m1.iloc[0]['cari_ad']!r}"
    print("  ilk müşteri:", m1.iloc[0]["cari_ad"])

    adim("6) Geçici veritabanı ve kopya dosya temizlendi mi")
    con = baglan(sql)
    cur = con.cursor()
    cur.execute("SELECT name FROM sys.databases WHERE name LIKE 'LOGOANALIZ[_]%'")
    kalan = [r[0] for r in cur.fetchall()]
    con.close()
    assert not kalan, f"geçici veritabanı silinmemiş: {kalan}"
    artik = [x for x in os.listdir(sql["yedek_klasoru"]) if x.startswith("LOGOANALIZ_")]
    assert not artik, f"kopya .bak silinmemiş: {artik}"

    print(f"\nTÜM ADIMLAR BAŞARILI ({time.time() - t0:.0f} sn)")


if __name__ == "__main__":
    main()
