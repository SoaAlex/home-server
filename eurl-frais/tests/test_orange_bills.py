"""Offline test of the bills pages (structure observed on espace-client.orange.fr, 23/10/2026).

A local HTTP server plays espace-client.orange.fr so that both the browser and the replayed
API request reach it (Playwright routes do not apply to APIRequestContext).
"""
import threading
from datetime import date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright

from eurl_frais.orange_portal import _collect_month_pdfs, _Session

FILLER = "".join(f'<a href="/x{i}">x{i}</a>' for i in range(5))

HOME = f"""<html><body>
<header><button type="button" onclick="location.href='/boutique'">Internet</button></header>
<nav><a href="/accueil">Test</a>
  <button type="button" onclick="showMobile()">Forfait mobile</button>
  <ul id="menu"><li><a aria-label="Factures de Forfait mobile de Test" href="/facture-paiement/111">Factures</a></li></ul>
  <button type="button" onclick="showInternet()">Offre internet</button></nav>
<a href="/facture-paiement/111">Factures 6 octobre 2026 : 21,00 € Consultez, gérez, payez vos factures en ligne.</a>
{FILLER}
<script>
function setMenu(id, name) {{
  document.getElementById('menu').innerHTML = '<li><a aria-label="Factures de ' + name +
    ' de Test" href="/facture-paiement/' + id + '">Factures</a></li>';
}}
function showMobile() {{ setMenu('111', 'Forfait mobile'); }}
function showInternet() {{ setMenu('222', 'Offre internet'); }}
</script></body></html>"""


def history(contract, bills):
    """Live markup: label only in a padded sr-only span, icon glyph injected by CSS, PDF fetched by XHR."""
    def row(day, key, amount):
        action = (f"event.preventDefault(); fetch('/ecd_wp/facture/v1.0/pdf?c={contract}&d={key}')"
                  f".then(() => history.pushState(null, '', '/facture-paiement/{contract}/afficher-la-facture'))")
        return (f'<tr><td><a class="icon-pdf-file" href="" data-e2e="bh-bill-iconPdf-0" onclick="{action}">'
                f'<span class="sr-only"> Voir la facture du {day} au format PDF </span></a></td>'
                f"<td>{day}</td><td>{amount} €</td><td>Statut de paiement Ok pour la facture du {day}</td></tr>")

    style = ('<style>.icon-pdf-file::before { content: "\\e91e"; } .sr-only { position: absolute; width: 1px; '
             'height: 1px; overflow: hidden; clip: rect(0,0,0,0); }</style>')
    rows = "".join(row(*bill) for bill in bills)
    return f"<html><head>{style}</head><body>{FILLER}<table>{rows}</table></body></html>"


PAGES = {
    "/accueil": HOME,
    "/facture-paiement/111/historique-des-factures": history(
        "111", [("6 octobre 2026", "1006", "21,00"), ("7 septembre 2026", "0907", "33,33"), ("6 août 2026", "0806", "15,99")]),
    "/facture-paiement/222/historique-des-factures": history(
        "222", [("12 septembre 2026", "0912", "1 045,99"), ("12 août 2026", "0812", "45,99")]),
}


def pdf_body(query):
    # Binary bytes that are not valid UTF-8, served with « charset=UTF-8 » like the live endpoint.
    return b"%PDF-1.5\n%\xe2\xe3\xcf\xd3 \xff\xfe\x80 " + query.encode()


class FakeOrange(BaseHTTPRequestHandler):
    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/ecd_wp/facture/v1.0/pdf":
            self.server.pdf_queries.append(query)
            body, content_type = pdf_body(query), "application/pdf;charset=UTF-8"
        elif path in PAGES:
            body, content_type = PAGES[path].encode(), "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def portal():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOrange)
    server.pdf_queries = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        page.goto(f"{base}/accueil")
        yield page, base, server
        browser.close()
    server.shutdown()


def test_downloads_exact_pdf_bytes_with_portal_metadata(portal, tmp_path):
    page, base, _server = portal

    invoices = _collect_month_pdfs(_Session(page, None), tmp_path, 2026, 9, base=base)

    found = sorted((i.service, i.issue_date, i.total_ttc, i.path.read_bytes()) for i in invoices)
    assert found == [
        ("internet", date(2026, 9, 12), Decimal("1045.99"), pdf_body("c=222&d=0912")),
        ("mobile", date(2026, 9, 7), Decimal("33.33"), pdf_body("c=111&d=0907")),
    ]


def test_month_without_invoice_downloads_nothing(portal, tmp_path):
    page, base, server = portal

    assert _collect_month_pdfs(_Session(page, None), tmp_path, 2026, 7, base=base) == []
    assert server.pdf_queries == []
