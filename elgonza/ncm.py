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
    for k in (ncm[:7], ncm[:10], ncm):
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


def sugerir(codigo, descripcion, ancho_mm=None, ncm_factura=""):
    """(ncm, descripción en español, motivo). ncm vacío si no hay sugerencia."""
    for clave, ncm, desc_es in antecedentes():
        if (_norm(clave) and _norm(clave) == _norm(codigo)) or \
                (not re.fullmatch(r"[A-Z0-9\-#()./]+", clave) and clave.lower() in (descripcion or "").lower()):
            return _verificar(ncm, desc_es, f"Antecedente: «{clave}» → {ncm}.")
    if ncm_factura:
        d = re.sub(r"\D", "", ncm_factura)
        legible = f"{d[:4]}.{d[4:6]}.{d[6:8]}"
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
        aviso = f"El NCM de la factura ({legible}) no está en el arancel. "
    else:
        aviso = ""
    ncm, desc_es, motivo = _por_reglas(codigo, descripcion, ancho_mm)
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
    return "", "", "Sin sugerencia: agregá el producto a antecedentes_ncm.txt para la próxima vez."


def _verificar(ncm, desc_es, motivo):
    if ncm not in arancel():
        return "", desc_es, motivo + f" (Ojo: {ncm} no está en el arancel cargado.)"
    return ncm, desc_es, motivo + f" {ncm}: {descripcion_completa(ncm)}."
