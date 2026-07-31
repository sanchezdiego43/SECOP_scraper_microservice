"""
Este archivo es tu script original, con dos cambios importantes para que
funcione en el VPS (un servidor no tiene pantalla, así que no puede abrir
una ventana de Chrome visible):

  1. La API key de 2Captcha ya NO está escrita en el código. Se lee desde
     una "variable de entorno" (una especie de nota secreta que le pasamos
     al contenedor desde fuera, sin que quede grabada en el código).

  2. headless=True: el navegador corre "invisible" (sin ventana), que es
     obligatorio en un servidor.
"""

import os
import time
import requests
from playwright.sync_api import sync_playwright

# La API key ahora se lee de una variable de entorno llamada TWOCAPTCHA_API_KEY.
# La configuraremos desde EasyPanel, nunca la escribas aquí directamente.
TWOCAPTCHA_API_KEY = os.environ.get("TWOCAPTCHA_API_KEY", "")

TEXT_FIELDS = [
    ("% Cumplimiento del contrato", "#nbxComplianceContractPercentageField"),
    ("Valor garantía - Cumplimiento del contrato", "#nbxComplianceContractValueField"),
    ("Fecha desde - Cumplimiento del contrato", "#dtmbComplianceContractStartDateBox_txt"),
    ("Fecha hasta - Cumplimiento del contrato", "#dtmbComplianceContractEndDateBox_txt"),
    ("% Pago de salarios", "#nbxComplianceWagesPercentageField"),
    ("Valor garantía - Pago de salarios", "#nbxComplianceWagesValueField"),
    ("Fecha desde - Pago de salarios", "#dtmbComplianceWagesStartDateBox_txt"),
    ("Fecha hasta - Pago de salarios", "#dtmbComplianceWagesEndDateBox_txt"),
    ("% Calidad del servicio", "#nbxComplianceServicePercentageField"),
    ("Valor garantía - Calidad del servicio", "#nbxComplianceServiceValueField"),
    ("Fecha desde - Calidad del servicio", "#dtmbComplianceServiceStartDateBox_txt"),
    ("Fecha hasta - Calidad del servicio", "#dtmbComplianceServiceEndDateBox_txt"),
    ("% Estabilidad y calidad de la obra", "#nbxComplianceWorksPercentageField"),
    ("Valor garantía - Estabilidad y calidad de la obra", "#nbxComplianceWorksValueField"),
    ("Fecha desde - Estabilidad y calidad de la obra", "#dtmbComplianceWorksStartDateBox_txt"),
    ("Fecha hasta - Estabilidad y calidad de la obra", "#dtmbComplianceWorksEndDateBox_txt"),
    ("% Calidad y correcto funcionamiento de los bienes", "#nbxComplianceGoodsPercentageField"),
    ("Valor garantía - Calidad y correcto funcionamiento de los bienes", "#nbxComplianceGoodsValueField"),
    ("Fecha desde - Calidad y correcto funcionamiento de los bienes", "#dtmbComplianceGoodsStartDateBox_txt"),
    ("Fecha hasta - Calidad y correcto funcionamiento de los bienes", "#dtmbComplianceGoodsEndDateBox_txt"),
    ("% Póliza responsabilidad civil profesional médica", "#nbxComplianceOtherPercentageField"),
    ("Valor garantía - Póliza responsabilidad civil profesional médica", "#nbxComplianceOtherValueField"),
    ("Fecha desde - Póliza responsabilidad civil profesional médica", "#dtmbComplianceOtherStartDateBox_txt"),
    ("Fecha hasta - Póliza responsabilidad civil profesional médica", "#dtmbComplianceOtherEndDateBox_txt"),
    ("% Responsabilidad civil extracontractual", "#nbxCivilLiabilityPercentageField"),
    ("Valor SMMLV - Responsabilidad civil extracontractual", "#nbxCivilLiabilityMinWagesField"),
    ("Valor pesos - Responsabilidad civil extracontractual", "#cbxCivilLiabilityValueField"),
]

REQUEST_REFERENCE_SELECTOR = (
    "#fdsRequestSummaryInfo_tblDetail_trRowRef_tdCell2_spnRequestReference"
)


def solve_recaptcha(sitekey: str, page_url: str) -> str:
    if not TWOCAPTCHA_API_KEY:
        raise RuntimeError(
            "Falta la variable de entorno TWOCAPTCHA_API_KEY. "
            "Configúrala en EasyPanel antes de usar este servicio."
        )

    submit_resp = requests.get(
        "https://2captcha.com/in.php",
        params={
            "key": TWOCAPTCHA_API_KEY,
            "method": "userrecaptcha",
            "googlekey": sitekey,
            "pageurl": page_url,
            "json": 1,
        },
        timeout=30,
    ).json()

    if submit_resp.get("status") != 1:
        raise RuntimeError(f"2Captcha submit error: {submit_resp}")

    job_id = submit_resp["request"]
    print(f"[2Captcha] Job submitted (id={job_id}). Waiting for the solution...")

    max_attempts = 24
    for attempt in range(max_attempts):
        time.sleep(10)
        result_resp = requests.get(
            "https://2captcha.com/res.php",
            params={"key": TWOCAPTCHA_API_KEY, "action": "get", "id": job_id, "json": 1},
            timeout=30,
        ).json()

        if result_resp.get("status") == 1:
            print("[2Captcha] Solved!")
            return result_resp["request"]

        if result_resp.get("request") != "CAPCHA_NOT_READY":
            raise RuntimeError(f"2Captcha solve error: {result_resp}")

        print(f"[2Captcha] Still working on it... ({attempt + 1}/{max_attempts})")

    raise TimeoutError("2Captcha did not return a solution in time.")


def unlock_page_with_token(page, token: str, callback_name):
    page.evaluate(
        """(token) => {
            const box = document.getElementById('g-recaptcha-response');
            if (box) { box.innerHTML = token; }
        }""",
        token,
    )

    if not callback_name:
        print("No data-callback attribute found - nothing to call.")
        return

    print(f"Calling the page's own callback function: {callback_name}(token)")
    function_found = page.evaluate(
        """({callbackName, token}) => {
            if (typeof window[callbackName] === 'function') {
                window[callbackName](token);
                return true;
            }
            return false;
        }""",
        {"callbackName": callback_name, "token": token},
    )
    if not function_found:
        print(f"WARNING: window.{callback_name} does not exist as a function.")


def read_text(page, selector: str):
    element = page.query_selector(selector)
    if element is None:
        return None
    return element.inner_text().strip()


def read_civil_liability_flag(page):
    yes_span = page.query_selector("#spnCivilLiabilityFieldTrue")
    no_span = page.query_selector("#spnCivilLiabilityFieldFalse")
    if yes_span is not None and yes_span.is_visible():
        return "Sí"
    if no_span is not None and no_span.is_visible():
        return "No"
    return None


def scrape_secop_process(url: str) -> dict:
    with sync_playwright() as p:
        # headless=True: obligatorio en el VPS, no hay pantalla disponible.
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        print(f"Opening {url}")
        page.goto(url, wait_until="networkidle", timeout=60000)

        recaptcha_iframe = page.query_selector("iframe[src*='recaptcha']")

        if recaptcha_iframe:
            print("Captcha found. Reading its configuration...")
            captcha_div = page.query_selector("div.g-recaptcha")
            if captcha_div is None:
                raise RuntimeError(
                    "Expected a 'div.g-recaptcha' element but didn't find one."
                )

            sitekey = captcha_div.get_attribute("data-sitekey")
            callback_name = captcha_div.get_attribute("data-callback")
            token = solve_recaptcha(sitekey, url)

            try:
                with page.expect_navigation(timeout=30000):
                    unlock_page_with_token(page, token, callback_name)
            except Exception:
                pass

            page.wait_for_load_state("networkidle", timeout=30000)
        else:
            print("No captcha on this load - continuing directly.")

        data = {}
        data["Número del proceso"] = read_text(page, REQUEST_REFERENCE_SELECTOR)

        for label, selector in TEXT_FIELDS:
            data[label] = read_text(page, selector)

        data["Tiene responsabilidad civil extracontractual"] = read_civil_liability_flag(page)

        browser.close()
        return data
