import { isDuplicateUpdate } from './dedup.js';
import { checkMessageFlood } from './antiflood.js';
import { handleCommand, handleUnknownText } from './commands.js';
import { getUser, putUser } from './state.js';
import { notifyRevoked } from './revoke.js';
import { sendMessage } from './telegram.js';
import { FLOOD_WARNING_TEXT } from './texts.js';
import { CHANNEL_USERNAME, MEMBER_STATUSES } from './config.js';

const channelHandle = CHANNEL_USERNAME.replace(/^@/, '').toLowerCase();

function isTargetChannel(chat) {
  return !!chat && typeof chat.username === 'string' && chat.username.toLowerCase() === channelHandle;
}

function extractCommand(text) {
  const firstToken = text.trim().split(/\s+/)[0] || '';
  return firstToken.split('@')[0].toLowerCase();
}

async function handleMessageUpdate(message, env, ctx) {
  if (!message || !message.chat || typeof message.chat.id === 'undefined') return;
  if (!message.from || typeof message.from.id === 'undefined' || message.from.is_bot) return;
  if (typeof message.text !== 'string' || !message.text.trim()) return;

  const userId = String(message.from.id);
  const chatId = message.chat.id;
  const command = extractCommand(message.text);

  if (command === '/start') {
    await handleCommand('start', userId, chatId, env, ctx);
  } else if (command === '/stop') {
    await handleCommand('stop', userId, chatId, env, ctx);
  } else if (command === '/help') {
    await handleCommand('help', userId, chatId, env, ctx);
  } else {
    await handleUnknownText(chatId, env);
  }
}

// Вышел из канала (или иначе потерял членство) -> revoked + уведомление.
// Вернулся в канал -> ничего не делаем, ждём явный /start.
async function handleChatMemberUpdate(update, env, ctx) {
  const cm = update.chat_member;
  if (!cm || !isTargetChannel(cm.chat)) return;

  const newMember = cm.new_chat_member;
  const user = newMember && newMember.user;
  if (!user || typeof user.id === 'undefined' || user.is_bot) return;
  if (typeof newMember.status !== 'string') return;

  if (MEMBER_STATUSES.has(newMember.status)) return;

  const userId = String(user.id);
  const record = await getUser(env.AUDIENCE_KV, userId);
  if (!record || record.state !== 'active') return;

  record.state = 'revoked';
  record.last_checked_at = Date.now();
  await putUser(env.AUDIENCE_KV, userId, record);

  await notifyRevoked(env, ctx, userId);
}

export async function handleWebhookUpdate(update, env, ctx) {
  if (!update || typeof update.update_id === 'undefined') return;

  const message = update.message;

  // Группы/каналы не обслуживаем - только личка с ботом. Проверка ничего не
  // стоит (без обращения к KV), поэтому стоит первой, до антифлуда и дедупа.
  if (message && message.chat && message.chat.type !== 'private') return;

  // Антифлуд - до дедупа и любой обработки, иначе спам платит KV-записью
  // за каждое сообщение ещё до того, как мы решим, что это спам.
  if (message && message.from && typeof message.from.id !== 'undefined' && !message.from.is_bot) {
    const userId = String(message.from.id);
    const flood = await checkMessageFlood(env.AUDIENCE_KV, userId);
    if (flood.limited) {
      if (flood.justEntered) {
        try {
          await sendMessage(env.BOT_TOKEN, userId, FLOOD_WARNING_TEXT);
        } catch (err) {
          // предупреждение не критично - не роняем обработку апдейта из-за сети
        }
      }
      return;
    }
  }

  if (await isDuplicateUpdate(env.AUDIENCE_KV, update.update_id)) return;

  if (update.message) {
    await handleMessageUpdate(update.message, env, ctx);
  } else if (update.chat_member) {
    await handleChatMemberUpdate(update, env, ctx);
  }
}
