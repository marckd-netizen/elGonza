# elGonza · revisión de borradores de DUA

Programa para Windows que toma la carpeta de un despacho (borrador de DUA, facturas, flete, seguro,
conocimiento / guía, packing), lee los documentos escaneados con OCR y avisa posibles errores del borrador
según el [libro de criterios](docs/libro_de_criterios.md).

No modifica nada en la carpeta del despacho. Los informes quedan en `Documentos\elGonza\informes`.

## Uso

1. Abrir `elGonza.cmd` (o `programa\elGonza.exe`).
2. **Elegir carpeta y revisar…**: la carpeta de un despacho, o una carpeta que tenga varias carpetas de despacho adentro.
3. Se abre el informe en el navegador con errores, advertencias, puntos a revisar y los controles que dieron bien.
4. En cada observación se puede marcar **Es correcto** o **Falsa alarma** y dejar un comentario.
   **Exportar mis comentarios** baja un `.json` que sirve para ajustar las reglas.

La carpeta tiene que tener un archivo cuyo nombre empiece con `Borrador` (el PDF del borrador con texto).
El tipo de los demás documentos se deduce del nombre (`fact`, `inv`, `flete`, `seguro`, `cese`, `conc`, `bl`,
`awb`, `guia`, `pack`, `thc`, `gastos`) y, si no alcanza, del contenido.

## Documentos desde Mega6

Con **Carpeta de Mega6…** alcanza con el número de carpeta: elGonza toma el borrador que Mega6 deja al
generarlo (`Permiso6\respaldo\Borrador149461.pdf`, propone el último generado) y los documentos de la carpeta.
Desde la línea de comandos: `elgonza --carpeta 149461`.

Con **Borrador + Mega6…** se elige solo el PDF del borrador y elGonza trae los documentos RADE de esa carpeta
desde Mega6 (facturas, conocimiento, packing, flete, seguro, certificados), sin copiarlos a mano. También pasa
si la carpeta elegida tiene solo el borrador. Desde la línea de comandos: `elgonza Borrador145475.pdf` o
`elgonza carpeta --mega6`.

El programa busca la base en `F:\Permiso6`, `D:\Permiso6` o `C:\Permiso6` (o en la variable `ELGONZA_MEGA6`).
Solo lee: el índice `A2137081` une cada documento con su número RADE y su imagen
`IMGRADE\NNNN\M00xxxxxx.tif`. La primera vez arma el índice (unos segundos) y lo guarda en la caché.
No se leen los poderes (AURI).

## Libro de clientes

Los criterios propios de cada cliente se anotan en `Documentos\elGonza\clientes.txt` (botón **Libro de clientes…** en la ventana). Una regla por línea:

```
DECIRAL | vinculacion = si | Deciral: hay que declarar vinculación en el precio
DECIRAL | gastos = 10,28% fob | Deciral: sumar a mano un 10,28% del FOB/FCA al valor en aduana (va en los gastos)
MADINOR | forma de pago iva = 2 | Madinor siempre paga el IVA con certificados de crédito (DGI)
210297450018 | recordar | Devoto: confirmar el stock del depósito
```

El cliente puede ser parte del nombre o el RUT. Condiciones: `ajuste > 0`, `ajuste = N`, `gastos = N% fob` (los Gastos/Aj del borrador tienen que ser ese % del FOB/FCA de las facturas comerciales), `forma de pago aduana = N`, `forma de pago iva = N` (o `contiene TEXTO`; si el despacho no paga IVA ni IRAE queda como advertencia), `vinculacion = si` y `recordar` (solo muestra la nota). Si no se cumple, el informe lo marca como error.

## Descarga

Cada cambio en el repositorio arma el paquete en GitHub Actions (flujo **Windows**). El artefacto
`elGonza-windows` trae `elGonza.cmd` y la carpeta `programa` (el programa con `Tesseract-OCR` en español).

**En la red (F:) para todas las computadoras:** copiar `elGonza.cmd` y `programa` a una carpeta de F: y hacer un
acceso directo a `elGonza.cmd` en cada equipo. Al abrirlo, copia el programa a
`%LOCALAPPDATA%\elGonza\programa` solo si cambió (la primera vez tarda un poco, después es casi inmediato) y lo
abre desde la computadora. Para actualizar todas, alcanza con reemplazar la carpeta `programa` en F:.

## Desde Python

```
pip install -r requirements.txt
python -m elgonza                      # ventana
python -m elgonza "C:\ruta\147317"     # una carpeta, por línea de comandos
```

Para el OCR hace falta [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) con el idioma español.
El programa lo busca en el PATH, en `C:\Program Files\Tesseract-OCR` o junto al `.exe`.
El texto leído por OCR se guarda en `%LOCALAPPDATA%\elGonza\ocr` para no repetirlo.

## Pruebas

```
python -m pytest -q tests
set ELGONZA_EJEMPLOS=C:\ruta\carpetas_de_ejemplo   # opcional: prueba contra carpetas reales
python -m pytest -q tests
```

Las pruebas con carpetas reales verifican que el programa encuentre lo que encontró la revisión manual.
Los documentos de clientes no se suben al repositorio.

## Cómo se agrega una regla

Cada regla es un método `r_NN_nombre` de `Revision` en `elgonza/reglas.py`, donde `NN` es el número del
libro de criterios. Usa `self.b` (el borrador ya leído) y `self.docs` (los documentos con su texto) y llama a
`self.agregar(regla, tema, resultado, título, detalle)`.
