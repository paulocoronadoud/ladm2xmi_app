#!/usr/bin/env python3
"""Lanzador. Ejecutar con  python ladm_uml_a_xmi.py  (abre la interfaz gráfica)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ladm2xmi.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
