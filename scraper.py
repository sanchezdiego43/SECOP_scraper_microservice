"""
==================================================================
 SECOP II Scraper - explained for beginners
==================================================================

WHAT DOES THIS SCRIPT DO, IN PLAIN WORDS?

    1. It opens a SECOP process page using a real (but automated)
       Chrome browser, thanks to a tool called Playwright.

    2. That page shows a "I'm not a robot" reCAPTCHA challenge before
       letting anyone see the real content. We can't solve that
       ourselves, so we pay a service called 2Captcha to solve it for
       us. 2Captcha gives us back a "token" (a long text string) that
       proves the challenge was solved.

    3. We give that token to the page in the exact way the page
       expects to receive it (more on this below), and the page
       unlocks and shows the real content — the same as if a person
       had solved the captcha by hand.

    4. Once unlocked, we read specific pieces of text from the page
       (percentages, dates, amounts, etc.) using their HTML "id" —
       think of an id as a unique label glued to one specific piece
       of the page, so we can say "give me exactly THIS piece" instead
       of "give me some random text".

    5. We collect everything into a Python dictionary: a simple
       {"label": "value"} structure, so the data is easy to save,
       print, or send somewhere else later (a spreadsheet, a database,
       etc).

WHY DOES THE CAPTCHA PART LOOK COMPLICATED?

    Google's reCAPTCHA is normally solved by a human clicking a
    checkbox. Here we don't have a human, so we:
      a) Ask 2Captcha to solve it (this costs a small fee per solve).
      b) Take the answer 2Captcha gives us (the "token").
      c) Manually place that token exactly where the checkbox would
         have placed it if a human had clicked it.
      d) Manually trigger the same function the page itself would
         have triggered after a successful human click. This function
         name is written in the page's HTML as "data-callback", so we
         don't have to guess it — we just read it and call it.

Requirements to run this script:
    pip install playwright requests
    playwright install chromium

--------------------------------------------------------------------
NOTES ADDED FOR THE VPS / MICROSERVICE VERSION (read this once):
--------------------------------------------------------------------
This file is running inside a Docker container on a VPS, called by
app.py every time n8n asks for a scrape. Two things had to change
compared to the original local version, both marked below with a
"VPS CHANGE" comment right above the modified line:

  1. TWOCAPTCHA_API_KEY is no longer written directly in the code.
     A server is not a private laptop — code sitting in a Git
     repository can be seen by anyone with repo access, and gets
     logged in your commit history forever. So instead, the key is
     read from an "environment variable": a value we inject from
     outside the code (configured in EasyPanel's "Environment" tab),
     which keeps the real secret out of GitHub entirely.

  2. headless=False became headless=True. A VPS has no screen
     attached, so it's physically unable to open a visible Chrome
     window — trying to do so would crash immediately. headless=True
     runs the exact same browser and logic, just without drawing
     anything to a display.

TARGET_URL and the `if __name__ == "__main__":` block at the bottom
still exist, but they're now only useful if you ever want to test
this file by itself, directly on your own computer. Inside the VPS,
they're never used: app.py calls scrape_secop_process(url) directly,
passing in whatever URL n8n sent for that specific process — which is
exactly what that function was already designed to accept.
"""

import time
import os
import requests
from playwright.sync_api import sync_playwright

# ==================================================================
# CONFIG - things you are likely to change
# ==================================================================

# Your 2Captcha account key. Get it from https://2captcha.com/enterpage
# VPS CHANGE: instead of pasting the key as text here, we read it from
# an environment variable configured in EasyPanel (tab "Entorno" /
# "Environment"), named TWOCAPTCHA_API_KEY. This way the real key never
# lives inside the code or gets pushed to GitHub.
TWOCAPTCHA_API_KEY = os.environ.get("TWOCAPTCHA_API_KEY", "")

# The SECOP process page we want to read.
# Still here for local testing (see the __main__ block at the bottom),
# but the microservice (app.py) ignores this constant completely — it
# passes the real URL it received from n8n straight into
# scrape_secop_process(url) instead.
TARGET_URL = (
    "https://community.secop.gov.co/Public/Tendering/OpportunityDetail/"
    "Index?noticeUID=CO1.NTC.8534047"
)

# Every field we want to pull out of the page.
# Each entry is: (label we want in the final dictionary, the id of the
# HTML element that holds the value).
#
# Why a list of tuples instead of writing 28 separate lines of code?
# Because the code to "go get a value" is exactly the same for all of
# them — only the id changes. Writing it once and looping over this
# list avoids repeating the same 3 lines of code 28 times, and makes
# it trivial to add a new field later: just add one line here.
TEXT_FIELDS = [
    # --- Cumplimiento del contrato ---
    ("% Cumplimiento del contrato", "#nbxComplianceContractPercentageField"),
    ("Valor garantía - Cumplimiento del contrato", "#nbxComplianceContractValueField"),
    ("Fecha desde - Cumplimiento del contrato", "#dtmbComplianceContractStartDateBox_txt"),
    ("Fecha hasta - Cumplimiento del contrato", "#dtmbComplianceContractEndDateBox_txt"),

    # --- Pago de salarios ---
    ("% Pago de salarios", "#nbxComplianceWagesPercentageField"),
    ("Valor garantía - Pago de salarios", "#nbxComplianceWagesValueField"),
    ("Fecha desde - Pago de salarios", "#dtmbComplianceWagesStartDateBox_txt"),
    ("Fecha hasta - Pago de salarios", "#dtmbComplianceWagesEndDateBox_txt"),

    # --- Calidad del servicio ---
    ("% Calidad del servicio", "#nbxComplianceServicePercentageField"),
    ("Valor garantía - Calidad del servicio", "#nbxComplianceServiceValueField"),
    ("Fecha desde - Calidad del servicio", "#dtmbComplianceServiceStartDateBox_txt"),
    ("Fecha hasta - Calidad del servicio", "#dtmbComplianceServiceEndDateBox_txt"),

    # --- Estabilidad y calidad de la obra ---
    ("% Estabilidad y calidad de la obra", "#nbxComplianceWorksPercentageField"),
    ("Valor garantía - Estabilidad y calidad de la obra", "#nbxComplianceWorksValueField"),
    ("Fecha desde - Estabilidad y calidad de la obra", "#dtmbComplianceWorksStartDateBox_txt"),
    ("Fecha hasta - Estabilidad y calidad de la obra", "#dtmbComplianceWorksEndDateBox_txt"),

    # --- Calidad y correcto funcionamiento de los bienes ---
    ("% Calidad y correcto funcionamiento de los bienes", "#nbxComplianceGoodsPercentageField"),
    ("Valor garantía - Calidad y correcto funcionamiento de los bienes", "#nbxComplianceGoodsValueField"),
    ("Fecha desde - Calidad y correcto funcionamiento de los bienes", "#dtmbComplianceGoodsStartDateBox_txt"),
    ("Fecha hasta - Calidad y correcto funcionamiento de los bienes", "#dtmbComplianceGoodsEndDateBox_txt"),

    # --- Póliza de responsabilidad civil profesional médica ---
    ("% Póliza responsabilidad civil profesional médica", "#nbxComplianceOtherPercentageField"),
    ("Valor garantía - Póliza responsabilidad civil profesional médica", "#nbxComplianceOtherValueField"),
    ("Fecha desde - Póliza responsabilidad civil profesional médica", "#dtmbComplianceOtherStartDateBox_txt"),
    ("Fecha hasta - Póliza responsabilidad civil profesional médica", "#dtmbComplianceOtherEndDateBox_txt"),

    # --- Responsabilidad civil extracontractual ---
    ("% Responsabilidad civil extracontractual", "#nbxCivilLiabilityPercentageField"),
    ("Valor SMMLV - Responsabilidad civil extracontractual", "#nbxCivilLiabilityMinWagesField"),
    ("Valor pesos - Responsabilidad civil extracontractual", "#cbxCivilLiabilityValueField"),
]

# CSS selector for the process reference number (e.g. "IPMC-014-2025")
REQUEST_REFERENCE_SELECTOR = (
    "#fdsRequestSummaryInfo_tblDetail_trRowRef_tdCell2_spnRequestReference"
)


# ==================================================================
# STEP 1: Solve the captcha using 2Captcha
# ==================================================================

def solve_recaptcha(sitekey: str, page_url: str) -> str:
    """
    Talks to the 2Captcha service and returns the solved token.

    This works in two steps, because solving a captcha takes time
    (usually 15-40 seconds) and 2Captcha's API is built around
    "ask now, come back and check later":

        1. We SUBMIT the job: "here is a captcha on this page, please
           solve it". 2Captcha replies with a job id.
        2. We POLL (ask again and again, every 10 seconds): "is job id
           X done yet?". We keep asking until it says yes, or until we
           give up after ~4 minutes.
    """
    # --- Submit the job ---
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

    # --- Poll until it's ready ---
    max_attempts = 24  # 24 tries * 10 seconds = up to 4 minutes of waiting
    for attempt in range(max_attempts):
        time.sleep(10)

        result_resp = requests.get(
            "https://2captcha.com/res.php",
            params={
                "key": TWOCAPTCHA_API_KEY,
                "action": "get",
                "id": job_id,
                "json": 1,
            },
            timeout=30,
        ).json()

        if result_resp.get("status") == 1:
            print("[2Captcha] Solved!")
            return result_resp["request"]  # this is the token

        if result_resp.get("request") != "CAPCHA_NOT_READY":
            # Some other error happened (bad key, no funds, etc.)
            raise RuntimeError(f"2Captcha solve error: {result_resp}")

        print(f"[2Captcha] Still working on it... ({attempt + 1}/{max_attempts})")

    raise TimeoutError("2Captcha did not return a solution in time.")


# ==================================================================
# STEP 2: Hand the solved token back to the page
# ==================================================================

def unlock_page_with_token(page, token: str, callback_name: str | None) -> None:
    """
    A reCAPTCHA checkbox, when a human clicks it, does two things once
    it gets a valid answer from Google:
        1. It writes the token into a hidden text box on the page
           (id="g-recaptcha-response").
        2. It calls a JavaScript function that the WEBSITE (not Google)
           defined, to tell the website "hey, the visitor is verified,
           you can continue". The name of that function is written on
           the captcha's HTML as the "data-callback" attribute — so we
           don't have to guess it, we just read it directly from the
           page and call it ourselves with our token.
    """
    # 1. Put the token where reCAPTCHA normally puts it.
    page.evaluate(
        """(token) => {
            const box = document.getElementById('g-recaptcha-response');
            if (box) {
                box.innerHTML = token;
            }
        }""",
        token,
    )

    # 2. Call the website's own "captcha solved" function.
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


# ==================================================================
# STEP 3: Read one piece of text from the page
# ==================================================================

def read_text(page, selector: str) -> str | None:
    """
    Reads the text inside whatever HTML element matches `selector`.

    Returns None if the element simply isn't on this page. This is
    expected and normal: not every SECOP process has every single
    field filled in (for example, a process with no civil-liability
    insurance simply won't have those numbers anywhere on the page).
    """
    element = page.query_selector(selector)
    if element is None:
        return None
    return element.inner_text().strip()


def read_civil_liability_flag(page) -> str | None:
    """
    The page always has TWO hidden spans ready for this question:
    one that says "Sí" and one that (would say) "No" - but only ONE
    of the two is actually shown at a time; the other stays hidden
    with a "display:none" style.

    So instead of reading text, we check WHICH of the two is visible.
    """
    yes_span = page.query_selector("#spnCivilLiabilityFieldTrue")
    no_span = page.query_selector("#spnCivilLiabilityFieldFalse")

    if yes_span is not None and yes_span.is_visible():
        return "Sí"
    if no_span is not None and no_span.is_visible():
        return "No"
    return None


# ==================================================================
# MAIN FLOW: open the page, unlock it, read everything
# ==================================================================

def scrape_secop_process(url: str) -> dict:
    with sync_playwright() as p:
        # headless=False means "open a real, visible Chrome window".
        # We keep it visible on purpose: some anti-bot systems behave
        # more strictly (or block outright) when they detect a browser
        # running with no visible window at all.
        # VPS CHANGE: headless=True. A VPS has no display attached, so
        # a visible window simply cannot open here - this runs the
        # identical browser and logic, just without drawing to a screen.
        # If the target site ever starts blocking headless browsers
        # specifically, the fix is to add a virtual display (Xvfb)
        # inside the container rather than reverting this line.
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

        # --- Is there a captcha blocking us? ---
        recaptcha_iframe = page.query_selector("iframe[src*='recaptcha']")

        if recaptcha_iframe:
            print("Captcha found. Reading its configuration...")
            captcha_div = page.query_selector("div.g-recaptcha")
            if captcha_div is None:
                raise RuntimeError(
                    "Expected a 'div.g-recaptcha' element but didn't find one. "
                    "The page's structure may have changed."
                )

            sitekey = captcha_div.get_attribute("data-sitekey")
            callback_name = captcha_div.get_attribute("data-callback")

            token = solve_recaptcha(sitekey, url)

            # Solving the captcha usually makes the site submit a hidden
            # form and reload/redirect to the real content. We wrap the
            # unlock step in `expect_navigation` so Playwright waits for
            # that page change to fully finish before we try to read
            # anything - otherwise we might try to read data from a page
            # that hasn't loaded yet.
            try:
                with page.expect_navigation(timeout=30000):
                    unlock_page_with_token(page, token, callback_name)
            except Exception:
                # Not every site navigates to a new URL - some just
                # refresh their content in place. That's fine, we just
                # continue and wait for the network to settle below.
                pass

            page.wait_for_load_state("networkidle", timeout=30000)
        else:
            print("No captcha on this load - continuing directly.")

        # --- Now that the real content is visible, read everything ---
        data = {}

        data["Número del proceso"] = read_text(page, REQUEST_REFERENCE_SELECTOR)

        for label, selector in TEXT_FIELDS:
            data[label] = read_text(page, selector)

        data["Tiene responsabilidad civil extracontractual"] = read_civil_liability_flag(page)

        browser.close()
        return data


if __name__ == "__main__":
    # This block only runs if you execute "python scraper.py" directly
    # on your own computer. Inside the VPS/Docker container, app.py is
    # what actually starts things up (via uvicorn), and it calls
    # scrape_secop_process(url) directly with the URL n8n sent - this
    # block never runs there.
    result = scrape_secop_process(TARGET_URL)
    print("\n--- RESULT ---")
    for key, value in result.items():
        print(f"{key}: {value}")
