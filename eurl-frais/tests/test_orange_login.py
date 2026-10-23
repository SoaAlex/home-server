"""Offline browser tests: Orange login pages are simulated with Playwright routing (no network).

The fake pages reproduce the data-testid structure of login.orange.fr observed on 23/10/2026.
"""
import pytest
from playwright.sync_api import sync_playwright

from eurl_frais.orange_portal import OrangePortalError, _login, _Session

CHANGE_ACCOUNT = """<div role="button" data-testid="change-account"
     onclick="location.href='https://login.orange.fr/accounts'"><span data-testid="user-login">{identity}</span></div>"""

PASSWORD_STEP = """<html><body><h1>Ravi de vous revoir</h1>{chip}
<input readonly tabindex="-1" id="login" aria-hidden="true" type="text" value="{identity}" style="display:none">
<input type="password" id="password">
<button type="button" data-testid="input-password-toggle-password">Show password</button>
<button data-testid="submit-password" type="button" aria-label="S‘identifier"
  onclick="window.submitted = true; location.href='https://espace-client.orange.fr/accueil?l=' +
           encodeURIComponent(new URLSearchParams(location.search).get('l') || '{identity}')">Valider</button>
</body></html>"""

ACCOUNTS = """<html><body><button type="button" data-testid="someone@example.test">someone@example.test</button>
<button type="button" data-testid="input-other-account"
        onclick="location.href='https://login.orange.fr/'">Saisir un autre compte</button></body></html>"""

LOGIN = """<html><body><h1>Espace Client</h1>
<input data-testid="input-login" type="text" id="login" placeholder=" ">
<button data-testid="submit-login" type="button" aria-label="Continuez et passez à l‘étape suivante"
  onclick="location.href='https://login.orange.fr/typed-password?l=' +
           encodeURIComponent(document.getElementById('login').value)">Continuer</button></body></html>"""


def password_page(identity, *, chip=True):
    return PASSWORD_STEP.format(identity=identity, chip=CHANGE_ACCOUNT.format(identity=identity) if chip else "")


@pytest.fixture
def browser_page():
    pages = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()

        def handle(route):
            url = route.request.url.split("?")[0]
            if url in pages:
                return route.fulfill(status=200, content_type="text/html", body=pages[url])
            if url.startswith("https://espace-client.orange.fr/"):
                return route.fulfill(status=200, content_type="text/html", body="<html><body>Mes factures</body></html>")
            return route.abort()

        context.route("**/*", handle)
        yield context.new_page(), pages
        browser.close()


def test_pre_identified_configured_account_submits_the_password(browser_page):
    page, pages = browser_page
    pages["https://login.orange.fr/password"] = password_page("l.user@example.test")
    page.goto("https://login.orange.fr/password")

    _login(_Session(page, None), "L.User@example.test", "pw")

    assert page.url.startswith("https://espace-client.orange.fr/accueil")


def test_other_pre_identified_account_is_switched_to_the_configured_login(browser_page):
    page, pages = browser_page
    pages["https://login.orange.fr/password"] = password_page("someone@example.test")
    pages["https://login.orange.fr/accounts"] = ACCOUNTS
    pages["https://login.orange.fr/"] = LOGIN
    pages["https://login.orange.fr/typed-password"] = password_page("06 12 34 56 78", chip=False)
    page.goto("https://login.orange.fr/password")

    _login(_Session(page, None), "0612345678", "pw")

    assert page.url == "https://espace-client.orange.fr/accueil?l=0612345678"


def test_password_is_never_sent_to_an_unexpected_account(browser_page):
    page, pages = browser_page
    pages["https://login.orange.fr/password"] = password_page("someone@example.test", chip=False)
    page.goto("https://login.orange.fr/password")

    with pytest.raises(OrangePortalError, match="autre compte"):
        _login(_Session(page, None), "0612345678", "pw")

    assert page.evaluate("window.submitted === true") is False
    assert page.locator("#password").input_value() == ""
