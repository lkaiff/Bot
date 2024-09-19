import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext
from config import TOKEN, VOTE_THRESHOLD

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Словари для хранения голосований, предупреждений и счетчиков сообщений
votes = {}
warnings = {}
message_count = {}

# Списки ключевых слов и фраз
SPAM_KEYWORDS = ['доход', 'заработок', 'пассивный доход', 'сотрудничество', 'проект', 'онлайн', 'дистанционно']
SPAM_PHRASES = [
    r'доход от \d+',
    r'заработок от \d+',
    r'\d+-\d+ часа в день',
    r'пишите ["+"] в л[си]',
    r'пишите в личку',
    r'места ограничены',
    r'всех заинтересованных',
    r'ищу людей',
    r'возьму в .* проект',
    r'в свободное время'
]

def is_spam(text: str) -> bool:
    text = text.lower()
    
    keyword_count = sum(1 for keyword in SPAM_KEYWORDS if keyword in text)
    phrase_count = sum(1 for phrase in SPAM_PHRASES if re.search(phrase, text))
    has_numbers = bool(re.search(r'\d+', text))
    short_lines = len([line for line in text.split('\n') if len(line.strip()) < 50])
    
    spam_score = keyword_count + phrase_count * 2 + has_numbers + (short_lines > 2)
    
    logger.info(f"Спам-скор для сообщения: {spam_score}. Текст: {text[:50]}...")
    
    return spam_score >= 3

def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('Привет! Я бот для модерации чата. Я буду следить за потенциальным спамом и давать участникам возможность голосовать за удаление подозрительных сообщений.')

def check_message(update: Update, context: CallbackContext) -> None:
    message = update.message
    chat_id = message.chat_id
    message_id = message.message_id
    user_id = message.from_user.id

    if (chat_id, message_id) in votes:
        return

    if is_spam(message.text):
        logger.info(f"Подозрительное сообщение обнаружено: {message.text[:50]}...")
        
        keyboard = [
            [InlineKeyboardButton("Удалить", callback_data=f'delete_{chat_id}_{message_id}_{user_id}'),
             InlineKeyboardButton("Оставить", callback_data=f'keep_{chat_id}_{message_id}_{user_id}')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        context.bot.send_message(
            chat_id=chat_id,
            reply_to_message_id=message_id,
            text='⚠️ Обнаружено подозрительное сообщение. Голосуйте, чтобы решить его судьбу.',
            reply_markup=reply_markup
        )

        votes[(chat_id, message_id)] = {'delete': set(), 'keep': set(), 'user_id': user_id}
    else:
        if user_id not in message_count:
            message_count[user_id] = 0
        message_count[user_id] += 1

        if message_count[user_id] >= 10 and user_id in warnings:
            del warnings[user_id]
            message_count[user_id] = 0
            logger.info(f"Сброс предупреждений для пользователя {user_id}")

def button(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()

    action, chat_id, message_id, user_id = query.data.split('_')
    chat_id = int(chat_id)
    message_id = int(message_id)
    user_id = int(user_id)

    if (chat_id, message_id) not in votes:
        logger.warning(f"Попытка голосования для несуществующего сообщения: {chat_id}, {message_id}")
        return

    if query.from_user.id == user_id:
        context.bot.send_message(chat_id=query.from_user.id, text="Вы не можете голосовать за свое сообщение.")
        return

    if action == 'delete':
        votes[(chat_id, message_id)]['delete'].add(query.from_user.id)
        votes[(chat_id, message_id)]['keep'].discard(query.from_user.id)
    elif action == 'keep':
        votes[(chat_id, message_id)]['keep'].add(query.from_user.id)
        votes[(chat_id, message_id)]['delete'].discard(query.from_user.id)

    delete_count = len(votes[(chat_id, message_id)]['delete'])
    keep_count = len(votes[(chat_id, message_id)]['keep'])
    
    keyboard = [
        [InlineKeyboardButton(f"Удалить ({delete_count}/{VOTE_THRESHOLD})", callback_data=f'delete_{chat_id}_{message_id}_{user_id}'),
         InlineKeyboardButton(f"Оставить ({keep_count}/{VOTE_THRESHOLD})", callback_data=f'keep_{chat_id}_{message_id}_{user_id}')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_reply_markup(reply_markup=reply_markup)

    if delete_count >= VOTE_THRESHOLD:
        handle_delete_vote(context, chat_id, message_id, user_id, query.message.message_id)
    elif keep_count >= VOTE_THRESHOLD:
        handle_keep_vote(context, chat_id, message_id, query.message.message_id)

def handle_delete_vote(context: CallbackContext, chat_id: int, message_id: int, user_id: int, vote_message_id: int) -> None:
    try:
        # Попытка удалить оригинальное сообщение
        context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        
        user = context.bot.get_chat_member(chat_id, user_id).user
        username = f"@{user.username}" if user.username else f"User ID: {user_id}"

        if user_id not in warnings:
            warnings[user_id] = 0
        warnings[user_id] += 1

        if warnings[user_id] >= 2:
            context.bot.ban_chat_member(chat_id, user_id)
            result_message = f'🚫 Пользователь {username} забанен за повторное нарушение правил.'
        else:
            result_message = f'⚠️ Пользователь {username} получил предупреждение. При повторном нарушении он будет забанен.'

        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=vote_message_id,
            text=f'✅ Сообщение удалено по результатам голосования.\n\n{result_message}'
        )

    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")
    finally:
        if (chat_id, message_id) in votes:
            del votes[(chat_id, message_id)]

def handle_keep_vote(context: CallbackContext, chat_id: int, message_id: int, vote_message_id: int) -> None:
    try:
        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=vote_message_id,
            text="✅ Участники решили оставить сообщение."
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке голосования за сохранение: {e}")
    finally:
        if (chat_id, message_id) in votes:
            del votes[(chat_id, message_id)]

def unban(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    if len(context.args) == 1 and context.args[0].startswith('@'):
        username = context.args[0][1:]  # Убираем '@'
        try:
            user = context.bot.get_chat(username)
            context.bot.unban_chat_member(chat_id, user.id)
            context.bot.send_message(chat_id=chat_id, text=f'✅ Пользователь @{username} восстановлен в чате.')
            
            if user.id in warnings:
                del warnings[user.id]
            if user.id in message_count:
                del message_count[user.id]
        except Exception as e:
            update.message.reply_text(f'❌ Ошибка: {e}')
    else:
        update.message.reply_text('Пожалуйста, введите username в формате @username.')

def main() -> None:
    updater = Updater(TOKEN)

    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("unban", unban))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, check_message))
    dispatcher.add_handler(CallbackQueryHandler(button))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()