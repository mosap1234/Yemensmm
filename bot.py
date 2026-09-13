import os
import sys
import base64
import sqlite3
import asyncio
import threading
import subprocess
import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# إعدادات التسجيل (Logging)
logging.basicConfig(
    filename='proxy.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# الإعدادات الأساسية
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8822657967:AAEge7b0zqC_gNgYzoFXzxCvIiAF7Qo6-xk')
ADMIN_ID = 8419807374
PORT = int(os.environ.get('PORT', 8080))

# سحب الرابط والبورت الخارجي تلقائياً من بيئة ريلوي
EXTERNAL_HOST = os.environ.get('RAILWAY_TCP_PROXY_DOMAIN', 'رابط_غير_متوفر')
EXTERNAL_PORT = os.environ.get('RAILWAY_TCP_PROXY_PORT', 'بورت_غير_متوفر')

DB_FILE = 'proxy_data.db'
active_sockets = set()

# إعداد قاعدة البيانات
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT,
                limit_gb REAL,
                used_bytes INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
        ''')
        conn.commit()

init_db()

# دوال إدارة البيانات
def get_user(username):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT username, password, limit_gb, used_bytes, is_active FROM users WHERE username = ?', (username,))
        return cursor.fetchone()

def update_traffic(username, bytes_count):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET used_bytes = used_bytes + ? WHERE username = ?', (bytes_count, username))
        conn.commit()

# محرك البروكسي (Asyncio HTTP Proxy)
async def pipe_streams(reader, writer, username):
    total = 0
    try:
        while True:
            data = await reader.read(8192)
            if not data:
                break
            writer.write(data)
            await writer.drain()
            total += len(data)
            if total > 524288:  # حفظ الاستهلاك كل 512KB
                update_traffic(username, total)
                total = 0
    except Exception:
        pass
    finally:
        if total > 0:
            update_traffic(username, total)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

async def handle_client(reader, writer):
    client_id = id(writer)
    active_sockets.add(client_id)
    auth_user = None
    target_info = "Unknown"

    try:
        request_line = await reader.readline()
        if not request_line:
            return

        method, path, _ = request_line.decode('utf-8', errors='ignore').split(' ', 2)
        headers = {}

        while True:
            line = await reader.readline()
            if line in (b'\r\n', b'\n', b''):
                break
            line_str = line.decode('utf-8', errors='ignore').strip()
            if ':' in line_str:
                k, v = line_str.split(':', 1)
                headers[k.strip().lower()] = v.strip()

        # التحقق من المصادقة
        auth_header = headers.get('proxy-authorization')
        if not auth_header or not auth_header.startswith('Basic '):
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm=\"Proxy\"\r\n\r\n")
            await writer.drain()
            return

        auth_decoded = base64.b64decode(auth_header.split(' ')[1]).decode('utf-8', errors='ignore')
        u, p = auth_decoded.split(':', 1)
        
        user_info = get_user(u)
        if not user_info or user_info[1] != p:
            logging.warning(f"Failed login attempt for user: {u}")
            writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            await writer.drain()
            return

        username, _, limit_gb, used_bytes, is_active = user_info

        # التحقق من صلاحية الحساب
        if is_active == 0 or (used_bytes >= limit_gb * (1024 ** 3)):
            logging.info(f"Blocked connection for {username} (Exceeded limit or disabled)")
            writer.write(b"HTTP/1.1 403 Bandwidth Exceeded or Account Disabled\r\n\r\n")
            await writer.drain()
            return

        auth_user = username

        # معالجة طلب CONNECT
        if method.upper() == 'CONNECT':
            target_host, target_port = path.split(':')
            target_port = int(target_port)
            target_info = f"{target_host}:{target_port}"
            
            logging.info(f"[{auth_user}] Connected to {target_info}")
            
            remote_reader, remote_writer = await asyncio.open_connection(target_host, target_port)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            await asyncio.gather(
                pipe_streams(reader, remote_writer, auth_user),
                pipe_streams(remote_reader, writer, auth_user),
                return_exceptions=True
            )
        else:
            writer.write(b"HTTP/1.1 501 Not Implemented\r\n\r\n")
            await writer.drain()

    except Exception as e:
        pass
    finally:
        active_sockets.discard(client_id)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

async def run_proxy():
    logging.info(f"Starting Proxy Server on internal port {PORT}...")
    server = await asyncio.start_server(handle_client, '0.0.0.0', PORT)
    async with server:
        await server.serve_forever()

# ----------------- واجهة بوت تلجرام -----------------
bot = telebot.TeleBot(BOT_TOKEN)

def is_admin(user_id):
    return user_id == ADMIN_ID

def main_menu():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📊 الإحصائيات", callback_data="stats"),
        InlineKeyboardButton("📡 بيانات الاتصال", callback_data="conn_info")
    )
    markup.row(
        InlineKeyboardButton("👥 إدارة البروكسيات", callback_data="list_users"),
        InlineKeyboardButton("➕ إنشاء بروكسي", callback_data="add_user")
    )
    markup.row(
        InlineKeyboardButton("📄 جلب اللوج", callback_data="fetch_logs"),
        InlineKeyboardButton("💻 تيرمنال", callback_data="open_terminal")
    )
    return markup

@bot.message_handler(commands=['start'])
def start_cmd(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(
        message.chat.id,
        "🎛️ **لوحة التحكم المتقدمة في خادم البروكسي**\nاختر الإجراء المطلوب أدناه:",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    if not is_admin(call.from_user.id):
        return

    cid = call.message.chat.id
    mid = call.message.message_id

    if call.data == "stats":
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*), SUM(used_bytes) FROM users')
            count, total_bytes = cursor.fetchone()
            total_bytes = total_bytes or 0

        total_gb = total_bytes / (1024 ** 3)
        text = (
            f"📊 **إحصائيات الخادم المباشرة:**\n\n"
            f"⚡ **الاتصالات النشطة حالياً:** `{len(active_sockets)}`\n"
            f"👥 **إجمالي المستخدمين:** `{count}`\n"
            f"📈 **إجمالي البيانات المستهلكة:** `{total_gb:.3f} GB`"
        )
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
        bot.edit_message_text(text, cid, mid, parse_mode="Markdown", reply_markup=markup)

    elif call.data == "conn_info":
        text = (
            f"📡 **بيانات الاتصال بالسيرفر:**\n\n"
            f"🌐 **الهوست (Host):** `{EXTERNAL_HOST}`\n"
            f"🔌 **المنفذ (Port):** `{EXTERNAL_PORT}`\n"
            f"🔒 **النوع:** `HTTP / HTTPS Proxy`"
        )
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
        bot.edit_message_text(text, cid, mid, parse_mode="Markdown", reply_markup=markup)

    elif call.data == "fetch_logs":
        bot.answer_callback_query(call.id, "جاري سحب ملف اللوج...")
        if os.path.exists('proxy.log'):
            with open('proxy.log', 'rb') as f:
                bot.send_document(cid, f, caption="📄 ملف السجلات (Logs)")
        else:
            bot.send_message(cid, "⚠️ ملف اللوج فارغ أو لم يتم إنشاؤه بعد.")

    elif call.data == "open_terminal":
        msg = bot.send_message(cid, "💻 **وضع التيرمنال النشط**\n\nأرسل الأمر البرمجي الذي تريد تنفيذه في سيرفر Railway.\nللخروج أرسل كلمة `إلغاء`", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_terminal)

    elif call.data == "list_users":
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, username, limit_gb, used_bytes, is_active FROM users')
            users = cursor.fetchall()

        if not users:
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
            bot.edit_message_text("⚠️ لا يوجد مستخدمين مضافين حتى الآن.", cid, mid, reply_markup=markup)
            return

        markup = InlineKeyboardMarkup()
        for uid, u, limit_gb, used_bytes, is_active in users:
            status_icon = "🟢" if is_active else "🔴"
            used_gb = used_bytes / (1024 ** 3)
            btn_text = f"{status_icon} {u} | {used_gb:.2f}/{limit_gb} GB"
            markup.add(InlineKeyboardButton(btn_text, callback_data=f"user_{uid}"))
        
        markup.add(InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
        bot.edit_message_text("👥 **قائمة الحسابات (اضغط على حساب للتحكم به):**", cid, mid, parse_mode="Markdown", reply_markup=markup)

    elif call.data.startswith("user_"):
        uid = int(call.data.split('_')[1])
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, username, password, limit_gb, used_bytes, is_active FROM users WHERE id = ?', (uid,))
            user = cursor.fetchone()

        if not user:
            return

        uid, username, password, limit_gb, used_bytes, is_active = user
        used_gb = used_bytes / (1024 ** 3)
        status_str = "🟢 نشط" if is_active else "🔴 معطل"

        text = (
            f"👤 **بيانات البروكسي للمستخدم:** `{username}`\n\n"
            f"🔑 **كلمة المرور:** `{password}`\n"
            f"📊 **الاستهلاك:** `{used_gb:.3f} GB` من `{limit_gb:.1f} GB`\n"
            f"⚙️ **الحالة:** {status_str}\n\n"
            f"📋 **كود الاتصال المباشر:**\n`{username}:{password}@{EXTERNAL_HOST}:{EXTERNAL_PORT}`"
        )

        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("⏸️ إيقاف" if is_active else "▶️ تفعيل", callback_data=f"toggle_{uid}"),
            InlineKeyboardButton("🗑️ حذف", callback_data=f"del_{uid}")
        )
        markup.add(InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="list_users"))
        bot.edit_message_text(text, cid, mid, parse_mode="Markdown", reply_markup=markup)

    elif call.data.startswith("toggle_"):
        uid = int(call.data.split('_')[1])
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?', (uid,))
            conn.commit()
        bot.answer_callback_query(call.id, "تم تغيير حالة الحساب ✅")
        handle_callbacks(type('obj', (object,), {'message': call.message, 'data': f"user_{uid}"}))

    elif call.data.startswith("del_"):
        uid = int(call.data.split('_')[1])
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM users WHERE id = ?', (uid,))
            conn.commit()
        bot.answer_callback_query(call.id, "تم حذف البروكسي 🗑️")
        handle_callbacks(type('obj', (object,), {'message': call.message, 'data': "list_users"}))

    elif call.data == "add_user":
        msg = bot.send_message(cid, "✏️ أرسل بيانات البروكسي بالصيغة التالية:\n`اليوزر:الباسورد:الحد_بالجيجا`", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_add_user)

    elif call.data == "main_menu":
        bot.edit_message_text("🎛️ **لوحة التحكم المتقدمة في خادم البروكسي**\nاختر الإجراء المطلوب أدناه:", cid, mid, parse_mode="Markdown", reply_markup=main_menu())

# دوال معالجة المدخلات (تيرمنال و إضافة مستخدم)
def process_terminal(message):
    if not is_admin(message.from_user.id): return
    if message.text.strip() == 'إلغاء':
        bot.reply_to(message, "✅ تم الخروج من وضع التيرمنال.", reply_markup=main_menu())
        return
    
    try:
        # تنفيذ الأمر في نظام Linux
        result = subprocess.run(message.text, shell=True, capture_output=True, text=True, timeout=20)
        output = result.stdout if result.stdout else result.stderr
        
        if not output:
            output = "[تم تنفيذ الأمر بنجاح بدون أي مخرجات]"
            
        # تلجرام يمنع الرسائل أطول من 4096 حرف
        if len(output) > 4000:
            output = output[:4000] + "\n...[تم قص باقي النص لطوله]..."
            
        bot.reply_to(message, f"```\n{output}\n```", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ خطأ أثناء التنفيذ:\n`{str(e)}`", parse_mode="Markdown")
    
    # البقاء في وضع التيرمنال
    msg = bot.send_message(message.chat.id, "أرسل أمراً آخر، أو أرسل `إلغاء` للخروج:")
    bot.register_next_step_handler(msg, process_terminal)


def process_add_user(message):
    if not is_admin(message.from_user.id):
        return
    try:
        parts = message.text.strip().split(':')
        u, p, limit = parts[0].strip(), parts[1].strip(), float(parts[2].strip())
        
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO users (username, password, limit_gb) VALUES (?, ?, ?)', (u, p, limit))
            conn.commit()
            
        bot.reply_to(message, f"✅ تم إنشاء البروكسي بنجاح!\nالمستخدم: `{u}`", parse_mode="Markdown", reply_markup=main_menu())
    except Exception:
        bot.reply_to(message, "⚠️ خطأ! الصيغة الصحيحة هي: `اليوزر:الباسورد:الجيجا`", parse_mode="Markdown", reply_markup=main_menu())

def start_bot_polling():
    bot.infinity_polling()

if __name__ == '__main__':
    t = threading.Thread(target=start_bot_polling, daemon=True)
    t.start()
    asyncio.run(run_proxy())
