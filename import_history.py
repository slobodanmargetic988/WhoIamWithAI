#!/usr/bin/env python3
"""Convenience entry point: python3 import_history.py [import options]."""
import sys
from ai_archive.__main__ import main

if __name__ == '__main__':
    raise SystemExit(main(['import', *sys.argv[1:]]))
