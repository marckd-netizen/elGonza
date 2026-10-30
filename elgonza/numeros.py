"""Números en formato uruguayo (1.234,56) y búsqueda tolerante en texto de OCR."""
import re


def num(s):
    """'1.234,56' -> 1234.56. Devuelve None si no es un número."""
    if s is None:
        return None
    s = s.strip().replace(" ", "")
    if not s:
        return None
    neg = s.endswith("-") or s.startswith("-")
    s = s.strip("-")
    try:
        v = float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None
    return -v if neg else v


def fmt(v, dec=2):
    """1234.5 -> '1.234,50'."""
    s = f"{v:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _patron(v, dec):
    entero, _, decimales = f"{abs(v):.{dec}f}".partition(".")
    sep = r"[\s.,']?"
    cuerpo = sep.join(entero)
    if decimales and int(decimales):
        cuerpo += r"[\s]?[.,][\s]?" + r"\s?".join(decimales)
    elif decimales:
        cuerpo += r"(?:[\s]?[.,][\s]?" + r"\s?".join(decimales) + ")?"
    if decimales:
        cuerpo += "0*"
    return r"(?<![\d])" + cuerpo + r"(?![\d])"


def aparece(v, texto, dec=2):
    """¿Aparece el importe v en el texto, en cualquier formato (1.234,56 / 1,234.56 / 1234.56)?

    Tolera espacios que mete el OCR entre dígitos. Con dec=2 también acepta el
    importe sin decimales cuando son ,00.
    """
    if v is None or not texto:
        return False
    if re.search(_patron(v, dec), texto):
        return True
    # pesos de 3 decimales en el borrador (8.058,620) suelen aparecer con 2 o sin decimales
    if dec == 3:
        return any(re.search(_patron(round(v, d), d), texto) for d in (2, 1))
    return False


def solo_alnum(s):
    return re.sub(r"[^0-9A-Z]", "", (s or "").upper())


def contiene_codigo(codigo, texto):
    """Busca un código (factura, contenedor, BL) ignorando guiones, espacios y barras."""
    c = solo_alnum(codigo)
    if not c:
        return False
    return c in solo_alnum(texto)


def digito_contenedor(codigo):
    """Dígito verificador ISO 6346. Devuelve (calculado, declarado) o None si no tiene formato."""
    c = solo_alnum(codigo)
    if not re.fullmatch(r"[A-Z]{4}\d{7}", c):
        return None
    valores = {}
    n = 10
    for letra in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if n % 11 == 0:
            n += 1
        valores[letra] = n
        n += 1
    total = 0
    for i, ch in enumerate(c[:10]):
        v = valores[ch] if ch.isalpha() else int(ch)
        total += v * (2 ** i)
    return (total % 11) % 10, int(c[10])


_PARECIDOS = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6"})


def coincidencia_codigo(codigo, texto):
    """'exacta', 'aproximada' (difiere en letras que el OCR confunde o en un carácter) o None."""
    c = solo_alnum(codigo)
    if not c:
        return None
    t = solo_alnum(texto)
    if c in t:
        return "exacta"
    cc, tt = c.translate(_PARECIDOS), t.translate(_PARECIDOS)
    if cc in tt:
        return "aproximada"
    if len(cc) >= 7:
        n = len(cc)
        for i in range(len(tt) - n + 1):
            difs = 0
            for a, b in zip(cc, tt[i:i + n]):
                if a != b:
                    difs += 1
                    if difs > 1:
                        break
            if difs <= 1:
                return "aproximada"
    return None
