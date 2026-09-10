import { handleWebhookUpdate } from './webhook.js';
import { handleSubscribersRequest, handleRemoveSubscribersRequest } from './subscribers.js';
import { timingSafeEqual } from './security.js';
import { logError, sendTestAlert } from './alerts.js';

async function handleTelegramWebhook(request, env, ctx) {
  const secretHeader = request.headers.get('x-telegram-bot-api-secret-token') || '';
  if (!env.TELEGRAM_WEBHOOK_SECRET || !timingSafeEqual(secretHeader, env.TELEGRAM_WEBHOOK_SECRET)) {
    return new Response('Unauthorized', { status: 401 });
  }

  let update = null;
  try {
    update = await request.json();
  } catch (err) {
    // Не наш формат - Телеграму всё равно отвечаем 200, чтобы не спровоцировать повторы.
    return new Response('OK', { status: 200 });
  }

  try {
    await handleWebhookUpdate(update, env, ctx);
  } catch (err) {
    await logError(env, ctx, 'webhook_handler_failed', err);
  }

  // Телеграму отвечаем 200 всегда, даже если внутри что-то упало -
  // иначе он будет бесконечно повторять апдейт.
  return new Response('OK', { status: 200 });
}

// Диагностический эндпоинт - шлёт владельцу тестовое сообщение тем же путём,
// что и реальные алерты, и отдаёт результат отправки в ответе (не в логах),
// чтобы проверить доставку без необходимости специально что-то ломать.
// Секрет тот же, что у /subscribers - отдельный заводить незачем, это тоже
// служебный эндпоинт с тем же уровнем доступа (владелец/CI, не публика).
async function handleTestAlertRequest(request, env) {
  const secretHeader = request.headers.get('x-subscribers-secret') || '';
  if (!env.SUBSCRIBERS_API_SECRET || !timingSafeEqual(secretHeader, env.SUBSCRIBERS_API_SECRET)) {
    return new Response('Unauthorized', { status: 401 });
  }

  const result = await sendTestAlert(env);
  return new Response(JSON.stringify(result), {
    status: result.ok ? 200 : 502,
    headers: { 'content-type': 'application/json' },
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === '/webhook' && request.method === 'POST') {
      return handleTelegramWebhook(request, env, ctx);
    }

    if (url.pathname === '/subscribers' && request.method === 'GET') {
      return handleSubscribersRequest(request, env, ctx);
    }

    if (url.pathname === '/subscribers/remove' && request.method === 'POST') {
      return handleRemoveSubscribersRequest(request, env, ctx);
    }

    if (url.pathname === '/test-alert' && request.method === 'GET') {
      return handleTestAlertRequest(request, env);
    }

    return new Response('Not Found', { status: 404 });
  },
};
