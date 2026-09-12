# -*- coding: utf-8 -*-
"""
РАЗОВАЯ миграция: проставляет 'pre-v1' в колонку "Версия промпта" уже
оценённым строкам листа "Вакансии", у которых эта колонка пустая - то есть
строкам, записанным ДО того, как появилось версионирование промпта.

Отделена от обычной логики llm_scorer.py намеренно: обычная запись
(build_row) пишет PROMPT_VERSION только в НОВЫЕ строки; здесь - разовая
правка уже существующих. Идемпотентна: повторный запуск ничего не меняет
(после первого прохода не останется строк "оценена, но версия пуста").

Смысл метки: после миграции пустая "Версия промпта" означает сбой записи,
а не "строка старше версионирования" - pre-v1 однозначно про это говорит.

Запускать вручную один раз (без параметров):
    python backfill_prompt_version.py
"""

from llm_scorer import (CREDENTIALS_FILE, TRACKER_SPREADSHEET_ID, OUT_SHEET,
                         COLUMNS, with_retry, col_to_letter)

import gspread
from google.oauth2.service_account import Credentials

PRE_VERSION_LABEL = 'pre-v1'


def main():
    creds = Credentials.from_service_account_file(
        CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
    gc = gspread.authorize(creds)
    ss = with_retry(lambda: gc.open_by_key(TRACKER_SPREADSHEET_ID),
                    what="открытие Таблицы неудач")
    ws = with_retry(lambda: ss.worksheet(OUT_SHEET), what="открытие листа «Вакансии»")

    vals = with_retry(lambda: ws.get_all_values(), what="чтение листа «Вакансии»")
    if len(vals) <= 1:
        print('Лист пуст - нечего мигрировать.')
        return

    score_idx = COLUMNS.index('Оценка')
    version_idx = COLUMNS.index('Версия промпта')
    version_col = col_to_letter(version_idx)

    updates = []
    for i, row in enumerate(vals[1:], start=2):
        score = row[score_idx] if len(row) > score_idx else ''
        version = row[version_idx] if len(row) > version_idx else ''
        if score.strip() and not version.strip():
            updates.append({'range': f'{version_col}{i}', 'values': [[PRE_VERSION_LABEL]]})

    if not updates:
        print('Нечего помечать - все оценённые строки уже с версией.')
        return

    with_retry(lambda: ws.batch_update(updates, value_input_option='USER_ENTERED'),
              what="проставление pre-v1")
    print(f'Помечено строк как {PRE_VERSION_LABEL}: {len(updates)}')


if __name__ == '__main__':
    main()
