#!/bin/bash
# Masaüstüne "Musashi Görüntüleyici" kısayolu koyar. Bir kez çift tıklamanız yeterli.
SOURCE="${BASH_SOURCE[0]:-$0}"
while [ -L "$SOURCE" ]; do
  LINK_DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  case "$SOURCE" in
    /*) ;;
    *) SOURCE="$LINK_DIR/$SOURCE" ;;
  esac
done
HERE="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
TARGET="$HERE/baslat.command"

if [ ! -f "$TARGET" ]; then
  echo "  Hata: baslat.command bulunamadı ($HERE)"
  read -r -p "  Kapatmak için Enter'a basın." _
  exit 1
fi

# Masaüstü klasörü (Türkçe sistemlerde de Finder klasörünün adı Desktop'tır)
DESKTOP="$HOME/Desktop"
[ -d "$DESKTOP" ] || DESKTOP="$HOME/Masaüstü"
if [ ! -d "$DESKTOP" ]; then
  echo "  Masaüstü klasörü bulunamadı."
  read -r -p "  Kapatmak için Enter'a basın." _
  exit 1
fi

SHORTCUT="$DESKTOP/Musashi Görüntüleyici.command"
cat > "$SHORTCUT" <<INNER
#!/bin/bash
# Masaüstü kısayolu — asıl program şurada:
# $HERE
exec "$TARGET" "\$@"
INNER
chmod +x "$SHORTCUT"

echo
echo "  ✓ Kısayol oluşturuldu:"
echo "      $SHORTCUT"
echo
echo "    Artık masaüstündeki \"Musashi Görüntüleyici\" dosyasına çift tıklayarak"
echo "    programı açabilirsiniz."
echo
echo "    İlk açılışta macOS \"geliştirici doğrulanamadı\" derse:"
echo "    kısayola sağ tık → Aç → Aç."
echo
read -r -p "  Kapatmak için Enter'a basın." _
