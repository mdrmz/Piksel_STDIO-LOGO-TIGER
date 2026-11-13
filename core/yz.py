"""
Yapay zekâ katmanı.

- Sağlayıcılar: Gemini, DeepSeek, OpenAI (ChatGPT API), Anthropic (Claude API). Ek paket gerekmez (urllib).
- Model adı sabit değildir: anahtar test edilirken sağlayıcının güncel model listesinden uygun olan seçilir.
- Anonimleştirme (varsayılan açık): cari ve firma adları yapay zekâya "Cari-0012" gibi takma adlarla gider,
  cevap kullanıcıya gerçek adlarla gösterilir. Eşleme tablosu bilgisayardan çıkmaz.
- Soru-cevap: model önce analizin özetini görür; gerekirse salt-okur SQL ister, sonuçları görüp cevaplar.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
import urllib.error
import urllib.request

import pandas as pd

from . import kurulum

SAGLAYICILAR = {
    "gemini": {
        "ad": "Google Gemini", "tur": "openai", "url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "tercih": ["gemini-flash-latest", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"],
        "kalip": r"^(models/)?gemini-[\d.]+-flash$|^gemini-flash-latest$",
        "anahtar_sayfa": "https://aistudio.google.com/apikey",
        "not": "Ücretsiz katmanı var. Ücretsiz katmanda Google gönderilen veriyi ürün geliştirmek için kullanabilir.",
    },
    "deepseek": {
        "ad": "DeepSeek", "tur": "openai", "url": "https://api.deepseek.com",
        "tercih": ["deepseek-flash", "deepseek-chat"], "kalip": r"^deepseek-(flash|chat)",
        "anahtar_sayfa": "https://platform.deepseek.com/api_keys",
        "not": "Çok ucuz; birkaç dolar yükleme yeterli. Sunucular Çin'de (yurt dışına veri aktarımı).",
    },
    "openai": {
        "ad": "OpenAI (ChatGPT API)", "tur": "openai", "url": "https://api.openai.com/v1",
        "tercih": ["gpt-5-mini", "gpt-5.4-mini", "gpt-4.1-mini", "gpt-4o-mini"], "kalip": r"^gpt-[\d.]+-mini$",
        "anahtar_sayfa": "https://platform.openai.com/api-keys",
        "not": "ChatGPT Plus aboneliği API'yi kapsamaz; API ayrı ücretlendirilir.",
    },
    "anthropic": {
        "ad": "Anthropic (Claude API)", "tur": "anthropic", "url": "https://api.anthropic.com/v1",
        "tercih": ["claude-sonnet-5-5", "claude-haiku-5-5"], "kalip": r"^claude-(sonnet|haiku)-",
        "anahtar_sayfa": "https://console.anthropic.com/settings/keys",
        "not": "Claude Pro aboneliği API'yi kapsamaz. Aboneliği kullanmak için 'Claude Desktop'a bağla' seçeneği var.",
    },
}


class YzHatasi(Exception):
    pass


# ---------------------------------------------------------------------------- ayarlar
def ayar() -> dict:
    a = kurulum.ayar_oku().get("yz") or {}
    a.setdefault("anonim", True)
    return a


def ayar_kaydet(yeni: dict):
    a = kurulum.ayar_oku()
    yz = a.get("yz") or {}
    yz.update({k: v for k, v in yeni.items() if v is not None})
    a["yz"] = yz
    kurulum.ayar_yaz(a)


def hazir_mi(a=None) -> bool:
    a = a or ayar()
    return bool(a.get("saglayici") and a.get("anahtar") and a.get("model"))


# ---------------------------------------------------------------------------- HTTP
def _istek(url, basliklar, govde=None, zaman_asimi=180):
    veri = json.dumps(govde).encode() if govde is not None else None
    req = urllib.request.Request(url, data=veri, method="POST" if veri else "GET",
                                 headers={"Content-Type": "application/json", "User-Agent": "LogoTigerAnaliz", **basliklar})
    try:
        with urllib.request.urlopen(req, timeout=zaman_asimi) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        ayrinti = e.read().decode("utf-8", "replace")[:500]
        if e.code in (401, 403):
            raise YzHatasi(f"API anahtarı geçersiz ya da yetkisiz ({e.code}). {ayrinti}") from e
        if e.code == 429:
            raise YzHatasi(f"Sağlayıcının kullanım sınırına ulaşıldı ya da bakiye yetersiz (429). {ayrinti}") from e
        if e.code == 404:
            raise YzHatasi(f"Model bulunamadı (404). Ayarlardan 'Bağlantıyı test et' ile modeli yenileyin. {ayrinti}") from e
        raise YzHatasi(f"Yapay zekâ servisi hata verdi ({e.code}): {ayrinti}") from e
    except urllib.error.URLError as e:
        raise YzHatasi(f"Yapay zekâ servisine ulaşılamadı (internet?): {e.reason}") from e


def _basliklar(sag, anahtar):
    if SAGLAYICILAR[sag]["tur"] == "anthropic":
        return {"x-api-key": anahtar, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {anahtar}"}


def modelleri_listele(sag, anahtar) -> list[str]:
    s = SAGLAYICILAR[sag]
    d = _istek(f"{s['url']}/models", _basliklar(sag, anahtar), zaman_asimi=30)
    return sorted({m.get("id", "").removeprefix("models/") for m in d.get("data", []) if m.get("id")})


def model_sec(sag, mevcut: list[str]) -> str | None:
    s = SAGLAYICILAR[sag]
    for m in s["tercih"]:
        if m in mevcut:
            return m
    adaylar = [m for m in mevcut if re.search(s["kalip"], m)]
    # en yeni sürüm genelde alfabetik/sayısal olarak en büyük olandır
    return sorted(adaylar, key=lambda x: [int(n) if n.isdigit() else n for n in re.split(r"(\d+)", x)])[-1] if adaylar else None


def baglanti_test(sag, anahtar, model=None) -> dict:
    if sag not in SAGLAYICILAR:
        raise YzHatasi("Bilinmeyen sağlayıcı.")
    mevcut = modelleri_listele(sag, anahtar)
    secilen = model if model and model in mevcut else model_sec(sag, mevcut)
    if not secilen:
        raise YzHatasi("Uygun model bulunamadı; model adını elle yazın.")
    cevap = sohbet(sag, anahtar, secilen, "Kısa cevap ver.", [{"role": "user", "content": "Sadece 'tamam' yaz."}],
                   azami=20)
    return {"model": secilen, "modeller": mevcut, "cevap": cevap}


def sohbet(sag, anahtar, model, sistem, mesajlar, azami=2500) -> str:
    s = SAGLAYICILAR[sag]
    if s["tur"] == "anthropic":
        d = _istek(f"{s['url']}/messages", _basliklar(sag, anahtar),
                   {"model": model, "max_tokens": azami, "system": sistem, "messages": mesajlar})
        return "".join(p.get("text", "") for p in d.get("content", []) if p.get("type") == "text").strip()
    govde = {"model": model, "messages": [{"role": "system", "content": sistem}] + mesajlar}
    if sag == "openai":
        govde["max_completion_tokens"] = max(azami, 4000)  # gpt-5 ailesi düşünme jetonlarını da buradan harcar
    else:
        govde["max_tokens"] = azami
    d = _istek(f"{s['url']}/chat/completions", _basliklar(sag, anahtar), govde)
    try:
        return (d["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError) as e:
        raise YzHatasi(f"Beklenmeyen cevap biçimi: {str(d)[:300]}") from e


# ---------------------------------------------------------------------------- anonimleştirme
class Maske:
    """Gerçek ad <-> takma ad eşlemesi. Uzun adlar önce değiştirilir (biri diğerini içeriyorsa karışmasın)."""

    def __init__(self, analiz, firma_ad: str = "", acik: bool = True):
        self.acik = acik
        self.ileri: dict[str, str] = {}
        if not acik:
            return
        for i, r in enumerate(analiz.cari.sort_values("cari_ref").itertuples(), start=1):
            ad = str(r.cari_ad or "").strip()
            if len(ad) >= 3 and ad not in self.ileri:
                self.ileri[ad] = f"Cari-{i:04d}"
        # satış temsilcileri gerçek kişilerdir: adları da takma adla gider
        for i, r in enumerate(getattr(analiz, "temsilci", pd.DataFrame()).itertuples(), start=1):
            ad = str(getattr(r, "temsilci_ad", "") or "").strip()
            if len(ad) >= 3 and ad not in self.ileri:
                self.ileri[ad] = f"Temsilci-{i:02d}"
        # birden çok firma (yıl yıl ayrı açılmış) birleştirildiyse hepsinin adı gizlenir
        adlar = [firma_ad] + str(getattr(analiz, "meta", {}).get("firma_adlari") or "").split(" | ")
        for ad in dict.fromkeys(a.strip() for a in adlar):
            if len(ad) >= 3:
                self.ileri.setdefault(ad, "FİRMA")
        self.geri = {v: k for k, v in self.ileri.items()}
        self._ileri_re = self._derle(self.ileri)
        self._geri_re = self._derle(self.geri)

    @staticmethod
    def _derle(d):
        if not d:
            return None
        return re.compile("|".join(re.escape(k) for k in sorted(d, key=len, reverse=True)))

    def gizle(self, metin):
        if not self.acik or not isinstance(metin, str) or not self._ileri_re:
            return metin
        return self._ileri_re.sub(lambda m: self.ileri[m.group(0)], metin)

    def ac(self, metin):
        if not self.acik or not isinstance(metin, str) or not self._geri_re:
            return metin
        return self._geri_re.sub(lambda m: self.geri[m.group(0)], metin)

    def tablo(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self.acik:
            return df
        df = df.copy()
        for c in df.columns:
            if df[c].dtype == object or str(df[c].dtype).startswith("str"):
                df[c] = df[c].map(self.gizle)
        return df


# ---------------------------------------------------------------------------- bağlam
def _md_tablo(df: pd.DataFrame, n=None) -> str:
    if df is None or df.empty:
        return "_(veri yok)_"
    d = df.head(n) if n else df
    def f(v):
        if isinstance(v, float):
            if pd.isna(v):
                return ""
            return f"{v:,.2f}" if abs(v) >= 1 else f"{v:.3f}"
        return str(v)
    satirlar = ["| " + " | ".join(map(str, d.columns)) + " |", "|" + "---|" * len(d.columns)]
    satirlar += ["| " + " | ".join(f(v).replace("|", "/") for v in r) + " |" for r in d.itertuples(index=False)]
    return "\n".join(satirlar)


def baglam(analiz, bilgi: dict, maske: Maske) -> str:
    o = analiz.ozet()
    from .analiz import AbcEsik, abc_ozet
    esik = AbcEsik(bilgi.get("abc_a", 0.8), bilgi.get("abc_b", 0.95))
    u, m, t = analiz.urun_abc(esik=esik), analiz.musteri_abc(esik), analiz.tedarikci_abc(esik)
    parcalar = [
        f"# Logo Tiger satış analizi — {maske.gizle(bilgi.get('firma_ad') or 'Firma')} (firma {bilgi.get('firma')})",
        f"Dönem: {o['tarih_bas']} – {o['tarih_bit']}. Tutarlar TL, KDV hariç net. Miktarlar ana birimde.",
        "## Özet",
        _md_tablo(pd.DataFrame([{k: v for k, v in o.items() if k not in ("tarih_bas", "tarih_bit")}])),
        f"## ABC dağılımı (A ≤ %{esik.a * 100:.0f}, B ≤ %{esik.b * 100:.0f} kümülatif tutar)",
        "Ürün:\n" + _md_tablo(abc_ozet(u, "net_tutar")),
        "Müşteri:\n" + _md_tablo(abc_ozet(m, "net_tutar")),
        "Tedarikçi:\n" + _md_tablo(abc_ozet(t, "net_tutar")),
        "## İlk 25 ürün", _md_tablo(u[["sira", "sinif", "malzeme_kod", "malzeme_ad", "grup_kod", "net_tutar", "toplam_miktar", "kumulatif_pay"]], 25),
        "## İlk 25 müşteri", _md_tablo(maske.tablo(m[["sira", "sinif", "cari_kod", "cari_ad", "sehir", "net_tutar", "fatura_sayisi", "son_alim", "kumulatif_pay"]]), 25),
        "## İlk 10 tedarikçi", _md_tablo(maske.tablo(t[["sira", "sinif", "cari_kod", "cari_ad", "net_tutar"]]), 10),
        "## Aylık trend", _md_tablo(analiz.aylik_trend()),
        "## Malzeme grubu", _md_tablo(analiz.grup_satis()),
    ]
    for sev, baslik, kol in (("cari", "müşteri", ["cari_kod", "cari_ad"]), ("malzeme", "ürün", ["malzeme_kod", "malzeme_ad"])):
        k = analiz.donem_karsilastirma(sev)
        if k is not None:
            bu, gecen = k.attrs["donem"]
            k = maske.tablo(k[kol + ["bu_yil", "gecen_yil", "fark", "degisim"]])
            parcalar += [f"## Geçen yıla göre en çok düşen 10 {baslik} ({bu} vs {gecen})", _md_tablo(k, 10),
                         f"## Geçen yıla göre en çok artan 10 {baslik}", _md_tablo(k.iloc[::-1], 10)]
    # ---- v0.4: kârlılık, cari, temsilci, müşteri kaybı, sipariş, depo
    ku = analiz.karlilik("malzeme")
    if ku is not None:
        km, kg = analiz.karlilik("cari"), analiz.karlilik("grup")
        parcalar += ["## Kârlılık (tahmini brüt kâr = net satış − satış miktarı × o tarihteki son alış fiyatı; muhasebe kârı değildir)",
                     "En çok kâr getiren 15 ürün:\n" + _md_tablo(ku.drop(columns=["kar_payi"]), 15),
                     "En düşük marjlı 10 ürün (net satışı 10.000 TL üzeri):\n"
                     + _md_tablo(ku[ku["net_tutar"] > 10000].sort_values("kar_marji").drop(columns=["kar_payi"]), 10),
                     "En çok kâr getiren 15 müşteri:\n" + _md_tablo(maske.tablo(km.drop(columns=["kar_payi"])), 15),
                     "Malzeme grubu kârlılığı:\n" + _md_tablo(kg)]
    co = analiz.cari_ozet()
    if co:
        parcalar += [f"## Cari bakiyeler ({co['ref_tarih']} itibarıyla, KDV dahil, dönem {co['donem']})",
                     f"Müşterilerden alacak: {co['alacak']:,.0f} TL ({co['alacak_cari']} cari). "
                     f"Yaşlandırma (gün): 0-30: {co['dilim']['g0_30']:,.0f} · 31-60: {co['dilim']['g31_60']:,.0f} · "
                     f"61-90: {co['dilim']['g61_90']:,.0f} · 91-180: {co['dilim']['g91_180']:,.0f} · 180+: {co['dilim']['g180p']:,.0f}. "
                     + (f"Ortalama tahsilat süresi {co['tahsilat_gun']:.0f} gün. " if co["tahsilat_gun"] is not None else "")
                     + f"Tedarikçilere borç: {co['borc']:,.0f} TL ({co['borc_cari']} cari).",
                     "En yüksek alacaklı 15 müşteri (ödemeler en eski kayıttan düşülmüş, FIFO):\n"
                     + _md_tablo(maske.tablo(analiz.cari_yaslandirma("alacak")), 15)]
    tp = analiz.temsilci_performans()
    if tp is not None:
        parcalar += ["## Satış temsilcisi performansı", _md_tablo(maske.tablo(tp))]
    mk = analiz.musteri_kaybi()
    parcalar += [f"## Müşteri kaybı ({int((mk['durum'] == 'Kayıp').sum())} kayıp, {int((mk['durum'] == 'Risk').sum())} risk) — "
                 "kendi ortalama alım aralığının 3 katı (Kayıp) / 2 katı (Risk) süredir alım yapmayanlar", _md_tablo(maske.tablo(mk), 20)]
    so = analiz.siparis_onerisi()
    parcalar += [f"## Sipariş önerisi ({len(so)} ürün; stoğu 15 günden az yetecekler, 45 günlük ihtiyaca tamamlanır) — ilk 20",
                 _md_tablo(maske.tablo(so), 20)]
    dk = analiz.depo_kirilim()
    if dk is not None:
        parcalar += ["## Depo (ambar) kırılımı", _md_tablo(dk)]
    parcalar += ["## Öne çıkanlar (kural tabanlı)", "\n".join(f"- {c}" for _, c in analiz.one_cikanlar(bilgi.get("olu_stok_gun", 90)))]
    olu = analiz.olu_stok(bilgi.get("olu_stok_gun", 90))
    parcalar += [f"## Ölü stok ({len(olu)} kalem, tahmini {olu['tahmini_deger'].sum():,.0f} TL) — ilk 15", _md_tablo(olu, 15),
                 "## Veri notları", "\n".join(f"- {k}: {maske.gizle(str(v))}" for k, v in analiz.veri_notlari())]
    return "\n\n".join(parcalar)


SEMA = """Analiz veritabanı (SQLite, salt-okur) tabloları:
- fact_satis(satir_ref, donem, tarih, trcode, hareket, iocode, malzeme_ref, cari_ref, ambar, faturali, fatura_no, miktar, net_tutar, satis_temsilci_ref, iade, birim_maliyet, maliyet_tutar)
  Satış satırları. net_tutar KDV hariç net TL; iadelerde miktar ve net_tutar EKSİdir (iade=1). tarih 'YYYY-MM-DD HH:MM:SS'.
  maliyet_tutar = miktar × o tarihteki son alış birim fiyatı (NULL olabilir). Tahmini brüt kâr = SUM(net_tutar - maliyet_tutar) WHERE maliyet_tutar IS NOT NULL.
- fact_alis(satir_ref, donem, tarih, trcode, hareket, iocode, malzeme_ref, cari_ref, ambar, faturali, fatura_no, miktar, net_tutar, satis_temsilci_ref, iade) Satınalma satırları; alış iadeleri eksi.
- fact_cari_hareket(hareket_ref, cari_ref, tarih, trcode, hareket, borc, alacak)  en son dönemin cari hareketleri, KDV dahil TL.
  Cari bakiye = SUM(borc) - SUM(alacak): artı ise cari bize borçlu (alacağımız), eksi ise biz borçluyuz. trcode 14 = dönem başı devir.
- dim_temsilci(temsilci_ref, temsilci_kod, temsilci_ad)  fact_satis.satis_temsilci_ref ile bağlanır (0 = atanmamış)
- dim_ambar(ambar, ambar_ad) · stok_ambar(malzeme_ref, ambar, stok_miktar)  ambar bazında stok
- malzeme_maliyet(malzeme_ref, son_giris_tarih, son_birim_maliyet, son_tedarikci_ref)
- dim_cari(cari_ref, cari_kod, cari_ad, cari_tur, sehir, ozel_kod, yetki_kod, pasif)
- dim_malzeme(malzeme_ref, malzeme_kod, malzeme_ad, grup_kod, ozel_kod, kart_tur, pasif)
- dim_tarih(tarih, yil, ay, ay_ad, ceyrek, yil_ay)
- stok_bakiye(malzeme_ref, stok_miktar)  güncel stok (ana birim)
Örnek: SELECT c.cari_ad, SUM(s.net_tutar) t FROM fact_satis s JOIN dim_cari c USING(cari_ref)
       WHERE strftime('%Y', s.tarih)='2026' GROUP BY 1 ORDER BY t DESC LIMIT 10"""

SISTEM = """Sen bir Türk KOBİ'sinin Logo Tiger ERP verisini yorumlayan deneyimli bir finans ve satış analistisin.
Kullanıcı firmanın sahibi ya da danışmanıdır. Türkçe, net ve kısa cevap ver; rakamları Türkçe biçimde yaz (1.234.567 TL, %12,5).
Sadece verilen veriye dayan; veride olmayan bir şeyi uydurma, emin değilsen söyle.
Cari adları gizlilik için "Cari-0012" gibi takma adlarla verilmiş olabilir; bu adları aynen kullan.

Özette olmayan bir bilgi gerekiyorsa SADECE tek bir ```sql ... ``` bloğu yaz (başka metin yazma).
SQLite sözdizimi, sadece SELECT. Sonucu sana göndereceğim; sonra cevabını yazarsın. En fazla {tur} sorgu hakkın var.
{sema}"""


def _sql_cikar(metin):
    m = re.search(r"```sql\s*(.+?)```", metin or "", re.S | re.I)
    return m.group(1).strip().rstrip(";") if m else None


def sql_calistir(depo, sql, maske: Maske, sinir=200) -> str:
    sql = maske.ac(sql.strip().rstrip(";"))
    if ";" in sql or not re.match(r"^\s*(select|with)\b", sql, re.I):
        return "HATA: sadece tek bir SELECT sorgusu çalıştırılabilir."
    con = sqlite3.connect(f"file:{depo}?mode=ro", uri=True)
    sayac = [0]
    def _durdur():
        sayac[0] += 1
        return 1 if sayac[0] > 2000 else 0  # ~birkaç saniyelik sınır
    con.set_progress_handler(_durdur, 10000)
    try:
        df = pd.read_sql_query(sql, con)
    except Exception as e:
        return f"HATA: {e}"
    finally:
        con.close()
    toplam = len(df)
    return f"{toplam} satır" + (f" (ilk {sinir} gösteriliyor)" if toplam > sinir else "") + "\n" + _md_tablo(maske.tablo(df), sinir)


def sor(analiz, depo, bilgi, soru, gecmis=None, a=None, azami_tur=4) -> dict:
    a = a or ayar()
    if not hazir_mi(a):
        raise YzHatasi("Yapay zekâ ayarlanmamış. Ayarlar sayfasından sağlayıcı ve API anahtarı girin.")
    maske = Maske(analiz, bilgi.get("firma_ad", ""), a.get("anonim", True))
    sistem = SISTEM.format(tur=azami_tur, sema=SEMA) + "\n\n" + baglam(analiz, bilgi, maske)
    mesajlar = []
    for g in (gecmis or [])[-6:]:
        mesajlar += [{"role": "user", "content": maske.gizle(g["soru"])},
                     {"role": "assistant", "content": maske.gizle(g["cevap"])}]
    mesajlar.append({"role": "user", "content": maske.gizle(soru)})
    sorgular = []
    for tur in range(azami_tur + 1):
        cevap = sohbet(a["saglayici"], a["anahtar"], a["model"], sistem, mesajlar)
        sql = _sql_cikar(cevap)
        if not sql or tur == azami_tur:
            break
        sonuc = sql_calistir(depo, sql, maske)
        sorgular.append({"sql": maske.ac(sql), "sonuc_ozet": sonuc.split("\n", 1)[0]})
        mesajlar += [{"role": "assistant", "content": cevap},
                     {"role": "user", "content": f"Sorgu sonucu:\n{sonuc}\n\nŞimdi soruyu cevapla (gerekirse bir sorgu daha isteyebilirsin)."}]
    if _sql_cikar(cevap):
        cevap = "Soruyu cevaplamak için yeterli sorgu hakkı kalmadı; soruyu daraltıp tekrar deneyin."
    return {"cevap": maske.ac(cevap), "sorgular": sorgular, "anonim": maske.acik, "model": a["model"]}


YORUM_ISTEGI = """Bu verilere dayanarak firma yönetimi için tek sayfalık bir "Yönetici yorumu" yaz. Başlıklar:
## Genel tablo
## Güçlü yanlar
## Riskler ve dikkat edilecekler
## Önerilen 5 aksiyon
Somut rakam ve isim ver (ürün, müşteri, grup). Abartma; veri neyi gösteriyorsa onu söyle. SQL isteme, özet yeterli."""


def yonetici_yorumu(analiz, depo, bilgi, a=None) -> str:
    a = a or ayar()
    if not hazir_mi(a):
        raise YzHatasi("Yapay zekâ ayarlanmamış.")
    maske = Maske(analiz, bilgi.get("firma_ad", ""), a.get("anonim", True))
    sistem = SISTEM.format(tur=0, sema="") + "\n\n" + baglam(analiz, bilgi, maske)
    return maske.ac(sohbet(a["saglayici"], a["anahtar"], a["model"], sistem,
                           [{"role": "user", "content": YORUM_ISTEGI}], azami=3000))


# ---------------------------------------------------------------------------- basit markdown -> HTML
def md_html(metin: str) -> str:
    satirlar = (metin or "").replace("\r", "").split("\n")
    out, liste, tablo = [], None, []

    def satir_ici(s):
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return s

    def tablo_bitir():
        if not tablo:
            return
        h = [c.strip() for c in tablo[0].strip("|").split("|")]
        govde = [r for r in tablo[1:] if not re.match(r"^\|?\s*:?-{2,}", r)]
        out.append("<table><tr>" + "".join(f"<th>{satir_ici(c)}</th>" for c in h) + "</tr>" + "".join(
            "<tr>" + "".join(f"<td>{satir_ici(c.strip())}</td>" for c in r.strip("|").split("|")) + "</tr>" for r in govde)
            + "</table>")
        tablo.clear()

    for s in satirlar:
        if s.strip().startswith("|"):
            tablo.append(s.strip())
            continue
        tablo_bitir()
        m = re.match(r"^\s*([-*]|\d+[.)])\s+(.*)", s)
        if m:
            tur = "ol" if m.group(1)[0].isdigit() else "ul"
            if liste != tur:
                if liste:
                    out.append(f"</{liste}>")
                out.append(f"<{tur}>")
                liste = tur
            out.append(f"<li>{satir_ici(m.group(2))}</li>")
            continue
        if liste:
            out.append(f"</{liste}>")
            liste = None
        h = re.match(r"^(#{1,4})\s+(.*)", s)
        if h:
            out.append(f"<h{len(h.group(1)) + 2}>{satir_ici(h.group(2))}</h{len(h.group(1)) + 2}>")
        elif s.strip():
            out.append(f"<p>{satir_ici(s)}</p>")
    tablo_bitir()
    if liste:
        out.append(f"</{liste}>")
    return "\n".join(out)
