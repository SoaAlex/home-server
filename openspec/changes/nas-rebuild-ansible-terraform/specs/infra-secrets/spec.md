# Spec Delta

## Purpose

Garde tous les secrets nécessaires à la reconstruction (contenu des `.env`, identifiants des API externes) chiffrés dans le repo public, et les restitue sur le NAS sans jamais les exposer en clair.

## ADDED Requirements

### Requirement: Secrets chiffrés et versionnés
Les secrets de l'infrastructure SHALL être stockés dans un fichier Ansible Vault chiffré et versionné dans le repo. Aucun secret en clair ne SHALL être suivi par git.

#### Scenario: Fichier vault commité
- **WHEN** le fichier de secrets est ajouté au repo
- **THEN** son contenu commence par l'en-tête `$ANSIBLE_VAULT;` et aucune valeur de secret n'apparaît en clair

#### Scenario: Contrôle avant commit
- **WHEN** un fichier de secrets déchiffré est mis en staging par erreur
- **THEN** le contrôle fourni par le repo le détecte et refuse en nommant le fichier

### Requirement: Mot de passe du vault hors du repo
Le mot de passe du vault SHALL être fourni à l'exécution (saisie interactive ou fichier local git-ignoré en mode 600) et ne SHALL jamais être écrit dans le repo.

#### Scenario: Exécution sans mot de passe
- **WHEN** un playbook qui lit des secrets est lancé sans mot de passe de vault
- **THEN** il s'arrête avant toute modification avec un message qui indique comment le fournir

### Requirement: Génération des fichiers .env
Le playbook SHALL écrire chaque `.env` des stacks à partir du vault, avec le propriétaire `1000:10` et le mode 600, et les variables attendues par le compose de la stack.

#### Scenario: .env identique
- **WHEN** le `.env` sur le NAS a déjà le contenu issu du vault
- **THEN** le fichier n'est pas réécrit et aucun changement n'est rapporté

#### Scenario: Variable manquante
- **WHEN** le vault ne contient pas une variable listée comme requise pour une stack
- **THEN** l'exécution échoue avant d'écrire le fichier, en nommant la stack et la variable, sans afficher de valeur

### Requirement: Aucune fuite dans les sorties
Aucune tâche qui manipule un secret ne SHALL afficher sa valeur, y compris en mode `--diff`, en mode verbeux ou en cas d'échec.

#### Scenario: Diff d'un .env
- **WHEN** le playbook tourne en `--check --diff` et un `.env` diffère
- **THEN** la sortie indique que le fichier changerait sans montrer son contenu

### Requirement: Import initial depuis le NAS
Le repo SHALL fournir un outil qui construit le contenu du vault à partir des `.env` présents sur le NAS, sans afficher les valeurs, et qui signale les variables présentes dans un `.env` mais absentes du `.env.example` correspondant.

#### Scenario: Import
- **WHEN** l'outil d'import est lancé sur le NAS actuel
- **THEN** il produit un fichier vault chiffré qui contient tous les `.env` des stacks, et sa sortie ne contient que des noms de fichiers et de variables

#### Scenario: Aller-retour
- **WHEN** le playbook génère les `.env` depuis le vault importé
- **THEN** chaque `.env` généré est identique octet pour octet au fichier d'origine
