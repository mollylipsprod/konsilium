#!/usr/bin/env python3
"""Спросить любую модель OpenRouter: второе мнение, ревью кода, ответ для дебатов.

Ключ берётся из OPENROUTER_API_KEY (переменная окружения или .env в корне репозитория).

  python3 scripts/ask.py --model moonshotai/kimi-k3 --file app.py "Найди ошибки"
  git diff | python3 scripts/ask.py --model moonshotai/kimi-k3 --stdin "Проверь эти изменения"
  python3 scripts/ask.py --list qwen          # найти точные id моделей
"""
import argparse, json, os, ssl, sys, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    env = ROOT / '.env'
    if env.exists():
        for line in env.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))


def api(path, body=None):
    key = os.environ.get('OPENROUTER_API_KEY')
    if not key:
        sys.exit('Нет OPENROUTER_API_KEY: добавь его в .env в корне репозитория.')
    base = os.environ.get('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1').rstrip('/')
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
                                          'X-Title': 'Konsilium'})
    ctx = ssl.create_default_context(cafile=os.environ.get('SSL_CERT_FILE') or None)
    try:
        with urllib.request.urlopen(req, timeout=600, context=ctx) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        text = e.read().decode('utf-8', 'replace')
        try:
            text = json.loads(text).get('error', {}).get('message', text)
        except Exception:
            pass
        sys.exit(f'OpenRouter ответил {e.code}: {text[:300]}')


def main():
    load_env()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('question', nargs='?', default='')
    p.add_argument('--model', default='moonshotai/kimi-k3')
    p.add_argument('--system', default='', help='роль и правила для модели')
    p.add_argument('--file', action='append', default=[], help='приложить файл (можно несколько раз)')
    p.add_argument('--stdin', action='store_true', help='приложить то, что пришло на вход (например git diff)')
    p.add_argument('--list', metavar='ПОИСК', help='показать id моделей, в названии которых есть ПОИСК')
    p.add_argument('--json', action='store_true', help='вывести ответ, модель и цену в JSON')
    a = p.parse_args()

    if a.list is not None:
        for m in api('/models').get('data', []):
            if a.list.lower() in (m['id'] + ' ' + m.get('name', '')).lower():
                pr = m.get('pricing') or {}
                print(f"{m['id']}\t$ {pr.get('prompt', '?')} / {pr.get('completion', '?')} за токен")
        return

    parts = []
    for f in a.file:
        parts.append(f'=== Файл: {f} ===\n{Path(f).read_text(encoding="utf-8", errors="replace")}')
    if a.stdin:
        parts.append('=== Входные данные ===\n' + sys.stdin.read())
    if a.question:
        parts.append(a.question)
    if not parts:
        sys.exit('Нечего отправлять: добавь вопрос, --file или --stdin.')

    messages = ([{'role': 'system', 'content': a.system}] if a.system else []) + \
               [{'role': 'user', 'content': '\n\n'.join(parts)}]
    res = api('/chat/completions', {'model': a.model, 'messages': messages, 'usage': {'include': True}})
    text = (res.get('choices') or [{}])[0].get('message', {}).get('content') or ''
    cost = (res.get('usage') or {}).get('cost')
    if a.json:
        print(json.dumps({'model': res.get('model', a.model), 'text': text, 'cost': cost}, ensure_ascii=False))
    else:
        print(text)
        print(f"\n--- {res.get('model', a.model)}" + (f' · ${cost:.4f}' if isinstance(cost, (int, float)) else ''), file=sys.stderr)


if __name__ == '__main__':
    main()
