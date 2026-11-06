# Licencias textiles DNI · extensión de Chrome

Sube varias planillas de licencia textil al sitio de DNI (tramitesdni.miem.gub.uy) usando tu sesión ya abierta,
y al final baja una planilla con el VU de cada archivo:

| A | B | C |
|---|---|---|
| Tex_149130_0001.XLS | VU26080021242242 | |
| Tex_149130_0002.XLS | | No se envió. DNI dijo al importar: … |

La columna C solo se llena cuando algo no salió bien.

## Instalar (una sola vez)

1. Descomprimir `licencias-textiles-dni.zip` en una carpeta fija (por ejemplo `C:\elGonza\licencias-textiles`).
2. En Chrome abrir `chrome://extensions`, activar **Modo de desarrollador** (arriba a la derecha).
3. **Cargar extensión sin empaquetar** y elegir esa carpeta.

Para actualizarla: reemplazar los archivos de la carpeta y apretar el botón ↻ de la extensión en `chrome://extensions`.

## Usar

1. Entrar a DNI con tu usuario, como siempre, y quedarse en la página de inicio (la de "Inicio de procesos").
2. Abajo a la derecha aparece el botón **Licencias textiles**. Abrirlo.
3. **Elegir carpeta** (toma todos los `.xls` / `.xlsx` de la carpeta) o **Elegir archivos**.
4. **Iniciar**. Por cada archivo hace lo mismo que a mano: Licencia Textiles → Tipo ingreso *Planilla Excel* →
   Seleccionar archivo → sube el archivo → Confirmar → Importar → Confirmar → lee el número de trámite → Cerrar.
5. Con **Parar antes de enviar cada uno** marcado (viene marcado), se detiene antes del Confirmar final para que
   revises los datos importados: **Continuar (enviar)** o **Saltear este**. Cuando ya confíes, desmarcalo y va de corrido.
6. Al terminar, **Descargar planilla** (o **Copiar** y pegar en Excel).

- Cada envío usa un formulario del saldo. Si el saldo llega a 0, se detiene.
- Si DNI rechaza una planilla al importar, la marca como error con el mensaje de DNI y sigue con la siguiente.
- Si la página se recarga en el medio, sigue sola. Si se recargó justo después de enviar, ese archivo queda
  como **Revisar** (puede haberse creado el trámite: buscalo en DNI antes de volver a subirlo).
- **Diagnóstico** baja un `diagnostico_dni.html` con la página tal como la ve la extensión. Si algo falla,
  mandar ese archivo para ajustarla.

## Cómo está hecha

`content.js` corre solo en `tramitesdni.miem.gub.uy`. Encuentra los botones por su texto (busca también dentro
de los marcos de Apia), entrega el archivo al selector de la ventana "Documentos" y guarda el avance en el
almacenamiento local de la extensión. No manda datos a ningún otro lado. `lib/xlsx.mini.min.js` es SheetJS
(licencia Apache 2.0) para escribir la planilla.
