#!/usr/bin/env python3
"""Быстрые решения через Jev (TypeSafe) на OpenRouter Decisions API.

Jev не пишет текст: получает данные (state) и вопросы, возвращает вероятности.
Типы вопросов: noul (да/нет, вероятность «да»), choice (один вариант из criteria), score (оценка по шкале).
Стоит около $0.04 за миллион входных токенов — подходит для массовой разметки (например, сотни отзывов).

  python3 scripts/decide.py --state отзыв.txt --questions вопросы.json
  echo "текст" | python3 scripts/decide.py --stdin --questions вопросы.json

Файл вопросов: {"size": {"type": "choice", "instructions": "...", "criteria": {"small": "...", "large": "..."}},
                "fake": {"type": "noul", "instructions": "..."}}
"""
import argparse, json, os, ssl, sys, urllib.error, urllib.request
from pathlib import Path

URL = 'https://openrouter.ai/api/alpha/decisions'


def decide(state, questions, model='typesafe/jev-1.13'):
    headers = {'Content-Type': 'application/json', 'X-Title': 'Konsilium'}
    key = os.environ.get('OPENROUTER_API_KEY')
    if key:  # в облачной среде ключ подставляет прокси (Network secret)
        headers['Authorization'] = f'Bearer {key}'
    req = urllib.request.Request(URL, data=json.dumps({'model': model, 'state': state, 'questions': questions}).encode(), headers=headers)
    ctx = ssl.create_default_context(cafile=os.environ.get('SSL_CERT_FILE') or None)
    try:
        with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f'OpenRouter ответил {e.code}: {e.read().decode("utf-8", "replace")[:300]}')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--state', help='файл с данными'); p.add_argument('--stdin', action='store_true')
    p.add_argument('--questions', required=True, help='JSON-файл с вопросами')
    p.add_argument('--model', default='typesafe/jev-1.13')
    a = p.parse_args()
    state = sys.stdin.read() if a.stdin else Path(a.state).read_text(encoding='utf-8')
    res = decide(state, json.loads(Path(a.questions).read_text(encoding='utf-8')), a.model)
    print(json.dumps({'answers': res.get('answers'), 'cost': (res.get('usage') or {}).get('cost')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
