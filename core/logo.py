"""
Logo Tiger veri modeli: firma/dönem tarama ve ham tablolardan temiz analiz modeline aktarım.

Logo isimlendirmesi:
  LG_{FFF}_{TABLO}         firma bazlı kartlar      (CLCARD = cari, ITEMS = malzeme)
  LG_{FFF}_{PP}_{TABLO}    dönem bazlı hareketler   (INVOICE = fatura başlığı, STLINE = stok/fatura satırı)

Ham verideki tuzaklar ve burada nasıl ele alındıkları:
  * STLINE.CANCELLED=1 veya bağlı faturası iptal      -> dışlanır
  * LINETYPE != 0 (indirim, masraf, promosyon satırı)  -> malzeme analizine girmez
      (dip indirimler Logo'da malzeme satırlarına DISTDISC olarak dağıtılır; VATMATRAH bunu içerir)
  * Faturalanmamış irsaliye (INVOICEREF=0)            -> varsayılan olarak satışa sayılmaz (ayarlanabilir)
  * İadeler (TRCODE 2,3 satış iade / 6 alış iade)     -> eksi olarak düşülür
  * Satır birimi ≠ ana birim (koli, paket…)           -> miktar ana birime çevrilir: AMOUNT * UINFO2 / UINFO1
  * Dönem başı devir fişi (TRCODE 14)                 -> stok bakiyesinde sadece en son dönemden alınır
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# STLINE.TRCODE anlamları (malzeme hareketleri)
TRCODE_AD = {
    1: "Satınalma", 2: "Perakende satış iade", 3: "Toptan satış iade", 4: "Konsinye çıkış iade",
    5: "Konsinye giriş", 6: "Satınalma iade", 7: "Perakende satış", 8: "Toptan satış",
    9: "Konsinye çıkış", 10: "Konsinye giriş iade", 11: "Fire", 12: "Sarf", 13: "Üretimden giriş",
    14: "Devir", 25: "Ambar transferi", 26: "Mustahsil", 50: "Sayım fazlası", 51: "Sayım eksiği",
}
SATIS = {7: 1, 8: 1, 2: -1, 3: -1}
ALIS = {1: 1, 6: -1}

_DONEM_RE = re.compile(r"^LG_(\d{3})_(\d{2})_([A-Z0-9]+)$")
_FIRMA_RE = re.compile(r"^LG_(\d{3})_([A-Z0-9]+)$")


@dataclass
class Firma:
    no: str
    ad: str = ""
    donemler: list = field(default_factory=list)
    kart_tablolari: set = field(default_factory=set)
    donem_tablolari: dict = field(default_factory=dict)  # donem -> set(tablo)

    @property
    def analiz_edilebilir(self):
        return {"CLCARD", "ITEMS"} <= self.kart_tablolari and any(
            "STLINE" in t for t in self.donem_tablolari.values())


def firmalari_tara(kaynak) -> list[Firma]:
    firmalar: dict[str, Firma] = {}
    tablolar = [t.upper() for t in kaynak.tablolar()]
    for t in tablolar:
        m = _DONEM_RE.match(t)
        if m:
            f = firmalar.setdefault(m[1], Firma(m[1]))
            f.donem_tablolari.setdefault(m[2], set()).add(m[3])
            continue
        m = _FIRMA_RE.match(t)
        if m:
            firmalar.setdefault(m[1], Firma(m[1])).kart_tablolari.add(m[2])
    # Firma adları (L_CAPIFIRM aynı veritabanındaysa)
    if "L_CAPIFIRM" in tablolar:
        try:
            df = kaynak.tablo_oku("L_CAPIFIRM", ["NR", "NAME"])
            for _, r in df.iterrows():
                no = f"{int(r['NR']):03d}"
                if no in firmalar:
                    firmalar[no].ad = str(r["NAME"] or "").strip()
        except Exception:
            pass
    for f in firmalar.values():
        f.donemler = sorted(p for p, ts in f.donem_tablolari.items() if "STLINE" in ts)
    return sorted((f for f in firmalar.values() if f.analiz_edilebilir), key=lambda f: f.no)


def _oku(kaynak, tablo, istenen: dict):
    """istenen: kolon -> varsayılan. Logo sürümleri arasında olmayan kolonlar varsayılanla doldurulur."""
    mevcut = set(kaynak.kolonlar(tablo))
    var = [k for k in istenen if k in mevcut]
    df = kaynak.tablo_oku(tablo, var)
    df.columns = [c.upper() for c in df.columns]
    for k, v in istenen.items():
        if k not in df.columns:
            df[k] = v
    return df[list(istenen)]


@dataclass
class AktarimAyari:
    sadece_faturali: bool = True     # faturalanmamış irsaliyeleri satışa sayma
    hizmetleri_dahil_et: bool = False  # LINETYPE=4 hizmet satırları


def aktar(kaynak, firma: Firma, donemler: list[str], hedef_db: str, ayar: AktarimAyari | None = None,
          ilerleme=None) -> dict:
    """Ham Logo tablolarını okuyup temiz modeli hedef SQLite dosyasına yazar. Veri kalite özetini döner."""
    ayar = ayar or AktarimAyari()
    say = ilerleme or (lambda m: None)
    f = firma.no
    kalite = {"firma": f, "firma_ad": firma.ad, "donemler": ", ".join(donemler)}

    say("Cari kartlar okunuyor…")
    cari = _oku(kaynak, f"LG_{f}_CLCARD", {"LOGICALREF": 0, "CODE": "", "DEFINITION_": "", "CARDTYPE": 0,
                                         "CITY": "", "SPECODE": "", "CYPHCODE": "", "ACTIVE": 0})
    cari.columns = ["cari_ref", "cari_kod", "cari_ad", "cari_tur", "sehir", "ozel_kod", "yetki_kod", "pasif"]
    say("Malzeme kartları okunuyor…")
    mal = _oku(kaynak, f"LG_{f}_ITEMS", {"LOGICALREF": 0, "CODE": "", "NAME": "", "STGRPCODE": "",
                                       "SPECODE": "", "CARDTYPE": 0, "ACTIVE": 0})
    mal.columns = ["malzeme_ref", "malzeme_kod", "malzeme_ad", "grup_kod", "ozel_kod", "kart_tur", "pasif"]
    for d in (cari, mal):
        for c in d.columns:
            if d[c].dtype == object:
                d[c] = d[c].fillna("").astype(str).str.strip()

    satirlar = []
    for p in donemler:
        say(f"Dönem {p}: fatura başlıkları okunuyor…")
        inv = _oku(kaynak, f"LG_{f}_{p}_INVOICE", {"LOGICALREF": 0, "FICHENO": "", "CANCELLED": 0, "TRCODE": 0})
        inv.columns = ["invoiceref", "fatura_no", "fatura_iptal", "fatura_trcode"]
        say(f"Dönem {p}: stok hareket satırları okunuyor…")
        st = _oku(kaynak, f"LG_{f}_{p}_STLINE", {
            "LOGICALREF": 0, "STOCKREF": 0, "LINETYPE": 0, "TRCODE": 0, "IOCODE": 0, "DATE_": None,
            "INVOICEREF": 0, "CLIENTREF": 0, "SOURCEINDEX": 0, "AMOUNT": 0.0, "PRICE": 0.0, "TOTAL": 0.0,
            "DISTDISC": 0.0, "VATMATRAH": np.nan, "LINENET": np.nan, "UINFO1": 1.0, "UINFO2": 1.0,
            "CANCELLED": 0, "SALESMANREF": 0})
        st["donem"] = p
        st = st.merge(inv, how="left", left_on="INVOICEREF", right_on="invoiceref")
        satirlar.append(st)
        say(f"Dönem {p}: {len(st):,} satır okundu.".replace(",", "."))
    st = pd.concat(satirlar, ignore_index=True)
    kalite["ham_satir"] = int(len(st))

    # ---- temizlik
    st["tarih"] = pd.to_datetime(st["DATE_"], errors="coerce")
    for c in ["AMOUNT", "PRICE", "TOTAL", "DISTDISC", "VATMATRAH", "LINENET", "UINFO1", "UINFO2"]:
        st[c] = pd.to_numeric(st[c], errors="coerce")
    iptal = (st["CANCELLED"].fillna(0).astype(int) == 1) | (st["fatura_iptal"].fillna(0).astype(int) == 1)
    kalite["iptal_satir"] = int(iptal.sum())
    linetypes = [0, 4] if ayar.hizmetleri_dahil_et else [0]
    malzeme_disi = ~st["LINETYPE"].fillna(0).astype(int).isin(linetypes)
    kalite["malzeme_disi_satir"] = int((malzeme_disi & ~iptal).sum())
    st = st[~iptal & ~malzeme_disi].copy()

    u1 = st["UINFO1"].replace(0, np.nan).fillna(1.0)
    u2 = st["UINFO2"].replace(0, np.nan).fillna(1.0)
    st["miktar"] = st["AMOUNT"].fillna(0) * u2 / u1
    # net tutar: VATMATRAH (satır + dağıtılmış dip indirim/masraf sonrası KDV matrahı), yoksa LINENET, yoksa TOTAL-DISTDISC
    st["net_tutar"] = st["VATMATRAH"].where(st["VATMATRAH"].notna(),
                                            st["LINENET"].where(st["LINENET"].notna(),
                                                                st["TOTAL"].fillna(0) - st["DISTDISC"].fillna(0)))
    st["hareket"] = st["TRCODE"].map(TRCODE_AD).fillna("Diğer")
    st = st.rename(columns={"STOCKREF": "malzeme_ref", "CLIENTREF": "cari_ref", "TRCODE": "trcode",
                            "IOCODE": "iocode", "INVOICEREF": "invoiceref_", "SOURCEINDEX": "ambar",
                            "SALESMANREF": "satis_temsilci_ref", "LOGICALREF": "satir_ref"})
    st["faturali"] = st["invoiceref_"].fillna(0).astype(int) > 0

    hareket = st[["satir_ref", "donem", "tarih", "trcode", "hareket", "iocode", "malzeme_ref", "cari_ref",
                  "ambar", "faturali", "fatura_no", "miktar", "net_tutar", "satis_temsilci_ref"]].copy()

    # ---- satış ve alış
    def _fakt(harita, ad):
        d = hareket[hareket["trcode"].isin(harita)].copy()
        if ayar.sadece_faturali:
            kalite[f"{ad}_faturasiz_dislanan"] = int((~d["faturali"]).sum())
            d = d[d["faturali"]]
        isaret = d["trcode"].map(harita)
        d["miktar"] = d["miktar"] * isaret
        d["net_tutar"] = d["net_tutar"] * isaret
        d["iade"] = isaret < 0
        return d

    satis = _fakt(SATIS, "satis")
    alis = _fakt(ALIS, "alis")

    # ---- stok bakiyesi: en son seçilen dönemden (o dönemin devir fişi önceki bakiyeyi taşır)
    son = max(donemler)
    s = hareket[hareket["donem"] == son]
    yon = np.select([s["iocode"].isin([1, 2]), s["iocode"].isin([3, 4])], [1, -1], 0)
    stok = (s.assign(m=s["miktar"] * yon).groupby("malzeme_ref", as_index=False)["m"].sum()
            .rename(columns={"m": "stok_miktar"}))
    kalite["stok_donemi"] = son

    tarih_min, tarih_max = hareket["tarih"].min(), hareket["tarih"].max()
    gunler = pd.date_range(tarih_min.normalize(), tarih_max.normalize(), freq="D")
    AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    dim_tarih = pd.DataFrame({"tarih": gunler, "yil": gunler.year, "ay": gunler.month,
                              "ay_ad": [AYLAR[m - 1] for m in gunler.month], "ceyrek": gunler.quarter,
                              "yil_ay": gunler.strftime("%Y-%m")})

    kalite.update(satis_satir=int(len(satis)), alis_satir=int(len(alis)),
                  tarih_bas=str(tarih_min.date()), tarih_bit=str(tarih_max.date()))
    say("Analiz deposuna yazılıyor…")
    con = sqlite3.connect(hedef_db)
    for ad, df in {"dim_cari": cari, "dim_malzeme": mal, "dim_tarih": dim_tarih, "fact_hareket": hareket,
                   "fact_satis": satis, "fact_alis": alis, "stok_bakiye": stok}.items():
        df.to_sql(ad, con, if_exists="replace", index=False)
    meta = pd.DataFrame([{"anahtar": k, "deger": str(v)} for k, v in kalite.items()])
    meta.to_sql("meta", con, if_exists="replace", index=False)
    con.commit()
    con.close()
    say("Aktarım tamamlandı.")
    return kalite
