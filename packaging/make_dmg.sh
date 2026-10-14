#!/bin/bash
# Образ для установки на Mac: окно с программой и ярлыком «Программы» — перетащить и готово.
#   bash packaging/make_dmg.sh   (после сборки .app в dist/)
set -euo pipefail
cd "$(dirname "$0")/.."
APP="dist/PDF-мастерская.app"
VER=$(cat pdfmaster/resources/VERSION)
OUT="dist/PDF-masterskaya-mac.dmg"
[ -d "$APP" ] || { echo "нет $APP — сначала соберите приложение" >&2; exit 1; }
STAGE=$(mktemp -d)
ditto "$APP" "$STAGE/PDF-мастерская.app"
ln -s /Applications "$STAGE/Программы"
cat > "$STAGE/Как установить.txt" <<'TXT'
Перетащите «PDF-мастерская» на папку «Программы».

Первый запуск: программа подписана не Apple, поэтому macOS спросит разрешение.
Откройте «Программы», нажмите на «PDF-мастерская» правой кнопкой → «Открыть» → «Открыть».
Если кнопки «Открыть» нет: «Системные настройки» → «Конфиденциальность и безопасность» →
внизу «Всё равно открыть».
TXT
rm -f "$OUT"
hdiutil create -volname "PDF-мастерская $VER" -srcfolder "$STAGE" -ov -format UDZO "$OUT" >/dev/null 2>&1
rm -rf "$STAGE"
echo "$OUT ($(du -h "$OUT" | cut -f1))"
