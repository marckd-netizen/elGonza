"""Libro de clientes: criterios propios de cada importador que el despachante va anotando.

Es un archivo de texto en Documentos\\elGonza\\clientes.txt, una regla por línea:

    CLIENTE | condición | explicación

CLIENTE es una parte del nombre del importador o su RUT. Condiciones que entiende el programa:

    ajuste > 0                         todos los ítems llevan ajuste de valor
    ajuste = 5                         todos los ítems llevan ese % de ajuste
    forma de pago aduana = 7           código de la forma de pago a la Aduana
    forma de pago aduana contiene CERT texto que tiene que aparecer en la forma de pago
    forma de pago iva = 2              igual para la forma de pago del IVA (2 = DGI, certificados de crédito)
    recordar                           solo muestra la explicación para tenerla presente
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path

EJEMPLO = """# Libro de clientes de elGonza
# Una regla por línea:  CLIENTE | condición | explicación
# CLIENTE puede ser parte del nombre del importador o su RUT.
# Condiciones: ajuste > 0 · ajuste = N · forma de pago aduana = N · forma de pago iva = N
#              (también «contiene TEXTO» en vez de «= N») · recordar
# Las líneas que empiezan con # no se leen.

DECIRAL | ajuste > 0 | Deciral siempre tiene ajuste de valor
MADINOR | forma de pago iva = 2 | Madinor siempre paga el IVA con certificados de crédito (DGI)
"""


@dataclass
class ReglaCliente:
    cliente: str
    condicion: str
    explicacion: str
    linea: int = 0

    def aplica(self, borrador):
        c = self.cliente.upper()
        if re.fullmatch(r"\d{6,12}", c):
            return borrador.importador_rut == c
        return c in (borrador.importador or "").upper()


def ruta_libro():
    if os.environ.get("ELGONZA_CLIENTES"):
        return Path(os.environ["ELGONZA_CLIENTES"])
    return Path.home() / "Documents" / "elGonza" / "clientes.txt"


def cargar(ruta=None, crear=True):
    ruta = Path(ruta) if ruta else ruta_libro()
    if not ruta.exists():
        if not crear:
            return []
        try:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(EJEMPLO, encoding="utf-8")
        except OSError:
            return leer(EJEMPLO)
    return leer(ruta.read_text(encoding="utf-8", errors="replace"))


def leer(texto):
    reglas = []
    for n, l in enumerate(texto.splitlines(), 1):
        l = l.strip()
        if not l or l.startswith("#"):
            continue
        partes = [p.strip() for p in l.split("|")]
        if len(partes) < 2 or not partes[0]:
            continue
        reglas.append(ReglaCliente(partes[0], partes[1], partes[2] if len(partes) > 2 else "", n))
    return reglas


def evaluar(regla, b):
    """Devuelve (cumple, detalle). cumple es None si la condición no se entiende o es solo un recordatorio."""
    cond = regla.condicion.lower()
    if cond.startswith("recordar"):
        return None, ""
    m = re.fullmatch(r"ajuste\s*(>|=)\s*([\d.,]+)\s*%?", cond)
    if m:
        valor = float(m.group(2).replace(",", "."))
        malos = [it for it in b.items if not (it.ajuste > valor if m.group(1) == ">" else abs(it.ajuste - valor) < 0.0005)]
        if not malos:
            return True, ""
        lista = ", ".join(f"{it.nro} ({it.ajuste:g}%)" for it in malos[:10]) + ("…" if len(malos) > 10 else "")
        return False, f"Ítems sin el ajuste esperado: {lista}"
    m = re.fullmatch(r"forma de pago (aduana|iva)\s*(=|contiene)\s*(.+)", cond)
    if m:
        actual = (b.forma_pago_aduana if m.group(1) == "aduana" else b.forma_pago_iva) or ""
        esperado = m.group(3).strip()
        ok = actual.split()[0] == esperado if m.group(2) == "=" and actual else esperado.upper() in actual.upper()
        return ok, f"El borrador dice «{actual or 'sin forma de pago'}»"
    return None, f"No entiendo la condición «{regla.condicion}» (línea {regla.linea} del libro de clientes)"
