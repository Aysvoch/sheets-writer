import { deleteUser } from './state.js';
import { sendMessage, isPermanentSendFailure } from './telegram.js';
import { REVOKED_TEXT } from './texts.js';
import { logError } from './alerts.js';

// Общая точка для отзыва доступа - используется и из webhook.js (событие
// chat_member), и из subscribers.js (догоняющая проверка). Если получатель
// недостижим навсегда (заблокировал бота, удалил аккаунт) - чистим запись
// в KV за собой, а не оставляем висеть мёртвым грузом.
export async function notifyRevoked(env, ctx, userId) {
  try {
    const result = await sendMessage(env.BOT_TOKEN, userId, REVOKED_TEXT);
    if (!result.ok) {
      if (isPermanentSendFailure(result)) {
        await deleteUser(env.AUDIENCE_KV, userId);
      } else {
        await logError(env, ctx, 'revoke_notify_failed', new Error(`telegram error ${result.errorCode}: ${result.description}`));
      }
    }
  } catch (err) {
    await logError(env, ctx, 'revoke_notify_failed', err);
  }
}
