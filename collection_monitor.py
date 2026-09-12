# -*- coding: utf-8 -*-
"""
Детектор аномалий сбора: сравнивает число СОБРАННЫХ карточек за прогон (тех,
что распарсились со страниц источника, ДО применения предфильтра PRODUCT_KEYWORDS)
с историей прошлых прогонов и алертит владельцу при резком падении.

Порог на предфильтрованные карточки не ставим специально: их число - нормальная
волатильность (0-6 в день), а вот сколько карточек вообще удалось распарсить со
страниц - куда более надёжный сигнал сбоя парсера/источника (именно так Хабр
месяц молча отдавал 2-4 поста вместо 37).

История - отдельный лист "Мониторинг" в той же таблице-сборщике, где уже лежат
листы "Хабр"/"Телеграм" (см. open_ws в vacancy_collector.py - передаётся сюда
параметром, чтобы не тянуть циклический импорт).

Порог - скользящее среднее за HISTORY_WINDOW прогонов (без сегодняшнего), а не
фиксированное число: объём у источников естественно меняется (новые каналы,
сезонность), фиксированный порог пришлось бы пересматривать руками - а руками
его никто не пересматривает, это тот же провал внимания, который лечит эта
фича. Ноль карточек - алерт всегда, независимо от истории и её длины.

Слабое место скользящего среднего: при ЗАТЯЖНОЙ деградации среднее само
"сползёт" к плохому уровню и через HISTORY_WINDOW прогонов алерт замолчит.
Компромисс осознанный - две недели ежедневных алертов всё равно кардинально
лучше нуля, которые были раньше.

Fail-open: любой сбой самого детектора (лист недоступен, битые значения и
т.п.) не должен ронять прогон сборщика - вызывающий код в vacancy_collector.py
оборачивает check_collection_anomaly в try/except.
"""

import datetime as dt

from alerts import send_owner_alert

SHEET_MONITOR = 'Мониторинг'
MONITOR_COLS = ['Дата', 'Источник', 'Собрано']

HISTORY_WINDOW = 14         # прогонов для скользящего среднего
MIN_HISTORY_FOR_RATIO = 5   # меньше прошлых прогонов в истории - правило "% от среднего" не применяем
DROP_RATIO = 0.5            # алерт, если собрано < 50% среднего за HISTORY_WINDOW
HISTORY_KEEP_ROWS = 60      # хранить на источник (запас поверх HISTORY_WINDOW)


def _history_for_source(ws, source):
    """Прошлые значения 'Собрано' для источника, в порядке от старых к новым."""
    vals = ws.get_all_values()
    if len(vals) <= 1:
        return []
    out = []
    for row in vals[1:]:
        if len(row) >= 3 and row[1] == source:
            try:
                out.append(int(row[2]))
            except (TypeError, ValueError):
                continue
    return out


def _trim_source_history(ws, source):
    """Удаляет самые старые строки источника сверх HISTORY_KEEP_ROWS."""
    vals = ws.get_all_values()
    idx = [i for i, row in enumerate(vals[1:], start=2)
           if len(row) >= 2 and row[1] == source]
    excess = len(idx) - HISTORY_KEEP_ROWS
    if excess <= 0:
        return
    to_delete = sorted(idx[:excess], reverse=True)
    reqs = [{'deleteDimension': {'range': {
                'sheetId': ws.id, 'dimension': 'ROWS',
                'startIndex': rownum - 1, 'endIndex': rownum}}}
            for rownum in to_delete]
    ws.spreadsheet.batch_update({'requests': reqs})


def check_collection_anomaly(gc, counts, open_ws):
    """counts: {'Хабр': int, 'Телеграм': int} - карточки ДО предфильтра.
    open_ws: функция открытия/создания листа из vacancy_collector.py (та же,
    что создаёт листы "Хабр"/"Телеграм" - переиспользуем, не дублируем)."""
    ws = open_ws(gc, SHEET_MONITOR, MONITOR_COLS)
    today = dt.date.today().isoformat()

    for source, collected in counts.items():
        history = _history_for_source(ws, source)
        recent = history[-HISTORY_WINDOW:]

        if collected == 0:
            send_owner_alert(f'📉 Сбор «{source}»: за прогон собрано 0 карточек.')
        elif len(recent) >= MIN_HISTORY_FOR_RATIO:
            avg = sum(recent) / len(recent)
            if avg > 0 and collected < DROP_RATIO * avg:
                send_owner_alert(
                    f'📉 Сбор «{source}»: резкое падение - собрано {collected}, '
                    f'среднее за {len(recent)} прогонов {avg:.1f}.')

        ws.append_row([today, source, collected], value_input_option='USER_ENTERED')

    for source in counts:
        try:
            _trim_source_history(ws, source)
        except Exception as e:
            print(f'  [мониторинг] обрезка истории «{source}» не удалась: {e}')
