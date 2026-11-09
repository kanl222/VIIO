-- Скрипт создания 22 больших неоптимизированных таблиц для тестирования агента VIIO
-- База: viio_gosapteka56_test, схемы: public и analytics

CREATE SCHEMA IF NOT EXISTS analytics;
GRANT USAGE ON SCHEMA public TO viio_gosapteka56_qa;
GRANT USAGE ON SCHEMA analytics TO viio_gosapteka56_qa;

-- 1. Таблица сырых логов транзакций (без индексов по дате/клиенту, TEXT вместо enum/foreign key)
DROP TABLE IF EXISTS public.raw_pharmacy_transactions CASCADE;
CREATE TABLE public.raw_pharmacy_transactions (
    tx_id text,
    kassa_number text,
    pharmacist_fio text,
    client_card_num text,
    transaction_date text,
    payment_method text,
    total_amount text,
    discount_applied text,
    payload_json text,
    fiscal_receipt_string text,
    notes text
);

-- 2. Таблица чеков и позиций (денормализованная, дублирует данные, нет индексов)
DROP TABLE IF EXISTS public.receipt_items_unindexed CASCADE;
CREATE TABLE public.receipt_items_unindexed (
    item_row_id text,
    receipt_guid text,
    branch_name text,
    branch_city text,
    branch_address text,
    article_code text,
    drug_trade_name text,
    manufacturer_name text,
    series_number text,
    expiration_date_str text,
    qty_sold text,
    price_per_unit text,
    tax_percent text,
    discount_sum text,
    is_prescription_required text,
    prescription_series text,
    doctor_fio text,
    patient_snils text
);

-- 3. Каталог складских остатков партий (огромная таблица партий без FK и без индексов)
DROP TABLE IF EXISTS public.warehouse_batches_loose CASCADE;
CREATE TABLE public.warehouse_batches_loose (
    batch_barcode text,
    warehouse_title text,
    shelf_zone_code text,
    drug_id_ref text,
    gtin_code text,
    mark_sgtin text,
    batch_status text,
    quantity_arrived text,
    quantity_current text,
    quantity_reserved text,
    cost_price_rub text,
    retail_price_rub text,
    supplier_inn text,
    supplier_legal_name text,
    invoice_number text,
    invoice_date_str text,
    storage_temp_condition text,
    customs_declaration text
);

-- 4. История изменения цен (огромный исторический лог)
DROP TABLE IF EXISTS public.price_audit_history CASCADE;
CREATE TABLE public.price_audit_history (
    change_id text,
    drug_code text,
    trade_name text,
    pharmacy_id text,
    old_retail_price text,
    new_retail_price text,
    old_wholesale_price text,
    new_wholesale_price text,
    markup_percent text,
    max_state_price_limit text,
    is_vital_drug_gnvls text,
    changed_by_user text,
    change_reason_comment text,
    effective_timestamp text,
    recalculated_flag text
);

-- 5. Журнал рецептурно-производственного отдела (РПО)
DROP TABLE IF EXISTS public.compounded_medication_orders CASCADE;
CREATE TABLE public.compounded_medication_orders (
    order_number text,
    prescription_id text,
    order_date text,
    target_dosage_form text,
    pharmacist_technologist text,
    component_names_aggregated text,
    component_doses_aggregated text,
    sterility_check_status text,
    control_chemist_fio text,
    calculated_cost text,
    storage_duration_days text,
    customer_fio text,
    customer_phone text,
    handover_date text,
    quality_certificate_num text
);

-- 6. Сырые входящие накладные поставщиков
DROP TABLE IF EXISTS public.supplier_invoices_raw CASCADE;
CREATE TABLE public.supplier_invoices_raw (
    doc_guid text,
    doc_number text,
    doc_date text,
    supplier_inn text,
    supplier_kpp text,
    supplier_account text,
    recipient_org_code text,
    total_positions_count text,
    sum_without_vat text,
    vat_sum text,
    total_invoice_sum text,
    payment_due_date text,
    acceptance_act_number text,
    acceptance_status text,
    xml_electronic_doc_copy text,
    discrepancy_details text
);

-- 7. Позиции входящих накладных
DROP TABLE IF EXISTS public.supplier_invoice_items_loose CASCADE;
CREATE TABLE public.supplier_invoice_items_loose (
    row_uid text,
    invoice_guid_ref text,
    item_position_num text,
    supplier_product_code text,
    internal_sku text,
    product_full_name text,
    dosage text,
    producer_country text,
    vat_rate text,
    quantity text,
    producer_price_without_vat text,
    supplier_price_without_vat text,
    retail_markup text,
    declared_vital_register_price text
);

-- 8. Заявки на закупку и дефектура аптечной сети
DROP TABLE IF EXISTS public.pharmacy_defectura_orders CASCADE;
CREATE TABLE public.pharmacy_defectura_orders (
    order_id text,
    request_timestamp text,
    pharmacy_unit_code text,
    department_name text,
    product_name_requested text,
    preferred_producer text,
    current_stock_quantity text,
    average_daily_sales text,
    urgency_level text,
    rejection_counter_last_week text,
    procurement_decision text,
    assigned_purchasing_manager text,
    comments text
);

-- 9. Журнал температур и влажности холодильников и витрин
DROP TABLE IF EXISTS public.cold_chain_telemetry_logs CASCADE;
CREATE TABLE public.cold_chain_telemetry_logs (
    log_id text,
    equipment_serial_number text,
    equipment_type text,
    pharmacy_number text,
    room_zone text,
    recorded_at text,
    temperature_celsius text,
    humidity_percentage text,
    door_opened_seconds text,
    alarm_status text,
    battery_level_percent text,
    raw_sensor_frame text
);

-- 10. Журнал списаний и брака
DROP TABLE IF EXISTS public.damaged_expired_writeoffs CASCADE;
CREATE TABLE public.damaged_expired_writeoffs (
    writeoff_act_id text,
    pharmacy_id text,
    act_date text,
    product_id text,
    product_name text,
    batch_number text,
    writeoff_reason text,
    quantity text,
    cost_price_total text,
    commission_member_1 text,
    commission_member_2 text,
    commission_head text,
    utilization_contractor text,
    destruction_certificate_num text
);

-- 11. Журнал выбытия кодов маркировки Честный Знак (МДЛП)
DROP TABLE IF EXISTS public.mdlp_serialization_logs CASCADE;
CREATE TABLE public.mdlp_serialization_logs (
    event_id text,
    gtin text,
    serial_number text,
    full_datamatrix_raw text,
    operation_type text,
    operation_timestamp text,
    pharmacy_mdlp_id text,
    mdlp_request_guid text,
    mdlp_response_status text,
    error_code text,
    error_description text,
    document_xml_payload text
);

-- 12. Льготные рецепты и реестры ДЛО/ОНЛС
DROP TABLE IF EXISTS public.preferential_prescriptions_registry CASCADE;
CREATE TABLE public.preferential_prescriptions_registry (
    prescription_global_id text,
    series_num text,
    recipe_date text,
    funding_source_code text,
    benefit_category_code text,
    benefit_percentage text,
    clinic_ogrn text,
    doctor_snils text,
    patient_snils text,
    patient_birthdate text,
    mkb10_disease_code text,
    prescribed_inn_code text,
    dispensed_trade_code text,
    dispensed_quantity text,
    reimbursement_amount text,
    mo_status text
);

-- 13. Программа лояльности и скидочные карты клиентов
DROP TABLE IF EXISTS public.customer_loyalty_transactions CASCADE;
CREATE TABLE public.customer_loyalty_transactions (
    loyalty_tx_id text,
    card_number text,
    phone_hash text,
    tx_timestamp text,
    receipt_number text,
    points_earned text,
    points_spent text,
    balance_after_tx text,
    campaign_promo_id text,
    client_gender text,
    client_birth_year text,
    favorite_category text
);

-- 14. Графики смен и табель сотрудников аптек
DROP TABLE IF EXISTS public.staff_timesheet_unnormalized CASCADE;
CREATE TABLE public.staff_timesheet_unnormalized (
    timesheet_row_id text,
    employee_tab_number text,
    employee_fio text,
    post_role text,
    pharmacy_branch_code text,
    shift_date text,
    planned_hours text,
    actual_hours text,
    overtime_hours text,
    night_hours text,
    holiday_hours text,
    sick_leave_flag text,
    vacation_flag text,
    supervisor_approved text
);

-- 15. Аудит действий пользователей в учётной системе (ИС Аптека)
DROP TABLE IF EXISTS public.user_action_audit_raw CASCADE;
CREATE TABLE public.user_action_audit_raw (
    audit_id text,
    session_guid text,
    user_login text,
    client_ip_address text,
    action_type text,
    table_affected text,
    record_key text,
    old_values_dump text,
    new_values_dump text,
    action_timestamp text,
    app_version text
);

-- 16. Аналитическая таблица: ежедневные агрегаты по аптекам (без PK)
DROP TABLE IF EXISTS analytics.daily_pharmacy_sales_denorm CASCADE;
CREATE TABLE analytics.daily_pharmacy_sales_denorm (
    report_date text,
    pharmacy_code text,
    pharmacy_region text,
    total_checks_count text,
    total_gross_revenue text,
    total_cost_goods_sold text,
    total_discount_rub text,
    vital_drugs_revenue text,
    preferential_recipes_count text,
    average_receipt_amount text,
    highest_single_receipt text,
    card_payments_sum text,
    cash_payments_sum text,
    qr_sbp_payments_sum text
);

-- 17. Аналитическая таблица: матрица популярности лекарств и смежных продаж
DROP TABLE IF EXISTS analytics.cross_sale_associations CASCADE;
CREATE TABLE analytics.cross_sale_associations (
    pair_id text,
    primary_drug_name text,
    associated_drug_name text,
    primary_category text,
    associated_category text,
    co_occurrence_count text,
    confidence_score text,
    support_score text,
    seasonal_quarter text,
    suggested_cashier_script text
);

-- 18. Аналитическая таблица: контроль предельных оптовых и розничных цен (ЖНВЛП)
DROP TABLE IF EXISTS analytics.vital_drugs_markup_compliance CASCADE;
CREATE TABLE analytics.vital_drugs_markup_compliance (
    check_event_id text,
    check_date text,
    pharmacy_id text,
    drug_registration_number text,
    trade_name text,
    actual_retail_price text,
    state_limit_retail_price text,
    difference_rub text,
    violation_status text,
    supplier_name text,
    wholesale_price text,
    state_limit_wholesale_price text,
    investigation_status text
);

-- 19. Аналитическая таблица: медленно оборачиваемые остатки (неликвиды)
DROP TABLE IF EXISTS analytics.dead_stock_aging_analysis CASCADE;
CREATE TABLE analytics.dead_stock_aging_analysis (
    analysis_record_id text,
    calculated_on text,
    pharmacy_id text,
    drug_code text,
    drug_name text,
    current_quantity text,
    stock_value_rub text,
    days_without_sales text,
    shelf_life_remaining_days text,
    recommended_discount_pct text,
    recommended_transfer_pharmacy text,
    status text
);

-- 20. Журнал интеграционных обменов с внешними системами (МФО, СБП, 1С, ЕМИАС)
DROP TABLE IF EXISTS analytics.external_integration_queue CASCADE;
CREATE TABLE analytics.external_integration_queue (
    queue_item_id text,
    target_system text,
    message_type text,
    payload_body text,
    created_at text,
    scheduled_at text,
    processed_at text,
    retry_attempts text,
    status text,
    last_error_message text,
    http_status_code text
);

-- 21. Реестр контрактов и договоров с поставщиками
DROP TABLE IF EXISTS analytics.vendor_contracts_register CASCADE;
CREATE TABLE analytics.vendor_contracts_register (
    contract_id text,
    contract_code text,
    vendor_inn text,
    vendor_name text,
    contract_start_date text,
    contract_end_date text,
    payment_deferral_days text,
    rebate_percentage text,
    annual_volume_obligation_rub text,
    current_turnover_rub text,
    manager_fio text,
    penalty_terms_text text
);

-- 22. Аналитика интернет-заказов и бронирования (Apteka / СберЕАптека / Сайт)
DROP TABLE IF EXISTS analytics.ecommerce_reservations_feed CASCADE;
CREATE TABLE analytics.ecommerce_reservations_feed (
    booking_id text,
    source_platform text,
    customer_phone_masked text,
    destination_pharmacy_id text,
    created_datetime text,
    pickup_deadline text,
    assembled_datetime text,
    completed_datetime text,
    booking_status text,
    cancellation_reason text,
    total_items_count text,
    order_sum text
);

-- Наполнение таблиц тестовыми данными (от десятков до тысяч строк с имитацией аптечной сети)

-- 1. raw_pharmacy_transactions (~5000 строк)
INSERT INTO public.raw_pharmacy_transactions
SELECT
    'TX-' || (100000 + s.i)::text,
    'KASSA-' || (1 + (s.i % 5))::text,
    (ARRAY['Иванова М.А.', 'Смирнова О.В.', 'Кузнецова Т.И.', 'Попова Е.С.', 'Васильева Н.П.'])[1 + (s.i % 5)],
    'CARD-' || (1000 + (s.i % 450))::text,
    to_char(NOW() - (s.i || ' minutes')::interval, 'YYYY-MM-DD HH24:MI:SS'),
    (ARRAY['CASH', 'CARD_MIR', 'CARD_VISA', 'SBP_QR', 'LOYALTY_POINTS'])[1 + (s.i % 5)],
    (150.00 + (s.i % 3850) + ((s.i % 99) * 0.01))::text,
    ((s.i % 15) * 10)::text,
    '{"device_id": "ATOL-' || (s.i % 12) || '", "shift_num": ' || (s.i % 300) || ', "items_count": ' || (1 + (s.i % 7)) || '}',
    'FP:' || md5((s.i * 12345)::text),
    CASE WHEN s.i % 10 = 0 THEN 'Возврат по чеку или корректировка' ELSE 'Обычная покупка' END
FROM generate_series(1, 5000) AS s(i);

-- 2. receipt_items_unindexed (~15000 строк)
INSERT INTO public.receipt_items_unindexed
SELECT
    'ITEM-' || s.i::text,
    'REC-GUID-' || (10000 + (s.i % 3000))::text,
    'Аптека №' || (1 + (s.i % 18))::text,
    (ARRAY['Оренбург', 'Орск', 'Бузулук', 'Новотроицк', 'Гай'])[1 + (s.i % 5)],
    'ул. ' || (ARRAY['Советская', 'Ленина', 'Чкалова', 'Победы', 'Мира', 'Гагарина'])[1 + (s.i % 6)] || ', д. ' || (1 + (s.i % 120)),
    'ART-' || (1000 + (s.i % 800))::text,
    (ARRAY['Парацетамол 500мг таб №20', 'Амоксициллин 500мг капс №16', 'Нурофен Экспресс 200мг №12', 'Анальгин 500мг №10', 'Лозартан 50мг №30', 'Омепразол 20мг №28', 'Лизиноприл 10мг №30', 'Ацетилсалициловая к-та 500мг №10', 'Биссопролол 5мг №30', 'Ксилометазолин спрей 0.1% 15мл'])[1 + (s.i % 10)],
    (ARRAY['Озон Фармацевтика', 'Фармстандарт-Лексредства', 'Биннофарм Групп', 'Акрихин ХФК', 'КРКА-РУС', 'Вертекс'])[1 + (s.i % 6)],
    'SER-' || (202400 + (s.i % 450))::text,
    to_char(NOW() + ((100 + (s.i % 800)) || ' days')::interval, 'YYYY-MM-DD'),
    (1 + (s.i % 4))::text,
    (45.50 + (s.i % 650) + ((s.i % 50) * 0.1))::text,
    (ARRAY['10', '20', '0'])[1 + (s.i % 3)],
    ((s.i % 5) * 5.0)::text,
    CASE WHEN s.i % 4 = 0 THEN 'TRUE' ELSE 'FALSE' END,
    CASE WHEN s.i % 4 = 0 THEN '107-1/У №' || (100000 + s.i) ELSE '' END,
    CASE WHEN s.i % 4 = 0 THEN 'Петров С.Н. (Терапевт)' ELSE '' END,
    CASE WHEN s.i % 4 = 0 THEN '123-456-' || (100 + (s.i % 899)) || ' 00' ELSE '' END
FROM generate_series(1, 15000) AS s(i);

-- 3. warehouse_batches_loose (~4000 строк)
INSERT INTO public.warehouse_batches_loose
SELECT
    'BATCH-BC-' || (100000 + s.i)::text,
    (ARRAY['Центральный аптечный склад №1', 'Склад хранения термолабильных ЛС', 'Аптечный склад №2 Орск', 'Склад карантинной зоны'])[1 + (s.i % 4)],
    'ZONE-' || (ARRAY['A', 'B', 'C', 'D', 'FRIDGE'])[1 + (s.i % 5)] || '-' || (1 + (s.i % 20)),
    'DRUG-REF-' || (100 + (s.i % 500))::text,
    '04601234' || (100000 + (s.i % 90000))::text,
    'sgtin-21-' || md5(s.i::text),
    (ARRAY['AVAILABLE', 'RESERVED', 'QUARANTINE', 'EXPIRED_TRANSIT', 'BLOCKED_BY_ROSZDRAV'])[1 + (s.i % 5)],
    (100 + (s.i % 500))::text,
    (10 + (s.i % 250))::text,
    (s.i % 15)::text,
    (30.00 + (s.i % 400))::text,
    (45.00 + (s.i % 600))::text,
    '77' || (10000000 + (s.i % 5000))::text,
    (ARRAY['ЗАО НПК Катрен', 'АО Протек', 'ООО Пульс', 'ООО ФК Гранд Капитал', 'ЗАО СИА Интернейшнл'])[1 + (s.i % 5)],
    'INV-№' || (50000 + s.i)::text,
    to_char(NOW() - ((s.i % 90) || ' days')::interval, 'YYYY-MM-DD'),
    (ARRAY['15-25C (Обычные условия)', '2-8C (Холодильник)', '8-15C (Прохладное место)', 'До 30C'])[1 + (s.i % 4)],
    'GTD-101000/' || (100000 + s.i)
FROM generate_series(1, 4000) AS s(i);

-- 4. price_audit_history (~6000 строк)
INSERT INTO public.price_audit_history
SELECT
    'CHG-' || s.i::text,
    'DRUG-' || (100 + (s.i % 600))::text,
    'Препарат наименование №' || (100 + (s.i % 600)),
    'PHARM-' || (1 + (s.i % 25))::text,
    (100.00 + (s.i % 300))::text,
    (115.00 + (s.i % 320))::text,
    (80.00 + (s.i % 250))::text,
    (92.00 + (s.i % 270))::text,
    (18.5 + (s.i % 12))::text,
    (140.00 + (s.i % 350))::text,
    CASE WHEN s.i % 3 = 0 THEN '1' ELSE '0' END,
    (ARRAY['admin_price', 'operator_kass', 'system_auto_sync', 'manager_fed'])[1 + (s.i % 4)],
    (ARRAY['Плановая переоценка поставщика', 'Изменение НДС', 'Постановление минздрава', 'Округление розничной цены'])[1 + (s.i % 4)],
    to_char(NOW() - ((s.i % 120) || ' hours')::interval, 'YYYY-MM-DD HH24:MI:SS'),
    CASE WHEN s.i % 2 = 0 THEN 'TRUE' ELSE 'FALSE' END
FROM generate_series(1, 6000) AS s(i);

-- 5. compounded_medication_orders (~1200 строк)
INSERT INTO public.compounded_medication_orders
SELECT
    'RPO-ORDER-' || (2024000 + s.i)::text,
    'PRESC-' || (100000 + s.i)::text,
    to_char(NOW() - ((s.i % 60) || ' days')::interval, 'YYYY-MM-DD'),
    (ARRAY['Микстура от кашля 200мл', 'Порошки с димедролом и папаверином №20', 'Мазь ихтиоловая 10% 50г', 'Капли глазные левомицетин 0.25% 10мл', 'Раствор фурацилина 1:5000 400мл'])[1 + (s.i % 5)],
    (ARRAY['Провизор Ковалева А.С.', 'Технолог Морозов И.Д.', 'Фармацевт Павлова В.К.'])[1 + (s.i % 3)],
    'Натрия бромид, Натрия гидрокарбонат, Нашатырно-анисовые капли, Вода очищенная',
    '3.0г, 4.0г, 4.0мл, до 200мл',
    (ARRAY['СТЕРИЛЬНО', 'АСЕПТИЧЕСКИ_ПРИГОТОВЛЕНО', 'НЕ ТРЕБУЕТСЯ'])[1 + (s.i % 3)],
    'Аналитик Сидорова Т.П.',
    (250.00 + (s.i % 350))::text,
    (ARRAY['10', '20', '30'])[1 + (s.i % 3)],
    'Пациент-' || (s.i % 200),
    '+7900' || (1000000 + s.i),
    to_char(NOW() - ((s.i % 55) || ' days')::interval, 'YYYY-MM-DD'),
    'PASSPORT-QC-' || (1000 + s.i)
FROM generate_series(1, 1200) AS s(i);

-- 6. supplier_invoices_raw (~1000 строк)
INSERT INTO public.supplier_invoices_raw
SELECT
    'INV-GUID-' || s.i::text,
    '№SF-' || (10000 + s.i)::text,
    to_char(NOW() - ((s.i % 180) || ' days')::interval, 'YYYY-MM-DD'),
    '77' || (10000000 + (s.i % 500))::text,
    '770101001',
    '40702810900000' || (1000 + (s.i % 200)),
    'ORG-GOSTAPTEKA-56',
    (10 + (s.i % 40))::text,
    (50000.00 + (s.i % 120000))::text,
    (5000.00 + (s.i % 12000))::text,
    (55000.00 + (s.i % 132000))::text,
    to_char(NOW() - ((s.i % 180 - 30) || ' days')::interval, 'YYYY-MM-DD'),
    'ACT-IN-' || (5000 + s.i),
    (ARRAY['ACCEPTED_FULLY', 'ACCEPTED_WITH_DISCREPANCY', 'PENDING_APPROVAL', 'RETURNED'])[1 + (s.i % 4)],
    '<?xml version="1.0" encoding="windows-1251"?><Invoice id="' || s.i || '"><Total>' || (55000 + s.i) || '</Total></Invoice>',
    CASE WHEN s.i % 4 = 1 THEN 'Недостача 2 упаковок по позиции №3' ELSE '' END
FROM generate_series(1, 1000) AS s(i);

-- 7. supplier_invoice_items_loose (~8000 строк)
INSERT INTO public.supplier_invoice_items_loose
SELECT
    'ROW-' || s.i::text,
    'INV-GUID-' || (1 + (s.i % 1000))::text,
    (1 + (s.i % 30))::text,
    'VEND-CODE-' || (1000 + (s.i % 700)),
    'SKU-' || (5000 + (s.i % 600)),
    'Медикамент сортовой №' || (s.i % 500) || ' форма выпуска капсулы',
    '250мг / 5мл',
    (ARRAY['Россия', 'Индия', 'Германия', 'Беларусь', 'Словения', 'Венгрия'])[1 + (s.i % 6)],
    '10%',
    (20 + (s.i % 150))::text,
    (120.00 + (s.i % 300))::text,
    (132.00 + (s.i % 330))::text,
    (12.5)::text,
    (155.00 + (s.i % 350))::text
FROM generate_series(1, 8000) AS s(i);

-- 8. pharmacy_defectura_orders (~2000 строк)
INSERT INTO public.pharmacy_defectura_orders
SELECT
    'DEF-' || s.i::text,
    to_char(NOW() - ((s.i % 45) || ' days')::interval, 'YYYY-MM-DD HH24:MI'),
    'PHARM-' || (1 + (s.i % 30)),
    'Отдел готовых форм №' || (1 + (s.i % 3)),
    (ARRAY['Инсулин гларгин 100ЕД/мл 3мл №5', 'Эутирокс 50мкг №100', 'Азитромицин 500мг №3', 'Канефрон Н таб №60', 'Мексидол 125мг №50', 'Детралекс 1000мг №30'])[1 + (s.i % 6)],
    (ARRAY['Санофи', 'Мерк', 'Озон', 'Бионорика', 'Фармасофт', 'Сервье'])[1 + (s.i % 6)],
    (s.i % 3)::text,
    (4.5 + (s.i % 10))::text,
    (ARRAY['СРОЧНО', 'ВЫСОКАЯ', 'ПЛАНОВАЯ'])[1 + (s.i % 3)],
    (1 + (s.i % 15))::text,
    (ARRAY['Заказано у Катрен', 'Поставлено на ожидание тендера', 'Аналог предложен покупателю', 'Отказ производителя'])[1 + (s.i % 4)],
    'Менеджер Семенов К.В.',
    'Постоянный пациент по льготе или частые отказы на кассе'
FROM generate_series(1, 2000) AS s(i);

-- 9. cold_chain_telemetry_logs (~10000 строк)
INSERT INTO public.cold_chain_telemetry_logs
SELECT
    'LOG-SENSOR-' || s.i::text,
    'SN-REFRIG-' || (100 + (s.i % 40)),
    (ARRAY['Холодильник фармацевтический ХФ-250', 'Термошкаф СТ-140', 'Витрина холодильная Polair', 'Морозильник ММ-180'])[1 + (s.i % 4)],
    'PHARM-' || (1 + (s.i % 25)),
    'Материальная комната хранения №' || (1 + (s.i % 4)),
    to_char(NOW() - (s.i || ' minutes')::interval, 'YYYY-MM-DD HH24:MI:SS'),
    (3.5 + ((s.i % 35) * 0.1))::text,
    (55 + (s.i % 25))::text,
    (CASE WHEN s.i % 30 = 0 THEN 120 ELSE 0 END)::text,
    (CASE WHEN s.i % 500 = 0 THEN 'TEMP_EXCEEDED_WARNING' ELSE 'NORMAL' END),
    (98 - (s.i % 40))::text,
    'HEX:AA5501' || md5(s.i::text)
FROM generate_series(1, 10000) AS s(i);

-- 10. damaged_expired_writeoffs (~1500 строк)
INSERT INTO public.damaged_expired_writeoffs
SELECT
    'ACT-WO-' || (1000 + s.i)::text,
    'PHARM-' || (1 + (s.i % 30)),
    to_char(NOW() - ((s.i % 90) || ' days')::interval, 'YYYY-MM-DD'),
    'DRUG-SKU-' || (100 + (s.i % 400)),
    'Препарат бракованный/просроченный партийный №' || s.i,
    'SER-' || (10000 + s.i),
    (ARRAY['Истечение срока годности', 'Бой ампул при транспортировке', 'Повреждение первичной упаковки', 'Отзыв Росздравнадзора'])[1 + (s.i % 4)],
    (1 + (s.i % 12))::text,
    (150.00 + (s.i % 2500))::text,
    'Зав. аптекой Никитина Л.Г.',
    'Старший провизор Федорова М.И.',
    'Председатель комиссии Архипов В.Д.',
    'ООО ЭкоУтилизация-Оренбург',
    'CERT-DESTR-' || (500 + s.i)
FROM generate_series(1, 1500) AS s(i);

-- 11. mdlp_serialization_logs (~8000 строк)
INSERT INTO public.mdlp_serialization_logs
SELECT
    'MDLP-EVT-' || s.i::text,
    '04607008' || (100000 + (s.i % 50000))::text,
    'SN' || (1000000000 + s.i)::text,
    '010460700812345621' || (1000000000 + s.i) || '91EE0692' || md5(s.i::text),
    (ARRAY['10511_RETAIL_SALE', '10521_DISPENSARY_RECIPE', '10531_MEDICAL_USE', '211_RECEIPT_ACCEPTANCE'])[1 + (s.i % 4)],
    to_char(NOW() - (s.i || ' seconds')::interval, 'YYYY-MM-DD HH24:MI:SS'),
    '00000000' || (1000 + (s.i % 30)),
    'MDLP-DOC-' || md5(s.i::text),
    (ARRAY['PROCESSED_SUCCESS', 'IN_PROCESS', 'REJECTED_ALREADY_DISPENSED', 'FAILED_SIGNATURE_ERROR'])[1 + (s.i % 4)],
    (CASE WHEN s.i % 4 = 2 THEN 'ERROR_15' WHEN s.i % 4 = 3 THEN 'ERROR_401' ELSE '0' END),
    (CASE WHEN s.i % 4 = 2 THEN 'КМ уже выведен из оборота ранее' WHEN s.i % 4 = 3 THEN 'ЭЦП не подтверждена' ELSE 'Успешная фиксация выбытия' END),
    '<mdlp:doc id="' || s.i || '"><result>OK</result></mdlp:doc>'
FROM generate_series(1, 8000) AS s(i);

-- 12. preferential_prescriptions_registry (~3500 строк)
INSERT INTO public.preferential_prescriptions_registry
SELECT
    'REC-GLOB-' || (500000 + s.i)::text,
    '56-24-№' || (100000 + s.i)::text,
    to_char(NOW() - ((s.i % 120) || ' days')::interval, 'YYYY-MM-DD'),
    (ARRAY['ФБ (Федеральный бюджет ОНЛП)', 'РБ (Региональная льгота)', '7ВЗН (Высокозатратные нозологии)'])[1 + (s.i % 3)],
    (ARRAY['010 (Инвалиды 1-2 группы)', '020 (Дети-инвалиды)', '081 (Сахарный диабет)', '082 (Онкология)', '085 (Бронхиальная астма)'])[1 + (s.i % 5)],
    '100%',
    '102560' || (100000 + (s.i % 50))::text,
    '056-123-' || (100 + (s.i % 899)) || ' 22',
    '156-987-' || (100 + (s.i % 899)) || ' 88',
    '19' || (40 + (s.i % 55)) || '-05-12',
    (ARRAY['E10.9 (Сахарный диабет 1 типа)', 'I10 (Гипертония)', 'J45.0 (Астма)', 'C50.9 (Новообразования)', 'G35 (Рассеянный склероз)'])[1 + (s.i % 5)],
    'INN-' || (100 + (s.i % 60)),
    'Торговое название отпущенного медикамента №' || (s.i % 80),
    (1 + (s.i % 5))::text,
    (450.00 + (s.i % 8500))::text,
    (ARRAY['ОПУЩЕН_ПОЛНОСТЬЮ', 'ОТПУЩЕН_ЧАСТИЧНО', 'НА_ОТСРОЧЕННОМ_ОБСЛУЖИВАНИИ'])[1 + (s.i % 3)]
FROM generate_series(1, 3500) AS s(i);

-- 13. customer_loyalty_transactions (~5000 строк)
INSERT INTO public.customer_loyalty_transactions
SELECT
    'LOY-TX-' || s.i::text,
    'CARD-5600-' || (100000 + (s.i % 2500))::text,
    md5('PHONE-CLIENT-' || (s.i % 2500)),
    to_char(NOW() - ((s.i % 100) || ' hours')::interval, 'YYYY-MM-DD HH24:MI:SS'),
    'REC-CHK-' || (10000 + s.i)::text,
    ((s.i % 50) * 5)::text,
    (CASE WHEN s.i % 5 = 0 THEN (s.i % 100) * 3 ELSE 0 END)::text,
    (350 + (s.i % 2000))::text,
    'PROMO-AUTUMN-56',
    (ARRAY['FEMALE', 'MALE', 'UNKNOWN'])[1 + (s.i % 3)],
    (1950 + (s.i % 55))::text,
    (ARRAY['Витамины и БАД', 'Кардиология', 'Косметика и гигиена', 'Простуда и грипп'])[1 + (s.i % 4)]
FROM generate_series(1, 5000) AS s(i);

-- 14. staff_timesheet_unnormalized (~2500 строк)
INSERT INTO public.staff_timesheet_unnormalized
SELECT
    'TS-ROW-' || s.i::text,
    'TAB-№' || (100 + (s.i % 150))::text,
    (ARRAY['Михайлова Анна Петровна', 'Григорьев Сергей Николаевич', 'Романова Ольга Игоревна', 'Алексеева Дарья Владимировна', 'Степанов Игорь Олегович'])[1 + (s.i % 5)],
    (ARRAY['Заведующий аптекой', 'Провизор-рецептар', 'Фармацевт кассир', 'Фасовщик', 'Ночной дежурный провизор'])[1 + (s.i % 5)],
    'PHARM-BRANCH-' || (1 + (s.i % 25)),
    to_char(CURRENT_DATE - ((s.i % 60) || ' days')::interval, 'YYYY-MM-DD'),
    '12.0',
    (ARRAY['12.0', '11.5', '12.0', '8.0', '0.0'])[1 + (s.i % 5)],
    (CASE WHEN s.i % 7 = 0 THEN '2.0' ELSE '0.0' END),
    (CASE WHEN s.i % 5 = 4 THEN '4.0' ELSE '0.0' END),
    '0.0',
    (CASE WHEN s.i % 20 = 0 THEN 'YES' ELSE 'NO' END),
    (CASE WHEN s.i % 35 = 0 THEN 'YES' ELSE 'NO' END),
    'APPROVED_BY_HR'
FROM generate_series(1, 2500) AS s(i);

-- 15. user_action_audit_raw (~7000 строк)
INSERT INTO public.user_action_audit_raw
SELECT
    'AUDIT-' || s.i::text,
    'SESS-' || md5((s.i % 1000)::text),
    'user_apteka_' || (1 + (s.i % 40)),
    '192.168.10.' || (10 + (s.i % 240)),
    (ARRAY['INSERT_CHECK', 'CANCEL_CHECK_POSITION', 'UPDATE_PRICE', 'MANUAL_DISCOUNT_APPLIED', 'STOCK_MOVEMENT_CREATE', 'PRINT_REPLACE_RECEIPT'])[1 + (s.i % 6)],
    (ARRAY['raw_pharmacy_transactions', 'receipt_items_unindexed', 'warehouse_batches_loose', 'damaged_expired_writeoffs'])[1 + (s.i % 4)],
    'REC-PK-' || (s.i % 5000),
    '{"old_val": "data_dump_previous_' || s.i || '"}',
    '{"new_val": "data_dump_modified_' || s.i || '"}',
    to_char(NOW() - (s.i || ' seconds')::interval, 'YYYY-MM-DD HH24:MI:SS'),
    'v4.18.2-build56'
FROM generate_series(1, 7000) AS s(i);

-- 16. analytics.daily_pharmacy_sales_denorm (~3000 строк)
INSERT INTO analytics.daily_pharmacy_sales_denorm
SELECT
    to_char(CURRENT_DATE - ((s.i % 120) || ' days')::interval, 'YYYY-MM-DD'),
    'PHARM-BRANCH-' || (1 + (s.i % 25)),
    (ARRAY['Оренбург-Центральный', 'Оренбург-Северный', 'Орск-Восток', 'Бузулук-Запад', 'Новотроицк'])[1 + (s.i % 5)],
    (120 + (s.i % 450))::text,
    (85000.00 + (s.i % 350000))::text,
    (60000.00 + (s.i % 240000))::text,
    (4500.00 + (s.i % 25000))::text,
    (35000.00 + (s.i % 140000))::text,
    (15 + (s.i % 60))::text,
    (550.00 + (s.i % 900))::text,
    (4500.00 + (s.i % 12000))::text,
    (65000.00 + (s.i % 260000))::text,
    (15000.00 + (s.i % 60000))::text,
    (5000.00 + (s.i % 30000))::text
FROM generate_series(1, 3000) AS s(i);

-- 17. analytics.cross_sale_associations (~1500 строк)
INSERT INTO analytics.cross_sale_associations
SELECT
    'PAIR-' || s.i::text,
    (ARRAY['Антибиотики широкого спектра', 'Нестероидные противовоспалительные', 'Капли в нос сосудосуживающие', 'Сердечные гликозиды', 'Противовирусные капсулы'])[1 + (s.i % 5)],
    (ARRAY['Пробиотики и пребиотики', 'Гастропротекторы (Омепразол)', 'Морская вода для промывания', 'Препараты калия и магния', 'Витамин C 1000мг'])[1 + (s.i % 5)],
    'Фармакотерапия',
    'Сопутствующая защита/профилактика',
    (150 + (s.i % 800))::text,
    (0.65 + ((s.i % 30) * 0.01))::text,
    (0.12 + ((s.i % 20) * 0.01))::text,
    (ARRAY['Q1-ЗИМА', 'Q2-ВЕСНА', 'Q3-ЛЕТО', 'Q4-ОСЕНЬ'])[1 + (s.i % 4)],
    'Предложите покупателю восстанавливающий комплекс для ЖКТ или витаминную поддержку'
FROM generate_series(1, 1500) AS s(i);

-- 18. analytics.vital_drugs_markup_compliance (~2500 строк)
INSERT INTO analytics.vital_drugs_markup_compliance
SELECT
    'COMPL-CHK-' || s.i::text,
    to_char(CURRENT_DATE - ((s.i % 45) || ' days')::interval, 'YYYY-MM-DD'),
    'PHARM-' || (1 + (s.i % 25)),
    'Р N00' || (10000 + (s.i % 8000)),
    'ЖНВЛП Препарат наименование №' || (s.i % 300),
    (145.50 + (s.i % 400))::text,
    (150.00 + (s.i % 400))::text,
    (-4.50 + ((s.i % 8) - 4))::text,
    (CASE WHEN s.i % 17 = 0 THEN 'OVERPRICED_WARNING' ELSE 'COMPLIANT' END),
    (ARRAY['ЗАО НПК Катрен', 'АО Протек', 'ООО Пульс'])[1 + (s.i % 3)],
    (115.00 + (s.i % 320))::text,
    (125.00 + (s.i % 330))::text,
    (CASE WHEN s.i % 17 = 0 THEN 'Назначена внутренняя ревизия' ELSE 'В пределах нормы предельных надбавок' END)
FROM generate_series(1, 2500) AS s(i);

-- 19. analytics.dead_stock_aging_analysis (~2000 строк)
INSERT INTO analytics.dead_stock_aging_analysis
SELECT
    'AGING-' || s.i::text,
    to_char(CURRENT_DATE, 'YYYY-MM-DD'),
    'PHARM-' || (1 + (s.i % 25)),
    'SKU-STOCK-' || (1000 + (s.i % 400)),
    'Редко спрашиваемый препарат ' || (s.i % 300) || ' форма суспензия',
    (5 + (s.i % 25))::text,
    (1500.00 + (s.i % 35000))::text,
    (90 + (s.i % 280))::text,
    (45 + (s.i % 200))::text,
    (ARRAY['10%', '20%', '30%', '50%'])[1 + (s.i % 4)],
    'PHARM-CENTRAL-01 (Аптека с высоким пешеходным трафиком)',
    (ARRAY['CRITICAL_EXPIRING', 'MEDIUM_AGING', 'NORMAL'])[1 + (s.i % 3)]
FROM generate_series(1, 2000) AS s(i);

-- 20. analytics.external_integration_queue (~4000 строк)
INSERT INTO analytics.external_integration_queue
SELECT
    'QUEUE-' || s.i::text,
    (ARRAY['MDLP_CHESTNY_ZNAK', '1C_ENTERPRISE_ERP', 'EMIAS_ORENBURG_SERVICE', 'SBP_BANK_GATEWAY', 'SBER_EAPTEKA_ORDERS'])[1 + (s.i % 5)],
    (ARRAY['EXPORT_SALES_PACK', 'SYNC_RESIDUALS', 'IMPORT_E_RECIPES', 'CONFIRM_PAYMENT', 'RESERVE_HOLD'])[1 + (s.i % 5)],
    '{"packet_id": "' || md5(s.i::text) || '", "retry_count": ' || (s.i % 4) || ', "batch_size": 50}',
    to_char(NOW() - (s.i || ' minutes')::interval, 'YYYY-MM-DD HH24:MI:SS'),
    to_char(NOW() - (s.i || ' minutes')::interval, 'YYYY-MM-DD HH24:MI:SS'),
    to_char(NOW() - ((s.i - 1) || ' minutes')::interval, 'YYYY-MM-DD HH24:MI:SS'),
    (s.i % 4)::text,
    (ARRAY['COMPLETED', 'PENDING_RETRY', 'FAILED_TIMEOUT', 'PROCESSING'])[1 + (s.i % 4)],
    (CASE WHEN s.i % 4 = 2 THEN 'HTTP 504 Gateway Timeout from external partner' ELSE '' END),
    (ARRAY['200', '200', '504', '400'])[1 + (s.i % 4)]
FROM generate_series(1, 4000) AS s(i);

-- 21. analytics.vendor_contracts_register (~500 строк)
INSERT INTO analytics.vendor_contracts_register
SELECT
    'CTR-' || s.i::text,
    'DOG-2024/' || (100 + s.i),
    '77' || (20000000 + s.i)::text,
    'Дистрибьютор Фармацевтический №' || (s.i % 80) || ' ООО',
    '2024-01-01',
    '2025-12-31',
    (ARRAY['14', '30', '45', '60'])[1 + (s.i % 4)],
    (ARRAY['1.5', '2.0', '3.5', '5.0'])[1 + (s.i % 4)],
    (10000000.00 + (s.i % 50000000))::text,
    (6500000.00 + (s.i % 35000000))::text,
    (ARRAY['Власов Д.М.', 'Николаева Е.С.', 'Федосеев А.П.'])[1 + (s.i % 3)],
    'Неустойка 0.1% за каждый день просрочки поставки или оплаты'
FROM generate_series(1, 500) AS s(i);

-- 22. analytics.ecommerce_reservations_feed (~3000 строк)
INSERT INTO analytics.ecommerce_reservations_feed
SELECT
    'ECOM-ORD-' || (800000 + s.i)::text,
    (ARRAY['СБЕР_ЕАПТЕКА', 'APTEKA_RU', 'ЮТЕКА', 'СОБСТВЕННЫЙ_САЙТ_GOSAPTEKA56'])[1 + (s.i % 4)],
    '+7(9**)***-' || (10 + (s.i % 89)) || '-' || (10 + (s.i % 89)),
    'PHARM-' || (1 + (s.i % 25)),
    to_char(NOW() - (s.i || ' minutes')::interval, 'YYYY-MM-DD HH24:MI'),
    to_char(NOW() + (s.i || ' minutes')::interval, 'YYYY-MM-DD HH24:MI'),
    to_char(NOW() - ((s.i % 30) || ' minutes')::interval, 'YYYY-MM-DD HH24:MI'),
    to_char(NOW() - ((s.i % 15) || ' minutes')::interval, 'YYYY-MM-DD HH24:MI'),
    (ARRAY['COMPLETED_PICKED_UP', 'READY_FOR_PICKUP', 'NEW_COLLECTING', 'CANCELLED_BY_CUSTOMER', 'CANCELLED_EXPIRED'])[1 + (s.i % 5)],
    (CASE WHEN s.i % 5 = 3 THEN 'Клиент передумал' WHEN s.i % 5 = 4 THEN 'Истек срок хранения 48 часов' ELSE '' END),
    (1 + (s.i % 8))::text,
    (450.00 + (s.i % 4800))::text
FROM generate_series(1, 3000) AS s(i);

-- Предоставляем полные права только на чтение (SELECT) для пользователя viio_gosapteka56_qa
GRANT SELECT ON ALL TABLES IN SCHEMA public TO viio_gosapteka56_qa;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO viio_gosapteka56_qa;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO viio_gosapteka56_qa;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO viio_gosapteka56_qa;
