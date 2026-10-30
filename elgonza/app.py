"""Punto de entrada: línea de comandos y ventana para elegir la carpeta."""
import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__
from .borrador import extraer_texto_pdf, parsear
from .documentos import cargar_carpeta
from .informe import a_json, generar
from .reglas import Revision


def carpeta_informes():
    d = Path.home() / "Documents" / "elGonza" / "informes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def revisar_carpeta(carpeta, salida=None, usar_ocr=True, avisar=print):
    """Revisa una carpeta de despacho. Devuelve (ruta_informe, controles) o (None, mensaje) si no hay borrador."""
    carpeta = Path(carpeta)
    ruta_borrador, docs = cargar_carpeta(carpeta, usar_ocr=usar_ocr, avisar=avisar)
    if not ruta_borrador:
        return None, f"No encontré un archivo «Borrador…» en {carpeta}"
    avisar(f"Leyendo {ruta_borrador.name}…")
    b = parsear(extraer_texto_pdf(ruta_borrador))
    if not b.carpeta:
        b.carpeta = carpeta.name
    controles = Revision(b, docs).ejecutar()
    salida = Path(salida) if salida else carpeta_informes()
    salida.mkdir(parents=True, exist_ok=True)
    ruta = salida / f"revision-{b.carpeta}.html"
    ruta.write_text(generar(b, docs, controles, carpeta), encoding="utf-8")
    (salida / f"revision-{b.carpeta}.json").write_text(a_json(b, controles), encoding="utf-8")
    return ruta, controles


def carpetas_a_revisar(raiz):
    """La carpeta elegida, o sus subcarpetas si es una carpeta de carpetas."""
    raiz = Path(raiz)
    if any(p.name.lower().startswith("borrador") for p in raiz.iterdir() if p.is_file()):
        return [raiz]
    return sorted(p for p in raiz.iterdir() if p.is_dir() and any(
        q.name.lower().startswith("borrador") for q in p.iterdir() if q.is_file()))


def resumen(controles):
    n = {k: sum(1 for c in controles if c.resultado == k) for k in ("error", "advertencia", "revisar")}
    return f"{n['error']} errores, {n['advertencia']} advertencias, {n['revisar']} a revisar"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="elgonza", description="Revisa el borrador de DUA contra los documentos de la carpeta.")
    ap.add_argument("carpeta", nargs="?", help="Carpeta del despacho (o carpeta con varias carpetas de despacho)")
    ap.add_argument("--salida", help="Carpeta donde guardar los informes (por defecto Documentos\\elGonza\\informes)")
    ap.add_argument("--sin-ocr", action="store_true", help="No usar OCR en documentos escaneados")
    ap.add_argument("--no-abrir", action="store_true", help="No abrir el informe en el navegador")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
    if not args.carpeta:
        return ventana()
    carpetas = carpetas_a_revisar(args.carpeta)
    if not carpetas:
        print(f"No encontré borradores en {args.carpeta}")
        return 1
    for c in carpetas:
        ruta, controles = revisar_carpeta(c, args.salida, not args.sin_ocr)
        if ruta is None:
            print(controles)
            continue
        print(f"{c.name}: {resumen(controles)} -> {ruta}")
        if not args.no_abrir and len(carpetas) == 1:
            webbrowser.open(ruta.as_uri())
    return 0


def ventana():
    import tkinter as tk
    from tkinter import filedialog, scrolledtext

    raiz = tk.Tk()
    raiz.title(f"elGonza · revisión de DUA {__version__}")
    raiz.geometry("720x460")
    marco = tk.Frame(raiz, padx=12, pady=12)
    marco.pack(fill="both", expand=True)
    tk.Label(marco, text="Elegí la carpeta del despacho (o una carpeta con varias).", anchor="w").pack(fill="x")
    log = scrolledtext.ScrolledText(marco, height=18, state="disabled", font=("Consolas", 10))
    informes = []

    def escribir(msg):
        def _():
            log.configure(state="normal")
            log.insert("end", msg + "\n")
            log.see("end")
            log.configure(state="disabled")
        raiz.after(0, _)

    def trabajar(carpeta):
        try:
            carpetas = carpetas_a_revisar(carpeta)
            if not carpetas:
                escribir("No encontré ningún archivo «Borrador…» en esa carpeta.")
            for c in carpetas:
                escribir(f"— Revisando {c.name}")
                ruta, controles = revisar_carpeta(c, avisar=escribir)
                if ruta is None:
                    escribir(controles)
                    continue
                informes.append(ruta)
                escribir(f"  {resumen(controles)}")
                escribir(f"  Informe: {ruta}")
            if len(informes) == 1:
                webbrowser.open(informes[0].as_uri())
            elif informes:
                os.startfile(str(informes[0].parent)) if sys.platform == "win32" else webbrowser.open(informes[0].parent.as_uri())
        except Exception as e:
            escribir(f"Error: {e}")
        finally:
            raiz.after(0, lambda: boton.configure(state="normal"))

    def elegir():
        carpeta = filedialog.askdirectory(title="Carpeta del despacho")
        if not carpeta:
            return
        informes.clear()
        boton.configure(state="disabled")
        threading.Thread(target=trabajar, args=(carpeta,), daemon=True).start()

    def abrir_libro():
        from .clientes import cargar, ruta_libro
        cargar()  # lo crea con ejemplos si no existe
        ruta = ruta_libro()
        os.startfile(str(ruta)) if sys.platform == "win32" else webbrowser.open(ruta.as_uri())

    botones = tk.Frame(marco)
    botones.pack(anchor="w", pady=8, fill="x")
    boton = tk.Button(botones, text="Elegir carpeta y revisar…", command=elegir, padx=10, pady=4)
    boton.pack(side="left")
    tk.Button(botones, text="Libro de clientes…", command=abrir_libro, padx=10, pady=4).pack(side="left", padx=8)
    log.pack(fill="both", expand=True)
    raiz.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
