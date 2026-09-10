const API_BASE = 'https://api.telegram.org/bot';

function apiUrl(botToken, method) {
  return `${API_BASE}${botToken}/${method}`;
}

async function callTelegram(botToken, method, payload) {
  const res = await fetch(apiUrl(botToken, method), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => null);
  return { httpOk: res.ok, status: res.status, data };
}

// Не бросает исключение на отказ Телеграма (403, chat not found и т.п.) - это
// ожидаемый исход, а не сеть/сбой. Вызывающий код обязан проверить result.ok.
export async function sendMessage(botToken, chatId, text) {
  const { httpOk, status, data } = await callTelegram(botToken, 'sendMessage', {
    chat_id: chatId,
    text,
    parse_mode: 'HTML',
    disable_web_page_preview: true,
  });

  const ok = httpOk && !!data && data.ok === true;
  if (ok) return { ok: true };

  return {
    ok: false,
    errorCode: (data && data.error_code) || status || null,
    description: (data && data.description) || null,
  };
}

// 403 (заблокировал бота/удалил аккаунт) или 400 "chat not found" - получатель
// недостижим навсегда, запись из KV можно удалять (см. п.7 спеки, таблица ошибок).
export function isPermanentSendFailure(result) {
  if (!result || result.ok) return false;
  if (result.errorCode === 403) return true;
  if (
    result.errorCode === 400 &&
    typeof result.description === 'string' &&
    result.description.toLowerCase().includes('chat not found')
  ) {
    return true;
  }
  return false;
}

// Возвращает { ok: true, status } либо { ok: false, errorCode } - errorCode это HTTP-код ответа Telegram.
export async function getChatMember(botToken, chatUsername, userId) {
  const { httpOk, status, data } = await callTelegram(botToken, 'getChatMember', {
    chat_id: chatUsername,
    user_id: Number(userId),
  });
  if (!httpOk || !data || !data.ok || !data.result) {
    return { ok: false, errorCode: status };
  }
  return { ok: true, status: data.result.status };
}
