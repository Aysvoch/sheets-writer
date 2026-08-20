# -*- coding: utf-8 -*-
"""
ЗВЕНО 2: LLM-оценка вакансий -> чистый лист "Вакансии" в "Таблице неудач".

Конвейер:
  читает сырьё из листов "Хабр"/"Телеграм" (файл-сборщик) ->
  по каждой НОВОЙ вакансии зовёт LLM (через OpenRouter) ->
  LLM извлекает поля + ставит оценку 0-10 + сухой вердикт ->
  пишет строку в лист "Вакансии" (в "Таблице неудач") с подсветкой ->
  шлёт в телеграм счётчик новых (если задан бот).

ЗАЩИТА ТАБЛИЦ (важно):
  - пишем ТОЛЬКО в лист "Вакансии". Трекер/Дашборд/Справочник не открываются на запись.
  - только дозапись новых строк (дедуп по source_id). Ничего не перезаписываем.
  - LLM не пишет в таблицу - он лишь возвращает JSON; строку формирует и пишет код.
  - кривой ответ LLM ловится в try/except: вакансия помечается, прогон не падает.

Значения Грейд/Формат/Опыт/Источник LLM выдаёт СТРОГО из твоего Справочника,
чтобы поля совпадали с выпадающими списками Трекера (для будущего переноса).

Секретов в коде нет: всё из .env.
"""

import os
import re
import json
import time
import datetime as dt

import requests
import gspread
from google.oauth2.service_account import Credentials

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ==========================================================================
# СЕКРЕТЫ / ПРИВЯЗКИ
# ==========================================================================
CREDENTIALS_FILE = os.getenv('CREDENTIALS_FILE', 'credentials.json')
RAW_SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '')            # файл-сборщик (сырьё)
TRACKER_SPREADSHEET_ID = os.getenv('TRACKER_SPREADSHEET_ID', '')  # "Таблица неудач"
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.getenv('TG_CHAT_ID', '')

# ==========================================================================
# ПЕРЕКЛЮЧАТЕЛЬ МОДЕЛИ  (раскомментируй нужную строку)
# ==========================================================================
# openrouter/free - авто-роутер бесплатных моделей (лимит ~200/день, 429 при превышении).
# deepseek/deepseek-v4-flash - платная, дёшево (~центы), без жёстких лимитов - для полного прогона.
# LLM_MODEL = 'openrouter/free'                    # бесплатно (медленно, лимиты)
LLM_MODEL = 'deepseek/deepseek-v4-flash'           # ДЁШЕВО, без лимитов (нужен баланс) - по умолчанию
# LLM_MODEL = 'anthropic/claude-3.5-haiku'         # Claude, точнее (дороже)

# ==========================================================================
# НАСТРОЙКИ
# ==========================================================================
RAW_SHEETS = ['Хабр', 'Телеграм']       # откуда читаем сырьё
OUT_SHEET = 'Вакансии'                   # куда пишем чистое (в Таблице неудач)
SLEEP_LLM = 0.6                          # пауза между вызовами LLM
MAX_PER_RUN = 0                          # 0 = без лимита; >0 = не больше N за прогон (для теста)

# Отсечка по свежести: не оценивать вакансии старше N дней.
# 0 = не отсекать. Дата не распозналась -> НЕ отсекаем (лучше лишняя, чем потерянная).
MAX_AGE_DAYS = 10

# Страховочный потолок листа "Вакансии": если после подчистки старья строк
# всё ещё больше - обрезаем лишние снизу (см. cleanup_old_rows).
MAX_ROWS = 150

_MONTHS = {'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
           'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12}

def parse_date(s):
    """Понимает ISO (2026-08-12, телеграм) и '13 августа' (Хабр). None если не распознал."""
    s = (s or '').strip().lower()
    if not s:
        return None
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.match(r'(\d{1,2})\s+([а-яё]+)', s)
    if m and m.group(2) in _MONTHS:
        d, mon = int(m.group(1)), _MONTHS[m.group(2)]
        today = dt.date.today()
        try:
            cand = dt.date(today.year, mon, d)
        except ValueError:
            return None
        if cand > today:                 # дата "в будущем" -> это прошлый год
            cand = dt.date(today.year - 1, mon, d)
        return cand
    return None

def too_old(published):
    """True если вакансия старше MAX_AGE_DAYS. Нераспознанная дата -> False (не режем)."""
    if not MAX_AGE_DAYS:
        return False
    d = parse_date(published)
    if d is None:
        return False
    return (dt.date.today() - d).days > MAX_AGE_DAYS

# Бигтехи -> цвет строки (RGB 0..1). Подсветка в листе "Вакансии".
BIG_TECH_COLORS = {
    'яндекс': (1.00, 0.95, 0.80), 'ozon': (0.87, 0.92, 0.97), 'озон': (0.87, 0.92, 0.97),
    'vk': (0.84, 0.89, 1.00), 'вконтакте': (0.84, 0.89, 1.00),
    'т-банк': (1.00, 0.90, 0.85), 'тинькофф': (1.00, 0.90, 0.85), 'tbank': (1.00, 0.90, 0.85),
    'сбер': (0.86, 0.94, 0.86), 'sber': (0.86, 0.94, 0.86),
    'avito': (0.90, 0.94, 0.85), 'авито': (0.90, 0.94, 0.85),
    'мтс': (1.00, 0.88, 0.90), 'альфа': (0.96, 0.85, 0.85),
}

# Допустимые значения ИЗ ТВОЕГО СПРАВОЧНИКА - LLM выбирает только из них.
V_GRADE = ['Intern', 'Junior', 'Junior+', 'Middle', 'Senior', 'Lead', 'не указано']
V_EXP = ['Без опыта', '1-3 года', '3-6 лет', '6+ лет', 'не указано']
V_FORMAT = ['Офис', 'Удалёнка', 'Гибрид', 'не указано']

# Колонки листа "Вакансии". source_id - технический ключ дедупа (колонка A,
# скрыта в UI, но остаётся в данных). Просмотрено - чекбокс, последняя колонка,
# чтобы не сбивать чтение данных и дедуп по колонке A.
COLUMNS = ['source_id', 'Компания', 'Должность', 'Опыт (треб.)', 'Грейд',
           'Источник', 'Формат', 'ЗП вилка', 'Локация', 'Оценка',
           'Вердикт', 'Опубликовано', 'Ссылка', 'Комментарий', 'Просмотрено']

# ==========================================================================
# ПРОФИЛЬ КАНДИДАТА для промпта (правь под себя)
# ==========================================================================
CANDIDATE_PROFILE = """Кандидат: переход в продакт-менеджмент из операционного/проектного
менеджмента (5+ лет). Учится на продакт-менеджера в Школе 21 (Сбер).
Ищет: junior / стажировку / позиции без опыта или до 1-3 лет в продукте.
Локация: Казань, Москва, удалёнка (в другие города - только со стажировкой/релокацией).
Цель - первая роль в продукте, не Senior/Lead."""

SYSTEM_PROMPT = f"""Ты - строгий ассистент по подбору вакансий. Оцениваешь вакансию
на соответствие профилю кандидата. Работай ТОЛЬКО с фактами из текста вакансии.
ЗАПРЕЩЕНО: выдумывать данные, приукрашивать, добавлять то, чего нет в тексте.
Если поля нет в тексте - ставь "не указано". Вердикт - сухой и конкретный,
без похвал и воды, 1 короткое предложение.

Профиль кандидата:
{CANDIDATE_PROFILE}

Верни СТРОГО JSON без пояснений и markdown, ровно эти ключи:
{{
  "is_vacancy": "true если это ОДНА конкретная вакансия; false если это подборка/
                дайджест из нескольких вакансий, инфопост, реклама или не вакансия",
  "company": "название компании или 'не указано'",
  "title": "должность или 'не указано'",
  "experience": "одно из: {V_EXP}",
  "grade": "одно из: {V_GRADE}",
  "format": "одно из: {V_FORMAT}",
  "salary": "вилка как в тексте (например '80000-120000 RUB') или 'не указано'",
  "salary_min": "минимум зарплаты числом в рублях, или null если не указано/не в рублях",
  "location": "город/условия или 'не указано'",
  "score": "целое 0-10, насколько подходит кандидату (10=идеально, 0=совсем нет)",
  "verdict": "1 сухое предложение почему такая оценка, максимум 25 слов"
}}
Оценку занижай для Senior/Lead, требований 5+ лет, нерелевантных доменов;
повышай для junior/intern/стажировок в продукте с удалёнкой или в Казани/Москве."""

# ==========================================================================
# LLM через OpenRouter
# ==========================================================================
def llm_evaluate(vacancy_text):
    """Возвращает dict полей или None при ошибке."""
    body = {
        'model': LLM_MODEL,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': f'Текст вакансии:\n{vacancy_text[:4000]}'},
        ],
        'temperature': 0,               # без фантазии - максимально детерминированно
        'max_tokens': 400,
    }
    headers = {
        'Authorization': f'Bearer {OPENROUTER_API_KEY}',
        'Content-Type': 'application/json',
        'X-Title': 'vacancy-llm',
    }
    try:
        r = requests.post('https://openrouter.ai/api/v1/chat/completions',
                          json=body, headers=headers, timeout=60)
        if r.status_code == 429:                 # rate limit бесплатного тира
            print('    [429] лимит запросов - жду 20с и повторяю...')
            time.sleep(20)
            r = requests.post('https://openrouter.ai/api/v1/chat/completions',
                              json=body, headers=headers, timeout=60)
        time.sleep(SLEEP_LLM)
        r.raise_for_status()
        j = r.json()
        choices = j.get('choices') or []
        if not choices:
            print(f'    [LLM пусто] нет choices в ответе')
            return None
        msg = choices[0].get('message', {}) or {}
        content = msg.get('content') or msg.get('reasoning') or ''
        return parse_llm_json(content)
    except Exception as e:
        print(f'    [LLM ошибка] {e}')
        return None

def parse_llm_json(content):
    """Достаёт JSON даже если модель обернула его в markdown/текст. None-safe."""
    if not content or not isinstance(content, str):
        return None
    m = re.search(r'\{.*\}', content, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

# ==========================================================================
# Нормализация под Справочник (чтобы совпадало с выпадающими списками)
# ==========================================================================
def _norm(s):
    return str(s).strip().lower().replace('ё', 'е')

# синонимы -> каноничное значение Справочника (для формата/грейда)
_SYNONYMS = {
    'удаленка': 'Удалёнка', 'удаленно': 'Удалёнка', 'remote': 'Удалёнка',
    'можно удаленно': 'Удалёнка', 'офис': 'Офис', 'on-site': 'Офис', 'onsite': 'Офис',
    'гибрид': 'Гибрид', 'hybrid': 'Гибрид',
    'интерн': 'Intern', 'intern': 'Intern', 'стажер': 'Intern', 'стажёр': 'Intern',
    'джуниор': 'Junior', 'джун': 'Junior',
}

def pick(value, allowed, default='не указано'):
    if not value:
        return default
    nv = _norm(value)
    # прямое совпадение с допустимым значением (без учёта ё/регистра)
    for a in allowed:
        if nv == _norm(a):
            return a
    # синоним -> каноничное (если оно вообще в списке допустимых)
    if nv in _SYNONYMS and _SYNONYMS[nv] in allowed:
        return _SYNONYMS[nv]
    return default

# ==========================================================================
# Retry с экспоненциальной задержкой (для нестабильных внешних сервисов)
# ==========================================================================
def with_retry(fn, tries=5, base_delay=2, what="операция"):
    """До 5 попыток с паузами 2/4/8/16с. После последней - падаем с понятной ошибкой."""
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == tries:
                print(f"  [retry] {what}: не удалось после {tries} попыток: {e}")
                raise
            delay = base_delay * (2 ** (attempt - 1))
            print(f"  [retry] {what}: попытка {attempt} не удалась ({e}), "
                  f"жду {delay}с и повторяю...")
            time.sleep(delay)

# ==========================================================================
# Google Sheets
# ==========================================================================
def gc_client():
    try:
        creds = Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        return gspread.authorize(creds)
    except Exception as e:
        raise SystemExit(
            f"Ошибка подключения к Google Sheets: {e}. "
            f"Проверь credentials.json и доступ к таблице."
        )

def read_raw(gc):
    """Читает обе вкладки сырья, возвращает список (source_id, текст_для_LLM, дата, ссылка)."""
    ss = with_retry(lambda: gc.open_by_key(RAW_SPREADSHEET_ID),
                    what="открытие таблицы-сборщика")
    items = []
    for name in RAW_SHEETS:
        try:
            ws = ss.worksheet(name)
        except gspread.WorksheetNotFound:
            continue
        rows = with_retry(lambda ws=ws, name=name: ws.get_all_records(),
                          what=f"чтение листа «{name}»")   # list of dict по заголовкам
        for row in rows:
            sid = str(row.get('source_id', '')).strip()
            if not sid:
                continue
            if name == 'Хабр':
                text = (f"{row.get('Должность','')} | {row.get('Компания','')} | "
                        f"{row.get('Локация','')} | грейд {row.get('Грейд','')} | "
                        f"вилка {row.get('Вилка','')}")
                src = 'Хабр'
            else:
                text = row.get('Текст', '')
                src = f"Telegram/{row.get('Канал','')}"
            items.append({
                'source_id': sid, 'text': text, 'src': src,
                'published': row.get('Опубликовано', ''),
                'url': row.get('Ссылка', ''),
            })
    return items

def open_out(gc):
    """Открывает/создаёт лист 'Вакансии' в Таблице неудач. Трекер и пр. НЕ трогаем."""
    ss = with_retry(lambda: gc.open_by_key(TRACKER_SPREADSHEET_ID),
                    what="открытие Таблицы неудач")
    try:
        ws = ss.worksheet(OUT_SHEET)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=OUT_SHEET, rows=2000, cols=len(COLUMNS))
    vals = with_retry(lambda: ws.get_all_values(), what="чтение листа «Вакансии»")
    if not vals or vals[0][:len(COLUMNS)] != COLUMNS:
        with_retry(lambda: ws.update([COLUMNS], 'A1'), what="запись заголовка")
        with_retry(lambda: ws.freeze(rows=1), what="закрепление шапки")
    return ws

def existing_ids(ws):
    vals = with_retry(lambda: ws.get_all_values(), what="чтение существующих ID")
    return {r[0] for r in vals[1:] if r and r[0]} if len(vals) > 1 else set()

def build_row(item, data):
    def g(key, default='не указано'):
        v = data.get(key)
        return v if v not in (None, '') else default
    company = g('company')
    grade = pick(data.get('grade'), V_GRADE)
    exp = pick(data.get('experience'), V_EXP)
    fmt = pick(data.get('format'), V_FORMAT)
    verdict = str(g('verdict', ''))
    if len(verdict) > 200:                       # ровные строки: длинный вердикт обрезаем
        verdict = verdict[:200].rstrip() + '…'
    return [
        item['source_id'], company, g('title'),
        exp, grade, item['src'], fmt, g('salary'), g('location'),
        g('score', ''), verdict,
        item['published'], item['url'], '', '',
    ]

def color_for(company):
    c = (company or '').lower()          # None-safe: пустая компания не роняет
    if not c:
        return None
    for key, rgb in BIG_TECH_COLORS.items():
        if key in c:
            return rgb
    return None

def fill_row(ws, rownum, rgb):
    last = chr(ord('A') + len(COLUMNS) - 1)
    with_retry(lambda: ws.format(
        f'A{rownum}:{last}{rownum}',
        {'backgroundColor': {'red': rgb[0], 'green': rgb[1], 'blue': rgb[2]}}
    ), what="подсветка строки")

def style_sheet(ws, n_rows):
    """Оформление под Дашборд: тёмная шапка, границы, ширины, закрепление, автофильтр."""
    last = chr(ord('A') + len(COLUMNS) - 1)
    # тёмная шапка с белым жирным текстом (как заголовки Дашборда)
    ws.format(f'A1:{last}1', {
        'backgroundColor': {'red': 0.17, 'green': 0.24, 'blue': 0.31},
        'textFormat': {'bold': True,
                       'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
        'horizontalAlignment': 'CENTER',
        'verticalAlignment': 'MIDDLE',
    })
    ws.freeze(rows=1)
    try:
        ws.set_basic_filter(f'A1:{last}{max(n_rows,1)+1}')
    except Exception:
        pass
    # ширины колонок под содержимое (через batch-запрос к Sheets API)
    # source_id, Компания, Должность, Опыт, Грейд, Источник, Формат, ЗП вилка,
    # Локация, Оценка, Вердикт, Опубликовано, Ссылка, Комментарий, Просмотрено
    widths = [80, 130, 240, 90, 70, 110, 90, 130, 150, 60, 320, 100, 60, 200, 90]
    reqs = []
    sid = ws.id
    for i, w in enumerate(widths):
        if w:
            reqs.append({'updateDimensionProperties': {
                'range': {'sheetId': sid, 'dimension': 'COLUMNS',
                          'startIndex': i, 'endIndex': i + 1},
                'properties': {'pixelSize': w}, 'fields': 'pixelSize'}})
    # source_id остаётся в данных (дедуп), но скрыт от глаз в UI
    reqs.append({'updateDimensionProperties': {
        'range': {'sheetId': sid, 'dimension': 'COLUMNS',
                  'startIndex': COLUMNS.index('source_id'),
                  'endIndex': COLUMNS.index('source_id') + 1},
        'properties': {'hiddenByUser': True}, 'fields': 'hiddenByUser'}})
    # перенос текста в колонках Должность и Вердикт, выравнивание вверх
    for ci in (COLUMNS.index('Должность'), COLUMNS.index('Вердикт')):
        reqs.append({'repeatCell': {
            'range': {'sheetId': sid, 'startRowIndex': 1,
                      'startColumnIndex': ci, 'endColumnIndex': ci + 1},
            'cell': {'userEnteredFormat': {'wrapStrategy': 'WRAP',
                                           'verticalAlignment': 'TOP'}},
            'fields': 'userEnteredFormat(wrapStrategy,verticalAlignment)'}})
    # чекбоксы в колонке "Просмотрено" (данные + запас до конца текущего диапазона)
    seen_idx = COLUMNS.index('Просмотрено')
    reqs.append({'setDataValidation': {
        'range': {'sheetId': sid, 'startRowIndex': 1, 'endRowIndex': max(n_rows, 1) + 1,
                  'startColumnIndex': seen_idx, 'endColumnIndex': seen_idx + 1},
        'rule': {'condition': {'type': 'BOOLEAN'}, 'strict': True}}})
    # сортировка: непросмотренные (чекбокс снят) сверху, внутри - по Оценке убыв.
    # Сортируем весь диапазон строк целиком -> Комментарий и подсветка едут со строкой.
    if n_rows > 1:
        reqs.append({'sortRange': {
            'range': {'sheetId': sid, 'startRowIndex': 1, 'endRowIndex': n_rows,
                      'startColumnIndex': 0, 'endColumnIndex': len(COLUMNS)},
            'sortSpecs': [
                {'dimensionIndex': seen_idx, 'sortOrder': 'ASCENDING'},
                {'dimensionIndex': COLUMNS.index('Оценка'), 'sortOrder': 'DESCENDING'},
            ]}})
    if reqs:
        try:
            ws.spreadsheet.batch_update({'requests': reqs})
        except Exception as e:
            print(f'  [оформление] часть стилей не применилась: {e}')

def cleanup_old_rows(ws):
    """Разгрузка листа: удаляет строки старше MAX_AGE_DAYS (по 'Опубликовано'),
    затем страховочный cap - если данных всё ещё > MAX_ROWS, обрезает лишние
    снизу (после сортировки непросмотренные-сверху/оценка-убыв, режутся низкие
    непросмотренные и все просмотренные). Нераспознанная дата -> НЕ удаляем.
    Источники сырья (Хабр/Телеграм) не трогаем - дедуп остаётся по source_id."""
    vals = with_retry(lambda: ws.get_all_values(), what="чтение листа перед подчисткой")
    if len(vals) <= 1:
        return
    pub_idx = COLUMNS.index('Опубликовано')
    to_delete = [i for i, row in enumerate(vals[1:], start=2)
                 if too_old(row[pub_idx] if len(row) > pub_idx else '')]
    sid = ws.id
    if to_delete:
        reqs = [{'deleteDimension': {
            'range': {'sheetId': sid, 'dimension': 'ROWS',
                      'startIndex': rownum - 1, 'endIndex': rownum}}}
                for rownum in sorted(to_delete, reverse=True)]
        try:
            ws.spreadsheet.batch_update({'requests': reqs})
            print(f'  [подчистка] удалено старых строк (>{MAX_AGE_DAYS}д): {len(to_delete)}')
        except Exception as e:
            print(f'  [подчистка] не удалось удалить старые строки: {e}')

    vals = with_retry(lambda: ws.get_all_values(), what="чтение листа после подчистки старья")
    n_data = len(vals) - 1
    if n_data > MAX_ROWS:
        seen_idx = COLUMNS.index('Просмотрено')
        score_idx = COLUMNS.index('Оценка')
        reqs = [
            {'sortRange': {
                'range': {'sheetId': sid, 'startRowIndex': 1, 'endRowIndex': 1 + n_data,
                          'startColumnIndex': 0, 'endColumnIndex': len(COLUMNS)},
                'sortSpecs': [
                    {'dimensionIndex': seen_idx, 'sortOrder': 'ASCENDING'},
                    {'dimensionIndex': score_idx, 'sortOrder': 'DESCENDING'},
                ]}},
            {'deleteDimension': {
                'range': {'sheetId': sid, 'dimension': 'ROWS',
                          'startIndex': 1 + MAX_ROWS, 'endIndex': 1 + n_data}}},
        ]
        try:
            ws.spreadsheet.batch_update({'requests': reqs})
            print(f'  [подчистка] cap {MAX_ROWS}: удалено лишних строк: {n_data - MAX_ROWS}')
        except Exception as e:
            print(f'  [подчистка] cap не применился: {e}')

# ==========================================================================
# Телеграм-счётчик
# ==========================================================================
def notify_count(n):
    if not TG_BOT_TOKEN or not TG_CHAT_ID or n == 0:
        return
    try:
        requests.post(f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage',
                      json={'chat_id': TG_CHAT_ID, 'text': f'🔔 Новых вакансий: {n}'},
                      timeout=30)
    except Exception as e:
        print(f'  [телеграм] не отправлено: {e}')

# ==========================================================================
# MAIN
# ==========================================================================
def main():
    for name, val in [('SPREADSHEET_ID', RAW_SPREADSHEET_ID),
                      ('TRACKER_SPREADSHEET_ID', TRACKER_SPREADSHEET_ID),
                      ('OPENROUTER_API_KEY', OPENROUTER_API_KEY)]:
        if not val:
            raise SystemExit(f'{name} не задан в .env')

    gc = gc_client()
    print('Читаю сырьё...')
    items = read_raw(gc)
    ws = open_out(gc)
    have = existing_ids(ws)
    todo = [it for it in items
            if it['source_id'] not in have and not too_old(it['published'])]
    skipped_old = sum(1 for it in items
                      if it['source_id'] not in have and too_old(it['published']))
    if MAX_PER_RUN:
        todo = todo[:MAX_PER_RUN]
    print(f'  всего в сырье: {len(items)}, уже оценено: {len(have)}, '
          f'старше {MAX_AGE_DAYS}д пропущено: {skipped_old}, к оценке: {len(todo)}')

    start_row = len(with_retry(lambda: ws.get_all_values(),
                               what="чтение листа перед записью")) + 1   # с какой строки дописываем
    num = len(have)
    added = 0
    skipped_junk = 0
    for it in todo:
        data = llm_evaluate(it['text'])
        if not data:
            # LLM не ответил (пусто/ошибка) - НЕ пишем, попадёт на повтор в след. прогон
            skipped_junk += 1
            print(f'    пропуск (LLM не ответил): {it["source_id"]}')
            continue
        # не одиночная вакансия (дайджест/инфопост/реклама) - не засоряем лист
        is_vac = str(data.get('is_vacancy', 'true')).strip().lower()
        if is_vac in ('false', 'нет', '0', 'no'):
            skipped_junk += 1
            continue
        # мусорная оценка (0-1) - не засоряем лист. Нераспознанный score -> не отсекаем.
        try:
            score_num = float(data.get('score'))
        except (TypeError, ValueError):
            score_num = None
        if score_num is not None and score_num < 2:
            skipped_junk += 1
            continue
        num += 1
        row = build_row(it, data)
        with_retry(lambda row=row: ws.append_row(row, value_input_option='USER_ENTERED'),
                  what="запись строки в лист")
        rgb = color_for(data.get('company'))
        if rgb:
            fill_row(ws, start_row + added, rgb)
        added += 1
        print(f'  [{num}] {str(data.get("score","?")):>2}/10 | {str(data.get("company") or "")[:18]:18} | {str(data.get("title") or "")[:40]}')

    print(f'  отсеяно (не вакансия/без ответа): {skipped_junk}')

    notify_count(added)
    cleanup_old_rows(ws)
    total_rows = len(with_retry(lambda: ws.get_all_values(), what="чтение листа перед оформлением"))
    style_sheet(ws, total_rows)
    print(f'\nЗвено 2 готово. Оценено и записано: {added}. Прогон: {dt.date.today().isoformat()}')
    print(f'Модель: {LLM_MODEL}')

if __name__ == '__main__':
    main()
