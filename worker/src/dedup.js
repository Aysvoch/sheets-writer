import { UPDATE_DEDUP_TTL_SECONDS } from './config.js';

// Защита от повторной обработки update_id при ретраях Телеграма.
export async function isDuplicateUpdate(kv, updateId) {
  const key = `upd:${updateId}`;
  const existing = await kv.get(key);
  if (existing) return true;
  await kv.put(key, '1', { expirationTtl: UPDATE_DEDUP_TTL_SECONDS });
  return false;
}
