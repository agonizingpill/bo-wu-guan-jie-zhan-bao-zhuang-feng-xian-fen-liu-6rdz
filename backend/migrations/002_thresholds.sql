CREATE TABLE IF NOT EXISTS threshold_profile (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thresholds jsonb NOT NULL,
    origin text NOT NULL CHECK (origin IN ('initial', 'trial')),
    trial_id bigint,
    published_by text,
    published_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS threshold_trial (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status text NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'published')),
    proposed jsonb NOT NULL,
    baseline jsonb NOT NULL,
    baseline_profile_id bigint NOT NULL,
    sample jsonb,
    sample_sha256 text NOT NULL,
    results jsonb,
    eligible boolean,
    ineligibility jsonb NOT NULL DEFAULT '[]'::jsonb,
    attempts integer NOT NULL DEFAULT 1 CHECK (attempts >= 1),
    error text,
    requested_by text NOT NULL REFERENCES staff,
    created_at timestamptz NOT NULL DEFAULT now(),
    computed_at timestamptz,
    published_at timestamptz
);

CREATE INDEX IF NOT EXISTS threshold_trial_recent ON threshold_trial (id DESC);
CREATE INDEX IF NOT EXISTS threshold_profile_recent ON threshold_profile (id DESC);

INSERT INTO threshold_profile (thresholds, origin, published_by)
SELECT '{"porcelain": 0.58, "paper": 0.58, "bronze": 0.58}'::jsonb, 'initial', NULL
WHERE NOT EXISTS (SELECT 1 FROM threshold_profile);

ALTER TABLE triage_case ADD COLUMN IF NOT EXISTS thresholds jsonb;

-- Cases queued before threshold versioning keep the profile that was current at migration time.
UPDATE triage_case
SET thresholds = (SELECT thresholds FROM threshold_profile ORDER BY id DESC LIMIT 1)
WHERE thresholds IS NULL;
