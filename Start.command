#!/bin/zsh
cd -- "${0:A:h}" || exit 1
python3 -m ai_archive serve --port 8765 --open
