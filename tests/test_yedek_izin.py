"""
macOS/Linux (Docker): tarayıcıdan yüklenen yedek SQL Server konteynerinin okuyabileceği izinle hazırlanmalı.
Geçici dosyalar 0600 açılır; konteynerdeki mssql kullanıcısı (uid 10001) bunu okuyamazsa
"Operating system error 5 (Access is denied)" alınır. Gerçek SQL Server gerekmez.
"""
import os
import stat
import sys
import tempfile

import pytest

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX dosya izinleri")


def _kaynak():
    from core.kaynak import BakKaynak
    k = object.__new__(BakKaynak)   # SQL Server bağlantısı kurmadan yalnızca dosya hazırlama
    k._ilerleme, k._kopya, k.gecici_db = (lambda m: None), None, "LOGOANALIZ_TEST1234"
    return k


@pytest.mark.parametrize("tasi", [True, False])
def test_yuklenen_yedek_konteynerin_okuyacagi_izinle_hazirlanir(tmp_path, tasi):
    yukleme = tmp_path / "_yukleme"
    yukleme.mkdir()
    sunucu_kl = tmp_path / "_sqlyedek"
    sunucu_kl.mkdir()
    with tempfile.NamedTemporaryFile("wb", dir=yukleme, suffix=".tmp", delete=False) as fh:   # app.py'deki gibi
        fh.write(b"yedek")
    assert stat.S_IMODE(os.stat(fh.name).st_mode) & 0o044 == 0      # başlangıçta yalnızca sahibi okuyabilir
    eski_umask = os.umask(0o077)    # kısıtlı umask'ta kopya da yalnızca sahibine açık olurdu
    try:
        yol = _kaynak()._dosyayi_hazirla(fh.name, str(sunucu_kl), "/var/opt/mssql/backup", tasi=tasi)
    finally:
        os.umask(eski_umask)
    assert yol == "/var/opt/mssql/backup/LOGOANALIZ_TEST1234.bak"
    mod = stat.S_IMODE(os.stat(sunucu_kl / "LOGOANALIZ_TEST1234.bak").st_mode)
    assert mod & 0o004, oct(mod)     # konteynerdeki başka kullanıcı okuyabilir
    assert os.path.exists(fh.name) != tasi
