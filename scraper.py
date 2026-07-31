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
       unlocks and shows the real content - the same as if a person
       had solved the captcha by hand.

    4. Once unlocked, we read specific pieces of text from the page
       (percentages, guarantee values, etc.) using their HTML "id" -
       think of an id as a unique label glued to one specific piece
       of the page, so we can say "give me exactly THIS piece" instead
       of "give me some random text".

    5. We collect everything into a Python dictionary that mirrors the
       FINAL TABLE ROW we want to store in a database:
           - numero_de_proceso          (str, used to JOIN with an API)
           - informacion_cumplimiento    (dict -> stored as JSON/JSONB)
           - responsabilidad_civil_contractual     (dict or "N/A")
           - responsabilidad_civil_extracontractual (str: "Si"/"No")

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
         don't have to guess it - we just read it and call it.

WHY DO SOME FIELDS USE "DYNAMIC" NAMES INSTEAD OF HARDCODED ONES?

    For the compliance/guarantee fields (Cumplimiento del contrato,
    Pago de salarios, etc.), the actual text shown on screen can
    change between processes, even though the underlying HTML "id"
    pattern stays mostly the same. So instead of hardcoding the
    Spanish label as a fixed dictionary key, we READ the label
    directly from the page at runtime and use that as the key. This
    way, if the displayed text changes, our code adapts automatically
    instead of silently producing a wrong or missing key.

    We also discovered that the id fragment used to find the LABEL
    (the visible name) is not always the same fragment used to find
    the associated FIELDS (percentage, value). For example:
        label id starts with:  lblComplianceInvestment...
        field ids start with:  nbxComplianceInvest...PercentageField
    That's why each compliance group below stores TWO separate
    fragments (label_fragment and field_fragment) instead of one.

WHY IS THE FINAL OUTPUT SHAPED AS A "TABLE ROW" INSTEAD OF ONE FLAT
DICTIONARY WITH MANY KEYS?

    The end goal of this scraper is to store one row per SECOP process
    in a database, then JOIN that row with data coming from an
    external API (using numero_de_proceso as the shared key), and feed
    the result into a Dashboard.

    Because different processes can have a different NUMBER of
    compliance/guarantee clauses (some have 3, some have 7), a flat
    table with one column per clause would need a huge number of
    mostly-empty columns. Instead, we group all compliance clauses
    into a single nested dictionary (informacion_cumplimiento), which
    maps naturally to a JSON / JSONB column in a database. This keeps
    the table narrow and still fully queryable.

Requirements to run this script:
    pip install playwright requests pandas
    playwright install chromium

--------------------------------------------------------------------
NOTES ADDED FOR THE VPS / MICROSERVICE VERSION (read this once):
--------------------------------------------------------------------
This file runs inside a Docker container on a VPS, called by app.py
every time n8n asks for a scrape. Same two changes as before, each
marked with a "VPS CHANGE" comment right above the modified line:

  1. TWOCAPTCHA_API_KEY is read from an environment variable instead
     of being written directly in the code, so the real secret never
     sits inside GitHub or the container image itself.

  2. headless=False became headless=True, since the VPS has no screen
     attached and can't physically open a visible browser window.

TARGET_URL and the `if __name__ == "__main__":` block (including
row_to_dataframe) are still here, but only matter if you run this
file by itself on your own computer to test it. Inside the container,
app.py calls scrape_secop_process(url) directly with the URL n8n
sent, and returns that dictionary straight back to n8n as JSON - the
nested dicts inside it (informacion_cumplimiento, etc.) serialize to
JSON just fine on their own, so row_to_dataframe/pandas is never
needed for the microservice to work, only for your local testing.
"""

import json
import os
import re
import time

import pandas as pd
import requests
from playwright.sync_api import sync_playwright

# ==================================================================
# CONFIG - things you are likely to change
# ==================================================================

# Your 2Captcha account key. Get it from https://2captcha.com/enterpage
# VPS CHANGE: read from an environment variable (configured in
# EasyPanel's "Entorno" / "Environment" tab as TWOCAPTCHA_API_KEY)
# instead of being written here as text.
TWOCAPTCHA_API_KEY = os.environ.get("TWOCAPTCHA_API_KEY", "")

# The SECOP process page we want to read.
# Only used by the __main__ block below, for local testing. Inside the
# container, app.py passes the real URL from n8n straight into
# scrape_secop_process(url) instead of using this constant.
TARGET_URL = (
    "https://community.secop.gov.co/Public/Tendering/OpportunityDetail/"
    "Index?noticeUID=CO1.NTC.8534047"
)

# CSS selector for the process reference number (e.g. "IPMC-014-2025").
# NOTE: the raw text here can include a trailing phase description in
# parentheses, e.g. "LP-DEO-SMCN-018-2026 (Fase de Seleccion...)".
# We strip that off in clean_process_number() below, since it's not
# needed and would break the JOIN key against the API data.
REQUEST_REFERENCE_SELECTOR = (
    "#fdsRequestSummaryInfo_tblDetail_trRowRef_tdCell2_spnRequestReference"
)

# --- Civil liability (extracontractual): Si/No flag ---
CIVIL_LIABILITY_YES_SELECTOR = "#spnCivilLiabilityFieldTrue"
CIVIL_LIABILITY_NO_SELECTOR = "#spnCivilLiabilityFieldFalse"

# --- Civil liability (contractual): unit + value ---
# The unit label (e.g. "No. de SMMLV") is a <label for="..."> element,
# not an element with its own "lbl..." id, so we match it by its
# "for" attribute instead of the usual "starts with lbl" pattern.
CIVIL_LIABILITY_CONTRACTUAL_UNIT_SELECTOR = (
    'label[for="tdCivilLiabilityMinWagesRBCell_rdbCivilLiabilityMinWagesRB"]'
)
CIVIL_LIABILITY_CONTRACTUAL_VALUE_SELECTOR = "#nbxCivilLiabilityMinWagesField"

# ==================================================================
# COMPLIANCE / GUARANTEE GROUPS (dynamic fields)
# ==================================================================
#
# Each entry represents one compliance/guarantee row on the page
# (e.g. "Cumplimiento del contrato", "Pago de salarios", etc).
#
# Why two fragments instead of one?
#   - label_fragment: used to find the <label> (or similar element)
#     whose id STARTS WITH "lbl" + label_fragment. This element holds
#     the human-readable text currently shown on screen, which we use
#     as the key inside informacion_cumplimiento.
#   - field_fragment: used to build the ids of the related data
#     fields (percentage, value), following the pattern
#     "nbx" + field_fragment + "PercentageField", etc.
#
# These two fragments can differ for the same logical group (see
# ComplianceInvestment/ComplianceInvest or ComplianceRepayment/
# ComplianceRepay below), so we never try to derive one from the
# other - we always store both explicitly.
COMPLIANCE_GROUPS = [
    {"label_fragment": "ComplianceContract", "field_fragment": "ComplianceContract"},
    {"label_fragment": "ComplianceInvestment", "field_fragment": "ComplianceInvest"},
    {"label_fragment": "ComplianceRepayment", "field_fragment": "ComplianceRepay"},
    {"label_fragment": "ComplianceWages", "field_fragment": "ComplianceWages"},
    {"label_fragment": "ComplianceWorksQuality", "field_fragment": "ComplianceWorks"},
    {"label_fragment": "ComplianceServiceQuality", "field_fragment": "ComplianceService"},
    {"label_fragment": "ComplianceGoodsQuality", "field_fragment": "ComplianceGoods"},
    {"label_fragment": "ComplianceOther", "field_fragment": "ComplianceOther"},
]

# Id fragments that belong to a DATE or SUB-FIELD label, not to the
# main group label. Used to skip false-positive matches when several
# elements share the same id prefix (e.g. "lblComplianceContractLabel"
# vs "lblComplianceContractStartDateLabel" - both start with
# "lblComplianceContract", but only the first one is the group name).
LABEL_EXCLUDED_SUFFIXES = ("StartDate", "EndDate", "Percentage", "Value")


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
           the captcha's HTML as the "data-callback" attribute - so we
           don't have to guess it, we just read it directly from the
           page and call it ourselves with our token.
    """
    page.evaluate(
        """(token) => {
            const box = document.getElementById('g-recaptcha-response');
            if (box) {
                box.innerHTML = token;
            }
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


# ==================================================================
# STEP 3: Read pieces of text from the page
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


def clean_process_number(raw_text: str | None) -> str | None:
    """
    Strips the trailing phase description in parentheses from the raw
    process reference text, since only the process number itself is
    used as the JOIN key with the API - the phase text is discarded.

    Example input:
        "LP-DEO-SMCN-018-2026 (Fase de Seleccion (Presentacion de ofertas))"
    Returns:
        "LP-DEO-SMCN-018-2026"

    If there's no parenthesis at all, the whole text (trimmed) is
    returned as-is.
    """
    if raw_text is None:
        return None

    match = re.match(r"^(.*?)\s*\(.*\)\s*$", raw_text.strip())
    if match:
        return match.group(1).strip()

    return raw_text.strip()


def read_civil_liability_extracontractual(page) -> str | None:
    """
    The page always has TWO hidden spans ready for this question:
    one that says "Si" and one that (would say) "No" - but only ONE
    of the two is actually shown at a time; the other stays hidden
    with a "display:none" style.

    So instead of reading text, we check WHICH of the two is visible.
    """
    yes_span = page.query_selector(CIVIL_LIABILITY_YES_SELECTOR)
    no_span = page.query_selector(CIVIL_LIABILITY_NO_SELECTOR)

    if yes_span is not None and yes_span.is_visible():
        return "Si"
    if no_span is not None and no_span.is_visible():
        return "No"
    return None


def read_civil_liability_contractual(page) -> dict | str:
    """
    Reads the "Responsabilidad civil contractual" unit + value.

    Returns a dict like {"unidad": "No. de SMMLV", "valor": "500"} when
    the process defines this guarantee, or the string "N/A" when it
    doesn't apply to this process (no value present on the page).

    NOTE: this currently reads the SMMLV-based value field
    (#nbxCivilLiabilityMinWagesField). If a given process instead
    expresses this guarantee directly in pesos (a different field id),
    that alternate selector isn't wired in yet - let me know the exact
    id if you run into that case and I'll add it as a second check.
    """
    value = read_text(page, CIVIL_LIABILITY_CONTRACTUAL_VALUE_SELECTOR)
    if not value:
        return "N/A"

    unit_label = read_text(page, CIVIL_LIABILITY_CONTRACTUAL_UNIT_SELECTOR)

    return {
        "unidad": unit_label,
        "valor": value,
    }


def read_compliance_group(page, group: dict) -> dict:
    """
    Reads one full compliance/guarantee group (label + percentage +
    value) and returns it as:

        { "<clause label read from the page>": {
              "porcentaje": "...",
              "valor_garantia": "...",
          }
        }

    Two independent id fragments are used on purpose (see the
    COMPLIANCE_GROUPS comment above for why they can differ):
        - group["label_fragment"] to locate the visible label text.
        - group["field_fragment"] to locate the related data fields.

    The label lookup uses a "starts with" selector ([id^="lbl..."])
    because the exact suffix (Label, Field, etc.) isn't guaranteed to
    stay the same. Since that same prefix can also match OTHER labels
    belonging to sub-fields (start date, end date, etc.), we fetch ALL
    matching candidates and skip any whose id contains one of the
    LABEL_EXCLUDED_SUFFIXES, keeping only the actual group name label.

    If no valid label is found, this group simply doesn't apply to
    this particular process, so we return an empty dict and nothing
    gets added to informacion_cumplimiento.
    """
    label_fragment = group["label_fragment"]
    field_fragment = group["field_fragment"]

    candidates = page.query_selector_all(f'[id^="lbl{label_fragment}"]')

    label_element = None
    for candidate in candidates:
        candidate_id = candidate.get_attribute("id") or ""
        if any(suffix in candidate_id for suffix in LABEL_EXCLUDED_SUFFIXES):
            continue
        label_element = candidate
        break

    if label_element is None:
        return {}

    label_text = label_element.inner_text().strip()

    return {
        label_text: {
            "porcentaje": read_text(page, f"#nbx{field_fragment}PercentageField"),
            "valor_garantia": read_text(page, f"#nbx{field_fragment}ValueField"),
        }
    }


def build_compliance_summary(page) -> dict:
    """
    Loops over every configured compliance group and merges the
    results into a single nested dictionary, ready to be stored as
    JSON/JSONB: informacion_cumplimiento.
    """
    summary = {}
    for group in COMPLIANCE_GROUPS:
        summary.update(read_compliance_group(page, group))
    return summary


# ==================================================================
# MAIN FLOW: open the page, unlock it, read everything
# ==================================================================

def scrape_secop_process(url: str) -> dict:
    """
    Returns a dictionary shaped exactly like the final database row:
        {
            "numero_de_proceso": str | None,
            "informacion_cumplimiento": dict,
            "responsabilidad_civil_contractual": dict | "N/A",
            "responsabilidad_civil_extracontractual": "Si" | "No" | None,
        }
    """
    with sync_playwright() as p:
        # headless=False means "open a real, visible Chrome window".
        # We keep it visible on purpose: some anti-bot systems behave
        # more strictly (or block outright) when they detect a browser
        # running with no visible window at all.
        # VPS CHANGE: headless=True. The VPS has no screen attached, so
        # a visible window can't open here - same browser and logic,
        # just not drawn to a display. If the site ever starts blocking
        # headless specifically, add a virtual display (Xvfb) inside
        # the container instead of reverting this line.
        browser = p.chromium.launch(headless=True)
        # VPS CHANGE: locale + Accept-Language added. Locally, Windows was
        # configured in Spanish, so Chrome silently told the SECOP page
        # "I'm a Spanish-speaking browser" and it replied with Spanish
        # labels (Cumplimiento del contrato, Pago de salarios, etc). The
        # VPS container has no such OS-level language setting, so without
        # this, the page falls back to English and build_compliance_summary
        # ends up using the English labels as dictionary keys instead -
        # exactly what you saw (Contract Compliance, Wages payment...).
        # Setting both locale and the Accept-Language header recreates
        # that same "Spanish browser" signal on the VPS.
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-CO",
            extra_http_headers={"Accept-Language": "es-CO,es;q=0.9"},
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
        raw_reference = read_text(page, REQUEST_REFERENCE_SELECTOR)

        row = {
            "numero_de_proceso": clean_process_number(raw_reference),
            "informacion_cumplimiento": build_compliance_summary(page),
            "responsabilidad_civil_contractual": read_civil_liability_contractual(page),
            "responsabilidad_civil_extracontractual": read_civil_liability_extracontractual(page),
        }

        browser.close()
        return row


# ==================================================================
# STEP 4: Turn the result into a table row
# ==================================================================

def row_to_dataframe(row: dict) -> pd.DataFrame:
    """
    Converts one scraped row into a single-row pandas DataFrame.

    informacion_cumplimiento and responsabilidad_civil_contractual are
    stored as JSON TEXT here (not as native Python dicts), because
    that's how they need to travel into a JSON/JSONB column, a CSV
    cell, or an n8n HTTP request body later on.
    """
    flat_row = {
        "numero_de_proceso": row["numero_de_proceso"],
        "informacion_cumplimiento": json.dumps(
            row["informacion_cumplimiento"], ensure_ascii=False
        ),
        "responsabilidad_civil_contractual": json.dumps(
            row["responsabilidad_civil_contractual"], ensure_ascii=False
        ),
        "responsabilidad_civil_extracontractual": row[
            "responsabilidad_civil_extracontractual"
        ],
    }
    return pd.DataFrame([flat_row])


if __name__ == "__main__":
    # This block only runs if you execute "python scraper.py" directly
    # on your own computer. Inside the container, app.py (via uvicorn)
    # is what starts things up, and it calls scrape_secop_process(url)
    # directly with the URL n8n sent - this block never runs there.
    result = scrape_secop_process(TARGET_URL)

    print("\n--- RESULT (row) ---")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print("\n--- RESULT (as DataFrame) ---")
    print(row_to_dataframe(result))
