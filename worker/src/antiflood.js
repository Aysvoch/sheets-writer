import { BURST_WINDOW_SECONDS, BURST_MAX_MESSAGES, FLOOD_COOLDOWN_SECONDS } from './config.js';

// Двухуровневый антифлуд на все входящие сообщения. Бесплатный тариф KV - 1000
// записей/сутки, а дедуп по update_id и запись состояния платят KV-записью за
// каждое сообщение без всякого лимита - один человек, шлющий текст в цикле,
// выжигает суточную квоту за минуты и ломает бота для всех.
//
// Уровень 1 (burst) - счётчик, пока в пределах BURST_MAX_MESSAGES за
// BURST_WINDOW_SECONDS сообщения идут в обработку как обычно.
// Уровень 2 (cooldown) - как только burst превышен, ставится cooldown-ключ;
// пока он жив, всё дальнейшее дропается по одному чтению, без единой записи.
//
// justEntered = true ровно один раз - в момент, когда cooldown-ключ только что
// поставлен. Это и есть флаг "уже предупредили": он не хранится отдельной
// записью, а вычисляется из того, какая ветка сработала - следующий вызов той
// же функции увидит уже существующий ключ и вернёт justEntered:false без записи.
//
// KV eventually consistent - гонки на границах окна возможны, это лучшее
// приближение к лимиту, а не жёсткая гарантия.
export async function checkMessageFlood(kv, userId) {
  const cooldownKey = `flood:${userId}`;
  if (await kv.get(cooldownKey)) {
    return { limited: true, justEntered: false };
  }

  const burstKey = `burst:${userId}`;
  const raw = await kv.get(burstKey);
  const count = raw ? parseInt(raw, 10) || 0 : 0;

  if (count >= BURST_MAX_MESSAGES) {
    await kv.put(cooldownKey, '1', { expirationTtl: FLOOD_COOLDOWN_SECONDS });
    return { limited: true, justEntered: true };
  }

  await kv.put(burstKey, String(count + 1), { expirationTtl: BURST_WINDOW_SECONDS });
  return { limited: false, justEntered: false };
}
