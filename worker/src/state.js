const USER_PREFIX = 'user:';

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
