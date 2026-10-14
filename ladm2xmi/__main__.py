"""Punto de entrada. Sin argumentos abre la GUI. Con --cli convierte por línea de comandos."""
import argparse
import os
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ladm2xmi", description="Convierte un .uml de LADM-COL (UML/INTERLIS Editor) a XMI 2.1")
    ap.add_argument("--cli", action="store_true", help="ejecutar sin interfaz gráfica")
    ap.add_argument("entrada", nargs="?", help="archivo .uml de origen")
    ap.add_argument("-o", "--salida", help="archivo .xmi de salida")
    ap.add_argument("-c", "--config", help="archivo JSON de configuración")
    args = ap.parse_args(argv)

    if not args.cli:
        from .gui import run_gui
        run_gui(args.entrada)
        return 0

    from .config import Config
    from .converter import Converter
    cfg = Config.load(args.config) if args.config else Config()
    if args.entrada:
        cfg.input_path = args.entrada
    if args.salida:
        cfg.output_path = args.salida
    if not cfg.input_path:
        ap.error("falta el archivo de entrada")
    if not cfg.output_path:
        cfg.output_path = os.path.splitext(cfg.input_path)[0] + ".xmi"
    if not cfg.report_dir:
        cfg.report_dir = os.path.dirname(os.path.abspath(cfg.output_path))
    conv = Converter(cfg, log=print, progress=lambda p, m="": None)
    rep = conv.run()
    base = os.path.join(cfg.report_dir, os.path.splitext(os.path.basename(cfg.output_path))[0] + "_reporte")
    files = rep.write_all(base)
    print("Convertidos %d, parciales %d, perdidos %d, excluidos %d, formas gráficas %d" % (
        rep.total("converted"), rep.total("degraded"), rep.total("lost"), rep.total("excluded"), rep.graphic_total()))
    print("Validación", rep.validation.get("Resultado"))
    for f in files:
        print("Reporte", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
