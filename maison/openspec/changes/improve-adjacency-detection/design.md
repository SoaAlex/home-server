# Design

## Context

Les motivations sont dans proposal.md. Les comportements attendus sont dans les quatre specs : `building-adjacency`, `property-type-resolution`, `photo-adjacency-review` et `listing-map-views`.

**État actuel**, lu dans le code et la base le 2026-11-01 :

- **Type à l'ingestion** : `listing.property_type` est rempli par `textparse.detect_type(texte)`. Pour Netty, `TYPES.get(type_house) or detect_type(texte)` est utilisé (01 individuelle, 02 semi, 03 mitoyenne). `upsert` garde la dernière valeur non nulle.
  - La règle `independante` vaut individuelle, et « non mitoyenne » n'est testée qu'après les motifs semi.
- **Agent** : il écrit `enrichment.property_type`. `scoring.OVERRIDABLE` fait primer l'enrichissement sur la colonne, dans `effective()` (Python) comme dans `eff()` (JS).
  - Barème du critère « type » : 12 / 7 / 2.
  - Libellés dans `TYPES` (`app.js`), boutons de filtre dans `index.html`.
  - L'API d'enrichissement ne valide rien : elle fusionne `enrichment || body`.
- **Groupes fusionnés** : `dedupe.FILL_FIELDS` contient `property_type`, mais `unknown` n'est pas `NULL`. Le complément depuis une secondaire n'a donc jamais lieu.
- **Collecteur** : `run_once` enchaîne `upsert`, `postprocess` (seulement pour les annonces renvoyées par une source pendant le cycle), `dedupe.process` puis `rescore`. Les annonces modifiées par l'agent ou par l'utilisateur passent par `postprocess` depuis le web, mais une carte e-mail ancienne n'est jamais revue par le collecteur.
- **Outils de l'agent** :
  - relais `/api/tools/{ademe,ban,reverse,cadastre}`, protégés par jeton ;
  - CLI `scripts/maison_cli.py` en urllib, avec des copies `/workspace/bin/maison` (sandbox) et `~/.local/bin/maison` (backend local) ;
  - les investigations d'adresse ont leur propre job, lequel lit `pending --investigations` (filtre côté client sur `address_request`) ;
  - `vision_analyze` lit les chemins locaux du dossier scratch, pas `/volume1/docker` : la CLI doit donc télécharger les images.
- **Base au 2026-11-01 (après les fusions de doublons)** :
  - 1 476 annonces principales actives, dont 951 non exclues ;
  - parmi les non exclues, 449 ont une adresse exacte ou probable, et 411 un garage confirmé ;
  - 323 non exclues n'ont qu'une vignette (cartes e-mail).

**Spike** (lecture seule, conteneur jetable `python:3.12-slim` + shapely, 626 annonces à adresse `exact` ou `probable` ; scripts et résultats dans `maison/.scratch/adjacency-spike/`, ignoré par git) :

- **Services** : WFS Géoplateforme `BDTOPO_V3:batiment`, `CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle` et `BAN-PLUS:lien_adresse_bati`.
  - Attributs utiles de la BD TOPO : `usage_1` (Résidentiel 56 %, Indifférencié 32 %, Annexe 10 %), `construction_legere`, `hauteur`, `nombre_de_logements`, `nombre_d_etages`, `date_d_apparition`, `identifiants_rnb`.
  - BAN PLUS relie l'identifiant BAN de l'adresse (de la forme `<insee>_<voie>_<numéro>`) au bâtiment BD TOPO (`id_bat`) et à la parcelle (`idu`). Le filtre `CQL_FILTER=id_adr='…'` fonctionne.
  - Les réponses GeoJSON sont en lon/lat ; la `BBOX` en EPSG:4326 de WFS 2.0 est en lat/lon.
- **Couverture** :
  - identifiant BAN trouvé pour 619 annonces sur 626 (point BAN à moins de 1 m du point stocké pour 593) ;
  - bâtiment trouvé par lien BAN PLUS pour 598, par la parcelle pour 17 et par proximité pour 8 ; introuvable pour 3.
- **Coût** : environ 1,7 s par requête WFS, 4 requêtes par annonce. Les 626 annonces ont été traitées en 18 min avec 4 requêtes en parallèle. Une seule erreur 504 sur 2 290 requêtes.
- **Tolérance** : 0,5 m ou 1,0 m donnent les mêmes résultats, car les murs communs partagent leurs sommets dans la BD TOPO. Les polygones sont décalés de 1 à 3 m par rapport à l'orthophoto (`precision_planimetrique` 3 m) : sans effet sur la topologie, visible sur les tracés.
- **Classement retenu** (version 3 du spike, règles de D3) : `terraced` 395 (63 %), `semi` 149 (24 %), `link_detached` 23 (4 %), `detached` 56 (9 %).
- **Accord** avec :
  - les verdicts actuels de l'agent : 77 % (391 annonces) ;
  - le mot « individuelle » : 71 % ;
  - un texte semi explicite : 74 % ;
  - Netty : 63 %.

  Un comptage des seuls voisins par parcelle, sans distinguer les annexes, donnait 72 %.
- **Désaccords examinés** (vue aérienne annotée + photos) :
  - #15 : rangée classée individuelle par l'agent, à cause de « indépendante » dans le texte et d'aucune photo de façade. La carte avait raison.
  - #259616 (Lomme, années 1970, « semi-mitoyenne avec garage ») : les garages sont dessinés dans le polygone des maisons, et la carte donne `terraced`. Seules les images peuvent trancher.
  - #108 : un côté touche le voisin par une annexe étroite de la propriété, ce qui donne `semi` pour la carte et mitoyenne pour l'agent.
  - #52425 : un abri touche un abri. Il faut l'ignorer, ce que fait la version 3.
- **Panoramax** : pour #15, une photo 360° MEL du 2026-09-22 existe à 6,8 m.
  - Image HD de 12288×6144 px (7,6 Mo), téléchargée en 0,2 s, décodée à demi-résolution en 0,6 s, projetée avec numpy en 0,3 s.
  - La vue montre la façade, les deux voisines et le numéro.
  - Pour #236721 (Wasquehal), aucune photo 360° à moins de 30 m : la couverture a des trous.
- **Planche** : 10 photos de 240×180 px, en grille de 4 de large, se lisent en un appel de vision. Le tri extérieur/intérieur est fiable (#15 n'a aucune photo de façade).

## Goals / Non-Goals

**Goals:**
- Un calcul géométrique pur et testable hors réseau : une fonction sur des entités GeoJSON. Le réseau reste isolé dans une couche de récupération.
- Aucune latence ajoutée au POST de l'agent ni à la liste. Le coût du collecteur est borné.
- Un seul endroit où le type est arbitré, côté serveur. Le JS affiche le résultat sans recalculer.
- La pile actuelle (FastAPI, psycopg, httpx, Pillow, numpy) plus shapely, sans nouveau service.

**Non-Goals:**
- Pas de cache des réponses WFS brutes : seul le résultat est conservé.
- Pas de classification automatique des photos dans l'application : la vision reste le travail de l'agent.
- Pas de correction du placement des polygones BD TOPO sur l'orthophoto (décalage de 1 à 3 m accepté et signalé dans la légende).
- Pas de changement de la recherche d'adresse elle-même : seul un outil de contrôle est ajouté.

## Decisions

### D1 — Données : BD TOPO + BAN PLUS + Parcellaire Express par WFS Géoplateforme
Chaque calcul envoie quatre requêtes, chacune avec un délai de 20 s et deux nouvelles tentatives espacées :
1. BAN `/search` sur le libellé de l'adresse, biaisée par lat/lon, pour obtenir l'identifiant `housenumber` (score > 0,5, à moins de 30 m du point stocké, sinon avertissement `ban_mismatch`) ;
2. `BAN-PLUS:lien_adresse_bati`, filtrée par `CQL_FILTER=id_adr='<id>'` ;
3. `BDTOPO_V3:batiment` dans une boîte de ±60 m ;
4. `CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle` dans la même boîte.

Le client HTTP est partagé avec le reste du collecteur, mais ces requêtes portent un User-Agent explicite et en ASCII (`maison/1.0 (…)`) : celui du collecteur imite un navigateur et n'identifie pas le système.

Alternatives écartées :
- (a) Cadastre Etalab en fichiers par commune (`batiments.json`) : il faut les stocker et les rafraîchir, ils n'ont ni usage ni hauteur pour reconnaître une annexe, ni lien avec l'adresse.
- (b) OSM par Overpass : bâti importé du cadastre, mais service public bridé et non officiel.
- (c) API RNB : elle a les formes et les parcelles, mais ni l'usage ni la hauteur.
- (d) BDNB (`surface_facade_mitoyenne`) : surface agrégée par groupe de bâtiments, sans détail par voisin ni par côté, et tables détaillées non ouvertes.
- (e) Google : CGU (voir proposal.md).

### D2 — Module `app/adjacency.py` : récupération séparée du calcul
- `fetch(client, point, address) -> Context` émet les requêtes de D1.
- `analyse(ctx) -> dict` est pure : projection locale équirectangulaire au point (erreur inférieure à 0,1 % à 100 m, sans pyproj), puis shapely 2.x.

Les tests appellent `analyse` sur des fixtures GeoJSON anonymisées (D16).

Alternative écartée : une géométrie écrite à la main (proximité de segments). Elle ferait plus de code et plus de cas limites, alors que la roue de shapely (GEOS inclus) pèse environ 2,5 Mo.

### D3 — Règles géométriques
- **Bâtiment cible** : lien BAN PLUS, puis plus grand bâtiment en dur de la parcelle qui contient le point (avertissement `fallback_target`), puis bâtiment en dur de plus de 30 m² à moins de 6 m (même avertissement). Seuls les objets `etat_de_l_objet = 'En service'` comptent.
- **Propriété** : bâtiments dont le point représentatif est dans la parcelle cible (`idu` du lien, sinon parcelle qui contient le point), plus le bâtiment cible.
- **Volume** : un volume est une **annexe** dans l'un des cas suivants :
  - `construction_legere` ;
  - `usage_1 = 'Annexe'` ;
  - surface inférieure à 15 m² ;
  - hauteur inférieure à 3,0 m ;
  - `usage_1 = 'Indifférencié'` avec une hauteur inférieure à 3,5 m ou une surface inférieure à 25 m².

  Sinon, c'est un volume **principal**. Les seuils viennent du spike et sont des constantes, recalibrées sur le rapport à blanc.
- **Contact** :
  - un bâtiment hors propriété à moins de 0,5 m de la propriété est candidat ;
  - la longueur de contact est celle de la frontière de la propriété contenue dans `voisin.buffer(0,5)`, moins 0,5 m à chaque extrémité de tronçon (la zone englobe ce bout de façade : sans correction, un angle commun de 1 m mesurait 2 m). Elle doit atteindre au moins 2 m ;
  - les voisins sont regroupés par parcelle (point représentatif).
- **Nature d'un contact** :
  - mur habitable : volume principal de la propriété contre volume principal voisin ;
  - liaison par annexe : principal contre annexe, ou annexe de la propriété contre principal voisin ;
  - annexe contre annexe : ignoré.
- **Catégorie** : telle que décrite dans la spec `building-adjacency`.
- **Côté vu de la rue** : il permet de comparer directement la carte aux photos, et l'orientation est marquée comme estimée.
  - **Façade** : direction du centroïde de la cible vers le point de la limite de parcelle sur la voie publique (partie de la limite qu'aucune autre parcelle ne touche, la voirie n'étant pas cadastrée) le plus proche du point BAN, qui départage les parcelles d'angle. À défaut, direction du centroïde vers le point BAN.
  - **Côté d'un mur** : un mur orienté comme la façade (de la rue vers le fond, à 45° près) est à gauche ou à droite selon sa position ; sinon il est à l'avant ou à l'arrière.
  - Sur le spike, 92 % des rangées ont ainsi un mur à gauche et un à droite, et 95 % des semis ont un mur latéral. Avec l'azimut du centre du mur vers le point BAN, prévu au départ, les taux n'étaient que de 67 % et 72 %.
- **Avertissements bloquants** : `multi_dwelling` (`nombre_de_logements > 1`), `large_footprint` (emprise de plus de 300 m²) et `no_building`.
- **Avertissements non bloquants** : `fallback_target`, `ban_mismatch` et `probable_address`.

### D4 — Stockage dans `listing.adjacency JSONB`
Le résultat n'est pas stocké dans `enrichment`, pour trois raisons : ce champ appartient à l'agent, il est fusionné par `||`, et l'agent le recopierait.

Exemple de contenu :

```json
{"v": 1, "status": "ok", "computed_at": "…", "input": {"address": "…", "lat": 50.6, "lon": 3.1,
  "confidence": "exact|user|source|probable", "ban_id": "…"},
 "type": "semi", "usable": true, "warnings": [],
 "target": {"method": "link", "bdtopo_id": "…", "rnb_id": "…", "parcel": "…", "parcel_m2": 251,
            "area_m2": 51, "height_m": 5.3, "floors": 2, "dwellings": 1, "year": 1931},
 "neighbours": [{"parcel": "…", "kind": "living|annex_link", "side": "left|right|rear|front", "bearing": 41,
                 "length_m": 8.0, "ours": "main", "theirs": "main", "their_height_m": 5.5}],
 "living_walls": 1, "annex_links": 0, "type_request_created": false,
 "geometry": {"type": "FeatureCollection", "features": ["parcel|target|property|neighbour_main|neighbour_annex|shared_wall"]}}
```

- `status` vaut `ok`, `no_building`, `not_applicable` ou `error` (avec `error` et `tried_at`).
- `geometry` est en WGS84, arrondie à 1e-6, soit 2 à 5 Ko. Les requêtes de liste lisent `adjacency - 'geometry'`.
- **Recalcul** : quand la signature d'entrée change (adresse, lat/lon arrondis, confiance), ou quand un `error` date de plus de 6 h.

Une colonne `property_type_src` (`structured`, `text_explicit` ou `text_weak`) accompagne `property_type`. Elle est renseignée à l'ingestion (D7).

### D5 — Étape du collecteur, hors chemin web
`adjacency.process(conn, client, limit=40)` est appelée dans `run_once`, après `dedupe.process` et avant `rescore`. Elle sélectionne les annonces suivantes :
- annonce principale et active (`merged_into IS NULL`, bien actif) ;
- position effective (`merged_columns`, qui inclut celle d'une secondaire) avec `geo_precision = 'exact'` et une adresse ;
- confiance `exact`, `probable`, `user`, ou adresse de la source géocodée au numéro (PAP, notaires : confiance `source`) ;
- ni `none` ni `street` ;
- résultat absent, signature d'entrée changée, ou erreur de plus de 6 h.

Le tri se fait par `enriched_at` décroissant, puis `first_seen` décroissant. Chaque annonce est validée (commit) séparément, et un échec n'arrête pas l'étape.

Le POST de l'agent et la correction d'adresse ne calculent rien : ils restent rapides, et le résultat arrive au cycle suivant. Quand l'agent a besoin du résultat tout de suite, il appelle l'outil « point quelconque » (D8).

Alternative écartée : un calcul dans `postprocess`. Cette fonction ne voit que les annonces renvoyées par une source pendant le cycle : une carte e-mail dont l'agent a trouvé l'adresse ne serait jamais calculée. Le calcul ajouterait aussi environ 7 s au POST de l'agent.

### D6 — Arbitrage côté serveur : `scoring.resolve_type(row) -> type_info`
Les verdicts sont collectés ainsi :

| Source | Origine du verdict |
|---|---|
| utilisateur | `enrichment.property_type_user` (nouvelle clé, écrite seulement par la route de l'UI, retirée des corps envoyés par l'agent) |
| agent (nouveau protocole) | `enrichment.property_type` accompagné de `enrichment.type_evidence` |
| agent (ancien protocole) | `enrichment.property_type` sans preuves |
| carte | `adjacency.type` si `usable`, rang selon `input.confidence` (`exact`, `user` ou `source` contre `probable`) |
| annonce | colonne `property_type` avec `property_type_src` ; à défaut, `cluster.fill.property_type` |

- L'ordre, les désaccords et les confiances sont ceux de la spec `property-type-resolution`. `unknown` est ignoré.
- `effective()` renvoie `property_type = type_info.type`, et `property_type` sort de `OVERRIDABLE`.
- `/api/listings`, `/api/listings/{id}`, `/api/enrichment/pending` et la file des vérifications exposent `type_info` : type, source, confiance, désaccord, verdicts et preuves courtes.
- `eff(l, "property_type")` lit `l.type_info.type`.

Pour un bien fusionné, `dedupe.build_cluster` traite `unknown` comme vide pour `property_type`. Il range dans `cluster.fill` le meilleur verdict des secondaires (agent avec preuves, puis agent, puis annonce structurée, puis texte), avec sa provenance.

Alternative écartée : dupliquer l'arbitrage en JS. Les deux versions finiraient par diverger, comme le note déjà le commentaire sur `eff()`.

### D7 — Détection dans le texte : règles ordonnées, avec une force
`detect_type(texte) -> (type, force, expression)` teste les motifs dans l'ordre suivant ; le premier qui correspond gagne :
1. garage → `link_detached` explicite : `(mitoyenne?|jumelee?|accolee?|reliee?)s? (uniquement |seulement )?par (le|les|un|son|leurs?) garages?` ;
2. semi → `semi` explicite : `semi[- ]?(individuelle|mitoyenne)`, `mitoyenne? d.un (seul )?cote`, `jumelee` ;
3. mitoyenne → `terraced` explicite : `mitoyenne? (des deux|de chaque|des 2) cotes`, `en bande` ;
4. individuelle → `detached` explicite : `non mitoyenne|sans mitoyennete|aucune mitoyennete|(4|quatre) facades|\bindividuelle\b` ;
5. faible : `mitoyenne|maison de ville|bel[- ]etage` → `terraced`.

« Individuelle » n'est pas retenu si le texte dit aussi « mitoyenne » : « maison individuelle mitoyenne » désigne une maison unifamiliale accolée.

Les mots `independante` et `villa` sont retirés. Contrôle sur les annonces du spike à adresse connue : aucune des 30 « villa » n'est isolée (23 mitoyennes). Par comparaison, « individuelle » est isolée dans 36 cas sur 50, et « maison de ville » mitoyenne dans 49 cas sur 57. Netty range `type_house` dans `property_type` avec `property_type_src = 'structured'`. Les autres sources rangent le verdict du texte. Les sources balayées à chaque cycle se recalculent d'elles-mêmes, et le rattrapage (D12) recalcule les autres.

Les tests reprennent les phrases réelles relevées dans le spike, anonymisées.

### D8 — Outils de l'agent et routes
| Route | Auth | Usage |
|---|---|---|
| `GET /api/tools/adjacency?address=…` ou `?lon=&lat=` | jeton | analyse d'un point sans enregistrement (adresses candidates) |
| `GET /api/tools/aerial?listing_id=…` ou `?lon=&lat=` | jeton | vue aérienne annotée (D10) |
| `GET /api/listings/{id}/contact_sheet.jpg` | jeton | planche contact (D11) |
| `GET /api/listings/{id}/street` et `/street.jpg` | aucune (comme `/photos`) | vue de rue (D9), pour l'UI et l'agent |
| `GET /api/enrichment/type_checks?limit=` | jeton | file des vérifications (D13) |
| `POST /api/listings/{id}/property_type` | aucune (UI, comme `/address`) | correction manuelle `{type | null}` |
| `POST /api/listings/{id}/type_request` | aucune (UI) | demande manuelle `{note}` |

La CLI reste en urllib seulement. Nouvelles commandes :
- `maison pending --types N`, avec une copie dans `/tmp/pending_types.json` ;
- `maison mitoyennete ID` : résumé et vue aérienne ;
- `maison mitoyennete-adresse 'ADRESSE'` ou `LON LAT` ;
- `maison planche ID` ;
- `maison rue ID`.

Les images sont écrites dans `$TMPDIR` (dossier scratch en backend local), sinon dans `/tmp`, et la commande affiche leur chemin absolu pour `vision_analyze`. Aucun jeton n'apparaît dans une URL : il passe dans l'en-tête `X-Token`, comme aujourd'hui.

### D9 — Vue de rue : `app/streetview.py` (Panoramax)
- **Recherche** : `https://api.panoramax.xyz/api/search?place_position=lon,lat&place_distance=0-30&limit=20`. La cible est le centroïde du bâtiment retenu, sinon le point de l'adresse.
- **Choix de la photo** :
  - les photos 360° (`field_of_view = 360`), ou les photos planes dont l'axe ± la moitié du champ contient l'azimut de la cible ;
  - parmi celles qui sont à moins de 1,5 fois la distance minimale, la plus récente.
- **Rendu** :
  - téléchargement de `assets.hd.href` en suivant les redirections ;
  - décodage Pillow `draft` à demi-résolution, soit environ 57 Mo transitoires pour une image 360° ;
  - projection équirectangulaire vers une vue plane avec numpy (plus proche voisin, champ de 90°, 1200×800 px, tangage +3°). Une photo plane est seulement recadrée et réduite.
- **Cache** : `data/streetview/<id>.jpg` et `<id>.json` (identifiant de photo, date, distance, auteur ou producteur, licence, lien vers la visionneuse). La clé est la position arrondie. Le cache est invalidé si la position change ou après 180 jours.

Le téléchargement HD n'a lieu qu'à l'ouverture d'une fiche ou à l'appel de l'agent, jamais en masse.

Alternative écartée : laisser l'agent recadrer avec ffmpeg (recette des références de la skill). C'est fragile en cron (azimuts, redirection S3), et l'UI ne pourrait pas en profiter.

### D10 — Vue aérienne annotée
Requête WMS `ORTHOIMAGERY.ORTHOPHOTOS` sur 70×70 m en 700×700 px, puis tracé avec Pillow à partir de `adjacency.geometry` :
- parcelle en blanc ;
- cible en bleu ;
- reste de la propriété en cyan ;
- voisin principal en rouge, voisin annexe en orange ;
- murs communs en magenta épais.

La légende indique « tracés ±3 m ». L'image est mise en cache dans `data/aerial/<id>.jpg`, avec comme clé `computed_at`. Pour un point quelconque, elle n'est pas mise en cache.

### D11 — Planche contact
Pillow assemble au plus 12 photos (celles du bien fusionné, `cluster.photos` sinon `photos`) en vignettes de 240×180 px, sur 4 colonnes, soit 960 px de large. Chaque vignette porte son numéro dans l'ordre de l'annonce, pour que l'agent cite les photos par leur indice. L'image est générée à la demande, sans cache, en environ 20 ms par photo grâce à `draft`.

### D12 — Rattrapage : `scripts/adjacency_backfill.py`
- **Par défaut, à blanc** :
  - calcul de toutes les annonces éligibles (D5), avec 4 requêtes en parallèle au plus ;
  - verdicts « annonce » recalculés avec D7 ;
  - écriture d'un rapport HTML autonome dans `data/reports/adjacency-*.html`.
- **Contenu du rapport** :
  - effectifs par catégorie et par avertissement ;
  - confusion entre le type actuel et le type arbitré ;
  - variations de score et annonces qui franchissent le seuil d'alerte de 75 ;
  - pour les 40 désaccords au plus fort score : vue aérienne annotée et première photo, en base64.
- **`--apply`** :
  - écrit `adjacency`, ainsi que `property_type`/`property_type_src` pour les sources non balayées ;
  - lance `rescore` avec `DISABLE_ALERTS=1` ;
  - affiche le résumé des biens qui deviennent alertables.
- **`--request-checks`** : crée les demandes de vérification de la spec `photo-adjacency-review`, pour les annonces principales actives non exclues avec `has_garage` effectif vrai, sauf celles dont le type a été corrigé par l'utilisateur ou qui ont déjà une demande en attente.

C'est le même schéma que `dedupe_backfill.py`, qui a fait ses preuves.

### D13 — File des vérifications de type, hors `enrichment_status`
- **Demande** : `enrichment.type_request = {requested_at, reason: user|garage_backfill|conflict, note, adjacency_at}`.
- **File** : `GET /api/enrichment/type_checks` renvoie les annonces actives avec une demande, triées par score décroissant puis par `requested_at`, dans le même format allégé que `pending`, avec `type_info`, l'adjacence sans géométrie et les URLs des photos.
- **Réponse de l'agent** : il poste sur la route d'enrichissement existante avec `"_type_check": true`. L'API :
  - valide les champs du type (D14) ;
  - fusionne seulement `property_type`, `type_evidence` et les éventuels `red_flags` ;
  - retire `type_request` et l'ajoute à `enrichment.type_checks[]` ;
  - ne touche ni `enrichment_status` ni `enriched_at`.

  Un enrichissement standard qui contient `type_evidence` solde aussi une demande en attente.
- **Désaccords** : après un calcul (D5), si la carte (exacte) contredit l'agent sur une annonce avec garage confirmé, une demande `conflict` est créée une seule fois. `adjacency.type_request_created` évite la répétition.

Alternative écartée : le schéma `address_request`, qui repasse l'annonce en `pending` avec un filtre côté client. L'arriéré de 411 annonces inonderait `--standard` et déclencherait les alertes « en attente » du contrôle santé.

**Job Hermes `maison-type-check`** :
- créé avec `cronjob_manage`, à `40 8-22 * * *`, avec la skill `maison-enrichment` ;
- consigne : `maison pending --types 8`, section « Vérification du type » de la skill, un sous-agent par annonce (3 en parallèle au plus, environ 5 min chacun), POST avec `_type_check`, réponse `[SILENT]` si la file est vide.

À ce rythme (8 annonces par passage, 15 passages par jour), l'arriéré s'écoule en 4 jours environ.

### D14 — Validation côté API des champs du type
Avant toute écriture, un validateur contrôle :
- l'énumération de `property_type` ;
- le schéma de `type_evidence` ;
- la cohérence entre côtés et type (spec `photo-adjacency-review`).

En cas d'erreur, l'API répond 422 avec un message qui donne les valeurs attendues. Les autres clés ne sont pas validées dans ce change.

`property_type_user` est retiré des corps envoyés par l'agent. Le CLI affiche déjà le corps des erreurs HTTP, si bien que le sous-agent voit le message et corrige.

### D15 — Procédure de l'agent (skill `maison-enrichment`)
La skill reçoit une nouvelle section « Mitoyenneté », appliquée dans l'enrichissement standard et dans le job de vérification :
1. `maison planche ID` et un appel de vision : liste des vues extérieures (façade avant, arrière, côté, rue) ;
2. un à trois appels de vision sur les vues extérieures en pleine résolution (URLs https), avec la question par côté ;
3. si l'adresse est précise : lecture de `type_info` et de l'adjacence, puis `maison mitoyennete ID` pour la vue aérienne ;
4. sans façade visible : `maison rue ID` ;
5. conclusion selon la table de cohérence, et remplissage de `type_evidence` ;
6. jamais d'image Google.

Pièges à ajouter :
- un mur de clôture n'est pas un mur commun ;
- des fenêtres sur un pignon signifient que ce côté est libre ;
- un garage dans le même polygone que la maison trompe la carte ;
- photos retouchées par IA ;
- décalage de 1 à 3 m des tracés ;
- maison d'angle ;
- vue Panoramax plus ancienne qu'une extension.

Pour les investigations d'adresse, `maison mitoyennete-adresse` sur les candidates écarte celles dont le type contredit les photos. Le numéro lu sur la vue de rue confirme la numérotation, comme le décrivent les références existantes.

Surcoût : 3 à 5 appels de vision par annonce.

### D16 — Dépôt public : données réelles hors de git
Le dépôt `home-server` est public, et `.gitignore` exclut déjà `maison/tests/fixtures/`, `maison/data/` et `maison/.scratch/`.

- **Fixtures** : celles de mitoyenneté vont dans `tests/fixtures/adjacency/`, des réponses WFS réelles réduites à la boîte utile, et ne sont donc jamais versionnées.
- **Code et documentation versionnés** : le code de test, le README et les artefacts OpenSpec ne contiennent aucune adresse, aucune coordonnée réelle ni aucune photo. Les tests citent des rôles (« rangée », « garage du voisin ») et les artefacts seulement des identifiants d'annonce et des communes.
- **Rapports** : ils restent dans `data/reports`.
- **Spike** : ses scripts et résultats sont conservés dans `maison/.scratch/adjacency-spike/`, ignoré par git, pour le recalibrage.

### D17 — Interface
- **Mini-carte** :
  - contrôle de couches Leaflet : fond « Plan » (OSM, actuel) ou « Photo aérienne » (WMTS IGN `ORTHOIMAGERY.ORTHOPHOTOS`, `TILEMATRIXSET=PM`, zoom natif 19 étiré jusqu'à 21) ;
  - surcouche « Cadastre » (`CADASTRALPARCELS.PARCELLAIRE_EXPRESS`, style `PCI vecteur`, PNG) ;
  - choix mémorisé dans `localStorage` et mentions de source activées ;
  - couche GeoJSON de `adjacency.geometry`, reçue avec `/api/listings/{id}`, avec une légende.
- **Bloc « Localisation »** :
  - vue de rue (image, date, auteur, lien vers la visionneuse) ;
  - liens Google, qui utilisent les URL officielles « Maps URLs », sans clé : `https://www.google.com/maps/@?api=1&map_action=map&center={lat},{lon}&zoom=20&basemap=satellite` et `…&map_action=pano&viewpoint={lat},{lon}`.
- **Ligne « Type »** :
  - libellé, pastille de source et badge de désaccord ;
  - « Pourquoi ? », qui déplie les verdicts par source ;
  - menu de correction et bouton « Faire vérifier ».
- **Autres changements** :
  - filtre de type : Tous, Individuelle, Par garage, Semi, Autre ;
  - `TYPES` reçoit la valeur `link_detached`, libellée « Accolée par le garage » ;
  - champ « Type » dans l'embed Discord.

## Risks / Trade-offs

- [Garage dessiné dans le polygone de la maison : la carte dit semi ou mitoyenne alors que la maison n'est reliée que par le garage] → Le verdict de l'agent avec preuves passe avant la carte. Les annonces avec garage sont toutes re-vérifiées, et un badge signale le désaccord.
- [Adresse probable fausse, donc mauvais bâtiment] → Une carte à adresse probable passe après l'ancien verdict de l'agent, et l'avertissement `probable_address` est affiché. L'agent contrôle le numéro sur la vue de rue.
- [BD TOPO en retard sur une extension ou une démolition] → La vue aérienne et la vue de rue servent à l'agent, et l'utilisateur peut corriger.
- [Seuils d'annexe : une maison de plain-pied prise pour une annexe, ou un grand garage pour une maison] → Les critères sont combinés (usage, hauteur, surface), puis recalibrés sur le rapport à blanc avant `--apply`.
- [Géoplateforme lente ou en panne] → Le débit est borné par cycle, les essais sont espacés, la panne ne bloque pas le cycle, et le contrôle santé la signale.
- [Trous ou ancienneté de la couverture Panoramax] → La fiche affiche « aucune vue » et la date de la photo.
- [Coût de l'agent : 3 à 5 appels de vision de plus par annonce, arriéré de 411] → Au plus 8 annonces par passage, planche en un seul appel, et périmètre limité aux annonces avec garage (choix d'Alexandre).
- [Scores qui bougent à l'application, par exemple « cuisine indépendante » qui perd ses 10 points] → Le rapport à blanc liste les franchissements du seuil d'alerte. L'application se fait sans alerte, après validation du rapport par Alexandre.
- [Validation 422 qui fait échouer les envois des sous-agents pendant la transition] → La skill et les copies du CLI sont mises à jour avant le redéploiement, et le message d'erreur donne les valeurs attendues.
- [Charge mémoire de la projection 360° (environ 57 Mo transitoires)] → Une projection à la fois, protégée par un verrou de processus. Une seconde demande simultanée attend.

## Migration Plan

1. **Prérequis** : `merge-duplicate-listings` est terminé et validé (commit local), car les deux changes touchent les mêmes fichiers.
2. **Image** : `docker compose build`, avec `shapely` dans `requirements.txt`. Le schéma est additif : `adjacency JSONB`, `property_type_src TEXT`, et un index partiel sur `(enrichment ? 'type_request')`.
3. **Agent** : mettre à jour la skill et les copies du CLI (`/workspace/bin/maison`, `~/.local/bin/maison`) dans la même fenêtre que le redéploiement (`docker compose up -d`), avant le passage suivant de `maison-enrichment`.
4. **Rattrapage à blanc** : lancer `adjacency_backfill.py`, puis Alexandre valide le rapport et les seuils sont ajustés si besoin.
5. **Application** : `--apply`, puis `--request-checks`.
6. **Job** : créer `maison-type-check` (actif). Vérifier un premier passage manuel sur 2 ou 3 annonces avant de le laisser tourner.
7. **Suivi** : après l'écoulement de l'arriéré, relire un échantillon de désaccords entre carte et agent, et ajuster les constantes.

**Retour arrière** :
- revenir à l'image précédente ;
- `UPDATE listing SET adjacency = NULL` (colonnes laissées en place) ;
- `UPDATE listing SET enrichment = enrichment - 'type_request'` ;
- mettre le job en pause ;
- restaurer la skill depuis la copie de sauvegarde faite avant modification.

Les verdicts de l'agent restent dans `enrichment` : l'ancien comportement reprend sans perte.

## Open Questions

- **Seuils exacts** (annexe, contact minimal de 2 m, emprise de 300 m², 6 m pour le repli) : ils seront fixés sur le rapport à blanc, sans effet sur les specs.
- **Photo Panoramax** : choix entre la plus récente et la plus proche quand plusieurs conviennent. Réglage d'implémentation.
- **Attributs BD TOPO dans la fiche** (année, niveaux, logements) : on pourra les afficher sous le type si la ligne ne devient pas trop chargée. Choix de présentation seulement.
