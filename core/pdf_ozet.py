"""
Tek sayfalık PDF yönetici özeti (A4 dikey). Ek kütüphane gerektirmez; matplotlib ile çizilir.
Yapay zekâ kullanmaz: rakamlar ve "öne çıkanlar" analizlerin kendisinden gelir.
"""
from __future__ import annotations

import datetime as dt
import textwrap

from .analiz import AbcEsik, Analiz

LACIVERT, GRI, KIRMIZI, YESIL, ZEMIN = "#1F3864", "#A6A6A6", "#B42318", "#1E6B2F", "#F4F5F7"
SEVIYE_RENK = {"iyi": YESIL, "bilgi": LACIVERT, "dikkat": KIRMIZI}


def _tl(v, ondalik=0):
    try:
        return f"{float(v):,.{ondalik}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "–"


def _kisa(v):
    """Büyük tutarları kısaltır: 45.995.309 -> '46,0 M ₺', 116.915 -> '117 bin ₺'."""
    v = float(v or 0)
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f} M ₺".replace(".", ",")
    if abs(v) >= 1e4:
        return f"{v / 1e3:.0f} bin ₺"
    return f"{_tl(v)} ₺"


def _yuzde(v):
    return "–" if v is None else f"%{float(v) * 100:.1f}".replace(".", ",")


def _kes(metin, n):
    metin = str(metin or "")
    return metin if len(metin) <= n else metin[: n - 1] + "…"


def pdf_ozeti(a: Analiz, bilgi: dict, yol: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Rectangle
    from matplotlib.ticker import FuncFormatter

    esik = AbcEsik(bilgi.get("abc_a", 0.8), bilgi.get("abc_b", 0.95))
    olu_gun = int(bilgi.get("olu_stok_gun", 90))
    o = a.ozet()
    co = a.cari_ozet()
    olu = a.olu_stok(olu_gun)
    mk = a.musteri_kaybi()
    so = a.siparis_onerisi()
    urun, musteri = a.urun_abc(esik=esik), a.musteri_abc(esik)
    trend = a.aylik_trend()

    # paketlenmiş programda da aynı görünsün diye matplotlib'in kendi yazı tipi (Türkçe karakterler ve ₺ içerir)
    plt.rcParams["font.family"] = "DejaVu Sans"
    fig = plt.figure(figsize=(8.27, 11.69), dpi=150)   # A4 dikey
    fig.patch.set_facecolor("white")
    kanvas = fig.add_axes([0, 0, 1, 1])
    kanvas.set_xlim(0, 1); kanvas.set_ylim(0, 1); kanvas.axis("off")

    # ---- başlık şeridi
    kanvas.add_patch(Rectangle((0, 0.925), 1, 0.075, color=LACIVERT, zorder=0))
    firma = bilgi.get("firma_ad") or f"Firma {bilgi.get('firma', '')}"
    kanvas.text(0.05, 0.968, _kes(firma, 60), color="white", fontsize=16, fontweight="bold", va="center")
    kanvas.text(0.05, 0.942, "Yönetici özeti — satış, kârlılık, alacak ve stok", color="white", fontsize=9.5, va="center", alpha=0.9)
    kanvas.text(0.95, 0.968, f"{o['tarih_bas']} – {o['tarih_bit']}", color="white", fontsize=9.5, va="center", ha="right")
    kanvas.text(0.95, 0.942, f"Rapor tarihi {dt.date.today():%d.%m.%Y}", color="white", fontsize=8.5, va="center", ha="right", alpha=0.9)

    # ---- KPI kutuları (2 satır × 4)
    kpi = [
        ("Net satış (KDV hariç)", _kisa(o["net_satis"]), f"{_tl(o['fatura_sayisi'])} fatura · {_tl(o['musteri_sayisi'])} müşteri"),
        ("Tahmini brüt kâr", _kisa(o["brut_kar"]) if o.get("brut_kar") is not None else "–",
         f"marj {_yuzde(o.get('kar_marji'))}" if o.get("brut_kar") is not None else "maliyet verisi yok"),
        ("Müşterilerden alacak", _kisa(co["alacak"]) if co else "–",
         (f"60 günü aşan {_kisa(co['gecikmis'])}" if co else "cari hareket tablosu yok")),
        ("Ort. tahsilat süresi", f"{co['tahsilat_gun']:.0f} gün" if co and co["tahsilat_gun"] is not None else "–",
         f"tedarikçilere borç {_kisa(co['borc'])}" if co else ""),
        ("Ölü stok", _kisa(olu["tahmini_deger"].sum()), f"{len(olu)} kalem · {olu_gun}+ gün satışsız"),
        ("Kaybedilen müşteri", str(int((mk["durum"] == "Kayıp").sum())), f"{int((mk['durum'] == 'Risk').sum())} müşteri risk altında"),
        ("Sipariş gereken ürün", str(len(so)), f"{int((so['durum'] == 'Acil').sum())} acil · {_kisa(so['oneri_tutar'].sum())}"),
        ("İade oranı", _yuzde(o["iade_orani"]), f"iade {_kisa(o['iade_tutar'])}"),
    ]
    x0, gen, bosluk, yuk = 0.05, 0.2125, 0.0167, 0.062
    for i, (ad, deger, alt) in enumerate(kpi):
        x = x0 + (i % 4) * (gen + bosluk)
        y = 0.848 - (i // 4) * (yuk + 0.011)
        kanvas.add_patch(FancyBboxPatch((x, y), gen, yuk, boxstyle="round,pad=0,rounding_size=0.006",
                                        facecolor=ZEMIN, edgecolor="#E2E5EA", linewidth=0.6))
        kanvas.text(x + 0.01, y + yuk - 0.013, ad, fontsize=7, color="#6B7280", va="center")
        kanvas.text(x + 0.01, y + 0.031, deger, fontsize=13, fontweight="bold", color=LACIVERT, va="center")
        kanvas.text(x + 0.01, y + 0.011, _kes(alt, 38), fontsize=6.3, color="#6B7280", va="center")

    # ---- aylık satış / alış grafiği
    kanvas.text(0.05, 0.752, "Aylık net satış ve alış", fontsize=10, fontweight="bold", color=LACIVERT)
    ax = fig.add_axes([0.085, 0.585, 0.865, 0.155])
    t = trend.tail(24)
    xs = range(len(t))
    ax.bar([i - 0.2 for i in xs], t["net_satis"], width=0.4, color=LACIVERT, label="Net satış")
    ax.bar([i + 0.2 for i in xs], t["net_alis"], width=0.4, color=GRI, label="Net alış")
    ax.set_xticks(list(xs), t["yil_ay"], rotation=45, ha="right", fontsize=6)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1e6:.1f} M".replace(".", ",")))
    ax.tick_params(axis="y", labelsize=6.5, length=0); ax.tick_params(axis="x", length=0)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25, linewidth=0.5); ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=7, loc="upper right", ncol=2, bbox_to_anchor=(1, 1.14))

    # ---- ilk 5 ürün / müşteri
    def liste(x, y, baslik, satirlar):
        kanvas.text(x, y, baslik, fontsize=10, fontweight="bold", color=LACIVERT)
        for i, (ad, tutar, pay) in enumerate(satirlar):
            yy = y - 0.021 - i * 0.0165
            kanvas.text(x, yy, f"{i + 1}. {_kes(ad, 34)}", fontsize=7.6, va="center")
            kanvas.text(x + 0.345, yy, f"{_tl(tutar)} ₺", fontsize=7.6, va="center", ha="right")
            kanvas.text(x + 0.425, yy, _yuzde(pay), fontsize=7.6, va="center", ha="right", color="#6B7280")
        kanvas.plot([x, x + 0.425], [y - 0.009, y - 0.009], color="#E2E5EA", linewidth=0.6)

    liste(0.05, 0.515, "En çok satan 5 ürün", [(r.malzeme_ad, r.net_tutar, r.pay) for r in urun.head(5).itertuples()])
    liste(0.525, 0.515, "En büyük 5 müşteri", [(r.cari_ad, r.net_tutar, r.pay) for r in musteri.head(5).itertuples()])

    # ---- alacak yaşlandırma grafiği + ABC özeti
    kanvas.text(0.05, 0.385, "Alacak yaşlandırma (gün · KDV dahil)", fontsize=10, fontweight="bold", color=LACIVERT)
    if co and co["alacak"] > 0:
        ax2 = fig.add_axes([0.085, 0.262, 0.39, 0.108])
        etiket = ["0-30", "31-60", "61-90", "91-180", "180+"]
        deger = list(co["dilim"].values())
        renk = [LACIVERT, LACIVERT, "#D97706", KIRMIZI, KIRMIZI]
        cubuk = ax2.bar(etiket, deger, color=renk, width=0.62)
        for c, v in zip(cubuk, deger):
            ax2.text(c.get_x() + c.get_width() / 2, c.get_height(), _kisa(v).replace(" ₺", ""), ha="center", va="bottom", fontsize=6)
        ax2.set_yticks([]); ax2.tick_params(axis="x", labelsize=7, length=0)
        ax2.spines[["top", "right", "left"]].set_visible(False)
        ax2.margins(y=0.2)
    else:
        kanvas.text(0.05, 0.345, "Bu yedekte cari hareket tablosu (CLFLINE) bulunmadığı için\nalacak yaşlandırması hesaplanamadı.",
                    fontsize=7.6, color="#6B7280", va="top")

    kanvas.text(0.525, 0.385, "ABC dağılımı", fontsize=10, fontweight="bold", color=LACIVERT)
    from .analiz import abc_ozet
    kanvas.plot([0.525, 0.95], [0.376, 0.376], color="#E2E5EA", linewidth=0.6)
    kanvas.text(0.525, 0.364, "A sınıfı", fontsize=7, color="#6B7280", va="center")
    kanvas.text(0.72, 0.364, "kalem", fontsize=7, color="#6B7280", va="center", ha="right")
    kanvas.text(0.83, 0.364, "kalem payı", fontsize=7, color="#6B7280", va="center", ha="right")
    kanvas.text(0.95, 0.364, "tutar payı", fontsize=7, color="#6B7280", va="center", ha="right")
    for i, (ad, tablo) in enumerate((("Ürün", urun), ("Müşteri", musteri), ("Tedarikçi", a.tedarikci_abc(esik)))):
        oz = abc_ozet(tablo, "net_tutar")
        r = oz[oz["sinif"] == "A"].iloc[0]
        yy = 0.345 - i * 0.0175
        kanvas.text(0.525, yy, ad, fontsize=7.8, va="center")
        kanvas.text(0.72, yy, f"{int(r['kalem_sayisi'])}", fontsize=7.8, va="center", ha="right")
        kanvas.text(0.83, yy, _yuzde(r["kalem_payi"]), fontsize=7.8, va="center", ha="right")
        kanvas.text(0.95, yy, _yuzde(r["deger_payi"]), fontsize=7.8, va="center", ha="right")
    kanvas.text(0.525, 0.283, f"A sınıfı: tutarın ilk {_yuzde(esik.a)} kadarını oluşturan kalemler.\nAz sayıda kalemin cironun büyük kısmını yaptığını gösterir.",
                fontsize=6.5, color="#6B7280", va="top", linespacing=1.4)

    # ---- öne çıkanlar
    kanvas.text(0.05, 0.228, "Öne çıkanlar", fontsize=10, fontweight="bold", color=LACIVERT)
    kanvas.plot([0.05, 0.95], [0.219, 0.219], color="#E2E5EA", linewidth=0.6)
    y = 0.205
    for seviye, cumle in a.one_cikanlar(olu_gun)[:8]:
        satirlar = textwrap.wrap(cumle, 118)
        if y - 0.0135 * len(satirlar) < 0.052:
            break
        kanvas.add_patch(Rectangle((0.05, y - 0.004), 0.007, 0.008, color=SEVIYE_RENK[seviye]))
        for s in satirlar:
            kanvas.text(0.066, y, s, fontsize=7.6, va="center")
            y -= 0.0135
        y -= 0.004

    # ---- dipnot
    kanvas.plot([0.05, 0.95], [0.044, 0.044], color="#E2E5EA", linewidth=0.6)
    kanvas.text(0.05, 0.03, "Tutarlar TL. Satışlar KDV hariç nettir (iadeler düşülmüş, iptaller hariç); alacak/borç bakiyeleri KDV dahildir. "
                            "Brüt kâr son alış maliyetiyle tahmindir, muhasebe kârı değildir.", fontsize=5.8, color="#6B7280", va="center")
    kanvas.text(0.05, 0.018, "Logo Tiger Analiz · Piksel Analitik · Ayrıntılar Excel raporunda.", fontsize=5.8, color="#6B7280", va="center")

    fig.savefig(yol, format="pdf")
    plt.close(fig)
    return yol
