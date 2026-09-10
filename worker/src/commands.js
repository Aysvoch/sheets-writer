import { getUser, putUser, newUser } from './state.js';
import { getChatMember, sendMessage } from './telegram.js';
import { CHANNEL_USERNAME, MEMBER_STATUSES } from './config.js';
import {
  WELCOME_TEXT,
  ACCESS_DENIED_TEXT,
  ALREADY_ACTIVE_TEXT,
  STOPPED_TEXT,
  HELP_TEXT,
  UNKNOWN_TEXT,
  CHECK_FAILED_TEXT,
} from './texts.js';
import { logError } from './alerts.js';

// Антифлуд на /start отдельно не нужен - общий гейт на все сообщения
// (antiflood.js, включается до диспетчеризации команд в webhook.js) уже
// ограничивает и его, причём жёстче, чем прежний лимит только на /start.
async function handleStart(userId, chatId, env, ctx) {
  let check;
  try {
    check = await getChatMember(env.BOT_TOKEN, CHANNEL_USERNAME, userId);
  } catch (err) {
    await logError(env, ctx, 'getchatmember_failed', err);
    await sendMessage(env.BOT_TOKEN, chatId, CHECK_FAILED_TEXT);
    return;
  }

  if (!check.ok) {
    // API ответил, но не смогли получить статус - не тот же случай, что "не подписан":
    // реальный подписчик не должен получать отказ из-за нашего сбоя.
    await logError(env, ctx, 'getchatmember_failed', new Error(`telegram error ${check.errorCode}`));
    await sendMessage(env.BOT_TOKEN, chatId, CHECK_FAILED_TEXT);
    return;
  }

  const isMember = MEMBER_STATUSES.has(check.status);
  if (!isMember) {
    await sendMessage(env.BOT_TOKEN, chatId, ACCESS_DENIED_TEXT);
    return;
  }

  const existing = await getUser(env.AUDIENCE_KV, userId);
  const now = Date.now();

  if (existing && existing.state === 'active') {
    await sendMessage(env.BOT_TOKEN, chatId, ALREADY_ACTIVE_TEXT);
    return;
  }

  if (existing) {
    existing.state = 'active';
    existing.last_checked_at = now;
    await putUser(env.AUDIENCE_KV, userId, existing);
  } else {
    await putUser(env.AUDIENCE_KV, userId, newUser(now));
  }

  await sendMessage(env.BOT_TOKEN, chatId, WELCOME_TEXT);
}

async function handleStop(userId, chatId, env) {
  const existing = await getUser(env.AUDIENCE_KV, userId);
  if (existing && existing.state !== 'stopped') {
    existing.state = 'stopped';
    await putUser(env.AUDIENCE_KV, userId, existing);
  }
  await sendMessage(env.BOT_TOKEN, chatId, STOPPED_TEXT);
}

async function handleHelp(chatId, env) {
  await sendMessage(env.BOT_TOKEN, chatId, HELP_TEXT);
}

export async function handleCommand(command, userId, chatId, env, ctx) {
  if (command === 'start') return handleStart(userId, chatId, env, ctx);
  if (command === 'stop') return handleStop(userId, chatId, env);
  if (command === 'help') return handleHelp(chatId, env);
}

export async function handleUnknownText(chatId, env) {
  await sendMessage(env.BOT_TOKEN, chatId, UNKNOWN_TEXT);
}
