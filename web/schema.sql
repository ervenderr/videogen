CREATE TABLE IF NOT EXISTS jobs (
  id          serial PRIMARY KEY,
  topic       text NOT NULL,
  status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  progress    text,
  output_path text,
  error       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
