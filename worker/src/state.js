const USER_PREFIX = 'user:';
const ACTIVE_COUNT_KEY = 'stats:active_count';
const ACTIVE_COUNT_SNAPSHOT_KEY = 'stats:active_count_snapshot';

export function userKey(userId) {
  return `${USER_PREFIX}${userId}`;
}

export function userIdFromKey(key) {
  return key.slice(USER_PREFIX.length);
}

// Хранение разбито на metadata и value, чтобы kv.list() отдавал состояние подписчиков
// пачкой без отдельного kv.get на каждого - иначе список активных подписчиков сам по себе
// съедает по одному подзапросу на каждую запись в KV и упирается в лимит Cloudflare (50).
//
// metadata (приходит вместе с kv.list() и kv.getWithMetadata(), без доп. запроса):
//   { state, last_checked_at }
// value (то, что в metadata не кладём - sent_ids для части Б, растущий список):
//   { subscribed_at, sent_ids }

export async function getUser(kv, userId) {
  const { value, metadata } = await kv.getWithMetadata(userKey(userId), 'json');
  if (!metadata) return null;
  return {
    state: metadata.state,
    last_checked_at: metadata.last_checked_at,
    subscribed_at: value ? value.subscribed_at : undefined,
    sent_ids: value ? value.sent_ids : [],
  };
}

export async function putUser(kv, userId, record) {
  const value = JSON.stringify({
    subscribed_at: record.subscribed_at,
    sent_ids: record.sent_ids || [],
  });
  const metadata = { state: record.state, last_checked_at: record.last_checked_at };
  await kv.put(userKey(userId), value, { metadata });
}

export async function deleteUser(kv, userId) {
  await kv.delete(userKey(userId));
}

export function newUser(now) {
  return {
    state: 'active',
    subscribed_at: now,
    last_checked_at: now,
    sent_ids: [],
  };
}

// Счётчик активных подписчиков - живой (правится на каждом /start /stop /revoke),
// но не атомарный: KV не даёт compare-and-set, только read-modify-write. При редкой
// гонке двух одновременных событий возможен дрейф на ±1. Это не страшно - счётчик
// только для текста уведомлений владельцу, ни на что не влияет, и caмоисцеляется:
// /subscribers на каждом вызове пересчитывает точное число по всей KV и перезаписывает
// этот же ключ (см. checkSubscriberDropAlert в subscribers.js) - дрейф живёт не дольше,
// чем до следующего вызова догоняющей проверки.
export async function getActiveCount(kv) {
  const raw = await kv.get(ACTIVE_COUNT_KEY);
  return raw !== null ? parseInt(raw, 10) || 0 : 0;
}

export async function setActiveCount(kv, count) {
  await kv.put(ACTIVE_COUNT_KEY, String(count));
}

// Не уходит в минус - защита от того же дрейфа: если гонка уже увела счётчик
// ниже нуля, следующий -1 не должен утащить его дальше в абсурдные значения.
export async function adjustActiveCount(kv, delta) {
  const current = await getActiveCount(kv);
  const next = Math.max(0, current + delta);
  await setActiveCount(kv, next);
  return next;
}

// Отдельный ключ-снимок для алерта no_active_subscribers (checkSubscriberDropAlert
// в subscribers.js) - НЕ то же самое, что живой ACTIVE_COUNT_KEY выше. Тот меняется
// на каждом /start /stop /revoke, а этому алерту нужно "сколько было активных на
// момент ПРЕДЫДУЩЕГО вызова /subscribers", неизменное между вызовами - иначе
// типичный сценарий "последний подписчик сам отправил /stop" тихо гасит сам себя:
// живой счётчик уже станет 0 в момент /stop, и к моменту следующего /subscribers
// "предыдущее" тоже читалось бы как 0, алерт о падении с >0 до 0 просто не увидит
// это падение. Пишет и читает эту пару только checkSubscriberDropAlert.
export async function getActiveCountSnapshot(kv) {
  const raw = await kv.get(ACTIVE_COUNT_SNAPSHOT_KEY);
  return raw !== null ? parseInt(raw, 10) || 0 : 0;
}

export async function setActiveCountSnapshot(kv, count) {
  await kv.put(ACTIVE_COUNT_SNAPSHOT_KEY, String(count));
}
