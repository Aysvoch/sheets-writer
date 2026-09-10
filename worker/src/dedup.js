import { UPDATE_DEDUP_TTL_SECONDS } from './config.js';
import { logError } from './alerts.js';

// Защита от повторной обработки update_id при ретраях Телеграма - служебная
// механика, вторична по отношению к ответу пользователю. Если KV сломался
// именно на этой проверке, считаем апдейт НЕ дублем и пропускаем в обработку:
// лучше изредка обработать ретрай Телеграма дважды (команды идемпотентны),
// чем оставить человека без ответа из-за сбоя в бухгалтерии.
export async function isDuplicateUpdate(kv, updateId, env, ctx) {
  const key = `upd:${updateId}`;
  try {
    const existing = await kv.get(key);
    if (existing) return true;
    await kv.put(key, '1', { expirationTtl: UPDATE_DEDUP_TTL_SECONDS });
    return false;
  } catch (err) {
    await logError(env, ctx, 'dedup_check_failed', err);
    return false;
  }
}
