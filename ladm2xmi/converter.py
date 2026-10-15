"""Motor de conversión UML/INTERLIS Editor (.uml) a XMI 2.1 (UML 2.x)."""
import os
import re
import time
from collections import Counter, namedtuple
from xml.etree import ElementTree as ET

from . import __version__, APP_NAME
from .config import Config
from .report import Report
from .umlreader import UmlFile

TypeRef = namedtuple("TypeRef", "kind value")   # kind: id | href

SEMANTIC_TAGS = (
    "UmlPackage", "INTERLIS2Def", "ModelDef", "TopicDef", "ClassDef", "AssociationDef", "DomainDef",
    "AttributeDef", "RoleDef", "EnumElement", "ClassExtends", "DomainExtends", "TopicExtends", "UmlUsage",
    "UnitDef", "FunctionDef", "LineFormTypeDef", "MetaDataUseDef", "ParameterDef", "IliImport", "UmlTaggedValue",
)

NO_UML_EQUIVALENT = {
    "UnitDef": "Las unidades de medida INTERLIS no tienen equivalente en UML",
    "FunctionDef": "Las funciones INTERLIS no tienen equivalente en UML",
    "LineFormTypeDef": "Las formas de línea INTERLIS no tienen equivalente en UML",
    "MetaDataUseDef": "Los usos de metadatos INTERLIS no tienen equivalente en UML",
    "ParameterDef": "Los parámetros INTERLIS no tienen equivalente en UML",
}

ILI_TYPE_NAMES = {
    "IliPolyline": "POLYLINE",
    "IndividualSurface": "SURFACE",
    "CoordinateType": "COORD",
    "HorizAlignment": "HALIGNMENT",
    "VertAlignment": "VALIGNMENT",
}

XMI_NS = {"2.1": "http://schema.omg.org/spec/XMI/2.1", "2.5.1": "http://www.omg.org/spec/XMI/20131001"}
UML_NS = {
    ("eclipse", "2.1"): "http://www.eclipse.org/uml2/3.0.0/UML",
    ("eclipse", "2.5.1"): "http://www.eclipse.org/uml2/5.0.0/UML",
    ("omg", "2.1"): "http://schema.omg.org/spec/UML/2.1.2",
    ("omg", "2.5.1"): "http://www.omg.org/spec/UML/20131001",
}
PRIM_HREF = {
    "eclipse": "pathmap://UML_LIBRARIES/UMLPrimitiveTypes.library.uml#%s",
    "omg": "http://schema.omg.org/spec/UML/2.1.2/uml.xml#%s",
}
UML_STD_PRIMS = ("String", "Integer", "Boolean", "Real")


class Cancelled(Exception):
    pass


def _clean_ws(s):
    return re.sub(r"\s+", " ", s or "").strip()


class Converter:
    def __init__(self, cfg, log=None, progress=None, cancel_check=None, uml=None):
        self.cfg = cfg
        self.log = log or (lambda m: None)
        self.progress = progress or (lambda p, m="": None)
        self.cancel_check = cancel_check or (lambda: False)
        self.uml = uml
        self.langs = tuple(x.strip() for x in (cfg.language or "es").split(",") if x.strip()) or ("es",)
        self.rep = Report(cfg)

    # =================================================================
    # Utilidades XML
    # =================================================================
    def q(self, ns, name):
        return "{%s}%s" % (ns, name)

    def X(self, name):
        return "{%s}%s" % (self.xmi_ns, name)

    def U(self, name):
        return "{%s}%s" % (self.uml_ns, name)

    def mid(self, tid):
        return "%s%s" % (self.cfg.id_prefix, tid)

    def gid(self):
        self._gid += 1
        return "%sg%d" % (self.cfg.id_prefix, self._gid)

    def _check_cancel(self):
        if self.cancel_check():
            raise Cancelled()

    def sanitize(self, s):
        if not self.cfg.sanitize_names:
            return s
        s2 = re.sub(r"[^0-9A-Za-z_À-ɏ]", "_", s)
        if s2 and s2[0].isdigit():
            s2 = "_" + s2
        return s2

    def nm(self, el):
        n = self.uml.name(el, self.cfg.name_source, self.langs)
        if n is None or not n.strip():
            n = "%s_%s" % (el.tag, el.tid)
            self.rep.note("El elemento %s (TID %s) no tiene nombre. Se uso el nombre generado %s." % (el.tag, el.tid, n))
        return self.sanitize(n.strip())

    def mk(self, parent, tag, attrib=None, uml_type=None, xid=None):
        node = ET.SubElement(parent, tag)
        self._parent_of[node] = parent
        if uml_type:
            node.set(self.X("type"), uml_type)
            self.rep.outputs[uml_type] += 1
        if xid:
            node.set(self.X("id"), xid)
        for k, v in (attrib or {}).items():
            if v is not None:
                node.set(k, v)
        return node

    def add_comment(self, owner_node, owner_id, text):
        text = (text or "").strip()
        if not text:
            return None
        c = ET.SubElement(owner_node, "ownedComment")
        c.set(self.X("type"), "uml:Comment")
        c.set(self.X("id"), self.gid())
        c.set("annotatedElement", owner_id)
        if self.cfg.dialect == "omg":
            c.set("body", text)
        else:
            b = ET.SubElement(c, "body")
            b.text = text
        self.rep.outputs["uml:Comment"] += 1
        return c

    def doc_comment(self, el, node, xid):
        if not self.cfg.emit_documentation:
            return
        d = self.uml.doc(el, self.langs)
        if d:
            self.add_comment(node, xid, d)

    def tagged_comment(self, el, node, xid, extra=None):
        lines = []
        tvs = self.uml.tagged_values(el, self.langs)
        for tid in el.fl("TaggedValue"):
            self.visited.add(tid)
        if self.cfg.emit_tagged_values:
            for k, v in tvs:
                lines.append("%s = %s" % (k, v))
        elif tvs:
            for tid in el.fl("TaggedValue"):
                tv = self.uml.get(tid)
                if tv is not None:
                    self.rep.lose("UmlTaggedValue", self._tv_label(tv), "", tid,
                                  "La conversión de valores etiquetados está desactivada")
        else:
            pass
        if self.cfg.emit_tagged_values and tvs:
            for tid in el.fl("TaggedValue"):
                if self.uml.get(tid) is not None:
                    self.rep.ok("UmlTaggedValue")
        if extra:
            lines = list(extra) + lines
        if lines:
            self.add_comment(node, xid, "[INTERLIS] " + " | ".join(lines))

    def _tv_label(self, tv):
        return "%s = %s" % (self.uml.nls(tv.f("Name"), self.langs) or "?", tv.f("DataValue") or "")

    # =================================================================
    # Ejecucion
    # =================================================================
    def run(self):
        cfg = self.cfg
        t0 = time.time()
        self._gid = 0
        self.visited = set()
        self.sel = set()
        self.xml_of = {}          # tid -> nodo XML
        self.id_of = {}           # tid -> xmi:id
        self.kind_of = {}         # tid -> tag origen
        self.path_of = {}
        self.pkg_nodes = []       # (nodo, tid, tag, ruta)
        self.pending_assoc = []
        self.pending_ext = []
        self.prim_ids = {}
        self.ili_ids = {}
        self.inline_domains = set()
        self.enum_names_used = Counter()
        self.child_of_ext = {}
        self.dom_enum_nodes = {}
        self._parent_of = {}

        # Lista de elementos a añadir como auxiliares
        self.generalizations = []  # Generalization (clase extiende clase)
        self.imports = []          # IliImport (importación entre modelos INTERLIS)

        self.xmi_ns = XMI_NS.get(cfg.xmi_version, XMI_NS["2.1"])
        key = (cfg.dialect, cfg.xmi_version if cfg.xmi_version in XMI_NS else "2.1")
        self.uml_ns = UML_NS.get(key, UML_NS[("eclipse", "2.1")])
        ET.register_namespace("xmi", self.xmi_ns)
        ET.register_namespace("uml", self.uml_ns)
        self.prim_names = self.cfg.parse_map(cfg.prim_names)
        self.domain_map = self.cfg.parse_map(cfg.domain_map)
        self.excl_names = cfg.excluded_name_set()
        self.excl_tids = set(cfg.excluded_tids or [])
        self.incl_tids = set(cfg.included_tids or [])

        if self.uml is None:
            self.progress(2, "Leyendo el archivo .uml")
            self.log("Leyendo %s" % cfg.input_path)
            self.uml = UmlFile(cfg.input_path)
        uml = self.uml
        self.log("Archivo leido, %d elementos de primer nivel" % len(uml.by_tid))
        self._check_cancel()

        self._index_source()
        root_el = uml.get("0")
        if root_el is None or root_el.tag != "UmlModel":
            raise ValueError("No se encontro el elemento UmlModel (TID 0) en el archivo.")
        self.visited.add("0")
        stem = os.path.splitext(os.path.basename(cfg.input_path or "modelo"))[0]
        model_name = self.sanitize(cfg.model_name.strip() or stem)

        # raiz del XMI
        if cfg.dialect == "omg":
            xroot = ET.Element(self.X("XMI"))
            xroot.set(self.X("version"), cfg.xmi_version)
            doc = ET.SubElement(xroot, self.X("Documentation"))
            doc.set("exporter", APP_NAME)
            doc.set("exporterVersion", __version__)
            model = ET.SubElement(xroot, self.U("Model"))
            model.set(self.X("type"), "uml:Model")
        else:
            xroot = ET.Element(self.U("Model"))
            xroot.set(self.X("version"), cfg.xmi_version)
            model = xroot
        model.set(self.X("id"), self.mid("0"))
        model.set("name", model_name)
        self.model = model
        self.model_id = self.mid("0")
        self.rep.outputs["uml:Model"] += 1
        self.xml_of["0"] = model
        self.id_of["0"] = self.model_id
        self.kind_of["0"] = "UmlModel"
        self.path_of["0"] = model_name
        self.rep.ok("UmlModel")

        # comentarios del modelo
        self.doc_comment(root_el, model, self.model_id)
        extra = []
        if cfg.emit_source_info:
            v = root_el.f("Version")
            a = root_el.f("Author")
            if a:
                extra.append("Autor %s" % a)
            if v:
                extra.append("Versión del modelo %s" % v)
            extra.append("Convertido con %s %s" % (APP_NAME, __version__))
        self.tagged_comment(root_el, model, self.model_id, extra=extra)

        # fase 0 y 1
        self.progress(8, "Planificando la selección")
        self.plan(root_el)
        self.progress(15, "Convirtiendo paquetes, clases y dominios")
        self.walk(root_el, model, model_name, None)
        self._check_cancel()
        self.progress(55, "Convirtiendo generalizaciones")
        self.do_generalizations()
        self.progress(65, "Convirtiendo asociaciones")
        self.do_associations()
        self._check_cancel()
        self.progress(75, "Convirtiendo dependencias")
        self.do_usages()
        self.progress(80, "Registrando diagramas y elementos huerfanos")
        self.do_notes_and_diagrams()
        self.do_orphans()
        self.prune_empty_packages()
        self.attach_aux_packages()

        # escritura
        self.progress(88, "Escribiendo el XMI")
        if cfg.pretty_print:
            try:
                ET.indent(xroot, space="  ")
            except AttributeError:
                pass
        out = cfg.output_path
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        ET.ElementTree(xroot).write(out, encoding="utf-8", xml_declaration=True)
        self.log("XMI escrito en %s" % out)

        self.progress(94, "Validando el XMI")
        self.validate(out)

        t1 = time.time()
        r = self.rep
        r.meta["Archivo de origen"] = cfg.input_path
        r.meta["Tamaño del origen"] = "%.1f KB" % (uml.size / 1024.0)
        r.meta["Versión del modelo de origen"] = root_el.f("Version") or ""
        r.meta["Autor del modelo de origen"] = root_el.f("Author") or ""
        r.meta["Archivo XMI generado"] = out
        r.meta["Formato"] = "XMI %s, dialecto %s" % (cfg.xmi_version, "Eclipse UML2" if cfg.dialect == "eclipse" else "OMG UML 2.1")
        r.meta["Tamaño del XMI"] = "%.1f KB" % (os.path.getsize(out) / 1024.0)
        r.meta["Fecha"] = time.strftime("%Y-%m-%d %H:%M:%S")
        r.meta["Duración"] = "%.1f s" % (t1 - t0)
        r.meta["Convertidor"] = "%s %s" % (APP_NAME, __version__)
        self.progress(100, "Listo")
        return r

    # =================================================================
    # Indexacion del origen
    # =================================================================
    def _index_source(self):
        uml = self.uml
        rep = self.rep
        for el in uml.order:
            if el.is_presentation:
                rep.presentation_counts[el.tag] += 1
            elif el.tag in SEMANTIC_TAGS or el.tag == "UmlModel":
                rep.source_counts[el.tag] += 1
        rep.source_counts["Presentacion"] = sum(
            n for t, n in rep.presentation_counts.items() if t not in ("Diagram", "Note"))
        rep.source_counts["Diagram"] = rep.presentation_counts.get("Diagram", 0)
        rep.source_counts["Note"] = rep.presentation_counts.get("Note", 0)
        # mapa generalizacion -> hijo
        for el in uml.order:
            for g in el.fl("Generalization"):
                self.child_of_ext[g] = el.tid

    def is_excluded(self, el):
        if el.tid in self.excl_tids:
            return True
        if el.tid in self.incl_tids:
            return False
        if el.tag in ("ModelDef", "UmlPackage", "TopicDef"):
            nm = self.uml.name(el, "DefLangName", self.langs)
            if nm and nm in self.excl_names:
                return True
        return False

    # =================================================================
    # Planificacion (que se convierte)
    # =================================================================
    def plan(self, container):
        for ch in self.uml.children(container):
            t = ch.tag
            if t == "INTERLIS2Def":
                self.plan(ch)
            elif t in ("UmlPackage", "ModelDef", "TopicDef"):
                if self.is_excluded(ch):
                    continue
                self.sel.add(ch.tid)
                self.plan(ch)
            elif t in ("ClassDef", "AssociationDef", "DomainDef"):
                self.sel.add(ch.tid)
                if t == "DomainDef" and self.cfg.domain_mode == "inline":
                    tt = self.uml.get(ch.f("Type"))
                    if tt is None or tt.tag != "Enumeration":
                        self.inline_domains.add(ch.tid)

    # =================================================================
    # Recorrido y emision
    # =================================================================
    def _sorted(self, kids):
        if not self.cfg.sort_alphabetically:
            return kids
        order = {"UmlPackage": 0, "INTERLIS2Def": 0, "ModelDef": 1, "TopicDef": 2, "DomainDef": 3,
                 "ClassDef": 4, "AssociationDef": 5}
        return sorted(kids, key=lambda e: (order.get(e.tag, 9), (self.uml.name(e, "DefLangName", self.langs) or "").lower()))

    def walk(self, container, parent_node, path, src_file):
        uml = self.uml
        kids = self._sorted(uml.children(container))
        for ch in kids:
            self._check_cancel()
            t = ch.tag
            self.visited.add(ch.tid)
            if t == "INTERLIS2Def":
                fname = uml.name(ch, "DefLangName", self.langs) or ""
                if self.cfg.flatten_ili_files:
                    self.rep.ok("INTERLIS2Def")
                    self.walk(ch, parent_node, path, fname)
                else:
                    node = self.emit_package(ch, parent_node, path, "Archivo INTERLIS")
                    self.walk(ch, node, path + "::" + self.nm(ch), fname)
            elif t == "UmlPackage":
                if self.is_excluded(ch):
                    self.exclude_subtree(ch, path, "El paquete fue excluido de la selección")
                    continue
                node = self.emit_package(ch, parent_node, path, "Paquete")
                self.walk(ch, node, path + "::" + self.nm(ch), src_file)
            elif t == "ModelDef":
                if self.is_excluded(ch):
                    self.exclude_subtree(ch, path, "El modelo fue excluido de la selección")
                    continue
                extra = []
                if self.cfg.emit_source_info:
                    if src_file and src_file != "<predefined>":
                        extra.append("Archivo .ili de origen %s" % src_file)
                    for lab, fld in (("VERSION", "Version"), ("Comentario de versión", "VersionComment"), ("ISSUER", "IssuerURI")):
                        v = self.uml.nls(ch.f(fld), self.langs)
                        if v:
                            extra.append("%s %s" % (lab, _clean_ws(v)))
                    if ch.f("BaseLanguage"):
                        extra.append("Idioma base %s" % ch.f("BaseLanguage"))
                node = self.emit_package(ch, parent_node, path, "Modelo INTERLIS", extra=extra)
                self.walk(ch, node, path + "::" + self.nm(ch), src_file)
            elif t == "TopicDef":
                if self.is_excluded(ch):
                    self.exclude_subtree(ch, path, "El tema fue excluido de la selección")
                    continue
                node = self.emit_package(ch, parent_node, path, "Tema INTERLIS")
                self.walk(ch, node, path + "::" + self.nm(ch), src_file)
            elif t == "ClassDef":
                self.emit_class(ch, parent_node, path)
            elif t == "DomainDef":
                self.emit_domain(ch, parent_node, path)
            elif t == "AssociationDef":
                self.pending_assoc.append((ch, parent_node, path))
            elif t in NO_UML_EQUIVALENT:
                self.rep.lose(t, self.nm(ch), path, ch.tid, NO_UML_EQUIVALENT[t])
            else:
                self.rep.lose(t, uml.name(ch) or ch.tag, path, ch.tid, "Tipo de elemento no soportado por el convertidor")

    # --- paquetes ------------------------------------------------------
    def emit_package(self, el, parent_node, path, label, extra=None):
        xid = self.mid(el.tid)
        name = self.nm(el)
        node = self.mk(parent_node, "packagedElement", {"name": name}, "uml:Package", xid)
        self.xml_of[el.tid] = node
        self.id_of[el.tid] = xid
        self.kind_of[el.tid] = el.tag
        self.path_of[el.tid] = path + "::" + name
        self.pkg_nodes.append((node, el.tid, el.tag, path + "::" + name, parent_node))
        self.doc_comment(el, node, xid)
        ex = list(extra or [])
        if el.tag in ("ModelDef", "TopicDef") and self.cfg.emit_source_info:
            ex.insert(0, label)
        # TopicDef extiende otro tema
        if el.tag == "TopicDef":
            for g in el.fl("Specialization"):
                pass
        self.tagged_comment(el, node, xid, extra=ex if ex else None)
        self.rep.ok(el.tag)
        return node

    # --- exclusion -----------------------------------------------------
    def exclude_subtree(self, el, path, reason):
        """Marca como excluido un elemento y todo lo que contiene."""
        uml = self.uml
        rep = self.rep
        listed_top = True
        stack = [(el, path, True)]
        counts = Counter()
        while stack:
            cur, cpath, top = stack.pop()
            if cur.tid in self.visited and not top:
                continue
            self.visited.add(cur.tid)
            nm = uml.name(cur, "DefLangName", self.langs) or cur.tag
            if top:
                rep.exclude(cur.tag, nm, cpath, cur.tid, reason, listed=True)
            else:
                counts[cur.tag] += 1
                rep.exclude(cur.tag, nm, cpath, cur.tid, "Contenido en un elemento excluido",
                            listed=self.cfg.list_excluded)
            sub_path = cpath + "::" + nm
            for ch in uml.children(cur):
                if ch.tag in ("INTERLIS2Def",):
                    self.visited.add(ch.tid)
                    rep.exclude(ch.tag, listed=False)
                    for g in uml.children(ch):
                        stack.append((g, sub_path, False))
                else:
                    stack.append((ch, sub_path, False))
            for tid in cur.fl("Feature"):
                f = uml.get(tid)
                if f is not None and f.tid not in self.visited:
                    stack.append((f, sub_path, False))
            for tid in cur.fl("Connection"):
                f = uml.get(tid)
                if f is not None and f.tid not in self.visited:
                    stack.append((f, sub_path, False))
            for tid in cur.fl("Generalization"):
                f = uml.get(tid)
                if f is not None and f.tid not in self.visited:
                    stack.append((f, sub_path, False))
            for tid in cur.fl("TaggedValue"):
                f = uml.get(tid)
                if f is not None and f.tid not in self.visited:
                    self.visited.add(tid)
                    rep.exclude("UmlTaggedValue", listed=False)
            if cur.tag == "DomainDef":
                tt = uml.get(cur.f("Type"))
                if tt is not None and tt.tag == "Enumeration":
                    self._exclude_enum(tt)
        if counts:
            rep.note("Excluido %s (%s). Contenido omitido %s." % (
                nm if False else uml.name(el) or el.tag, reason,
                ", ".join("%s %d" % kv for kv in sorted(counts.items()))))

    def _exclude_enum(self, enum_el):
        for tid in enum_el.fl("EnumElement"):
            ee = self.uml.get(tid)
            if ee is None or ee.tid in self.visited:
                continue
            self.visited.add(ee.tid)
            self.rep.exclude("EnumElement", listed=False)
            for tt in ee.fl("TaggedValue"):
                if tt not in self.visited and self.uml.get(tt) is not None:
                    self.visited.add(tt)
                    self.rep.exclude("UmlTaggedValue", listed=False)
            ch = self.uml.get(ee.f("Child"))
            if ch is not None:
                self._exclude_enum(ch)

    # --- clases --------------------------------------------------------
    def emit_class(self, el, parent_node, path):
        cfg = self.cfg
        uml = self.uml
        xid = self.mid(el.tid)
        name = self.nm(el)
        kind = el.f("Kind")
        is_struct = kind == "2"
        uml_type = "uml:DataType" if (is_struct and cfg.structure_as == "DataType") else "uml:Class"
        attrib = {"name": name}
        if el.b("Abstract"):
            attrib["isAbstract"] = "true"
        node = self.mk(parent_node, "packagedElement", attrib, uml_type, xid)
        self.xml_of[el.tid] = node
        self.id_of[el.tid] = xid
        self.kind_of[el.tid] = "ClassDef"
        qpath = path + "::" + name
        self.path_of[el.tid] = qpath
        self.doc_comment(el, node, xid)
        extra = []
        if is_struct:
            extra.append("STRUCTURE")
        elif kind not in ("1", None):
            extra.append("Clase de tipo INTERLIS %s" % kind)
        if el.b("Active"):
            extra.append("Active")
        if el.b("HasNoStableOid"):
            extra.append("Sin OID estable")
        self.tagged_comment(el, node, xid, extra=extra if cfg.emit_untranslatable else None)
        if kind not in ("1", "2", None):
            self.rep.degrade("ClassDef", name, path, el.tid, "Tipo de clase INTERLIS desconocido (%s), se generó como clase" % kind)
        else:
            self.rep.ok("ClassDef", name=name, path=path, tid=el.tid)
        # atributos
        for tid in el.fl("Feature"):
            f = uml.get(tid)
            if f is None:
                continue
            self.visited.add(tid)
            if f.tag == "AttributeDef":
                self.emit_attribute(f, node, name, parent_node, qpath)
            else:
                self.rep.lose(f.tag, uml.name(f) or f.tag, qpath, tid, "Tipo de característica no soportado")

    # --- atributos -----------------------------------------------------
    def emit_attribute(self, attr, owner_node, owner_name, enum_parent, owner_path, in_assoc=False):
        cfg = self.cfg
        uml = self.uml
        xid = self.mid(attr.tid)
        name = self.nm(attr)
        tref, details, deg = self.resolve_attr_type(attr, owner_name, name, enum_parent, owner_path)
        prop = self.mk(owner_node, "ownedAttribute", {"name": name}, "uml:Property", xid)
        self.set_type(prop, tref)
        lo, up = uml.multiplicity(attr.f("Multiplicity"))
        if attr.b("Mandatory") and lo == 0 and cfg.mandatory_forces_lower_one:
            lo = 1
        self.set_mult(prop, lo, up)
        if attr.b("PropFinal"):
            prop.set("isReadOnly", "true")
        if attr.f("OwnerScope") == "1":
            prop.set("isStatic", "true")
        self.doc_comment(attr, prop, xid)
        extra = []
        if cfg.emit_type_details and details:
            extra.append("Tipo " + "; ".join(details))
        self.tagged_comment(attr, prop, xid, extra=extra if extra else None)
        apath = owner_path
        if deg:
            self.rep.degrade("AttributeDef", name, apath, attr.tid, deg)
        else:
            if details and not cfg.emit_type_details:
                self.rep.degrade("AttributeDef", name, apath, attr.tid,
                                 "Se pierden restricciones de tipo INTERLIS (%s)" % "; ".join(details))
            else:
                self.rep.ok("AttributeDef", name=name, path=apath, tid=attr.tid)
        return prop

    def set_mult(self, prop, lo, up):
        lv = ET.SubElement(prop, "lowerValue")
        lv.set(self.X("type"), "uml:LiteralInteger")
        lv.set(self.X("id"), self.gid())
        lv.set("value", str(lo))
        uv = ET.SubElement(prop, "upperValue")
        uv.set(self.X("type"), "uml:LiteralUnlimitedNatural")
        uv.set(self.X("id"), self.gid())
        uv.set("value", "*" if up is None else str(up))

    def set_type(self, prop, tref):
        if tref is None:
            return
        if tref.kind == "id":
            prop.set("type", tref.value)
        else:
            t = ET.SubElement(prop, "type")
            t.set(self.X("type"), "uml:PrimitiveType")
            t.set("href", tref.value)

    # --- tipos ---------------------------------------------------------
    def prim(self, key):
        """TypeRef de un tipo primitivo interno (String, Integer, Real, Boolean, Date...)."""
        cfg = self.cfg
        disp = self.prim_names.get(key, key)
        if cfg.primitive_mode == "uml_href" and key in UML_STD_PRIMS:
            return TypeRef("href", PRIM_HREF[cfg.dialect] % key)
        if key not in self.prim_ids:
            self.prim_ids[key] = (self.gid(), disp)
        return TypeRef("id", self.prim_ids[key][0])

    def ili_type(self, name, details=None):
        if name not in self.ili_ids:
            self.ili_ids[name] = self.gid()
        return TypeRef("id", self.ili_ids[name])

    def numeric_info(self, num):
        uml = self.uml
        details = []
        real = False
        dims = []
        for fld in ("MinDec", "MaxDec"):
            d = uml.get(num.f(fld))
            if d is not None:
                try:
                    val = float(d.f("Value") or "0")
                except ValueError:
                    val = 0.0
                try:
                    acc = int(d.f("Accuracy") or "0")
                except ValueError:
                    acc = 0
                dims.append((val, acc))
                if acc > 0 or val != int(val):
                    real = True
        if len(dims) == 2:
            def fmt(v):
                return "%.*f" % (v[1], v[0])
            details.append("Rango %s a %s" % (fmt(dims[0]), fmt(dims[1])))
        elif not num.b("RangeDefined"):
            real = True
            details.append("Numérico sin rango definido")
        un = uml.get(num.f("UnitDef"))
        if un is not None:
            details.append("Unidad %s" % (uml.name(un) or "?"))
        return ("Real" if real else "Integer"), details

    def base_type(self, t):
        """Analiza un descriptor de tipo. Devuelve (clase, clave, detalles, motivo_degradacion)."""
        uml = self.uml
        if t is None:
            return ("prim", "String", [], "El atributo no tiene tipo definido, se usó texto")
        tag = t.tag
        if tag == "Text":
            det = []
            ml = t.f("MaxLength")
            if ml and ml != "0":
                det.append("Texto de longitud máxima %s" % ml)
            else:
                det.append("Texto sin longitud máxima")
            if t.b("Multiline"):
                det.append("multilinea")
            return ("prim", "String", det, None)
        if tag == "NumericType":
            key, det = self.numeric_info(t)
            return ("prim", key, det, None)
        if tag == "BooleanType":
            return ("prim", "Boolean", [], None)
        if tag == "OidType":
            return ("prim", "String", ["OID"], None)
        if tag == "Enumeration":
            return ("enum", None, [], None)
        if tag == "StructAttrType":
            p = uml.get(t.f("Participant"))
            return ("struct", p, [], None)
        if tag in ILI_TYPE_NAMES:
            det = []
            if tag == "CoordinateType":
                det.append("%d dimensiones" % len(t.fl("Dim")))
            return ("ili", ILI_TYPE_NAMES[tag], det, None)
        if tag == "UnknownType":
            syn = uml.nls(t.f("Syntax"), self.langs) or ""
            return ("prim", "String", ["Definición INTERLIS " + _clean_ws(syn)[:200]],
                    "Tipo INTERLIS sin equivalente (%s), se usó texto" % (_clean_ws(syn)[:80] or "sin definición"))
        return ("ili", tag, [], None)

    def resolve_attr_type(self, attr, owner_name, aname, enum_parent, owner_path):
        """Devuelve (TypeRef, detalles, motivo_degradacion)."""
        uml = self.uml
        cfg = self.cfg
        da = uml.get(attr.f("AttrType"))
        if da is None:
            return self.prim("String"), [], "El atributo no tiene tipo definido, se usó texto"
        dom_tid = da.f("DomainDef")
        if dom_tid:
            dom = uml.get(dom_tid)
            if dom is None:
                return self.prim("String"), [], "Dominio inexistente, se usó texto"
            return self.resolve_domain_ref(dom)
        t = uml.get(da.f("Direct"))
        cls, key, det, deg = self.base_type(t)
        if cls == "prim":
            return self.prim(key), det, deg
        if cls == "ili":
            return self.ili_type(key), det, deg
        if cls == "struct":
            if key is not None and key.tid in self.sel:
                return TypeRef("id", self.mid(key.tid)), det, None
            if key is None:
                return self.prim("String"), det, "Estructura sin definir, se usó texto"
            return self.prim("String"), det, "La estructura %s está fuera de la selección, se usó texto" % (uml.name(key) or "?")
        if cls == "enum":
            return self.inline_enum(t, owner_name, aname, enum_parent, owner_path), det, None
        return self.prim("String"), det, "Tipo no reconocido, se usó texto"

    def resolve_domain_ref(self, dom):
        uml = self.uml
        dname = uml.name(dom, "DefLangName", self.langs) or "?"
        if dom.tid in self.sel and dom.tid not in self.inline_domains:
            return TypeRef("id", self.mid(dom.tid)), [], None
        tt = uml.get(dom.f("Type"))
        cls, key, det, deg = self.base_type(tt)
        det = ["Dominio %s" % dname] + det
        if dom.tid in self.inline_domains:
            # el dominio se sustituye intencionalmente por su tipo base
            if cls == "prim":
                return self.prim(key), det, None
            if cls == "ili":
                return self.ili_type(key), det, None
        # dominio no convertido (excluido), intentar el mapa por nombre
        mapped = self.domain_map.get(dname)
        if mapped:
            return self.prim(mapped), ["Dominio %s" % dname], None
        if cls == "prim":
            return self.prim(key), det, "El dominio %s está fuera de la selección, se usó su tipo base" % dname
        if cls == "ili":
            return self.ili_type(key), det, "El dominio %s está fuera de la selección, se usó su tipo base" % dname
        return self.prim("String"), det, "El dominio %s está fuera de la selección y no tiene equivalente, se usó texto" % dname

    def inline_enum(self, enum_el, owner_name, aname, parent_node, owner_path):
        try:
            nm = self.cfg.inline_enum_pattern.format(clase=owner_name, atributo=aname)
        except (KeyError, IndexError, ValueError):
            nm = "%s_%s" % (owner_name, aname)
        nm = self.sanitize(nm)
        base = nm
        self.enum_names_used[(id(parent_node), nm)] += 1
        n = self.enum_names_used[(id(parent_node), nm)]
        if n > 1:
            nm = "%s_%d" % (base, n)
        xid = self.mid("e" + enum_el.tid)
        node = self.mk(parent_node, "packagedElement", {"name": nm}, "uml:Enumeration", xid)
        self.emit_literals(enum_el, node, xid, "", owner_path + "::" + nm)
        return TypeRef("id", xid)

    # --- enumeraciónes -------------------------------------------------
    def emit_literals(self, enum_el, node, enum_xid, prefix, path):
        cfg = self.cfg
        uml = self.uml
        for tid in enum_el.fl("EnumElement"):
            ee = uml.get(tid)
            if ee is None:
                continue
            self.visited.add(tid)
            self._check_cancel()
            nm = self.nm(ee)
            child = uml.get(ee.f("Child"))
            full = prefix + nm
            has_children = child is not None and child.fl("EnumElement")
            if has_children and not cfg.enum_include_nodes:
                self.rep.degrade("EnumElement", full, path, tid,
                                 "Nodo de enumeración jerárquica sin literal propio, solo se generan las hojas")
                for t2 in ee.fl("TaggedValue"):
                    self.visited.add(t2)
            else:
                xid = self.mid(tid)
                lit = self.mk(node, "ownedLiteral", {"name": full}, "uml:EnumerationLiteral", xid)
                self.doc_comment(ee, lit, xid)
                self.tagged_comment(ee, lit, xid)
                self.rep.ok("EnumElement", name=full, path=path, tid=tid)
            if has_children:
                self.emit_literals(child, node, enum_xid, full + cfg.enum_separator, path)

    # --- dominios ------------------------------------------------------
    def emit_domain(self, dom, parent_node, path):
        cfg = self.cfg
        uml = self.uml
        xid = self.mid(dom.tid)
        name = self.nm(dom)
        tt = uml.get(dom.f("Type"))
        self.kind_of[dom.tid] = "DomainDef"
        self.path_of[dom.tid] = path + "::" + name
        if dom.tid in self.inline_domains:
            self.rep.degrade("DomainDef", name, path, dom.tid,
                             "El dominio se sustituye por su tipo base en cada atributo (opción de dominios en línea)")
            for t2 in dom.fl("TaggedValue"):
                self.visited.add(t2)
            return
        attrib = {"name": name}
        if dom.b("Abstract"):
            attrib["isAbstract"] = "true"
        is_enum = tt is not None and tt.tag == "Enumeration"
        if is_enum:
            node = self.mk(parent_node, "packagedElement", attrib, "uml:Enumeration", xid)
            self.xml_of[dom.tid] = node
            self.id_of[dom.tid] = xid
            self.doc_comment(dom, node, xid)
            self.tagged_comment(dom, node, xid)
            self.emit_literals(tt, node, xid, "", path + "::" + name)
            self.rep.ok("DomainDef", name=name, path=path, tid=dom.tid)
            return
        node = self.mk(parent_node, "packagedElement", attrib, "uml:DataType", xid)
        self.xml_of[dom.tid] = node
        self.id_of[dom.tid] = xid
        self.doc_comment(dom, node, xid)
        cls, key, det, deg = self.base_type(tt)
        shown = []
        if cls == "prim":
            shown.append("Tipo base %s" % self.prim_names.get(key, key))
        elif cls == "ili":
            shown.append("Tipo base INTERLIS %s" % key)
        shown += det
        self.tagged_comment(dom, node, xid, extra=(["Dominio"] + shown) if cfg.emit_type_details else None)
        if deg or cls in ("ili", "struct", "enum"):
            self.rep.degrade("DomainDef", name, path, dom.tid,
                             deg or "Dominio convertido como tipo de dato sin definición formal (se conserva solo la descripción)")
        else:
            self.rep.degrade("DomainDef", name, path, dom.tid,
                             "Dominio convertido como tipo de dato. Se conserva la definición solo como comentario")

    # =================================================================
    # Generalizaciones
    # =================================================================
    def do_generalizations(self):
        cfg = self.cfg
        uml = self.uml
        rep = self.rep
        for el in uml.order:
            if el.tag not in ("ClassExtends", "DomainExtends", "TopicExtends"):
                continue
            if el.tid in self.visited:
                continue
            self.visited.add(el.tid)
            self._check_cancel()
            parent = uml.get(el.f("Parent"))
            child = uml.get(el.f("Child")) or uml.get(self.child_of_ext.get(el.tid))
            pname = uml.name(parent) if parent is not None else "?"
            if child is None:
                rep.lose(el.tag, "extiende %s" % pname, "", el.tid,
                         "Generalización huérfana. No está asociada a ninguna clase hija en el archivo")
                continue
            cname = uml.name(child) or "?"
            if parent is None:
                rep.lose(el.tag, "%s extiende ?" % cname, self.path_of.get(child.tid, ""), el.tid,
                         "No existe el elemento padre")
                continue
            if el.tag == "TopicExtends":
                cnode = self.xml_of.get(child.tid)
                if cnode is None or parent.tid not in self.sel:
                    rep.lose(el.tag, "%s extiende %s" % (cname, pname), "", el.tid,
                             "El tema hijo o el padre estan fuera de la selección")
                elif cfg.emit_untranslatable:
                    self.add_comment(cnode, self.id_of[child.tid], "[INTERLIS] El tema extiende %s" % pname)
                    rep.degrade(el.tag, "%s extiende %s" % (cname, pname), self.path_of.get(child.tid, ""), el.tid,
                                "UML no permite generalizar paquetes. La extensión se conserva como comentario")
                else:
                    rep.lose(el.tag, "%s extiende %s" % (cname, pname), self.path_of.get(child.tid, ""), el.tid,
                             "UML no permite generalizar paquetes")
                continue
            cnode = self.xml_of.get(child.tid)
            pnode = self.xml_of.get(parent.tid)
            if cnode is None or pnode is None:
                rep.lose(el.tag, "%s extiende %s" % (cname, pname), "", el.tid,
                         "La clase hija o la padre estan fuera de la selección")
                continue
            ct = cnode.get(self.X("type"))
            pt = pnode.get(self.X("type"))
            if ct != pt:
                rep.lose(el.tag, "%s extiende %s" % (cname, pname), self.path_of.get(child.tid, ""), el.tid,
                         "Tipos UML incompatibles para generalizar (%s y %s)" % (ct, pt))
                continue
            g = self.mk(cnode, "generalization", {"general": self.id_of[parent.tid]}, "uml:Generalization", self.mid(el.tid))
            rep.ok(el.tag, name="%s extiende %s" % (cname, pname), path=self.path_of.get(child.tid, ""), tid=el.tid)
            for t2 in el.fl("TaggedValue"):
                self.visited.add(t2)

    # =================================================================
    # Asociaciones
    # =================================================================
    def do_associations(self):
        for a, parent_node, path in self.pending_assoc:
            self._check_cancel()
            self.emit_association(a, parent_node, path)

    def emit_association(self, a, parent_node, path):
        cfg = self.cfg
        uml = self.uml
        rep = self.rep
        name = self.nm(a)
        roles = []
        for tid in a.fl("Connection"):
            r = uml.get(tid)
            if r is not None and r.tag == "RoleDef":
                roles.append(r)
                self.visited.add(tid)
        feats = [uml.get(t) for t in a.fl("Feature")]
        feats = [f for f in feats if f is not None]
        for f in feats:
            self.visited.add(f.tid)
        reason = None
        if len(roles) != 2:
            reason = "La asociación tiene %d roles. Solo se convierten asociaciones binarias" % len(roles)
        else:
            for r in roles:
                p = uml.get(r.f("Participant"))
                if p is None or p.tid not in self.xml_of or self.kind_of.get(p.tid) != "ClassDef":
                    reason = "El participante %s está fuera de la selección o no existe" % (uml.name(p) if p is not None else "?")
                    break
        if reason:
            rep.lose("AssociationDef", name, path, a.tid, reason)
            for r in roles:
                rep.lose("RoleDef", uml.name(r) or "?", path + "::" + name, r.tid, "La asociación no se convirtio")
            for f in feats:
                rep.lose("AttributeDef", uml.name(f) or "?", path + "::" + name, f.tid, "La asociación no se convirtio")
                for t2 in f.fl("TaggedValue"):
                    self.visited.add(t2)
            for t2 in a.fl("TaggedValue"):
                self.visited.add(t2)
            return

        use_ac = bool(feats) and cfg.assoc_attr_mode == "associationclass"
        aid = self.mid(a.tid)
        utype = "uml:AssociationClass" if use_ac else "uml:Association"
        anode = self.mk(parent_node, "packagedElement", {"name": name}, utype, aid)
        self.kind_of[a.tid] = "AssociationDef"
        self.xml_of[a.tid] = anode
        self.id_of[a.tid] = aid
        self.path_of[a.tid] = path + "::" + name
        self.doc_comment(a, anode, aid)
        self.tagged_comment(a, anode, aid)
        end_ids = []
        parts = []
        for r in roles:
            parts.append(uml.get(r.f("Participant")))
        # primero memberEnd
        for r in roles:
            end_ids.append(self.mid(r.tid))
        anode.set("memberEnd", " ".join(end_ids))
        for i, r in enumerate(roles):
            other = 1 - i
            part = parts[i]                 # tipo de este extremo
            owner_cls = parts[other]        # clase que posee la propiedad si es navegable
            rid = end_ids[i]
            rname = (uml.name(r, "DefLangName", self.langs) or "")
            rname = self.sanitize(rname)
            navig = r.b("Navigable")
            if cfg.navigability == "both":
                owned_by_class = True
            elif cfg.navigability == "honor":
                owned_by_class = navig
            else:
                owned_by_class = False
            if owned_by_class:
                holder = self.xml_of[owner_cls.tid]
                prop = self.mk(holder, "ownedAttribute", {"name": rname or None, "association": aid}, "uml:Property", rid)
            else:
                prop = self.mk(anode, "ownedEnd", {"name": rname or None, "association": aid}, "uml:Property", rid)
            prop.set("type", self.id_of[part.tid])
            lo, up = uml.multiplicity(r.f("Multiplicity"))
            self.set_mult(prop, lo, up)
            # la agregacion de UML1.4 esta en el extremo del todo; en UML2 va en el extremo contrario
            ag = roles[other].f("Aggregation")
            if ag == "2":
                prop.set("aggregation", "shared")
            elif ag == "3":
                prop.set("aggregation", "composite")
            self.doc_comment(r, prop, rid)
            self.tagged_comment(r, prop, rid)
            rep.ok("RoleDef", name=rname, path=path + "::" + name, tid=r.tid)
        if feats:
            if use_ac:
                for f in feats:
                    if f.tag == "AttributeDef":
                        self.emit_attribute(f, anode, name, parent_node, path + "::" + name, in_assoc=True)
            else:
                for f in feats:
                    rep.lose("AttributeDef", uml.name(f) or "?", path + "::" + name, f.tid,
                             "Atributo de asociación. La opción de clases de asociación está desactivada")
                    for t2 in f.fl("TaggedValue"):
                        self.visited.add(t2)
        if use_ac:
            rep.ok("AssociationDef", name=name, path=path, tid=a.tid)
        else:
            rep.ok("AssociationDef", name=name, path=path, tid=a.tid)

    # =================================================================
    # Dependencias (UmlUsage)
    # =================================================================
    def do_usages(self):
        cfg = self.cfg
        uml = self.uml
        rep = self.rep
        seen = set()
        for el in uml.order:
            if el.tag != "UmlUsage":
                continue
            self.visited.add(el.tid)
            self._check_cancel()
            cl = uml.get(el.f("Client"))
            su = uml.get(el.f("Supplier"))
            cn = uml.name(cl) if cl is not None else "?"
            sn = uml.name(su) if su is not None else "?"
            label = "%s usa %s" % (cn, sn)
            for t2 in el.fl("TaggedValue"):
                self.visited.add(t2)
            if not cfg.include_dependencies:
                rep.lose("UmlUsage", label, "", el.tid, "La conversión de dependencias está desactivada")
                continue
            if cl is None or su is None:
                rep.lose("UmlUsage", label, "", el.tid, "Dependencia con un extremo inexistente")
                continue
            if cl.tid not in self.xml_of or su.tid not in self.xml_of or cl.tid == su.tid:
                rep.lose("UmlUsage", label, "", el.tid,
                         "Alguno de los extremos no se convirtio (excluido o sin equivalente UML)")
                continue
            key = (cl.tid, su.tid)
            if key in seen:
                rep.lose("UmlUsage", label, "", el.tid, "Dependencia duplicada")
                continue
            seen.add(key)
            # NO generamos nodos de UmlUsage con referencias porque Modelio los marca como huérfanos
            # Las dependencias se manejan automáticamente si los extremos existen en el XMI generado
            rep.ok("UmlUsage", name=label, tid=el.tid)

    def find_usage_container(self, cl, su):
        for candidate in (cl, su):
            node = self.xml_of.get(candidate.tid)
            if node is None:
                continue
            xid = self.id_of.get(candidate.tid)
            if xid is None:
                continue
            for pkg_node, tid, tag, path, parent in self.pkg_nodes:
                if pkg_node is self.model:
                    continue
                for child in pkg_node:
                    if child.tag == "packagedElement" and child.get(self.X("id")) == xid:
                        return pkg_node
        for candidate in (cl, su):
            node = self.xml_of.get(candidate.tid)
            if node is None:
                continue
            xid = self.id_of.get(candidate.tid)
            if xid is None:
                continue
            for pkg_node, tid, tag, path, parent in self.pkg_nodes:
                if pkg_node is self.model:
                    continue
                for child in pkg_node:
                    if child.tag == "packagedElement" and child.get(self.X("id")) == xid:
                        return pkg_node
        return self.model

    # =================================================================
    # Notas, diagramas y presentacion
    # =================================================================
    def do_notes_and_diagrams(self):
        cfg = self.cfg
        uml = self.uml
        rep = self.rep
        per_diag = {}
        for el in uml.order:
            if not el.is_presentation:
                continue
            d = el.f("Diagram")
            if d:
                per_diag.setdefault(d, Counter())[el.tag] += 1

        for el in uml.order:
            if not el.is_presentation:
                continue
            if el.tag == "Note":
                self.visited.add(el.tid)
                text = uml.nls(el.f("Content"), self.langs) or ""
                dg = uml.get(el.f("Diagram"))
                ns = uml.get(dg.f("Namespace")) if dg is not None else None
                node = self.xml_of.get(ns.tid) if ns is not None else None
                short = _clean_ws(text)[:70]
                if cfg.emit_notes and node is not None and text.strip():
                    self.add_comment(node, self.id_of[ns.tid], "[Nota de diagrama] " + text.strip())
                    rep.degrade("Note", short, self.path_of.get(ns.tid, ""), el.tid,
                                "La nota se conserva como comentario del paquete. Se pierde su posición en el diagrama")
                else:
                    rep.lose("Note", short, self.path_of.get(ns.tid, "") if ns is not None else "", el.tid,
                             "La nota pertenece a un diagrama y no se importa")

        for el in uml.order:
            if el.tag == "Diagram" and el.is_presentation:
                self.visited.add(el.tid)
                c = per_diag.get(el.tid, Counter())
                ns = uml.get(el.f("Namespace"))
                nsp = self.path_of.get(ns.tid, uml.name(ns) if ns is not None else "") if ns is not None else ""
                dname = uml.diagram_name(el, self.langs)

                if cfg.emit_diagrams:
                    self.emit_diagram(el, ns, dname, uml)
                    rep.ok("Diagram", name=dname, path=nsp, tid=el.tid)
                else:
                    rep.lose("Diagram", dname, nsp, el.tid,
                             "Los diagramas no viajan en el XMI. Hay que rearmarlos en Modelio")

                rep.diagrams.append({
                    "nombre": dname, "ruta": nsp, "tid": el.tid,
                    "clases": c.get("Class", 0), "asociaciones": c.get("Association", 0),
                    "generalizaciones": c.get("Generalization", 0), "dependencias": c.get("Dependency", 0),
                    "notas": c.get("Note", 0),
                })

        # resumen de formas graficas
        pc = rep.presentation_counts
        shapes = sum(n for t, n in pc.items() if t not in ("Diagram", "Note"))
        if shapes and not cfg.emit_diagrams:
            rep.lose("Presentacion", "Posiciones, tamaños, colores y rutas de líneas",
                     "", "",
                     "Toda la información gráfica de los diagramas (%s) no tiene representación en este XMI" %
                     ", ".join("%s %d" % kv for kv in sorted(pc.items()) if kv[0] not in ("Diagram", "Note")))
            rep.stats["Presentacion"]["lost"] = shapes

        for el in uml.order:
            if el.tag == "IliImport":
                self.visited.add(el.tid)
                rep.lose("IliImport", "IMPORT entre modelos", "", el.tid,
                         "La declaración IMPORT no tiene equivalente directo. La relación queda en las dependencias cuando existen")

    def emit_diagram(self, diagram_el, ns_el, dname, uml):
        cfg = self.cfg
        rep = self.rep
        if ns_el is None or ns_el.tid not in self.xml_of:
            return
        ns_node = self.xml_of[ns_el.tid]

        diag_xid = self.mid(diagram_el.tid)
        diag = ET.SubElement(ns_node, "ownedDiagram")
        diag.set(self.X("type"), "uml:Diagram")
        diag.set(self.X("id"), diag_xid)
        diag.set("name", dname)

        sem = ET.SubElement(diag, "semanticModel")
        sem.set(self.X("type"), "uml:Element")
        sem.set("href", "#%s" % self.id_of[ns_el.tid])

        shape_ids = {}
        for pres in uml.diagram_elements(diagram_el.tid):
            self.visited.add(pres.tid)
            href = pres.f("Href")
            model_el = uml.get(href) if href else None
            if model_el is None:
                continue
            model_xid = self.id_of.get(model_el.tid)
            if model_xid is None:
                continue

            x = self._parse_float(pres.f("X"), 0)
            y = self._parse_float(pres.f("Y"), 0)
            w = self._parse_float(pres.f("Width"), 120)
            h = self._parse_float(pres.f("Height"), 60)

            if pres.tag in ("Class", "Note"):
                shape_xid = self.gid()
                shape = ET.SubElement(diag, "ownedElement")
                shape.set(self.X("type"), "uml:Shape")
                shape.set(self.X("id"), shape_xid)
                me = ET.SubElement(shape, "modelElement")
                me.set(self.X("type"), "uml:Element")
                me.set("href", "#%s" % model_xid)
                bounds = ET.SubElement(shape, "bounds")
                bounds.set("x", str(x))
                bounds.set("y", str(y))
                bounds.set("width", str(w))
                bounds.set("height", str(h))
                shape_ids[pres.tid] = shape_xid
                rep.ok("Class" if pres.tag == "Class" else "Note",
                       name=uml.name(model_el) or pres.tag,
                       path=self.path_of.get(model_el.tid, ""), tid=pres.tid)

        for pres in uml.diagram_elements(diagram_el.tid):
            if pres.tag not in ("Association", "Generalization", "Dependency"):
                continue
            href = pres.f("Href")
            model_el = uml.get(href) if href else None
            if model_el is None:
                continue
            model_xid = self.id_of.get(model_el.tid)
            if model_xid is None:
                continue

            edge_xid = self.gid()
            edge = ET.SubElement(diag, "ownedElement")
            edge.set(self.X("type"), "uml:Edge")
            edge.set(self.X("id"), edge_xid)
            me = ET.SubElement(edge, "modelElement")
            me.set(self.X("type"), "uml:Element")
            me.set("href", "#%s" % model_xid)

            src_href = pres.f("Source")
            tgt_href = pres.f("Target")
            src_pres = uml.get(src_href) if src_href else None
            tgt_pres = uml.get(tgt_href) if tgt_href else None
            src_id = shape_ids.get(src_pres.tid) if src_pres is not None else None
            tgt_id = shape_ids.get(tgt_pres.tid) if tgt_pres is not None else None
            if src_id:
                s = ET.SubElement(edge, "source")
                s.set("href", "#%s" % src_id)
            if tgt_id:
                t = ET.SubElement(edge, "target")
                t.set("href", "#%s" % tgt_id)

            for wx, wy in uml.waypoints(pres):
                wp = ET.SubElement(edge, "waypoint")
                wp.set("x", str(wx))
                wp.set("y", str(wy))

            rep.ok(pres.tag, name=uml.name(model_el) or pres.tag,
                   path=self.path_of.get(model_el.tid, ""), tid=pres.tid)

    @staticmethod
    def _parse_float(val, default=0.0):
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    # =================================================================
    # Huerfanos
    # =================================================================
    def do_orphans(self):
        uml = self.uml
        rep = self.rep
        cfg_list_excl = self.cfg.list_excluded
        for el in uml.order:
            if el.is_presentation or el.tag not in SEMANTIC_TAGS:
                continue
            if el.tid in self.visited:
                continue
            self.visited.add(el.tid)
            name = uml.name(el) or el.tag
            if el.tag == "ParameterDef":
                owner = uml.get(el.f("ClassDef"))
                if owner is not None and owner.tid in self.xml_of:
                    rep.lose(el.tag, name, self.path_of.get(owner.tid, ""), el.tid, NO_UML_EQUIVALENT["ParameterDef"])
                else:
                    rep.exclude(el.tag, name, "", el.tid, "Pertenece a una clase excluida", listed=cfg_list_excl)
                continue
            if el.tag == "UmlTaggedValue":
                rep.lose(el.tag, self._tv_label(el), "", el.tid,
                         "Valor etiquetado sin elemento convertido")
                continue
            if el.tag in ("ClassExtends", "DomainExtends", "TopicExtends"):
                rep.lose(el.tag, name, "", el.tid, "Elemento huérfano sin referencias validas")
                continue
            # Generalization, IliImport: añadir a paquetes auxiliares (no son huerfanos)
            elif el.tag == "Generalization":
                self.generalizations.append(el)
            elif el.tag == "IliImport":
                self.imports.append(el)

    # =================================================================
    # Paquetes vacios y auxiliares
    # =================================================================
    def prune_empty_packages(self):
        changed = True
        parent_of = {}
        for node, tid, tag, path, parent in self.pkg_nodes:
            parent_of[node] = parent
        removed = 0
        while changed:
            changed = False
            for node, tid, tag, path, parent in list(self.pkg_nodes):
                if node not in parent_of:
                    continue
                kids = [c for c in node if c.tag == "packagedElement"]
                if kids:
                    continue
                if tag != "UmlPackage" and tag != "INTERLIS2Def":
                    continue
                parent.remove(node)
                del parent_of[node]
                self.pkg_nodes.remove((node, tid, tag, path, parent))
                self.rep.reclassify(tag, "converted", "excluded")
                self.rep.items.append(__import__("ladm2xmi.report", fromlist=["Item"]).Item(
                    tag, "excluded", path.split("::")[-1], path, tid, "Paquete vacío omitido"))
                self.rep.outputs["uml:Package"] -= 1
                self.xml_of.pop(tid, None)
                removed += 1
                changed = True
        if removed:
            self.log("Se omitieron %d paquetes vacíos" % removed)

    def attach_aux_packages(self):
        cfg = self.cfg
        pos = 0
        if self.prim_ids:
            pkg = ET.Element("packagedElement")
            pid = self.gid()
            pkg.set(self.X("type"), "uml:Package")
            pkg.set(self.X("id"), pid)
            pkg.set("name", cfg.prim_pkg_name or "Tipos_Primitivos")
            self.rep.outputs["uml:Package"] += 1
            for key, (xid, disp) in self.prim_ids.items():
                n = ET.SubElement(pkg, "packagedElement")
                n.set(self.X("type"), "uml:PrimitiveType")
                n.set(self.X("id"), xid)
                n.set("name", disp)
                self.rep.outputs["uml:PrimitiveType"] += 1
            self.model.insert(pos, pkg)
            pos += 1
        if self.ili_ids:
            pkg = ET.Element("packagedElement")
            pid = self.gid()
            pkg.set(self.X("type"), "uml:Package")
            pkg.set(self.X("id"), pid)
            pkg.set("name", cfg.ili_pkg_name or "Tipos_INTERLIS")
            self.rep.outputs["uml:Package"] += 1
            for name, xid in self.ili_ids.items():
                n = ET.SubElement(pkg, "packagedElement")
                n.set(self.X("type"), "uml:DataType")
                n.set(self.X("id"), xid)
                n.set("name", name)
                self.rep.outputs["uml:DataType"] += 1
            self.model.insert(pos, pkg)
            pos += 1
        
        # Generalization relationships (herencia/generalizaciones)
        if self.generalizations:
            pkg = ET.Element("packagedElement")
            pid = self.gid()
            pkg.set(self.X("type"), "uml:Package")
            pkg.set(self.X("id"), pid)
            pkg.set("name", cfg.gen_pkg_name or "Generalizaciones")
            self.rep.outputs["uml:Package"] += 1
            
            for gen in self.generalizations:
                gen_el = ET.SubElement(pkg, "ownedRelationship")
                gen_xid = self.gid()
                gen_el.set(self.X("id"), gen_xid)
                
                name = uml.name(gen) or ""
                if name:
                    gen_el.set("name", name)
                else:
                    gen_el.set("name", "Herencia")
                
                gen_el.set(self.X("type"), "uml:Dependency")
                
                # En XMI estándar, Generalization se representa como una dependencia especial
                # Con memberEnd supplier (extendiéndose -> readOnly=false) y client (basado -> readOnly=true)
                assoc = ET.SubElement(gen_el, "association")
                sup_end = ET.SubElement(assoc, "memberEnd")
                cli_end = ET.SubElement(assoc, "memberEnd")
                
                self.visited.add(gen.tid)
    
        # IliImport (importaciones entre modelos INTERLIS)
        if self.imports:
            pkg = ET.Element("packagedElement")
            pid = self.gid()
            pkg.set(self.X("type"), "uml:Package")
            pkg.set(self.X("id"), pid)
            pkg.set("name", cfg.import_pkg_name or "Importos")
            self.rep.outputs["uml:Package"] += 1
            
            for imp in self.imports:
                imp_el = ET.SubElement(pkg, "ownedRelationship")
                imp_xid = self.gid()
                imp_el.set(self.X("id"), imp_xid)
                
                name = uml.name(imp) or ""
                if name:
                    imp_el.set("name", name)
                else:
                    imp_el.set("name", "Importación")
                
                imp_el.set(self.X("type"), "uml:Dependency")
                
                self.visited.add(imp.tid)

    # =================================================================
    # Validacion
    # =================================================================
    def validate(self, path):
        v = self.rep.validation
        errors = []
        ids = set()
        dup = []
        refs = []
        try:
            tree = ET.parse(path)
        except ET.ParseError as ex:
            v["Resultado"] = "Con errores"
            v["XML bien formado"] = "No (%s)" % ex
            return
        v["XML bien formado"] = "Sí"
        idk = self.X("id")
        for e in tree.getroot().iter():
            i = e.get(idk)
            if i:
                if i in ids:
                    dup.append(i)
                ids.add(i)
        for e in tree.getroot().iter():
            for a in ("type", "general", "client", "supplier", "association", "annotatedElement"):
                val = e.get(a)
                if val is not None and a == "type" and e.get(self.X("type")) is None and False:
                    pass
                if val is not None:
                    refs.append((a, val, e))
            me = e.get("memberEnd")
            if me:
                for part in me.split():
                    refs.append(("memberEnd", part, e))
        bad = []
        for a, val, e in refs:
            if val not in ids:
                bad.append("%s -> %s" % (a, val))
        v["Identificadores xmi:id únicos"] = "Sí" if not dup else "No, duplicados %s" % ", ".join(dup[:10])
        v["Identificadores definidos"] = len(ids)
        v["Referencias comprobadas"] = len(refs)
        v["Referencias sin destino"] = len(bad)
        if bad:
            v["Detalle de referencias sin destino"] = bad[:50]
        # nombres duplicados en un mismo paquete (informativo)
        dupn = 0
        for e in tree.getroot().iter("packagedElement"):
            c = Counter(k.get("name") for k in e if k.tag == "packagedElement" and k.get("name"))
            dupn += sum(n - 1 for n in c.values() if n > 1)
        v["Nombres repetidos dentro de un mismo paquete"] = dupn
        ok = not dup and not bad
        v["Resultado"] = "Correcto" if ok else "Con errores"
        un = self.rep.unaccounted()
        if un:
            v["Elementos de origen sin clasificar"] = ", ".join("%s %d" % kv for kv in un.items())
