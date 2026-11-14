-- Read-only role for Grafana on Phoenix's database (run once as `phoenix`, password via psql -v).
--   psql -U phoenix -d phoenix -v pw="$PHOENIX_DB_RO_PASSWORD" -f grafana-ro.sql
-- Only the trace tables: the same database holds users, API keys and secrets.
CREATE ROLE grafana_ro LOGIN PASSWORD :'pw' CONNECTION LIMIT 5;
ALTER ROLE grafana_ro SET default_transaction_read_only = on;
ALTER ROLE grafana_ro SET statement_timeout = '30s';
GRANT CONNECT ON DATABASE phoenix TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON projects, traces, spans, span_annotations, span_costs, project_sessions, generative_models
  TO grafana_ro;
