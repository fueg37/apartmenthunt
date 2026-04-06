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
