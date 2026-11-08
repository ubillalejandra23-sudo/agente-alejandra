"""Captura automática de la consola web funcionando (evidencia visual). Opcional.

    pip install playwright            # usa el Microsoft Edge o Google Chrome que ya tiene instalado (no descarga nada)
    python evidencias/capturar_ui.py --url http://127.0.0.1:8000 --entorno local

Abre la consola, ingresa la clave (de API_KEYS en .env o --key), elige 2 agentes, envía una consulta colaborativa y guarda
capturas del recorrido en evidencias/capturas/. La clave se escribe solo en el almacenamiento de la pestaña: no queda
visible en ninguna captura (el diálogo de la clave nunca se abre).
"""
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CONSULTA = "Revisa la concentración del fondo FM Inmobiliario y dime si conviene automatizar esa revisión con IA."


def clave() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(RAIZ / ".env")
    except ImportError:
        pass
    return os.environ.get("EVAL_API_KEY") or (os.environ.get("API_KEYS", "").split(",")[0]).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--key", default=None)
    ap.add_argument("--entorno", default="local")
    a = ap.parse_args()
    k = a.key or clave()
    if not k:
        sys.exit("Falta la clave: use --key o defina API_KEYS en .env")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Instale playwright: pip install playwright")

    sello = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = RAIZ / "evidencias" / "capturas"
    destino.mkdir(parents=True, exist_ok=True)
    ruta = lambda n: str(destino / f"{sello}-{a.entorno}-{n}.png")  # noqa: E731

    with sync_playwright() as p:
        nav = None
        for canal in ("msedge", "chrome"):
            try:
                nav = p.chromium.launch(channel=canal, headless=True)
                break
            except Exception:
                continue
        if not nav:
            sys.exit("No se encontró Microsoft Edge ni Google Chrome instalados.")
        pg = nav.new_page(viewport={"width": 1480, "height": 920})
        pg.goto(a.url)
        pg.evaluate("k => sessionStorage.setItem('a2a_key', k)", k)
        pg.reload()
        pg.wait_for_selector(".agente")
        pg.screenshot(path=ruta("1-inicio"))

        pg.click(".agente >> text=Alejandra Ubilla")
        pg.click(".agente >> text=Bryhan Cortes")
        pg.fill("#texto", CONSULTA)
        pg.press("#texto", "Enter")
        pg.wait_for_selector(".msg.progreso")
        pg.wait_for_timeout(5000)  # a mitad de camino: el diagrama muestra a los agentes trabajando
        pg.screenshot(path=ruta("2-en-curso"))
        pg.wait_for_selector(".msg.bot.md, .msg.err", timeout=180000)
        pg.wait_for_timeout(600)
        pg.screenshot(path=ruta("3-resultado"))
        for d in pg.query_selector_all(".tr-agente"):
            d.evaluate("e => e.open = true")
        pg.screenshot(path=ruta("4-traza-aportes"))
        nav.close()
    print("Capturas guardadas en", destino)


if __name__ == "__main__":
    main()
