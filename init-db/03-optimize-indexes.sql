-- ==============================================================================
-- 03-optimize-indexes.sql
-- Индексы и оптимизации производительности в соответствии со скиллом postgres-patterns
-- ==============================================================================

\connect agent_db;

-- 1. B-Tree индексы на внешние ключи и частые фильтры равенства
CREATE INDEX IF NOT EXISTS idx_receipt_items_receipt_id
  ON public.receipt_items_unindexed (receipt_id);

CREATE INDEX IF NOT EXISTS idx_supplier_items_invoice_id
  ON public.supplier_invoice_items_loose (invoice_id);

-- 2. Составные индексы (Composite Indexes): колонки равенства первыми, диапазон вторыми
CREATE INDEX IF NOT EXISTS idx_raw_pharmacy_tx_pharmacy_time
  ON public.raw_pharmacy_transactions (pharmacy_id, transaction_time);

CREATE INDEX IF NOT EXISTS idx_daily_sales_pharmacy_date
  ON analytics.daily_pharmacy_sales_denorm (pharmacy_id, report_date);

CREATE INDEX IF NOT EXISTS idx_warehouse_batches_med_expiry
  ON public.warehouse_batches_loose (medication_name, expiry_date);

-- 3. Частичные индексы (Partial Indexes) для быстрого обслуживания очередей и активных статусов
CREATE INDEX IF NOT EXISTS idx_integration_queue_pending
  ON analytics.external_integration_queue (priority, scheduled_at)
  WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_ecom_reservations_active
  ON analytics.ecommerce_reservations_feed (pharmacy_id, created_at)
  WHERE status IN ('created', 'awaiting_pickup');

-- 4. BRIN индексы для тяжелых append-only таблиц логов и телеметрии (эффективность хранения в разы выше B-Tree)
CREATE INDEX IF NOT EXISTS idx_telemetry_time_brin
  ON public.cold_chain_telemetry_logs USING brin (recorded_at);

CREATE INDEX IF NOT EXISTS idx_audit_time_brin
  ON public.user_action_audit_raw USING brin (action_timestamp);

CREATE INDEX IF NOT EXISTS idx_mdlp_time_brin
  ON public.mdlp_serialization_logs USING brin (processed_at);

-- 5. GIN индексы для быстрого поиска по неструктурированным JSONB метаданным
CREATE INDEX IF NOT EXISTS idx_raw_pharmacy_tx_metadata_gin
  ON public.raw_pharmacy_transactions USING gin (payment_breakdown);
