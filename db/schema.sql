PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS locations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL UNIQUE,
    address        TEXT,
    lat            REAL,
    lon            REAL,
    location_type  TEXT    NOT NULL,
    rating         REAL,
    review_count   INTEGER,
    website_url    TEXT,
    phone          TEXT,
    is_top_pick    INTEGER DEFAULT 0,
    notes          TEXT,
    extra_json     TEXT    DEFAULT '{}',
    last_scraped   TEXT,
    scraped_ok     INTEGER
);

CREATE TABLE IF NOT EXISTS units (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id      INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    floor_plan_name  TEXT    NOT NULL,
    bed              INTEGER,
    bath             REAL,
    sqft_min         INTEGER,
    sqft_max         INTEGER,
    price_min        INTEGER,
    price_max        INTEGER,
    available        INTEGER DEFAULT 1,
    move_in_date     TEXT,
    unit_number      TEXT,
    is_manual        INTEGER DEFAULT 0,
    scraped_at       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_units_location ON units(location_id, scraped_at DESC);

CREATE TABLE IF NOT EXISTS scrape_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id  INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    scraped_at   TEXT    NOT NULL,
    success      INTEGER,
    error_msg    TEXT
);

CREATE TABLE IF NOT EXISTS change_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id  INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    changed_at   TEXT    NOT NULL,
    field        TEXT    NOT NULL,
    old_value    TEXT,
    new_value    TEXT
);

CREATE INDEX IF NOT EXISTS idx_change_log_location ON change_log(location_id, changed_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    visit_date  TEXT NOT NULL,
    impression  INTEGER,
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_visits_location ON visits(location_id, visit_date DESC);

CREATE TABLE IF NOT EXISTS discoveries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL,
    address        TEXT,
    lat            REAL,
    lon            REAL,
    location_type  TEXT    NOT NULL,
    source         TEXT,
    source_id      TEXT,
    data_json      TEXT    DEFAULT '{}',
    discovered_at  TEXT    NOT NULL,
    status         TEXT    DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    is_active   INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_constraints (
    profile_id                 INTEGER PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    max_true_monthly           INTEGER,
    max_expected_commute_mins  INTEGER,
    min_bedrooms               INTEGER,
    required_subtypes_json     TEXT DEFAULT '[]',
    required_amenities_json    TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS profile_weights (
    profile_id       INTEGER PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    affordability_w  REAL DEFAULT 0.35,
    commute_w        REAL DEFAULT 0.20,
    type_fit_w       REAL DEFAULT 0.10,
    space_w          REAL DEFAULT 0.10,
    amenities_w      REAL DEFAULT 0.10,
    proximity_w      REAL DEFAULT 0.10,
    quality_w        REAL DEFAULT 0.05
);

CREATE TABLE IF NOT EXISTS profile_commute_scenarios (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id   INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    lat          REAL NOT NULL,
    lon          REAL NOT NULL,
    probability  REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS score_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    apartment_id        INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    profile_id          INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    score               INTEGER NOT NULL,
    tier                TEXT NOT NULL,
    confidence          TEXT,
    eligibility_passed  INTEGER DEFAULT 1,
    breakdown_json      TEXT DEFAULT '{}',
    reasons_json        TEXT DEFAULT '[]',
    computed_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_score_runs_apartment_profile ON score_runs(apartment_id, profile_id, computed_at DESC);

