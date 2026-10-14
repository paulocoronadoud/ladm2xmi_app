"""Registro del proceso de conversión y generacion de reportes (HTML, CSV, JSON, TXT)."""
import csv
import html
import json
import os
from collections import Counter, OrderedDict

STATUSES = ("converted", "degraded", "lost", "excluded")
STATUS_LABEL = {
    "converted": "Convertido",
    "degraded": "Convertido con pérdida parcial",
    "lost": "No convertido (perdido)",
    "excluded": "Excluido por la selección",
}

# Descripcion legible del tipo de elemento de origen
KIND_LABEL = OrderedDict([
    ("UmlModel", "Modelo raíz"),
    ("UmlPackage", "Paquete UML"),
    ("INTERLIS2Def", "Archivo .ili (contenedor)"),
    ("ModelDef", "Modelo INTERLIS"),
    ("TopicDef", "Tema (TOPIC)"),
    ("ClassDef", "Clase o estructura"),
    ("AttributeDef", "Atributo"),
    ("AssociationDef", "Asociacion"),
    ("RoleDef", "Rol de asociación"),
    ("DomainDef", "Dominio"),
    ("EnumElement", "Elemento de enumeracion"),
    ("ClassExtends", "Generalización de clases"),
    ("DomainExtends", "Extensión de dominio"),
    ("TopicExtends", "Extensión de tema"),
    ("UmlUsage", "Dependencia (uso)"),
    ("IliImport", "Importación de modelo"),
    ("UnitDef", "Unidad"),
    ("FunctionDef", "Funcion"),
    ("LineFormTypeDef", "Forma de línea"),
    ("MetaDataUseDef", "Uso de metadatos"),
    ("ParameterDef", "Parametro"),
    ("UmlTaggedValue", "Valor etiquetado"),
    ("Diagram", "Diagrama"),
    ("Note", "Nota de diagrama"),
    ("Presentacion", "Elementos gráficos de diagramas"),
])


class Item:
    __slots__ = ("kind", "status", "name", "path", "tid", "reason")

    def __init__(self, kind, status, name, path, tid, reason):
        self.kind = kind
        self.status = status
        self.name = name
        self.path = path
        self.tid = tid
        self.reason = reason

    def as_dict(self):
        return {
            "tipo": self.kind,
            "estado": self.status,
            "nombre": self.name,
            "ruta": self.path,
            "tid": self.tid,
            "motivo": self.reason,
        }


class Report:
    def __init__(self, cfg):
        self.cfg = cfg
        self.meta = OrderedDict()
        self.stats = OrderedDict()           # kind -> Counter
        self.items = []
        self.outputs = Counter()             # elementos UML generados
        self.validation = OrderedDict()
        self.notes = []
        self.diagrams = []
        self.source_counts = Counter()       # elementos por tipo en el archivo
        self.presentation_counts = Counter()
        self.ref_sources = []                # reservado

    # --- registro ------------------------------------------------------
    def _count(self, kind, status, n=1):
        self.stats.setdefault(kind, Counter())[status] += n

    def ok(self, kind, n=1, name=None, path=None, tid=None, reason="Convertido sin pérdida"):
        self._count(kind, "converted", n)
        if self.cfg.list_converted and name is not None:
            self.items.append(Item(kind, "converted", name, path or "", tid or "", reason))

    def degrade(self, kind, name, path, tid, reason):
        self._count(kind, "degraded")
        self.items.append(Item(kind, "degraded", name, path or "", tid or "", reason))

    def lose(self, kind, name, path, tid, reason):
        self._count(kind, "lost")
        self.items.append(Item(kind, "lost", name, path or "", tid or "", reason))

    def exclude(self, kind, name=None, path=None, tid=None, reason="", n=1, listed=True):
        self._count(kind, "excluded", n)
        if listed and name is not None:
            self.items.append(Item(kind, "excluded", name, path or "", tid or "", reason))

    def reclassify(self, kind, old, new):
        c = self.stats.setdefault(kind, Counter())
        if c[old] > 0:
            c[old] -= 1
            c[new] += 1

    def note(self, text):
        self.notes.append(text)

    # --- consultas -----------------------------------------------------
    def total(self, status):
        """Total de elementos del modelo (no cuenta las formas gráficas de los diagramas)."""
        return sum(c[status] for k, c in self.stats.items() if k != "Presentacion")

    def graphic_total(self):
        return self.stats.get("Presentacion", Counter())["lost"]

    def kind_total(self, kind):
        c = self.stats.get(kind, Counter())
        return sum(c[s] for s in STATUSES)

    def unaccounted(self):
        res = {}
        for kind, n in self.source_counts.items():
            if kind in ("Presentacion",):
                continue
            diff = n - self.kind_total(kind)
            if diff:
                res[kind] = diff
        return res

    def ordered_kinds(self):
        keys = list(self.stats.keys())
        order = {k: i for i, k in enumerate(KIND_LABEL.keys())}
        keys.sort(key=lambda k: order.get(k, 999))
        return keys

    # --- salida --------------------------------------------------------
    def write_all(self, base_path):
        """base_path sin extensión. Devuelve lista de archivos escritos."""
        written = []
        cfg = self.cfg
        if cfg.report_html:
            p = base_path + ".html"
            self.write_html(p)
            written.append(p)
        if cfg.report_csv:
            p = base_path + "_detalle.csv"
            self.write_csv(p)
            written.append(p)
            p2 = base_path + "_resumen.csv"
            self.write_summary_csv(p2)
            written.append(p2)
        if cfg.report_json:
            p = base_path + ".json"
            self.write_json(p)
            written.append(p)
        if cfg.report_txt:
            p = base_path + ".txt"
            self.write_txt(p)
            written.append(p)
        return written

    def write_csv(self, path):
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["Tipo de elemento", "Estado", "Nombre", "Ruta", "TID", "Motivo"])
            for it in self.items:
                w.writerow([it.kind, STATUS_LABEL[it.status], it.name, it.path, it.tid, it.reason])

    def write_summary_csv(self, path):
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["Tipo de elemento", "Descripcion", "En el archivo", "Convertidos",
                        "Con pérdida parcial", "Perdidos", "Excluidos"])
            for k in self.ordered_kinds():
                c = self.stats[k]
                w.writerow([k, KIND_LABEL.get(k, k), self.source_counts.get(k, self.kind_total(k)),
                            c["converted"], c["degraded"], c["lost"], c["excluded"]])

    def write_json(self, path):
        data = {
            "meta": self.meta,
            "opciones": self.cfg.to_dict(),
            "resumen": {k: dict(v) for k, v in self.stats.items()},
            "generados": dict(self.outputs),
            "validacion": self.validation,
            "avisos": self.notes,
            "diagramas": self.diagrams,
            "elementos": [i.as_dict() for i in self.items],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def write_txt(self, path):
        L = []
        L.append("REPORTE DE CONVERSION UML/INTERLIS a XMI")
        L.append("=" * 60)
        for k, v in self.meta.items():
            L.append("%s  %s" % (k.ljust(28), v))
        L.append("")
        L.append("RESUMEN POR TIPO DE ELEMENTO")
        L.append("-" * 60)
        L.append("%-18s %8s %8s %8s %8s %8s" % ("Tipo", "Archivo", "Conv.", "Parcial", "Perdido", "Excl."))
        for k in self.ordered_kinds():
            c = self.stats[k]
            L.append("%-18s %8d %8d %8d %8d %8d" % (k, self.source_counts.get(k, self.kind_total(k)),
                                                    c["converted"], c["degraded"], c["lost"], c["excluded"]))
        L.append("")
        L.append("ELEMENTOS GENERADOS EN EL XMI")
        for k, v in sorted(self.outputs.items()):
            L.append("  %-28s %d" % (k, v))
        L.append("")
        L.append("VALIDACION")
        for k, v in self.validation.items():
            L.append("  %s  %s" % (k, v))
        if self.notes:
            L.append("")
            L.append("AVISOS")
            for n in self.notes:
                L.append("  - " + n)
        for st in ("lost", "degraded"):
            sel = [i for i in self.items if i.status == st]
            if not sel:
                continue
            L.append("")
            L.append(STATUS_LABEL[st].upper() + " (%d)" % len(sel))
            L.append("-" * 60)
            for it in sel:
                L.append("[%s] %s | %s | TID %s | %s" % (it.kind, it.name, it.path, it.tid, it.reason))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(L) + "\n")

    # --- HTML ----------------------------------------------------------
    def write_html(self, path):
        e = html.escape
        cfg = self.cfg
        maxn = max(10, int(cfg.html_max_items or 500))
        parts = []
        parts.append("""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reporte de conversión UML a XMI</title>
<style>
:root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--line:#dcdcd4;--card:#fff;--ok:#2e7d32;--deg:#b26a00;--lost:#b3261e;--exc:#546e7a;--acc:#1f4e79}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecece6;--mut:#a3a39b;--line:#34342f;--card:#1f1f1c;--ok:#7fcf84;--deg:#f0b35a;--lost:#ff8a80;--exc:#9db2bd;--acc:#8ab8e6}}
*{box-sizing:border-box}body{margin:0;padding:24px 16px 64px;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.sub{color:var(--mut);margin:0 0 20px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}.card b{display:block;font-size:24px;font-variant-numeric:tabular-nums}
.card span{color:var(--mut);font-size:13px}.ok b{color:var(--ok)}.deg b{color:var(--deg)}.lost b{color:var(--lost)}.exc b{color:var(--exc)}
table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;font-size:14px}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}th{background:var(--bg);font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}tr:last-child td{border-bottom:0}.wrap{overflow-x:auto}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:8px 0;padding:0 12px}summary{cursor:pointer;padding:10px 0;font-weight:600}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;border:1px solid var(--line);color:var(--mut)}
.s-lost{color:var(--lost)}.s-degraded{color:var(--deg)}.s-excluded{color:var(--exc)}.s-converted{color:var(--ok)}
code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px}.warn{border-left:4px solid var(--deg);background:var(--card);padding:8px 12px;margin:6px 0;border-radius:4px}
.bad{border-left-color:var(--lost)}.good{border-left-color:var(--ok)}td.r{word-break:break-word}
</style></head><body><main>""")
        parts.append("<h1>Reporte de conversión UML a XMI</h1>")
        parts.append("<p class='sub'>%s</p>" % e(str(self.meta.get("Archivo de origen", ""))))

        # tarjetas
        parts.append("<div class='cards'>")
        for st, cls in (("converted", "ok"), ("degraded", "deg"), ("lost", "lost"), ("excluded", "exc")):
            parts.append("<div class='card %s'><b>%d</b><span>%s</span></div>" % (cls, self.total(st), e(STATUS_LABEL[st])))
        parts.append("<div class='card exc'><b>%d</b><span>Formas gráficas de diagramas (no se importan)</span></div>" % self.graphic_total())
        parts.append("</div>")

        # metadatos
        parts.append("<h2>Datos de la conversión</h2><div class='wrap'><table>")
        for k, v in self.meta.items():
            parts.append("<tr><th>%s</th><td class='r'>%s</td></tr>" % (e(str(k)), e(str(v))))
        parts.append("</table></div>")

        # validacion
        parts.append("<h2>Validación del XMI generado</h2>")
        okv = self.validation.get("Resultado") == "Correcto"
        parts.append("<div class='warn %s'>%s</div>" % ("good" if okv else "bad", e(str(self.validation.get("Resultado", "Sin validar")))))
        parts.append("<div class='wrap'><table>")
        for k, v in self.validation.items():
            if k == "Resultado":
                continue
            if isinstance(v, list):
                v = "; ".join(v[:20]) + (" ..." if len(v) > 20 else "")
            parts.append("<tr><th>%s</th><td class='r'>%s</td></tr>" % (e(str(k)), e(str(v))))
        parts.append("</table></div>")

        for n in self.notes:
            parts.append("<div class='warn'>%s</div>" % e(n))

        # resumen por tipo
        parts.append("<h2>Resumen por tipo de elemento de origen</h2><div class='wrap'><table>")
        parts.append("<tr><th>Tipo</th><th>Descripción</th><th class='n'>En el archivo</th><th class='n'>Convertidos</th>"
                     "<th class='n'>Pérdida parcial</th><th class='n'>Perdidos</th><th class='n'>Excluidos</th></tr>")
        for k in self.ordered_kinds():
            c = self.stats[k]
            parts.append("<tr><td><code>%s</code></td><td>%s</td><td class='n'>%d</td><td class='n s-converted'>%d</td>"
                         "<td class='n s-degraded'>%d</td><td class='n s-lost'>%d</td><td class='n s-excluded'>%d</td></tr>" % (
                             e(k), e(KIND_LABEL.get(k, k)), self.source_counts.get(k, self.kind_total(k)),
                             c["converted"], c["degraded"], c["lost"], c["excluded"]))
        parts.append("</table></div>")
        un = self.unaccounted()
        if un:
            parts.append("<div class='warn bad'>Hay elementos del archivo sin clasificar: %s</div>" % e(
                ", ".join("%s (%d)" % kv for kv in un.items())))

        # generados
        parts.append("<h2>Elementos generados en el XMI</h2><div class='wrap'><table>")
        for k, v in sorted(self.outputs.items()):
            parts.append("<tr><th>%s</th><td class='n'>%d</td></tr>" % (e(k), v))
        parts.append("</table></div>")

        # detalle por estado
        titles = {
            "lost": "Elementos que se pierden en la conversión",
            "degraded": "Elementos convertidos con pérdida parcial de información",
            "excluded": "Elementos excluidos por la selección de contenido",
            "converted": "Elementos convertidos",
        }
        for st in ("lost", "degraded", "excluded", "converted"):
            sel = [i for i in self.items if i.status == st]
            if not sel:
                continue
            parts.append("<h2>%s (%d)</h2>" % (e(titles[st]), len(sel)))
            # agrupar por motivo y tipo
            groups = OrderedDict()
            for it in sel:
                groups.setdefault((it.kind, it.reason), []).append(it)
            for (kind, reason), its in groups.items():
                parts.append("<details%s><summary><code>%s</code> %s <span class='tag'>%d</span></summary>" % (
                    " open" if st == "lost" and len(its) <= 12 else "", e(kind), e(reason), len(its)))
                parts.append("<div class='wrap'><table><tr><th>Nombre</th><th>Ruta</th><th>TID</th></tr>")
                for it in its[:maxn]:
                    parts.append("<tr><td>%s</td><td class='r'>%s</td><td><code>%s</code></td></tr>" % (
                        e(it.name), e(it.path), e(it.tid)))
                if len(its) > maxn:
                    parts.append("<tr><td colspan='3'>... y %d más. El listado completo está en el CSV de detalle.</td></tr>" % (len(its) - maxn))
                parts.append("</table></div></details>")

        # diagramas
        if self.diagrams:
            parts.append("<h2>Diagramas del archivo de origen (no se importan)</h2>")
            parts.append("<p class='sub'>El XMI solo lleva el modelo. Los diagramas deben rearmarse en Modelio arrastrando los elementos desde el explorador de modelo.</p>")
            parts.append("<div class='wrap'><table><tr><th>Diagrama</th><th>Pertenece a</th><th class='n'>Clases</th><th class='n'>Asociaciones</th><th class='n'>Generalizaciones</th><th class='n'>Dependencias</th><th class='n'>Notas</th></tr>")
            for d in self.diagrams:
                parts.append("<tr><td>%s</td><td class='r'>%s</td><td class='n'>%d</td><td class='n'>%d</td><td class='n'>%d</td><td class='n'>%d</td><td class='n'>%d</td></tr>" % (
                    e(d["nombre"]), e(d["ruta"]), d["clases"], d["asociaciones"], d["generalizaciones"], d["dependencias"], d["notas"]))
            parts.append("</table></div>")

        # opciones
        parts.append("<h2>Opciones usadas</h2><details><summary>Ver parámetros</summary><div class='wrap'><table>")
        for k, v in self.cfg.to_dict().items():
            if k in ("excluded_tids", "included_tids"):
                v = "%d elementos" % len(v)
            parts.append("<tr><th>%s</th><td class='r'><code>%s</code></td></tr>" % (e(k), e(str(v).replace("\n", " | "))))
        parts.append("</table></div></details>")
        parts.append("</main></body></html>")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(parts))
