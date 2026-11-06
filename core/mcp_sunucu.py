"""
Claude Desktop bağlantısı (MCP, stdio).

Claude Pro aboneliği olan kullanıcı, analizleri Claude Desktop içinden sorgulayabilsin diye program kendini
bir MCP sunucusu olarak çalıştırabilir:   LogoTigerAnaliz --mcp    (kaynaktan: python app.py --mcp)

Araçlar:
  analizleri_listele      kayıtlı analizler
  analiz_ozeti            seçilen analizin özeti (ABC, trend, geçen yıl karşılaştırması, ölü stok…)
  sql_sorgula             analiz veritabanında salt-okur SELECT

Anonimleştirme ayarı (Ayarlar > Yapay zekâ) burada da geçerlidir: açıksa cari/firma adları Claude'a takma adla gider.
Bağımlılık yok: JSON-RPC 2.0, satır başına bir mesaj (MCP stdio taşıması).
"""
from __future__ import annotations

import json
import os
import shutil
import sys

from . import __version__, kurulum

PROTOKOLLER = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
SUNUCU_ADI = "logo-tiger-analiz"


# ---------------------------------------------------------------------------- analiz erişimi
def _analizler():
    k = kurulum.calisma_klasoru()
    out = []
    for ad in sorted(os.listdir(k), reverse=True):
        b = os.path.join(k, ad, "bilgi.json")
        if os.path.exists(b) and os.path.exists(os.path.join(k, ad, "analiz.sqlite")):
            with open(b, encoding="utf-8") as fh:
                out.append({"id": ad, **json.load(fh)})
    return out


def _ac(analiz_id=None):
    from .analiz import Analiz
    from .yz import Maske, ayar
    lst = _analizler()
    if not lst:
        raise ValueError("Henüz analiz yok. Önce Logo Tiger Analiz programında bir yedek analiz edin.")
    b = next((x for x in lst if x["id"] == analiz_id), None) if analiz_id else lst[0]
    if b is None:
        raise ValueError(f"Analiz bulunamadı: {analiz_id}. analizleri_listele ile geçerli kimlikleri görün.")
    depo = os.path.join(kurulum.calisma_klasoru(), b["id"], "analiz.sqlite")
    a = Analiz(depo)
    return b, a, depo, Maske(a, b.get("firma_ad", ""), ayar().get("anonim", True))


def arac_analizleri_listele(_):
    lst = _analizler()
    if not lst:
        return "Henüz analiz yok."
    from .yz import ayar
    anonim = ayar().get("anonim", True)
    satirlar = [f"- id={x['id']} · firma {x['firma']} {'(ad gizli)' if anonim else x.get('firma_ad', '')} · "
                f"dönemler {', '.join(x['donemler'])} · {x.get('tarih_bas')} – {x.get('tarih_bit')}" for x in lst]
    return "Kayıtlı analizler (en yenisi üstte):\n" + "\n".join(satirlar)


def arac_analiz_ozeti(arg):
    from .yz import baglam
    b, a, _, maske = _ac(arg.get("analiz_id"))
    return baglam(a, b, maske)


def arac_sql_sorgula(arg):
    from .yz import sql_calistir
    sql = (arg.get("sql") or "").strip()
    if not sql:
        raise ValueError("sql parametresi gerekli.")
    _, _, depo, maske = _ac(arg.get("analiz_id"))
    sonuc = sql_calistir(depo, sql, maske, sinir=int(arg.get("satir_siniri") or 200))
    if sonuc.startswith("HATA"):
        raise ValueError(sonuc.removeprefix("HATA: "))
    return sonuc


def _araclar():
    from .yz import SEMA
    aid = {"analiz_id": {"type": "string", "description": "analizleri_listele'deki id. Boş bırakılırsa en yeni analiz."}}
    return [
        {"name": "analizleri_listele", "description": "Logo Tiger Analiz programında kayıtlı analizleri (firma, dönem) listeler.",
         "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}},
        {"name": "analiz_ozeti",
         "description": "Bir Logo Tiger analizinin özetini döner: net satış/alış, ABC dağılımı, ilk ürün/müşteri/tedarikçiler, "
                        "aylık trend, malzeme grupları, geçen yıla göre düşen/artan müşteri ve ürünler, ölü stok. "
                        "Sorulara cevap vermeden önce ilk iş bunu çağır.",
         "inputSchema": {"type": "object", "properties": aid}, "annotations": {"readOnlyHint": True}},
        {"name": "sql_sorgula",
         "description": "Analiz veritabanında salt-okur SQLite SELECT sorgusu çalıştırır (özette olmayan ayrıntılar için).\n" + SEMA,
         "inputSchema": {"type": "object", "required": ["sql"], "properties": {
             "sql": {"type": "string", "description": "Tek bir SELECT (veya WITH) sorgusu."},
             "satir_siniri": {"type": "integer", "description": "Döndürülecek en fazla satır (varsayılan 200)."}, **aid}},
         "annotations": {"readOnlyHint": True}},
    ]


ARACLAR = {"analizleri_listele": arac_analizleri_listele, "analiz_ozeti": arac_analiz_ozeti, "sql_sorgula": arac_sql_sorgula}


# ---------------------------------------------------------------------------- JSON-RPC döngüsü
def isle(mesaj: dict) -> dict | None:
    yontem, kimlik = mesaj.get("method"), mesaj.get("id")
    if kimlik is None:  # bildirim (notifications/initialized vb.)
        return None

    def cevap(sonuc):
        return {"jsonrpc": "2.0", "id": kimlik, "result": sonuc}

    if yontem == "initialize":
        istenen = (mesaj.get("params") or {}).get("protocolVersion")
        return cevap({"protocolVersion": istenen if istenen in PROTOKOLLER else PROTOKOLLER[0],
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": SUNUCU_ADI, "title": "Logo Tiger Analiz", "version": __version__},
                      "instructions": "Logo Tiger ERP satış/stok analizleri. Önce analiz_ozeti'ni çağır; ayrıntı için sql_sorgula. "
                                      "Cari adları 'Cari-0012' gibi takma adlar olabilir; aynen kullan. Cevapları Türkçe ver."})
    if yontem == "ping":
        return cevap({})
    if yontem == "tools/list":
        return cevap({"tools": _araclar()})
    if yontem == "tools/call":
        p = mesaj.get("params") or {}
        fn = ARACLAR.get(p.get("name"))
        if not fn:
            return {"jsonrpc": "2.0", "id": kimlik, "error": {"code": -32602, "message": f"Bilinmeyen araç: {p.get('name')}"}}
        try:
            metin, hata = fn(p.get("arguments") or {}), False
        except Exception as e:
            metin, hata = f"Hata: {e}", True
        return cevap({"content": [{"type": "text", "text": metin}], "isError": hata})
    return {"jsonrpc": "2.0", "id": kimlik, "error": {"code": -32601, "message": f"Desteklenmeyen yöntem: {yontem}"}}


def calistir():
    giris = sys.stdin.buffer
    cikis = sys.stdout.buffer
    sys.stdout = sys.stderr  # yanlışlıkla yapılan print'ler protokolü bozmasın
    for satir in giris:
        satir = satir.strip()
        if not satir:
            continue
        try:
            mesaj = json.loads(satir.decode("utf-8"))
        except ValueError:
            yanit = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Ayrıştırma hatası"}}
        else:
            yanit = isle(mesaj)
        if yanit is not None:
            cikis.write((json.dumps(yanit, ensure_ascii=False) + "\n").encode("utf-8"))
            cikis.flush()


# ---------------------------------------------------------------------------- Claude Desktop ayarı
def claude_ayar_yolu():
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser(r"~\AppData\Roaming")), "Claude",
                            "claude_desktop_config.json")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
    return os.path.expanduser("~/.config/Claude/claude_desktop_config.json")


def _komut():
    if getattr(sys, "frozen", False):
        return sys.executable, ["--mcp"]
    app = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
    return sys.executable, [app, "--mcp"]


def claude_durumu() -> dict:
    yol = claude_ayar_yolu()
    kurulu = os.path.isdir(os.path.dirname(yol))
    bagli = False
    if os.path.exists(yol):
        try:
            with open(yol, encoding="utf-8") as fh:
                bagli = SUNUCU_ADI in (json.load(fh).get("mcpServers") or {})
        except (OSError, ValueError):
            pass
    return {"yol": yol, "kurulu": kurulu, "bagli": bagli}


def claude_bagla() -> str:
    yol = claude_ayar_yolu()
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    ayar = {}
    if os.path.exists(yol):
        try:
            with open(yol, encoding="utf-8") as fh:
                ayar = json.load(fh)
        except ValueError:
            raise ValueError(f"Claude ayar dosyası bozuk görünüyor, elle düzeltin: {yol}")
        shutil.copyfile(yol, yol + ".yedek")
    komut, argumanlar = _komut()
    ayar.setdefault("mcpServers", {})[SUNUCU_ADI] = {
        "command": komut, "args": argumanlar,
        "env": {"LOGO_ANALIZ_KLASOR": kurulum.calisma_klasoru(), "PYTHONIOENCODING": "utf-8"}}
    with open(yol, "w", encoding="utf-8") as fh:
        json.dump(ayar, fh, ensure_ascii=False, indent=2)
    return yol


def claude_ayir():
    yol = claude_ayar_yolu()
    if not os.path.exists(yol):
        return
    with open(yol, encoding="utf-8") as fh:
        ayar = json.load(fh)
    if (ayar.get("mcpServers") or {}).pop(SUNUCU_ADI, None) is not None:
        with open(yol, "w", encoding="utf-8") as fh:
            json.dump(ayar, fh, ensure_ascii=False, indent=2)
