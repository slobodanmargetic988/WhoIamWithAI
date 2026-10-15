#!/bin/sh
cd -- "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Install Python 3.11 or later from https://www.python.org/downloads/macos/'
    printf '%s\n' 'Then open Start.command again. No pip packages are needed.'
    read -r answer
    exit 1
fi
python3 start.py "$@"
launcher_result=$?
if [ "$launcher_result" -ne 0 ] && [ "$launcher_result" -ne 130 ]; then
    printf '%s\n' 'Press Enter to close this window.'
    read -r answer
fi
exit "$launcher_result"
