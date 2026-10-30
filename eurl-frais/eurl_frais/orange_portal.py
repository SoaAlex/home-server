"""Download the month's invoice PDFs from the Orange customer area with a real browser.

Flow observed on 23/10/2026 (data-testid / aria-label selectors, generic fallbacks):
login.orange.fr pre-selects the account recognised from the home Orange line -> switch to ORANGE_USERNAME
when different -> password -> optional promotion screen (« Plus tard ») -> espace-client.orange.fr/accueil.
Each contract (« Forfait mobile », « Offre internet ») has /facture-paiement/<id>/historique-des-factures
listing links « Voir la facture du <date> au format PDF »; a click fetches the PDF from /ecd_wp/facture/.

MFA, CAPTCHA or « validate on your phone » screens are never bypassed: they raise ManualAuthRequired.
The browser session is kept in the private state directory to avoid logging in every month.
`debug=True` saves screenshots and link inventories (local only, they contain personal data).
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from .domain import money

ESPACE_CLIENT = "https://espace-client.orange.fr"
ORANGE_HOME = f"{ESPACE_CLIENT}/"
CUSTOMER_AREA = f"{ESPACE_CLIENT}/accueil"
_MONTHS = ("janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
           "septembre", "octobre", "novembre", "decembre")
_INVOICE_LABEL_RE = re.compile(r"voir la facture du (\d{1,2})(?:er)? ([a-z]+) (\d{4})")
_HUMAN_CHECKS = (
    "captcha", "pas un robot", "code recu par sms", "code de verification", "saisissez le code",
    "authentification a deux facteurs", "double authentification", "validez la connexion sur votre mobile",
    "mobile connect", "/otp", "/2fa",
    # « Authentification renforcée » (Orange et moi app): push request to the trusted phone, no password step.
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


def _identity(text: str) -> str:
    """Comparable login: lower case, no separators, +33/0033 phone prefix -> 0."""
    return re.sub(r"^(\+33|0033)", "0", _compact(text))


def manual_auth_required(url: str, text: str) -> bool:
    probe = _plain(f"{url} {text}")
    if any(token in probe for token in _HUMAN_CHECKS):
        raise ManualAuthRequired(
            "Orange demande une validation humaine (code SMS, CAPTCHA ou validation sur mobile) : "
            "lancer `eurl-frais orange --month AAAA-MM` en supervision ou déposer les PDF dans Charges/Orange."
        )
    return False


def invoice_label_date(label: str) -> date | None:
    """Date of a « Voir la facture du 7 septembre 2026 au format PDF » link, else None."""
    match = _INVOICE_LABEL_RE.search(_plain(label))
    if not match or match.group(2) not in _MONTHS:
        return None
    return date(int(match.group(3)), _MONTHS.index(match.group(2)) + 1, int(match.group(1)))


_COLLECT_JS = """() => Array.from(document.querySelectorAll('a, button')).map((el, index) => {
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
        try:
            self.page.screenshot(path=f"{prefix}.png", full_page=True)
            links = self.page.evaluate(_COLLECT_JS)
            prefix.with_suffix(".json").write_text(
                json.dumps({"url": self.page.url.split("?")[0], "links": links}, ensure_ascii=False, indent=1)
            )
        except Exception:  # debugging must never break the run
            pass

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


def _visible(locator) -> bool:
    try:
        return bool(locator.count()) and locator.first.is_visible()
    except Exception:
        return False


def _click_and_wait(session: _Session, locator) -> None:
    locator.first.click(timeout=10_000)
    session.page.wait_for_load_state("domcontentloaded", timeout=30_000)
    session.page.wait_for_timeout(1_500)


def _dismiss_cookies(session: _Session) -> None:
    session.click_text(r"^(tout refuser|continuer sans accepter|refuser)$", roles=("button",)) or session.click_text(
        r"^(tout accepter|accepter)$", roles=("button",)
    )


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
    if not _visible(password_box):
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
    if "/promo/" in page.url:
        # Optional upsell (e.g. « Activez l'authentification renforcée ») shown after a successful login:
        # postpone it, never accept it on the user's behalf.
        if not session.click_text(r"^\s*plus tard\s*$", roles=("button", "link")):
            raise OrangePortalError("Écran promotionnel Orange sans bouton « Plus tard » (voir --debug)")
        session.check_human()
        session.snapshot("after-promo")
    if "login.orange.fr" in page.url:
        text = _plain(session.body())
        if "incorrect" in text or "erreur" in text:
            raise OrangePortalError("Identifiant ou mot de passe Orange refusé")
        raise OrangePortalError("Connexion Orange non aboutie (écran inattendu, voir --debug)")


def _wait_for_customer_area(page, timeout_ms: int = 30_000) -> None:
    """espace-client.orange.fr is a single-page app: wait until it has rendered real links."""
    try:
        page.wait_for_function("document.querySelectorAll('a[href]').length >= 5", timeout=timeout_ms)
    except Exception:
        raise OrangePortalError("L'espace client Orange ne s'est pas affiché (voir --debug)") from None
    page.wait_for_timeout(1_500)


@dataclass(frozen=True)
class PortalInvoice:
    """A downloaded PDF with the facts shown by the customer area (source of truth for date and amount)."""

    path: Path
    service: str
    issue_date: date
    total_ttc: Decimal
    contract: str


def _service_from_labels(labels: str) -> str:
    plain = _plain(labels)
    # Mobile plans are named after their data allowance (« Forfait 100Go 5G »), without the word « mobile ».
    mobile = bool(re.search(r"\bmobile\b|\bforfait\b|\d+\s?go\b|\b[45]g\b", plain))
    internet = bool(re.search(r"internet|livebox|fibre|adsl|\bbox\b", plain))
    if mobile != internet:
        return "mobile" if mobile else "internet"
    return "orange"


def _contracts(page) -> dict[str, str]:
    """Contract id -> service, from the « Factures de <offre> » links; each contract button is opened in turn."""
    labels: dict[str, str] = {}

    def scan() -> None:
        links = page.eval_on_selector_all(
            'a[href*="/facture-paiement/"]',
            "els => els.map(e => [e.href, (e.getAttribute('aria-label') || '') + ' ' + (e.textContent || '')])")
        for href, label in links:
            match = re.search(r"/facture-paiement/(\d+)", href)
            if match:
                labels[match.group(1)] = f"{labels.get(match.group(1), '')} {label}"

    scan()
    buttons = page.get_by_role("button", name=re.compile(r"^\s*(forfait|offre)\b", re.I))
    for index in range(buttons.count()):
        button = buttons.nth(index)
        if button.is_visible():
            button.click(timeout=10_000)
            page.wait_for_timeout(1_500)
            scan()
    return {contract: _service_from_labels(text) for contract, text in labels.items()}


def _row_amount(row_text: str, issued: date) -> Decimal:
    """Amount printed right after the invoice date in its history row (« 7 septembre 2026  33,33 € »)."""
    plain = _plain(row_text)
    pattern = (rf"\b{issued.day}(?:er)? {_MONTHS[issued.month - 1]} {issued.year}\s+"
               r"(\d{1,3}(?:[ \u00a0\u202f]\d{3})*,\d{2})\s*€")
    match = re.search(pattern, plain)
    if not match:
        raise OrangePortalError(f"Montant de la facture du {issued:%d/%m/%Y} introuvable sur l'espace client")
    return money(match.group(1))


_ROWS_JS = """els => els.map(e => {
    const label = (e.getAttribute('aria-label') || e.textContent || '').replace(/\\s+/g, ' ').trim();
    const row = e.closest('tr, li, [role=row], [class*=bill], [class*=facture], [class*=invoice]') || e.parentElement;
    return [label, row ? row.innerText : ''];
})"""


def _collect_month_pdfs(
    session: _Session, download_dir: Path, year: int, month: int, *, base: str = ESPACE_CLIENT,
) -> list[PortalInvoice]:
    page = session.page
    contracts = _contracts(page)
    if not contracts:
        raise OrangePortalError("Aucun contrat trouvé dans l'espace client Orange (voir --debug)")
    invoices: list[PortalInvoice] = []
    for contract, service in contracts.items():
        history = f"{base}/facture-paiement/{contract}/historique-des-factures"
        page.goto(history, wait_until="domcontentloaded", timeout=45_000)
        _wait_for_customer_area(page)
        session.check_human()
        session.snapshot(f"history-{contract[-4:]}")
        wanted: dict[str, tuple[date, Decimal]] = {}
        for label, row in page.locator("a").evaluate_all(_ROWS_JS):
            issued = invoice_label_date(label)
            if issued and (issued.year, issued.month) == (year, month) and label not in wanted:
                wanted[label] = (issued, _row_amount(row, issued))
        for label, (issued, amount) in wanted.items():
            if page.url.split("?")[0] != history:
                page.goto(history, wait_until="domcontentloaded", timeout=45_000)
                _wait_for_customer_area(page)
            # Substring match: the live accessible name also contains the CSS icon glyph of the link.
            link = page.get_by_role("link", name=label).or_(page.locator("a").filter(has_text=label)).first
            with page.expect_response(
                lambda response: "application/pdf" in response.headers.get("content-type", "").lower(),
                timeout=30_000,
            ) as pdf_response:
                link.click(timeout=10_000)
            # The endpoint answers « application/pdf;charset=UTF-8 »: the browser-side body is decoded as text
            # and corrupted, so the same request is replayed through the session's HTTP client (raw bytes).
            replay = page.context.request.fetch(pdf_response.value.request)
            content = replay.body()
            if replay.status != 200 or not content.startswith(b"%PDF"):
                raise OrangePortalError(f"La facture Orange du {issued:%d/%m/%Y} n'a pas pu être téléchargée en PDF")
            target = download_dir / f"orange-{contract}-{issued:%Y-%m-%d}.pdf"
            target.write_bytes(content)
            invoices.append(PortalInvoice(target, service, issued, amount, contract))
    return invoices


def download_invoices(
    username: str, password: str, session_dir: Path, download_dir: Path, year: int, month: int, *, debug: bool = False,
) -> list[PortalInvoice]:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    if not (username and password):
        raise OrangePortalError("ORANGE_USERNAME / ORANGE_PASSWORD absents du .env")
    session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_file = session_dir / "storage_state.json"
    debug_dir = session_dir / "debug" if debug else None
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
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
            context.storage_state(path=str(state_file))  # keep the session as soon as the login succeeded
            state_file.chmod(0o600)
            if not page.url.startswith(CUSTOMER_AREA):
                page.goto(CUSTOMER_AREA, wait_until="domcontentloaded", timeout=45_000)
            _wait_for_customer_area(page)
            session.check_human()
            session.snapshot("customer-area")
            invoices = _collect_month_pdfs(session, download_dir, year, month)
            context.storage_state(path=str(state_file))
            state_file.chmod(0o600)
            return invoices
        except PlaywrightTimeout as exc:
            session.snapshot("timeout")
            raise OrangePortalError(f"Délai dépassé sur le portail Orange ({page.url.split('?')[0]})") from exc
        finally:
            context.close()
            browser.close()
