-- Modul CEK KEMIRIPAN (ala Turnitin) — idempoten

-- Sumber data yang di-crawl (repositori jurnal, situs web, RSS) + sumber bawaan sistem
CREATE TABLE IF NOT EXISTS sim_sources (
  id            SERIAL PRIMARY KEY,
  name          TEXT        NOT NULL,
  kind          VARCHAR(20) NOT NULL,   -- oai_pmh | sitemap | web_crawl | rss | upload | web_search | submission
  category      VARCHAR(20) NOT NULL DEFAULT 'publication', -- internet | publication | submission
  url           TEXT,
  config        JSONB       NOT NULL DEFAULT '{}',
  schedule      VARCHAR(10) NOT NULL DEFAULT 'nightly',     -- nightly | weekly | manual
  enabled       BOOLEAN     NOT NULL DEFAULT true,
  system        BOOLEAN     NOT NULL DEFAULT false,         -- sumber bawaan, tidak bisa dihapus
  state         JSONB       NOT NULL DEFAULT '{}',          -- kursor crawl (mis. datestamp OAI terakhir)
  last_crawl_at TIMESTAMPTZ,
  last_status   VARCHAR(20),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS sim_sources_system_kind ON sim_sources (kind) WHERE system;

INSERT INTO sim_sources (name, kind, category, schedule, system)
VALUES ('Web — hasil pencarian', 'web_search', 'internet', 'manual', true),
       ('Repositori — unggahan manual', 'upload', 'publication', 'manual', true),
       ('Repositori tugas mahasiswa', 'submission', 'submission', 'manual', true)
ON CONFLICT (kind) WHERE system DO NOTHING;

-- Dokumen yang terindeks
CREATE TABLE IF NOT EXISTS sim_documents (
  id            BIGSERIAL PRIMARY KEY,
  source_id     INT         NOT NULL REFERENCES sim_sources(id) ON DELETE CASCADE,
  doc_key       TEXT        NOT NULL,   -- URL atau identifier OAI (unik per sumber)
  url           TEXT,
  title         TEXT,
  authors       TEXT,
  published     VARCHAR(32),
  text          TEXT        NOT NULL,
  content_hash  CHAR(64)    NOT NULL,
  word_count    INT         NOT NULL,
  etag          TEXT,
  last_modified TEXT,
  fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_id, doc_key)
);
CREATE INDEX IF NOT EXISTS sim_documents_hash_idx ON sim_documents (content_hash);
CREATE INDEX IF NOT EXISTS sim_documents_url_idx ON sim_documents (url);

-- Indeks terbalik fingerprint (hasil winnowing) → dokumen
CREATE TABLE IF NOT EXISTS sim_fingerprints (
  hash    BIGINT NOT NULL,
  doc_id  BIGINT NOT NULL REFERENCES sim_documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS sim_fingerprints_hash_idx ON sim_fingerprints (hash);
CREATE INDEX IF NOT EXISTS sim_fingerprints_doc_idx ON sim_fingerprints (doc_id);

-- Pemeriksaan kemiripan (antrean + laporan)
CREATE TABLE IF NOT EXISTS sim_checks (
  id                BIGSERIAL PRIMARY KEY,
  title             TEXT,
  author            TEXT,
  filename          TEXT,
  text              TEXT        NOT NULL,
  word_count        INT,
  options           JSONB       NOT NULL DEFAULT '{}',
  status            VARCHAR(12) NOT NULL DEFAULT 'queued',  -- queued | running | done | failed
  stage             TEXT,
  progress          SMALLINT    NOT NULL DEFAULT 0,
  similarity        REAL,
  internet          REAL,
  publication       REAL,
  submission        REAL,
  result            JSONB,
  error             TEXT,
  submission_doc_id BIGINT REFERENCES sim_documents(id) ON DELETE SET NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at        TIMESTAMPTZ,
  finished_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS sim_checks_status_idx ON sim_checks (status, id);

-- Riwayat crawling
CREATE TABLE IF NOT EXISTS sim_crawl_runs (
  id            BIGSERIAL PRIMARY KEY,
  source_id     INT         NOT NULL REFERENCES sim_sources(id) ON DELETE CASCADE,
  trigger       VARCHAR(12) NOT NULL DEFAULT 'manual',      -- manual | nightly | weekly
  status        VARCHAR(12) NOT NULL DEFAULT 'queued',      -- queued | running | done | failed
  docs_seen     INT NOT NULL DEFAULT 0,
  docs_new      INT NOT NULL DEFAULT 0,
  docs_updated  INT NOT NULL DEFAULT 0,
  docs_skipped  INT NOT NULL DEFAULT 0,
  docs_failed   INT NOT NULL DEFAULT 0,
  log           TEXT NOT NULL DEFAULT '',
  error         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at    TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS sim_crawl_runs_status_idx ON sim_crawl_runs (status, id);
CREATE INDEX IF NOT EXISTS sim_crawl_runs_source_idx ON sim_crawl_runs (source_id, id DESC);

CREATE TABLE IF NOT EXISTS sim_settings (
  key        TEXT PRIMARY KEY,
  value      JSONB,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
