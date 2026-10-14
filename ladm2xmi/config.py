"""Parámetros de configuración del convertidor. Todos son editables desde la GUI."""
import json
import os
from dataclasses import dataclass, field, asdict, fields

DEFAULT_DOMAIN_MAP = (
    "XMLDate=Date\n"
    "XMLDateTime=DateTime\n"
    "XMLTime=Time\n"
    "INTERLIS_1_DATE=Date\n"
    "BOOLEAN=Boolean\n"
    "URI=String\n"
    "NAME=String\n"
    "UUIDOID=String\n"
    "STANDARDOID=String\n"
    "ANYOID=String\n"
    "I32OID=Integer\n"
)

DEFAULT_PRIM_NAMES = (
    "String=String\n"
    "Integer=Integer\n"
    "Real=Real\n"
    "Boolean=Boolean\n"
    "Date=Date\n"
    "DateTime=DateTime\n"
    "Time=Time\n"
)

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".ladm_uml2xmi.json")


@dataclass
class Config:
    # Archivos
    input_path: str = ""
    output_path: str = ""
    report_dir: str = ""
    report_html: bool = True
    report_csv: bool = True
    report_json: bool = False
    report_txt: bool = False
    open_report: bool = True

    # XMI
    dialect: str = "eclipse"          # eclipse | omg
    xmi_version: str = "2.1"
    model_name: str = ""
    id_prefix: str = "id_"
    pretty_print: bool = True

    # Contenido
    excluded_names: str = "INTERLIS,INTERLIS_TOPOLOGY"
    excluded_tids: list = field(default_factory=list)
    included_tids: list = field(default_factory=list)   # reservado para seleccion explicita
    flatten_ili_files: bool = True
    sort_alphabetically: bool = False

    # Nombres
    name_source: str = "DefLangName"  # DefLangName | Name
    language: str = "es"
    sanitize_names: bool = False

    # Mapeo
    structure_as: str = "DataType"    # DataType | Class
    domain_mode: str = "datatype"     # datatype | inline
    enum_separator: str = "."
    enum_include_nodes: bool = False
    inline_enum_pattern: str = "{clase}_{atributo}"
    primitive_mode: str = "local"     # local | uml_href
    prim_pkg_name: str = "Tipos_Primitivos"
    ili_pkg_name: str = "Tipos_INTERLIS"
    prim_names: str = DEFAULT_PRIM_NAMES
    domain_map: str = DEFAULT_DOMAIN_MAP
    navigability: str = "both"        # both | honor | none
    assoc_attr_mode: str = "associationclass"  # associationclass | lose
    include_dependencies: bool = True
    mandatory_forces_lower_one: bool = True

    # Informacion adicional como comentarios
    emit_documentation: bool = True
    emit_tagged_values: bool = True
    emit_type_details: bool = True
    emit_notes: bool = True
    emit_source_info: bool = True
    emit_untranslatable: bool = True

    # Reporte
    list_converted: bool = False
    list_excluded: bool = False
    html_max_items: int = 500

    # ------------------------------------------------------------------
    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        known = {f.name for f in fields(cls)}
        cfg = cls()
        for k, v in (data or {}).items():
            if k in known:
                setattr(cfg, k, v)
        return cfg

    def save(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def parse_map(self, text):
        out = {}
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
        return out

    def excluded_name_set(self):
        return {n.strip() for n in (self.excluded_names or "").split(",") if n.strip()}
