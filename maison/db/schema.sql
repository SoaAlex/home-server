CREATE TABLE IF NOT EXISTS listing (
    id               BIGSERIAL PRIMARY KEY,
    source           TEXT NOT NULL,
    external_id      TEXT NOT NULL,
    url              TEXT NOT NULL,
    title            TEXT,
    description      TEXT,
    price            INTEGER,
    surface_m2       NUMERIC,
    land_m2          NUMERIC,
    rooms            INTEGER,
    bedrooms         INTEGER,
    property_type    TEXT,
    city             TEXT,
    postal_code      TEXT,
    insee            TEXT,
    dpe              TEXT,
    ges              TEXT,
    dpe_value        INTEGER,
    dpe_date         DATE,
    year_built       INTEGER,
    has_garage       BOOLEAN,
    has_garden       BOOLEAN,
    has_bathtub      BOOLEAN,
    works            TEXT,
    agency           TEXT,
    lat              DOUBLE PRECISION,
    lon              DOUBLE PRECISION,
    geo_precision    TEXT,
    address          TEXT,
    photo_urls       JSONB NOT NULL DEFAULT '[]',
    photos           JSONB NOT NULL DEFAULT '[]',
    raw              JSONB,
    published_at     TIMESTAMPTZ,
    first_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
    status           TEXT NOT NULL DEFAULT 'active',
    price_history    JSONB NOT NULL DEFAULT '[]',
    commute          JSONB,
    enrichment       JSONB NOT NULL DEFAULT '{}',
    enrichment_status TEXT NOT NULL DEFAULT 'pending',
    enriched_at      TIMESTAMPTZ,
    score            INTEGER,
    score_details    JSONB,
    excluded_reason  TEXT,
    fingerprint      TEXT,
    alerted_at       TIMESTAMPTZ,
    user_status      TEXT,
    user_note        TEXT,
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS listing_score_idx ON listing (score DESC);
CREATE INDEX IF NOT EXISTS listing_fp_idx ON listing (fingerprint);
CREATE INDEX IF NOT EXISTS listing_enrich_idx ON listing (enrichment_status);

-- Like / dislike (indépendant de user_status = à visiter / visitée)
ALTER TABLE listing ADD COLUMN IF NOT EXISTS reaction TEXT;   -- like | dislike | NULL
ALTER TABLE listing ADD COLUMN IF NOT EXISTS seen_at TIMESTAMPTZ;   -- 1re ouverture de la fiche dans l'UI
UPDATE listing SET reaction = 'like', user_status = NULL WHERE user_status = 'favorite';
UPDATE listing SET reaction = 'dislike', user_status = NULL WHERE user_status = 'rejected';

-- Déduplication des annonces (change OpenSpec merge-duplicate-listings).
-- Une annonce secondaire pointe vers l'annonce principale de son groupe (groupes plats, jamais de chaîne) ;
-- `cluster` = vue matérialisée du groupe sur la principale (offres, prix retenu, champs complétés, photos).
ALTER TABLE listing ADD COLUMN IF NOT EXISTS merged_into BIGINT REFERENCES listing(id) ON DELETE SET NULL;
ALTER TABLE listing ADD COLUMN IF NOT EXISTS merged_at TIMESTAMPTZ;
ALTER TABLE listing ADD COLUMN IF NOT EXISTS merge_method TEXT;     -- auto | user
ALTER TABLE listing ADD COLUMN IF NOT EXISTS cluster JSONB;
ALTER TABLE listing ADD COLUMN IF NOT EXISTS dedupe_sig TEXT;       -- empreinte des champs comparés au dernier passage
CREATE INDEX IF NOT EXISTS listing_merged_idx ON listing (merged_into) WHERE merged_into IS NOT NULL;

-- Empreintes visuelles des photos locales (pHash/dHash 64 bits stockés en BIGINT signé).
CREATE TABLE IF NOT EXISTS listing_photo (
    listing_id BIGINT NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    path       TEXT NOT NULL,
    phash      BIGINT NOT NULL,
    dhash      BIGINT NOT NULL,
    std        REAL NOT NULL,
    file_size  BIGINT,
    file_mtime DOUBLE PRECISION,
    generic    BOOLEAN NOT NULL DEFAULT false,
    hashed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, idx)
);

-- Paires de doublons : preuves, suggestions (« doublon possible »), fusions et refus mémorisés.
CREATE TABLE IF NOT EXISTS duplicate_pair (
    id_a       BIGINT NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
    id_b       BIGINT NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
    status     TEXT NOT NULL,              -- suggested | merged | rejected
    decided_by TEXT,                       -- auto | user
    evidence   JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    PRIMARY KEY (id_a, id_b),
    CHECK (id_a < id_b)
);
CREATE INDEX IF NOT EXISTS duplicate_pair_b_idx ON duplicate_pair (id_b);
CREATE INDEX IF NOT EXISTS duplicate_pair_status_idx ON duplicate_pair (status);

-- Mitoyenneté (change OpenSpec improve-adjacency-detection).
-- `adjacency` = calcul cartographique (BD TOPO, BAN PLUS, cadastre) ; `property_type_src` = provenance du type
-- déclaré par l'annonce : structured (champ de la source) | text_explicit | text_weak.
ALTER TABLE listing ADD COLUMN IF NOT EXISTS adjacency JSONB;
ALTER TABLE listing ADD COLUMN IF NOT EXISTS property_type_src TEXT;
-- File des vérifications de type par l'agent (enrichment.type_request), hors enrichment_status.
CREATE INDEX IF NOT EXISTS listing_type_request_idx ON listing ((enrichment ? 'type_request'))
    WHERE enrichment ? 'type_request';
