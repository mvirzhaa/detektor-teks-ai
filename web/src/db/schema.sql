-- Skema database Detektor Teks AI (idempoten: aman dijalankan berulang)

CREATE TABLE IF NOT EXISTS samples (
  id           BIGSERIAL PRIMARY KEY,
  text         TEXT        NOT NULL,
  text_hash    CHAR(64)    NOT NULL UNIQUE,          -- sha256 teks ternormalisasi, cegah duplikat
  label        SMALLINT    NOT NULL CHECK (label IN (0, 1)),  -- 0 = manusia, 1 = AI
  source       VARCHAR(20) NOT NULL DEFAULT 'manual', -- manual | file | import | feedback | cli
  filename     TEXT,
  language     VARCHAR(8),
  word_count   INT         NOT NULL DEFAULT 0,
  note         TEXT,
  learned_at   TIMESTAMPTZ,                           -- kapan dipelajari model
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS samples_label_idx   ON samples (label);
CREATE INDEX IF NOT EXISTS samples_created_idx ON samples (created_at DESC);

CREATE TABLE IF NOT EXISTS model_versions (
  id          SERIAL PRIMARY KEY,
  kind        VARCHAR(16) NOT NULL,                   -- incremental | full
  file_path   TEXT        NOT NULL,
  is_active   BOOLEAN     NOT NULL DEFAULT false,
  n_samples   INT, n_human INT, n_ai INT,
  updates     INT         NOT NULL DEFAULT 0,         -- jumlah pembaruan incremental
  params      JSONB,
  metrics     JSONB,                                  -- hasil evaluasi data uji
  note        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS model_versions_one_active ON model_versions (is_active) WHERE is_active;

CREATE TABLE IF NOT EXISTS checks (
  id                 BIGSERIAL PRIMARY KEY,
  input_type         VARCHAR(10) NOT NULL,            -- text | file
  filename           TEXT,
  text               TEXT        NOT NULL,
  word_count         INT,
  ai_probability     REAL        NOT NULL,
  verdict            VARCHAR(20) NOT NULL,
  verdict_label      TEXT,
  mode               VARCHAR(12) NOT NULL,            -- model | heuristic
  model_version_id   INT REFERENCES model_versions(id) ON DELETE SET NULL,
  result             JSONB       NOT NULL,
  feedback_label     SMALLINT CHECK (feedback_label IN (0, 1)),
  feedback_sample_id BIGINT REFERENCES samples(id) ON DELETE SET NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS checks_created_idx ON checks (created_at DESC);
