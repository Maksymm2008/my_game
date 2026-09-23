import json
import logging
import urllib.parse
import os
import re
import html
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# --- KONFIGURATION ---
BOT_TOKEN = "7838039463:AAHLjN_Uk4XeMp4NafV6HOw7rbCcIkgy9RQ"
ADMIN_CHAT_IDS = [1701791586]  # Список ID всех администраторов
WEB_APP_URL = "https://maksymm2008.github.io/kulturverein-app/"
DATA_FILE = "bookings.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- БАЗА ДАННЫХ ---
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        user_bookings = json.load(f)
else:
    user_bookings = {}


def save_bookings():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(user_bookings, f, ensure_ascii=False, indent=4)


def get_all_occupied_seats():
    occupied = []
    for u_id, data in user_bookings.items():
        occupied.extend(data.get('approved_seats', []))
        occupied.extend(data.get('pending_seats', []))
    return list(set(occupied))


def get_user_seats_info(user_id: str):
    user_data = user_bookings.get(user_id, {})
    approved = user_data.get('approved_seats', [])
    pending = user_data.get('pending_seats', [])
    total_user_seats = list(set(approved + pending))
    remaining_quota = max(0, 4 - len(total_user_seats))
    return total_user_seats, approved, pending, remaining_quota


def build_user_keyboard(user_id: str):
    user_id = str(user_id)
    all_occupied = get_all_occupied_seats()
    total_seats, approved, pending, remaining = get_user_seats_info(user_id)

    # Достаем имя пользователя из базы (если оно уже есть и это не заглушка)
    saved_name = user_bookings.get(user_id, {}).get('name', '')
    if saved_name == "Bereits registriert":
        saved_name = ""

    params = {
        'occupied': ",".join(all_occupied),
        'my_seats': ",".join(total_seats),
        'max_seats': str(remaining),
        'user_id': user_id,
        'name': saved_name  # <-- Передаем уже сохраненное имя в WebApp
    }
    app_url = f"{WEB_APP_URL}?{urllib.parse.urlencode(params)}"

    buttons = [[KeyboardButton("🎭 Saalplan & Buchen", web_app=WebAppInfo(url=app_url))]]
    if total_seats:
        buttons.append([KeyboardButton("❌ Reservierung stornieren")])

    return ReplyKeyboardMarkup(buttons, resize_keyboard=True), remaining, total_seats


def generate_admin_seat_map(new_seats, user_approved):
    """Генерация визуальной карты зала для админа в Telegram"""
    all_occupied = get_all_occupied_seats()
    lines = ["<b>BÜHNE</b>"]
    for r in range(1, 9):
        row_str = f"R{r} "
        for s in range(1, 11):
            seat = f"R{r}-S{s}"
            if seat in new_seats:
                row_str += "🟩"
            elif seat in user_approved:
                row_str += "🟦"
            elif seat in all_occupied and seat not in new_seats:
                row_str += "🟥"
            else:
                row_str += "⬜"
        lines.append(row_str)
    lines.append("\n⬜Frei 🟥Besetzt 🟦Eigene 🟩Neu")
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    keyboard, remaining, total_seats = build_user_keyboard(user_id)

    msg = "Willkommen beim Kulturverein! 🎭\n\nKlicke auf <b>Saalplan & Buchen</b>, um Plätze zu wählen.\nBei Fragen schreibe einfach eine Nachricht hier in den Chat!"
    if total_seats:
        msg += f"\n\n📌 <b>Deine Plätze:</b> {', '.join(total_seats)}\n💡 Noch <b>{remaining}</b> Platz/Plätze verfügbar."

    await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="HTML")


async def stornieren_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    total_seats, _, _, _ = get_user_seats_info(user_id)
    if not total_seats:
        await update.message.reply_text("❌ Keine aktiven Reservierungen.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ Ja, stornieren", callback_data=f"usercancel_{user_id}")],
        [InlineKeyboardButton("Abbrechen", callback_data="close_menu")]
    ])
    await update.message.reply_text(
        f"❓ <b>Reservierung stornieren?</b>\nPlätze: <b>{', '.join(total_seats)}</b>",
        reply_markup=keyboard, parse_mode="HTML"
    )


async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.message or not update.message.web_app_data:
        return

    user_id = str(user.id)
    total_seats, approved, pending, remaining = get_user_seats_info(user_id)

    try:
        data = json.loads(update.message.web_app_data.data)
        booking_name = (data.get('name') or '').strip()
        event_name = data.get('event')
        new_seats = data.get('seats', [])
    except Exception as e:
        logging.error(f"Fehler beim Lesen der WebApp-Daten: {e}")
        return

    if not new_seats:
        await update.message.reply_text("⚠️ Bitte wähle mindestens einen Platz aus.")
        return

    if len(new_seats) > remaining:
        await update.message.reply_text(f"⚠️ Du kannst maximal noch {remaining} Platz/Plätze buchen.")
        return

    # 1. Имя пользователя из аккаунта Telegram
    telegram_name = user.full_name or user.first_name or "Gast"

    # 2. Имя, которое уже записано в базе (если есть)
    existing_name = user_bookings.get(user_id, {}).get('name', '').strip()

    # 3. Выбираем ПРАВИЛЬНОЕ имя (защита от "Bereits registriert" и пустых строк):
    if existing_name and existing_name != "Bereits registriert":
        final_name = existing_name
    elif booking_name and booking_name != "Bereits registriert":
        final_name = booking_name
    else:
        final_name = telegram_name

    # Сохраняем в базу данных
    if user_id not in user_bookings:
        user_bookings[user_id] = {
            'name': final_name,
            'event': event_name,
            'username': user.username or "",
            'approved_seats': [],
            'pending_seats': []
        }
    else:
        user_bookings[user_id]['name'] = final_name
        user_bookings[user_id]['username'] = user.username or ""

    user_bookings[user_id]['pending_seats'] = list(set(user_bookings[user_id].get('pending_seats', []) + new_seats))
    save_bookings()

    new_seats_str = ", ".join(new_seats)

    # Экранируем HTML-символы для безопасности
    safe_name = html.escape(final_name)
    safe_seats = html.escape(new_seats_str)

    # Сообщение пользователю
    keyboard, _, _ = build_user_keyboard(user_id)
    await update.message.reply_text(
        f"✅ <b>Anfrage gesendet!</b>\n🪑 Neu: {safe_seats}\nSobald bestätigt, erhältst du das Ticket.",
        reply_markup=keyboard, parse_mode="HTML"
    )

    # Красивое отображение username (если его нет — просто не пишем его)
    username_str = f" (@{user.username})" if user.username else ""

    # Визуальная карта для админа
    seat_map_visual = generate_admin_seat_map(new_seats, approved)

    admin_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Bestätigen", callback_data=f"approve_{user_id}"),
         InlineKeyboardButton("❌ Ablehnen", callback_data=f"reject_{user_id}")]
    ])

    admin_msg = (
        f"📥 <b>NEUE RESERVIERUNG</b>\n👤 {safe_name}{username_str} (ID: <code>{user_id}</code>)\n"
        f"🪑 Neu: {safe_seats}\n\n{seat_map_visual}"
    )

    for admin_id in ADMIN_CHAT_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=admin_msg, reply_markup=admin_markup,
                                           parse_mode="HTML")
        except Exception as e:
            logging.error(f"Fehler beim Senden an Admin {admin_id}: {e}")


async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Связь с администрацией и ответы от админов"""
    if not update.message or not update.message.text:
        return

    text = update.message.text
    user_id = update.effective_user.id

    if text == "❌ Reservierung stornieren": return

    # Если пишет АДМИН в ответ на пересланное сообщение
    if user_id in ADMIN_CHAT_IDS and update.message.reply_to_message and update.message.reply_to_message.text:
        match = re.search(r"ID:\s*(\d+)", update.message.reply_to_message.text)
        if match:
            target_id = match.group(1)
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"👨‍💼 <b>Antwort der Administration:</b>\n\n{html.escape(text)}",
                    parse_mode="HTML"
                )
                await update.message.reply_text("✅ Antwort gesendet.")
                return
            except Exception:
                await update.message.reply_text("❌ Fehler beim Senden.")
                return

    # Если пишет обычный пользователь (вопрос в техподдержку)
    if user_id not in ADMIN_CHAT_IDS:
        user_first_name = html.escape(update.effective_user.first_name or "Пользователь")
        username_str = f" (@{update.effective_user.username})" if update.effective_user.username else ""
        safe_text = html.escape(text)

        for admin_id in ADMIN_CHAT_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"✉️ <b>Nachricht von {user_first_name}</b>{username_str} (ID: <code>{user_id}</code>):\n\n{safe_text}",
                    parse_mode="HTML"
                )
            except Exception as e:
                logging.error(f"Fehler beim Weiterleiten an Admin: {e}")
        await update.message.reply_text("✅ Deine Nachricht wurde an die Administration weitergeleitet.")


async def handle_callback_queries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "close_menu":
        await query.delete_message()
        return

    action, target_user_id = query.data.split("_")

    if action == "usercancel" and target_user_id in user_bookings:
        user_name = user_bookings[target_user_id].get('name', 'Gast')
        if user_name == "Bereits registriert":
            user_name = query.from_user.full_name or "Gast"

        del user_bookings[target_user_id]
        save_bookings()
        await query.edit_message_text("❌ Reservierung storniert.")
        safe_user_name = html.escape(user_name)
        for admin_id in ADMIN_CHAT_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"ℹ️ <b>STORNIERUNG:</b> {safe_user_name} (ID: <code>{target_user_id}</code>) hat storniert.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        return

    if target_user_id not in user_bookings:
        await query.edit_message_text("Diese Anfrage ist abgelaufen.")
        return

    booking = user_bookings[target_user_id]
    pending = booking.get('pending_seats', [])

    # ПРОВЕРКА И ИСПРАВЛЕНИЕ ИМЕНИ:
    current_name = booking.get('name', '').strip()
    if not current_name or current_name == "Bereits registriert":
        real_name = query.from_user.full_name if query.from_user else "Gast"
        booking['name'] = real_name
        save_bookings()

    user_display_name = booking['name']
    safe_display_name = html.escape(user_display_name)

    if action == "approve":
        booking['approved_seats'] = list(set(booking.get('approved_seats', []) + pending))
        booking['pending_seats'] = []
        save_bookings()

        all_seats = ", ".join(booking['approved_seats'])
        safe_all_seats = html.escape(all_seats)
        await query.edit_message_text(f"✅ <b>BESTÄTIGT:</b> {safe_display_name} — <b>{safe_all_seats}</b>",
                                      parse_mode="HTML")

        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=TICKET-{target_user_id}"
        msg = f"🎉 <b>TICKET BESTÄTIGT!</b>\n👤 {safe_display_name}\n🪑 {safe_all_seats}\nZeige diesen QR-Code am Einlass."
        user_kbd, _, _ = build_user_keyboard(target_user_id)

        await context.bot.send_photo(
            chat_id=int(target_user_id),
            photo=qr_url,
            caption=msg,
            reply_markup=user_kbd,
            parse_mode="HTML"
        )

    elif action == "reject":
        booking['pending_seats'] = []
        save_bookings()
        await query.edit_message_text(f"❌ <b>ABGELEHNT:</b> {safe_display_name}", parse_mode="HTML")
        await context.bot.send_message(chat_id=int(target_user_id), text="Leider wurde deine letzte Anfrage abgelehnt.")


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buchen", start))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    app.add_handler(MessageHandler(filters.Regex("^❌ Reservierung stornieren$"), stornieren_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    app.add_handler(CallbackQueryHandler(handle_callback_queries))
    print("Bot läuft...")
    app.run_polling()


if __name__ == '__main__':
    main()
