"""Factura a planilla: lectura de facturas y packing lists, pesos y NCM sugerido."""
import openpyxl
import pymupdf as fitz
import pytest

from elgonza import ncm as modulo_ncm
from elgonza.ncm import arancel, sugerir
from elgonza.planilla import _agrupar, _leer_fecha, leer_carpeta, planilla_de_carpeta


@pytest.fixture(autouse=True)
def sin_antecedentes(tmp_path, monkeypatch):
    monkeypatch.setattr(modulo_ncm, "ruta_antecedentes", lambda: tmp_path / "antecedentes_ncm.txt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    from elgonza import historial
    historial._cache.clear()


def _pdf(ruta, paginas, ancho=595):
    """paginas: [[(x, y, texto)]]"""
    doc = fitz.open()
    for textos in paginas:
        p = doc.new_page(width=ancho, height=842)
        for x, y, t in textos:
            p.insert_text((x, y), t, fontsize=8, fontname="helv")
    doc.save(ruta)


def _fila(y, nro, codigo, desc, ancho, cant, precio, importe):
    return [(60, y, nro), (95, y, codigo), (170, y, desc), (330, y, ancho), (380, y, cant), (440, y, precio),
            (500, y, importe)]


def _factura(numero, fecha, filas, extra=()):
    enc = [(250, 60, "COMMERCIAL INVOICE"), (60, 90, "TO: Cliente SA"), (380, 90, f"INVOICE NO.: {numero}"),
           (380, 105, f"DATE: {fecha}"), (330, 140, "WIDTH"), (60, 150, "NO."), (95, 150, "CODE"),
           (170, 150, "DESCRIPTION"), (330, 150, "MM"), (380, 150, "M2"), (440, 150, "PRICE"), (500, 150, "AMOUNT")]
    return enc + [t for f in filas for t in f] + list(extra)


def _carpeta(tmp_path):
    filas = [
        _fila(180, "1", "AL-MH", "Semi gloss paper/acrylic/glassine", "350", "22,400.00", "0.232", "5,196.80"),
        _fila(210, "2", "AL-MH", "Semi gloss paper/acrylic/glassine", "120", "480.00", "0.232", "111.36"),
        # código y descripción partidos en dos renglones
        [(95, 232, "PP-"), (170, 232, "Metallized PP film/top coated/")] +
        _fila(240, "3", "", "", "350", "1,386.00", "0.321", "444.91")[0:1] +
        _fila(240, "3", "", "", "350", "1,386.00", "0.321", "444.91")[3:] +
        [(95, 248, "S50#(TC)"), (170, 248, "acrylic/white glassine")],
        _fila(275, "4", "OPAT-W24", "Gloss lamination film / Acrylic", "245", "2,450.00", "0.060", "147.00"),
    ]
    total = [(170, 310, "TOTAL"), (380, 310, "26,716.00"), (500, 310, "5,900.07")]
    packing = [(250, 60, "PACKING LIST"), (380, 90, "INVOICE NO.: FLS-1"), (60, 150, "NO."), (95, 150, "CODE"),
               (380, 150, "M2"), (430, 150, "PCS"), (470, 150, "N.W."), (520, 150, "G.W."),
               (60, 180, "1"), (95, 180, "AL-MH"), (380, 180, "12,000.00"), (430, 180, "1"), (470, 180, "1000"), (520, 180, "1030"),
               (60, 200, "2"), (95, 200, "AL-MH"), (380, 200, "10,400.00"),
               (430, 210, "1"), (470, 210, "900"), (520, 210, "930"),
               (60, 220, "3"), (95, 220, "AL-MH"), (380, 220, "480.00"),
               (60, 245, "4"), (95, 245, "PP-S50#(TC)"), (380, 245, "1,386.00"), (430, 255, "1"), (470, 255, "250"), (520, 255, "270"),
               (60, 265, "5"), (95, 265, "OPAT-W24"), (380, 265, "2,450.00"),
               (60, 300, "TOTAL"), (380, 300, "26,716.00"), (430, 300, "3"), (470, 300, "2,150"), (520, 300, "2,230")]
    _pdf(tmp_path / "FLS-1 invoice.pdf", [_factura("FLS-1", "2026/8/31", filas, total), packing])
    return tmp_path


def test_lee_factura_y_packing(tmp_path):
    (f,) = leer_carpeta(_carpeta(tmp_path), usar_ocr=False)
    assert (f.numero, f.fecha) == ("FLS-1", "31/08/2026")
    assert [(l.codigo, l.cantidad, l.importe) for l in f.lineas] == [
        ("AL-MH", 22400, 5196.80), ("AL-MH", 480, 111.36), ("PP-S50#(TC)", 1386, 444.91), ("OPAT-W24", 2450, 147.00)]
    assert f.lineas[2].descripcion == "Metallized PP film/top coated/ acrylic/white glassine"
    assert {l.unidad for l in f.lineas} == {"METROS CUADRADOS"}
    assert [l.ancho_mm for l in f.lineas] == [350, 120, 350, 245]
    # bulto 2 (900 kg) tiene 10.400 + 480 m² de AL-MH: todo AL-MH suma 1900 kg, repartido por m²
    assert f.lineas[0].neto + f.lineas[1].neto == pytest.approx(1900)
    assert f.lineas[0].neto == pytest.approx(1900 * 22400 / 22880, abs=0.01)
    # el último bulto (250 kg) se reparte entre PP-S50 y OPAT por m²
    assert f.lineas[2].neto == pytest.approx(250 * 1386 / 3836, abs=0.01)
    assert sum(l.bruto for l in f.lineas) == pytest.approx(2230, abs=0.05)
    assert f.neto_total == 2150 and f.bultos == 3


def test_ncm_sugerido(tmp_path):
    (f,) = leer_carpeta(_carpeta(tmp_path), usar_ocr=False)
    assert [l.ncm for l in f.lineas] == ["4811.41.90.00", "4811.41.10.00", "3919.90.10.90", "3919.90.90.00"]
    assert "Acrylic" in f.lineas[3].motivo and "ficha técnica" in f.lineas[3].motivo


def test_planilla_una_factura(tmp_path):
    ruta, _ = planilla_de_carpeta(_carpeta(tmp_path), usar_ocr=False, avisar=lambda m: None)
    ws = openpyxl.load_workbook(ruta)["Hoja1"]
    assert [c.value for c in ws[1]] == ["ITEM", "CANTIDAD", "VALOR", "NCM", "DESCRIPCION", "NETO", "BRUTO",
                                        "UNIDAD MEDIDA", "CODIGO FACTURA", "DESCRIPCION FACTURA", "NCM SUGERIDO", "ORIGEN"]
    assert [c.value for c in ws[2]][:3] == [1, 22400, 5196.8]
    assert ws["K2"].value == "4811.41.90.00"


def test_planilla_multifactura(tmp_path):
    _carpeta(tmp_path)
    fila = _fila(180, "1", "PVC-20", "White PVC film/acrylic adhesive", "150", "100.00", "2.00", "200.00")
    _pdf(tmp_path / "INV-77.pdf", [_factura("INV-77", "05/09/2026", [fila], [(170, 220, "TOTAL"), (500, 220, "200.00")])])
    ruta, facturas = planilla_de_carpeta(tmp_path, usar_ocr=False, avisar=lambda m: None)
    ws = openpyxl.load_workbook(ruta)["Hoja1"]
    assert [c.value for c in ws[1]][:6] == ["ITEM", "FACTURA", "FECHA", "ITEM FACTURA", "CANTIDAD", "VALOR"]
    filas = [[c.value for c in r] for r in ws.iter_rows(min_row=2)]
    assert [r[:4] for r in filas] == [[1, "FLS-1", "31/08/2026", 1], [2, "FLS-1", "31/08/2026", 2],
                                      [3, "FLS-1", "31/08/2026", 3], [4, "FLS-1", "31/08/2026", 4],
                                      [5, "INV-77", "05/09/2026", 1]]
    assert filas[4][-2] == "3919.10.20.00"
    assert any("packing" in o for o in facturas[1].observaciones)


def test_observaciones_de_totales(tmp_path):
    fila = _fila(180, "1", "AB-1", "Thing", "10", "100.00", "2.00", "200.00")
    _pdf(tmp_path / "inv.pdf", [_factura("X-100", "2026-01-02", [fila], [
        (60, 220, "Deduct 3% commission of other invoice"), (500, 220, "6.00"),
        (170, 240, "TOTAL"), (380, 240, "100.00"), (500, 240, "194.00")])])
    (f,) = leer_carpeta(tmp_path, usar_ocr=False)
    obs = " ".join(f.observaciones)
    assert "descuento" in obs and "total igual" in obs and "packing" in obs
    assert f.lineas[0].ncm == ""


def test_fechas():
    assert _leer_fecha("2026/8/31") == "31/08/2026"
    assert _leer_fecha("05/09/26") == "05/09/2026"
    assert _leer_fecha("Aug. 31, 2026") == "31/08/2026"
    assert _leer_fecha("31-AUG-2026") == "31/08/2026"
    assert _leer_fecha("sin fecha") == ""


def test_agrupar_bultos():
    # renglones en y=0,10,20,30; pesos en 0 (solo) y 25 (centro de 20 y 30)
    assert _agrupar([0, 10, 20, 30], [0, 25], 12) == [[0, 1], [2, 3]] or _agrupar([0, 10, 20, 30], [0, 25], 12) == [[0], [1, 2, 3]]
    assert _agrupar([0, 24, 48, 72], [0, 48], 24) == [[0], [1, 2, 3]]


def test_arancel_y_antecedentes(tmp_path):
    t = arancel()
    assert t["3919.10.10.00"].ez == "14.4" and t["4811.41.90.00"].aec == "10.8"
    (tmp_path / "antecedentes_ncm.txt").write_text("OPAT-W24 | 3919.90.10.90 | FILM BOPP PARA LAMINAR\n", encoding="utf-8")
    ncm, desc, motivo = sugerir("OPAT-W24", "Gloss lamination film / Acrylic", 245)
    assert (ncm, desc) == ("3919.90.10.90", "FILM BOPP PARA LAMINAR") and "Antecedente" in motivo
    assert sugerir("X", "Ballpoint pens", None)[0] == ""


# --- factura sin numerar, con la cantidad en un renglón aparte (estilo Madesa) ---
def _bloque(y, cant, letras, desc, cont, precio, total):
    return [(70, y, cant), (110, y, letras), (95, y + 10, "UN"), (120, y + 10, desc), (430, y + 10, precio),
            (500, y + 10, total), (130, y + 20, cont)]


def _madesa(tmp_path):
    enc = [(300, 60, "FACTURA PROFORMA Nº 20260909"), (60, 120, "CANTIDAD"), (95, 130, "UN"),
           (130, 130, "DESCRIPCION DE MERCANCIAS"), (430, 130, "PRECIO UNIT"), (500, 130, "PRECIO TOTAL")]
    filas = (_bloque(150, "60", "(Sesenta)", "COMEDOR LEXY / NEGRO - 340479 /", "341642 - 53295Z1 - NCM: 94036000",
                     "20.24", "1,214.40")
             + _bloque(190, "48", "(Cuarenta y Ocho)", "ROPERO TOKIO BLANCO 3 PUERTAS - 341061 - 109509 -",
                       "NCM: 94035000", "125.96", "6,046.08"))
    pie = [(60, 240, "MONTO TOTAL MERCANCIAS"), (500, 240, "7,260.48"), (60, 260, "BOM PRINCIPIO, 27 DE OCTUBRE DE 2026.")]
    _pdf(tmp_path / "Fatura 20260909.pdf", [enc + filas + pie])
    pack = [(300, 60, "PACKING LIST Nº 20260909"), (380, 120, "PESOS (Kg)"), (380, 130, "NETO"), (430, 130, "BRUTO"),
            (480, 130, "CBM"),
            (70, 150, "60"), (110, 150, "(Sesenta)"), (95, 160, "UN"), (120, 160, "COMEDOR LEXY / NEGRO - 340479"),
            (380, 160, "906"), (430, 160, "918"), (480, 160, "2.284"), (130, 170, "/ 341642 - 53295Z1"),
            (70, 190, "48"), (110, 190, "(Cuarenta y Ocho)"), (95, 200, "UN"), (120, 200, "ROPERO TOKIO BLANCO - 341061 -"),
            (380, 200, "4,972.8"), (430, 200, "5,126.4"), (480, 200, "11.581"), (130, 210, "109509 - NCM: 94035000"),
            (60, 240, "TOTAL DE BULTOS: 204"), (60, 252, "PESO NETO: 5,878.8 Kg  PESO BRUTO: 6,044.4 Kg")]
    _pdf(tmp_path / "Packing list 20260909.pdf", [pack])
    return tmp_path


def test_factura_sin_numerar_cantidad_arriba(tmp_path):
    (f,) = leer_carpeta(_madesa(tmp_path), usar_ocr=False)
    assert (f.numero, f.fecha) == ("20260909", "27/10/2026")
    assert [(l.nro, l.codigo, l.cantidad, l.precio, l.importe, l.unidad) for l in f.lineas] == [
        ("1", "340479", 60, 20.24, 1214.40, "UNIDADES"), ("2", "341061", 48, 125.96, 6046.08, "UNIDADES")]
    assert f.lineas[0].descripcion == "COMEDOR LEXY / NEGRO - 340479 / 341642 - 53295Z1 - NCM: 94036000"
    assert [(l.neto, l.bruto) for l in f.lineas] == [(906, 918), (4972.8, 5126.4)]
    assert (f.neto_total, f.bruto_total, f.bultos) == (5878.8, 6044.4, 204)
    # el NCM de la factura, llevado a su apertura nacional
    assert [l.ncm for l in f.lineas] == ["9403.60.00.00", "9403.50.00.00"]
    assert "NCM de la factura" in f.lineas[0].motivo
    assert any("proforma" in o for o in f.observaciones)


def test_ncm_valvulas():
    casos = {
        "BRASS UNIVERSAL CHECK VALVE 3/4\"": "8481.30.00.90",
        "BRASS DOUBLE GUIDE FOOT VALVE 3\"": "8481.30.00.90",
        "BRASS SAFETY VALVE 1/2\"": "8481.40.00.90",
        "BRASS BALL VALVE 1030 S FF PRESS": "8481.80.95.00",
        "GAS VALVE WITH ALUMINIUM YELLOW BUTTERFLY HANDLE": "8481.80.95.00",
        "SS304BALL Valve with IRON HANDLE": "8481.80.95.00",
        "BRASS BALL BIBCOCKS 1/2 x 3/4": "8481.80.19.00",
    }
    for desc, esperado in casos.items():
        assert sugerir("", desc)[0] == esperado, desc
    assert sugerir("", "BRASS SAFETY VALVE")[1] == "VALVULA DE SEGURIDAD DE LATON"


def test_titulo_de_grupo_no_entra_en_la_descripcion(tmp_path):
    enc = [(250, 60, "COMMERCIAL INVOICE"), (380, 90, "INVOICE NO.: SAS-1"), (60, 140, "Art"), (160, 140, "Description"),
           (400, 140, "PCS"), (450, 140, "USD/PC"), (510, 140, "USD")]
    filas = [(160, 160, "CHECK VALVE"),
             (60, 169, "VV01"), (160, 169, "BRASS CHECK VALVE"), (400, 169, "144"), (440, 169, "$"), (450, 169, "2.38"),
             (500, 169, "$"), (510, 169, "342.72"),
             (60, 178, "VV02"), (160, 178, "BRASS CHECK VALVE"), (400, 178, "216"), (440, 178, "$"), (450, 178, "3.09"),
             (500, 178, "$"), (510, 178, "667.44"),
             (160, 188, "BRASS VALVE"),
             (160, 198, "BRASS BALL VALVE 1030 S FF"),
             (60, 206, "V03"), (160, 206, "COATED FLAT HANDLE"), (400, 206, "10"), (440, 206, "$"), (450, 206, "2.01"),
             (500, 206, "$"), (510, 206, "20.10"),
             (160, 214, "SAS / ON-OFF"),
             (60, 240, "TOTAL:"), (400, 240, "370"), (510, 240, "1,030.26")]
    _pdf(tmp_path / "inv.pdf", [enc + filas])
    (f,) = leer_carpeta(tmp_path, usar_ocr=False)
    assert [l.descripcion for l in f.lineas] == [
        "BRASS CHECK VALVE", "BRASS CHECK VALVE", "BRASS BALL VALVE 1030 S FF COATED FLAT HANDLE SAS / ON-OFF"]


def _base_de_prueba(tmp_path, monkeypatch):
    """Respaldo de Mega6 con dos borradores: una cámara y una válvula esférica de latón."""
    from pathlib import Path
    from elgonza import historial
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    historial._cache.clear()
    respaldo = tmp_path / "Permiso6" / "respaldo"
    respaldo.mkdir(parents=True)
    texto = (Path(__file__).parent / "datos" / "borrador_aereo.txt").read_text(encoding="utf-8")
    (respaldo / "Borrador900001.txt").write_text(texto, encoding="utf-8")
    valvula = (texto.replace("8525.89.29.00", "8481.80.95.00")
               .replace("CAMARA PARA LINEA DE ENVASADO EN FRIGORIFICO", "VALVULA ESFERICA DE LATON DE 1/2 PULGADA")
               .replace("Carpeta : 900001", "Carpeta : 900002"))
    (respaldo / "Borrador900002.txt").write_text(valvula, encoding="utf-8")
    return historial, tmp_path / "Permiso6"


def test_base_de_antecedentes_de_mega6(tmp_path, monkeypatch):
    historial, raiz = _base_de_prueba(tmp_path, monkeypatch)
    assert not historial.existe()
    assert historial.actualizar(raiz) == 2
    assert historial.existe()
    assert historial.actualizar(raiz) == 0          # solo lee los nuevos o cambiados
    ncm, desc, motivo, puntaje = historial.buscar("", "BRASS BALL VALVE 1/2\"")
    assert ncm == "8481.80.95.00" and "VALVULA ESFERICA" in desc and "900002" in motivo
    assert historial.buscar("", "DIGITAL CAMERA FOR PACKAGING LINE")[0] == "8525.89.29.00"
    assert historial.importador_en_texto("SOLD TO: IMPORTADORA EJEMPLO S.A.\nMONTEVIDEO") == "IMPORTADORA EJEMPLO S.A."
    # sugerir usa la base cuando no hay reglas ni NCM en la factura
    ncm, desc_es, motivo = sugerir("", "DIGITAL CAMERA FOR PACKAGING LINE")
    assert ncm == "8525.89.29.00" and "Mega6" in motivo


def test_aprende_codigos_al_revisar(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from elgonza.borrador import parsear
    historial, raiz = _base_de_prueba(tmp_path, monkeypatch)
    historial.actualizar(raiz)
    b = parsear((raiz / "respaldo" / "Borrador900002.txt").read_text(encoding="utf-8"))
    pdf = tmp_path / "factura.pdf"
    _pdf(pdf, [_factura("612", "21/09/2026",
                        [_fila(180, "1", "VX-77120", "Brass ball valve 1/2", "", "100.00", "41.795", "4,179.50")])])
    doc = SimpleNamespace(tipo="factura", ruta=pdf, texto="COMMERCIAL INVOICE No. 612")
    assert historial.aprender(b, [doc]) == 1
    ncm, desc, motivo, puntaje = historial.buscar("VX-77120", "algo distinto")
    assert ncm == "8481.80.95.00" and puntaje == 1.0 and "VX-77120" in motivo


def test_planilla_en_ods(tmp_path):
    import zipfile
    carpeta = tmp_path / "despacho"
    carpeta.mkdir()
    _carpeta(carpeta)
    ruta, _ = planilla_de_carpeta(carpeta, formato="ambos", avisar=lambda *_: None)
    assert ruta.suffix == ".xlsx" and ruta.with_suffix(".ods").exists()
    with zipfile.ZipFile(ruta.with_suffix(".ods")) as z:
        assert z.namelist()[0] == "mimetype" and z.read("mimetype") == b"application/vnd.oasis.opendocument.spreadsheet"
        contenido = z.read("content.xml").decode()
    assert 'table:name="Hoja1"' in contenido and 'table:name="Notas"' in contenido and "NCM SUGERIDO" in contenido
    ruta, _ = planilla_de_carpeta(carpeta, formato="ods", avisar=lambda *_: None)
    assert ruta.suffix == ".ods"


def test_pais_de_origen():
    from elgonza.planilla import origen_expreso, pais_del_proveedor
    assert pais_del_proveedor("ZHEJIANG SAS VALVE CO.\nYUHUAN, ZHEJIANG, CHINA\nTO: EFUSION S.A. MONTEVIDEO, URUGUAY") == "CHINA"
    assert pais_del_proveedor("MADESA\nBOM PRINCIPIO - RS - Brasil\nCLIENTE: DEVOTO URUGUAY") == "BRASIL"
    assert origen_expreso("PUERTO DE ORIGEN: BOM PRINCIPIO - BRAZIL") == ""
    assert origen_expreso("COUNTRY OF ORIGIN: VIET NAM") == "VIETNAM"
    assert origen_expreso("Ball valve 1/2 Made in Taiwan") == "TAIWAN"


def test_origen_en_la_planilla(tmp_path):
    filas = [_fila(180, "1", "BV-1", "Brass ball valve 1/2 made in Taiwan", "", "100.00", "2.00", "200.00"),
             _fila(210, "2", "BV-2", "Brass ball valve 3/4", "", "100.00", "3.00", "300.00")]
    extra = [(60, 40, "NINGBO VALVES CO., LTD. NINGBO, ZHEJIANG, CHINA")]
    _pdf(tmp_path / "invoice.pdf", [_factura("NV-9", "01/10/2026", filas, extra)])
    f = leer_carpeta(tmp_path, usar_ocr=False)[0]
    assert [l.origen for l in f.lineas] == ["TAIWAN", "CHINA"]
    assert "made in" not in f.lineas[0].descripcion.lower()
    assert any("Origen CHINA" in o and "otro origen" in o for o in f.observaciones)


def test_factura_de_libros_cantidad_despues_del_precio(tmp_path):
    """Precio, cantidad (casi siempre 1) e importe; código del proveedor e ISBN numéricos; HS code en el encabezado."""
    enc = [(60, 40, "SHIPPERS NAME: TE TRAEMOS INC"), (60, 52, "COUNTRY: United States"),
           (380, 40, "Export Invoice: # 523098"), (380, 52, "DATE: October 30, 2026"),
           (60, 70, "HS CODE:"), (60, 82, "4901,99,9000"),
           (450, 130, "Und"), (60, 140, "Item Code"), (110, 140, "Intl Item Code"), (200, 140, "Product"),
           (380, 140, "Taxable Amount"), (450, 145, "Qty"), (490, 140, "Total Amount")]

    def fila(y, cod, isbn, desc, precio, cant, total):
        return [(60, y, cod), (110, y, isbn), (200, y, desc), (380, y, "$"), (395, y, precio), (450, y, cant),
                (490, y, "$"), (505, y, total)]
    filas = fila(170, "70536287", "9798337404356", "SIX OF CROWS- A DARKER...", "15.61", "1", "15.61") + \
        fila(185, "64533045", "9798572382181", "ANATOMIA VETERINARIA D...", "8.41", "2", "16.82") + \
        fila(200, "72207331", "9780785850328", "BOTANICA COLORING BOOK...", "7.85", "1", "7.85") + \
        [(380, 230, "Total Amount"), (450, 230, "4"), (490, 230, "$ 40.28")]
    _pdf(tmp_path / "invoice.pdf", [enc + filas])
    f = leer_carpeta(tmp_path, usar_ocr=False)[0]
    assert [(l.codigo, l.cantidad, l.precio, l.importe) for l in f.lineas] == [
        ("70536287", 1, 15.61, 15.61), ("64533045", 2, 8.41, 16.82), ("72207331", 1, 7.85, 7.85)]
    assert f.lineas[0].descripcion == "SIX OF CROWS- A DARKER... ISBN 9798337404356"
    assert {l.ncm for l in f.lineas} == {"4901.99.00.00"}
    assert f.lineas[0].descripcion_es == "LIBRO" and "4903" in f.lineas[2].motivo
    assert {l.origen for l in f.lineas} == {"ESTADOS UNIDOS"}


def test_sin_packing_reparte_el_peso_de_la_guia(tmp_path):
    from elgonza.planilla import peso_bruto_transporte
    assert peso_bruto_transporte("Custom Value USD 2029.22\nWeight KG 85.71\nTotal Pieces 4 of 4") == (85.71, "Weight KG 85.71")
    assert peso_bruto_transporte("Chargeable Weight 120 KG\nGross Weight: 98,5 KGS")[0] == 98.5
    assert peso_bruto_transporte("GROSS WEIGHT 100 LBS")[0] == 45.36
    filas = [_fila(180, "1", "BV-1", "Brass ball valve 1/2", "", "100.00", "2.00", "200.00"),
             _fila(210, "2", "BV-2", "Brass ball valve 3/4", "", "100.00", "1.50", "150.00")]
    guia = [(250, 60, "INTERNATIONAL AIR WAYBILL"), (60, 100, "Weight KG 10.00"), (60, 120, "Total Pieces 1 of 1")]
    _pdf(tmp_path / "preAlert.pdf", [guia, _factura("NV-9", "01/10/2026", filas)])
    f = leer_carpeta(tmp_path, usar_ocr=False)[0]
    assert [(l.bruto, l.neto) for l in f.lineas] == [(5.71, 5.2), (4.29, 3.9)]
    assert f.bruto_total == 10 and f.neto_total == 9.1
    assert any("91 %" in o for o in f.observaciones)


def _xlsx(ruta, filas):
    wb = openpyxl.Workbook()
    for f in filas:
        wb.active.append(f)
    wb.save(ruta)


def test_factura_y_packing_en_excel(tmp_path):
    """Ítems en dos filas (descripción arriba, números abajo), HSN por ítem y packing con una celda de dos líneas."""
    enc = [["INVOICE"], ["EXPORTER", None, None, "Invoice No. & Date"], ["LICHEE CLOTHING", None, None, None],
           [None, None, None, "LCI/40/2026-27 DT. 06-10-2026"], ["Country of Origin of Goods\nINDIA"]]
    factura = enc + [
        ["Sno.", "Article", "Description", "Composition", "HSN Code", "Pcs", "USD\nPER Garment", "Amount IN\nUSD"],
        [1, "KID GIRL 1", "KNITTED KID GIRL", "100 % COTTON", 61112000],
        [None, None, "TSHIRT", None, None, 620, 2.15, 1333],
        [2, "BABY BOY 1", "KNITTED BABY BOY", "100 % COTTON", 61112000],
        [None, None, "TSHIRT", None, None, 100, 1.9, 190],
        ["Amount Chargeble( in words )\nTOTAL NUMBER OF CARTONS : 72", None, None, None, None, 720, "TOTAL", 1523]]
    packing = [["PACKING LIST"]] + enc[1:] + [
        ["Sno.", "Article", "Description", "No. Of Box", "Total Pcs", "Net Wt Per Ctn", "Total Nt Wt", "Total Gr Wt"],
        ["1\n2", "KID GIRL 1\nBABY BOY 1", "TSHIRT", "62\n10", "620\n100", "1.33\n0.65", "82.460\n6.5", "105.4\n10"],
        [None, None, None, 72, 720, None, 88.96, 115.4]]
    _xlsx(tmp_path / "LCI 40.xlsx", factura)
    _xlsx(tmp_path / "LCI 40 PACKING LIST.xlsx", packing)
    f = leer_carpeta(tmp_path, usar_ocr=False)[0]
    assert (f.numero, f.fecha) == ("LCI/40/2026-27", "06/10/2026")
    assert [(l.codigo, l.cantidad, l.precio, l.importe, l.neto, l.bruto) for l in f.lineas] == [
        ("KID GIRL 1", 620, 2.15, 1333, 82.46, 105.4), ("BABY BOY 1", 100, 1.9, 190, 6.5, 10)]
    assert f.lineas[0].descripcion == "KNITTED KID GIRL TSHIRT 100 % COTTON"
    assert f.bultos == 72 and {l.origen for l in f.lineas} == {"INDIA"}
    # 6111 es para bebés: la remera de niña va en 6109.10; la de bebé queda en 6111.20
    assert [l.ncm for l in f.lineas] == ["6109.10.00.00", "6111.20.00.00"]
    assert not any("total igual" in o for o in f.observaciones)


def test_factura_escaneada_con_sello():
    """Lo que trae una factura escaneada (TRM): «Packing Slip:» en el encabezado, lote y vencimiento debajo de la
    descripción, número de línea leído dentro del texto, precio 1.00, renglón partido por un sello."""
    from elgonza.planilla import Palabra, _limpiar_descripcion, _renglones, _renumerar, _tipo_pagina, leer_items

    def renglon(top, *palabras):
        return [Palabra(t, x, x + 5 * len(t), top) for x, t in palabras]
    enc = renglon(50, (300, "INVOICE")) + renglon(80, (40, "Packing"), (80, "Slip:"), (110, "642884"))
    assert _tipo_pagina(_renglones(enc, 0), None) == "factura"
    assert _limpiar_descripcion("23. HOOF19/ HOOF CONDITIONER 500ML Lot #:261027 EXP : 10/2030") == \
        ("23", "", "HOOF19/ HOOF CONDITIONER 500ML")
    assert _limpiar_descripcion("Invoice: 743070 XMAR33-3/ HAT 65050030")[1:] == ("65050030", "XMAR33-3/ HAT")
    cab = renglon(150, (40, "Line"), (62, "Part"), (255, "HS"), (310, "Quantity"), (400, "Price"), (465, "VAT"),
                  (505, "Total"))
    filas = renglon(170, (40, "1"), (62, "BIOA01"), (110, "/"), (118, "BIOACTIVE"), (255, "23099096"),
                    (310, "60.0000"), (400, "20.75"), (465, "0.00"), (505, "1,245.00")) + \
        renglon(184, (141, "Lot"), (160, "#:260907"), (234, "EXP"), (259, "09/2028")) + \
        renglon(200, (311, "30.0000"), (505, "30.00")) + \
        renglon(204, (40, "2"), (62, "XMAR200/BUCKET"), (180, "GREEN"), (400, "1.00"), (465, "0.00"))
    ls = leer_items(_renglones(cab + filas, 0, 5))
    assert [(l.nro, l.codigo, l.descripcion, l.cantidad, l.precio, l.importe) for l in ls] == [
        ("1", "BIOA01", "BIOACTIVE", 60, 20.75, 1245), ("2", "XMAR200", "BUCKET GREEN", 30, 1, 30)]
    assert ls[0].ncm_factura == "23099096"
    from types import SimpleNamespace as L
    xs = [L(nro=str(n)) for n in range(20, 29)] + [L(nro=n) for n in ("29", "29", "31", "31", "33")]
    _renumerar(xs)
    assert [x.nro for x in xs][-6:] == ["28", "29", "30", "31", "32", "33"]


def test_pesos_dichos_en_la_factura_y_ncm_en_la_descripcion():
    from elgonza.planilla import Factura, Linea, _limpiar_descripcion, _pesos_sin_packing, peso_neto_en
    assert _limpiar_descripcion("CÁMARA HDCVI TIPO DOMO G4 8525.89.29 4 PÇ") == ("", "85258929", "CÁMARA HDCVI TIPO DOMO G4")
    texto = "FACTURA INVOICE NO.: 0022/26\nTOTAL US$ 7.086,30\nPESO NETO: 75,000 KG\nPESO BRUTO: 83,000 KG"
    assert peso_neto_en(texto) == 75
    f = Factura("f.pdf", "0022/26", "", [Linea("1", "A", "x", 1, 300, 300), Linea("2", "B", "y", 1, 100, 100)])
    f.texto, f.sin_packing = texto, True
    # una hoja con un peso mal leído (983 kg brutos para 75 netos) no se usa
    f.otros_textos = []
    _pesos_sin_packing([f], [])
    assert (f.neto_total, f.bruto_total) == (75, 83)
    assert [(l.neto, l.bruto) for l in f.lineas] == [(56.25, 62.25), (18.75, 20.75)]
    g = Factura("g.pdf", "1", "", [Linea("1", "A", "x", 1, 100, 100)])
    g.texto, g.sin_packing = "PESO NETO: 75,000 KG\nPESO BRUTO: 983,000 KG", True
    g.otros_textos = ["LISTA DE EMPAQUE\nPESO NETO: 75,000 KG\nPESO BRUTO: 83,000 KG"]
    _pesos_sin_packing([g], [])
    assert g.bruto_total == 83


def test_unidad_en_columna_junto_a_la_cantidad():
    from elgonza.planilla import Palabra, _renglones, leer_items

    def renglon(top, *palabras):
        return [Palabra(t, x, x + 5 * len(t), top) for x, t in palabras]
    cab = renglon(150, (40, "COD."), (120, "DESCRIPCION"), (300, "CANTIDAD"), (360, "MEDIDA"), (420, "PRECIO"),
                  (480, "TOTAL"))
    filas = renglon(170, (40, "28957"), (120, "CAMARA DOMO"), (300, "4"), (360, "PÇ"), (420, "171,20"), (480, "684,80")) + \
        renglon(185, (40, "28958"), (120, "CABLE COAXIAL"), (300, "200"), (360, "MTR"), (420, "9,59"), (480, "1.918,00"))
    ls = leer_items(_renglones(cab + filas, 0))
    assert [(l.cantidad, l.unidad) for l in ls] == [(4, "UNIDADES"), (200, "METROS")]


def test_no_pisa_planilla_existente(tmp_path):
    from elgonza.planilla import ruta_libre
    base = tmp_path / "Planilla X.xlsx"
    assert ruta_libre(base, [".xlsx"]) == base
    base.write_text("editada")
    assert ruta_libre(base, [".xlsx"]).name == "Planilla X (2).xlsx"
    (tmp_path / "Planilla X (2).ods").write_text("editada")
    assert ruta_libre(base, [".xlsx", ".ods"]).name == "Planilla X (3).xlsx"


def test_precio_en_renglon_de_abajo_y_packing_con_pallets(tmp_path):
    # factura alemana: el precio unitario va debajo («4,10 /1PC»), junto al HS; el packing da el bruto con pallets
    _pdf(tmp_path / "invoice 801.pdf", [[
        (350, 60, "Invoice 801356989 from Nov 05, 2026"), (60, 80, "Kühnehöfe 11 Hamburg GERMANY"),
        (60, 100, "URUGUAY"),
        (50, 150, "Itm"), (77, 150, "Article"), (110, 150, "Description"), (356, 150, "Amount"), (392, 150, "Quantity"),
        (445, 150, "Net Value EUR"), (510, 150, "Total EUR"),
        (110, 160, "GTIN"), (191, 160, "HS Code"), (313, 160, "Netto kg"), (367, 160, "Gross kg"), (468, 160, "Price per"),
        (49, 185, "001"), (77, 185, "43050"), (110, 185, "Beetroot slices, 2650ml,jars"), (358, 185, "1080"),
        (379, 185, "TR"), (400, 185, "4.320"), (423, 185, "PC"), (461, 185, "17.712,00"), (507, 185, "17.712,00"),
        (110, 196, "40804323"), (195, 196, "200190"), (303, 196, "10.584,000"), (352, 196, "15.785,280"),
        (450, 196, "4,10"), (468, 196, "/1PC"),
        (195, 240, "Invoice amount EUR"), (507, 240, "17.712,00")]])
    _pdf(tmp_path / "Packing List 801.pdf", [[
        (400, 50, "Packing List"), (693, 70, "801356989"),
        (729, 100, "Total Net"), (776, 100, "Total Gross"),
        (49, 120, "1"), (65, 120, "43050"), (87, 120, "Beetroot slices"), (584, 120, "1080"), (615, 120, "20019097"),
        (727, 120, "10584,000"), (776, 120, "15785,280"),
        (95, 140, "Cont. No.: FBIU 023154-3"), (590, 140, "1080 trays"), (729, 140, "10584,000"), (779, 140, "15785,280"),
        (500, 160, "Total weight including pallets (Net / Gross)"), (726, 160, "11012,000 kg"), (775, 160, "16213,280 kg"),
        (752, 200, "Page 1 of 1")]], ancho=850)
    (f,) = leer_carpeta(tmp_path, usar_ocr=False)
    (l,) = f.lineas
    assert (l.codigo, l.descripcion, l.cantidad, l.precio, l.importe) == \
        ("43050", "Beetroot slices, 2650ml,jars", 4320, 4.1, 17712)
    assert l.ncm_factura == "200190" and l.ncm == "2001.90.00.00"
    assert (l.neto, l.bruto) == (10584, 16213.28)
    assert not any("total igual" in o or "descuento" in o for o in f.observaciones)


def test_facturas_en_subcarpetas(tmp_path):
    sub = tmp_path / "Facturas" / "Proveedor A"
    sub.mkdir(parents=True)
    fila = _fila(180, "1", "PVC-20", "White PVC film", "150", "100.00", "2.00", "200.00")
    _pdf(sub / "INV-77.pdf", [_factura("INV-77", "05/09/2026", [fila], [(170, 220, "TOTAL"), (500, 220, "200.00")])])
    (tmp_path / "Planillas viejas").mkdir()
    _pdf(tmp_path / "Planillas viejas" / "INV-77.pdf", [_factura("INV-77", "05/09/2026", [fila])])
    (f,) = leer_carpeta(tmp_path, usar_ocr=False)
    assert f.numero == "INV-77" and len(f.lineas) == 1


def test_factura_word_con_columnas_en_una_celda(tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    t = d.add_table(rows=2, cols=4)
    t.cell(0, 0).text = "Invoice No: & Date 03 dated 03/07/2026"
    t.cell(1, 0).text = ("# ENM987 SACO CORTO GAMUZA 100% GOAT SUEDE\nREF MD JACKET\n\n# ENM988- POLLERA GAMUZA\n\n"
                         "BANK ADDRESS & DETAILS")
    t.cell(1, 1).text = "TABACO 6pc\nNEGRO 20pc"
    t.cell(1, 2).text = "US$ 120\n\nUS$ 58.50"
    t.cell(1, 3).text = "720.00\n\n1170.00\nTotal US$ 1890.00"
    d.save(tmp_path / "invoice women.docx")
    (f,) = leer_carpeta(tmp_path, usar_ocr=False)
    assert f.numero == "03" and f.fecha == "03/07/2026"
    assert [(l.codigo, l.cantidad, l.precio, l.importe) for l in f.lineas] == \
        [("ENM987", 6, 120, 720), ("ENM988", 20, 58.5, 1170)]
    assert f.lineas[0].descripcion == "SACO CORTO GAMUZA 100% GOAT SUEDE REF MD JACKET"


def test_excel_subencabezado_cajas_mixtas_y_cuadro_de_clasificacion(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["COMMERCIAL INVOICE"])
    ws.append(["Invoice No. & Date"])
    ws.append(["AO4347 DTD 01.08.2026"])
    ws.append(["Marks & Nos.", "No.& Kind", "DESCRIPTION OF GOODS", None, None, "Quantity", None, "Rate", "Amount"])
    ws.append([None, None, "COTTON LADIES GARMENTS"])
    ws.append([None, "STYLE NO.", "STYLE NAME", "COLOUR", "ITEM DESCRIPTION"])
    ws.append([1, "ENM998", "CAMISA VOILE", "BLANCO", "100% COTTON SHIRT", 34, "PCS", 7.25, 246.5])
    ws.append([2, "ENM998", "CAMISA VOILE", "CAFE", "100% COTTON SHIRT", 30, "PCS", 7.25, 217.5])
    ws.append([3, "ENM1303", "PANTALON VOILE", "BLANCO", "100% COTTON TROUSER", 10, "PCS", 7.75, 77.5])
    ws.append([None, None, "TOTAL", None, None, 74, None, None, 541.5])
    pl = wb.create_sheet("PL")
    pl.append(["PACKING LIST"])
    pl.append(["BOX", "STYLE", "COLOUR", "SIZE", None, "TOTAL", "GROSS"])
    pl.append(["NO", "NAME", None, "S", "M", "PCS", "WT(KG'S)"])
    pl.append([1, "ENM998", "BLANCO", 20, 14, 64, 10])
    pl.append([None, "ENM998", "CAFE", 15, 15])
    pl.append([2, "ENM1303", "BLANCO", 5, 5, 10, 2])
    pl.append([None, None, None, None, None, 74, 12])
    wb.save(tmp_path / "INVOICE + PL.xlsx")
    cuadro = openpyxl.Workbook()
    c = cuadro.active
    c.append(["Proveedor: AHUJA"])
    c.append(["Style", "modelo", "P.A. SIM"])
    c.append(["ENM998", "CAMISA", "6206.30.00.291L"])
    cuadro.save(tmp_path / "Cuadro de clasificacion AHUJA.xlsx")
    (f,) = leer_carpeta(tmp_path, usar_ocr=False)
    assert [(l.codigo, l.descripcion) for l in f.lineas][:2] == [
        ("ENM998", "CAMISA VOILE BLANCO 100% COTTON SHIRT"), ("ENM998", "CAMISA VOILE CAFE 100% COTTON SHIRT")]
    assert [l.bruto for l in f.lineas] == [5.31, 4.69, 2.0]
    assert [l.neto for l in f.lineas] == [4.83, 4.27, 1.82]
    assert f.lineas[0].ncm == "6206.30.00.291L" and "Cuadro" in f.lineas[0].motivo
