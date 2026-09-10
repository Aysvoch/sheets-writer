export const CHANNEL_USERNAME = '@Private_ether';

export const MEMBER_STATUSES = new Set(['member', 'administrator', 'creator']);

export const RECHECK_THRESHOLD_MS = 7 * 24 * 60 * 60 * 1000;
// Потолок синхронных перепроверок за один вызов /subscribers - защита от лимита
// исходящих подзапросов Cloudflare (50 за обработку запроса). Остальные просроченные
// догонятся следующим вызовом (берём самых давно не проверенных, так что не зависают).
//
// Худший случай на кандидата - 4 подзапроса (getChatMember + kv.get записи +
// kv.put + sendMessage при отзыве). Плюс фиксированные накладные: rate-limit (2),
// kv.list (1), учёт счётчика подписчиков для алерта о падении до нуля (2, всегда) +
// сам алерт, если падение реально случилось (3, редко).
// 10 * 4 + 2 + 1 + 2 + 3 = 48 - с запасом укладывается в 50 даже если ВСЕ 10 окажутся
// отозваны разом и это совпадёт с падением активных подписчиков до нуля.
export const MAX_RECHECKS_PER_CALL = 10;

export const UPDATE_DEDUP_TTL_SECONDS = 24 * 60 * 60;

// Антифлуд на ВСЕ входящие сообщения (не только /start) - см. antiflood.js.
// Уровень 1 - короткий всплеск: пока укладываемся в BURST_MAX_MESSAGES за
// BURST_WINDOW_SECONDS, сообщения обрабатываются как обычно (сюда укладывается
// нормальный сценарий "/start -> отказ -> /help -> подписался -> /start ещё раз").
// Уровень 2 - если всплеск превышен, один раз предупреждаем и включаем
// FLOOD_COOLDOWN_SECONDS, на время которого сообщения дропаются без единой
// записи в KV (кроме той самой одной, что ставит cooldown-ключ).
export const BURST_WINDOW_SECONDS = 15;
export const BURST_MAX_MESSAGES = 5;
export const FLOOD_COOLDOWN_SECONDS = 5 * 60;

export const ALERT_THROTTLE_SECONDS = 60 * 60;

export const SUBSCRIBERS_ENDPOINT_MIN_INTERVAL_SECONDS = 30;
