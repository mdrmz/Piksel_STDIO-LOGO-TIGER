# PyInstaller tanımı — Windows, macOS ve Linux'ta aynı şekilde çalışır.
#   pip install pyinstaller
#   python demo/demo_db_olustur.py demo/logo_demo.sqlite
#   pyinstaller LogoTigerAnaliz.spec --noconfirm
# Çıktı: dist/LogoTigerAnaliz/  (içindeki LogoTigerAnaliz / LogoTigerAnaliz.exe çalıştırılır)
from PyInstaller.utils.hooks import collect_all

datas = [("templates", "templates"), ("static", "static")]
import os
if os.path.exists("demo/logo_demo.sqlite"):
    datas.append(("demo/logo_demo.sqlite", "demo"))

pm_datas, pm_bins, pm_hidden = collect_all("pymssql")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=pm_bins,
    datas=datas + pm_datas,
    hiddenimports=pm_hidden + ["pymssql", "xlsxwriter", "openpyxl"],
    excludes=["tkinter", "IPython", "pytest", "PyQt5", "PyQt6", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LogoTigerAnaliz", console=True,
          upx=False, debug=False, strip=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="LogoTigerAnaliz")
