# eurl-frais — frais mensuels de l'EURL

Conteneur CLI (aucun port publié) lancé le 10 de chaque mois par une tâche Hermes. Pour le mois de frais
**M** (par défaut le mois précédent), il :

1. télécharge les factures Orange (Internet + Mobile) émises en **M+1**, le mois courant du passage normal,
   depuis l'espace client (Playwright : chaque contrat, page « historique des factures ») et les archive dans
   `EURL/CHARGES/ORANGE/AAAA-MM-JJ_Orange_<internet|mobile>_<TTC>EUR.pdf` (ex. `2026-09-07_Orange_mobile_12,34EUR.pdf`).
   Date, service et montant viennent de l'espace client ; le montant doit aussi figurer dans le texte du PDF, sinon
   la facture est mise de côté dans `state/orange/a-verifier/` et non comptée. Jamais d'écrasement : une facture
   déjà archivée (même nom) est conservée telle quelle, Orange régénérant certains PDF à chaque téléchargement ;
2. émet la quittance du loyer de M (`EURL/LOYER/QUITTANCE DE LOYER <MOIS> <AAAA>.pdf`) en recopiant le modèle Word
   `LOYER/QUITTANCE DE LOYER.docx` (textes, gras/italique, filets, signature) ; seules la période, la date de paiement
   (par défaut le `RENT_PAYMENT_DAY` de M+1) et la date d'émission (jour du passage) changent ;
3. publie le rapport directement dans un message Discord (pas de PDF de rapport) : note « Charges <Mois> » =
   quote-part `ORANGE_SHARE` du TTC de chaque facture Orange (arrondie au centime) + forfaits gaz/électricité,
   note « Loyer <Mois> », **HT = TTC, TVA 0**, avec la quittance et les factures Orange en pièces jointes. Un mois
   complet n'est publié qu'une fois ; un mois incomplet est signalé (⚠️) et republié une fois complété.
4. publie juste après le rapport un message texte par commentaire Tiime, facile à copier :
   `INTERNET PRORATA 80/20` et `MOBILE PRORATA 80/20` (ratio calculé depuis `ORANGE_SHARE`).

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
eurl-frais monthly --month 2026-09           # loyer septembre + factures Orange émises en octobre
eurl-frais monthly --skip-orange             # n'utilise que les PDF déjà déposés dans CHARGES/ORANGE
eurl-frais orange --month 2026-09 --debug     # Orange d'octobre pour les frais de septembre + captures privées
eurl-frais quittance --month 2026-02 --payment-date 2026-03-06 --issued-on 2026-03-06 \
    --output 'LOYER/QUITTANCE DE LOYER FEVRIER - CORRIGEE.pdf'   # copie corrigée, jamais d'écrasement
```

Codes de sortie : `0` complet, `3` incomplet (facture Orange manquante ou quittance pas encore due), `1` erreur,
`2` configuration.

## Si Orange bloque

Code SMS, CAPTCHA ou validation sur mobile ne sont jamais contournés : le rapport part en **INCOMPLET** avec la
raison. Solutions : relancer `eurl-frais orange --month AAAA-MM --debug` en supervision, ou déposer les PDF de
factures dans `EURL/CHARGES/ORANGE/` puis `eurl-frais monthly --month AAAA-MM --skip-orange`. Le plus sûr est de
les nommer `AAAA-MM-JJ_Orange_<internet|mobile>_<TTC>EUR.pdf` (lu sans analyser le PDF) ; un autre nom est
analysé (texte du PDF) et signalé s'il est illisible. La session Orange est conservée dans `state/orange/`.
La sélection Orange se fait par **date d'émission en M+1**, tandis que la quittance et les titres
« Charges <Mois> » / « Loyer <Mois> » restent en M. Exemple : passage en octobre = quittance de septembre
et factures Orange d'octobre. Le 10 novembre = quittance d'octobre et factures Orange de novembre.
`--month` désigne toujours M, y compris pour la sous-commande `orange` et les relances historiques ;
aucune facture d'un autre mois n'est ajoutée au calcul. Les anciennes archives ne sont pas supprimées.
Pour republier un récapitulatif corrigé : `eurl-frais monthly --month AAAA-MM --resend`.

## Tests

```bash
docker compose run --rm -T --entrypoint python runner -m pytest -q
```
