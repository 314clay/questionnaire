CREATE TABLE questionnaires (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN (
    'multiple-choice', 'multi-select', 'confirm', 'rich-choice',
    'toggle', 'hold-button', 'multi-live', 'button-grid', 'combo',
    'live-stream'
  )),
  title TEXT NOT NULL,
  payload JSONB NOT NULL,
  is_persistent BOOLEAN NOT NULL DEFAULT FALSE,
  allow_multiple BOOLEAN NOT NULL DEFAULT FALSE,
  closed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE responses (
  id SERIAL PRIMARY KEY,
  questionnaire_id TEXT NOT NULL REFERENCES questionnaires(id) ON DELETE CASCADE,
  response_data JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE audio_clips (
  id SERIAL PRIMARY KEY,
  response_id INTEGER NOT NULL REFERENCES responses(id) ON DELETE CASCADE,
  clip_index INTEGER NOT NULL DEFAULT 0,
  file_path TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  duration_ms INTEGER,
  size_bytes INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_responses_questionnaire ON responses(questionnaire_id, created_at DESC);
CREATE INDEX idx_questionnaires_active ON questionnaires(closed_at) WHERE closed_at IS NULL;
CREATE INDEX idx_audio_response ON audio_clips(response_id);
