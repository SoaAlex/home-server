# Spec Delta

## Purpose

Décrit en Terraform la configuration publique du NAS (enregistrements DNS Cloudflare de `soaalex.com` et proxy hosts Nginx Proxy Manager) pour la recréer à l'identique et détecter toute modification faite à la main.

## ADDED Requirements

### Requirement: DNS Cloudflare décrit en code
Les enregistrements DNS de la zone `soaalex.com` utilisés par le NAS SHALL être décrits en Terraform et importés depuis l'existant, de sorte qu'un `plan` sur la zone actuelle ne propose aucun changement.

#### Scenario: Après import
- **WHEN** `terraform plan` est lancé après l'import initial
- **THEN** il rapporte « No changes »

#### Scenario: Modification manuelle dans Cloudflare
- **WHEN** un enregistrement géré est modifié dans l'interface Cloudflare
- **THEN** le `plan` suivant affiche l'écart et propose de revenir à la valeur du repo

### Requirement: Adresse IP dynamique non gérée
Pour les enregistrements tenus à jour par la DDNS, Terraform ne SHALL pas gérer l'adresse IP : un changement d'IP publique ne SHALL produire aucun écart dans le `plan`.

#### Scenario: Changement d'IP publique
- **WHEN** la DDNS a mis à jour l'adresse d'un enregistrement
- **THEN** `terraform plan` ne propose aucun changement pour cet enregistrement

### Requirement: Proxy hosts NPM décrits en code
Chaque proxy host de Nginx Proxy Manager SHALL être décrit en Terraform avec son domaine, sa cible, ses options (WebSockets, SSL forcé, HTTP/2) et sa configuration avancée, et importé depuis l'existant pour un premier `plan` sans changement.

#### Scenario: Proxy host du tunnel VPN
- **WHEN** l'état Terraform est appliqué sur un NPM vide
- **THEN** le proxy host `vpn.soaalex.com` vers le port 8480 est recréé avec WebSockets actifs et les délais `proxy_read_timeout 1d` et `proxy_send_timeout 1d`

### Requirement: Certificats des proxy hosts
Chaque proxy host servi en HTTPS SHALL obtenir un certificat Let's Encrypt après sa recréation, soit géré par Terraform, soit restauré avec les données NPM, sans étape manuelle dans l'interface NPM.

#### Scenario: NPM recréé à vide
- **WHEN** les proxy hosts sont recréés sur un NPM sans certificats et le DNS pointe vers le NAS
- **THEN** chaque hôte HTTPS répond avec un certificat valide pour son domaine

### Requirement: État et identifiants protégés
L'état Terraform et les identifiants des API (token Cloudflare, compte admin NPM) ne SHALL jamais être suivis en clair par git. Les identifiants SHALL provenir du vault des secrets, et l'état SHALL être stocké sur le NAS, accessible depuis le Mac comme depuis le NAS, et couvert par la sauvegarde nocturne.

#### Scenario: Statut git après apply
- **WHEN** `terraform apply` a été exécuté dans le repo
- **THEN** `git status` ne montre ni fichier d'état, ni dossier `.terraform/`, ni fichier de variables contenant un secret

### Requirement: Token Cloudflare au moindre privilège
Le token Cloudflare utilisé SHALL être limité à la lecture et à l'écriture DNS de la zone `soaalex.com`.

#### Scenario: Ressource hors périmètre
- **WHEN** Terraform tente de lire ou modifier une ressource Cloudflare hors DNS de la zone
- **THEN** l'API refuse l'accès
