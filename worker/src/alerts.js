import { ALERT_THROTTLE_SECONDS } from './config.js';
import { sendMessage } from './telegram.js';

async function isAlertThrottled(kv, code) {
  const key = `alert:${code}`;
  const existing = await kv.get(key);
  if (existing) return true;
  await kv.put(key, '1', { expirationTtl: ALERT_THROTTLE_SECONDS });
  return false;
}

// Лог - без user_id и содержимого сообщений, только код ситуации и техническая причина.
// Алерт владельцу - не чаще раза в час на один и тот же код, без ретраев отправки.
export async function logError(env, ctx, code, err) {
  const reason = err && err.message ? err.message : String(err);
  console.error(`[audience-bot] ${code}: ${reason}`);

  const notify = async () => {
    try {
      if (!env.AUDIENCE_KV || !env.BOT_TOKEN || !env.OWNER_CHAT_ID) return;
      if (await isAlertThrottled(env.AUDIENCE_KV, code)) return;
      const result = await sendMessage(env.BOT_TOKEN, env.OWNER_CHAT_ID, `⚠️ Воркер аудитории: ${code}`);
      if (!result.ok) {
        console.error(`[audience-bot] alert_send_failed: telegram error ${result.errorCode}`);
      }
    } catch (alertErr) {
      console.error(`[audience-bot] alert_send_failed: ${alertErr && alertErr.message}`);
    }
  };

  if (ctx && typeof ctx.waitUntil === 'function') {
    ctx.waitUntil(notify());
  } else {
    await notify();
  }
}
