"""
Logo Tiger tablo yapısını taklit eden demo SQLite veritabanı üretir.

Gerçek Logo'daki isimlendirme ve kolonlar kullanılır:
  LG_{firma}_CLCARD, LG_{firma}_ITEMS              (firma bazlı kartlar)
  LG_{firma}_{donem}_INVOICE, LG_{firma}_{donem}_STLINE  (dönem bazlı hareketler)

İçine bilerek "tuzaklar" konur (iptal fatura, indirim satırı, faturalanmamış irsaliye,
iade, dönem başı devir fişi) — analiz katmanının bunları doğru ayıkladığını test etmek için.

Kullanım:  python demo/demo_db_olustur.py [cikti.sqlite]
"""
import random
from collections import defaultdict
import sqlite3
import sys
from datetime import date, timedelta

random.seed(42)

SEHIRLER = ["GAZİANTEP", "İSTANBUL", "ANKARA", "İZMİR", "ADANA", "KONYA", "BURSA", "KAYSERİ", "MERSİN", "ŞANLIURFA"]
GRUPLAR = ["HIRDAVAT", "ELEKTRİK", "BOYA", "SIHHİ TESİSAT", "İNŞAAT MLZ", "AMBALAJ"]
URUN_KOK = ["Vida", "Dübel", "Kablo", "Priz", "Boya", "Astar", "Boru", "Vana", "Çimento", "Alçı",
            "Koli", "Streç Film", "Silikon", "Matkap Ucu", "Sigorta", "Anahtar", "Conta", "Dirsek"]

CLCARD_DDL = """CREATE TABLE {t} (
  LOGICALREF INTEGER PRIMARY KEY, ACTIVE INTEGER, CARDTYPE INTEGER, CODE TEXT, DEFINITION_ TEXT,
  SPECODE TEXT, CYPHCODE TEXT, CITY TEXT, TOWN TEXT, COUNTRY TEXT, TAXNR TEXT)"""
ITEMS_DDL = """CREATE TABLE {t} (
  LOGICALREF INTEGER PRIMARY KEY, ACTIVE INTEGER, CARDTYPE INTEGER, CODE TEXT, NAME TEXT,
  STGRPCODE TEXT, SPECODE TEXT, PRODUCERCODE TEXT)"""
INVOICE_DDL = """CREATE TABLE {t} (
  LOGICALREF INTEGER PRIMARY KEY, GRPCODE INTEGER, TRCODE INTEGER, FICHENO TEXT, DATE_ TEXT,
  CLIENTREF INTEGER, SOURCEINDEX INTEGER, CANCELLED INTEGER, GROSSTOTAL REAL, TOTALDISCOUNTS REAL,
  TOTALVAT REAL, NETTOTAL REAL, SALESMANREF INTEGER)"""
STLINE_DDL = """CREATE TABLE {t} (
  LOGICALREF INTEGER PRIMARY KEY, STOCKREF INTEGER, LINETYPE INTEGER, TRCODE INTEGER, DATE_ TEXT,
  IOCODE INTEGER, STFICHEREF INTEGER, INVOICEREF INTEGER, CLIENTREF INTEGER, SOURCEINDEX INTEGER,
  AMOUNT REAL, PRICE REAL, TOTAL REAL, DISTDISC REAL, VATMATRAH REAL, VAT REAL, VATAMNT REAL,
  LINENET REAL, UINFO1 REAL, UINFO2 REAL, CANCELLED INTEGER, SALESMANREF INTEGER)"""


def gun_listesi(bas, bit):
    d = bas
    while d <= bit:
        if d.weekday() < 6:  # pazar hariç
            yield d
        d += timedelta(days=1)


def firma_olustur(con, firma, donemler, n_musteri, n_tedarikci, n_urun, fatura_gunluk):
    f = f"{firma:03d}"
    con.execute(CLCARD_DDL.format(t=f"LG_{f}_CLCARD"))
    con.execute(ITEMS_DDL.format(t=f"LG_{f}_ITEMS"))

    # ---- Cari kartlar
    cariler = []
    ref = 1
    for i in range(n_musteri):
        cariler.append((ref, 0, 1, f"120.{i+1:04d}", f"MÜŞTERİ {i+1:03d} TİC. LTD. ŞTİ.",
                        random.choice(["BAYİ", "PERAKENDE", "PROJE"]), "", random.choice(SEHIRLER), "", "TÜRKİYE", ""))
        ref += 1
    for i in range(n_tedarikci):
        cariler.append((ref, 0, 2, f"320.{i+1:04d}", f"TEDARİKÇİ {i+1:02d} A.Ş.",
                        "TEDARİK", "", random.choice(SEHIRLER), "", "TÜRKİYE", ""))
        ref += 1
    con.executemany(f"INSERT INTO LG_{f}_CLCARD VALUES (?,?,?,?,?,?,?,?,?,?,?)", cariler)
    musteri_refs = [c[0] for c in cariler if c[2] == 1]
    tedarikci_refs = [c[0] for c in cariler if c[2] == 2]

    # ---- Malzeme kartları (fiyat ve popülerlik Pareto dağılımlı)
    urunler = []
    for i in range(n_urun):
        grp = random.choice(GRUPLAR)
        ad = f"{random.choice(URUN_KOK)} {random.choice(['Standart', 'Pro', 'Eko', 'Plus', 'Max'])} {random.randint(1, 99)}"
        urunler.append((i + 1, 0, 1, f"M.{i+1:05d}", ad, grp, "", ""))
    con.executemany(f"INSERT INTO LG_{f}_ITEMS VALUES (?,?,?,?,?,?,?,?)", urunler)
    fiyat = {u[0]: round(random.lognormvariate(3.5, 0.9), 2) for u in urunler}
    pop = {u[0]: random.paretovariate(1.2) for u in urunler}
    musteri_pop = {m: random.paretovariate(1.1) for m in musteri_refs}
    # son %8 ürün "ölü stok" olacak: sadece ilk dönemin başında alınır, sonra hiç satılmaz
    olu_urunler = set(u[0] for u in urunler[-max(1, n_urun * 8 // 100):])
    satilan_urunler = [u[0] for u in urunler if u[0] not in olu_urunler]

    stok = {u[0]: 0.0 for u in urunler}
    max_pop = max(pop.values())
    urun_tedarikci = {u[0]: random.choice(tedarikci_refs) for u in urunler}
    son_satis = defaultdict(list)   # ürün -> haftalık satış miktarları
    hafta = defaultdict(float)      # bu haftanın satışı
    fis_no = 1

    for pi, (donem, bas, bit) in enumerate(donemler):
        p = f"{donem:02d}"
        tinv, tst = f"LG_{f}_{p}_INVOICE", f"LG_{f}_{p}_STLINE"
        con.execute(INVOICE_DDL.format(t=tinv))
        con.execute(STLINE_DDL.format(t=tst))
        inv_ref, st_ref = 1, 1
        inv_rows, st_rows = [], []

        def satir(stockref, linetype, trcode, d, iocode, invref, clref, amount, price, distdisc=0.0,
                  cancelled=0, u1=1.0, u2=1.0):
            nonlocal st_ref
            total = round(amount * price, 2)
            matrah = round(total - distdisc, 2)
            vatamnt = round(matrah * 0.20, 2)
            st_rows.append((st_ref, stockref, linetype, trcode, f"{d.isoformat()} 00:00:00", iocode, 0, invref,
                            clref, 0, amount, price, total, distdisc, matrah, 20.0, vatamnt, matrah, u1, u2,
                            cancelled, 0))
            st_ref += 1

        # Dönem başı devir fişi (TRCODE 14) — önceki dönemden kalan stok
        if pi > 0:
            for sref, miktar in stok.items():
                if miktar > 0:
                    satir(sref, 0, 14, bas, 1, 0, 0, miktar, fiyat[sref] * 0.7)

        # İlk dönemin başında ölü stok ürünlerinin alımı
        if pi == 0:
            for sref in olu_urunler:
                ted = random.choice(tedarikci_refs)
                d = bas + timedelta(days=random.randint(0, 20))
                miktar = random.randint(20, 200)
                inv_rows.append([inv_ref, 1, 1, f"ALF{fis_no:07d}", f"{d.isoformat()} 00:00:00", ted, 0, 0, 0, 0, 0, 0, 0])
                satir(sref, 0, 1, d, 1, inv_ref, ted, miktar, round(fiyat[sref] * 0.7, 2))
                stok[sref] += miktar
                inv_ref += 1
                fis_no += 1

        for d in gun_listesi(bas, bit):
            mevsim = 1.0 + 0.35 * (d.month in (4, 5, 6, 9, 10)) - 0.25 * (d.month in (1, 2))
            # ---- Alışlar: her pazartesi, stoğu son 4 haftalık satışın altına düşen ürünler için sipariş
            if d.weekday() == 0:
                for sref in satilan_urunler:
                    son_satis[sref].append(hafta[sref])
                    hafta[sref] = 0.0
                siparis = defaultdict(list)
                for sref in satilan_urunler:
                    talep = sum(son_satis[sref][-4:])
                    hedef = max(talep * 1.5, 30 * pop[sref] / max_pop * 50 + 10)
                    if stok[sref] < hedef * 0.5:
                        siparis[urun_tedarikci[sref]].append((sref, int(hedef - stok[sref]) + 1))
                for ted, kalemler_ in siparis.items():
                    inv_rows.append([inv_ref, 1, 1, f"ALF{fis_no:07d}", f"{d.isoformat()} 00:00:00", ted, 0, 0, 0, 0, 0, 0, 0])
                    for sref, miktar in kalemler_:
                        satir(sref, 0, 1, d, 1, inv_ref, ted, miktar, round(fiyat[sref] * random.uniform(0.62, 0.75), 2))
                        stok[sref] += miktar
                    inv_ref += 1
                    fis_no += 1
            # ---- Satışlar
            n_fatura = max(1, int(random.gauss(fatura_gunluk * mevsim, 2)))
            for _ in range(n_fatura):
                mus = random.choices(musteri_refs, weights=[musteri_pop[m] for m in musteri_refs])[0]
                iptal = 1 if random.random() < 0.02 else 0             # tuzak: iptal fatura
                faturasiz = random.random() < 0.03                     # tuzak: faturalanmamış irsaliye
                trcode = 8 if random.random() < 0.85 else 7
                invref = 0 if faturasiz else inv_ref
                if not faturasiz:
                    inv_rows.append([inv_ref, 2, trcode, f"STF{fis_no:07d}", f"{d.isoformat()} 00:00:00", mus, 0, iptal, 0, 0, 0, 0, 0])
                kalemler = random.choices(satilan_urunler, weights=[pop[s] for s in satilan_urunler], k=random.randint(1, 6))
                ara_toplam = 0.0
                for sref in set(kalemler):
                    koli = random.random() < 0.15                       # tuzak: koli biriminde satış (1 koli = 12 adet)
                    miktar = random.randint(1, 5) if koli else random.randint(1, 40)
                    birim_fiyat = round(fiyat[sref] * (12 if koli else 1) * random.uniform(0.95, 1.1), 2)
                    disc = round(miktar * birim_fiyat * random.choice([0, 0, 0, 0.05, 0.10]), 2)
                    satir(sref, 0, trcode, d, 4, invref, mus, miktar, birim_fiyat, distdisc=disc,
                          cancelled=iptal, u1=1.0, u2=12.0 if koli else 1.0)
                    ara_toplam += miktar * birim_fiyat - disc
                    if not iptal:
                        stok[sref] -= miktar * (12 if koli else 1)
                        hafta[sref] += miktar * (12 if koli else 1)
                # tuzak: dip indirim satırı (LINETYPE=2) — ciroya ikinci kez sayılmamalı
                if random.random() < 0.10 and not faturasiz:
                    satir(0, 2, trcode, d, 0, invref, mus, 0, 0, cancelled=iptal)
                    st_rows[-1] = st_rows[-1][:12] + (round(ara_toplam * 0.03, 2),) + st_rows[-1][13:]
                if not faturasiz:
                    inv_ref += 1
                fis_no += 1
                # tuzak: satış iadesi (TRCODE 3)
                if random.random() < 0.03 and not iptal:
                    sref = random.choice(kalemler)
                    miktar = random.randint(1, 5)
                    inv_rows.append([inv_ref, 2, 3, f"SIF{fis_no:07d}", f"{d.isoformat()} 00:00:00", mus, 0, 0, 0, 0, 0, 0, 0])
                    satir(sref, 0, 3, d, 1, inv_ref, mus, miktar, fiyat[sref])
                    stok[sref] += miktar
                    inv_ref += 1
                    fis_no += 1

        # Fatura başlık toplamlarını satırlardan doldur
        toplamlar = {}
        for r in st_rows:
            if r[7]:
                t = toplamlar.setdefault(r[7], [0.0, 0.0, 0.0])
                t[0] += r[12]; t[1] += r[14]; t[2] += r[16]
        for row in inv_rows:
            g, m, v = toplamlar.get(row[0], [0, 0, 0])
            row[8], row[9], row[10], row[11] = round(g, 2), round(g - m, 2), round(v, 2), round(m + v, 2)

        con.executemany(f"INSERT INTO {tinv} VALUES ({','.join('?' * 13)})", inv_rows)
        con.executemany(f"INSERT INTO {tst} VALUES ({','.join('?' * 22)})", st_rows)
        print(f"  {tinv}: {len(inv_rows):>6} fatura   {tst}: {len(st_rows):>7} satır")


def main(cikti="demo/logo_demo.sqlite"):
    import os
    if os.path.exists(cikti):
        os.remove(cikti)
    con = sqlite3.connect(cikti)
    print("Firma 001 (2 dönem):")
    firma_olustur(con, 1, [(1, date(2025, 1, 1), date(2025, 12, 31)), (2, date(2026, 1, 1), date(2026, 10, 31))],
                  n_musteri=150, n_tedarikci=25, n_urun=400, fatura_gunluk=20)
    print("Firma 002 (1 dönem):")
    firma_olustur(con, 2, [(1, date(2026, 1, 1), date(2026, 10, 31))],
                  n_musteri=40, n_tedarikci=8, n_urun=80, fatura_gunluk=5)
    # Logo'nun sistem tablosu (normalde ayrı LOGODB'de durur; burada aynı veritabanına koyuyoruz)
    con.execute("CREATE TABLE L_CAPIFIRM (NR INTEGER, NAME TEXT, TITLE TEXT)")
    con.executemany("INSERT INTO L_CAPIFIRM VALUES (?,?,?)",
                    [(1, "DEMO YAPI MARKET", "DEMO YAPI MARKET SAN. VE TİC. A.Ş."), (2, "DEMO ŞUBE", "DEMO ŞUBE LTD. ŞTİ.")])
    con.execute("CREATE TABLE L_CAPIPERIOD (FIRMNR INTEGER, NR INTEGER, BEGDATE TEXT, ENDDATE TEXT)")
    con.executemany("INSERT INTO L_CAPIPERIOD VALUES (?,?,?,?)",
                    [(1, 1, "2025-01-01", "2025-12-31"), (1, 2, "2026-01-01", "2026-12-31"), (2, 1, "2026-01-01", "2026-12-31")])
    con.commit()
    con.close()
    print(f"Hazır: {cikti}")


if __name__ == "__main__":
    main(*sys.argv[1:])
