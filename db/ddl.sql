-- Bloomberg Datenlizenz-Management: Datenmodell für Databricks (Unity Catalog, Delta Lake)
-- Anforderungs-IDs (B-01, A1, ...) verweisen auf den Anforderungskatalog.
-- Primär- und Fremdschlüssel sind in Unity Catalog informativ, sie werden nicht erzwungen.

CREATE CATALOG IF NOT EXISTS bbg_license;
USE CATALOG bbg_license;

CREATE SCHEMA IF NOT EXISTS bronze COMMENT 'Rohdaten, unverändert wie geliefert';
CREATE SCHEMA IF NOT EXISTS silver COMMENT 'Normalisiertes Datenmodell und Zeitreihen (A1, C1)';
CREATE SCHEMA IF NOT EXISTS gold   COMMENT 'Kosten, Nutzung, Datenqualität (B2, D1)';
CREATE SCHEMA IF NOT EXISTS app    COMMENT 'Zustand des Lizenz-Service: Universum, Bestellungen, Prüfpfad';

-- ---------------------------------------------------------------------------
-- bronze: Rohdaten
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bronze.bbg_response_raw (
  request_id      STRING    NOT NULL COMMENT 'Bloomberg-Request, aus dem die Datei stammt',
  file_name       STRING    NOT NULL,
  received_at     TIMESTAMP NOT NULL,
  line_no         INT       NOT NULL,
  payload         STRING    NOT NULL COMMENT 'Zeile bzw. JSON-Objekt wie geliefert'
) COMMENT 'Antwortdateien der Bloomberg Data License';

CREATE TABLE IF NOT EXISTS bronze.xentis_positions_raw (
  delivery_date   DATE      NOT NULL,
  file_name       STRING    NOT NULL,
  received_at     TIMESTAMP NOT NULL,
  line_no         INT       NOT NULL,
  payload         STRING    NOT NULL
) COMMENT 'Tägliche Positionslieferung aus Xentis';

-- ---------------------------------------------------------------------------
-- app: Zustand des Lizenz-Service
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS app.instrument (
  instrument_id         STRING    NOT NULL,
  figi                  STRING,
  isin                  STRING,
  cusip                 STRING,
  sedol                 STRING,
  ticker                STRING    COMMENT 'Bloomberg-Ticker inkl. Yellow Key, z. B. NESN SW Equity',
  name                  STRING,
  status                STRING    NOT NULL,
  source                STRING    NOT NULL COMMENT 'MANUAL oder XENTIS',
  subscribed_since      DATE,
  unsubscribe_effective DATE      COMMENT 'Monatsende, ab dem nicht mehr angefragt wird',
  created_at            TIMESTAMP NOT NULL,
  updated_at            TIMESTAMP NOT NULL,
  CONSTRAINT pk_instrument PRIMARY KEY (instrument_id),
  CONSTRAINT ck_instrument_status CHECK (status IN ('ACTIVE','PENDING_SUBSCRIBE','PENDING_UNSUBSCRIBE','INACTIVE')),
  CONSTRAINT ck_instrument_source CHECK (source IN ('MANUAL','XENTIS'))
) COMMENT 'Bloomberg-Universum (B-01, B-02)';

CREATE TABLE IF NOT EXISTS app.field_catalog (
  mnemonic     STRING NOT NULL COMMENT 'Bloomberg-Feld, z. B. PX_LAST',
  description  STRING,
  category     STRING NOT NULL COMMENT 'Abrechnungskategorie laut Bloomberg',
  data_type    STRING,
  CONSTRAINT pk_field_catalog PRIMARY KEY (mnemonic)
) COMMENT 'Feldkatalog (D3)';

CREATE TABLE IF NOT EXISTS app.field_set (
  field_set_id STRING NOT NULL,
  name         STRING NOT NULL,
  description  STRING,
  CONSTRAINT pk_field_set PRIMARY KEY (field_set_id)
) COMMENT 'Gebündelte Feldlisten, damit keine unnötigen Kategorien ausgelöst werden (B3)';

CREATE TABLE IF NOT EXISTS app.field_set_field (
  field_set_id STRING NOT NULL,
  mnemonic     STRING NOT NULL,
  CONSTRAINT pk_field_set_field PRIMARY KEY (field_set_id, mnemonic),
  CONSTRAINT fk_fsf_set   FOREIGN KEY (field_set_id) REFERENCES app.field_set,
  CONSTRAINT fk_fsf_field FOREIGN KEY (mnemonic)     REFERENCES app.field_catalog
);

CREATE TABLE IF NOT EXISTS app.instrument_field_set (
  instrument_id STRING NOT NULL,
  field_set_id  STRING NOT NULL,
  since         DATE   NOT NULL,
  CONSTRAINT pk_instrument_field_set PRIMARY KEY (instrument_id, field_set_id),
  CONSTRAINT fk_ifs_instrument FOREIGN KEY (instrument_id) REFERENCES app.instrument,
  CONSTRAINT fk_ifs_set        FOREIGN KEY (field_set_id)  REFERENCES app.field_set
);

CREATE TABLE IF NOT EXISTS app.pricing_category (
  category             STRING        NOT NULL,
  valid_from           DATE          NOT NULL,
  valid_to             DATE          COMMENT 'NULL = gültig',
  price_per_instrument DECIMAL(12,2) NOT NULL COMMENT 'Pro eindeutigem Instrument und Monat',
  currency             STRING        NOT NULL,
  CONSTRAINT pk_pricing_category PRIMARY KEY (category, valid_from)
) COMMENT 'Pflegbare Preistabelle; Vertragswerte werden nachgetragen (B2)';

CREATE TABLE IF NOT EXISTS app.`order` (
  order_id           STRING        NOT NULL,
  order_type         STRING        NOT NULL,
  status             STRING        NOT NULL,
  reason             STRING,
  estimated_cost_chf DECIMAL(12,2) COMMENT 'Mehrkosten im laufenden Monat',
  requires_approval  BOOLEAN       NOT NULL COMMENT 'true ab 200 CHF',
  requested_by       STRING        NOT NULL,
  requested_at       TIMESTAMP     NOT NULL,
  decided_by         STRING        COMMENT 'Muss sich von requested_by unterscheiden',
  decided_at         TIMESTAMP,
  decision_comment   STRING,
  effective_date     DATE,
  bbg_request_id     STRING,
  idempotency_key    STRING,
  CONSTRAINT pk_order PRIMARY KEY (order_id),
  CONSTRAINT ck_order_type   CHECK (order_type IN ('SUBSCRIBE','UNSUBSCRIBE')),
  CONSTRAINT ck_order_status CHECK (status IN ('DRAFT','PENDING_APPROVAL','APPROVED','REJECTED','SUBMITTED','COMPLETED','FAILED')),
  CONSTRAINT ck_order_4eyes  CHECK (decided_by IS NULL OR decided_by <> requested_by)
) COMMENT 'Bestellungen und Abbestellungen mit 4-Augen-Freigabe';

CREATE TABLE IF NOT EXISTS app.order_item (
  order_id         STRING NOT NULL,
  line_no          INT    NOT NULL,
  identifier_type  STRING NOT NULL,
  identifier_value STRING NOT NULL,
  instrument_id    STRING COMMENT 'Nach Auflösung des Identifiers',
  field_set_id     STRING,
  is_duplicate     BOOLEAN NOT NULL DEFAULT false COMMENT 'Bereits im Universum (B3)',
  CONSTRAINT pk_order_item PRIMARY KEY (order_id, line_no),
  CONSTRAINT fk_oi_order FOREIGN KEY (order_id) REFERENCES app.`order`,
  CONSTRAINT ck_oi_identifier CHECK (identifier_type IN ('ISIN','FIGI','CUSIP','SEDOL','TICKER'))
) TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported');

CREATE TABLE IF NOT EXISTS app.job (
  job_id      STRING    NOT NULL,
  kind        STRING    NOT NULL,
  status      STRING    NOT NULL,
  started_at  TIMESTAMP,
  finished_at TIMESTAMP,
  summary     STRING    COMMENT 'JSON',
  CONSTRAINT pk_job PRIMARY KEY (job_id),
  CONSTRAINT ck_job_kind   CHECK (kind IN ('BACKFILL','RECONCILE','DELIVERY')),
  CONSTRAINT ck_job_status CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED'))
);

CREATE TABLE IF NOT EXISTS app.reconcile_proposal (
  job_id        STRING NOT NULL,
  instrument_id STRING,
  isin          STRING,
  action        STRING NOT NULL,
  reason        STRING,
  order_id      STRING COMMENT 'Gesetzt, wenn daraus eine Bestellung wurde',
  CONSTRAINT fk_rp_job FOREIGN KEY (job_id) REFERENCES app.job,
  CONSTRAINT ck_rp_action CHECK (action IN ('SUBSCRIBE','UNSUBSCRIBE','KEEP'))
) COMMENT 'Ergebnis des täglichen Abgleichs mit Xentis (B1)';

CREATE TABLE IF NOT EXISTS app.audit_log (
  at        TIMESTAMP NOT NULL,
  user_name STRING    NOT NULL,
  action    STRING    NOT NULL COMMENT 'z. B. ORDER_CREATED, ORDER_APPROVED, PRICING_CHANGED',
  entity    STRING    NOT NULL,
  entity_id STRING    NOT NULL,
  details   STRING    COMMENT 'JSON'
) CLUSTER BY (entity, entity_id)
  COMMENT 'Prüfpfad, nur anfügen (D2)';

-- ---------------------------------------------------------------------------
-- silver: Datenmodell (A1) und Zeitreihen (C1)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS silver.issuer (
  issuer_id      STRING NOT NULL COMMENT 'Bloomberg ID_BB_COMPANY',
  name           STRING,
  lei            STRING,
  country        STRING,
  industry       STRING,
  CONSTRAINT pk_issuer PRIMARY KEY (issuer_id)
) COMMENT 'Emittent';

CREATE TABLE IF NOT EXISTS silver.security (
  instrument_id  STRING NOT NULL,
  issuer_id      STRING,
  security_type  STRING,
  currency       STRING,
  composite_figi STRING,
  share_class_figi STRING,
  CONSTRAINT pk_security PRIMARY KEY (instrument_id),
  CONSTRAINT fk_security_instrument FOREIGN KEY (instrument_id) REFERENCES app.instrument,
  CONSTRAINT fk_security_issuer     FOREIGN KEY (issuer_id)     REFERENCES silver.issuer
) COMMENT 'Instrument, verknüpft mit dem Emittenten';

CREATE TABLE IF NOT EXISTS silver.listing (
  figi           STRING NOT NULL COMMENT 'FIGI des Listings',
  instrument_id  STRING NOT NULL,
  exchange_code  STRING,
  ticker         STRING,
  currency       STRING,
  is_primary     BOOLEAN,
  CONSTRAINT pk_listing PRIMARY KEY (figi),
  CONSTRAINT fk_listing_security FOREIGN KEY (instrument_id) REFERENCES silver.security
) COMMENT 'Börsenlisting eines Instruments';

CREATE TABLE IF NOT EXISTS silver.timeseries_value (
  instrument_id  STRING    NOT NULL,
  field          STRING    NOT NULL,
  value_date     DATE      NOT NULL COMMENT 'Fachliches Datum des Werts',
  value_num      DECIMAL(28,10),
  value_str      STRING,
  recorded_at    TIMESTAMP NOT NULL COMMENT 'Ab wann der Wert bekannt war',
  superseded_at  TIMESTAMP COMMENT 'Ab wann ein korrigierter Wert galt; NULL = aktuell',
  bbg_request_id STRING    NOT NULL COMMENT 'Herkunft (D2)',
  CONSTRAINT fk_ts_instrument FOREIGN KEY (instrument_id) REFERENCES app.instrument
) CLUSTER BY (instrument_id, field, value_date)
  COMMENT 'Bitemporale Zeitreihen: Korrekturen werden angefügt, nie überschrieben (B-06, C1)';

-- Wissensstand zu einem Stichtag (C1)
CREATE OR REPLACE FUNCTION silver.timeseries_as_of(as_of TIMESTAMP)
  RETURNS TABLE (instrument_id STRING, field STRING, value_date DATE,
                 value_num DECIMAL(28,10), value_str STRING, recorded_at TIMESTAMP)
  RETURN SELECT instrument_id, field, value_date, value_num, value_str, recorded_at
         FROM silver.timeseries_value
         WHERE recorded_at <= as_of
           AND (superseded_at IS NULL OR superseded_at > as_of);

CREATE OR REPLACE VIEW silver.timeseries_current AS
  SELECT instrument_id, field, value_date, value_num, value_str, recorded_at
  FROM silver.timeseries_value
  WHERE superseded_at IS NULL;

CREATE TABLE IF NOT EXISTS silver.position (
  as_of_date    DATE   NOT NULL,
  portfolio     STRING NOT NULL,
  isin          STRING,
  instrument_id STRING COMMENT 'Nach Auflösung über app.instrument',
  quantity      DECIMAL(28,6)
) CLUSTER BY (as_of_date)
  COMMENT 'Positionen aus Xentis, einmal pro Tag (B1)';

-- ---------------------------------------------------------------------------
-- gold: Kosten und Qualität (B2, D1)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS gold.billing_instrument_month (
  billing_month      STRING    NOT NULL COMMENT 'YYYY-MM',
  instrument_id      STRING    NOT NULL,
  category           STRING    NOT NULL,
  first_requested_at TIMESTAMP NOT NULL,
  CONSTRAINT pk_billing PRIMARY KEY (billing_month, instrument_id, category)
) COMMENT 'Welche Instrumente je Kategorie im Monat schon abgerechnet sind; Grundlage für Prognose und Deduplizierung';

CREATE OR REPLACE VIEW gold.usage_monthly AS
  SELECT b.billing_month,
         b.category,
         COUNT(DISTINCT b.instrument_id)                         AS unique_instruments,
         COUNT(DISTINCT b.instrument_id) * p.price_per_instrument AS cost,
         p.currency
  FROM gold.billing_instrument_month b
  LEFT JOIN app.pricing_category p
    ON p.category = b.category
   AND to_date(b.billing_month || '-01') >= p.valid_from
   AND (p.valid_to IS NULL OR to_date(b.billing_month || '-01') < p.valid_to)
  GROUP BY b.billing_month, b.category, p.price_per_instrument, p.currency;

CREATE TABLE IF NOT EXISTS gold.dq_exception (
  exception_id  STRING    NOT NULL,
  type          STRING    NOT NULL,
  instrument_id STRING    NOT NULL,
  field         STRING    NOT NULL,
  value_date    DATE      NOT NULL,
  detected_at   TIMESTAMP NOT NULL,
  status        STRING    NOT NULL,
  handled_by    STRING,
  CONSTRAINT pk_dq_exception PRIMARY KEY (exception_id),
  CONSTRAINT ck_dq_type   CHECK (type IN ('STALE_PRICE','OUTLIER','MISSING_VALUE')),
  CONSTRAINT ck_dq_status CHECK (status IN ('OPEN','ACKNOWLEDGED','RESOLVED'))
) COMMENT 'Datenqualitäts-Ausnahmen (D1)';
