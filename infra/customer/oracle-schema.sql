-- Oracle source schema for call-center analytics.
--
-- Target: Oracle Database 19c or later. 19c is the only Oracle version
-- explicitly referenced in the account correspondence, and it is comfortably
-- within the range Fabric Mirroring supports (Oracle 10 and above with
-- LogMiner). The DDL avoids 21c/23ai-only syntax so it runs unchanged on 19c.
--
-- Run as a schema owner, not as SYS or SYSTEM. Mirroring cannot replicate
-- objects owned by Oracle-maintained schemas.

CREATE TABLE call_analytics (
  call_id                VARCHAR2(100)                   NOT NULL,
  call_start_utc         TIMESTAMP(6) WITH TIME ZONE     NOT NULL,
  queue_name             VARCHAR2(100)                   NOT NULL,
  region                 VARCHAR2(50)                    NOT NULL,
  customer_intent        VARCHAR2(100)                   NOT NULL,
  disposition            VARCHAR2(100)                   NOT NULL,
  -- VARCHAR2 rather than CLOB: Fabric Mirroring does not replicate LOB
  -- columns, so an oversized summary would silently drop out of OneLake.
  -- 4000 bytes is the non-extended VARCHAR2 maximum on 19c and is far above
  -- the 80-120 word PII-safe summary the pipeline emits.
  pii_safe_summary       VARCHAR2(4000)                  NOT NULL,
  sentiment              VARCHAR2(50)                    NOT NULL,
  escalation_flag        NUMBER(1)                       NOT NULL,
  cancellation_flag      NUMBER(1)                       NOT NULL,
  competitor             VARCHAR2(100),
  competitor_mentions    NUMBER(10)         DEFAULT 0    NOT NULL,
  handle_time_seconds    NUMBER(10),
  repeat_contact_flag    NUMBER(1),
  account_tenure_months  NUMBER(10),
  monthly_revenue_usd    NUMBER(12,2),
  ingestion_utc          TIMESTAMP(6) WITH TIME ZONE     NOT NULL,
  CONSTRAINT call_analytics_pk      PRIMARY KEY (call_id),
  CONSTRAINT call_analytics_esc_ck  CHECK (escalation_flag IN (0, 1)),
  CONSTRAINT call_analytics_can_ck  CHECK (cancellation_flag IN (0, 1)),
  CONSTRAINT call_analytics_rpt_ck  CHECK (repeat_contact_flag IN (0, 1)),
  CONSTRAINT call_analytics_ment_ck CHECK (competitor_mentions >= 0)
);

CREATE INDEX call_analytics_start_ix      ON call_analytics (call_start_utc);
CREATE INDEX call_analytics_competitor_ix ON call_analytics (competitor);

COMMENT ON TABLE call_analytics IS
  'PII-safe call analytics. Redacted summaries and normalized attributes only; no raw transcript, audio, or personal data.';

COMMENT ON COLUMN call_analytics.competitor IS
  'Normalized competitor name. Spelling variants are resolved upstream so counts are deterministic.';

COMMENT ON COLUMN call_analytics.monthly_revenue_usd IS
  'Recurring revenue for the account, joined from billing. Enables revenue-weighted churn-risk analysis.';

-- Fabric Mirroring prerequisites -------------------------------------------
-- Mirroring reads redo through LogMiner, so the table needs supplemental
-- logging. Without it, updates and deletes do not replicate correctly.

ALTER TABLE call_analytics ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;

-- Database-level prerequisites, run once by a DBA. Listed here for
-- completeness rather than automation: enabling archivelog requires the
-- instance to be restarted in MOUNT state, so it belongs in a change request,
-- not in a deployment script.
--
--   SELECT log_mode FROM v$database;                  -- expect ARCHIVELOG
--   SELECT supplemental_log_data_min FROM v$database; -- expect YES
--
--   ALTER DATABASE ADD SUPPLEMENTAL LOG DATA;
--   -- ALTER DATABASE ARCHIVELOG;  -- requires MOUNT state and a restart
