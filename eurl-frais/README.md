# eurl-frais — frais mensuels de l'EURL

Conteneur CLI (aucun port publié) lancé le 10 de chaque mois par une tâche Hermes. Pour le mois de frais
**M** (par défaut le mois précédent), il :

1. télécharge les factures Orange (Internet + Mobile) émises en M depuis l'espace client (Playwright) et les
   archive dans `EURL/CHARGES/ORANGE/AAAA-MM_Orange_<service>_<référence>.pdf` (jamais d'écrasement, dédoublonnage SHA-256) ;
2. émet la quittance du loyer de M (`EURL/LOYER/QUITTANCE DE LOYER <MOIS> <AAAA>.pdf`) en recopiant le modèle Word
   `LOYER/QUITTANCE DE LOYER.docx` (textes, gras/italique, filets, signature) ; seules la période, la date de paiement
   (par défaut le `RENT_PAYMENT_DAY` de M+1) et la date d'émission (jour du passage) changent ;
3. produit `EURL/CHARGES/RAPPORTS/RAPPORT_FRAIS_AAAA-MM.pdf` : note « Charges <Mois> » = quote-part `ORANGE_SHARE`
   du TTC de chaque facture Orange (arrondie au centime) + forfaits gaz/électricité, note « Loyer <Mois> », **HT = TTC,
   TVA 0** ;
4. publie le résumé et les PDF (rapport, quittance, factures) sur Discord, une seule fois par mois complet.

Aucune écriture dans Tiime, pas d'indemnités kilométriques.

## Accès et secrets

- Seuls `EURL/LOYER` et `EURL/CHARGES` sont montés (lecture/écriture), exécution en `1000:10`.
- `.env` (privé, `chmod 600`, git-ignoré) : modèle `.env.example`. Les montants personnels (quote-part, gaz,
  électricité) et les identifiants n'apparaissent que dans `.env`.
- `state/` (git-ignoré) : session du navigateur Orange, mois déjà envoyés, verrou, captures de diagnostic.

## Commandes

Depuis le sandbox Hermes, le wrapper `/workspace/bin/eurl-frais` exécute
`docker compose run --rm -T runner <args>` :

```bash
eurl-frais monthly --dry-run                 # simulation : n'écrit rien, n'envoie rien
eurl-frais monthly                           # frais du mois précédent
eurl-frais monthly --month 2026-09           # mois explicite ; --payment-date 2026-10-07 si la date réelle diffère
eurl-frais monthly --skip-orange             # n'utilise que les PDF déjà déposés dans CHARGES/ORANGE
eurl-frais orange --month 2026-09 --debug    # factures Orange seules + captures dans state/orange/debug
eurl-frais quittance --month 2026-02 --payment-date 2026-03-06 --issued-on 2026-03-06 \
    --output 'LOYER/QUITTANCE DE LOYER FEVRIER - CORRIGEE.pdf'   # copie corrigée, jamais d'écrasement
```

Codes de sortie : `0` complet, `3` incomplet (facture Orange manquante ou quittance pas encore due), `1` erreur,
`2` configuration.

## Si Orange bloque

Code SMS, CAPTCHA ou validation sur mobile ne sont jamais contournés : le rapport part en **INCOMPLET** avec la
raison. Solutions : relancer `eurl-frais orange --month AAAA-MM --debug` en supervision, ou déposer les PDF de
factures dans `EURL/CHARGES/ORANGE/` (n'importe quel nom) puis `eurl-frais monthly --month AAAA-MM --skip-orange`.
Le mois de rattachement est la **date d'émission** de la facture (facture reçue en M = charge de M, remboursée en M+1).

## Tests

```bash
docker compose run --rm -T --entrypoint python runner -m pytest -q
```
