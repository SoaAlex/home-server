"""Recalcule tous les scores (alertes actives si le webhook est configuré)."""
import httpx
from app import db, settings
from app.collector import rescore

with db.connect() as conn, httpx.Client() as client:
    print("alertes envoyées :", rescore(conn, client, settings.load_criteria()))
