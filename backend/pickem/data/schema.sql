-- Local store for CS2 match history, brackets, and derived ratings.
-- Mirrors the subset of PandaScore fields the predictor needs.

CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    acronym     TEXT,
    slug        TEXT,
    location    TEXT,
    image_url   TEXT,
    modified_at TEXT
);

CREATE TABLE IF NOT EXISTS tournaments (
    id         INTEGER PRIMARY KEY,
    name       TEXT,
    slug       TEXT,
    tier       TEXT,
    region     TEXT,
    serie_id   INTEGER,
    league_id  INTEGER,
    league_name TEXT,
    begin_at   TEXT,
    end_at     TEXT,
    has_bracket INTEGER
);

CREATE TABLE IF NOT EXISTS matches (
    id            INTEGER PRIMARY KEY,
    tournament_id INTEGER,
    name          TEXT,
    slug          TEXT,
    match_type    TEXT,          -- e.g. best_of
    n_games       INTEGER,       -- number_of_games (1 => Bo1, 3 => Bo3)
    status        TEXT,
    scheduled_at  TEXT,
    begin_at      TEXT,
    end_at        TEXT,
    team_a_id     INTEGER,
    team_b_id     INTEGER,
    score_a       INTEGER,
    score_b       INTEGER,
    winner_id     INTEGER,
    modified_at   TEXT,
    FOREIGN KEY (tournament_id) REFERENCES tournaments(id),
    FOREIGN KEY (team_a_id)     REFERENCES teams(id),
    FOREIGN KEY (team_b_id)     REFERENCES teams(id)
);

CREATE INDEX IF NOT EXISTS idx_matches_begin_at   ON matches(begin_at);
CREATE INDEX IF NOT EXISTS idx_matches_tournament ON matches(tournament_id);
CREATE INDEX IF NOT EXISTS idx_matches_teams      ON matches(team_a_id, team_b_id);

-- Map-level results (for finer-grained / Bo-aware modelling later).
CREATE TABLE IF NOT EXISTS games (
    id        INTEGER PRIMARY KEY,
    match_id  INTEGER,
    position  INTEGER,
    status    TEXT,
    complete  INTEGER,
    forfeit   INTEGER,
    winner_id INTEGER,
    FOREIGN KEY (match_id) REFERENCES matches(id)
);

CREATE INDEX IF NOT EXISTS idx_games_match ON games(match_id);

-- Derived team ratings, snapshotted at an as-of date so backtests can freeze
-- strength at a tournament's start without leaking future results.
CREATE TABLE IF NOT EXISTS ratings (
    team_id   INTEGER NOT NULL,
    as_of     TEXT NOT NULL,     -- ISO date the rating is valid as of
    system    TEXT NOT NULL,     -- e.g. glicko2, elo
    rating    REAL NOT NULL,
    deviation REAL,
    volatility REAL,
    PRIMARY KEY (team_id, as_of, system),
    FOREIGN KEY (team_id) REFERENCES teams(id)
);

-- Bookkeeping for incremental backfills.
CREATE TABLE IF NOT EXISTS ingest_log (
    key        TEXT PRIMARY KEY,  -- e.g. matches_through
    value      TEXT,
    updated_at TEXT
);

-- ---------------------------------------------------------------------------
-- bo3.gg enrichment: map veto sequences + named map results.
-- bo3.gg exposes the full pick/ban order and map names that PandaScore's free
-- tier does not. Teams are joined to our PandaScore ids via bo3's `ps_id`.
-- ---------------------------------------------------------------------------

-- PandaScore team id  <->  bo3.gg team id. Most teams join exactly on bo3's
-- stored ps_id; newer orgs (ps_id NULL on bo3) fall back to name/alias.
CREATE TABLE IF NOT EXISTS team_id_map (
    ps_id    INTEGER PRIMARY KEY,   -- our PandaScore team id
    bo3_id   INTEGER NOT NULL,
    bo3_name TEXT,
    bo3_slug TEXT,
    method   TEXT,                  -- ps_id | name | alias
    FOREIGN KEY (ps_id) REFERENCES teams(id)
);

-- Link from our match to its bo3.gg counterpart, with the raw per-map score
-- array (bo3's team1 perspective) kept for later per-map-winner attribution.
CREATE TABLE IF NOT EXISTS match_bo3 (
    match_id     INTEGER PRIMARY KEY,  -- our PandaScore match id
    bo3_id       INTEGER,
    bo3_slug     TEXT,
    bo3_team1_ps INTEGER,              -- ps id of bo3's team1 (maps_score perspective)
    maps_score   TEXT,                 -- JSON bool array, team1 view, play order
    fetched_at   TEXT,
    FOREIGN KEY (match_id) REFERENCES matches(id)
);

-- One row per veto step (ban / pick / decider), in veto order.
CREATE TABLE IF NOT EXISTS match_vetos (
    match_id    INTEGER NOT NULL,   -- our PandaScore match id
    order_idx   INTEGER NOT NULL,   -- veto order (1..7 for a 7-map pool)
    action      TEXT NOT NULL,      -- ban | pick | decider
    actor_ps_id INTEGER,            -- ps team id that acted (NULL for decider)
    map_name    TEXT NOT NULL,      -- canonical, e.g. de_mirage
    played      INTEGER NOT NULL,   -- 1 if the map was played (pick/decider)
    PRIMARY KEY (match_id, order_idx),
    FOREIGN KEY (match_id) REFERENCES matches(id)
);

CREATE INDEX IF NOT EXISTS idx_vetos_map   ON match_vetos(map_name);
CREATE INDEX IF NOT EXISTS idx_vetos_actor ON match_vetos(actor_ps_id);

-- Per-map-name team ratings, snapshotted at an as-of date. Built with the
-- team's global map rating as a shrinkage prior, so a thin map history stays
-- near overall map skill while a rich one reflects map-specific strength.
CREATE TABLE IF NOT EXISTS map_ratings (
    team_id    INTEGER NOT NULL,
    as_of      TEXT NOT NULL,
    map_name   TEXT NOT NULL,
    rating     REAL NOT NULL,
    deviation  REAL,
    volatility REAL,
    n_games    INTEGER,          -- map-specific games seen (transparency)
    PRIMARY KEY (team_id, as_of, map_name),
    FOREIGN KEY (team_id) REFERENCES teams(id)
);
