# -*- coding: utf-8 -*-
"""
РУЧНАЯ рассылка объявления подписчикам бота (файл-сборщик заявок в этом
пайплайне не участвует, это отдельный разовый инструмент).

Запуск ТОЛЬКО руками:  python broadcast_announcement.py
В пайплайн (run_all.py / GitHub Actions) НЕ встраивать.

Логика:
  1. читает announcement.txt рядом со скриптом (текст объявления, только
     оттуда - в коде текста нет);
  2. получает список активных подписчиков у воркера (GET /subscribers,
     переиспользует fetch_active_subscribers из llm_scorer.py);
  3. показывает текст ЦЕЛИКОМ и число получателей, просит подтверждение;
  4. рассылает (переиспользует send_audience_message,
     report_blocked_subscribers из llm_scorer.py - те же паузы,
     ретраи на 429/5xx, чистка заблокировавших);
  5. печатает итог: ушло / не дошло / вычищено.

Переиспользование: вся механика отправки - из llm_scorer.py, здесь её нет.
Импорт llm_scorer.py безопасен - весь код исполнения там лежит под
`if __name__ == '__main__'`, импорт не открывает таблицы и не шлёт запросы.

HTML-разметка в announcement.txt (правило):
  Текст уходит в Telegram как есть, БЕЗ экранирования - в отличие от
  основной рассылки, где html.escape() применяется к данным из таблицы
  (недоверенным). Здесь текст пишешь ты сам, поэтому можно сразу писать
  теги, которые понимает Telegram (parse_mode=HTML):
    <b>жирный</b>, <i>курсив</i>, <u>подчёркнутый</u>, <s>зачёркнутый</s>,
    <a href="https://...">ссылка</a>, <code>код</code>, <pre>блок кода</pre>,
    <tg-spoiler>спойлер</tg-spoiler>
  ЧТО СЛОМАЕТ ОТПРАВКУ:
    - одиночные символы < > & вне тегов (например "5 < 10" или "Кофе & Ко") -
      Telegram попытается разобрать их как начало тега и отклонит сообщение
      целиком (400 Bad Request). Такие символы пиши как &lt; &gt; &amp;.
    - незакрытый или неправильно вложенный тег (<b>текст без закрытия) -
      тоже 400 Bad Request на всё сообщение.
    - теги, которых нет в списке выше (<div>, <p>, <br> и т.п.) - Telegram
      их не поддерживает, тоже 400.
  Простой текст без тегов - всегда безопасен, экранировать его не нужно.
"""

import os
import sys
import time

from llm_scorer import (
    TG_BOT_TOKEN,
    SUBSCRIBERS_API_SECRET,
    AUDIENCE_SEND_PAUSE,
    fetch_active_subscribers,
    send_audience_message,
    report_blocked_subscribers,
)

ANNOUNCEMENT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'announcement.txt')
TELEGRAM_MAX_LEN = 4096


def load_announcement():
    if not os.path.isfile(ANNOUNCEMENT_FILE):
        raise SystemExit(
            f'Не найден {ANNOUNCEMENT_FILE}. Создай файл с текстом объявления и запусти снова.'
        )
    with open(ANNOUNCEMENT_FILE, 'r', encoding='utf-8') as f:
        text = f.read().strip()
    if not text:
        raise SystemExit(
            f'{ANNOUNCEMENT_FILE} пустой. Впиши текст объявления - рассылать пустоту нельзя.'
        )
    if len(text) > TELEGRAM_MAX_LEN:
        over = len(text) - TELEGRAM_MAX_LEN
        raise SystemExit(
            f'Текст объявления длиннее лимита Telegram ({len(text)} символов, '
            f'превышение на {over}). Сократи текст в {ANNOUNCEMENT_FILE} - '
            f'автоматически резать или разбивать на части скрипт не будет.'
        )
    return text


def confirm(text, n_recipients):
    print('=' * 60)
    print('ТЕКСТ ОБЪЯВЛЕНИЯ:')
    print('=' * 60)
    print(text)
    print('=' * 60)
    print(f'Получателей: {n_recipients}')
    print('Действие необратимо - отменить отправку после подтверждения нельзя.')
    answer = input('Разослать это сообщение? Введи "да" для подтверждения: ')
    if answer.strip().lower() != 'да':
        raise SystemExit('Отменено пользователем - ничего не отправлено.')


def main():
    if not TG_BOT_TOKEN:
        raise SystemExit('TG_BOT_TOKEN не задан в .env - без него отправлять некуда.')
    if not SUBSCRIBERS_API_SECRET:
        raise SystemExit('SUBSCRIBERS_API_SECRET не задан в .env - без него список подписчиков не получить.')

    text = load_announcement()

    print('Получаю список подписчиков у воркера...')
    subscribers = fetch_active_subscribers()
    if subscribers is None:
        raise SystemExit(
            'Не удалось получить список подписчиков (см. ошибку выше). '
            'Учти: у эндпоинта /subscribers лимит 1 запрос в 60 секунд - '
            'если автопрогон пайплайна только что его дёргал, подожди минуту и запусти снова.'
        )
    if not subscribers:
        raise SystemExit('Активных подписчиков сейчас нет - рассылать некому.')

    confirm(text, len(subscribers))

    print(f'Рассылаю {len(subscribers)} подписчикам...')
    sent, failed, blocked = 0, 0, []
    for chat_id in subscribers:
        status = send_audience_message(chat_id, text)
        if status == 'ok':
            sent += 1
        elif status == 'blocked':
            blocked.append(chat_id)
        else:
            failed += 1
        time.sleep(AUDIENCE_SEND_PAUSE)

    if blocked:
        report_blocked_subscribers(blocked)

    print('=' * 60)
    print(f'Ушло:      {sent}')
    print(f'Не дошло:  {failed}')
    print(f'Вычищено:  {len(blocked)} (заблокировали бота / удалили аккаунт)')
    print('=' * 60)


if __name__ == '__main__':
    main()
