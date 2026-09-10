import { timingSafeEqual } from './security.js';
import { getChatMember } from './telegram.js';
import { getUser, putUser, deleteUser, userIdFromKey } from './state.js';
import { notifyRevoked } from './revoke.js';
import {
  CHANNEL_USERNAME,
  MEMBER_STATUSES,
  RECHECK_THRESHOLD_MS,
  MAX_RECHECKS_PER_CALL,
  SUBSCRIBERS_ENDPOINT_MIN_INTERVAL_SECONDS,
  MAX_REMOVE_PER_CALL,
} from './config.js';
import { logError } from './alerts.js';

const RATE_LIMIT_KEY = 'ratelimit:subscribers';
const LAST_ACTIVE_COUNT_KEY = 'stats:active_count';

// Служебная проверка - вторична по отношению к самой рассылке. Если сама
// сломалась, не блокируем часть Б лишний раз - лучше изредка пропустить лимит
// частоты, чем оставить рассылку без списка подписчиков из-за сбоя в бухгалтерии.
async function isRateLimited(kv, env, ctx) {
  try {
    const last = await kv.get(RATE_LIMIT_KEY);
    const now = Date.now();
    if (last && now - Number(last) < SUBSCRIBERS_ENDPOINT_MIN_INTERVAL_SECONDS * 1000) {
      return true;
    }
    await kv.put(RATE_LIMIT_KEY, String(now), {
      expirationTtl: SUBSCRIBERS_ENDPOINT_MIN_INTERVAL_SECONDS,
    });
    return false;
  } catch (err) {
    await logError(env, ctx, 'ratelimit_check_failed', err);
    return false;
  }
}

// Активных подписчиков и их last_checked_at берём из metadata, которую kv.list()
// отдаёт вместе со списком ключей - без отдельного kv.get на каждую запись.
async function listActiveUsers(kv) {
  const users = [];
  let cursor;
  do {
    const page = await kv.list({ prefix: 'user:', cursor });
    for (const key of page.keys) {
      const metadata = key.metadata;
      if (metadata && metadata.state === 'active') {
        users.push({ userId: userIdFromKey(key.name), lastCheckedAt: metadata.last_checked_at || 0 });
      }
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return users;
}

// Перепроверяет подписку у переданных кандидатов (уже отобраны и обрезаны вызывающей
// стороной по MAX_RECHECKS_PER_CALL). Не подтвердилась подписка -> revoked + уведомление.
// Полную запись (kv.get) тянем только для тех, кого реально будем перезаписывать -
// её нет в metadata из list(), а kv.put требует value целиком, не только metadata.
async function recheckCandidates(kv, env, ctx, candidates) {
  const now = Date.now();
  const stillActiveIds = [];

  for (const { userId } of candidates) {
    let memberStatus = null;
    let checkFailed = false;
    try {
      const result = await getChatMember(env.BOT_TOKEN, CHANNEL_USERNAME, userId);
      if (result.ok) {
        memberStatus = result.status;
      } else {
        checkFailed = true;
        await logError(env, ctx, 'recheck_getchatmember_failed', new Error(`telegram error ${result.errorCode}`));
      }
    } catch (err) {
      checkFailed = true;
      await logError(env, ctx, 'recheck_getchatmember_failed', err);
    }

    if (checkFailed) {
      // Не смогли проверить - не трогаем состояние, попробуем на следующем заходе.
      stillActiveIds.push(userId);
      continue;
    }

    const record = await getUser(kv, userId);
    if (!record || record.state !== 'active') continue; // состояние уже сменилось помимо этого вызова

    if (memberStatus !== null && MEMBER_STATUSES.has(memberStatus)) {
      record.last_checked_at = now;
      await putUser(kv, userId, record);
      stillActiveIds.push(userId);
    } else {
      record.state = 'revoked';
      record.last_checked_at = now;
      await putUser(kv, userId, record);
      await notifyRevoked(env, ctx, userId);
    }
  }

  return stillActiveIds;
}

// Алерт только на падение с ненулевого числа подписчиков до нуля - а не на
// "подписчиков ноль вообще" (нормальное состояние прямо сейчас, KV пустой).
async function checkSubscriberDropAlert(kv, env, ctx, currentCount) {
  const raw = await kv.get(LAST_ACTIVE_COUNT_KEY);
  const previousCount = raw !== null ? parseInt(raw, 10) || 0 : null;

  if (previousCount !== null && previousCount > 0 && currentCount === 0) {
    await logError(env, ctx, 'no_active_subscribers', new Error(`active subscribers dropped from ${previousCount} to 0`));
  }

  await kv.put(LAST_ACTIVE_COUNT_KEY, String(currentCount));
}

export async function handleSubscribersRequest(request, env, ctx) {
  const secretHeader = request.headers.get('x-subscribers-secret') || '';
  if (!env.SUBSCRIBERS_API_SECRET || !timingSafeEqual(secretHeader, env.SUBSCRIBERS_API_SECRET)) {
    return new Response('Unauthorized', { status: 401 });
  }

  if (await isRateLimited(env.AUDIENCE_KV, env, ctx)) {
    return new Response('Too Many Requests', { status: 429 });
  }

  try {
    const activeUsers = await listActiveUsers(env.AUDIENCE_KV);
    const now = Date.now();

    const stale = [];
    const fresh = [];
    for (const user of activeUsers) {
      if (now - user.lastCheckedAt > RECHECK_THRESHOLD_MS) stale.push(user);
      else fresh.push(user);
    }
    stale.sort((a, b) => a.lastCheckedAt - b.lastCheckedAt);

    const toRecheck = stale.slice(0, MAX_RECHECKS_PER_CALL);
    const deferred = stale.slice(MAX_RECHECKS_PER_CALL);

    const rechecked = await recheckCandidates(env.AUDIENCE_KV, env, ctx, toRecheck);

    const activeIds = fresh
      .map((u) => u.userId)
      .concat(deferred.map((u) => u.userId))
      .concat(rechecked);

    await checkSubscriberDropAlert(env.AUDIENCE_KV, env, ctx, activeIds.length);

    return new Response(JSON.stringify({ user_ids: activeIds }), {
      headers: { 'content-type': 'application/json' },
    });
  } catch (err) {
    await logError(env, ctx, 'subscribers_endpoint_failed', err);
    return new Response('Internal Error', { status: 500 });
  }
}

// Не доверяем структуре тела: user_ids должен быть массивом, элементы -
// строка/число, приводимые к telegram user_id (положительное целое). Всё
// остальное молча отбрасываем, а не роняем весь запрос из-за одного мусорного
// элемента. Дедуп через Set - если id повторили в массиве, удаляем один раз.
function extractValidIds(userIds) {
  const ids = new Set();
  for (const raw of userIds) {
    if (typeof raw !== 'string' && typeof raw !== 'number') continue;
    const id = String(raw).trim();
    if (!/^\d+$/.test(id)) continue;
    ids.add(id);
  }
  return [...ids];
}

// Разрушающий эндпоинт (удаляет записи из KV насовсем) - в отличие от /subscribers
// и /test-alert, которые только читают/шлют. Тот же секрет и константное сравнение,
// но дополнительно: строгая валидация тела и жёсткий потолок id за вызов - чтобы
// один вызов (баг на стороне Python, утечка секрета) не мог вычистить всю базу разом.
export async function handleRemoveSubscribersRequest(request, env, ctx) {
  const secretHeader = request.headers.get('x-subscribers-secret') || '';
  if (!env.SUBSCRIBERS_API_SECRET || !timingSafeEqual(secretHeader, env.SUBSCRIBERS_API_SECRET)) {
    return new Response('Unauthorized', { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch (err) {
    return new Response('Bad Request: invalid JSON', { status: 400 });
  }

  if (!body || !Array.isArray(body.user_ids)) {
    return new Response('Bad Request: user_ids must be an array', { status: 400 });
  }

  const ids = extractValidIds(body.user_ids);
  if (ids.length > MAX_REMOVE_PER_CALL) {
    return new Response(
      JSON.stringify({ error: `too many ids in one call (max ${MAX_REMOVE_PER_CALL})` }),
      { status: 400, headers: { 'content-type': 'application/json' } },
    );
  }

  let removed = 0;
  for (const id of ids) {
    try {
      await deleteUser(env.AUDIENCE_KV, id);
      removed++;
    } catch (err) {
      await logError(env, ctx, 'remove_subscriber_failed', err);
    }
  }

  // Видимость массовой чистки в логах - отдельно от обычного шума.
  if (removed > 0) {
    console.error(`[audience-bot] subscribers_removed: ${removed}`);
  }

  return new Response(JSON.stringify({ removed }), {
    headers: { 'content-type': 'application/json' },
  });
}
