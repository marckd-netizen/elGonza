"""NCM sugerido a 10 dígitos (Arancel Nacional de Uruguay).

Orden: 1) antecedentes del usuario (Documentos\\elGonza\\antecedentes_ncm.txt), 2) reglas por tipo de mercadería.
Si nada aplica, queda sin sugerencia. Todo NCM sugerido se verifica contra el arancel.
"""
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATOS = Path(__file__).parent / "datos"


@dataclass
class Posicion:
    ncm: str
    descripcion: str
    aec: str = ""
    cl: str = ""
    ez: str = ""
    iz: str = ""
    uvf: str = ""


@lru_cache(maxsize=1)
def arancel():
    """{ncm: Posicion} con todas las posiciones (capítulos, partidas, subpartidas y aperturas de 10 dígitos)."""
    res = {}
    with open(DATOS / "arancel.tsv", encoding="utf-8") as fh:
        for l in fh:
            if l.startswith("#"):
                continue
            c = l.rstrip("\n").split("\t")
            res[c[0]] = Posicion(*(c + [""] * (7 - len(c)))[:7])
    return res


def descripcion_completa(ncm):
    """Texto de la posición con sus niveles superiores: «Autoadhesivos › Los demás»."""
    t = arancel()
    partes = []
    for k in (ncm[:7], ncm[:9], ncm[:10], ncm):
        p = t.get(k)
        if p and (not partes or p.descripcion != partes[-1]):
            partes.append(p.descripcion.lstrip("- ").rstrip(":"))
    return " › ".join(partes)


# --- antecedentes ------------------------------------------------------------
EJEMPLO_ANTECEDENTES = """\
# Antecedentes de NCM para la planilla de facturas.
# Una línea por producto:  código o texto de la descripción | NCM a 10 dígitos | descripción en español (opcional)
# El código se compara entero (sin guiones ni espacios); el texto, como parte de la descripción de la factura.
# Ejemplos:
# AL-MH | 4811.41.90.00 | PAPEL AUTOADHESIVO SEMIBRILLO EN BOBINAS
# lamination film | 3919.90.90.00 | PELICULA PLASTICA AUTOADHESIVA PARA LAMINADO
"""


def ruta_antecedentes():
    d = Path.home() / "Documents" / "elGonza"
    d.mkdir(parents=True, exist_ok=True)
    return d / "antecedentes_ncm.txt"


def antecedentes(ruta=None):
    ruta = Path(ruta) if ruta else ruta_antecedentes()
    if not ruta.exists():
        try:
            ruta.write_text(EJEMPLO_ANTECEDENTES, encoding="utf-8")
        except OSError:
            pass
        return []
    res = []
    for l in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        if l.strip().startswith("#") or "|" not in l:
            continue
        c = [x.strip() for x in l.split("|")]
        if len(c) >= 2 and c[0] and re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\.\d{2}", c[1]):
            res.append((c[0], c[1], c[2] if len(c) > 2 else ""))
    return res


def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


# --- reglas ------------------------------------------------------------------
ADHESIVO = re.compile(r"(?i)adhesiv|adhesive|acrylic|acr[ií]lic|hot ?melt|\bPSA\b|glassine|liner|self[- ]?adh|autoadh|sticker|label stock")
PAPEL = re.compile(r"(?i)\bpaper\b|\bpapel\b|kraft|thermal|t[ée]rmico|couch[ée]|\bart\b")
FILM = re.compile(r"(?i)\bfilm\b|pel[ií]cula|\bfoil\b|vinyl|vinilo|\bB?O?PP\b|\bPET\b|\bPE\b|\bPVC\b|polyester|poli[ée]ster|polypropylene|polipropileno|polyethylene|polietileno|lamination|laminado")
PP = re.compile(r"(?i)\bB?O?PP\b|polypropylene|polipropileno")
PVC = re.compile(r"(?i)\bPVC\b|vinyl|vinilo|poly\s?vinyl")


def _desc_es(desc, base):
    extra = []
    if re.search(r"(?i)semi ?gloss", desc):
        extra.append("SEMIBRILLO")
    elif re.search(r"(?i)\bgloss", desc):
        extra.append("BRILLANTE")
    elif re.search(r"(?i)matte?\b", desc):
        extra.append("MATE")
    for en, es in (("white", "BLANCO"), ("transparent|clear", "TRANSPARENTE"), ("metalli[sz]ed", "METALIZADO")):
        if re.search(rf"(?i)\b({en})\b", desc.split("/")[0]):
            extra.append(es)
    if re.search(r"(?i)top ?coat", desc):
        extra.append("CON TOP COATING")
    if re.search(r"(?i)acrylic", desc):
        extra.append("ADHESIVO ACRILICO")
    if re.search(r"(?i)glassine", desc):
        extra.append("SOPORTE GLASSINE")
    return " ".join([base] + extra)


def aperturas(ncm8):
    """Aperturas nacionales (10 dígitos) de un NCM de 8."""
    d = re.sub(r"\D", "", ncm8)[:8]
    return sorted(k for k in arancel() if len(k) == 13 and k.replace(".", "").startswith(d))


def sugerir(codigo, descripcion, ancho_mm=None, ncm_factura="", importador="", proveedor=""):
    """(ncm, descripción en español, motivo). ncm vacío si no hay sugerencia."""
    for clave, ncm, desc_es in antecedentes():
        if (_norm(clave) and _norm(clave) == _norm(codigo)) or \
                (not re.fullmatch(r"[A-Z0-9\-#()./]+", clave) and clave.lower() in (descripcion or "").lower()):
            return _verificar(ncm, desc_es, f"Antecedente: «{clave}» → {ncm}.")
    try:
        from . import historial
        previo = historial.buscar(codigo, descripcion, importador, proveedor)
    except Exception:
        previo = None
    if previo and previo[3] >= 1.0 and previo[0] in arancel():
        return _verificar(previo[0], previo[1], previo[2])
    if previo and not ncm_factura and previo[3] >= 0.6 and previo[0] in arancel():
        return _verificar(previo[0], previo[1], previo[2])
    if ncm_factura:
        d = re.sub(r"\D", "", ncm_factura)
        legible = f"{d[:4]}.{d[4:6]}.{d[6:]}"
        ops = aperturas(d)
        if len(d) == 10:
            ops = [k for k in ops if k.replace(".", "") == d] or ops
        if len(ops) == 1:
            return _verificar(ops[0], "", f"NCM de la factura ({legible}); tiene una sola apertura nacional.")
        if ops:
            t = arancel()
            elegido = next((k for k in ops if t[k].descripcion.lower().startswith(("las demás", "los demás"))), ops[0])
            otras = "; ".join(f"{k}: {t[k].descripcion}" for k in ops)
            return _verificar(elegido, "", f"NCM de la factura ({legible}), con varias aperturas nacionales: {otras}. "
                                           "Elegir la que corresponda.")
        # un HS code de otro país (4901.99.9000 de EE.UU.) solo coincide en los primeros 6 dígitos
        seis = sorted(k for k in arancel() if len(k) == 13 and k.replace(".", "").startswith(d[:6])) \
            if len(d) >= 6 else []
        if seis:
            regla = _por_reglas(codigo, descripcion, ancho_mm)
            elegido = regla[0] if regla[0] in seis else next(
                (k for k in seis if arancel()[k].descripcion.lower().lstrip("- ").startswith(("las demás", "los demás"))),
                seis[0])
            motivo = (f"La factura declara el HS {legible}, que no es un NCM; se toma la subpartida {d[:4]}.{d[4:6]}"
                      + (f" y su única apertura nacional." if len(seis) == 1 else
                         f", con varias aperturas: {'; '.join(seis)}. Elegir la que corresponda."))
            if regla[0] and regla[0] not in seis:
                motivo += f" Ojo: por la descripción podría ir en {regla[0]} ({regla[2].split('. ')[0]})."
            return _verificar(elegido, regla[1] if regla[0] == elegido else NOMBRE_PARTIDA.get(elegido[:4], ""), motivo)
        aviso = f"El NCM de la factura ({legible}) no está en el arancel. "
    else:
        aviso = ""
    ncm, desc_es, motivo = _por_reglas(codigo, descripcion, ancho_mm)
    if not ncm and previo and previo[3] >= 0.3 and previo[0] in arancel():
        return _verificar(previo[0], previo[1], aviso + previo[2] + " Parecido débil: verificar.")
    return ncm, desc_es, aviso + motivo


def _por_reglas(codigo, descripcion, ancho_mm):
    d = descripcion or ""
    angosto = ancho_mm is not None and ancho_mm <= 200
    if ADHESIVO.search(d) and PAPEL.search(d) and not FILM.search(d):
        if ancho_mm is not None and ancho_mm <= 150:
            return _verificar("4811.41.10.00", _desc_es(d, "PAPEL AUTOADHESIVO"),
                              f"Papel autoadhesivo en bobinas de hasta 15 cm de ancho ({ancho_mm:g} mm).")
        motivo = "Papel autoadhesivo (4811.41) que no es tira o bobina de hasta 15 cm ni hoja de hasta 36 cm."
        if ancho_mm is None:
            motivo += " No encontré el ancho: si es de 15 cm o menos, va en 4811.41.10.00."
        return _verificar("4811.41.90.00", _desc_es(d, "PAPEL AUTOADHESIVO"), motivo)
    if ADHESIVO.search(d) and FILM.search(d):
        mat = "PP" if PP.search(d) else "PVC" if PVC.search(d) else ""
        base = {"PP": "PELICULA DE POLIPROPILENO AUTOADHESIVA", "PVC": "PELICULA DE PVC AUTOADHESIVA"}.get(
            mat, "PELICULA PLASTICA AUTOADHESIVA")
        if angosto:
            ncm = {"PP": "3919.10.10.00", "PVC": "3919.10.20.00"}.get(mat, "3919.10.90.00")
            motivo = f"Plástico autoadhesivo en rollos de hasta 20 cm de ancho ({ancho_mm:g} mm)"
        else:
            ncm = {"PP": "3919.90.10.90", "PVC": "3919.90.20.90"}.get(mat, "3919.90.90.00")
            motivo = "Plástico autoadhesivo (3919) en rollos de más de 20 cm" if ancho_mm else \
                "Plástico autoadhesivo (3919); no encontré el ancho, si es de 20 cm o menos va en 3919.10"
        motivo += {"PP": ", de polipropileno.", "PVC": ", de PVC."}.get(
            mat, ". La factura no dice de qué plástico es: si es polipropileno va en 3919.x0.10, si es PVC en 3919.x0.20."
            " Pedir la ficha técnica.")
        if mat == "" and re.search(r"(?i)acrylic", d):
            motivo += " «Acrylic» se toma como el adhesivo, no como el material del film."
        return _verificar(ncm, _desc_es(d, base), motivo)
    for regla in REGLAS:
        if re.search(regla[0], d, re.I) and not (len(regla) > 4 and re.search(regla[4], d, re.I)):
            ncm, base, motivo = regla[1], regla[2], regla[3]
            mat = next((es for patron, es in MATERIALES if re.search(patron, d, re.I)), "")
            return _verificar(ncm, f"{base} {mat}".strip(), motivo)
    return "", "", "Sin sugerencia: agregá el producto a antecedentes_ncm.txt para la próxima vez."


# Reglas por palabras de la descripción: (patrón, NCM, descripción en español, motivo[, excepto]).
# Van de lo más específico a lo más general; gana la primera que coincide.
# nombre en español por partida, para la columna DESCRIPCION cuando el NCM sale del HS code de la factura
NOMBRE_PARTIDA = {"4901": "LIBRO", "4902": "PUBLICACION PERIODICA", "4903": "LIBRO PARA COLOREAR",
                  "4909": "TARJETAS POSTALES IMPRESAS", "4911": "IMPRESOS"}

REGLAS = [
    # capítulo 49: libros e impresos
    (r"colou?ring book|libros? para colorear|para colorear|picture book|sticker book", "4903.00.00.00",
     "LIBRO PARA COLOREAR", "Libro de estampas o para colorear, para niños (4903). Si no es para niños va en 4901."),
    (r"postcards?|postales", "4909.00.00.00", "TARJETAS POSTALES IMPRESAS", "Tarjetas postales impresas (4909)."),
    (r"dictionar|diccionario|encyclopa?edia|enciclopedia", "4901.91.00.00", "DICCIONARIO",
     "Diccionarios y enciclopedias (4901.91)."),
    (r"\bbooks?\b|\blibros?\b|novel|paperback|hardcover|tapa (dura|blanda)|\bISBN\b|biblia|bible", "4901.99.00.00",
     "LIBRO", "Libro impreso (4901.99)."),
    # 84.81 grifería y válvulas
    (r"pressure reduc|reductora de presi", "8481.10.00.90", "VALVULA REDUCTORA DE PRESION",
     "Válvula reductora de presión (8481.10). Si es para vehículos va en .10 y si es para garrafas de GLP en .20."),
    (r"check valve|foot valve|non[- ]?return|swing check|retenci[oó]n|v[aá]lvula de pie", "8481.30.00.90",
     "VALVULA DE RETENCION", "Válvula de retención (8481.30); las válvulas de pie también lo son. "
     "Si es para vehículos va en .10 y si es para garrafas de GLP en .20."),
    (r"safety valve|relief valve|v[aá]lvula de (seguridad|alivio)", "8481.40.00.90", "VALVULA DE SEGURIDAD",
     "Válvula de alivio o seguridad (8481.40)."),
    (r"solenoid", "8481.80.92.90", "VALVULA SOLENOIDE", "Válvula solenoide (8481.80.92)."),
    (r"butterfly valve|v[aá]lvula mariposa", "8481.80.97.90", "VALVULA MARIPOSA", "Válvula mariposa (8481.80.97).",
     r"butterfly handle"),
    (r"gate valve|v[aá]lvula esclusa", "8481.80.93.00", "VALVULA ESCLUSA", "Válvula esclusa (8481.80.93)."),
    (r"globe valve|v[aá]lvula globo", "8481.80.94.00", "VALVULA GLOBO", "Válvula globo (8481.80.94)."),
    (r"bibcock|faucet|\btap\b|canilla|grifo", "8481.80.19.00", "CANILLA",
     "Canilla o grifo (8481.80.1, de los tipos usados en baños o cocinas). Si es de jardín o industrial, "
     "va en 8481.80.99.90."),
    (r"ball ?valve|mini valve|gas valve|v[aá]lvula esf[eé]rica|llave de paso", "8481.80.95.00", "VALVULA ESFERICA",
     "Válvula esférica (8481.80.95). Si es de gas para artefactos domésticos va en 8481.80.3; las llaves de paso "
     "de gas para cañerías siguen siendo esféricas."),
    (r"\bvalves?\b|v[aá]lvula", "8481.80.99.90", "VALVULA", "Válvula sin tipo reconocido: 8481.80.99 (las demás). "
     "Revisar el tipo (retención, esférica, esclusa, etc.)."),
]
MATERIALES = [
    (r"brass|lat[oó]n", "DE LATON"),
    (r"\bSS ?30[0-9]|stainless|inoxidable", "DE ACERO INOXIDABLE"),
    (r"bronze|bronce", "DE BRONCE"),
    (r"\bPVC\b", "DE PVC"),
    (r"cast iron|hierro fundido", "DE HIERRO FUNDIDO"),
]


def _verificar(ncm, desc_es, motivo):
    if ncm not in arancel():
        return "", desc_es, motivo + f" (Ojo: {ncm} no está en el arancel cargado.)"
    return ncm, desc_es, motivo + f" {ncm}: {descripcion_completa(ncm)}."
