# Libro de criterios

Reglas que aplica elGonza al revisar un borrador de DUA. Versión 2, con las respuestas de Marcelo del 30/10/2026.
La columna **Programa** indica si la regla ya está automatizada (`reglas.py`, método `r_NN_…`).

Resultados: **error** (probable error con impacto en tributos o validez), **advertencia** (no coincide con los documentos),
**revisar** (puede estar bien, lo mira una persona), **ok**.

## Prioridades

| # | Regla | Programa |
|---|---|---|
| 1 | El importador del DUA es el de las facturas (nombre o RUT). | Sí |
| 2 | La condición de compra / Incoterm del DUA coincide con cada factura. | Sí (cabezal vs facturas y texto de la factura) |
| 3 | La frontera o aduana de ingreso coincide con el documento de transporte. | Parcial |
| 4 | Tipo de importación y subrégimen corresponden a la operación (Doc.Carga 32 ↔ subrégimen 12). | Sí |
| 5 | La moneda del DUA es la de la factura de origen. | Sí |
| 6 | Números y fechas de factura coinciden con los documentos. | Sí (con OCR; si no se lee, queda «revisar») |
| 7 | Bultos y cantidades coinciden; cada línea de factura asignada a un solo ítem. | Sí |
| 8 | Pesos brutos y netos coinciden. | Sí |
| 9 | Códigos de unidad correctos (kilos = neto, toneladas, total de unidades). | Sí |
| 10 | El origen de cada mercadería es el de la factura o el packing. | Sí |
| 11 | Atención a partidas de bajo arancel (0% de recargo e IMADUNI, o mucho menor que el resto). | Sí, como «revisar» |

## Valores

| # | Regla | Programa |
|---|---|---|
| 12 | La suma de las facturas da el Tot.Fac del cabezal. | Sí |
| 13 | CIF = FOB + flete + seguro (+ gastos); CIF del cabezal = suma de ítems; VNA = CIF + gastos. | Sí |
| 14 | Valor DNA = (CIF + gastos) × T/C; con factura no USD, CIF USD = valor × arbitraje. | Sí |
| 15 | Gastos/Aj del cabezal = suma de los gastos de los ítems. | Sí |
| 16 | Gastos/Aj incluye todos los gastos que no llevan IVA, salvo el almacenaje. Si la factura de gastos trae «Subtotal no gravado», se compara contra ese importe. | Sí, cuando hay subtotal no gravado; si no, aviso para confirmar |
| 17 | Flete prorrateado por FOB, solo hasta frontera / puerto de destino. | Sí (prorrateo parejo) |
| 18 | Sin certificado de seguro: ficto 1%. Con certificado: el premio internacional. Con CIF/CIP el seguro va dentro del precio y se declara 0. | Sí |
| 19 | EXW o FCA: el flete cubre desde el punto de entrega. | Sí (busca el flete en sus documentos) |
| 19b | CPT (y similares con flete incluido): se declara el precio total de la factura como valor, aunque la factura separe el flete y llegue a un destino dentro de Uruguay. No es error. | No se marca |

## Tributos

| # | Regla | Programa |
|---|---|---|
| 20 | Base IVA / IRAE = VNA + recargo + IMADUNI; IVA anticipado y tasa consular según tasas del ítem. | Sí |

## Transporte

| # | Regla | Programa |
|---|---|---|
| 21 | Número de BL / guía (MAWB, HAWB) del DUA = el del documento. | Sí |
| 22 | Buque: el del arribo, aunque el BL nombre otro por transbordo. | «Revisar» si no coincide con el BL |
| 23 | Contenedor solo en FCL; en LCL se declaran 0. Dígito verificador correcto. Cada contenedor declarado tiene que figurar en el BL. | Sí |
| 24 | Precinto: solo la parte numérica. | Sí |
| 25 | Terrestre: el CRT no se carga en el borrador (se asocia en frontera). | Sí (no se pide) |
| 26 | Bandera del medio de transporte: viene de LUCIA / manifiesto, no se compara. | No se controla |
| 27 | No numerar con un BL en estado DRAFT. | Sí si el OCR lee «DRAFT» |

## Bultos y pesos

| # | Regla | Programa |
|---|---|---|
| 9b | La cantidad comercial de cada ítem tiene que coincidir con la factura. Es error: las cantidades son importantes. | Sí: error si la factura se leyó (cantidad × precio da el importe); a revisar si el OCR no la pudo leer |
| 28 | Bultos del DUA = bultos del documento de transporte (o la suma por contenedor del BL). | Sí |
| 29 | Bruto y neto = packing / BL; si el packing trae peso por línea, el neto por ítem sale de ahí. Con muchas facturas, el neto puede ser la suma de los netos de cada una. | Sí |
| 30 | Convertir libras a kilos al comparar. | Se informa el equivalente |

## Origen y clasificación

| # | Regla | Programa |
|---|---|---|
| 31 | Origen distinto en una línea → se abre el ítem con ese origen, haya o no certificado. | Sí (regla 10) |
| 32 | NCM vs HS del BL o factura a 6 dígitos: si difiere, «revisar». NCM incompleto: advertencia. | Sí |
| 33 | 7321 vs 8419: no es error si el equipo es industrial. | No se marca |
| 34 | Prendas de bebé (6111): se sigue al proveedor. | No se marca |
| 35 | Prendas por sexo (6211.3x hombre, 6211.4x mujer). | Pendiente |

## Subrégimen y naturaleza

| # | Regla | Programa |
|---|---|---|
| 36 | M1 = muestras sin valor comercial (inutilizadas). M2 = con valor comercial. Factura comercial con pago no va en M1. | Sí |
| 37 | Reparación de un bien que salió de Uruguay: reimportación tras exportación temporal. | Sí |

## No se revisa

| # | Regla |
|---|---|
| 38 | Direcciones de importador y proveedor: salen de la base de datos del despachante. |

## Criterios por cliente

Se cargan en el libro de clientes (`Documentos\elGonza\clientes.txt`).

| Cliente | Criterio |
|---|---|
| Madinor | Paga IVA e IRAE con certificados de crédito: forma de pago IVA 2. Si el despacho no paga IVA ni IRAE no es error, pero es preferible declararla igual. En el historial, 560 de 575 despachos con forma 2; el 144330 fue un error. |
| Deciral | Declarar vinculación en el precio. Ajuste de valor de 10,28% sobre el FOB/FCA: no tiene campo propio, se calcula a mano y se suma en Gastos/Aj (ej. 145475: 46.605,90 × 10,28% = 4.791,06). |
| Buscalibre | Cuando le compra a Buscalibre: declarar vinculación (no afecta el precio). |
| Scotiabank | Cuando le compra a Idemia Colombia: no paga las tarjetas, va sin pago (F.Pago 4) y método de valoración 2. |
