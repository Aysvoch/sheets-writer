import { handleWebhookUpdate } from './webhook.js';
import { handleSubscribersRequest } from './subscribers.js';
import { timingSafeEqual } from './security.js';
import { logError } from './alerts.js';

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

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === '/webhook' && request.method === 'POST') {
      return handleTelegramWebhook(request, env, ctx);
    }

    if (url.pathname === '/subscribers' && request.method === 'GET') {
      return handleSubscribersRequest(request, env, ctx);
    }

    return new Response('Not Found', { status: 404 });
  },
};
