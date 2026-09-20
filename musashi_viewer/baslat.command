#!/bin/bash
# Musashi STEP Görüntüleyici — çift tıklayarak çalıştırın.
cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif [ -x /usr/bin/python3 ]; then
  PY=/usr/bin/python3
else
  echo
  echo "  Python 3 bulunamadı."
  echo "  Terminal'de şunu çalıştırıp Xcode komut satırı araçlarını kurun:"
  echo "      xcode-select --install"
  echo
  read -r -p "  Kapatmak için Enter'a basın." _
  exit 1
fi

"$PY" sunucu.py "$@"
