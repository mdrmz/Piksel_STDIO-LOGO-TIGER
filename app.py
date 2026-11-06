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
from core import kurulum
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
    neg = a.stok_devir()
    neg = neg[neg["stok_miktar"] < 0]
    return render_template(
        "panel.html", aid=aid, bilgi=bilgi, o=a.ozet(), notlar=a.veri_notlari(),
        abc=[("Ürün", abc_ozet(u, "net_tutar")), ("Müşteri", abc_ozet(m, "net_tutar")), ("Tedarikçi", abc_ozet(t, "net_tutar"))],
        urun=u.head(15), musteri=m.head(15), olu=olu.head(15), olu_say=len(olu), olu_deger=float(olu["tahmini_deger"].sum()),
        grup=a.grup_satis(), grafik=_grafik_aylik(trend), negatif_stok=len(neg), esik=esik,
        kaynak_acik=bool(DURUM["firmalar"]))


@app.route("/indir/<aid>/<tur>")
def indir(aid, tur):
    from core.rapor import excel_raporu, powerbi_disa_aktar
    klasor, bilgi, a = _analiz_ac(aid)
    ad = f"{bilgi['firma']}_{(bilgi.get('firma_ad') or 'firma').replace(' ', '_')}"
    ad = re.sub(r"[^\w\-]", "", ad)
    if tur == "excel":
        yol = os.path.join(klasor, f"Analiz_{ad}.xlsx")
        excel_raporu(a, yol, bilgi.get("olu_stok_gun", 90), AbcEsik(bilgi.get("abc_a", 0.8), bilgi.get("abc_b", 0.95)))
        return send_file(yol, as_attachment=True)
    if tur == "powerbi":
        zip_yol = powerbi_disa_aktar(a, os.path.join(klasor, "analiz.sqlite"), os.path.join(klasor, f"PowerBI_{ad}"))
        return send_file(zip_yol, as_attachment=True)
    abort(404)


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
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
