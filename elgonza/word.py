"""Facturas en Word (.docx). Se lee el XML del documento, sin depender de Word.

Muchas facturas hechas en Word ponen cada columna de la tabla en una sola celda alta, con un renglón por ítem
(descripciones en una celda, precios en otra, importes en otra). Se buscan dos celdas con la misma cantidad de
números en las que importe ÷ precio da una cantidad entera: esos son los ítems; las descripciones son los bloques que
empiezan con «#» o con un código en la celda de texto más larga.
"""
import re
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NUM = re.compile(r"(?<![\w.,])(?:US\$|USD|\$)?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\w.,/%])")


def _parrafos(nodo):
    lineas = []
    for p in nodo.iter(W + "p"):
        partes, actual = [], ""
        for e in p.iter():
            if e.tag == W + "t" and e.text:
                actual += e.text
            elif e.tag in (W + "br", W + "cr"):
                partes.append(actual)
                actual = ""
            elif e.tag == W + "tab":
                actual += " "
        partes.append(actual)
        lineas += [" ".join(x.split()) for x in partes]
    return lineas


def leer_docx(ruta):
    """(texto completo, [ítems]) de una factura en Word. Cada ítem: codigo, descripcion, cant, precio, importe."""
    with zipfile.ZipFile(ruta) as z:
        raiz = ET.fromstring(z.read("word/document.xml"))
    texto = "\n".join(_parrafos(raiz))
    celdas = []
    for tabla in raiz.iter(W + "tbl"):
        for fila in tabla.iter(W + "tr"):
            for j, tc in enumerate(fila.findall(W + "tc")):
                celdas.append((j, _parrafos(tc)))

    def numeros(lineas):
        res = []
        for l in lineas:
            if re.search(r"[A-Za-z]{3,}", re.sub(r"(?i)US\$|USD", "", l)):
                continue
            m = NUM.search(l)
            if m:
                res.append(float(m.group(1).replace(",", "")))
        return res

    mejor = None
    for a, (ja, la) in enumerate(celdas):
        pa = numeros(la)
        if not pa:
            continue
        for b, (jb, lb) in enumerate(celdas):
            if b == a or jb < ja:
                continue
            ib = numeros(lb)
            if len(ib) != len(pa) or sum(ib) <= sum(pa):
                continue
            cants = [i / p if p else 0 for p, i in zip(pa, ib)]
            if all(c >= 1 and abs(c - round(c)) < 0.001 for c in cants):
                if not mejor or len(pa) > len(mejor[0]):
                    mejor = (pa, ib, [round(c) for c in cants])
    if not mejor:
        return texto, []
    precios, importes, cants = mejor
    n = len(precios)
    # descripciones: bloques que empiezan con «#» o con un código, en la celda que más tiene
    bloques_mejor = []
    for _, lineas in celdas:
        bloques, actual = [], None
        for l in lineas:
            if re.match(r"#|[A-Z]{2,}[A-Z0-9\-]*\d[A-Z0-9\-]*\b", l):
                if actual:
                    bloques.append(actual)
                actual = [l]
            elif actual is not None:
                if not l.strip() or re.match(r"(?i)bank|total|declaration", l):
                    bloques.append(actual)
                    actual = None
                else:
                    actual.append(l)
        if actual:
            bloques.append(actual)
        if len(bloques) == n and len(" ".join(map(" ".join, bloques))) > len(" ".join(map(" ".join, bloques_mejor))):
            bloques_mejor = bloques
    items = []
    for i in range(n):
        desc = " ".join(bloques_mejor[i]) if bloques_mejor else ""
        codigo = ""
        m = re.match(r"#?\s*([A-Z]{2,}[A-Z0-9]*\d[A-Z0-9]*)\s*-?\s*", desc)
        if m:
            codigo, desc = m.group(1), desc[m.end():]
        items.append({"nro": str(i + 1), "codigo": codigo, "descripcion": desc.strip(" -"), "cant": cants[i],
                      "precio": precios[i], "importe": importes[i]})
    return texto, items
