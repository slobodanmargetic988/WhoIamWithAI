#!/bin/sh
cd -- "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Install Python 3.11 or later, then run this launcher again.'
    printf '%s\n' 'https://www.python.org/downloads/'
    exit 1
fi
exec python3 start.py "$@"
