// Студия: локальный генератор картинок через OpenRouter.
// Запуск: node studio/server.mjs, потом открыть http://localhost:7860
// Ключ берётся из .env в корне репозитория (OPENROUTER_API_KEY) и никогда не уходит в браузер.

import http from 'node:http';
import { readFile, writeFile, readdir, unlink, mkdir } from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import { join, dirname, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..');
const OUT = join(HERE, 'output');
const PUBLIC = join(HERE, 'public');

function loadEnv(file) {
  if (!existsSync(file)) return;
  for (const line of readFileSync(file, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m && !(m[1] in process.env)) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
}
loadEnv(join(ROOT, '.env'));

const KEY = process.env.OPENROUTER_API_KEY;
const BASE = (process.env.OPENROUTER_BASE_URL || 'https://openrouter.ai/api/v1').replace(/\/$/, '');
const PROMPT_MODEL = process.env.PROMPT_MODEL || 'moonshotai/kimi-k3';
const PORT = Number(process.env.STUDIO_PORT || 7860);

const FALLBACK_MODELS = [
  { id: 'black-forest-labs/flux.2-pro', name: 'FLUX.2 Pro' },
  { id: 'black-forest-labs/flux.2-flex', name: 'FLUX.2 Flex' },
  { id: 'google/gemini-3.1-flash-image', name: 'Gemini Flash Image (Nano Banana)' },
  { id: 'bytedance-seed/seedream-4.5', name: 'Seedream 4.5' },
];

const ENHANCE_SYSTEM = `You write prompts for text-to-image models.
Turn the user's idea (any language) into one strong English prompt for the model named below.
Describe subject, composition, camera or medium, lighting, color palette, mood and important details.
Keep what the user asked for; do not add text, logos or extra people unless asked.
Return only the prompt, no quotes, no explanations, 40-90 words.`;

// Роли референсов: как модель должна использовать каждую картинку.
const ROLE_TEXT = {
  base: 'use it as the base image and modify it according to the prompt',
  pose: 'copy the exact body pose, gesture and head angle of the person in it',
  composition: 'follow its composition, layout, perspective and camera angle',
  style: 'match its art style, color palette, lighting and texture, not its content',
  character: 'keep the same person or character: identical face, hair and features',
  object: 'include this exact object with the same shape and details',
};

const EDIT_PROMPT = instruction =>
  `Image 1 is the source picture. Image 2 is a black-and-white mask of the same picture. ` +
  `Change only the area that is white in image 2: ${instruction}. ` +
  `Everything in the black area must stay exactly as in image 1: same composition, framing, lighting, colors and identity. ` +
  `Blend the changed area naturally with its surroundings. Output the full picture, not the mask.`;

const UPSCALE_PROMPT = {
  1: 'Re-render this exact image at high resolution. Keep composition, faces, colors and every shape unchanged. Only make it sharper and add fine natural detail such as skin pores, fabric weave and foliage texture.',
  2: 'Re-render this image at high resolution with richer detail. Keep the composition, faces and colors, but enhance textures, micro-contrast, lighting depth and small details so it looks like a high-end photo or artwork.',
  3: 'Re-imagine this image at high resolution with a strong creative enhancement. Keep the composition and main subjects recognisable, but add intricate detail, refined lighting, dramatic micro-contrast and painterly richness.',
};

async function api(path, init = {}) {
  if (!KEY) throw new Error('Нет ключа. Добавь OPENROUTER_API_KEY в файл .env в корне репозитория и перезапусти студию.');
  const res = await fetch(BASE + path, {
    ...init,
    headers: {
      Authorization: `Bearer ${KEY}`,
      'Content-Type': 'application/json',
      'X-Title': 'Konsilium Studio',
      ...(init.headers || {}),
    },
  });
  const text = await res.text();
  let body;
  try { body = JSON.parse(text); } catch { body = { raw: text }; }
  if (!res.ok) {
    const msg = body?.error?.message || body?.message || text.slice(0, 300);
    throw new Error(`OpenRouter ответил ${res.status}: ${msg}`);
  }
  return body;
}

let modelsCache = null;
async function listModels() {
  if (modelsCache && Date.now() - modelsCache.at < 10 * 60 * 1000) return modelsCache.list;
  try {
    const body = await api('/images/models');
    const raw = Array.isArray(body) ? body : body.data || [];
    const list = raw.map(m => ({
      id: m.id,
      name: m.name || m.id,
      pricing: m.pricing || null,
      params: m.supported_parameters || null,
      refs: (m.architecture?.input_modalities || []).includes('image'),
    })).filter(m => m.id);
    if (list.length) { modelsCache = { at: Date.now(), list }; return list; }
  } catch (e) {
    console.warn('Список моделей не получен:', e.message);
  }
  return FALLBACK_MODELS;
}

const EXT = { 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp' };
const MIME = { '.html': 'text/html; charset=utf-8', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp' };
const SAFE = /^[A-Za-z0-9_.-]+$/;
const isDataImage = s => typeof s === 'string' && /^data:image\/(png|jpeg|webp);base64,/.test(s);
const newId = () => new Date().toISOString().replace(/[:.]/g, '-') + '-' + randomUUID().slice(0, 6);

async function sourceAsDataUrl(file) {
  if (!SAFE.test(file || '') || !MIME[extname(file)]) throw new Error('Нет исходной картинки.');
  const buf = await readFile(join(OUT, file));
  return `data:${MIME[extname(file)]};base64,${buf.toString('base64')}`;
}

async function saveImage(buf, type, meta) {
  const id = newId();
  const file = id + (EXT[type] || '.png');
  await writeFile(join(OUT, file), buf);
  const full = { id, file, createdAt: new Date().toISOString(), ...meta };
  await writeFile(join(OUT, id + '.json'), JSON.stringify(full, null, 2));
  return full;
}

async function generateOne(req) {
  const body = { model: req.model, prompt: req.finalPrompt };
  if (req.aspect_ratio) body.aspect_ratio = req.aspect_ratio;
  if (req.resolution) body.resolution = req.resolution;
  if (Number.isInteger(req.seed)) body.seed = req.seed;
  if (req.references.length) body.input_references = req.references.map(url => ({ type: 'image_url', image_url: { url } }));
  const res = await api('/images', { method: 'POST', body: JSON.stringify(body) });
  const item = res.data?.[0];
  if (!item?.b64_json && !item?.url) throw new Error('Модель не вернула картинку.');
  let buf, type = item.media_type || 'image/png';
  if (item.b64_json) buf = Buffer.from(item.b64_json, 'base64');
  else {
    const r = await fetch(item.url);
    buf = Buffer.from(await r.arrayBuffer());
    type = r.headers.get('content-type') || type;
  }
  return saveImage(buf, type, {
    kind: req.kind, parent: req.parent || null,
    model: req.model, prompt: req.prompt, negative: req.negative || '', style: req.style || '',
    finalPrompt: req.finalPrompt, aspect_ratio: req.aspect_ratio || null, resolution: req.resolution || null,
    seed: Number.isInteger(req.seed) ? req.seed : null, roles: req.roles || [],
    cost: typeof res.usage?.cost === 'number' ? res.usage.cost : null,
  });
}

// Собирает запрос к модели для трёх режимов: генерация, правка по маске, увеличение.
async function buildJob(b) {
  const mode = ['generate', 'edit', 'upscale'].includes(b.mode) ? b.mode : 'generate';
  const job = {
    kind: mode, model: String(b.model || ''), prompt: String(b.prompt || '').trim(),
    aspect_ratio: b.aspect_ratio || null, resolution: ['1K', '2K', '4K'].includes(b.resolution) ? b.resolution : null,
    references: [], roles: [],
  };
  if (!job.model) throw new Error('Выбери модель.');

  if (mode === 'generate') {
    if (!job.prompt) throw new Error('Напиши промпт.');
    const refs = (Array.isArray(b.references) ? b.references : []).filter(r => isDataImage(r?.image)).slice(0, 6);
    job.references = refs.map(r => r.image);
    job.roles = refs.map(r => (ROLE_TEXT[r.role] ? r.role : 'style'));
    const parts = [job.prompt];
    if (b.styleText) parts.push(String(b.styleText));
    job.roles.forEach((role, i) => parts.push(`Reference image ${i + 1}: ${ROLE_TEXT[role]}`));
    if (b.negative) parts.push('Avoid: ' + String(b.negative));
    job.finalPrompt = parts.join('. ');
    job.negative = b.negative || ''; job.style = b.style || '';
  }
  if (mode === 'edit') {
    if (!job.prompt) throw new Error('Напиши, что сделать в выделенной области.');
    if (!isDataImage(b.mask)) throw new Error('Нарисуй маску.');
    job.parent = b.source;
    job.references = [await sourceAsDataUrl(b.source), b.mask];
    job.finalPrompt = EDIT_PROMPT(job.prompt);
  }
  if (mode === 'upscale') {
    const level = [1, 2, 3].includes(b.creativity) ? b.creativity : 1;
    job.parent = b.source;
    job.references = [await sourceAsDataUrl(b.source)];
    job.finalPrompt = UPSCALE_PROMPT[level] + (job.prompt ? ' ' + job.prompt : '');
    job.prompt = job.prompt || `Увеличение, творчество ${level}`;
    job.resolution = job.resolution || '4K';
  }
  return job;
}

async function gallery() {
  const files = (await readdir(OUT)).filter(f => f.endsWith('.json'));
  const items = [];
  for (const f of files) {
    try { items.push(JSON.parse(await readFile(join(OUT, f), 'utf8'))); } catch {}
  }
  return items.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

function readJson(req, limit = 60 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    let size = 0; const chunks = [];
    req.on('data', c => { size += c.length; if (size > limit) { reject(new Error('Слишком большой запрос.')); req.destroy(); } else chunks.push(c); });
    req.on('end', () => { try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')); } catch { reject(new Error('Некорректный JSON.')); } });
    req.on('error', reject);
  });
}

function send(res, status, data, type = 'application/json; charset=utf-8') {
  res.writeHead(status, { 'Content-Type': type, 'Cache-Control': 'no-store' });
  res.end(type.startsWith('application/json') ? JSON.stringify(data) : data);
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  try {
    if (req.method === 'GET' && url.pathname === '/') {
      return send(res, 200, await readFile(join(PUBLIC, 'index.html')), MIME['.html']);
    }
    if (req.method === 'GET' && url.pathname === '/api/status') {
      return send(res, 200, { hasKey: !!KEY, promptModel: PROMPT_MODEL });
    }
    if (req.method === 'GET' && url.pathname === '/api/models') {
      return send(res, 200, { models: await listModels() });
    }
    if (req.method === 'GET' && url.pathname === '/api/gallery') {
      return send(res, 200, { items: await gallery() });
    }
    if (req.method === 'GET' && url.pathname.startsWith('/output/')) {
      const name = url.pathname.slice(8);
      if (!SAFE.test(name) || !MIME[extname(name)]) return send(res, 404, { error: 'Нет файла.' });
      return send(res, 200, await readFile(join(OUT, name)), MIME[extname(name)]);
    }
    if (req.method === 'POST' && url.pathname === '/api/generate') {
      const b = await readJson(req);
      const job = await buildJob(b);
      const n = job.kind === 'generate' ? Math.min(Math.max(parseInt(b.n, 10) || 1, 1), 4) : 1;
      const seed = Number.isInteger(b.seed) ? b.seed : null;
      const results = await Promise.allSettled(Array.from({ length: n }, (_, i) =>
        generateOne({ ...job, seed: seed === null ? null : seed + i })));
      const items = results.filter(r => r.status === 'fulfilled').map(r => r.value);
      const errors = results.filter(r => r.status === 'rejected').map(r => r.reason.message);
      return send(res, items.length ? 200 : 502, { items, errors });
    }
    // Сохранить результат, собранный в браузере (маска наложена на оригинал, слой подмешан).
    if (req.method === 'POST' && url.pathname === '/api/save') {
      const b = await readJson(req);
      if (!isDataImage(b.image)) return send(res, 400, { error: 'Нет картинки.' });
      if (!SAFE.test(b.replaceId || '')) return send(res, 400, { error: 'Неверный id.' });
      const metaPath = join(OUT, b.replaceId + '.json');
      const meta = JSON.parse(await readFile(metaPath, 'utf8'));
      const type = b.image.slice(5, b.image.indexOf(';'));
      const file = b.replaceId + (EXT[type] || '.png');
      if (meta.file !== file && SAFE.test(meta.file)) await unlink(join(OUT, meta.file)).catch(() => {});
      await writeFile(join(OUT, file), Buffer.from(b.image.split(',')[1], 'base64'));
      Object.assign(meta, { file, note: String(b.note || '').slice(0, 200) });
      await writeFile(metaPath, JSON.stringify(meta, null, 2));
      return send(res, 200, { item: meta });
    }
    if (req.method === 'POST' && url.pathname === '/api/upload') {
      const b = await readJson(req);
      if (!isDataImage(b.image)) return send(res, 400, { error: 'Нет картинки.' });
      const type = b.image.slice(5, b.image.indexOf(';'));
      const item = await saveImage(Buffer.from(b.image.split(',')[1], 'base64'), type,
        { kind: 'upload', prompt: String(b.name || 'Загруженная картинка').slice(0, 120), model: '—', cost: null });
      return send(res, 200, { item });
    }
    if (req.method === 'POST' && url.pathname === '/api/enhance') {
      const b = await readJson(req);
      const idea = String(b.idea || '').trim();
      if (!idea) return send(res, 400, { error: 'Напиши идею.' });
      const out = await api('/chat/completions', {
        method: 'POST',
        body: JSON.stringify({
          model: PROMPT_MODEL,
          messages: [
            { role: 'system', content: ENHANCE_SYSTEM + `\nTarget image model: ${b.model || 'any'}.` },
            { role: 'user', content: idea },
          ],
        }),
      });
      const text = out.choices?.[0]?.message?.content?.trim();
      if (!text) throw new Error('Модель не вернула промпт.');
      return send(res, 200, { prompt: text });
    }
    if (req.method === 'DELETE' && url.pathname.startsWith('/api/gallery/')) {
      const id = url.pathname.slice(13);
      if (!SAFE.test(id)) return send(res, 400, { error: 'Неверный id.' });
      const meta = JSON.parse(await readFile(join(OUT, id + '.json'), 'utf8'));
      if (SAFE.test(meta.file)) await unlink(join(OUT, meta.file)).catch(() => {});
      await unlink(join(OUT, id + '.json'));
      return send(res, 200, { ok: true });
    }
    send(res, 404, { error: 'Не найдено.' });
  } catch (e) {
    send(res, 500, { error: e.message || String(e) });
  }
});

await mkdir(OUT, { recursive: true });
server.listen(PORT, '127.0.0.1', () => {
  console.log(`Студия: http://localhost:${PORT}`);
  if (!KEY) console.log('Внимание: нет OPENROUTER_API_KEY в .env, генерация не заработает.');
});
