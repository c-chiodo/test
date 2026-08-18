-- PIMS (Process/Production Inventory Management System) — modern schema.
--
-- Column names deliberately track the legacy ProductionData entities (see
-- docs/PIMS_PARITY.md for the field-by-field map) so a cutover migration is a
-- mechanical INSERT ... SELECT rather than a re-interpretation of the data.
--
-- Two things are new, and both exist to fix documented legacy defects:
--   * material_test  — which analytes a material actually runs. The legacy QC
--     screen decided that from order type alone, which is the FE-2026-002 bug.
--   * material_spec  — min/max limits per material/analyte, so out-of-spec
--     results are flagged in PIMS instead of only in LIMS.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- org / ref

CREATE TABLE IF NOT EXISTS company (
    company_id    INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS plant (
    plant_id      INTEGER PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS department (
    department_id INTEGER PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,
    description   TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS plant_department (
    plant_id      INTEGER NOT NULL REFERENCES plant(plant_id),
    department_id INTEGER NOT NULL REFERENCES department(department_id),
    PRIMARY KEY (plant_id, department_id)
);

CREATE TABLE IF NOT EXISTS order_type (
    order_type_id INTEGER PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,     -- SO / PO / WO / TO
    description   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS status (
    status_id     INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    description   TEXT NOT NULL DEFAULT '',
    is_terminal   INTEGER NOT NULL DEFAULT 0
);

-- ------------------------------------------------------------- materials

CREATE TABLE IF NOT EXISTS material_type (
    material_type_id INTEGER PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS material (
    material_id      INTEGER PRIMARY KEY,
    number           TEXT NOT NULL UNIQUE,   -- '05001' — the code staff know
    description      TEXT NOT NULL,
    material_type_id INTEGER NOT NULL REFERENCES material_type(material_type_id),
    family           TEXT NOT NULL DEFAULT '',
    density          REAL NOT NULL DEFAULT 7.6,  -- lbs/gal
    active           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS material_plant (
    material_id   INTEGER NOT NULL REFERENCES material(material_id),
    plant_id      INTEGER NOT NULL REFERENCES plant(plant_id),
    PRIMARY KEY (material_id, plant_id)
);

-- Which analytes a material is actually tested for. Drives QC validation.
CREATE TABLE IF NOT EXISTS material_test (
    material_id   INTEGER NOT NULL REFERENCES material(material_id),
    analyte       TEXT NOT NULL,            -- moisture|temp|ph|ffa|tfa|spintest|flash|...
    required      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (material_id, analyte)
);

-- Min/max acceptance limits. NULL bound = unbounded on that side.
CREATE TABLE IF NOT EXISTS material_spec (
    spec_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id   INTEGER NOT NULL REFERENCES material(material_id),
    analyte       TEXT NOT NULL,
    min_value     REAL,
    max_value     REAL,
    source        TEXT NOT NULL DEFAULT 'QC sheet',
    note          TEXT NOT NULL DEFAULT '',
    needs_review  INTEGER NOT NULL DEFAULT 0,  -- the '⚠ confirm' rows
    active        INTEGER NOT NULL DEFAULT 1,
    UNIQUE (material_id, analyte)
);

-- ------------------------------------------------------------- locations

CREATE TABLE IF NOT EXISTS location_type (
    location_type_id INTEGER PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS location (
    location_id      INTEGER PRIMARY KEY,
    plant_id         INTEGER NOT NULL REFERENCES plant(plant_id),
    number           TEXT NOT NULL,
    description      TEXT NOT NULL,
    location_type_id INTEGER NOT NULL REFERENCES location_type(location_type_id),
    company_id       INTEGER REFERENCES company(company_id),
    max_capacity     REAL,                  -- lbs
    bol_required     INTEGER NOT NULL DEFAULT 0,
    active           INTEGER NOT NULL DEFAULT 1,
    UNIQUE (plant_id, number)
);

CREATE TABLE IF NOT EXISTS location_default (
    order_type_id INTEGER NOT NULL REFERENCES order_type(order_type_id),
    plant_id      INTEGER NOT NULL REFERENCES plant(plant_id),
    location_id   INTEGER NOT NULL REFERENCES location(location_id),
    PRIMARY KEY (order_type_id, plant_id)
);

-- ------------------------------------------------------- trading partners

CREATE TABLE IF NOT EXISTS customer (
    customer_id   INTEGER PRIMARY KEY,
    gp_custnmbr   TEXT NOT NULL UNIQUE,     -- Great Plains key
    name          TEXT NOT NULL,
    city          TEXT NOT NULL DEFAULT '',
    state         TEXT NOT NULL DEFAULT '',
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS vendor (
    vendor_id     INTEGER PRIMARY KEY,
    gp_vendorid   TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    city          TEXT NOT NULL DEFAULT '',
    state         TEXT NOT NULL DEFAULT '',
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS partner_requirement (
    requirement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    party_type     TEXT NOT NULL,           -- customer | vendor
    party_id       INTEGER NOT NULL,
    requirement    TEXT NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    is_production  INTEGER NOT NULL DEFAULT 0,
    is_carrier     INTEGER NOT NULL DEFAULT 0,
    active         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS carrier (
    carrier_id    INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1
);

-- ---------------------------------------------------------------- orders

CREATE TABLE IF NOT EXISTS "order" (
    order_id             INTEGER PRIMARY KEY,
    order_type_id        INTEGER NOT NULL REFERENCES order_type(order_type_id),
    order_date           TEXT NOT NULL,
    due_date             TEXT NOT NULL,
    order_reference      TEXT NOT NULL DEFAULT '',
    company_id           INTEGER NOT NULL REFERENCES company(company_id),
    plant_id             INTEGER NOT NULL REFERENCES plant(plant_id),
    department_id        INTEGER REFERENCES department(department_id),
    blend_serial_number  TEXT NOT NULL DEFAULT '',
    vendor_id            INTEGER REFERENCES vendor(vendor_id),
    customer_id          INTEGER REFERENCES customer(customer_id),
    material_one_id      INTEGER REFERENCES material(material_id),
    material_two_id      INTEGER REFERENCES material(material_id),
    material_three_id    INTEGER REFERENCES material(material_id),
    material_four_id     INTEGER REFERENCES material(material_id),
    material_one_quantity REAL NOT NULL DEFAULT 0,
    ship_method          TEXT NOT NULL DEFAULT '',
    trailer_number       TEXT NOT NULL DEFAULT '',
    load_by_eta          TEXT,
    comments             TEXT NOT NULL DEFAULT '',
    status_id            INTEGER NOT NULL REFERENCES status(status_id),
    active               INTEGER NOT NULL DEFAULT 1,
    date_added           TEXT NOT NULL,
    added_by             TEXT NOT NULL,
    date_modified        TEXT,
    modified_by          TEXT
);

CREATE INDEX IF NOT EXISTS ix_order_plant_due ON "order" (plant_id, due_date);
CREATE INDEX IF NOT EXISTS ix_order_status    ON "order" (status_id);

-- --------------------------------------------------------- inventory ledger

CREATE TABLE IF NOT EXISTS transaction_type (
    transaction_type_id INTEGER PRIMARY KEY,
    code                TEXT NOT NULL UNIQUE,  -- RECEIVE|PRODUCE|MOVE|LOAD|SHIP|SHRINK|ADJUST
    description         TEXT NOT NULL
);

-- Append-only. Corrections are reversing entries, never UPDATEs: the legacy
-- system allowed silent edits, which is why activity history could not be
-- reconciled with location balances.
CREATE TABLE IF NOT EXISTS inventory_transaction (
    transaction_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_transaction_id INTEGER REFERENCES inventory_transaction(transaction_id),
    transaction_type_id  INTEGER NOT NULL REFERENCES transaction_type(transaction_type_id),
    order_id             INTEGER REFERENCES "order"(order_id),
    plant_id             INTEGER NOT NULL REFERENCES plant(plant_id),
    department_id        INTEGER REFERENCES department(department_id),
    transaction_date     TEXT NOT NULL,      -- system timestamp (UTC)
    user_date            TEXT NOT NULL,      -- operator-declared business date
    user_id              INTEGER NOT NULL REFERENCES app_user(user_id),
    from_material_id     INTEGER REFERENCES material(material_id),
    from_location_id     INTEGER REFERENCES location(location_id),
    from_qty             REAL NOT NULL DEFAULT 0,
    from_bol             TEXT NOT NULL DEFAULT '',
    to_material_id       INTEGER REFERENCES material(material_id),
    to_location_id       INTEGER REFERENCES location(location_id),
    to_qty               REAL NOT NULL DEFAULT 0,
    to_bol               TEXT NOT NULL DEFAULT '',
    trailer_number       TEXT NOT NULL DEFAULT '',
    tank_hours           REAL,
    employee_hours       REAL,
    remarks              TEXT NOT NULL DEFAULT '',
    -- `voided` marks a row that has been reversed; `is_reversal` marks the
    -- offsetting row written to reverse it. Both stay in the ledger and both
    -- count toward balances (they cancel); fulfilment maths excludes the pair.
    voided               INTEGER NOT NULL DEFAULT 0,
    is_reversal          INTEGER NOT NULL DEFAULT 0,
    -- Groups the rows of one blend batch: a batch consumes several components
    -- and produces one product, and its ledger rows stand or fall together.
    batch_id             TEXT,
    -- Minted by the client before it posts and held until the post succeeds.
    -- If the answer is lost on the way back — plant Wi-Fi, a closed laptop —
    -- the retry carries the same key and returns the transaction that already
    -- exists instead of putting the load on the truck twice.
    idempotency_key      TEXT
);

CREATE INDEX IF NOT EXISTS ix_txn_batch ON inventory_transaction (batch_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_txn_idempotency
    ON inventory_transaction (idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_txn_order    ON inventory_transaction (order_id);
CREATE INDEX IF NOT EXISTS ix_txn_from_loc ON inventory_transaction (from_location_id, from_material_id);
CREATE INDEX IF NOT EXISTS ix_txn_to_loc   ON inventory_transaction (to_location_id, to_material_id);
CREATE INDEX IF NOT EXISTS ix_txn_date     ON inventory_transaction (transaction_date);

CREATE TABLE IF NOT EXISTS pending_shipment (
    stage_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id       INTEGER NOT NULL REFERENCES "order"(order_id),
    transaction_id INTEGER NOT NULL REFERENCES inventory_transaction(transaction_id),
    trailer_number TEXT NOT NULL DEFAULT '',
    quantity       REAL NOT NULL DEFAULT 0,
    -- `shipped` means the trailer left. `cancelled` means the load was voided
    -- and never left. Both drop the stage off the ship list, and keeping them
    -- apart is what stops a cancelled load being reported as a shipment.
    shipped        INTEGER NOT NULL DEFAULT 0,
    cancelled      INTEGER NOT NULL DEFAULT 0
);

-- -------------------------------------------------------------------- QC

CREATE TABLE IF NOT EXISTS qc (
    qc_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id            INTEGER NOT NULL REFERENCES "order"(order_id),
    bol_number          TEXT NOT NULL DEFAULT '',
    test_date           TEXT NOT NULL,
    performed_by        TEXT NOT NULL DEFAULT '',
    moisture            REAL,
    temp                REAL,
    ph                  REAL,
    ffa                 REAL,
    tfa                 REAL,
    spintest_fallout    REAL,
    flash_pf            TEXT,
    steam_on            INTEGER NOT NULL DEFAULT 0,
    seal_number         TEXT NOT NULL DEFAULT '',
    last_material_hauled TEXT NOT NULL DEFAULT '',
    sample_number       TEXT NOT NULL DEFAULT '',
    blend_serial_number TEXT NOT NULL DEFAULT '',
    comments            TEXT NOT NULL DEFAULT '',
    -- Out-of-spec results can be saved, but only deliberately: the warnings
    -- shown at the time are frozen here beside the acknowledgement, so the
    -- record says what the person was looking at when they signed off.
    acknowledged_warnings INTEGER NOT NULL DEFAULT 0,
    warning_snapshot    TEXT NOT NULL DEFAULT '',
    active              INTEGER NOT NULL DEFAULT 1,
    date_added          TEXT NOT NULL,
    added_by            TEXT NOT NULL,
    date_modified       TEXT,
    modified_by         TEXT
);

CREATE INDEX IF NOT EXISTS ix_qc_order  ON qc (order_id);
CREATE INDEX IF NOT EXISTS ix_qc_sample ON qc (sample_number);

CREATE TABLE IF NOT EXISTS test_point (
    test_point_id INTEGER PRIMARY KEY,
    plant_id      INTEGER NOT NULL REFERENCES plant(plant_id),
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    active        INTEGER NOT NULL DEFAULT 1
);

-- In-process testing: readings taken at a test point during a run.
CREATE TABLE IF NOT EXISTS qc_in_process (
    reading_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      INTEGER NOT NULL REFERENCES "order"(order_id),
    test_point_id INTEGER NOT NULL REFERENCES test_point(test_point_id),
    reading_time  TEXT NOT NULL,
    analyte       TEXT NOT NULL,
    value         REAL,
    comments      TEXT NOT NULL DEFAULT '',
    added_by      TEXT NOT NULL,
    date_added    TEXT NOT NULL
);

-- ------------------------------------------------------------ QA checklist

CREATE TABLE IF NOT EXISTS qa_question (
    question_id INTEGER PRIMARY KEY,
    question    TEXT NOT NULL,
    answer_type TEXT NOT NULL DEFAULT 'yesno',  -- yesno | text | number
    -- When the question has to be answered: 'pre_load' questions are trailer
    -- inspections and are worthless once product is in the tank, so the flow
    -- asks them before the load rather than after it.
    stage       TEXT NOT NULL DEFAULT 'post_load',  -- pre_load | post_load
    enabled     INTEGER NOT NULL DEFAULT 1,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS qa_header (
    header_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id       INTEGER NOT NULL REFERENCES "order"(order_id),
    plant_id       INTEGER NOT NULL REFERENCES plant(plant_id),
    qc_id          INTEGER REFERENCES qc(qc_id),
    trailer_number TEXT NOT NULL DEFAULT '',
    trailer_load_time TEXT,
    comments       TEXT NOT NULL DEFAULT '',
    stage          TEXT NOT NULL DEFAULT 'post_load',  -- which pass this is
    voided         INTEGER NOT NULL DEFAULT 0,
    date_added     TEXT NOT NULL,
    added_by       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_response (
    response_id INTEGER PRIMARY KEY AUTOINCREMENT,
    header_id   INTEGER NOT NULL REFERENCES qa_header(header_id),
    question_id INTEGER NOT NULL REFERENCES qa_question(question_id),
    response    TEXT NOT NULL DEFAULT ''
);

-- ------------------------------------------------------------------- LIMS

-- Local projection of LabWare sample results ("matrix"). Refreshed from the
-- live LIMS; every row carries the source it came from and when, so the
-- staleness that caused FE-2026-001 is visible instead of silent.
CREATE TABLE IF NOT EXISTS lims_result (
    lims_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_code    TEXT NOT NULL,
    test_code      TEXT NOT NULL,
    component      TEXT NOT NULL,
    value_text     TEXT,
    value_num      REAL,
    include_in_report INTEGER NOT NULL DEFAULT 1,
    current_version   INTEGER NOT NULL DEFAULT 1,
    sampled_at     TEXT,
    source         TEXT NOT NULL,          -- e.g. XLIMSFEEDGROUP
    retrieved_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_lims_sample ON lims_result (sample_code);
CREATE INDEX IF NOT EXISTS ix_lims_test   ON lims_result (test_code);

-- ------------------------------------------------------- users & security

CREATE TABLE IF NOT EXISTS app_user (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    full_name     TEXT NOT NULL,
    email         TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'operator',  -- operator|qc|supervisor|admin
    password_hash TEXT NOT NULL DEFAULT '',
    active        INTEGER NOT NULL DEFAULT 1,
    date_added    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_plant_access (
    user_id  INTEGER NOT NULL REFERENCES app_user(user_id),
    plant_id INTEGER NOT NULL REFERENCES plant(plant_id),
    PRIMARY KEY (user_id, plant_id)
);

CREATE TABLE IF NOT EXISTS user_session (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES app_user(user_id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- ------------------------------------------------------------ supportability

-- Every state change, with before/after. The legacy app had Date_modified /
-- Modified_by columns and nothing else, so "who changed this order and to
-- what?" was unanswerable without a DBA.
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    username    TEXT NOT NULL,
    action      TEXT NOT NULL,             -- create|update|void|login|...
    entity      TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    -- The order this change belongs to, whatever entity it was recorded
    -- against. A transaction and a QC record are events in an order's life,
    -- and the order's history screen is where a supervisor goes looking.
    order_id    INTEGER,
    summary     TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit_log (entity, entity_id);
CREATE INDEX IF NOT EXISTS ix_audit_order  ON audit_log (order_id);
CREATE INDEX IF NOT EXISTS ix_audit_time   ON audit_log (occurred_at);

CREATE TABLE IF NOT EXISTS saved_query (
    query_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    definition  TEXT NOT NULL,             -- JSON QueryDefinition
    shared      INTEGER NOT NULL DEFAULT 1,
    active      INTEGER NOT NULL DEFAULT 1,
    added_by    TEXT NOT NULL,
    date_added  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_setting (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);

-- ------------------------------------------------- automation (added v1.1)

-- Counters behind generated BOL and sample numbers. Kept in the database
-- rather than derived from MAX(), so two loadout terminals cannot mint the
-- same number in the same second.
-- ------------------------------------------------------------------ blending
--
-- How a product is made from what the tanks hold. A recipe belongs to the
-- product it makes (one active recipe per product), and its components are
-- percentages by weight that sum to 100. The batch that executes a recipe is
-- ordinary ledger rows sharing a batch_id — blending invents no new kind of
-- movement, it is PRODUCE, several times, atomically.

CREATE TABLE IF NOT EXISTS blend_recipe (
    recipe_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES material(material_id),
    name        TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_recipe_material
    ON blend_recipe (material_id) WHERE active = 1;

CREATE TABLE IF NOT EXISTS blend_recipe_component (
    component_id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id    INTEGER NOT NULL REFERENCES blend_recipe(recipe_id),
    material_id  INTEGER NOT NULL REFERENCES material(material_id),
    percentage   REAL NOT NULL,
    sort_order   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS number_sequence (
    key        TEXT PRIMARY KEY,
    next_value INTEGER NOT NULL DEFAULT 1
);

-- Alerts. `fingerprint` is what makes a condition the *same* condition on the
-- next run, so a trailer that has been sitting for a week is reported once and
-- then goes quiet instead of paging someone nightly.
CREATE TABLE IF NOT EXISTS alert_log (
    alert_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    rule        TEXT NOT NULL,
    severity    TEXT NOT NULL,               -- info | warning | critical
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL,
    plant_id    INTEGER REFERENCES plant(plant_id),
    entity      TEXT NOT NULL DEFAULT '',
    entity_id   TEXT NOT NULL DEFAULT '',
    channel     TEXT NOT NULL DEFAULT '',
    delivered   INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT '',
    acknowledged_at TEXT,
    acknowledged_by TEXT
);

CREATE INDEX IF NOT EXISTS ix_alert_fingerprint ON alert_log (fingerprint);
CREATE INDEX IF NOT EXISTS ix_alert_created     ON alert_log (created_at);

-- Standing orders: the thing the legacy "# of orders to create" box was a
-- manual stand-in for.
CREATE TABLE IF NOT EXISTS recurring_order (
    recurring_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    template     TEXT NOT NULL,              -- JSON order payload
    cadence      TEXT NOT NULL,              -- daily | weekly | monthly
    weekday      INTEGER,                    -- 0=Monday, for weekly
    day_of_month INTEGER,                    -- for monthly
    lead_days    INTEGER NOT NULL DEFAULT 0, -- due date = run date + lead
    next_run     TEXT NOT NULL,
    last_run     TEXT,
    last_order_id INTEGER,
    active       INTEGER NOT NULL DEFAULT 1,
    added_by     TEXT NOT NULL,
    date_added   TEXT NOT NULL
);

-- Weights posted by a truck-scale agent, waiting to be attached to a load.
CREATE TABLE IF NOT EXISTS scale_reading (
    reading_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    plant_id     INTEGER NOT NULL REFERENCES plant(plant_id),
    scale_id     TEXT NOT NULL DEFAULT '',
    trailer_number TEXT NOT NULL DEFAULT '',
    gross_lbs    REAL,
    tare_lbs     REAL,
    net_lbs      REAL,
    captured_at  TEXT NOT NULL,
    received_at  TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'agent',
    consumed_by  INTEGER REFERENCES inventory_transaction(transaction_id)
);

CREATE INDEX IF NOT EXISTS ix_scale_plant ON scale_reading (plant_id, captured_at);

-- Record of every integration run, so "did the LIMS job run last night?" is a
-- query rather than a hunt through logs.
CREATE TABLE IF NOT EXISTS job_run (
    run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    job        TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status     TEXT NOT NULL DEFAULT 'running',   -- running | ok | failed
    detail     TEXT NOT NULL DEFAULT '{}',
    error      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_job_run ON job_run (job, started_at);
