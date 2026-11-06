# AANI · VUCE · extensión de Chrome

Carga en VUCE (portal.vuce.gub.uy), con tu sesión ya abierta, un certificado **AANI** (DGSA · Certificado provisorio
para importación de alimento para animales) por cada fila de una tabla, y al final baja una planilla:

| Operación | Denominación comercial | Número de Registro | Monto CIF | Kg bruto | Kg neto | Ingreso | Observación |
|---|---|---|---|---|---|---|---|
| 6584227 | BIOACTIVE | 185359 | 900 | 15 | 10 | Carrasco / Aéreo | |

La columna Observación solo se llena cuando algo no salió bien.

## Instalar (una sola vez)

1. Descomprimir `vuce-aani.zip` en una carpeta fija (por ejemplo `C:\elGonza\vuce-aani`).
2. En Chrome abrir `chrome://extensions`, activar **Modo de desarrollador** (arriba a la derecha).
3. **Cargar extensión sin empaquetar** y elegir esa carpeta.

Para actualizarla: reemplazar los archivos de la carpeta y apretar ↻ en la extensión en `chrome://extensions`.

## La tabla

Lo que cambia en cada certificado. Se pega desde Excel (con o sin la fila de títulos) o se elige la planilla:

| Registro | Monto CIF | Kg bruto | Kg neto | Ingreso |
|---|---|---|---|---|
| 185359 | 900 | 15 | 10 | Carrasco |
| 185360 | 1.234,50 | 20,5 | 18 | Montevideo |

- **Ingreso**: `Carrasco` (o `Aéreo`, `A`) pone *CARRASCO / Aéreo*; `Montevideo` (o `Marítimo`, `Puerto`, `M`)
  pone *Puerto de Montevideo / Marítimo*.
- Los números pueden venir con coma; VUCE usa el punto como decimal y la extensión los convierte.
- Una columna extra cuyo título es un campo del formulario (por ejemplo `Destino de la Mercadería / Depósito`
  o `Nro Documento`) pisa el valor fijo solo para esa fila.

## Valores fijos

Todo lo demás (RUT del importador, régimen, entidad, trámite, teléfonos, emails, país y provincia de origen y
procedencia, NCM, depósito, datos del despachante) está en **Ver valores fijos**, un campo por línea:
`Sección / Campo = valor`. Vienen cargados con los de EQ-INTERNACIONAL. Se cambian ahí y **Guardar valores fijos**.

## Usar

1. Entrar a VUCE como siempre y quedarse en la página de inicio (la del botón INICIAR TRAMITE).
2. Abajo a la derecha aparece **AANI · VUCE**. Abrirlo, pegar la tabla y **Cargar lo pegado**.
3. **Iniciar**. Por cada fila hace lo mismo que a mano: INICIAR TRAMITE → Tipo de documento, Nro Documento,
   Código de régimen, Entidad, Trámite → CONFIRMAR → "…" en la línea AANI → Ejecutar → completa el formulario → ENVIAR.
4. Con **Parar antes de ENVIAR** marcado (viene marcado), se detiene con el formulario completo para que lo revises:
   **Continuar (ENVIAR)** o **Saltear esta**. Cuando ya confíes, desmarcalo y va de corrido. Si VUCE no muestra la
   denominación comercial o falta algún campo, para igual y avisa.
5. Si VUCE rechaza el ENVIAR (recuadro rojo de errores), muestra los errores; corregís en pantalla y Continuar.
6. Al terminar, **Descargar planilla** (o **Copiar** y pegar en Excel).

- Cada fila crea una operación en VUCE al apretar CONFIRMAR. Si después algo falla, la planilla dice qué operación
  quedó creada para completarla o anularla a mano.
- Si la página se recarga en el medio, sigue sola.
- **Diagnóstico** baja un `diagnostico_vuce.html` con la página tal como la ve la extensión. Si algo falla en un
  paso, mandar ese archivo para ajustarla.

## Cómo está hecha

`content.js` corre solo en `*.vuce.gub.uy`. Encuentra cada campo por su etiqueta (y su sección), los botones por su
texto, y guarda el avance en el almacenamiento local de la extensión. No manda datos a ningún otro lado.
`lib/xlsx.mini.min.js` es SheetJS (licencia Apache 2.0) para leer y escribir planillas.
