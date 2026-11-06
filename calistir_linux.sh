#!/bin/bash
# Terminalden: ./calistir_linux.sh
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 bulunamadı. Paket yöneticinizle kurun: sudo apt install python3 python3-venv"
  read -n 1 -s -r -p "Kapatmak için bir tuşa basın"; exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "İlk çalıştırma: sanal ortam kuruluyor..."
  python3 -m venv .venv || exit 1
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt || { echo "Paketler kurulamadı."; exit 1; }
fi

[ -f demo/logo_demo.sqlite ] || .venv/bin/python demo/demo_db_olustur.py demo/logo_demo.sqlite

.venv/bin/python app.py
