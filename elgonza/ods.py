"""Guarda un libro de openpyxl como planilla de OpenOffice / LibreOffice (.ods), sin depender de otras librerías.

Pasa valores (texto, números, fechas como texto), negrita, color de fondo, ancho de columnas, formato de número
(miles y decimales) y la primera fila fija.
"""
import re
import zipfile
from xml.sax.saxutils import escape

MIME = "application/vnd.oasis.opendocument.spreadsheet"
NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
      'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
      'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
      'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
      'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
      'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" '
      'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0" office:version="1.2"')


def _decimales(fmt):
    """(mínimo, máximo) de decimales de un formato como «#,##0.00#»."""
    m = re.search(r"\.([0#]+)", fmt or "")
    if not m:
        return None
    return m.group(1).count("0"), len(m.group(1))


def guardar_ods(wb, ruta):
    estilos_num, estilos_celda, celdas_xml = {}, {}, []

    def estilo(c):
        bold = bool(c.font and c.font.b)
        fondo = None
        if c.fill and c.fill.fill_type == "solid":
            rgb = str(c.fill.fgColor.rgb or "")
            if re.fullmatch(r"[0-9A-Fa-f]{6,8}", rgb):
                fondo = "#" + rgb[-6:]
        dec = _decimales(c.number_format) if isinstance(c.value, (int, float)) else None
        clave = (bold, fondo, dec)
        if clave == (False, None, None):
            return ""
        if clave not in estilos_celda:
            num = ""
            if dec:
                if dec not in estilos_num:
                    estilos_num[dec] = f"N{len(estilos_num) + 1}"
                num = f' style:data-style-name="{estilos_num[dec]}"'
            nombre = f"ce{len(estilos_celda) + 1}"
            props = (f'<style:table-cell-properties fo:background-color="{fondo}"/>' if fondo else "") + \
                    ('<style:text-properties fo:font-weight="bold"/>' if bold else "")
            estilos_celda[clave] = (nombre, f'<style:style style:name="{nombre}" style:family="table-cell"{num}>'
                                            f'{props}</style:style>')
        return f' table:style-name="{estilos_celda[clave][0]}"'

    hojas, anchos = [], {}
    for ws in wb.worksheets:
        filas = []
        ncols = ws.max_column
        cols = []
        for i in range(1, ncols + 1):
            letra = ws.cell(1, i).column_letter
            w = ws.column_dimensions[letra].width if letra in ws.column_dimensions else None
            w = round((w or 10) * 0.19, 2)
            if w not in anchos:
                anchos[w] = f"co{len(anchos) + 1}"
            cols.append(f'<table:table-column table:style-name="{anchos[w]}"/>')
        for fila in ws.iter_rows():
            celdas = []
            for c in fila:
                v, st = c.value, estilo(c)
                if v is None:
                    celdas.append(f"<table:table-cell{st}/>")
                elif isinstance(v, bool) or not isinstance(v, (int, float)):
                    celdas.append(f'<table:table-cell{st} office:value-type="string"><text:p>{escape(str(v))}'
                                  f'</text:p></table:table-cell>')
                else:
                    celdas.append(f'<table:table-cell{st} office:value-type="float" office:value="{v!r}">'
                                  f'<text:p>{v}</text:p></table:table-cell>')
            filas.append("<table:table-row>" + "".join(celdas) + "</table:table-row>")
        hojas.append(f'<table:table table:name="{escape(ws.title)}">' + "".join(cols) + "".join(filas)
                     + "</table:table>")

    numeros = "".join(
        f'<number:number-style style:name="{n}"><number:number number:decimal-places="{mx}" '
        f'number:min-decimal-places="{mn}" number:min-integer-digits="1" number:grouping="true"/></number:number-style>'
        for (mn, mx), n in estilos_num.items())
    columnas = "".join(f'<style:style style:name="{n}" style:family="table-column"><style:table-column-properties '
                       f'style:column-width="{w}cm"/></style:style>' for w, n in anchos.items())
    content = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {NS}><office:automatic-styles>'
               + numeros + columnas + "".join(x for _, x in estilos_celda.values())
               + '</office:automatic-styles><office:body><office:spreadsheet>' + "".join(hojas)
               + "</office:spreadsheet></office:body></office:document-content>")
    primera = wb.worksheets[0].title
    settings = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-settings {NS}><office:settings>'
                '<config:config-item-set config:name="ooo:view-settings"><config:config-item-map-indexed '
                'config:name="Views"><config:config-item-map-entry><config:config-item-map-named config:name="Tables">'
                f'<config:config-item-map-entry config:name="{escape(primera)}">'
                '<config:config-item config:name="VerticalSplitMode" config:type="short">2</config:config-item>'
                '<config:config-item config:name="VerticalSplitPosition" config:type="int">1</config:config-item>'
                '<config:config-item config:name="ActiveSplitRange" config:type="short">2</config:config-item>'
                '<config:config-item config:name="PositionBottom" config:type="int">1</config:config-item>'
                '</config:config-item-map-entry></config:config-item-map-named></config:config-item-map-entry>'
                '</config:config-item-map-indexed></config:config-item-set></office:settings></office:document-settings>')
    manifest = ('<?xml version="1.0" encoding="UTF-8"?><manifest:manifest '
                'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
                f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{MIME}"/>'
                '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                '<manifest:file-entry manifest:full-path="settings.xml" manifest:media-type="text/xml"/>'
                '</manifest:manifest>')
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), MIME, compress_type=zipfile.ZIP_STORED)
        z.writestr("content.xml", content, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("settings.xml", settings, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("META-INF/manifest.xml", manifest, compress_type=zipfile.ZIP_DEFLATED)
    return ruta
