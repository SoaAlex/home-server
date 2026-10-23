"""Download the month's invoice PDFs from the Orange customer area with a real browser.

The portal is not a documented API: selectors are generic on purpose and every
unexpected screen stops the run with a readable error. MFA, CAPTCHA or "validate on
your phone" screens are never bypassed: they raise ManualAuthRequired. The browser
session is kept in the private state directory to avoid logging in every month.
`debug=True` saves screenshots and link inventories (local only, may contain personal
data) to adapt the selectors after a portal change.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

ORANGE_HOME = "https://espace-client.orange.fr/"
_MONTHS = ("janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
           "septembre", "octobre", "novembre", "decembre")
_HUMAN_CHECKS = (
    "captcha", "pas un robot", "code recu par sms", "code de verification", "saisissez le code",
    "authentification a deux facteurs", "double authentification", "validez la connexion sur votre mobile",
    "mobile connect", "/otp", "/2fa",
    # "Authentification renforcée" (Orange et moi app): push request to the trusted phone, no password step.
    "mobile de confiance", "identification avec votre mobile", "/orange-et-moi",
)


class OrangePortalError(RuntimeError):
    """The portal could not be used (layout change, wrong password, no invoice...)."""


class ManualAuthRequired(OrangePortalError):
    """Orange asks for a human step (MFA, CAPTCHA, phone validation)."""


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.replace("’", "'"))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _compact(text: str) -> str:
    return re.sub(r"[\s.\-]", "", _plain(text))


def manual_auth_required(url: str, text: str) -> bool:
    probe = _plain(f"{url} {text}")
    if any(token in probe for token in _HUMAN_CHECKS):
        raise ManualAuthRequired(
            "Orange demande une validation humaine (code SMS, CAPTCHA ou validation sur mobile) : "
            "lancer `eurl-frais orange --month AAAA-MM` en supervision ou déposer les PDF dans CHARGES/ORANGE."
        )
    return False


def month_matches(text: str, year: int, month: int) -> bool:
    plain = _plain(text)
    return bool(
        re.search(rf"\b{_MONTHS[month - 1]}\s+{year}\b", plain)
        or re.search(rf"\b\d{{1,2}}/{month:02d}/{year}\b", plain)
        or re.search(rf"(?<![\d/]){month:02d}/{year}\b", plain)
    )


def select_invoice_links(links: list[dict[str, Any]], year: int, month: int) -> list[dict[str, Any]]:
    """Keep download links whose row mentions the requested month (issue month = expense month)."""
    selected = []
    for link in links:
        label = _plain(f"{link.get('text', '')} {link.get('aria', '')}")
        href = _plain(str(link.get("href", "")))
        looks_like_download = bool(re.search(r"telecharg|pdf|download", f"{label} {href}"))
        if looks_like_download and month_matches(f"{link.get('context', '')} {link.get('text', '')}", year, month):
            selected.append(link)
    return selected


_COLLECT_JS = """() => Array.from(document.querySelectorAll('a, button')).map((el, index) => {
    el.setAttribute('data-eurl-frais-index', String(index));
    const row = el.closest('tr, li, article, section, [class*=bill], [class*=facture], [class*=invoice]');
    return {index, tag: el.tagName.toLowerCase(), href: el.href || '', text: (el.innerText || '').trim().slice(0, 160),
            aria: el.getAttribute('aria-label') || '', context: row ? (row.innerText || '').trim().slice(0, 300) : ''};
})"""


class _Session:
    def __init__(self, page, debug_dir: Path | None):
        self.page = page
        self.debug_dir = debug_dir
        self.step = 0

    def snapshot(self, name: str) -> None:
        if not self.debug_dir:
            return
        self.step += 1
        prefix = self.debug_dir / f"{self.step:02d}-{name}"
        self.page.screenshot(path=f"{prefix}.png", full_page=True)
        links = self.page.evaluate(_COLLECT_JS)
        prefix.with_suffix(".json").write_text(
            json.dumps({"url": self.page.url.split("?")[0], "links": links}, ensure_ascii=False, indent=1)
        )

    def body(self) -> str:
        try:
            return self.page.locator("body").inner_text(timeout=5_000)
        except Exception:
            return ""

    def check_human(self) -> None:
        manual_auth_required(self.page.url, self.body())

    def click_text(self, pattern: str, roles=("button", "link")) -> bool:
        """Click the first visible control whose accessible name OR visible text matches.

        Orange buttons may carry an aria-label different from their text
        (text « Valider », accessible name « S‘identifier » with a typographic apostrophe).
        """
        regex = re.compile(pattern, re.I)
        tags = {"button": "button", "link": "a"}
        candidates = [self.page.get_by_role(role, name=regex) for role in roles]
        candidates += [self.page.locator(tags[role]).filter(has_text=regex) for role in roles]
        for target in candidates:
            for index in range(min(target.count(), 5)):
                element = target.nth(index)
                if element.is_visible():
                    element.click(timeout=10_000)
                    self.page.wait_for_load_state("domcontentloaded", timeout=30_000)
                    self.page.wait_for_timeout(1_500)
                    return True
        return False


def _dismiss_cookies(session: _Session) -> None:
    session.click_text(r"^(tout refuser|continuer sans accepter|refuser)$", roles=("button",)) or session.click_text(
        r"^(tout accepter|accepter)$", roles=("button",)
    )


def _visible(locator) -> bool:
    try:
        return bool(locator.count()) and locator.first.is_visible()
    except Exception:
        return False


def _identity(text: str) -> str:
    """Comparable login: lower case, no separators, +33/0033 phone prefix -> 0."""
    compact = _compact(text)
    return re.sub(r"^(\+33|0033)", "0", compact)


def _click_and_wait(session: _Session, locator) -> None:
    locator.first.click(timeout=10_000)
    session.page.wait_for_load_state("domcontentloaded", timeout=30_000)
    session.page.wait_for_timeout(1_500)


def _switch_account(session: _Session, username: str) -> None:
    """login.orange.fr pre-selects the account recognised from the Orange line: use the configured one."""
    page = session.page
    shown = page.locator('[data-testid="user-login"]')
    change = page.locator('[data-testid="change-account"]')
    if not _visible(shown) or _identity(shown.first.inner_text()) == _identity(username) or not _visible(change):
        return
    _click_and_wait(session, change)
    session.snapshot("accounts")
    for button in page.locator("[data-testid]").all():
        if _identity(button.get_attribute("data-testid") or "") == _identity(username) and button.is_visible():
            _click_and_wait(session, button)
            return
    other = page.locator('[data-testid="input-other-account"]')
    if not _visible(other):
        raise OrangePortalError("Choix « autre compte » introuvable sur login.orange.fr (voir --debug)")
    _click_and_wait(session, other)


def _login(session: _Session, username: str, password: str) -> None:
    page = session.page
    if "login.orange.fr" not in page.url:
        return
    _switch_account(session, username)
    login = page.locator('[data-testid="input-login"], input#login:not([readonly]), input[type="email"]')
    typed_login = _visible(login)
    if typed_login:
        login.first.fill(username)
        submit = page.locator('[data-testid="submit-login"]')
        if _visible(submit):
            _click_and_wait(session, submit)
        elif not session.click_text(r"continuer|suivant|valider"):
            raise OrangePortalError("Bouton « Continuer » introuvable sur la page d'identification Orange")
        session.check_human()
        session.snapshot("after-login")
    password_box = page.locator('input#password, input[type="password"]')
    if not (password_box.count() and password_box.first.is_visible()):
        session.check_human()
        raise OrangePortalError("Champ mot de passe Orange introuvable (page de connexion modifiée ?)")
    shown = page.locator('[data-testid="user-login"]')
    recognised = _visible(shown) and _identity(shown.first.inner_text()) == _identity(username)
    if not typed_login and not recognised and _identity(username) not in _identity(session.body()):
        # Never send the password to an account we did not type nor recognise.
        raise OrangePortalError("Orange propose un autre compte que ORANGE_USERNAME : connexion annulée, "
                                "aucun mot de passe envoyé")
    password_box.first.fill(password)
    submit = page.locator('[data-testid="submit-password"]')
    if _visible(submit):
        _click_and_wait(session, submit)
    elif not session.click_text(r"continuer|se connecter|s.identifier|valider"):
        password_box.first.fill("")
        raise OrangePortalError("Bouton de validation du mot de passe introuvable")
    session.check_human()
    session.snapshot("after-password")
    if "login.orange.fr" in page.url:
        text = _plain(session.body())
        if "incorrect" in text or "erreur" in text:
            raise OrangePortalError("Identifiant ou mot de passe Orange refusé")
        raise OrangePortalError("Connexion Orange non aboutie (écran inattendu, voir --debug)")


def download_invoices(
    username: str, password: str, session_dir: Path, download_dir: Path, year: int, month: int, *, debug: bool = False,
) -> list[Path]:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    if not (username and password):
        raise OrangePortalError("ORANGE_USERNAME / ORANGE_PASSWORD absents du .env")
    session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_file = session_dir / "storage_state.json"
    debug_dir = session_dir / "debug" if debug else None
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    downloads: list[Path] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            accept_downloads=True, locale="fr-FR", timezone_id="Europe/Paris",
            storage_state=str(state_file) if state_file.is_file() else None,
        )
        page = context.new_page()
        session = _Session(page, debug_dir)
        try:
            page.goto(ORANGE_HOME, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2_000)
            _dismiss_cookies(session)
            session.snapshot("home")
            session.check_human()
            _login(session, username, password)
            if not session.click_text(r"factures?\b|facturation|factures et paiements"):
                session.snapshot("no-bills-link")
                raise OrangePortalError("Lien « Factures » introuvable dans l'espace client")
            session.check_human()
            session.snapshot("bills")
            links = select_invoice_links(page.evaluate(_COLLECT_JS), year, month)
            if not links and session.click_text(r"historique|toutes mes factures|voir (plus|toutes)"):
                session.snapshot("bills-history")
                links = select_invoice_links(page.evaluate(_COLLECT_JS), year, month)
            for number, link in enumerate(links, start=1):
                target = download_dir / f"orange-{year:04d}-{month:02d}-{number:02d}.pdf"
                element = page.locator(f'[data-eurl-frais-index="{link["index"]}"]')
                try:
                    with page.expect_download(timeout=30_000) as info:
                        element.click(timeout=10_000)
                    info.value.save_as(str(target))
                except PlaywrightTimeout:
                    if not link.get("href", "").startswith("https://"):
                        raise OrangePortalError("Le clic sur « Télécharger » n'a produit aucun PDF") from None
                    response = context.request.get(link["href"], timeout=30_000)
                    if response.status != 200 or not response.body().startswith(b"%PDF"):
                        raise OrangePortalError("Le lien de facture Orange n'a pas renvoyé de PDF") from None
                    target.write_bytes(response.body())
                if not target.read_bytes().startswith(b"%PDF"):
                    raise OrangePortalError("Fichier téléchargé non PDF")
                downloads.append(target)
            context.storage_state(path=str(state_file))
            state_file.chmod(0o600)
            return downloads
        except PlaywrightTimeout as exc:
            session.snapshot("timeout")
            raise OrangePortalError(f"Délai dépassé sur le portail Orange ({page.url.split('?')[0]})") from exc
        finally:
            context.close()
            browser.close()
