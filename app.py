"""
Logo Tiger Analiz — yerel web arayüzü.

Çalıştırma:  python app.py      (tarayıcı otomatik açılır: http://127.0.0.1:5055)
Sadece bu bilgisayardan erişilir (127.0.0.1); müşteri ağına açılmaz.
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import traceback
import webbrowser

from flask import Flask, Request, abort, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.exceptions import ClientDisconnected

from core import __version__
from core.analiz import AbcEsik, Analiz, abc_ozet
from core import kurulum, tanilama, yz
from core.kaynak import BakKaynak, KaynakHatasi, _bos_alan, _gb, MssqlKaynak, SqliteKaynak, birlestir
from core.logo import AktarimAyari, aktar_coklu, firma_tanimlari, firmalari_tara

TEMEL = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
CALISMA = os.environ.get("LOGO_ANALIZ_KLASOR", os.path.join(os.path.expanduser("~"), "LogoAnaliz"))
os.makedirs(CALISMA, exist_ok=True)
DEMO_DB = os.path.join(TEMEL, "demo", "logo_demo.sqlite")

YUKLEME = os.path.join(CALISMA, "_yukleme")


class _Istek(Request):
    """Tarayıcıdan yüklenen yedek, Windows'un geçici klasörü yerine doğrudan _yukleme klasörüne yazılır ve sonra
    kopyalanmadan yeniden adlandırılır. Böylece büyük bir yedek diskte iki kez değil bir kez yer kaplar."""

    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        if self.path != "/kaynak":
            return super()._get_file_stream(total_content_length, content_type, filename, content_length)
        os.makedirs(YUKLEME, exist_ok=True)
        return tempfile.NamedTemporaryFile("wb+", dir=YUKLEME, prefix="yukleniyor_", suffix=".tmp", delete=False)


app = Flask(__name__, template_folder=os.path.join(TEMEL, "templates"), static_folder=os.path.join(TEMEL, "static"))
app.request_class = _Istek
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 ** 3


_YARIM_YUKLEME = (
    "Yedek dosyası tarayıcıdan programa yüklenirken yarıda kesildi. Genelde dosya seçildikten sonra "
    "değiştiğinde (hâlâ kopyalanıyor, başka bir program açık tutuyor), USB/ağ sürücüsünden okunamadığında, "
    "diskte yer kalmadığında ya da yükleme sürerken sayfa yenilendiğinde olur. Çözüm: dosyayı seçmek yerine "
    "\"…ya da dosyanın tam yolu\" kutusuna tam yolunu yazın (ör. C:\\Yedekler\\TIGER.bak). "
    "Dosya USB'de ya da ağdaysa önce bilgisayara kopyalayın.")


def _yukleme_artiklarini_sil(haric=()):
    """_yukleme klasöründe yarım kalmış yüklemeleri ve önceki işlerden kalan kopyaları siler."""
    if not os.path.isdir(YUKLEME):
        return 0
    silinen = 0
    for ad in os.listdir(YUKLEME):
        yol = os.path.join(YUKLEME, ad)
        if yol in haric or not os.path.isfile(yol):
            continue
        try:
            boyut = os.path.getsize(yol)
            os.remove(yol)
            silinen += boyut
        except OSError:
            pass  # başka bir işlem kullanıyor; sonraki seferde silinir
    return silinen


def _disk_dolu_mesaji(yer):
    bos = _bos_alan(yer)
    surucu = os.path.splitdrive(os.path.abspath(yer))[0] or "/"
    return ("Diskte yer kalmadı" + (f" ({surucu} sürücüsünde {_gb(bos)} boş)" if bos is not None else "") + ". "
            "Yedeği açmak için yaklaşık yedek boyutunun 3–4 katı boş alan gerekir (yedeğin kendisi, SQL Server için "
            "bir kopyası ve geri yüklenen veritabanı). Yer açın — Geri Dönüşüm Kutusu, İndirilenler, eski yedekler — "
            "ve yedeği seçmek yerine \"…ya da dosyanın tam yolu\" kutusuna tam yolunu yazın; bu yol bir kopyayı "
            "tamamen ortadan kaldırır.")


# ---------------------------------------------------------------------------- iş durumu (tek kullanıcılı yerel uygulama)
class Is:
    def __init__(self):
        self.kilit = threading.Lock()
        self.sifirla()

    def sifirla(self):
        self.calisiyor = False
        self.log: list[str] = []
        self.hata: str | None = None
        self.sonraki: str | None = None

    def yaz(self, m):
        with self.kilit:
            self.log.append(f"{dt.datetime.now():%H:%M:%S}  {m}")
        tanilama.yaz(m)

    def baslat(self, hedef, sonraki_url):
        if self.calisiyor:
            return False
        self.sifirla()
        self.calisiyor = True

        def calis():
            try:
                hedef()
                self.sonraki = sonraki_url
            except (KaynakHatasi, kurulum.KurulumHatasi) as e:
                self.hata = str(e)
                tanilama.yaz(f"HATA: {e}")
            except Exception as e:  # beklenmeyen hata: ayrıntıyı log'a da yaz
                self.hata = f"Beklenmeyen hata: {e}"
                self.yaz(traceback.format_exc())
            finally:
                self.calisiyor = False

        threading.Thread(target=calis, daemon=True).start()
        return True


IS = Is()
DURUM = {"kaynak": None, "firmalar": [], "kaynak_ad": "", "verisiz": []}


def _kaynagi_kapat():
    k = DURUM.get("kaynak")
    if k is not None:
        try:
            k.kapat()
        except Exception:
            pass
    DURUM.update(kaynak=None, firmalar=[], verisiz=[])


def _analizler():
    out = []
    for ad in sorted(os.listdir(CALISMA), reverse=True):
        meta_yol = os.path.join(CALISMA, ad, "bilgi.json")
        if os.path.exists(meta_yol):
            with open(meta_yol, encoding="utf-8") as fh:
                b = json.load(fh)
            b["id"] = ad
            out.append(b)
    return out


_ONARILAN: set = set()


def _analiz_klasor(aid):
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", aid or ""):
        abort(404)
    yol = os.path.join(CALISMA, aid)
    depo = os.path.join(yol, "analiz.sqlite")
    if not os.path.exists(depo):
        abort(404)
    if depo not in _ONARILAN:   # kartı yedekte olmayan cari/malzemelere ad ver (listelerde "nan" yerine)
        _ONARILAN.add(depo)
        from core.kontrol import kartsiz_onar
        try:
            n = kartsiz_onar(depo)
            if n["cari"] or n["malzeme"]:
                tanilama.yaz(f"Kartı olmayan {n['cari']} cari ve {n['malzeme']} malzeme için yer tutucu kart eklendi.")
        except Exception as e:  # onarım yapılamazsa sayfa yine açılsın
            tanilama.yaz(f"Kartsız kayıt onarımı yapılamadı: {e}")
    return yol


# ---------------------------------------------------------------------------- tarih filtresi (analiz başına, tüm ekranlar)
def _tarih_yolu(klasor):
    return os.path.join(klasor, "tarih_filtresi.json")


def _tarih_gecerli(v):
    v = (v or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return None
    try:
        dt.date.fromisoformat(v)
    except ValueError:
        return None
    return v


def _tarih(klasor) -> tuple:
    """(bas, bit) 'YYYY-MM-DD' ya da None. Seçilen aralık analiz klasöründe saklanır; bütün ekranlar, Excel, PDF,
    Power BI ve yapay zekâ aynı aralığı kullanır."""
    try:
        with open(_tarih_yolu(klasor), encoding="utf-8") as fh:
            d = json.load(fh)
        return _tarih_gecerli(d.get("bas")), _tarih_gecerli(d.get("bit"))
    except (OSError, ValueError, AttributeError):
        return None, None


def _tarih_hazir(bilgi):
    """Hızlı seçimler: verinin son tarihine göre (bugüne göre değil)."""
    try:
        ilk = dt.date.fromisoformat(str(bilgi.get("tarih_bas"))[:10])
        son = dt.date.fromisoformat(str(bilgi.get("tarih_bit"))[:10])
    except ValueError:
        return []
    import pandas as pd
    out = []
    for y in range(ilk.year, son.year + 1):
        out.append((str(y), str(max(ilk, dt.date(y, 1, 1))), str(min(son, dt.date(y, 12, 31)))))
    for ad, ay in (("Son 12 ay", 12), ("Son 6 ay", 6), ("Son 3 ay", 3)):
        b = (pd.Timestamp(son) - pd.DateOffset(months=ay) + pd.Timedelta(days=1)).date()
        if b > ilk:
            out.append((ad, str(b), str(son)))
    ay_bas = son.replace(day=1)
    out.append((f"{detay_ay_ad(son)} (son ay)", str(max(ilk, ay_bas)), str(son)))
    ceyrek_bas = dt.date(son.year, 3 * ((son.month - 1) // 3) + 1, 1)
    if ceyrek_bas > ilk:
        out.append((f"{son.year} Ç{(son.month - 1) // 3 + 1}", str(ceyrek_bas), str(son)))
    return out


def detay_ay_ad(g):
    from core.detay import AYLAR
    return f"{AYLAR[g.month - 1]} {g.year}"


@app.route("/tarih/<aid>", methods=["POST"])
def tarih_sec(aid):
    klasor = _analiz_klasor(aid)
    if request.form.get("aralik"):
        bas, _, bit = request.form["aralik"].partition("|")
    elif request.form.get("temizle"):
        bas = bit = ""
    else:
        bas, bit = request.form.get("bas", ""), request.form.get("bit", "")
    bas, bit = _tarih_gecerli(bas), _tarih_gecerli(bit)
    if bas and bit and bas > bit:
        bas, bit = bit, bas
    if bas or bit:
        with open(_tarih_yolu(klasor), "w", encoding="utf-8") as fh:
            json.dump({"bas": bas, "bit": bit}, fh)
    elif os.path.exists(_tarih_yolu(klasor)):
        os.remove(_tarih_yolu(klasor))
    donus = request.form.get("donus") or f"/panel/{aid}"
    if not donus.startswith("/") or donus.startswith("//"):
        donus = f"/panel/{aid}"
    # sayfanın kendi bas/bit/sayfa parametreleri (Faturalar) yeni genel aralığı ezmesin
    from urllib.parse import parse_qsl, urlencode, urlsplit
    u = urlsplit(donus)
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k not in ("bas", "bit", "sayfa", "ay")]
    return redirect(u.path + ("?" + urlencode(q) if q else ""))


# ---------------------------------------------------------------------------- sayfalar
@app.route("/")
def ana():
    try:
        sql_durum = kurulum.durum()
    except Exception as e:  # durum okunamazsa arayüz yine açılsın
        sql_durum = {"platform": "?", "hazir": False, "sql": None, "ornekler": [], "docker": None, "hata": str(e)}
    return render_template("ana.html", analizler=_analizler(), demo_var=os.path.exists(DEMO_DB), calisma=CALISMA,
                           sql=sql_durum, docker_sayfa=kurulum.DOCKER_SAYFA,
                           varsayilan_sunucu=os.environ.get("LOGO_ANALIZ_SQL", r"localhost\SQLEXPRESS"))


@app.route("/sql-hazirla", methods=["POST"])
def sql_hazirla():
    def is_():
        kurulum.hazirla(IS.yaz)
        IS.yaz("Hazır. Artık Logo yedeğini seçebilirsiniz.")
    IS.baslat(is_, url_for("ana"))
    return redirect(url_for("bekle"))


@app.route("/kaynak", methods=["POST"])
def kaynak_ac():
    if not IS.calisiyor:
        _kaynagi_kapat()
        _yukleme_artiklarini_sil()  # önceki yarım yüklemeler (form okunmadan önce: bu isteğin dosyaları henüz yok)
    try:
        tur = request.form.get("tur")
    except ClientDisconnected:
        # Tarayıcı yüklemeyi yarıda kesti; yarım dosyayı hemen sil. (Tarayıcı çoğu zaman bu cevabı göstermez,
        # kendi hata sayfasını açar; ama günlükte iz kalır ve disk boşa dolmaz.)
        import gc
        gc.collect()
        _yukleme_artiklarini_sil()
        tanilama.yaz(f"Yükleme yarıda kesildi (Content-Length {request.content_length or 0:,} bayt)")
        return render_template("hata.html", tanilama=True, mesaj=_YARIM_YUKLEME), 400
    except OSError as e:  # yükleme sırasında disk doldu
        _yukleme_dosyalarini_kapat()
        _yukleme_artiklarini_sil()
        tanilama.yaz(f"Yükleme diske yazılamadı: {e}")
        return render_template("hata.html", mesaj=_disk_dolu_mesaji(YUKLEME), tanilama=True), 507
    if IS.calisiyor:
        _yukleme_dosyalarini_kapat()
        return redirect(url_for("bekle"))

    if tur == "bak":
        # Bir ya da iki yedek: firma verileri + (ayrı yedeklendiyse) Logo sistem veritabanı (LOGODB).
        # Hangisinin hangisi olduğu içeriğinden anlaşılır; sıra önemli değildir.
        yollar, yuklenen = [], []
        try:
            for dosya in request.files.getlist("bak_dosya") + request.files.getlist("bak_dosya2"):
                if not dosya or not dosya.filename:
                    continue
                if not dosya.filename.lower().endswith(".bak"):
                    return render_template("hata.html", mesaj=f"{dosya.filename}: lütfen .bak uzantılı bir Logo yedeği seçin.")
                os.makedirs(YUKLEME, exist_ok=True)
                hedef = os.path.join(YUKLEME, f"{len(yuklenen) + 1}_" + re.sub(r"[^A-Za-z0-9_.\-]", "_", dosya.filename))
                _yuklemeyi_kaydet(dosya, hedef)
                yollar.append(hedef)
                yuklenen.append(hedef)
        except OSError as e:
            tanilama.yaz(f"Yüklenen yedek kaydedilemedi: {e}")
            _yukleme_dosyalarini_kapat()
            _yukleme_artiklarini_sil()
            return render_template("hata.html", mesaj=_disk_dolu_mesaji(YUKLEME), tanilama=True), 507
        finally:
            _yukleme_dosyalarini_kapat()
            _yukleme_artiklarini_sil(haric=set(yuklenen))  # boş dosya alanları ve hatalı seçimlerden kalan geçici dosyalar
        for alan in ("bak_yol", "bak_yol2"):
            for satir in (request.form.get(alan) or "").splitlines():
                y = satir.strip().strip('"')
                if y:
                    if not os.path.exists(y):
                        return render_template("hata.html", mesaj=f"Yedek dosyası bulunamadı: {y}")
                    yollar.append(y)
        yollar = list(dict.fromkeys(yollar))  # aynı dosya iki kez seçildiyse bir kez aç
        if not yollar:
            return render_template("hata.html", mesaj="Yedek dosyası bulunamadı. Dosyayı seçin ya da tam yolunu yazın.")
        if len(yollar) > 2:
            return render_template("hata.html", mesaj="En fazla iki yedek seçin: firma verileri ve (varsa) LOGODB.")
        otomatik = request.form.get("sql_kaynak", "otomatik") == "otomatik"
        ayar = dict(sunucu=request.form.get("sunucu") or r"localhost\SQLEXPRESS",
                    kullanici=request.form.get("kullanici", ""), sifre=request.form.get("sifre", ""),
                    port=int(request.form.get("port") or 1433))
        sunucudaki_yol = (request.form.get("sunucudaki_yol") or "").strip() or None
        if len(yollar) > 1:
            sunucudaki_yol = None  # tek dosya için anlamlı; iki dosyada her biri kopyalanarak açılır

        def is_():
            for y in yollar:
                IS.yaz(f"Yedek: {os.path.basename(y)} ({os.path.getsize(y) / 1024 ** 2:,.0f} MB)")
            acilan = []
            try:
                if otomatik:
                    IS.yaz("Programın SQL Server'ı kontrol ediliyor…")
                    sql = kurulum.hazirla(IS.yaz)
                    ac = lambda y: BakKaynak(y, sunucu=sql["sunucu"], port=sql["port"], kullanici=sql["kullanici"],  # noqa: E731
                                             sifre=sql["sifre"], yedek_klasoru=sql.get("yedek_klasoru"),
                                             sunucu_yedek_klasoru=sql.get("sunucu_yedek_klasoru"), ilerleme=IS.yaz,
                                             tasi=y in yuklenen, artiklari_temizle=True)
                else:
                    ac = lambda y: BakKaynak(y, ilerleme=IS.yaz, sunucudaki_yol=sunucudaki_yol, tasi=y in yuklenen,  # noqa: E731
                                             **ayar)
                for i, y in enumerate(yollar, 1):
                    if len(yollar) > 1:
                        IS.yaz(f"[{i}/{len(yollar)}] {os.path.basename(y)} açılıyor…")
                    acilan.append(ac(y))
                k = birlestir(acilan, IS.yaz)
            except Exception:
                for a in acilan:  # yarıda kalırsa açılmış geçici veritabanlarını da sil
                    try:
                        a.kapat()
                    except Exception:
                        pass
                raise
            finally:
                for y in yuklenen:  # tarayıcıdan yüklenen kopyalar artık gereksiz (veritabanları geri yüklendi)
                    try:
                        os.remove(y)
                    except OSError:
                        pass
            _tara(k, " + ".join(os.path.basename(y) for y in yollar))

    elif tur == "mssql":
        ayar = dict(sunucu=request.form.get("sunucu"), veritabani=request.form.get("veritabani"),
                    kullanici=request.form.get("kullanici", ""), sifre=request.form.get("sifre", ""),
                    port=int(request.form.get("port") or 1433))

        def is_():
            IS.yaz(f"{ayar['sunucu']} / {ayar['veritabani']} bağlanılıyor…")
            _tara(MssqlKaynak(**ayar), ayar["veritabani"])

    elif tur == "demo":
        def is_():
            IS.yaz("Demo veritabanı açılıyor…")
            _tara(SqliteKaynak(DEMO_DB), "Demo veritabanı")
    elif tur is None and (request.content_length or 0) > 0:
        # Form gövdesi okunamadı: tarayıcı yüklemeyi yarıda kesti (dosya seçildikten sonra değişti/kilitlendi,
        # USB/ağ sürücüsü koptu, sayfa yenilendi…). Werkzeug bu durumda formu sessizce boş bırakır.
        tanilama.yaz(f"Yükleme yarıda kesildi (Content-Length {request.content_length:,} bayt)")
        import gc
        gc.collect()  # yarım kalan geçici dosyanın tutamacı bırakılsın (Windows'ta açık dosya silinemez)
        _yukleme_artiklarini_sil()
        return render_template("hata.html", tanilama=True, mesaj=_YARIM_YUKLEME), 400
    else:
        abort(400)

    IS.baslat(is_, url_for("firmalar"))
    return redirect(url_for("bekle"))


def _yuklemeyi_kaydet(dosya, hedef):
    """Yüklenen dosya zaten _yukleme klasöründe geçici bir dosyadaysa kopyalamadan yeniden adlandırır."""
    ad = getattr(dosya.stream, "name", None)
    if isinstance(ad, str) and os.path.dirname(os.path.abspath(ad)) == os.path.abspath(YUKLEME):
        dosya.stream.close()  # Windows'ta açık dosya taşınamaz
        os.replace(ad, hedef)
    else:
        dosya.save(hedef)


def _yukleme_dosyalarini_kapat():
    try:
        for _, f in request.files.items(multi=True):
            try:
                f.stream.close()
            except Exception:
                pass
    except Exception:
        pass


def _tara(k, ad):
    IS.yaz("Logo firmaları ve dönemleri taranıyor…")
    fs = firmalari_tara(k)
    if not fs:
        k.kapat()
        raise KaynakHatasi("Bu veritabanında Logo firma tabloları (LG_XXX_CLCARD, LG_XXX_PP_STLINE…) bulunamadı.")
    # Sistem veritabanında tanımlı olup bu yedekte verisi olmayan firmalar (seçim ekranında bilgi olarak gösterilir)
    var = {f.no for f in fs}
    verisiz = sorted((no, ad) for no, ad in firma_tanimlari(k).items() if no not in var)
    DURUM.update(kaynak=k, firmalar=fs, kaynak_ad=ad, verisiz=verisiz)
    IS.yaz(f"{len(fs)} firmada analiz edilebilir veri var.")
    for f in fs:
        IS.yaz(f"Firma {f.no} {f.ad}: dönemler {', '.join(f.donemler)}")
    if verisiz:
        IS.yaz(f"Tanımlı ama bu yedekte verisi olmayan {len(verisiz)} firma: "
               + ", ".join(f"{no} {ad}".strip() for no, ad in verisiz))


@app.route("/bekle")
def bekle():
    return render_template("bekle.html")


@app.route("/durum")
def durum():
    with IS.kilit:
        return jsonify(calisiyor=IS.calisiyor, log=IS.log[-200:], hata=IS.hata, sonraki=IS.sonraki)


@app.route("/firmalar")
def firmalar():
    if not DURUM["firmalar"]:
        return redirect(url_for("ana"))
    return render_template("firmalar.html", firmalar=DURUM["firmalar"], kaynak_ad=DURUM["kaynak_ad"],
                           verisiz=DURUM.get("verisiz") or [])


@app.route("/aktar", methods=["POST"])
def aktar_baslat():
    # Bir ya da birden fazla firma: yıl yıl ayrı firma açılmış şirketlerde seçilen firmalar tek analizde birleşir
    nolar = list(dict.fromkeys(request.form.getlist("firma")))
    secimler = []
    for no in nolar:
        firma = next((f for f in DURUM["firmalar"] if f.no == no), None)
        donemler = request.form.getlist(f"donem_{no}")
        if not firma:
            return render_template("hata.html", mesaj=f"Firma {no} bu yedekte yok.")
        if not donemler:
            return render_template("hata.html", mesaj=f"Firma {no} için en az bir dönem seçin.")
        secimler.append((firma, donemler))
    if not secimler:
        return render_template("hata.html", mesaj="En az bir firma ve dönem seçin.")
    ayar = AktarimAyari(sadece_faturali=bool(request.form.get("sadece_faturali")),
                        hizmetleri_dahil_et=bool(request.form.get("hizmetler")))
    aid = f"{dt.datetime.now():%Y%m%d_%H%M%S}_F{nolar[0]}" + (f"-{nolar[-1]}" if len(nolar) > 1 else "")
    klasor = os.path.join(CALISMA, aid)
    os.makedirs(klasor)
    kaynak, kaynak_ad = DURUM["kaynak"], DURUM["kaynak_ad"]
    # form değerleri thread'den önce alınır (thread'de istek bağlamı yok)
    olu_gun = int(request.form.get("olu_gun") or 90)
    abc_a = float(request.form.get("abc_a") or 80) / 100
    abc_b = float(request.form.get("abc_b") or 95) / 100
    baska_firma = bool(request.form.get("baska_firma"))

    def is_():
        try:
            q = aktar_coklu(kaynak, secimler, os.path.join(klasor, "analiz.sqlite"), ayar, ilerleme=IS.yaz)
        except Exception:
            shutil.rmtree(klasor, ignore_errors=True)
            raise
        bilgi = {"firma": q["firma"], "firma_ad": q["firma_ad"], "donemler": q["donemler"].split(", "), "kaynak": kaynak_ad,
                 "olusturma": f"{dt.datetime.now():%d.%m.%Y %H:%M}", "tarih_bas": q["tarih_bas"], "tarih_bit": q["tarih_bit"],
                 "olu_stok_gun": olu_gun, "abc_a": abc_a, "abc_b": abc_b}
        if len(secimler) > 1:
            bilgi["firmalar"] = [{"no": f.no, "ad": f.ad, "donemler": d} for f, d in secimler]
            if q.get("uyari"):
                bilgi["uyari"] = q["uyari"]
        with open(os.path.join(klasor, "bilgi.json"), "w", encoding="utf-8") as fh:
            json.dump(bilgi, fh, ensure_ascii=False, indent=1)
        if not baska_firma:
            IS.yaz("Kaynak kapatılıyor (geçici veritabanı siliniyor)…")
            _kaynagi_kapat()

    IS.baslat(is_, url_for("panel", aid=aid))
    return redirect(url_for("bekle"))


def _grafik_aylik(t):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    fig, ax = plt.subplots(figsize=(9.5, 3.2), dpi=110)
    x = range(len(t))
    ax.bar([i - 0.2 for i in x], t["net_satis"], width=0.4, color="#1F3864", label="Net satış")
    ax.bar([i + 0.2 for i in x], t["net_alis"], width=0.4, color="#A6A6A6", label="Net alış")
    ax.set_xticks(list(x), t["yil_ay"], rotation=45, ha="right", fontsize=8)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1e6:,.1f} M".replace(".", ",")))
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _ambar_param():
    """?ambar=N (yalnızca o ambar) ya da None (tüm ambarlar)."""
    v = (request.args.get("ambar") or "").strip()
    if not v:
        return None
    if not re.fullmatch(r"-?\d{1,6}", v):
        abort(400)
    return int(v)


def _analiz_ac(aid, ambar=None, tarihli=True):
    klasor = _analiz_klasor(aid)
    with open(os.path.join(klasor, "bilgi.json"), encoding="utf-8") as fh:
        bilgi = json.load(fh)
    bas, bit = _tarih(klasor) if tarihli else (None, None)
    a = Analiz(os.path.join(klasor, "analiz.sqlite"), ambar=ambar, bas=bas, bit=bit)
    if ambar is not None and ambar not in {x["no"] for x in a.ambarlar}:
        abort(404)
    return klasor, bilgi, a


@app.route("/panel/<aid>")
def panel(aid):
    klasor, bilgi, a = _analiz_ac(aid, _ambar_param())
    esik = AbcEsik(bilgi.get("abc_a", 0.8), bilgi.get("abc_b", 0.95))
    u, m, t = a.urun_abc(esik=esik), a.musteri_abc(esik=esik), a.tedarikci_abc(esik=esik)
    olcut = _abc_olcut(a)
    ku, km = (a.kar_abc("malzeme", esik), a.kar_abc("cari", esik)) if olcut == "kar" else (None, None)
    trend = a.aylik_trend()
    olu = a.olu_stok(bilgi.get("olu_stok_gun", 90))
    olu_gun = bilgi.get("olu_stok_gun", 90)
    kar_urun, kar_musteri = a.karlilik("malzeme"), a.karlilik("cari")
    # en düşük marjlı ürünler: cironun anlamlı olduğu (ilk %95'lik) ürünler arasından
    dusuk_marj = None
    if kar_urun is not None:
        onemli = set(u[u["sinif"].isin(["A", "B"])]["malzeme_kod"])
        dusuk_marj = kar_urun[kar_urun["malzeme_kod"].isin(onemli)].sort_values("kar_marji").head(10)
    alacak = a.cari_yaslandirma("alacak")
    kayip, siparis = a.musteri_kaybi(), a.siparis_onerisi()
    return render_template(
        "panel.html", aid=aid, bilgi=bilgi, o=a.ozet(), notlar=a.veri_notlari(),
        one_cikanlar=a.one_cikanlar(olu_gun), kar_urun=None if kar_urun is None else kar_urun.head(10),
        kar_musteri=None if kar_musteri is None else kar_musteri.head(10), dusuk_marj=dusuk_marj,
        cari=a.cari_ozet(), alacak=None if alacak is None else alacak.head(15),
        temsilci=a.temsilci_performans(), kayip=kayip.head(15), kayip_say=len(kayip),
        kayip_kesin=int((kayip["durum"] == "Kayıp").sum()), siparis=siparis.head(15), siparis_say=len(siparis),
        siparis_acil=int((siparis["durum"] == "Acil").sum()), siparis_tutar=float(siparis["oneri_tutar"].sum()),
        depo=a.depo_kirilim(), ambar_urun=a.ambar_urun(), eski_surum=a.eski_surum,
        ambarlar=a.ambarlar if len(a.ambarlar) > 1 and not a.eski_surum else [], ambar_secili=a.ambar_secili,
        ambar_secili_ad=a.ambar_secili_ad, aq=f"?ambar={a.ambar_secili}" if a.ambar_secili is not None else "",
        abc=[("Ürün", abc_ozet(ku if olcut == "kar" else u, "brut_kar" if olcut == "kar" else "net_tutar")),
             ("Müşteri", abc_ozet(km if olcut == "kar" else m, "brut_kar" if olcut == "kar" else "net_tutar")),
             ("Tedarikçi", abc_ozet(t, "net_tutar"))],
        aylar=[(r.yil_ay, float(r.net_satis)) for r in trend.itertuples()],
        haric_say=(int(a.meta.get("haric_cari") or 0), int(a.meta.get("haric_fatura") or 0),
                   float(a.meta.get("haric_satis_tutar") or 0)),
        olcut=olcut, kar_var=a.maliyet_var, urun=(ku if olcut == "kar" else u).head(15),
        musteri=(km if olcut == "kar" else m).head(15), olu=olu.head(15), olu_say=len(olu), olu_deger=float(olu["tahmini_deger"].sum()),
        grup=a.grup_satis(), grafik=_grafik_aylik(trend), esik=esik,
        kaynak_acik=bool(DURUM["firmalar"]), yz_hazir=yz.hazir_mi(), yz_ayar=yz.ayar(), fatura_var=_fatura_var(a),
        yorum_html=_kayitli_yorum(klasor), gecmis=[(g["soru"], yz.md_html(g["cevap"])) for g in GECMIS.get(aid, [])],
        kontrol=_kontrol_ozeti(aid, klasor, a))


def _kontrol_ozeti(aid, klasor, a):
    """Panelin üstündeki 'Veri kontrolü' kutusu: Logo ile farklı fatura sayısı ve eksik veri türleri."""
    from core import fatura, kontrol
    try:
        bas, bit = a.tarih_filtre or (None, None)
        v = fatura.veri(os.path.join(klasor, "analiz.sqlite"))
        farklar = kontrol.logo_farklari(v, bas, bit) if v is not None else None
        eksik = kontrol.eksik_veriler(os.path.join(klasor, "analiz.sqlite"), bas, bit, ornek=0)
    except Exception as e:  # kontrol hesaplanamazsa panel yine açılsın
        tanilama.yaz(f"Veri kontrolü özeti hesaplanamadı: {e}")
        return None
    return {"fark": None if farklar is None else int(len(farklar)),
            "fark_tutar": None if farklar is None else float(farklar["fark"].abs().sum()),
            "nedenler": [] if farklar is None else list(kontrol.fark_ozeti(farklar)[["neden", "fatura"]].itertuples(index=False)),
            "eksik": [(e["baslik"], e["adet"], e["birim"]) for e in eksik]}


# ---------------------------------------------------------------------------- faturalar ve tam ABC listesi
SAYFA_BOYU = 100


def _abc_olcut(a):
    """ABC neye göre: 'kar' (tahmini brüt kâr; maliyet verisi varsa varsayılan) ya da 'ciro' (?abc=ciro)."""
    if not a.maliyet_var or request.args.get("abc") == "ciro":
        return "ciro"
    return "kar"


def _fatura_var(a):
    from core import fatura
    return fatura.var_mi(a.con)


def _fatura_verisi(aid):
    from core import fatura
    klasor, bilgi, _ = _depo(aid)
    return klasor, bilgi, fatura.veri(os.path.join(klasor, "analiz.sqlite"))


def _tarihli_args(klasor):
    """Faturalar ekranı: adreste tarih yoksa genel tarih filtresi uygulanır (panel toplamıyla aynı faturalar)."""
    from werkzeug.datastructures import MultiDict
    args_ = MultiDict(request.args)
    if "bas" not in args_ and "bit" not in args_:
        bas, bit = _tarih(klasor)
        if bas:
            args_["bas"] = bas
        if bit:
            args_["bit"] = bit
    return args_


@app.route("/faturalar/<aid>")
def faturalar_sayfa(aid):
    from core import fatura
    klasor, bilgi, v = _fatura_verisi(aid)
    if v is None:
        return render_template("faturalar.html", aid=aid, bilgi=bilgi, v=None)
    args_ = _tarihli_args(klasor)
    fl = fatura.Filtre.istekten(args_)
    f, urun_s = fatura.suz(v, fl)
    try:
        sayfa = max(1, int(request.args.get("sayfa", 1)))
    except ValueError:
        sayfa = 1
    sayfa_say = max(1, -(-len(f) // SAYFA_BOYU))
    sayfa = min(sayfa, sayfa_say)
    args = args_.to_dict(flat=False)

    def url(**degis):
        q = {k: v for k, v in args.items() if k not in degis}
        for k, d in degis.items():
            if d is not None:
                q[k] = [d]
        from urllib.parse import urlencode
        return f"/faturalar/{aid}?" + urlencode(q, doseq=True)

    def sirala_url(kolon):
        yon = "artan" if fl.sirala == kolon and fl.yon == "azalan" else "azalan"
        return url(sirala=kolon, yon=yon, sayfa=None)

    return render_template("faturalar.html", aid=aid, bilgi=bilgi, v=v, fl=fl, tur_grup=fatura.TUR_GRUP,
                           f=f.iloc[(sayfa - 1) * SAYFA_BOYU: sayfa * SAYFA_BOYU], o=fatura.ozet(f, urun_s),
                           sayfa=sayfa, sayfa_say=sayfa_say, url=url, sirala_url=sirala_url, fark_esik=fatura.FARK_ESIK,
                           excel_url=url(sayfa=None).replace(f"/faturalar/{aid}?", f"/faturalar/{aid}/excel?", 1),
                           sorgu=url(hata=None, sayfa=None).split("?", 1)[1], buraya=url(hata=None), hata=request.args.get("hata"),
                           toplu_ekle=int((~f["iptal"] & ~f["haric"]).sum()), toplu_geri=int((f["haric"] & ~f["iptal"] & (f["haric_neden"] == "Fatura hariç tutuldu")).sum()))


@app.route("/faturalar/<aid>/excel")
def faturalar_excel(aid):
    from core import fatura
    klasor, bilgi, v = _fatura_verisi(aid)
    if v is None:
        abort(404)
    fl = fatura.Filtre.istekten(_tarihli_args(klasor))
    f, _ = fatura.suz(v, fl)
    yol = os.path.join(klasor, f"Faturalar_{re.sub(r'[^0-9A-Za-z-]', '', str(bilgi['firma']))}.xlsx")
    fatura.excel_indir(v, f, yol, "Filtreli liste." if fl.aktif_mi() else "Tüm faturalar.")
    return send_file(yol, as_attachment=True)


@app.route("/fatura/<aid>")
def fatura_sayfa(aid):
    from core import fatura
    klasor, bilgi, v = _fatura_verisi(aid)
    if v is None:
        abort(404)
    try:
        sonuc = fatura.detay(v, request.args.get("d", ""), int(request.args.get("r", "")))
    except ValueError:
        abort(404)
    if sonuc is None:
        abort(404)
    bas, satirlar = sonuc
    from core import kontrol
    return render_template("fatura.html", aid=aid, bilgi=bilgi, f=bas, satirlar=satirlar, fark_esik=fatura.FARK_ESIK,
                           neden=kontrol.fatura_nedeni(v, bas),
                           buraya=request.full_path.rstrip("?"),
                           geri=request.referrer if request.referrer and "/faturalar/" in request.referrer else f"/faturalar/{aid}")


@app.route("/abc/<aid>/<tur>")
def abc_liste(aid, tur):
    if tur not in ("urun", "musteri", "tedarikci"):
        abort(404)
    klasor, bilgi, a = _analiz_ac(aid, _ambar_param())
    esik = AbcEsik(bilgi.get("abc_a", 0.8), bilgi.get("abc_b", 0.95))
    olcut = "ciro" if tur == "tedarikci" else _abc_olcut(a)
    if olcut == "kar":
        t = a.kar_abc("malzeme" if tur == "urun" else "cari", esik)
    else:
        t = {"urun": a.urun_abc, "musteri": a.musteri_abc, "tedarikci": a.tedarikci_abc}[tur](esik=esik)
    return render_template("abc.html", aid=aid, bilgi=bilgi, tur=tur, t=t, esik=esik, olcut=olcut, kar_var=a.maliyet_var,
                           ozet=abc_ozet(t, "brut_kar" if olcut == "kar" else "net_tutar"),
                           ambar_secili=a.ambar_secili, ambar_secili_ad=a.ambar_secili_ad,
                           fatura_var=_fatura_var(a))


# ---------------------------------------------------------------------------- ürün kartı, cari kartı, ay detayı, arama
def _depo(aid):
    klasor = _analiz_klasor(aid)
    with open(os.path.join(klasor, "bilgi.json"), encoding="utf-8") as fh:
        bilgi = json.load(fh)
    return klasor, bilgi, os.path.join(klasor, "analiz.sqlite")


def _ay_param():
    ay = (request.args.get("ay") or "").strip()
    return ay if re.fullmatch(r"\d{4}-\d{2}", ay) else None


@app.route("/urun/<aid>")
def urun_karti(aid):
    from core import detay
    klasor, bilgi, depo = _depo(aid)
    k = detay.urun_karti(depo, request.args.get("kod", ""), _ay_param(), *_tarih(klasor))
    if k is None:
        abort(404)
    return render_template("urun.html", aid=aid, bilgi=bilgi, k=k, kod=k["m"]["malzeme_kod"], ay_ad=detay.ay_ad,
                           fatura_var=_fatura_verisi(aid)[2] is not None)


@app.route("/cari/<aid>")
def cari_karti(aid):
    from core import detay, haric
    klasor, bilgi, depo = _depo(aid)
    k = detay.cari_karti(depo, request.args.get("kod", ""), _ay_param(), *_tarih(klasor))
    if k is None:
        abort(404)
    alacak = None
    try:
        y = Analiz(depo).cari_yaslandirma("alacak")
        if y is not None:
            y = y[y["cari_kod"] == k["c"]["cari_kod"]]
            alacak = y.iloc[0].to_dict() if len(y) else None
    except Exception:
        alacak = None
    con = __import__("sqlite3").connect(depo)
    try:
        haric_ok = haric.destekli_mi(con)
    finally:
        con.close()
    return render_template("cari.html", aid=aid, bilgi=bilgi, k=k, kod=k["c"]["cari_kod"], ay_ad=detay.ay_ad, alacak=alacak,
                           fatura_var=haric_ok, haric_ok=haric_ok)


@app.route("/ay/<aid>")
def ay_sayfa(aid):
    from core import detay
    klasor, bilgi, depo = _depo(aid)
    k = detay.ay_detayi(depo, _ay_param() or "", *_tarih(klasor))
    if k is None:
        abort(404)
    return render_template("ay.html", aid=aid, bilgi=bilgi, k=k, ay_ad=detay.ay_ad, fatura_var=_fatura_verisi(aid)[2] is not None)


@app.route("/ara/<aid>")
def ara_sayfa(aid):
    from core import detay
    klasor, bilgi, depo = _depo(aid)
    q = (request.args.get("q") or "").strip()
    return render_template("ara.html", aid=aid, bilgi=bilgi, q=q, s=detay.ara(depo, q))


# ---------------------------------------------------------------------------- veri kontrolü (Logo farkları, eksik veriler)
def _kontrol_verisi(aid):
    from core import fatura, kontrol
    klasor, bilgi, depo = _depo(aid)
    bas, bit = _tarih(klasor)
    v = fatura.veri(depo)
    farklar = kontrol.logo_farklari(v, bas, bit) if v is not None else None
    return {"klasor": klasor, "bilgi": bilgi, "v": v, "bas": bas, "bit": bit, "farklar": farklar,
            "ozet": kontrol.fark_ozeti(farklar) if farklar is not None else None,
            "disarida": kontrol.disarida_ozeti(v, bas, bit) if v is not None else None,
            "sagla": kontrol.sagla(v, bas, bit) if v is not None else None,
            "eksik": kontrol.eksik_veriler(depo, bas, bit)}


@app.route("/kontrol/<aid>")
def kontrol_sayfa(aid):
    k = _kontrol_verisi(aid)
    return render_template("kontrol.html", aid=aid, bilgi=k["bilgi"], k=k, fark_esik=1.0)


@app.route("/kontrol/<aid>/excel")
def kontrol_excel(aid):
    from core import kontrol
    import pandas as pd
    k = _kontrol_verisi(aid)
    bos = pd.DataFrame(columns=["tarih", "fatura_no", "belge_no", "tur", "cari_kod", "cari_ad", "logo_matrah", "kontrol_tutar",
                                "analiz_tutar", "fark", "neden", "hesap", "etki", "logo_tablo", "fatura_ref"])
    aralik = (f" Tarih filtresi: {k['bas'] or '…'} – {k['bit'] or '…'}." if (k["bas"] or k["bit"]) else "")
    yol = os.path.join(k["klasor"], f"Veri_Kontrolu_{re.sub(r'[^0-9A-Za-z-]', '', str(k['bilgi']['firma']))}.xlsx")
    kontrol.excel(yol, k["farklar"] if k["farklar"] is not None else bos,
                  k["ozet"] if k["ozet"] is not None else kontrol.fark_ozeti(bos),
                  k["eksik"], k["disarida"] if k["disarida"] is not None else pd.DataFrame(columns=["durum", "satir", "fatura", "tutar"]),
                  f"{k['bilgi'].get('firma')} {k['bilgi'].get('firma_ad') or ''}.{aralik} Oluşturma: {dt.datetime.now():%d.%m.%Y %H:%M}")
    return send_file(yol, as_attachment=True)


# ---------------------------------------------------------------------------- hesaplama dışı bırakma
@app.route("/haric/<aid>")
def haric_sayfa(aid):
    from core import haric
    klasor, bilgi, depo = _depo(aid)
    con = __import__("sqlite3").connect(depo)
    try:
        return render_template("haric.html", aid=aid, bilgi=bilgi, destekli=haric.destekli_mi(con), liste=haric.liste(con),
                               hata=request.args.get("hata"))
    finally:
        con.close()


@app.route("/haric/<aid>/<islem>", methods=["POST"])
def haric_islem(aid, islem):
    from urllib.parse import quote
    from core import haric
    klasor, bilgi, depo = _depo(aid)
    tur = request.form.get("tur", "")
    hatalar = []
    if islem == "ekle":
        anahtarlar = [x.strip() for x in re.split(r"[\n,;]+", request.form.get("anahtar", "")) if x.strip()]
        if not anahtarlar:
            hatalar.append("Kod yazın.")
        for a in anahtarlar:
            h = haric.ekle(depo, tur, a, request.form.get("aciklama", ""))
            if h:
                hatalar.append(h)
    elif islem == "cikar":
        haric.cikar(depo, tur, request.form.get("anahtar", ""))
    elif islem == "temizle":
        haric.temizle(depo, tur)
    elif islem == "toplu":
        # fatura listesinden: işaretlenen faturalar ya da filtreye uyanların tamamı
        from urllib.parse import parse_qsl
        from werkzeug.datastructures import MultiDict
        from core import fatura
        ne = "cikar" if request.form.get("ne") == "geri_al" else "ekle"
        if request.form.get("kapsam") == "filtre":
            v = fatura.veri(depo)
            fl = fatura.Filtre.istekten(MultiDict(parse_qsl(request.form.get("sorgu", ""))))
            if v is None or not fl.aktif_mi():
                hatalar.append("Tüm faturalar birden çıkarılamaz; önce filtreleyin.")
                anahtarlar = []
            else:
                f, _ = fatura.suz(v, fl)
                f = f[~f["iptal"] & (f["haric_neden"] != "Cari hariç tutuldu")]
                anahtarlar = list(f.loc[f["haric"] if ne == "cikar" else ~f["haric"], "anahtar"])
        else:
            anahtarlar = request.form.getlist("anahtar")
            if not anahtarlar:
                hatalar.append("Önce listeden fatura işaretleyin.")
        if anahtarlar:
            haric.toplu(depo, ne, "fatura", anahtarlar, request.form.get("aciklama", ""))
    else:
        abort(404)
    donus = request.form.get("donus") or ""
    if (hatalar and islem != "toplu") or not donus.startswith("/") or donus.startswith("//"):
        donus = f"/haric/{aid}" + (f"?hata={quote(' '.join(hatalar))}" if hatalar else "")
    elif hatalar:
        donus += ("&" if "?" in donus else "?") + "hata=" + quote(" ".join(hatalar))
    return redirect(donus)


def _kayitli_yorum(klasor):
    if not os.path.exists(_yorum_yolu(klasor)):
        return None
    with open(_yorum_yolu(klasor), encoding="utf-8") as fh:
        return yz.md_html(re.sub(r"^<!--.*?-->\n?", "", fh.read(), flags=re.S))


# ---------------------------------------------------------------------------- yapay zekâ
GECMIS: dict[str, list] = {}


def _yorum_yolu(klasor):
    return os.path.join(klasor, "yz_yorum.md")


@app.route("/ayarlar")
def ayarlar():
    a = yz.ayar()
    from core import mcp_sunucu
    return render_template("ayarlar.html", yz=a, anahtar_var=bool(a.get("anahtar")), saglayicilar=yz.SAGLAYICILAR,
                           hazir=yz.hazir_mi(a), claude=mcp_sunucu.claude_durumu(), kaydedildi=request.args.get("ok"))


@app.route("/ayarlar/yz", methods=["POST"])
def ayarlar_yz():
    yeni = {"saglayici": request.form.get("saglayici") or None, "anonim": bool(request.form.get("anonim")),
            "model": (request.form.get("model") or "").strip() or None}
    anahtar = (request.form.get("anahtar") or "").strip()
    if anahtar:
        yeni["anahtar"] = anahtar
    eski = yz.ayar()
    if yeni["saglayici"] and yeni["saglayici"] != eski.get("saglayici") and not anahtar:
        yeni["anahtar"] = ""  # sağlayıcı değişti, eski anahtar geçersiz
    yz.ayar_kaydet(yeni)
    return redirect(url_for("ayarlar", ok=1))


@app.route("/yz/test", methods=["POST"])
def yz_test():
    j = request.get_json(force=True)
    a = yz.ayar()
    sag = j.get("saglayici") or a.get("saglayici")
    anahtar = (j.get("anahtar") or "").strip() or (a.get("anahtar") if sag == a.get("saglayici") else "")
    if not sag or not anahtar:
        return jsonify(ok=False, hata="Sağlayıcı ve API anahtarı gerekli.")
    try:
        r = yz.baglanti_test(sag, anahtar, (j.get("model") or "").strip() or None)
    except yz.YzHatasi as e:
        return jsonify(ok=False, hata=str(e))
    yz.ayar_kaydet({"saglayici": sag, "anahtar": anahtar, "model": r["model"]})
    return jsonify(ok=True, model=r["model"], modeller=r["modeller"][:200])


@app.route("/claude/<islem>", methods=["POST"])
def claude(islem):
    from core import mcp_sunucu
    try:
        if islem == "bagla":
            mcp_sunucu.claude_bagla()
        elif islem == "ayir":
            mcp_sunucu.claude_ayir()
        else:
            abort(404)
    except (OSError, ValueError) as e:
        return render_template("hata.html", mesaj=f"Claude Desktop ayarı yazılamadı: {e}")
    return redirect(url_for("ayarlar", ok=1) + "#claude")


@app.route("/yz/sor/<aid>", methods=["POST"])
def yz_sor(aid):
    klasor, bilgi, a = _analiz_ac(aid)
    soru = (request.get_json(force=True).get("soru") or "").strip()
    if not soru:
        return jsonify(ok=False, hata="Bir soru yazın.")
    try:
        r = yz.sor(a, os.path.join(klasor, "analiz.sqlite"), bilgi, soru, GECMIS.get(aid))
    except yz.YzHatasi as e:
        return jsonify(ok=False, hata=str(e))
    GECMIS.setdefault(aid, []).append({"soru": soru, "cevap": r["cevap"]})
    return jsonify(ok=True, html=yz.md_html(r["cevap"]), sorgular=r["sorgular"], anonim=r["anonim"], model=r["model"])


@app.route("/yz/yorum/<aid>", methods=["POST"])
def yz_yorum(aid):
    klasor, bilgi, a = _analiz_ac(aid)
    try:
        metin = yz.yonetici_yorumu(a, os.path.join(klasor, "analiz.sqlite"), bilgi)
    except yz.YzHatasi as e:
        return jsonify(ok=False, hata=str(e))
    ayar_ = yz.ayar()
    with open(_yorum_yolu(klasor), "w", encoding="utf-8") as fh:
        fh.write(f"<!-- {ayar_.get('saglayici')} / {ayar_.get('model')} / {dt.datetime.now():%d.%m.%Y %H:%M} -->\n{metin}")
    return jsonify(ok=True, html=yz.md_html(metin))


@app.route("/indir/<aid>/<tur>")
def indir(aid, tur):
    from core.rapor import excel_raporu, powerbi_disa_aktar
    ambar = _ambar_param() if tur in ("excel", "pdf") else None   # Power BI paketinde ambar dilimleyicisi var
    klasor, bilgi, a = _analiz_ac(aid, ambar)
    ad = f"{bilgi['firma']}_{(bilgi.get('firma_ad') or 'firma').replace(' ', '_')}"
    if ambar is not None:
        ad += f"_{a.ambar_secili_ad.replace(' ', '_')}"
    ad = re.sub(r"[^\w\-]", "", ad)
    if tur == "excel":
        yol = os.path.join(klasor, f"Analiz_{ad}.xlsx")
        yorum = None
        if os.path.exists(_yorum_yolu(klasor)):
            with open(_yorum_yolu(klasor), encoding="utf-8") as fh:
                yorum = fh.read()
        excel_raporu(a, yol, bilgi.get("olu_stok_gun", 90), AbcEsik(bilgi.get("abc_a", 0.8), bilgi.get("abc_b", 0.95)),
                     yorum_md=yorum)
        return send_file(yol, as_attachment=True)
    if tur == "pdf":
        from core.pdf_ozet import pdf_ozeti
        yol = os.path.join(klasor, f"Yonetici_Ozeti_{ad}.pdf")
        pdf_ozeti(a, bilgi, yol)
        return send_file(yol, as_attachment=True)
    if tur == "yzpaket":
        from core.yzpaket import paket_olustur
        zip_yol = paket_olustur(a, bilgi, os.path.join(klasor, f"YZ_Paketi_{ad}.zip"), yz.ayar().get("anonim", True))
        return send_file(zip_yol, as_attachment=True)
    if tur == "powerbi":
        zip_yol = powerbi_disa_aktar(a, os.path.join(klasor, "analiz.sqlite"), os.path.join(klasor, f"PowerBI_{ad}"),
                                     bilgi.get("olu_stok_gun", 90))
        return send_file(zip_yol, as_attachment=True)
    abort(404)


@app.route("/powerbi-ac/<aid>", methods=["POST"])
def powerbi_ac(aid):
    """Hazır Power BI raporunu üretir ve bu bilgisayarda Power BI Desktop ile açar (yalnızca Windows)."""
    from core.powerbi import RAPOR_ADI
    from core.rapor import powerbi_disa_aktar
    klasor, bilgi, a = _analiz_ac(aid)
    ad = re.sub(r"[^\w\-]", "", f"{bilgi['firma']}_{(bilgi.get('firma_ad') or 'firma').replace(' ', '_')}")
    hedef = os.path.join(klasor, f"PowerBI_{ad}")
    powerbi_disa_aktar(a, os.path.join(klasor, "analiz.sqlite"), hedef, bilgi.get("olu_stok_gun", 90))
    pbit = os.path.join(hedef, RAPOR_ADI)
    if sys.platform != "win32":
        return jsonify(ok=False, mesaj="Power BI Desktop yalnızca Windows'ta çalışır. Rapor dosyası hazırlandı; "
                                       f"Windows'lu bir bilgisayarda açmak için Power BI paketini indirin. ({pbit})")
    try:
        os.startfile(pbit)  # noqa: S606 — kendi ürettiğimiz dosya, kullanıcının bilgisayarında varsayılan programla açılır
    except OSError:
        return jsonify(ok=False, mesaj="Power BI Desktop bu bilgisayarda kurulu görünmüyor. Microsoft Store'dan "
                                       "\"Power BI Desktop\" (ücretsiz) kurup tekrar deneyin.")
    tanilama.yaz("Power BI raporu açıldı.")
    return jsonify(ok=True, mesaj="Power BI Desktop açılıyor; veriler yüklenince rapor hazır olur (yaklaşık yarım dakika).")


@app.route("/tanilama")
def tanilama_indir():
    hedef = os.path.join(CALISMA, f"Tanilama_{dt.datetime.now():%Y%m%d_%H%M%S}.zip")
    with IS.kilit:
        son = list(IS.log)
    tanilama.paket(hedef, son)
    return send_file(hedef, as_attachment=True)


@app.errorhandler(Exception)
def beklenmeyen(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    tanilama.yaz("Beklenmeyen hata (sayfa):\n" + traceback.format_exc())
    return render_template("hata.html", mesaj=f"Beklenmeyen bir hata oluştu: {e}", tanilama=True), 500


@app.route("/sil/<aid>", methods=["POST"])
def sil(aid):
    shutil.rmtree(_analiz_klasor(aid), ignore_errors=True)
    return redirect(url_for("ana"))


@app.context_processor
def _surum():
    return {"surum": __version__}


# tarih filtresi çubuğunun göründüğü sayfalar
_TARIHLI = {"panel", "abc_liste", "urun_karti", "cari_karti", "ay_sayfa", "ara_sayfa", "faturalar_sayfa", "kontrol_sayfa"}


@app.context_processor
def _tarih_cubugu():
    if request.endpoint not in _TARIHLI or not (request.view_args or {}).get("aid"):
        return {}
    aid = request.view_args["aid"]
    try:
        klasor = _analiz_klasor(aid)
        with open(os.path.join(klasor, "bilgi.json"), encoding="utf-8") as fh:
            bilgi = json.load(fh)
    except Exception:
        return {}
    bas, bit = _tarih(klasor)
    g = lambda v: f"{dt.date.fromisoformat(v):%d.%m.%Y}" if v else "…"  # noqa: E731
    return {"tf": {"aid": aid, "bas": bas or "", "bit": bit or "", "aktif": bool(bas or bit),
                   "etiket": f"{g(bas)} – {g(bit)}" if (bas or bit) else "",
                   "veri_bas": str(bilgi.get("tarih_bas") or "")[:10], "veri_bit": str(bilgi.get("tarih_bit") or "")[:10],
                   "hazir": _tarih_hazir(bilgi), "donus": request.full_path.rstrip("?"),
                   "faturalar": request.endpoint == "faturalar_sayfa"}}


def _bos_degil(v):
    """Şablonda None / NaN / NaT 'None' ya da 'nan' diye yazılmasın: boş bırakılır."""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:
        return ""
    if type(v).__name__ in ("NaTType", "NAType"):
        return ""
    try:
        import numpy as np
        if isinstance(v, np.floating) and np.isnan(v):
            return ""
    except Exception:
        pass
    return v


app.jinja_env.finalize = _bos_degil
app.jinja_env.globals["zip"] = zip


@app.template_filter("tl")
def tl(v, ondalik=0):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    if x != x:  # NaN (Logo'da boş alan)
        return ""
    s = f"{x:,.{ondalik}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("abs")
def mutlak(v):
    try:
        return abs(float(v))
    except (TypeError, ValueError):
        return 0.0


@app.template_filter("yuzde")
def yuzde(v):
    try:
        if float(v) != float(v):
            return ""
        return f"%{float(v) * 100:.1f}".replace(".", ",")
    except (TypeError, ValueError):
        return ""


def main():
    if "--mcp" in sys.argv:  # Claude Desktop bağlantısı: web arayüzü yerine MCP sunucusu olarak çalış
        from core import mcp_sunucu
        mcp_sunucu.calistir()
        return
    for akis in (sys.stdout, sys.stderr):  # Windows konsolunda/yönlendirmede Türkçe karakterler hata vermesin
        try:
            akis.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    port = int(os.environ.get("LOGO_ANALIZ_PORT", 5055))
    if not os.environ.get("LOGO_ANALIZ_TARAYICI_ACMA"):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"Logo Tiger Analiz {__version__} çalışıyor: http://127.0.0.1:{port}   (kapatmak için bu pencereyi kapatın)")
    print(f"Analiz klasörü: {CALISMA}")
    tanilama.yaz(f"Uygulama başladı: sürüm {__version__}, port {port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
