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
