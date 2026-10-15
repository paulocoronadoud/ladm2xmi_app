"""Lector del formato nativo del UML/INTERLIS Editor (.uml, namespace umleditor)."""
import os
from xml.etree import ElementTree as ET

PRESENTATION_PREFIX = "ch.ehi.umleditor.umlpresentation."


class Elem:
    """Envoltorio ligero de un elemento de primer nivel (identificado por TID)."""

    __slots__ = ("el", "tid", "full", "tag", "_ns", "_cache")

    def __init__(self, el, ns):
        self.el = el
        self._ns = ns
        self.tid = el.get("TID")
        self.full = el.tag.replace(ns, "")
        self.tag = self.full.rsplit(".", 1)[-1]
        self._cache = None

    def f(self, name):
        c = self.el.find(self._ns + name)
        if c is None:
            return None
        return (c.text or "").strip()

    def fl(self, name):
        return [(c.text or "").strip() for c in self.el.findall(self._ns + name)]

    def has(self, name):
        return self.el.find(self._ns + name) is not None

    def b(self, name, default=False):
        v = self.f(name)
        if v is None:
            return default
        return v.lower() == "true"

    def fields(self):
        return [c.tag.replace(self._ns, "") for c in self.el]

    @property
    def is_presentation(self):
        return self.full.startswith(PRESENTATION_PREFIX)


class UmlFile:
    def __init__(self, path):
        self.path = path
        tree = ET.parse(path)
        root = tree.getroot()
        if "}" in root.tag:
            self.ns = root.tag.split("}")[0] + "}"
        else:
            self.ns = ""
        self.root_tag = root.tag.replace(self.ns, "")
        if self.root_tag != "ch.ehi.umleditor":
            raise ValueError(
                "El archivo no tiene el formato del UML/INTERLIS Editor (elemento raíz '%s')." % self.root_tag
            )
        self.by_tid = {}
        self.order = []
        for e in root:
            el = Elem(e, self.ns)
            if el.tid is None:
                continue
            self.by_tid[el.tid] = el
            self.order.append(el)
        self.size = os.path.getsize(path)
        self._mult_cache = {}

    # --- utilidades -------------------------------------------------------
    def get(self, tid):
        if tid is None:
            return None
        return self.by_tid.get(tid)

    def nls(self, tid, langs=("es",)):
        """Texto de una NlsString preferiendo los idiomas indicados."""
        e = self.get(tid)
        if e is None:
            return None
        entries = e.el.findall(self.ns + "Entry")
        best = None
        for lang in langs:
            for en in entries:
                if en.get("language") == lang and (en.get("translation") or "").strip():
                    return en.get("translation")
        for en in entries:
            t = en.get("translation")
            if t and t.strip():
                best = t
                break
        return best

    def name(self, el, source="DefLangName", langs=("es",)):
        if source == "Name":
            n = self.nls(el.f("Name"), langs)
            if n:
                return n
            return el.f("DefLangName")
        n = el.f("DefLangName")
        if n:
            return n
        return self.nls(el.f("Name"), langs)

    def doc(self, el, langs=("es",)):
        t = el.f("Documentation")
        if not t:
            return None
        s = self.nls(t, langs)
        if s and s.strip():
            return s.strip()
        return None

    def children(self, el):
        """Elementos contenidos (OwnedElementLink -> ElementOwnership -> OwnedElement)."""
        out = []
        for lk in el.fl("OwnedElementLink"):
            own = self.get(lk)
            if own is None:
                continue
            ch = self.get(own.f("OwnedElement"))
            if ch is not None:
                out.append(ch)
        return out

    def multiplicity(self, tid):
        """Devuelve (lower, upper) con upper=None para ilimitado. Por defecto (1, 1)."""
        if tid in self._mult_cache:
            return self._mult_cache[tid]
        res = (1, 1)
        m = self.get(tid)
        if m is not None:
            rng = self.get(m.f("Range"))
            if rng is not None:
                lo = rng.f("Lower")
                up = rng.f("Upper")
                try:
                    lo_i = int(lo) if lo is not None else 0
                except ValueError:
                    lo_i = 0
                try:
                    up_i = int(up) if up is not None else 1
                except ValueError:
                    up_i = 1
                if up_i >= 2 ** 62:
                    up_i = None
                res = (lo_i, up_i)
        self._mult_cache[tid] = res
        return res

    def diagram_name(self, el, langs):
        return self.nls(el.f("Name"), langs) or ("Diagrama %s" % el.tid)

    def tagged_values(self, el, langs):
        out = []
        for tv_tid in el.fl("TaggedValue"):
            tv = self.get(tv_tid)
            if tv is None:
                continue
            key = self.nls(tv.f("Name"), langs) or ""
            val = tv.f("DataValue") or ""
            out.append((key, val))
        return out

    def presentation_element(self, el):
        """Devuelve el elemento de presentación asociado a un elemento del modelo, si existe."""
        for p in self.order:
            if not p.is_presentation:
                continue
            href = p.f("Href")
            if href == el.tid:
                return p
        return None

    def diagram_elements(self, diagram_tid):
        """Devuelve los elementos de presentación hijos de un diagrama."""
        out = []
        for p in self.order:
            if not p.is_presentation:
                continue
            d = p.f("Diagram")
            if d == diagram_tid:
                out.append(p)
        return out

    def waypoints(self, pres_el):
        """Devuelve la lista de waypoints (x, y) de un elemento de presentación."""
        pts = []
        for wp in pres_el.el.findall(self.ns + "Waypoint"):
            try:
                x = float(wp.get("x", 0))
                y = float(wp.get("y", 0))
                pts.append((x, y))
            except (ValueError, TypeError):
                pass
        return pts
