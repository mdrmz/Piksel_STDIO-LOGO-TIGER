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

Ek tablolar (yedekte varsa okunur, yoksa ilgili analiz "veri yok" der):
  LG_{FFF}_{PP}_CLFLINE    cari hareketler (borç/alacak)  -> cari bakiye ve yaşlandırma
  LG_SLSMAN                satış elemanları               -> temsilci performansı
  L_CAPIWHOUSE             ambar adları                   -> depo kırılımı

Maliyet: Logo'nun maliyetlendirme servisi her firmada çalıştırılmadığı için satış maliyeti ALIŞ FİYATLARINDAN
hesaplanır: her satış satırına, o malzemenin satış tarihinden önceki en son alış (yoksa devir/üretim girişi)
birim maliyeti atanır. Bu "son alış maliyeti" yöntemidir; muhasebe kârı değil, yönetim amaçlı tahmini brüt kârdır.
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
MALIYET_KAYNAGI = (1, 13, 14, 50)   # alış, üretimden giriş, devir, sayım fazlası: birim maliyet bu girişlerden alınır

# CLFLINE.TRCODE anlamları (cari hareketler)
CARI_TRCODE_AD = {
    1: "Nakit tahsilat", 2: "Nakit ödeme", 3: "Borç dekontu", 4: "Alacak dekontu", 5: "Virman", 6: "Kur farkı",
    12: "Özel işlem", 14: "Devir (açılış)", 20: "Gelen havale", 21: "Gönderilen havale",
    31: "Satınalma faturası", 32: "Perakende satış iade faturası", 33: "Toptan satış iade faturası",
    34: "Alınan hizmet faturası", 36: "Satınalma iade faturası", 37: "Perakende satış faturası",
    38: "Toptan satış faturası", 39: "Verilen hizmet faturası", 61: "Çek girişi", 62: "Senet girişi",
    63: "Çek çıkışı", 64: "Senet çıkışı", 70: "Kredi kartı fişi", 72: "Firma kredi kartı fişi",
}

_DONEM_RE = re.compile(r"^LG_(\d{3})_(\d{2})_([A-Z0-9]+)$")
_FIRMA_RE = re.compile(r"^LG_(\d{3})_([A-Z0-9]+)$")


@dataclass
class Firma:
    no: str
    ad: str = ""
    donemler: list = field(default_factory=list)
    kart_tablolari: set = field(default_factory=set)
    donem_tablolari: dict = field(default_factory=dict)  # donem -> set(tablo)
    donem_tarih: dict = field(default_factory=dict)      # donem -> "01.01.2025 – 31.12.2025" (L_CAPIPERIOD varsa)

    @property
    def analiz_edilebilir(self):
        return {"CLCARD", "ITEMS"} <= self.kart_tablolari and any(
            "STLINE" in t for t in self.donem_tablolari.values())


def _tarih(v):
    t = pd.to_datetime(v, errors="coerce")
    return "" if pd.isna(t) else f"{t:%d.%m.%Y}"


def firma_tanimlari(kaynak) -> dict[str, str]:
    """L_CAPIFIRM'deki (Logo sistem veritabanı) tüm firma tanımları: firma no -> ad. Tablo yoksa boş."""
    if "L_CAPIFIRM" not in {t.upper() for t in kaynak.tablolar()}:
        return {}
    try:
        df = _oku(kaynak, "L_CAPIFIRM", {"NR": 0, "NAME": ""})
    except Exception:
        return {}
    out = {}
    for _, r in df.iterrows():
        try:
            out[f"{int(r['NR']):03d}"] = turkce_onar(str(r["NAME"] or "").strip())
        except (TypeError, ValueError):
            continue
    return out


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
    # Firma adları ve dönem tarihleri (L_CAPIFIRM / L_CAPIPERIOD: aynı veritabanında ya da ayrı LOGODB yedeğinde)
    for no, ad in firma_tanimlari(kaynak).items():
        if no in firmalar:
            firmalar[no].ad = ad
    if "L_CAPIPERIOD" in tablolar:
        try:
            df = _oku(kaynak, "L_CAPIPERIOD", {"FIRMNR": 0, "NR": 0, "BEGDATE": None, "ENDDATE": None})
            for _, r in df.iterrows():
                no, p = f"{int(r['FIRMNR']):03d}", f"{int(r['NR']):02d}"
                if no in firmalar:
                    bas, bit = _tarih(r["BEGDATE"]), _tarih(r["ENDDATE"])
                    if bas or bit:
                        firmalar[no].donem_tarih[p] = f"{bas} – {bit}"
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


_BOZUK = set("ÞÝÐþýð")
_DOGRU = set("ŞİĞşığ")


def turkce_onar(metin):
    """cp1254 baytları cp1252 sanılarak okunmuş metni onarır ("MÜÞTERÝ" -> "MÜŞTERİ").
    Yanlış harmanlamayla kurulmuş eski Logo veritabanlarında görülür. Metin zaten doğruysa dokunmaz."""
    if not isinstance(metin, str) or not (_BOZUK & set(metin)) or (_DOGRU & set(metin)):
        return metin
    try:
        return metin.encode("cp1252").decode("cp1254")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return metin


def _maliyet_ata(d: pd.DataFrame, gunluk: pd.DataFrame) -> pd.Series:
    """d'nin her satırı için: aynı malzemenin o tarihten ÖNCEKİ en son giriş birim maliyeti;
    daha önce giriş yoksa sonraki ilk giriş. Hiç girişi olmayan malzemelerde NaN."""
    sonuc = pd.Series(np.nan, index=d.index, dtype=float)
    if gunluk.empty or d.empty:
        return sonuc
    sol = d.loc[d["tarih"].notna(), ["tarih", "malzeme_ref"]].copy()
    sol["_i"] = sol.index
    sol = sol.sort_values("tarih", kind="mergesort")
    geri = pd.merge_asof(sol, gunluk, on="tarih", by="malzeme_ref", direction="backward")
    ileri = pd.merge_asof(sol, gunluk, on="tarih", by="malzeme_ref", direction="forward")
    sonuc.loc[sol["_i"].values] = geri["birim_maliyet"].fillna(ileri["birim_maliyet"]).values
    return sonuc


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
            if d[c].dtype == object or str(d[c].dtype).startswith("str"):
                d[c] = d[c].fillna("").astype(str).str.strip().map(turkce_onar)

    satirlar = []
    for p in donemler:
        say(f"Dönem {p}: fatura başlıkları okunuyor…")
        inv = _oku(kaynak, f"LG_{f}_{p}_INVOICE", {"LOGICALREF": 0, "FICHENO": "", "CANCELLED": 0, "TRCODE": 0,
                                                   "SALESMANREF": 0})
        inv.columns = ["invoiceref", "fatura_no", "fatura_iptal", "fatura_trcode", "fatura_temsilci"]
        inv["fatura_no"] = inv["fatura_no"].map(turkce_onar)
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
    # birim maliyet hesabı için giriş tutarı (devir fişlerinde VATMATRAH boş/0 olabilir -> TOTAL)
    st["_giris_tutar"] = st["net_tutar"].where(st["net_tutar"] > 0, st["TOTAL"].fillna(0))
    # satış elemanı satırda boşsa fatura başlığındakini kullan
    st["SALESMANREF"] = pd.to_numeric(st["SALESMANREF"], errors="coerce").fillna(0).astype(int)
    st["SALESMANREF"] = st["SALESMANREF"].where(
        st["SALESMANREF"] > 0, pd.to_numeric(st["fatura_temsilci"], errors="coerce").fillna(0).astype(int))
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

    # ---- maliyet: her satış satırına o tarihteki son alış birim maliyeti
    say("Satış maliyetleri hesaplanıyor (son alış fiyatına göre)…")
    giris = st[st["trcode"].isin(MALIYET_KAYNAGI) & (st["miktar"] > 0) & (st["_giris_tutar"] > 0) & st["tarih"].notna()]
    gunluk = giris.groupby(["malzeme_ref", "tarih"], as_index=False).agg(_t=("_giris_tutar", "sum"), _m=("miktar", "sum"))
    gunluk["birim_maliyet"] = gunluk["_t"] / gunluk["_m"]
    gunluk = gunluk[["malzeme_ref", "tarih", "birim_maliyet"]].sort_values("tarih").reset_index(drop=True)
    satis["birim_maliyet"] = _maliyet_ata(satis, gunluk)
    satis["maliyet_tutar"] = satis["miktar"] * satis["birim_maliyet"]
    pozitif = satis.loc[~satis["iade"], "net_tutar"].sum()
    kalite["maliyet_kapsama"] = round(float(
        satis.loc[~satis["iade"] & satis["birim_maliyet"].notna(), "net_tutar"].sum() / pozitif), 4) if pozitif else 0.0
    son_maliyet = (gunluk.groupby("malzeme_ref", as_index=False).last()
                   .rename(columns={"tarih": "son_giris_tarih", "birim_maliyet": "son_birim_maliyet"}))
    son_ted = (st[(st["trcode"] == 1) & st["tarih"].notna()].sort_values("tarih")
               .groupby("malzeme_ref", as_index=False)["cari_ref"].last().rename(columns={"cari_ref": "son_tedarikci_ref"}))
    malzeme_maliyet = son_maliyet.merge(son_ted, on="malzeme_ref", how="left")

    # ---- stok bakiyesi: en son seçilen dönemden (o dönemin devir fişi önceki bakiyeyi taşır)
    son = max(donemler)
    s = hareket[hareket["donem"] == son]
    yon = np.select([s["iocode"].isin([1, 2]), s["iocode"].isin([3, 4])], [1, -1], 0)
    stok = (s.assign(m=s["miktar"] * yon).groupby("malzeme_ref", as_index=False)["m"].sum()
            .rename(columns={"m": "stok_miktar"}))
    stok_ambar = (s.assign(m=s["miktar"] * yon).groupby(["malzeme_ref", "ambar"], as_index=False)["m"].sum()
                  .rename(columns={"m": "stok_miktar"}))
    kalite["stok_donemi"] = son

    mevcut_tablolar = {t.upper() for t in kaynak.tablolar()}

    # ---- ambar adları (L_CAPIWHOUSE aynı veritabanındaysa)
    ambar_no = sorted(set(pd.to_numeric(hareket["ambar"], errors="coerce").fillna(0).astype(int)))
    ambar_ad = {}
    if "L_CAPIWHOUSE" in mevcut_tablolar:
        try:
            w = _oku(kaynak, "L_CAPIWHOUSE", {"FIRMNR": 0, "NR": 0, "NAME": ""})
            w = w[pd.to_numeric(w["FIRMNR"], errors="coerce") == int(f)]
            ambar_ad = {int(r.NR): turkce_onar(str(r.NAME or "").strip()) for r in w.itertuples()}
        except Exception:
            pass
    dim_ambar = pd.DataFrame({"ambar": ambar_no, "ambar_ad": [ambar_ad.get(n) or f"Ambar {n}" for n in ambar_no]})

    # ---- satış elemanları (LG_SLSMAN)
    dim_temsilci = pd.DataFrame(columns=["temsilci_ref", "temsilci_kod", "temsilci_ad"])
    if "LG_SLSMAN" in mevcut_tablolar:
        try:
            t = _oku(kaynak, "LG_SLSMAN", {"LOGICALREF": 0, "CODE": "", "DEFINITION_": ""})
            t.columns = list(dim_temsilci.columns)
            for c in ("temsilci_kod", "temsilci_ad"):
                t[c] = t[c].fillna("").astype(str).str.strip().map(turkce_onar)
            dim_temsilci = t[t["temsilci_ref"].isin(set(hareket["satis_temsilci_ref"]))].reset_index(drop=True)
        except Exception:
            pass

    # ---- cari hareketler: en son dönemden (o dönemin devir/açılış fişi önceki bakiyeyi taşır)
    cari_hareket = pd.DataFrame(columns=["hareket_ref", "cari_ref", "tarih", "trcode", "hareket", "borc", "alacak"])
    if f"LG_{f}_{son}_CLFLINE" in mevcut_tablolar:
        say(f"Dönem {son}: cari hareketler okunuyor…")
        cl = _oku(kaynak, f"LG_{f}_{son}_CLFLINE", {"LOGICALREF": 0, "CLIENTREF": 0, "DATE_": None, "TRCODE": 0,
                                                    "SIGN": 0, "AMOUNT": 0.0, "CANCELLED": 0})
        cl["AMOUNT"] = pd.to_numeric(cl["AMOUNT"], errors="coerce").fillna(0.0)
        cl_iptal = pd.to_numeric(cl["CANCELLED"], errors="coerce").fillna(0).astype(int) == 1
        kalite["cari_iptal_satir"] = int(cl_iptal.sum())
        cl = cl[~cl_iptal]
        isaret = pd.to_numeric(cl["SIGN"], errors="coerce").fillna(0).astype(int)
        trc = pd.to_numeric(cl["TRCODE"], errors="coerce").fillna(0).astype(int)
        cari_hareket = pd.DataFrame({
            "hareket_ref": cl["LOGICALREF"].values, "cari_ref": cl["CLIENTREF"].values,
            "tarih": pd.to_datetime(cl["DATE_"], errors="coerce").values, "trcode": trc.values,
            "hareket": trc.map(CARI_TRCODE_AD).fillna("Diğer").values,
            "borc": np.where(isaret == 0, cl["AMOUNT"], 0.0), "alacak": np.where(isaret == 1, cl["AMOUNT"], 0.0)})
        kalite["cari_hareket_donemi"] = son
        kalite["cari_hareket_satir"] = int(len(cari_hareket))

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
                   "fact_satis": satis, "fact_alis": alis, "stok_bakiye": stok, "stok_ambar": stok_ambar,
                   "malzeme_maliyet": malzeme_maliyet, "dim_ambar": dim_ambar, "dim_temsilci": dim_temsilci,
                   "fact_cari_hareket": cari_hareket}.items():
        df.to_sql(ad, con, if_exists="replace", index=False)
    meta = pd.DataFrame([{"anahtar": k, "deger": str(v)} for k, v in kalite.items()])
    meta.to_sql("meta", con, if_exists="replace", index=False)
    con.commit()
    con.close()
    say("Aktarım tamamlandı.")
    return kalite
