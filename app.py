"""
Este archivo convierte tu script en un servicio web.

En palabras simples: crea una "puerta" (endpoint) llamada /scrape.
Cuando alguien (n8n) toca esa puerta y le pasa una URL de SECOP,
este archivo llama a tu función scrape_secop_process() y devuelve
el resultado como JSON (el mismo diccionario que ya generabas,
pero listo para viajar por internet).
"""

from fastapi import FastAPI
from pydantic import BaseModel
from scraper import scrape_secop_process

app = FastAPI()


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
def scrape(req: ScrapeRequest):
    try:
        data = scrape_secop_process(req.url)
        return {"ok": True, "data": data}
    except Exception as e:
        return {"ok": False, "error": str(e)}
