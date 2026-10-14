"""Interfaz gráfica de escritorio (Tkinter). Todos los parámetros del convertidor se editan aqui."""
import os
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from . import __version__, APP_NAME
from .config import Config, CONFIG_FILE, DEFAULT_DOMAIN_MAP, DEFAULT_PRIM_NAMES
from .converter import Converter, Cancelled
from .umlreader import UmlFile

CHK_ON = "☑ "
CHK_OFF = "☐ "

DIALECTS = [("Eclipse UML2 (recomendado para Modelio)", "eclipse"), ("OMG UML 2.1 (xmi:XMI)", "omg")]
XMI_VERSIONS = [("XMI 2.1", "2.1"), ("XMI 2.5.1", "2.5.1")]
STRUCT = [("Tipo de dato UML (DataType)", "DataType"), ("Clase UML", "Class")]
DOMAIN_MODE = [("Tipo de dato UML con comentario", "datatype"), ("Sustituir por el tipo base en cada atributo", "inline")]
PRIM_MODE = [("Tipos definidos dentro del modelo", "local"), ("Biblioteca estándar de UML (href)", "uml_href")]
NAVIG = [("Ambos extremos navegables", "both"), ("Respetar la marca del archivo origen", "honor"), ("Ninguno navegable", "none")]
ASSOC_ATTR = [("Clase de asociación UML", "associationclass"), ("Descartar los atributos", "lose")]
NAME_SRC = [("Nombre INTERLIS (DefLangName)", "DefLangName"), ("Nombre traducido (Name)", "Name")]


def open_path(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        webbrowser.open("file://" + os.path.abspath(path))


class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", self._wheel)
        self.canvas.bind_all("<Button-5>", self._wheel)

    def _unbind_wheel(self, _):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _wheel(self, e):
        if e.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif e.num == 5:
            self.canvas.yview_scroll(1, "units")
        else:
            self.canvas.yview_scroll(int(-e.delta / 120), "units")


class App(tk.Tk):
    def __init__(self, initial_file=None):
        super().__init__()
        self.title("%s  %s" % (APP_NAME, __version__))
        self.geometry("1020x800")
        self.minsize(860, 640)
        try:
            ttk.Style(self).theme_use("clam" if sys.platform.startswith("linux") else ttk.Style(self).theme_use())
        except tk.TclError:
            pass
        self.q = queue.Queue()
        self.worker = None
        self.cancel_flag = threading.Event()
        self.uml = None
        self.last_report_files = []
        self.last_output = ""
        self.tree_loaded = False
        self.saved_excl = []
        self.saved_incl = []
        self.vars = {}

        cfg = Config()
        if os.path.exists(CONFIG_FILE):
            try:
                cfg = Config.load(CONFIG_FILE)
            except Exception:
                cfg = Config()
        if initial_file:
            cfg.input_path = initial_file
            cfg.output_path = os.path.splitext(initial_file)[0] + ".xmi"
        self.build_vars(cfg)
        self.build_menu()
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.poll)
        if self.vars["input_path"].get() and os.path.exists(self.vars["input_path"].get()):
            self.after(300, self.analyze)

    # ------------------------------------------------------------------
    # Variables <-> Config
    # ------------------------------------------------------------------
    BOOLS = ["report_html", "report_csv", "report_json", "report_txt", "open_report", "pretty_print",
             "flatten_ili_files", "sort_alphabetically", "sanitize_names", "enum_include_nodes",
             "include_dependencies", "mandatory_forces_lower_one", "emit_documentation", "emit_tagged_values",
             "emit_type_details", "emit_notes", "emit_source_info", "emit_untranslatable", "list_converted",
             "list_excluded"]
    STRS = ["input_path", "output_path", "report_dir", "dialect", "xmi_version", "model_name", "id_prefix",
            "excluded_names", "name_source", "language", "structure_as", "domain_mode", "enum_separator",
            "inline_enum_pattern", "primitive_mode", "prim_pkg_name", "ili_pkg_name", "navigability",
            "assoc_attr_mode"]
    INTS = ["html_max_items"]

    def build_vars(self, cfg):
        for k in self.BOOLS:
            self.vars[k] = tk.BooleanVar(value=bool(getattr(cfg, k)))
        for k in self.STRS:
            self.vars[k] = tk.StringVar(value=str(getattr(cfg, k)))
        for k in self.INTS:
            self.vars[k] = tk.IntVar(value=int(getattr(cfg, k)))
        self.prim_names_text = cfg.prim_names
        self.domain_map_text = cfg.domain_map
        self.saved_excl = list(cfg.excluded_tids or [])
        self.saved_incl = list(cfg.included_tids or [])

    def push_cfg(self, cfg):
        """Carga una Config en los widgets."""
        for k in self.BOOLS:
            self.vars[k].set(bool(getattr(cfg, k)))
        for k in self.STRS:
            self.vars[k].set(str(getattr(cfg, k)))
        for k in self.INTS:
            self.vars[k].set(int(getattr(cfg, k)))
        self.txt_prim.delete("1.0", "end")
        self.txt_prim.insert("1.0", cfg.prim_names)
        self.txt_dom.delete("1.0", "end")
        self.txt_dom.insert("1.0", cfg.domain_map)
        self.saved_excl = list(cfg.excluded_tids or [])
        self.saved_incl = list(cfg.included_tids or [])
        for combo, key, table in self.combos:
            self.set_combo(combo, table, self.vars[key].get())
        if self.tree_loaded:
            self.apply_tree_defaults()

    def collect_cfg(self):
        cfg = Config()
        for k in self.BOOLS:
            setattr(cfg, k, bool(self.vars[k].get()))
        for k in self.STRS:
            setattr(cfg, k, self.vars[k].get())
        for k in self.INTS:
            try:
                setattr(cfg, k, int(self.vars[k].get()))
            except (tk.TclError, ValueError):
                pass
        for combo, key, table in self.combos:
            cfg_val = self.combo_value(combo, table)
            setattr(cfg, key, cfg_val)
        cfg.prim_names = self.txt_prim.get("1.0", "end").strip() + "\n"
        cfg.domain_map = self.txt_dom.get("1.0", "end").strip() + "\n"
        if self.tree_loaded:
            excl, incl = [], []
            for iid in self.node_state:
                (incl if self.node_state[iid] else excl).append(iid)
            cfg.excluded_tids = excl
            cfg.included_tids = incl
        else:
            cfg.excluded_tids = list(self.saved_excl)
            cfg.included_tids = list(self.saved_incl)
        return cfg

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------
    def build_menu(self):
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Abrir archivo .uml...", command=self.pick_input)
        f.add_separator()
        f.add_command(label="Guardar configuración...", command=self.save_cfg_dialog)
        f.add_command(label="Cargar configuración...", command=self.load_cfg_dialog)
        f.add_command(label="Restaurar valores por defecto", command=self.reset_cfg)
        f.add_separator()
        f.add_command(label="Salir", command=self.on_close)
        m.add_cascade(label="Archivo", menu=f)
        h = tk.Menu(m, tearoff=0)
        h.add_command(label="Acerca de", command=self.about)
        m.add_cascade(label="Ayuda", menu=h)
        self.config(menu=m)

    def about(self):
        messagebox.showinfo("Acerca de", "%s\nVersión %s\n\nConvierte el formato nativo del UML/INTERLIS Editor (.uml) "
                            "a XMI 2.1 para importarlo en Modelio y genera un reporte de lo que se pierde." % (APP_NAME, __version__))

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def build_ui(self):
        self.combos = []
        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)
        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill="both", expand=True)
        self.tab_files = ttk.Frame(self.nb, padding=10)
        self.tab_content = ttk.Frame(self.nb, padding=10)
        self.tab_map = ScrollFrame(self.nb)
        self.tab_text = ttk.Frame(self.nb, padding=10)
        self.tab_rep = ttk.Frame(self.nb, padding=10)
        self.nb.add(self.tab_files, text=" Archivos ")
        self.nb.add(self.tab_content, text=" Contenido ")
        self.nb.add(self.tab_map, text=" Mapeo a UML ")
        self.nb.add(self.tab_text, text=" Textos y metadatos ")
        self.nb.add(self.tab_rep, text=" Reporte ")
        self.build_files()
        self.build_content()
        self.build_map()
        self.build_text()
        self.build_report_tab()

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.lbl_prog = ttk.Label(bar, text="", width=34)
        self.lbl_prog.pack(side="left", padx=8)
        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=6)
        self.btn_convert = ttk.Button(btns, text="Convertir", command=self.start_convert)
        self.btn_convert.pack(side="left")
        self.btn_cancel = ttk.Button(btns, text="Cancelar", command=self.cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        self.btn_open_rep = ttk.Button(btns, text="Abrir reporte", command=self.open_report, state="disabled")
        self.btn_open_rep.pack(side="left", padx=6)
        self.btn_open_dir = ttk.Button(btns, text="Abrir carpeta de salida", command=self.open_out_dir, state="disabled")
        self.btn_open_dir.pack(side="left")
        self.log = scrolledtext.ScrolledText(outer, height=9, state="disabled", font=("TkFixedFont", 9))
        self.log.pack(fill="both", expand=False)

    # --- helpers de widgets -------------------------------------------
    def row_entry(self, parent, label, key, r, browse=None, width=60, hint=None):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 8))
        e = ttk.Entry(parent, textvariable=self.vars[key], width=width)
        e.grid(row=r, column=1, sticky="ew", pady=3)
        if browse:
            ttk.Button(parent, text="Examinar...", command=browse).grid(row=r, column=2, padx=(6, 0))
        if hint:
            ttk.Label(parent, text=hint, foreground="#666").grid(row=r + 1, column=1, sticky="w")
        return e

    def row_combo(self, parent, label, key, table, r, hint=None):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="nw", pady=3, padx=(0, 8))
        c = ttk.Combobox(parent, state="readonly", values=[t for t, _ in table], width=48)
        c.grid(row=r, column=1, sticky="w", pady=3)
        self.set_combo(c, table, self.vars[key].get())
        self.combos.append((c, key, table))
        if hint:
            ttk.Label(parent, text=hint, foreground="#666", wraplength=520, justify="left").grid(
                row=r + 1, column=1, sticky="w", pady=(0, 4))
        return c

    def set_combo(self, combo, table, value):
        for i, (_, v) in enumerate(table):
            if v == value:
                combo.current(i)
                return
        combo.current(0)

    def combo_value(self, combo, table):
        i = combo.current()
        return table[i][1] if i >= 0 else table[0][1]

    def row_check(self, parent, text, key, r, col=0, span=3):
        ttk.Checkbutton(parent, text=text, variable=self.vars[key]).grid(row=r, column=col, columnspan=span, sticky="w", pady=2)

    # --- pestana archivos ----------------------------------------------
    def build_files(self):
        f = self.tab_files
        f.columnconfigure(1, weight=1)
        lf = ttk.LabelFrame(f, text=" Origen y destino ", padding=10)
        lf.grid(row=0, column=0, sticky="ew", columnspan=3)
        lf.columnconfigure(1, weight=1)
        self.row_entry(lf, "Archivo .uml de origen", "input_path", 0, browse=self.pick_input)
        self.row_entry(lf, "Archivo XMI de salida", "output_path", 1, browse=self.pick_output)
        self.row_entry(lf, "Carpeta del reporte", "report_dir", 2, browse=self.pick_report_dir,
                       hint="Si se deja vacía, el reporte se guarda junto al XMI.")
        ttk.Button(f, text="Analizar archivo", command=self.analyze).grid(row=1, column=0, sticky="w", pady=10)
        self.lbl_info = ttk.Label(f, text="Aún no se ha analizado ningún archivo.", wraplength=820, justify="left")
        self.lbl_info.grid(row=2, column=0, columnspan=3, sticky="w")
        lf2 = ttk.LabelFrame(f, text=" Reportes a generar ", padding=10)
        lf2.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        self.row_check(lf2, "Reporte HTML (resumen, pérdidas y diagramas)", "report_html", 0)
        self.row_check(lf2, "CSV de detalle y de resumen (abre en Excel)", "report_csv", 1)
        self.row_check(lf2, "JSON (para procesar con otras herramientas)", "report_json", 2)
        self.row_check(lf2, "Texto plano", "report_txt", 3)
        self.row_check(lf2, "Abrir el reporte HTML al terminar", "open_report", 4)

    # --- pestana contenido ---------------------------------------------
    def build_content(self):
        f = self.tab_content
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        ttk.Label(f, text="Marque lo que se debe convertir. Haga doble clic o pulse la barra espaciadora sobre un nodo para marcarlo o desmarcarlo.",
                  wraplength=900, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.tree = ttk.Treeview(f, columns=("tipo", "contenido"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Elemento")
        self.tree.heading("tipo", text="Tipo")
        self.tree.heading("contenido", text="Contenido directo")
        self.tree.column("#0", width=420)
        self.tree.column("tipo", width=130, stretch=False)
        self.tree.column("contenido", width=300)
        vs = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        vs.grid(row=1, column=1, sticky="ns")
        self.tree.bind("<Double-1>", self.on_tree_toggle)
        self.tree.bind("<space>", self.on_tree_toggle)
        self.node_state = {}
        self.node_name = {}
        bt = ttk.Frame(f)
        bt.grid(row=2, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Button(bt, text="Marcar todo", command=lambda: self.set_all(True)).pack(side="left")
        ttk.Button(bt, text="Desmarcar todo", command=lambda: self.set_all(False)).pack(side="left", padx=6)
        ttk.Button(bt, text="Aplicar los nombres excluidos", command=self.apply_tree_defaults).pack(side="left")
        lf = ttk.Frame(f)
        lf.grid(row=3, column=0, columnspan=2, sticky="ew")
        lf.columnconfigure(1, weight=1)
        self.row_entry(lf, "Excluir por nombre, separados por coma", "excluded_names", 0, width=70)
        self.row_check(lf, "Aplanar los contenedores de archivo .ili (el modelo queda directamente dentro del paquete)", "flatten_ili_files", 1)
        self.row_check(lf, "Ordenar alfabéticamente los elementos en el XMI", "sort_alphabetically", 2)

    # --- pestana mapeo -------------------------------------------------
    def build_map(self):
        f = self.tab_map.inner
        f.columnconfigure(1, weight=1)
        pad = ttk.Frame(f, padding=10)
        pad.pack(fill="both", expand=True)
        pad.columnconfigure(1, weight=1)
        lf = ttk.LabelFrame(pad, text=" Formato XMI ", padding=10)
        lf.grid(row=0, column=0, columnspan=3, sticky="ew")
        lf.columnconfigure(1, weight=1)
        self.row_combo(lf, "Dialecto", "dialect", DIALECTS, 0,
                       hint="Si Modelio rechaza el archivo, pruebe el otro dialecto.")
        self.row_combo(lf, "Versión de XMI", "xmi_version", XMI_VERSIONS, 2)
        self.row_entry(lf, "Nombre del modelo raíz", "model_name", 4, hint="Vacio usa el nombre del archivo .uml.")
        self.row_entry(lf, "Prefijo de los identificadores", "id_prefix", 6, width=20)
        self.row_check(lf, "Sangrar el XML para que sea legible", "pretty_print", 7)

        lf = ttk.LabelFrame(pad, text=" Clases, estructuras y dominios ", padding=10)
        lf.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        lf.columnconfigure(1, weight=1)
        self.row_combo(lf, "Estructuras INTERLIS como", "structure_as", STRUCT, 0)
        self.row_combo(lf, "Dominios que no son enumeraciones", "domain_mode", DOMAIN_MODE, 1)
        self.row_entry(lf, "Separador de enumeraciones jerárquicas", "enum_separator", 2, width=8)
        self.row_check(lf, "Generar también un literal por cada nodo intermedio de la jerarquía", "enum_include_nodes", 3)
        self.row_entry(lf, "Nombre de las enumeraciones en línea", "inline_enum_pattern", 4, width=30,
                       hint="Use {clase} y {atributo}. Ejemplo  {clase}_{atributo}")

        lf = ttk.LabelFrame(pad, text=" Asociaciones y dependencias ", padding=10)
        lf.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        lf.columnconfigure(1, weight=1)
        self.row_combo(lf, "Navegabilidad de los roles", "navigability", NAVIG, 0)
        self.row_combo(lf, "Asociaciones con atributos", "assoc_attr_mode", ASSOC_ATTR, 1)
        self.row_check(lf, "Convertir las dependencias (UmlUsage) a relaciones de uso", "include_dependencies", 2)
        self.row_check(lf, "Un atributo MANDATORY fuerza la multiplicidad mínima en 1", "mandatory_forces_lower_one", 3)

        lf = ttk.LabelFrame(pad, text=" Tipos primitivos ", padding=10)
        lf.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        lf.columnconfigure(1, weight=1)
        self.row_combo(lf, "Origen de los tipos primitivos", "primitive_mode", PRIM_MODE, 0)
        self.row_entry(lf, "Paquete de tipos primitivos", "prim_pkg_name", 2, width=30)
        self.row_entry(lf, "Paquete de tipos geométricos INTERLIS", "ili_pkg_name", 3, width=30)
        ttk.Label(lf, text="Nombre de cada tipo primitivo\n(clave=nombre)").grid(row=4, column=0, sticky="nw", pady=4)
        self.txt_prim = tk.Text(lf, height=7, width=36)
        self.txt_prim.grid(row=4, column=1, sticky="w", pady=4)
        self.txt_prim.insert("1.0", self.prim_names_text)
        ttk.Label(lf, text="Equivalencias de dominios INTERLIS\nque no se convierten\n(dominio=tipo primitivo)").grid(row=5, column=0, sticky="nw", pady=4)
        self.txt_dom = tk.Text(lf, height=9, width=36)
        self.txt_dom.grid(row=5, column=1, sticky="w", pady=4)
        self.txt_dom.insert("1.0", self.domain_map_text)

    # --- pestana textos ------------------------------------------------
    def build_text(self):
        f = self.tab_text
        f.columnconfigure(1, weight=1)
        lf = ttk.LabelFrame(f, text=" Nombres ", padding=10)
        lf.grid(row=0, column=0, columnspan=3, sticky="ew")
        lf.columnconfigure(1, weight=1)
        self.row_combo(lf, "Nombre de los elementos", "name_source", NAME_SRC, 0)
        self.row_entry(lf, "Idioma preferido de los textos", "language", 2, width=12,
                       hint="Códigos separados por coma en orden de preferencia, por ejemplo  es,en")
        self.row_check(lf, "Reemplazar espacios y caracteres especiales por guion bajo", "sanitize_names", 4)
        lf = ttk.LabelFrame(f, text=" Información que viaja como comentarios ", padding=10)
        lf.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.row_check(lf, "Documentación de cada elemento", "emit_documentation", 0)
        self.row_check(lf, "Valores etiquetados de INTERLIS (por ejemplo ili2db.dispName)", "emit_tagged_values", 1)
        self.row_check(lf, "Detalles del tipo INTERLIS (longitud de texto, rango, unidad)", "emit_type_details", 2)
        self.row_check(lf, "Notas de los diagramas, en el paquete al que pertenece el diagrama", "emit_notes", 3)
        self.row_check(lf, "Datos del archivo de origen (versión, autor, archivo .ili, VERSION, ISSUER)", "emit_source_info", 4)
        self.row_check(lf, "Lo que UML no admite, como la extensión de temas (TOPIC EXTENDS)", "emit_untranslatable", 5)

    # --- pestana reporte -----------------------------------------------
    def build_report_tab(self):
        f = self.tab_rep
        lf = ttk.LabelFrame(f, text=" Contenido del reporte ", padding=10)
        lf.grid(row=0, column=0, sticky="ew")
        self.row_check(lf, "Listar también cada elemento convertido sin pérdida (el reporte crece mucho)", "list_converted", 0)
        self.row_check(lf, "Listar cada elemento excluido por la selección", "list_excluded", 1)
        ttk.Label(lf, text="Máximo de filas por grupo en el HTML").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Spinbox(lf, from_=10, to=100000, textvariable=self.vars["html_max_items"], width=10).grid(row=2, column=1, sticky="w", padx=8)
        ttk.Label(f, text="El CSV de detalle siempre trae todas las filas.", foreground="#666").grid(row=1, column=0, sticky="w", pady=6)

    # ------------------------------------------------------------------
    # Dialogos de archivo
    # ------------------------------------------------------------------
    def pick_input(self):
        p = filedialog.askopenfilename(title="Seleccione el archivo .uml", filetypes=[("Modelo UML/INTERLIS", "*.uml"), ("Todos", "*.*")])
        if p:
            self.vars["input_path"].set(p)
            out = self.vars["output_path"].get()
            if not out or out.endswith(".xmi"):
                self.vars["output_path"].set(os.path.splitext(p)[0] + ".xmi")
            self.analyze()

    def pick_output(self):
        p = filedialog.asksaveasfilename(title="Archivo XMI de salida", defaultextension=".xmi",
                                         filetypes=[("XMI", "*.xmi"), ("XML", "*.xml"), ("Todos", "*.*")])
        if p:
            self.vars["output_path"].set(p)

    def pick_report_dir(self):
        p = filedialog.askdirectory(title="Carpeta del reporte")
        if p:
            self.vars["report_dir"].set(p)

    def save_cfg_dialog(self):
        p = filedialog.asksaveasfilename(title="Guardar configuración", defaultextension=".json",
                                         filetypes=[("Configuración JSON", "*.json")])
        if p:
            try:
                self.collect_cfg().save(p)
                self.write_log("Configuración guardada en %s" % p)
            except Exception as ex:
                messagebox.showerror("Error", str(ex))

    def load_cfg_dialog(self):
        p = filedialog.askopenfilename(title="Cargar configuración", filetypes=[("Configuración JSON", "*.json"), ("Todos", "*.*")])
        if p:
            try:
                self.push_cfg(Config.load(p))
                self.write_log("Configuración cargada desde %s" % p)
            except Exception as ex:
                messagebox.showerror("Error", "No se pudo leer la configuración.\n%s" % ex)

    def reset_cfg(self):
        base = Config()
        cur = self.collect_cfg()
        base.input_path, base.output_path, base.report_dir = cur.input_path, cur.output_path, cur.report_dir
        self.push_cfg(base)

    # ------------------------------------------------------------------
    # Analisis y arbol
    # ------------------------------------------------------------------
    def analyze(self):
        path = self.vars["input_path"].get().strip()
        if not path or not os.path.exists(path):
            messagebox.showwarning("Archivo", "Seleccione un archivo .uml existente.")
            return
        self.lbl_info.config(text="Analizando...")
        self.config(cursor="watch")
        self.update_idletasks()

        def work():
            try:
                u = UmlFile(path)
                self.q.put(("analyzed", u))
            except Exception as ex:
                self.q.put(("analyze_error", "%s" % ex))
        threading.Thread(target=work, daemon=True).start()

    def populate_tree(self, u):
        self.uml = u
        self.tree.delete(*self.tree.get_children())
        self.node_state.clear()
        self.node_name.clear()
        names = {n.strip() for n in self.vars["excluded_names"].get().split(",") if n.strip()}
        excl = set(self.saved_excl)
        incl = set(self.saved_incl)
        root = u.get("0")
        counts_total = {}

        def add(container, parent_iid):
            kids = u.children(container)
            for ch in kids:
                if ch.tag == "INTERLIS2Def":
                    add(ch, parent_iid)
                elif ch.tag in ("UmlPackage", "ModelDef", "TopicDef"):
                    nm = u.name(ch) or ch.tag
                    cls = sum(1 for k in u.children(ch) if k.tag == "ClassDef")
                    asc = sum(1 for k in u.children(ch) if k.tag == "AssociationDef")
                    dom = sum(1 for k in u.children(ch) if k.tag == "DomainDef")
                    parts = []
                    if cls:
                        parts.append("%d clases" % cls)
                    if asc:
                        parts.append("%d asociaciones" % asc)
                    if dom:
                        parts.append("%d dominios" % dom)
                    on = True
                    if ch.tid in excl:
                        on = False
                    elif ch.tid in incl:
                        on = True
                    elif nm in names:
                        on = False
                    if parent_iid and not self.node_state.get(parent_iid, True):
                        on = False
                    label = {"UmlPackage": "Paquete", "ModelDef": "Modelo", "TopicDef": "Tema"}[ch.tag]
                    iid = ch.tid
                    self.tree.insert(parent_iid or "", "end", iid=iid, text=(CHK_ON if on else CHK_OFF) + nm,
                                     values=(label, ", ".join(parts)), open=True)
                    self.node_state[iid] = on
                    self.node_name[iid] = nm
                    add(ch, iid)
        add(root, "")
        self.tree_loaded = True
        n_cls = sum(1 for e in u.order if e.tag == "ClassDef")
        n_assoc = sum(1 for e in u.order if e.tag == "AssociationDef")
        n_dom = sum(1 for e in u.order if e.tag == "DomainDef")
        n_diag = sum(1 for e in u.order if e.full.endswith("umlpresentation.Diagram"))
        self.lbl_info.config(text="Archivo válido del UML/INTERLIS Editor. Modelo %s, versión %s, autor %s.\n"
                                  "Contiene %d clases y estructuras, %d asociaciones, %d dominios y %d diagramas (%d elementos en total)." % (
                                      u.name(root) or "", root.f("Version") or "?", root.f("Author") or "?",
                                      n_cls, n_assoc, n_dom, n_diag, len(u.by_tid)))
        self.write_log("Archivo analizado, %d elementos" % len(u.by_tid))

    def apply_tree_defaults(self):
        if not self.tree_loaded:
            return
        names = {n.strip() for n in self.vars["excluded_names"].get().split(",") if n.strip()}
        excl = set(self.saved_excl)

        def rec(iid, parent_on):
            nm = self.node_name[iid]
            on = parent_on and not (iid in excl or nm in names)
            self.set_node(iid, on)
            for c in self.tree.get_children(iid):
                rec(c, on)
        for c in self.tree.get_children(""):
            rec(c, True)

    def set_node(self, iid, on):
        self.node_state[iid] = on
        self.tree.item(iid, text=(CHK_ON if on else CHK_OFF) + self.node_name[iid])

    def on_tree_toggle(self, event):
        iid = self.tree.focus() if event.type == tk.EventType.KeyPress else self.tree.identify_row(event.y)
        if not iid:
            return "break"
        new = not self.node_state.get(iid, True)
        self.set_subtree(iid, new)
        if new:
            p = self.tree.parent(iid)
            while p:
                self.set_node(p, True)
                p = self.tree.parent(p)
        return "break"

    def set_subtree(self, iid, on):
        self.set_node(iid, on)
        for c in self.tree.get_children(iid):
            self.set_subtree(c, on)

    def set_all(self, on):
        for c in self.tree.get_children(""):
            self.set_subtree(c, on)

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------
    def write_log(self, msg):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def set_busy(self, busy):
        self.btn_convert.config(state="disabled" if busy else "normal")
        self.btn_cancel.config(state="normal" if busy else "disabled")

    def start_convert(self):
        if self.worker and self.worker.is_alive():
            return
        cfg = self.collect_cfg()
        if not cfg.input_path or not os.path.exists(cfg.input_path):
            messagebox.showwarning("Archivo", "Seleccione un archivo .uml existente.")
            return
        if not cfg.output_path:
            cfg.output_path = os.path.splitext(cfg.input_path)[0] + ".xmi"
            self.vars["output_path"].set(cfg.output_path)
        if not cfg.report_dir:
            cfg.report_dir = os.path.dirname(os.path.abspath(cfg.output_path))
        if not any((cfg.report_html, cfg.report_csv, cfg.report_json, cfg.report_txt)):
            if not messagebox.askyesno("Reporte", "No marcó ningún formato de reporte. Desea convertir sin reporte?"):
                return
        if os.path.exists(cfg.output_path):
            if not messagebox.askyesno("Sobrescribir", "El archivo de salida ya existe.\n%s\n\nDesea reemplazarlo?" % cfg.output_path):
                return
        try:
            cfg.save(CONFIG_FILE)
        except Exception:
            pass
        self.cancel_flag.clear()
        self.set_busy(True)
        self.btn_open_rep.config(state="disabled")
        self.btn_open_dir.config(state="disabled")
        self.progress["value"] = 0
        self.write_log("-" * 50)
        self.write_log("Iniciando la conversión")

        def log(m):
            self.q.put(("log", m))

        def prog(p, m=""):
            self.q.put(("progress", (p, m)))

        def work():
            try:
                conv = Converter(cfg, log=log, progress=prog, cancel_check=self.cancel_flag.is_set,
                                 uml=self.uml if (self.uml and self.uml.path == cfg.input_path) else None)
                rep = conv.run()
                base = os.path.join(cfg.report_dir, os.path.splitext(os.path.basename(cfg.output_path))[0] + "_reporte")
                files = []
                if any((cfg.report_html, cfg.report_csv, cfg.report_json, cfg.report_txt)):
                    os.makedirs(cfg.report_dir, exist_ok=True)
                    files = rep.write_all(base)
                self.q.put(("done", (rep, files, cfg)))
            except Cancelled:
                self.q.put(("cancelled", None))
            except Exception as ex:
                self.q.put(("error", (str(ex), traceback.format_exc())))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def cancel(self):
        self.cancel_flag.set()
        self.write_log("Cancelando...")

    def poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self.write_log(data)
                elif kind == "progress":
                    self.progress["value"] = data[0]
                    self.lbl_prog.config(text=data[1])
                elif kind == "analyzed":
                    self.config(cursor="")
                    self.populate_tree(data)
                elif kind == "analyze_error":
                    self.config(cursor="")
                    self.tree_loaded = False
                    self.lbl_info.config(text="No se pudo analizar el archivo.")
                    messagebox.showerror("Archivo no válido", data)
                elif kind == "done":
                    self.on_done(*data)
                elif kind == "cancelled":
                    self.set_busy(False)
                    self.lbl_prog.config(text="Cancelado")
                    self.write_log("Conversión cancelada")
                elif kind == "error":
                    self.set_busy(False)
                    self.lbl_prog.config(text="Error")
                    self.write_log(data[1])
                    messagebox.showerror("Error en la conversión", data[0])
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def on_done(self, rep, files, cfg):
        self.set_busy(False)
        self.last_report_files = files
        self.last_output = cfg.output_path
        self.btn_open_dir.config(state="normal")
        html_files = [f for f in files if f.endswith(".html")]
        if files:
            self.btn_open_rep.config(state="normal")
        self.write_log("Convertidos %d, con pérdida parcial %d, perdidos %d, excluidos %d" % (
            rep.total("converted"), rep.total("degraded"), rep.total("lost"), rep.total("excluded")))
        self.write_log("Validación del XMI  %s" % rep.validation.get("Resultado"))
        for f in files:
            self.write_log("Reporte  %s" % f)
        msg = ("Conversión terminada.\n\nConvertidos sin pérdida  %d\nConvertidos con pérdida parcial  %d\n"
               "No convertidos  %d\nExcluidos por la selección  %d\nFormas gráficas de diagramas que no se importan  %d\n\nValidación del XMI  %s" % (
                   rep.total("converted"), rep.total("degraded"), rep.total("lost"), rep.total("excluded"),
                   rep.graphic_total(), rep.validation.get("Resultado")))
        messagebox.showinfo("Conversión terminada", msg)
        if cfg.open_report and html_files:
            open_path(html_files[0])

    def open_report(self):
        for f in self.last_report_files:
            if f.endswith(".html"):
                open_path(f)
                return
        if self.last_report_files:
            open_path(self.last_report_files[0])

    def open_out_dir(self):
        if self.last_output:
            open_path(os.path.dirname(os.path.abspath(self.last_output)))

    def on_close(self):
        try:
            self.collect_cfg().save(CONFIG_FILE)
        except Exception:
            pass
        self.cancel_flag.set()
        self.destroy()


def run_gui(initial_file=None):
    app = App(initial_file)
    app.mainloop()
