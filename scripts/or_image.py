#!/usr/bin/env python3
"""Генерация картинок через OpenRouter для студии в боковой панели.

Claude запускает этот скрипт, когда пользователь пишет «го»: берёт задания из
хранилища страницы, скачивает исходники, вызывает модель и кладёт результат
обратно. Ключ берётся из OPENROUTER_API_KEY (переменная окружения или .env) либо подставляется
прокси облачной среды (Network secret).

  python3 scripts/or_image.py models
  python3 scripts/or_image.py generate --model M --prompt P [--ref file:role ...] --n 2 --out out/gen
  python3 scripts/or_image.py edit --model M --prompt P --source a.png --mask m.png --keep --out out/edit.png
  python3 scripts/or_image.py upscale --model M --source a.png --creativity 2 --resolution 4K --mix 80 --out out/up.png
"""
import argparse, base64, io, json, mimetypes, os, ssl, sys, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ROLE_TEXT = {
    'base': 'use it as the base image and modify it according to the prompt',
    'pose': 'copy the exact body pose, gesture and head angle of the person in it',
    'composition': 'follow its composition, layout, perspective and camera angle',
    'style': 'match its art style, color palette, lighting and texture, not its content',
    'character': 'keep the same person or character: identical face, hair and features',
    'object': 'include this exact object with the same shape and details',
}
EDIT_PROMPT = ('Image 1 is the source picture. Image 2 is a black-and-white mask of the same picture. '
               'Change only the area that is white in image 2: {0}. '
               'Everything in the black area must stay exactly as in image 1: same composition, framing, lighting, colors and identity. '
               'Blend the changed area naturally with its surroundings. Output the full picture, not the mask.')
UPSCALE_PROMPT = {
    1: 'Re-render this exact image at high resolution. Keep composition, faces, colors and every shape unchanged. Only make it sharper and add fine natural detail such as skin pores, fabric weave and foliage texture.',
    2: 'Re-render this image at high resolution with richer detail. Keep the composition, faces and colors, but enhance textures, micro-contrast, lighting depth and small details so it looks like a high-end photo or artwork.',
    3: 'Re-imagine this image at high resolution with a strong creative enhancement. Keep the composition and main subjects recognisable, but add intricate detail, refined lighting, dramatic micro-contrast and painterly richness.',
}


def load_env():
    env = ROOT / '.env'
    if env.exists():
        for line in env.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))


def api(path, body=None):
    # Ключ необязателен: в облачной среде его может подставить прокси (Network secret).
    key = os.environ.get('OPENROUTER_API_KEY')
    base = os.environ.get('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1').rstrip('/')
    headers = {'Content-Type': 'application/json', 'X-Title': 'Konsilium Studio'}
    if key:
        headers['Authorization'] = f'Bearer {key}'
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    ctx = ssl.create_default_context(cafile=os.environ.get('SSL_CERT_FILE') or None)
    try:
        with urllib.request.urlopen(req, timeout=300, context=ctx) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        text = e.read().decode('utf-8', 'replace')
        try:
            text = json.loads(text).get('error', {}).get('message', text)
        except Exception:
            pass
        if e.code in (401, 403):
            raise RuntimeError('Ключ не принят. Добавь его как Network secret для openrouter.ai в облачной среде (или OPENROUTER_API_KEY в переменные среды / .env).')
        raise RuntimeError(f'OpenRouter ответил {e.code}: {text[:300]}')


def data_url(path):
    mime = mimetypes.guess_type(str(path))[0] or 'image/png'
    return f'data:{mime};base64,' + base64.b64encode(Path(path).read_bytes()).decode()


def call_image(model, prompt, refs=(), aspect=None, resolution=None, seed=None):
    body = {'model': model, 'prompt': prompt}
    if aspect: body['aspect_ratio'] = aspect
    if resolution: body['resolution'] = resolution
    if seed is not None: body['seed'] = seed
    if refs: body['input_references'] = [{'type': 'image_url', 'image_url': {'url': u}} for u in refs]
    res = api('/images', body)
    item = (res.get('data') or [{}])[0]
    if item.get('b64_json'):
        raw = base64.b64decode(item['b64_json'])
    elif item.get('url'):
        with urllib.request.urlopen(item['url'], timeout=120) as r:
            raw = r.read()
    else:
        raise RuntimeError('Модель не вернула картинку.')
    cost = (res.get('usage') or {}).get('cost')
    return raw, cost if isinstance(cost, (int, float)) else None


def save_png(raw_or_img, out):
    from PIL import Image
    img = raw_or_img if hasattr(raw_or_img, 'save') else Image.open(io.BytesIO(raw_or_img))
    img = img.convert('RGB')
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out, 'PNG')
    return img.size


def cmd_models(_):
    res = api('/images/models')
    raw = res if isinstance(res, list) else res.get('data', [])
    print(json.dumps([{'id': m['id'], 'name': m.get('name') or m['id'], 'pricing': m.get('pricing')} for m in raw if m.get('id')], ensure_ascii=False))


def cmd_generate(a):
    refs, roles = [], []
    for r in a.ref or []:
        path, _, role = r.rpartition(':')
        refs.append(data_url(path)); roles.append(role if role in ROLE_TEXT else 'style')
    parts = [a.prompt]
    if a.style_text: parts.append(a.style_text)
    parts += [f'Reference image {i + 1}: {ROLE_TEXT[r]}' for i, r in enumerate(roles)]
    if a.negative: parts.append('Avoid: ' + a.negative)
    prompt = '. '.join(parts)

    def one(i):
        try:
            raw, cost = call_image(a.model, prompt, refs, a.aspect, a.resolution, None if a.seed is None else a.seed + i)
            out = f'{a.out}-{i + 1}.png'
            w, h = save_png(raw, out)
            return {'file': out, 'cost': cost, 'width': w, 'height': h, 'seed': None if a.seed is None else a.seed + i}
        except Exception as e:
            return {'error': str(e)}
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(one, range(max(1, min(a.n, 4)))))
    print(json.dumps({'finalPrompt': prompt, 'roles': roles, 'results': results}, ensure_ascii=False))


def cmd_edit(a):
    from PIL import Image, ImageFilter
    raw, cost = call_image(a.model, EDIT_PROMPT.format(a.prompt), [data_url(a.source), data_url(a.mask)], a.aspect)
    src = Image.open(a.source).convert('RGB')
    res = Image.open(io.BytesIO(raw)).convert('RGB')
    note = ''
    if a.keep:
        res = res.resize(src.size, Image.LANCZOS)
        mask = Image.open(a.mask).convert('L').resize(src.size)
        mask = mask.filter(ImageFilter.GaussianBlur(max(4, src.size[0] // 120)))
        res = Image.composite(res, src, mask)
        note = 'наложено на оригинал по маске'
    w, h = save_png(res, a.out)
    print(json.dumps({'file': a.out, 'cost': cost, 'width': w, 'height': h, 'note': note}, ensure_ascii=False))


def cmd_upscale(a):
    from PIL import Image
    level = a.creativity if a.creativity in UPSCALE_PROMPT else 1
    prompt = UPSCALE_PROMPT[level] + (' ' + a.prompt if a.prompt else '')
    raw, cost = call_image(a.model, prompt, [data_url(a.source)], a.aspect, a.resolution or '4K')
    res = Image.open(io.BytesIO(raw)).convert('RGB')
    note = ''
    if a.mix < 100:
        src = Image.open(a.source).convert('RGB').resize(res.size, Image.LANCZOS)
        res = Image.blend(src, res, a.mix / 100)
        note = f'новый слой {a.mix}% поверх оригинала'
    w, h = save_png(res, a.out)
    print(json.dumps({'file': a.out, 'cost': cost, 'width': w, 'height': h, 'note': note}, ensure_ascii=False))


def main():
    load_env()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('models').set_defaults(fn=cmd_models)
    g = sub.add_parser('generate'); g.set_defaults(fn=cmd_generate)
    g.add_argument('--model', required=True); g.add_argument('--prompt', required=True)
    g.add_argument('--negative'); g.add_argument('--style-text'); g.add_argument('--aspect'); g.add_argument('--resolution')
    g.add_argument('--seed', type=int); g.add_argument('--n', type=int, default=1); g.add_argument('--ref', action='append')
    g.add_argument('--out', required=True, help='префикс файлов, к нему добавится -1.png, -2.png')
    e = sub.add_parser('edit'); e.set_defaults(fn=cmd_edit)
    e.add_argument('--model', required=True); e.add_argument('--prompt', required=True)
    e.add_argument('--source', required=True); e.add_argument('--mask', required=True)
    e.add_argument('--keep', action='store_true'); e.add_argument('--aspect'); e.add_argument('--out', required=True)
    u = sub.add_parser('upscale'); u.set_defaults(fn=cmd_upscale)
    u.add_argument('--model', required=True); u.add_argument('--source', required=True)
    u.add_argument('--creativity', type=int, default=1); u.add_argument('--resolution'); u.add_argument('--mix', type=int, default=100)
    u.add_argument('--prompt', default=''); u.add_argument('--aspect'); u.add_argument('--out', required=True)
    a = p.parse_args()
    try:
        a.fn(a)
    except RuntimeError as err:
        sys.exit(str(err))


if __name__ == '__main__':
    main()
