# Convertidor LADM-COL (.uml) a XMI 2.1 para Modelio

Aplicación de escritorio en Python que lee el formato nativo del UML/INTERLIS Editor (los `.uml` de LADM-COL) y genera un XMI 2.1 que Modelio puede importar. Cada conversión produce un reporte de lo que se convirtió, lo que se degradó y lo que se pierde.

## Por qué hace falta

Un `.uml` de LADM-COL parece XMI pero no lo es. Es el archivo de proyecto del UML/INTERLIS Editor, con elementos propios como `ch.ehi.interlis.modeltopicclass.ClassDef`. Modelio no lo reconoce y responde que el formato no es válido.

## Requisitos

Python 3.9 o superior con Tkinter. Solo usa la biblioteca estándar.

En Windows y macOS, Tkinter viene con el instalador oficial de Python. En Ubuntu o Debian hay que instalarlo con `sudo apt install python3-tk`.

## Uso

```
python ladm_uml_a_xmi.py
```

1. En la pestaña Archivos, elija el `.uml` y pulse Analizar archivo.
2. En Contenido, marque los modelos y temas que quiere convertir. Por defecto se excluyen los modelos predefinidos de INTERLIS.
3. Ajuste lo que necesite en Mapeo a UML y Textos y metadatos.
4. Pulse Convertir. Al terminar se abre el reporte HTML.

La configuración se guarda sola en `~/.ladm_uml2xmi.json` y también se puede guardar y cargar desde el menú Archivo.

También funciona sin interfaz.

```
python ladm_uml_a_xmi.py --cli entrada.uml -o salida.xmi -c configuracion.json
```

## Cómo se convierte cada elemento

| Origen (INTERLIS) | Destino (UML 2) |
|---|---|
| Modelo, tema, paquete | Paquete |
| CLASS | Clase |
| STRUCTURE | DataType (o Clase, configurable) |
| Atributo | Propiedad con multiplicidad y tipo |
| Dominio con enumeración | Enumeración con literales |
| Enumeración en línea de un atributo | Enumeración generada con nombre `{clase}_{atributo}` |
| Enumeración jerárquica | Literales aplanados con separador, por ejemplo `Documento_Publico.Escritura_Publica` |
| Otros dominios | DataType con comentario (o tipo base en cada atributo) |
| Asociación binaria | Asociación, o clase de asociación si tiene atributos |
| EXTENDS entre clases | Generalización |
| Dependencia (UmlUsage) | Usage |
| Documentación | Comentario |
| Valores etiquetados | Comentario con prefijo `[INTERLIS]` |

Los tipos TEXT, numéricos y BOOLEAN pasan a tipos primitivos. El rango, la unidad y la longitud máxima viajan como comentario. Los tipos geométricos (POLYLINE, SURFACE, COORD) pasan a DataType en el paquete de tipos INTERLIS.

## Qué se pierde

El reporte lo detalla elemento por elemento. En general no viajan los diagramas (posiciones, tamaños, colores y trazado de líneas), las unidades, funciones y formas de línea de INTERLIS, las restricciones, la extensión de temas (queda como comentario), y los restos huérfanos que el editor deja dentro del archivo, como generalizaciones sin clase hija y atributos sin dueño.

## Reportes

Se generan en HTML, CSV de detalle, CSV de resumen y, opcionalmente, JSON y texto. El reporte incluye validación del XMI generado (XML bien formado, identificadores únicos, referencias con destino) y el listado de diagramas del origen, útil para rearmarlos en Modelio.

## Si Modelio rechaza el XMI

Pruebe en este orden. Cambie el dialecto a OMG UML 2.1, cambie la versión a XMI 2.5.1, cambie los tipos primitivos a la biblioteca estándar, y convierta primero un solo tema para aislar el problema.

## Crear un ejecutable

```
pip install pyinstaller
pyinstaller --onefile --windowed --name ConvertidorLADMCOL ladm_uml_a_xmi.py
```
## Autor
Semillero de Investigación OSGeoLAB-UD
osgeolab@udistrital.edu.co
Tecnologías geoespaciales e Inteligencia Artificial Geoespacial (GeoAI) de software libre y código abierto 
