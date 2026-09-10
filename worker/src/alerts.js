import { ALERT_THROTTLE_SECONDS } from './config.js';
import { sendMessage } from './telegram.js';

async function isAlertThrottled(kv, code) {
  return !!(await kv.get(`alert:${code}`));
}

// Троттлинг ставим ТОЛЬКО после подтверждённой успешной отправки - иначе первая
// же неудача (сеть, неверный OWNER_CHAT_ID и т.п.) заглушит этот код ошибки на
// ALERT_THROTTLE_SECONDS, и все последующие реальные алерты уйдут в никуда молча.
async function markAlertSent(kv, code) {
  await kv.put(`alert:${code}`, '1', { expirationTtl: ALERT_THROTTLE_SECONDS });
}

// Общая точка отправки алерта владельцу - используется и продовым logError (с
// троттлингом, в фоне), и диагностическим /test-alert (без троттлинга, напрямую).
// Отсутствие любого из биндингов - не молчаливый выход, а строка в console.error,
// иначе именно это и произошло: OWNER_CHAT_ID не был залит, и алерты годами
// уходили в пустоту без единого следа в логах.
async function sendOwnerAlert(env, text) {
  const missing = [];
  if (!env.AUDIENCE_KV) missing.push('AUDIENCE_KV');
  if (!env.BOT_TOKEN) missing.push('BOT_TOKEN');
  if (!env.OWNER_CHAT_ID) missing.push('OWNER_CHAT_ID');
  if (missing.length > 0) {
    console.error(`[audience-bot] alert_send_skipped: missing binding(s) ${missing.join(', ')}`);
    return { ok: false, errorCode: null, description: `missing binding(s) ${missing.join(', ')}` };
  }

  try {
    // .trim() - защита от случайного перевода строки/пробела при вставке
    // значения в `wrangler secret put OWNER_CHAT_ID` (частая причина немого сбоя).
    const result = await sendMessage(env.BOT_TOKEN, String(env.OWNER_CHAT_ID).trim(), text);
    if (!result.ok) {
      console.error(`[audience-bot] alert_send_failed: telegram error ${result.errorCode}`);
    }
    return result;
  } catch (err) {
    console.error(`[audience-bot] alert_send_failed: ${err && err.message}`);
    return { ok: false, errorCode: null, description: err && err.message };
  }
}

// Лог - без user_id и содержимого сообщений, только код ситуации и техническая причина.
// Алерт владельцу - не чаще раза в час на один и тот же код, без ретраев отправки.
// ctx.waitUntil регистрируется синхронно (до первого await внутри этой функции),
// поэтому фон гарантированно запущен до того, как вызывающий код сформирует ответ.
export async function logError(env, ctx, code, err) {
  const reason = err && err.message ? err.message : String(err);
  console.error(`[audience-bot] ${code}: ${reason}`);

  const notify = async () => {
    try {
      if (env.AUDIENCE_KV && (await isAlertThrottled(env.AUDIENCE_KV, code))) return;
      const result = await sendOwnerAlert(env, `⚠️ Воркер аудитории: ${code}`);
      if (result.ok && env.AUDIENCE_KV) {
        await markAlertSent(env.AUDIENCE_KV, code);
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

// Диагностика: реальная отправка владельцу тем же путём, что и logError
// (те же биндинги, тот же sendMessage), но синхронно и без троттлинга - чтобы
// вызывающий (эндпоинт /test-alert) мог отдать результат отправки в ответе.
export async function sendTestAlert(env) {
  return sendOwnerAlert(env, '🧪 Тестовый алерт воркера аудитории - если видишь это, доставка работает.');
}
