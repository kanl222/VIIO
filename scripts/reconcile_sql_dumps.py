#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт сопоставления двух SQL-справочников лекарственных средств и формирования
итоговой таблицы связей в формате SQL.

Поддерживает интерактивный запрос путей к файлам (с возможностью нажать Enter
для использования путей по умолчанию), а также аргументы командной строки.
"""

import os
import sys
import time
import csv
import re
import argparse
from typing import List, Dict, Tuple, Optional, Set
from collections import defaultdict

# Пути по умолчанию к справочникам проекта
DEFAULT_LOCAL_SQL = "data/gosapteka56/2026-10-27/Архив/drug_202610160923.sql"
DEFAULT_ECP_SQL = "data/gosapteka56/2026-10-27/Архив/drugrls_oas_202610160922.sql"
DEFAULT_OUTPUT_SQL = "data/gosapteka56/reconciled_cross_reference.sql"


# ==============================================================================
# Функции нормализации и парсинга характеристик ЛС
# ==============================================================================

def clean_text(text: Optional[str]) -> str:
    """Очистка текста от спецсимволов, кавычек и лишних пробелов."""
    if not text:
        return ""
    text = re.sub(r'[®™«»"\'~]', ' ', text)
    text = re.sub(r'\b(ооо|зао|оао|пао|ао|гмбх|лтд|ltd|gmbh|inc|llc)\b', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def normalize_dose_value(raw: Optional[str]) -> Optional[str]:
    """
    Приведение дозировки к каноническому виду (например: 250 мкг -> 0.25мг, 100 мг/5 мл -> 20мг/мл).
    """
    if not raw:
        return None
    raw = raw.lower().replace(' ', '').replace(',', '.')

    # 1. Концентрация: 100мг/5мл -> 20мг/мл
    m = re.match(r'^(\d+(?:\.\d+)?)(мг|мкг|г)/(\d+(?:\.\d+)?)(мл)$', raw)
    if m:
        val_top = float(m.group(1))
        unit_top = m.group(2)
        val_bot = float(m.group(3))
        if unit_top == 'мкг':
            val_top /= 1000.0
        elif unit_top == 'г':
            val_top *= 1000.0
        conc = val_top / val_bot
        return f"{round(conc, 4):g}мг/мл"

    # 2. Простая масса: 250мкг -> 0.25мг, 0.02г -> 20мг
    m = re.match(r'^(\d+(?:\.\d+)?)(мкг|мг|г)$', raw)
    if m:
        num = float(m.group(1))
        unit = m.group(2)
        if unit == 'мкг':
            num /= 1000.0
        elif unit == 'г':
            num *= 1000.0
        return f"{round(num, 4):g}мг"

    return raw


def extract_dosage(text: Optional[str]) -> Optional[str]:
    """Извлечение и нормализация дозировки из строки наименования."""
    if not text:
        return None
    text = text.replace(',', '.')

    # Специфические концентрации: 100 мг/5 мл, 20 мкг/доза, 15000 МЕ/мл, 300 ЕД/мл
    m = re.search(r'(\d+(?:\.\d+)?\s*(?:мг|мкг|г|ед|ме|тыс\.?\s*ед|тыс\.?\s*ме)\s*/\s*\d*(?:\.\d+)?\s*(?:мл|доза))', text, re.IGNORECASE)
    if m:
        raw = m.group(1).lower().replace(' ', '')
        return normalize_dose_value(raw)

    # Стандартные массы и проценты: 120 мг, 250 мкг, 0.25 мг, 10%
    m = re.search(r'(\d+(?:\.\d+)?\s*(?:мг/мл|мкг/доза|тыс\.?\s*ме|тыс\.?\s*ед|ме/мл|ед/мл|мг|мкг|г|ед|ме|%))', text, re.IGNORECASE)
    if m:
        raw = m.group(1).lower().replace(' ', '')
        return normalize_dose_value(raw)

    return None


def extract_pack_count(text: Optional[str]) -> Optional[int]:
    """Извлечение количества в упаковке (№30, в„–30, Х112, 10*3 -> 30)."""
    if not text:
        return None
    # Отсекаем хвост регистрационного номера (РУ, ЛП-)
    text_clean = re.split(r'\b(РУ|Р N|ЛП-|ЛСР-|Рег\.:)\b', text, flags=re.IGNORECASE)[0]

    # 1. Шаблоны вида №30, в„–30, N 30, x112
    m = re.search(r'(?:№|в„–|\bn\b|\b[хx]\b)\s*(\d{1,4})', text_clean, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        if 1 <= val <= 2000:
            return val

    # 2. Шаблоны вида 100 таб, 60 капс
    m = re.search(r'(\d{1,4})\s*(?:таб|капс|амп|фл|доз|пак)', text_clean, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        if 1 <= val <= 2000:
            return val

    return None


def parse_sql_line(line: str) -> Optional[List[Optional[str]]]:
    """Быстрый и надежный парсинг одной строки INSERT кортежа через csv.reader."""
    inner = line.strip()
    if inner.startswith('('):
        inner = inner[1:]
    if inner.endswith('),') or inner.endswith(');'):
        inner = inner[:-2]
    elif inner.endswith(')'):
        inner = inner[:-1]
    try:
        row = next(csv.reader([inner], quotechar="'", delimiter=',', skipinitialspace=True))
        return [None if (val is None or val.strip().upper() == 'NULL') else val.strip() for val in row]
    except Exception:
        return None


# ==============================================================================
# Индексация внешнего каталога и поиск соответствий
# ==============================================================================

def index_external_catalog(ecp_sql_path: str, max_records: Optional[int] = None) -> Tuple[
    Dict[Tuple[str, str, int], List[Tuple[int, int]]],
    Dict[Tuple[str, str, int], List[Tuple[int, int]]],
    Dict[Tuple[str, str], List[Tuple[int, int]]],
    Dict[str, int],
    int
]:
    """
    Потоковое чтение и построение многоуровневого индекса ECP/РЛС в оперативной памяти.
    Возвращает:
      - trade_pack_map: (trade, dose, pack) -> [(id, is_deleted)]
      - mnn_pack_map:   (mnn, dose, pack) -> [(id, is_deleted)]
      - trade_dose_map: (trade, dose) -> [(id, is_deleted)]
      - barcode_map:    barcode -> id
      - total_indexed: общее количество прочитанных записей
    """
    print(f"\n[*] Чтение и индексация внешнего справочника ECP: '{ecp_sql_path}'...")
    t0 = time.time()

    trade_pack_map: Dict[Tuple[str, str, int], List[Tuple[int, int]]] = defaultdict(list)
    mnn_pack_map: Dict[Tuple[str, str, int], List[Tuple[int, int]]] = defaultdict(list)
    trade_dose_map: Dict[Tuple[str, str], List[Tuple[int, int]]] = defaultdict(list)
    barcode_map: Dict[str, int] = {}

    total_records = 0
    indexed_with_dose = 0

    with open(ecp_sql_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.startswith("\t ("):
                continue

            total_records += 1
            row = parse_sql_line(line)
            if not row or len(row) < 23 or not row[0] or not row[2]:
                continue

            try:
                ecp_id = int(row[0])
            except ValueError:
                continue

            barcode = row[1] if row[1] and row[1] != 'NULL' else None
            name = row[2]
            mnn = row[5] if len(row) > 5 else None
            trade = row[16] if len(row) > 16 else None
            dose_raw = row[21] if len(row) > 21 else None
            is_deleted = 1 if len(row) > 26 and row[26] and row[26].lower() == 'true' else 0

            # Дозировка и фасовка
            dose = extract_dosage(name) or extract_dosage(dose_raw)
            pack = extract_pack_count(name)

            # Индекс по штрихкоду
            if barcode and len(barcode.strip()) >= 8:
                barcode_map[barcode.strip()] = ecp_id

            if not dose:
                continue

            indexed_with_dose += 1
            cand = (ecp_id, is_deleted)

            if trade:
                tr_c = clean_text(trade).lower()
                if pack:
                    trade_pack_map[(tr_c, dose, pack)].append(cand)
                trade_dose_map[(tr_c, dose)].append(cand)

            if mnn:
                mn_c = clean_text(mnn).lower()
                if pack:
                    mnn_pack_map[(mn_c, dose, pack)].append(cand)

            if total_records % 100000 == 0:
                print(f"  ...обработано {total_records} записей ({time.time()-t0:.1f} сек)")

            if max_records and total_records >= max_records:
                break

    elapsed = time.time() - t0
    print(f"[+] Успешно проиндексировано {total_records} записей ECP ({indexed_with_dose} с дозировкой) за {elapsed:.2f} сек.")
    return trade_pack_map, mnn_pack_map, trade_dose_map, barcode_map, total_records


def pick_best_candidate(candidates: List[Tuple[int, int]]) -> int:
    """
    Выбор наилучшего кандидата из дубликатов:
    Приоритет отдается активным (не удаленным) записям и более свежим регистрациям.
    """
    active = [eid for eid, deleted in candidates if deleted == 0]
    if active:
        return active[-1]  # последняя/наиболее свежая активная запись
    return candidates[-1][0]


def reconcile_catalogs(
    local_sql_path: str,
    trade_pack_map: Dict[Tuple[str, str, int], List[Tuple[int, int]]],
    mnn_pack_map: Dict[Tuple[str, str, int], List[Tuple[int, int]]],
    trade_dose_map: Dict[Tuple[str, str], List[Tuple[int, int]]],
    barcode_map: Dict[str, int],
    max_local: Optional[int] = None
) -> Tuple[List[Tuple[int, int, str, float, str]], int, Dict[str, int]]:
    """
    Сопоставление локального справочника с построенным индексом.
    Возвращает список связей: (id_drug, id_drug_ecp, match_type, confidence, details)
    """
    print(f"\n[*] Чтение и сопоставление локального справочника: '{local_sql_path}'...")
    t0 = time.time()

    matched_pairs: List[Tuple[int, int, str, float, str]] = []
    stats: Dict[str, int] = defaultdict(int)
    total_local = 0
    matched_local_ids: Set[int] = set()

    with open(local_sql_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.startswith("\t ("):
                continue

            total_local += 1
            row = parse_sql_line(line)
            if not row or not row[0]:
                continue

            # Если в дампе нет id (как в drug.sql), используем порядковый номер строки (1-based)
            drug_id = total_local
            name = row[0]

            # Извлечение структурированных характеристик
            c_name = clean_text(name)
            dose = extract_dosage(name)
            n_fv = int(row[26]) if len(row) > 26 and row[26] and row[26].isdigit() else None
            pack = extract_pack_count(name) or n_fv

            # Выделение МНН и торгового наименования
            parts = [p.strip() for p in name.split(',') if p.strip()]
            if len(parts) >= 2:
                mnn_kw = clean_text(parts[0]).lower()
                trade_kw = clean_text(parts[1]).lower()
            else:
                words = re.findall(r'[а-яёa-z0-9\-]+', c_name.lower())
                trade_kw = words[0] if words else ""
                mnn_kw = trade_kw

            if not dose:
                stats['NO_DOSE'] += 1
                continue

            # --- Уровень 1: Точное Торговое + Каноническая Доза + Фасовка (0.98) ---
            if trade_kw and pack and (trade_kw, dose, pack) in trade_pack_map:
                best_eid = pick_best_candidate(trade_pack_map[(trade_kw, dose, pack)])
                matched_pairs.append((drug_id, best_eid, 'EXACT_TRADE_DOSE_PACK', 0.98, f'{trade_kw} | {dose} | {pack}'))
                matched_local_ids.add(drug_id)
                stats['EXACT_TRADE_DOSE_PACK'] += 1
                continue

            # --- Уровень 2: Точное МНН + Каноническая Доза + Фасовка (0.92) ---
            if mnn_kw and pack and (mnn_kw, dose, pack) in mnn_pack_map:
                best_eid = pick_best_candidate(mnn_pack_map[(mnn_kw, dose, pack)])
                matched_pairs.append((drug_id, best_eid, 'EXACT_MNN_DOSE_PACK', 0.92, f'{mnn_kw} | {dose} | {pack}'))
                matched_local_ids.add(drug_id)
                stats['EXACT_MNN_DOSE_PACK'] += 1
                continue

            # --- Уровень 3: Точное Торговое + Каноническая Доза (0.88) ---
            if trade_kw and (trade_kw, dose) in trade_dose_map:
                best_eid = pick_best_candidate(trade_dose_map[(trade_kw, dose)])
                matched_pairs.append((drug_id, best_eid, 'EXACT_TRADE_DOSE', 0.88, f'{trade_kw} | {dose}'))
                matched_local_ids.add(drug_id)
                stats['EXACT_TRADE_DOSE'] += 1
                continue

            stats['UNMATCHED'] += 1

            if max_local and total_local >= max_local:
                break

    elapsed = time.time() - t0
    print(f"[+] Сопоставлено {len(matched_pairs)} записей из {total_local} ({len(matched_pairs)/max(1, total_local)*100:.1f}%) за {elapsed:.2f} сек.")
    return matched_pairs, total_local, stats


# ==============================================================================
# Генерация результирующего SQL-файла
# ==============================================================================

def write_sql_cross_reference(
    output_sql_path: str,
    matched_pairs: List[Tuple[int, int, str, float, str]],
    batch_size: int = 1000
):
    """
    Формирование готового автономного SQL скрипта создания и наполнения таблицы связей.
    Совместим как с PostgreSQL, так и со SQLite.
    """
    print(f"\n[*] Формирование SQL файла таблицы связей: '{output_sql_path}'...")
    os.makedirs(os.path.dirname(os.path.abspath(output_sql_path)), exist_ok=True)
    t0 = time.time()

    with open(output_sql_path, "w", encoding="utf-8") as f:
        # Заголовок скрипта
        f.write("-- ====================================================================\n")
        f.write("-- ТАБЛИЦА СВЯЗЕЙ СПРАВОЧНИКОВ ЛС (PublicDrugAndEcpDrugRlsOas)\n")
        f.write(f"-- Сгенерировано: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"-- Всего сопоставлено позиций: {len(matched_pairs)}\n")
        f.write("-- ====================================================================\n\n")

        # DDL создание таблицы
        f.write("-- Создание таблицы связей\n")
        f.write("CREATE TABLE IF NOT EXISTS PublicDrugAndEcpDrugRlsOas (\n")
        f.write("    id_drug INTEGER NOT NULL,\n")
        f.write("    id_drug_ecp INTEGER NOT NULL,\n")
        f.write("    deleted INTEGER DEFAULT 0,\n")
        f.write("    match_type VARCHAR(50),\n")
        f.write("    confidence REAL,\n")
        f.write("    match_details TEXT,\n")
        f.write("    created_user_id INTEGER DEFAULT 1,\n")
        f.write("    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n")
        f.write("    PRIMARY KEY (id_drug, id_drug_ecp)\n")
        f.write(");\n\n")

        # Индексы для быстрого поиска
        f.write("-- Индексы для оптимизации выборок\n")
        f.write("CREATE INDEX IF NOT EXISTS idx_xref_id_drug ON PublicDrugAndEcpDrugRlsOas (id_drug);\n")
        f.write("CREATE INDEX IF NOT EXISTS idx_xref_id_drug_ecp ON PublicDrugAndEcpDrugRlsOas (id_drug_ecp);\n")
        f.write("CREATE INDEX IF NOT EXISTS idx_xref_match_type ON PublicDrugAndEcpDrugRlsOas (match_type);\n\n")

        # Пакетная вставка данных (INSERT INTO)
        f.write("-- Пакетная вставка сопоставленных связей\n")
        total_batches = (len(matched_pairs) + batch_size - 1) // batch_size
        for b_idx in range(0, len(matched_pairs), batch_size):
            batch = matched_pairs[b_idx : b_idx + batch_size]
            f.write("INSERT INTO PublicDrugAndEcpDrugRlsOas (id_drug, id_drug_ecp, deleted, match_type, confidence, match_details, created_user_id, created_date) VALUES\n")
            value_lines = []
            for item in batch:
                id_drug, id_ecp, m_type, conf, det = item
                safe_det = det.replace("'", "''")
                value_lines.append(f"\t({id_drug}, {id_ecp}, 0, '{m_type}', {conf:.2f}, '{safe_det}', 1, CURRENT_TIMESTAMP)")
            f.write(",\n".join(value_lines))
            f.write(";\n\n")

    file_size_mb = os.path.getsize(output_sql_path) / (1024 * 1024)
    elapsed = time.time() - t0
    print(f"[+] SQL файл успешно сохранен: {output_sql_path} ({file_size_mb:.2f} МБ) за {elapsed:.2f} сек.")


# ==============================================================================
# Интерактивный диалог и запуск
# ==============================================================================

def prompt_user_path(prompt_text: str, default_path: str) -> str:
    """Запрашивает у пользователя путь к файлу с отображением дефолтного значения."""
    try:
        user_input = input(f"{prompt_text}\n[По умолчанию: {default_path}]: ").strip()
        if not user_input:
            return default_path
        # Очистка кавычек, если путь вставлен через Drag-and-Drop в терминал
        cleaned = user_input.strip("\"'")
        return cleaned if cleaned else default_path
    except (EOFError, KeyboardInterrupt):
        print("\nИспользуется путь по умолчанию.")
        return default_path


def main():
    parser = argparse.ArgumentParser(
        description="Сопоставление двух SQL дампов лекарственных справочников и создание таблицы связей SQL."
    )
    parser.add_argument("--local", type=str, default=None, help="Путь к SQL дампу локального справочника (public.drug)")
    parser.add_argument("--external", type=str, default=None, help="Путь к SQL дампу справочника ECP (ecp.drugrls_oas)")
    parser.add_argument("--output", type=str, default=None, help="Путь для сохранения результирующего SQL файла")
    parser.add_argument("--max-ecp", type=int, default=None, help="Ограничение количества загружаемых записей ECP")
    parser.add_argument("--max-local", type=int, default=None, help="Ограничение количества сопоставляемых локальных записей")
    parser.add_argument("--non-interactive", action="store_true", help="Не запрашивать пути интерактивно, использовать аргументы или дефолты")

    args = parser.parse_args()

    print("====================================================================")
    print("   МАСТЕР СОПОСТАВЛЕНИЯ СПРАВОЧНИКОВ ЛС И ГЕНЕРАЦИИ ТАБЛИЦЫ СВЯЗЕЙ   ")
    print("====================================================================")

    # Определение путей: аргументы CLI -> интерактивный запрос -> значения по умолчанию
    if args.non_interactive or (args.local and args.external and args.output):
        local_sql = args.local or DEFAULT_LOCAL_SQL
        ecp_sql = args.external or DEFAULT_ECP_SQL
        output_sql = args.output or DEFAULT_OUTPUT_SQL
    else:
        print("\nУкажите пути к файлам дампа или нажмите [ENTER] для путей по умолчанию:")
        local_sql = prompt_user_path("1. Введите путь к SQL дампу локального справочника (public.drug)", args.local or DEFAULT_LOCAL_SQL)
        ecp_sql = prompt_user_path("2. Введите путь к SQL дампу внешнего справочника ECP (ecp.drugrls_oas)", args.external or DEFAULT_ECP_SQL)
        output_sql = prompt_user_path("3. Введите путь для сохранения результирующего файла связей (.sql)", args.output or DEFAULT_OUTPUT_SQL)

    # Проверка существования входных файлов
    if not os.path.isfile(local_sql):
        print(f"\n[!] ОШИБКА: Файл локального справочника не найден: '{local_sql}'")
        sys.exit(1)

    if not os.path.isfile(ecp_sql):
        print(f"\n[!] ОШИБКА: Файл внешнего справочника ECP не найден: '{ecp_sql}'")
        sys.exit(1)

    print("\nПараметры выполнения:")
    print(f"  • Локальный справочник: {local_sql}")
    print(f"  • Справочник ECP:       {ecp_sql}")
    print(f"  • Итоговый SQL файл:    {output_sql}")

    t_total_start = time.time()

    # 1. Индексация ECP
    trade_pack_map, mnn_pack_map, trade_dose_map, barcode_map, total_ecp = index_external_catalog(
        ecp_sql, max_records=args.max_ecp
    )

    # 2. Сопоставление с локальным каталогом
    matched_pairs, total_local, stats = reconcile_catalogs(
        local_sql, trade_pack_map, mnn_pack_map, trade_dose_map, barcode_map, max_local=args.max_local
    )

    # 3. Генерация SQL файла
    write_sql_cross_reference(output_sql, matched_pairs)

    total_time = time.time() - t_total_start

    # Итоговый отчет
    print("\n====================================================================")
    print("ИТОГИ СОПОСТАВЛЕНИЯ СПРАВОЧНИКОВ:")
    print(f"  • Записей в локальном справочнике: {total_local}")
    print(f"  • Записей во внешнем каталоге ECP: {total_ecp}")
    print(f"  • Всего установлено связей:        {len(matched_pairs)} ({len(matched_pairs)/max(1, total_local)*100:.1f}% покрытие)")
    print("  • Распределение по точности:")
    print(f"      - EXACT_TRADE_DOSE_PACK (0.98): {stats['EXACT_TRADE_DOSE_PACK']} шт.")
    print(f"      - EXACT_MNN_DOSE_PACK   (0.92): {stats['EXACT_MNN_DOSE_PACK']} шт.")
    print(f"      - EXACT_TRADE_DOSE      (0.88): {stats['EXACT_TRADE_DOSE']} шт.")
    print(f"  • Не сопоставлено (нет дозы/пары):  {stats['UNMATCHED'] + stats['NO_DOSE']} шт.")
    print(f"  • Общее время работы:              {total_time:.1f} сек.")
    print(f"  • Результирующий SQL файл:         {output_sql}")
    print("====================================================================")


if __name__ == '__main__':
    main()
