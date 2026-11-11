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
import threading
import traceback
import webbrowser

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for

from core import __version__
from core.analiz import AbcEsik, Analiz, abc_ozet
from core import kurulum, tanilama, yz
from core.kaynak import BakKaynak, KaynakHatasi, MssqlKaynak, SqliteKaynak
from core.logo import AktarimAyari, aktar, firmalari_tara

TEMEL = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
CALISMA = os.environ.get("LOGO_ANALIZ_KLASOR", os.path.join(os.path.expanduser("~"), "LogoAnaliz"))
os.makedirs(CALISMA, exist_ok=True)
DEMO_DB = os.path.join(TEMEL, "demo", "logo_demo.sqlite")

app = Flask(__name__, template_folder=os.path.join(TEMEL, "templates"), static_folder=os.path.join(TEMEL, "static"))
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 ** 3


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
DURUM = {"kaynak": None, "firmalar": [], "kaynak_ad": ""}


def _kaynagi_kapat():
    k = DURUM.get("kaynak")
    if k is not None:
        try:
            k.kapat()
        except Exception:
            pass
    DURUM.update(kaynak=None, firmalar=[])


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


def _analiz_klasor(aid):
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", aid or ""):
        abort(404)
    yol = os.path.join(CALISMA, aid)
    if not os.path.exists(os.path.join(yol, "analiz.sqlite")):
        abort(404)
    return yol


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
    tur = request.form.get("tur")
    _kaynagi_kapat()

    if tur == "bak":
        dosya = request.files.get("bak_dosya")
        yol = (request.form.get("bak_yol") or "").strip().strip('"')
        yuklendi = False
        if dosya and dosya.filename:
            if not dosya.filename.lower().endswith(".bak"):
                return render_template("hata.html", mesaj="Lütfen .bak uzantılı bir Logo yedeği seçin.")
            yukleme = os.path.join(CALISMA, "_yukleme")
            os.makedirs(yukleme, exist_ok=True)
            yol = os.path.join(yukleme, re.sub(r"[^A-Za-z0-9_.\-]", "_", dosya.filename))
            dosya.save(yol)
            yuklendi = True
        if not yol or not os.path.exists(yol):
            return render_template("hata.html", mesaj="Yedek dosyası bulunamadı. Dosyayı seçin ya da tam yolunu yazın.")
        otomatik = request.form.get("sql_kaynak", "otomatik") == "otomatik"
        ayar = dict(sunucu=request.form.get("sunucu") or r"localhost\SQLEXPRESS",
                    kullanici=request.form.get("kullanici", ""), sifre=request.form.get("sifre", ""),
                    port=int(request.form.get("port") or 1433),
                    sunucudaki_yol=(request.form.get("sunucudaki_yol") or "").strip() or None)

        def is_():
            IS.yaz(f"Yedek: {os.path.basename(yol)} ({os.path.getsize(yol) / 1024 ** 2:,.0f} MB)")
            try:
                if otomatik:
                    IS.yaz("Programın SQL Server'ı kontrol ediliyor…")
                    sql = kurulum.hazirla(IS.yaz)
                    k = BakKaynak(yol, sunucu=sql["sunucu"], port=sql["port"], kullanici=sql["kullanici"],
                                  sifre=sql["sifre"], yedek_klasoru=sql.get("yedek_klasoru"),
                                  sunucu_yedek_klasoru=sql.get("sunucu_yedek_klasoru"), ilerleme=IS.yaz)
                else:
                    k = BakKaynak(yol, ilerleme=IS.yaz, **ayar)
            finally:
                if yuklendi:  # tarayıcıdan yüklenen kopya artık gereksiz (veritabanı geri yüklendi)
                    try:
                        os.remove(yol)
                    except OSError:
                        pass
            _tara(k, os.path.basename(yol))

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
    else:
        abort(400)

    IS.baslat(is_, url_for("firmalar"))
    return redirect(url_for("bekle"))


def _tara(k, ad):
    IS.yaz("Logo firmaları ve dönemleri taranıyor…")
    fs = firmalari_tara(k)
    if not fs:
        k.kapat()
        raise KaynakHatasi("Bu veritabanında Logo firma tabloları (LG_XXX_CLCARD, LG_XXX_PP_STLINE…) bulunamadı.")
    DURUM.update(kaynak=k, firmalar=fs, kaynak_ad=ad)
    for f in fs:
        IS.yaz(f"Firma {f.no} {f.ad}: dönemler {', '.join(f.donemler)}")


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
    return render_template("firmalar.html", firmalar=DURUM["firmalar"], kaynak_ad=DURUM["kaynak_ad"])


@app.route("/aktar", methods=["POST"])
def aktar_baslat():
    no = request.form.get("firma")
    firma = next((f for f in DURUM["firmalar"] if f.no == no), None)
    donemler = request.form.getlist(f"donem_{no}")
    if not firma or not donemler:
        return render_template("hata.html", mesaj="Bir firma ve en az bir dönem seçin.")
    ayar = AktarimAyari(sadece_faturali=bool(request.form.get("sadece_faturali")),
                        hizmetleri_dahil_et=bool(request.form.get("hizmetler")))
    aid = f"{dt.datetime.now():%Y%m%d_%H%M%S}_F{no}"
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
            q = aktar(kaynak, firma, donemler, os.path.join(klasor, "analiz.sqlite"), ayar, ilerleme=IS.yaz)
        except Exception:
            shutil.rmtree(klasor, ignore_errors=True)
            raise
        bilgi = {"firma": firma.no, "firma_ad": firma.ad, "donemler": donemler, "kaynak": kaynak_ad,
                 "olusturma": f"{dt.datetime.now():%d.%m.%Y %H:%M}", "tarih_bas": q["tarih_bas"], "tarih_bit": q["tarih_bit"],
                 "olu_stok_gun": olu_gun, "abc_a": abc_a, "abc_b": abc_b}
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


def _analiz_ac(aid):
    klasor = _analiz_klasor(aid)
    with open(os.path.join(klasor, "bilgi.json"), encoding="utf-8") as fh:
        bilgi = json.load(fh)
    return klasor, bilgi, Analiz(os.path.join(klasor, "analiz.sqlite"))


@app.route("/panel/<aid>")
def panel(aid):
    klasor, bilgi, a = _analiz_ac(aid)
    esik = AbcEsik(bilgi.get("abc_a", 0.8), bilgi.get("abc_b", 0.95))
    u, m, t = a.urun_abc(esik=esik), a.musteri_abc(esik=esik), a.tedarikci_abc(esik=esik)
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
        depo=a.depo_kirilim(), eski_surum=a.eski_surum,
        abc=[("Ürün", abc_ozet(u, "net_tutar")), ("Müşteri", abc_ozet(m, "net_tutar")), ("Tedarikçi", abc_ozet(t, "net_tutar"))],
        urun=u.head(15), musteri=m.head(15), olu=olu.head(15), olu_say=len(olu), olu_deger=float(olu["tahmini_deger"].sum()),
        grup=a.grup_satis(), grafik=_grafik_aylik(trend), esik=esik,
        kaynak_acik=bool(DURUM["firmalar"]), yz_hazir=yz.hazir_mi(), yz_ayar=yz.ayar(),
        yorum_html=_kayitli_yorum(klasor), gecmis=[(g["soru"], yz.md_html(g["cevap"])) for g in GECMIS.get(aid, [])])


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
    klasor, bilgi, a = _analiz_ac(aid)
    ad = f"{bilgi['firma']}_{(bilgi.get('firma_ad') or 'firma').replace(' ', '_')}"
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


@app.template_filter("tl")
def tl(v, ondalik=0):
    try:
        s = f"{float(v):,.{ondalik}f}"
    except (TypeError, ValueError):
        return ""
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("yuzde")
def yuzde(v):
    try:
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
