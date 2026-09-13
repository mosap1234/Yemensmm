import os
import subprocess
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# بياناتك التي أرسلتها
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8822657967:AAEge7b0zqC_gNgYzoFXzxCvIiAF7Qo6-xk')
ADMIN_ID = 8419807374 

# جلب منفذ ريلوي أو استخدام 8080 افتراضياً
PORT = os.environ.get('PORT', '8080')

# ريلوي يوفر هذا المتغير تلقائياً بمجرد إنشاء دومين للخدمة
PROXY_HOST = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'لم_يتم_توليد_رابط_بعد')

bot = telebot.TeleBot(BOT_TOKEN)

users = {
    "user1": "pass123",
    "user2": "pass456",
    "user3": "pass789"
}

def start_proxy():
    auth_args = []
    for u, p in users.items():
        auth_args.extend(["--basic-auth", f"{u}:{p}"])
    
    cmd = ["proxy", "--hostname", "0.0.0.0", "--port", str(PORT)] + auth_args
    return subprocess.Popen(cmd)

proxy_process = start_proxy()

# دالة التحقق من الأدمن
def is_admin(user_id):
    return user_id == ADMIN_ID

@bot.message_handler(commands=['start'])
def send_welcome(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ عذراً، هذا البوت مخصص للأدمن فقط.")
        return

    markup = InlineKeyboardMarkup()
    btn_info = InlineKeyboardButton("📡 بيانات البروكسي", callback_data="proxy_info")
    btn_status = InlineKeyboardButton("✅ حالة السيرفر", callback_data="proxy_status")
    markup.add(btn_info, btn_status)
    
    bot.reply_to(message, "أهلاً بك يا مصعب في لوحة تحكم البروكسي 🌐\nاختر من الأزرار أدناه:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    # حماية الأزرار أيضاً من أي شخص غير الأدمن
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ غير مصرح لك!", show_alert=True)
        return

    if call.data == "proxy_info":
        text = f"**بيانات خادم البروكسي HTTP:**\n\n"
        text += f"🔗 **العنوان (Host):** `{PROXY_HOST}`\n"
        text += f"🔌 **المنفذ (Port):** `{PORT}`\n\n"
        text += "**المستخدمين المتاحين:**\n"
        for i, (u, p) in enumerate(users.items(), 1):
            text += f"{i}. يوزر: `{u}` | باسورد: `{p}`\n"
        
        if PROXY_HOST == 'لم_يتم_توليد_رابط_بعد':
            text += "\n⚠️ **تنبيه:** لم تقم بتوليد دومين في ريلوي بعد. اذهب لإعدادات الخدمة واضغط على Generate Domain."

        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, text, parse_mode="Markdown")
        
    elif call.data == "proxy_status":
        status = "🟢 يعمل" if proxy_process.poll() is None else "🔴 متوقف"
        bot.answer_callback_query(call.id, f"حالة البروكسي: {status}", show_alert=True)

bot.infinity_polling()
