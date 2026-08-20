# -*- coding: utf-8 -*-
"""
Оркестратор: последовательно запускает сборщик вакансий и LLM-оценку.
Звено 2 (llm_scorer.py) запускается ТОЛЬКО если звено 1 (vacancy_collector.py)
завершилось успешно (код возврата 0). Ход выполнения пишется в run.log.
Секретов не содержит - только запуск двух скриптов тем же интерпретатором.
"""

import subprocess
import sys
import datetime as dt
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / 'run.log'

STEPS = [
    ('vacancy_collector.py', BASE_DIR / 'vacancy_collector.py'),
    ('llm_scorer.py', BASE_DIR / 'llm_scorer.py'),
]


def log(message):
    line = f'[{dt.datetime.now().isoformat(timespec="seconds")}] {message}'
    print(line)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def run_step(name, path):
    log(f'START {name}')
    result = subprocess.run([sys.executable, str(path)], cwd=BASE_DIR)
    if result.returncode == 0:
        log(f'END {name} OK (код возврата 0)')
    else:
        log(f'END {name} ERROR (код возврата {result.returncode})')
    return result.returncode


def main():
    log('=== Запуск run_all.py ===')

    code1 = run_step(*STEPS[0])
    if code1 != 0:
        log('Сборщик вакансий завершился с ошибкой - llm_scorer.py НЕ запускается.')
        print(f'\n[ОШИБКА] {STEPS[0][0]} завершился с кодом {code1}. '
              f'{STEPS[1][0]} не запущен. Подробности - в run.log.', file=sys.stderr)
        sys.exit(code1)

    code2 = run_step(*STEPS[1])
    if code2 != 0:
        log('LLM-оценка завершилась с ошибкой.')
        print(f'\n[ОШИБКА] {STEPS[1][0]} завершился с кодом {code2}. '
              f'Подробности - в run.log.', file=sys.stderr)
        sys.exit(code2)

    log('=== run_all.py завершён успешно ===')
    print('\nГотово: оба шага выполнены успешно.')


if __name__ == '__main__':
    main()
