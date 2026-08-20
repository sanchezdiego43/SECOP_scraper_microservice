"""
Este archivo convierte tu script en un servicio web.

En palabras simples: crea una "puerta" (endpoint) llamada /scrape.
Cuando alguien (n8n) toca esa puerta y le pasa una URL de SECOP,
este archivo llama a tu función scrape_secop_process() y devuelve
el resultado como JSON (el mismo diccionario que ya generabas,
pero listo para viajar por internet).

--------------------------------------------------------------------
NOTA AGREGADA PARA EL REFACTOR ASYNC / NAVEGADOR COMPARTIDO:
--------------------------------------------------------------------
Antes, cada llamada a /scrape lanzaba su propio proceso de Chromium
desde cero dentro de scraper.py, y lo cerraba al terminar. Bajo
concurrencia (varios requests de n8n en paralelo), esto causaba una
condición de carrera de Python/asyncio al intentar lanzar varios
procesos de Chromium al mismo instante ("Racing with another loop to
spawn a process").

Ahora, el navegador se lanza UNA SOLA VEZ cuando este servicio
arranca (ver la función `lifespan` abajo), se guarda en
`app.state.browser`, y se reutiliza para todos los requests durante
toda la vida del contenedor. Cada scrape individual solo abre un
`BrowserContext` liviano sobre ese navegador ya existente (ver
scraper.py) - no un proceso nuevo del sistema operativo - y lo cierra
al terminar. El navegador compartido solo se cierra una vez, cuando
el servicio se apaga.

El endpoint /scrape pasó de `def` a `async def` porque ahora usa
Playwright en modo asíncrono (requisito para poder compartir un mismo
navegador entre varios requests concurrentes de forma segura).
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from playwright.async_api import async_playwright
from pydantic import BaseModel

from scraper import scrape_secop_process


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Arranque del servicio: se ejecuta UNA sola vez ---
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    app.state.playwright = playwright
    app.state.browser = browser
    print("[startup] Navegador compartido lanzado y listo.")

    yield  # <- el servicio queda corriendo y atendiendo requests aquí

    # --- Apagado del servicio: se ejecuta UNA sola vez ---
    await app.state.browser.close()
    await app.state.playwright.stop()
    print("[shutdown] Navegador compartido cerrado.")


app = FastAPI(lifespan=lifespan)


class ScrapeRequest(BaseModel):
    url: str


@app.get("/")
def root():
    # Algunas plataformas (como EasyPanel) revisan esta dirección para
    # confirmar que el servicio sigue "vivo". Si no responde aquí, pueden
    # reiniciar el contenedor pensando que se cayó.
    return {"status": "ok"}


@app.get("/health")
def health():
    # Puerta simple para comprobar que el servicio está vivo.
    return {"status": "ok"}


@app.post("/scrape")
async def scrape(req: ScrapeRequest):
    try:
        data = await scrape_secop_process(req.url, app.state.browser)
        return {"ok": True, "data": data}
    except Exception as e:
        return {"ok": False, "error": str(e)}
