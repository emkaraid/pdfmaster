#!/bin/bash
# Сборка PDF-мастерской в .app и установка в /Applications.
#   ./build.sh               — тесты, сборка, установка
#   ./build.sh --no-tests    — без автотестов
#   ./build.sh --no-install  — собрать в dist/, не копировать в /Applications
set -euo pipefail
cd "$(dirname "$0")"

APP="PDF-мастерская.app"
RUN_TESTS=1
INSTALL=1
for a in "$@"; do
  case "$a" in
    --no-tests) RUN_TESTS=0 ;;
    --no-install) INSTALL=0 ;;
  esac
done

# Python под Apple Silicon (arm64): python.org 3.12+ или Homebrew /opt/homebrew
pick_python() {
  for p in /Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14 \
           /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 \
           /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3; do
    if [ -x "$p" ] && [ "$(arch -arm64 "$p" -c 'import platform;print(platform.machine())' 2>/dev/null)" = "arm64" ]; then
      echo "$p"; return
    fi
  done
  echo "Не найден Python 3.12+ для arm64" >&2; exit 1
}

if [ ! -x .venv/bin/python ]; then
  PY=$(pick_python)
  echo "==> Создаю окружение (.venv) на $PY"
  arch -arm64 "$PY" -m venv .venv
fi
echo "==> Зависимости"
arch -arm64 .venv/bin/python -m pip install -q --upgrade pip
arch -arm64 .venv/bin/python -m pip install -q -r requirements.txt

echo "==> Иконка"
arch -arm64 .venv/bin/python packaging/make_icon.py

if [ "$RUN_TESTS" = 1 ]; then
  echo "==> Автотесты"
  if ! command -v pdftotext >/dev/null && [ ! -x /opt/homebrew/bin/pdftotext ]; then
    echo "   (для тестов нужен pdftotext: brew install poppler)"
  fi
  PATH="/opt/homebrew/bin:$PATH" QT_QPA_PLATFORM=offscreen arch -arm64 .venv/bin/python -m pytest -q tests
fi

echo "==> Сборка .app"
rm -rf "dist/$APP" dist/PDFMaster
arch -arm64 .venv/bin/pyinstaller --noconfirm --log-level WARN --distpath dist --workpath build/pyinstaller \
  packaging/pdfmaster.spec
# подпись «ad hoc» (обязательна для arm64)
codesign --force --deep --sign - "dist/$APP" 2>/dev/null
echo "   dist/$APP ($(du -sh "dist/$APP" | cut -f1))"

if [ "$INSTALL" = 1 ]; then
  echo "==> Установка в /Applications"
  if pgrep -f "/Applications/$APP/Contents/MacOS/PDFMaster" >/dev/null; then
    echo "   Программа запущена — закройте её и запустите сборку снова." >&2; exit 1
  fi
  rm -rf "/Applications/$APP"
  ditto "dist/$APP" "/Applications/$APP"
  # обновить регистрацию (иконка, «Открыть с помощью»)
  LSR=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
  "$LSR" -u "$PWD/dist/$APP" 2>/dev/null || true   # чтобы в «Открыть с помощью» не было копии из dist
  "$LSR" -f "/Applications/$APP" || true
  # копию в dist/ убираем, иначе Spotlight снова добавит её в «Открыть с помощью»
  rm -rf "dist/$APP"
  echo "   Готово: /Applications/$APP"
fi
