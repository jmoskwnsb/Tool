import os
import io
import re
import gzip
import base64
import random
import zipfile
import sqlite3
import secrets
import string

from PIL import Image, ImageOps
import qrcode

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

CHANNEL = "@ByteTunnel"
DB_NAME = "bot.db"
MAX_SIZE = 45 * 1024 * 1024

if not TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده")

if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID تنظیم نشده")

ADMIN_ID = int(ADMIN_ID)


# =========================================================
# DATABASE
# =========================================================

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT
        )
    """)

    conn.commit()
    conn.close()


def save_user(user):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        INSERT OR REPLACE INTO users
        (user_id, username, first_name)
        VALUES (?, ?, ?)
    """, (
        user.id,
        user.username or "",
        user.first_name or ""
    ))

    conn.commit()
    conn.close()


def user_count():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    result = cur.fetchone()[0]

    conn.close()
    return result


def get_users():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("SELECT user_id FROM users")
    users = [x[0] for x in cur.fetchall()]

    conn.close()
    return users


# =========================================================
# MEMBERSHIP
# =========================================================

async def is_member(bot, user_id):
    try:
        member = await bot.get_chat_member(
            CHANNEL,
            user_id
        )

        return member.status in (
            "member",
            "administrator",
            "creator"
        )

    except Exception as e:
        print("Membership:", e)
        return False


def join_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 عضویت در کانال",
                url="https://t.me/ByteTunnel"
            )
        ],
        [
            InlineKeyboardButton(
                "✅ بررسی عضویت",
                callback_data="check_join"
            )
        ]
    ])


async def check_access(update, context):
    user_id = update.effective_user.id

    if not await is_member(context.bot, user_id):

        text = (
            "🔐 <b>عضویت الزامی است</b>\n\n"
            "برای استفاده از بات ابتدا عضو "
            "@ByteTunnel شوید."
        )

        if update.callback_query:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=join_keyboard(),
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=join_keyboard(),
                parse_mode="HTML"
            )

        return False

    return True


# =========================================================
# MAIN MENU
# =========================================================

def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🖼 ابزار عکس",
                callback_data="photo"
            ),
            InlineKeyboardButton(
                "📁 ابزار فایل",
                callback_data="file"
            )
        ],
        [
            InlineKeyboardButton(
                "📝 ابزار متن",
                callback_data="text"
            ),
            InlineKeyboardButton(
                "🔗 ابزار لینک",
                callback_data="link"
            )
        ],
        [
            InlineKeyboardButton(
                "🎲 سرگرمی",
                callback_data="fun"
            )
        ],
        [
            InlineKeyboardButton(
                "🔐 امنیت و رمز",
                callback_data="security"
            )
        ],
        [
            InlineKeyboardButton(
                "👑 پنل مدیریت",
                callback_data="admin"
            )
        ]
    ])


async def show_main(update, context):
    text = (
        "🛠 <b>بات ابزار همه‌کاره</b>\n\n"
        "یک بخش را انتخاب کن:"
    )

    await update.callback_query.edit_message_text(
        text,
        reply_markup=main_menu(),
        parse_mode="HTML"
    )


# =========================================================
# PHOTO MENU
# =========================================================

def photo_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📉 کاهش حجم",
                callback_data="compress"
            )
        ],
        [
            InlineKeyboardButton(
                "🔄 تبدیل فرمت",
                callback_data="convert"
            )
        ],
        [
            InlineKeyboardButton(
                "📐 تغییر اندازه",
                callback_data="resize"
            )
        ],
        [
            InlineKeyboardButton(
                "✂️ برش",
                callback_data="crop"
            )
        ],
        [
            InlineKeyboardButton(
                "🔃 چرخش",
                callback_data="rotate"
            )
        ],
        [
            InlineKeyboardButton(
                "🖤 سیاه و سفید",
                callback_data="bw"
            )
        ],
        [
            InlineKeyboardButton(
                "🖼 عکس به PDF",
                callback_data="to_pdf"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="main"
            )
        ]
    ])


async def photo_page(update, context):
    await update.callback_query.edit_message_text(
        "🖼 <b>ابزار عکس</b>\n\n"
        "ابزار موردنظر را انتخاب کن:",
        reply_markup=photo_menu(),
        parse_mode="HTML"
    )


# =========================================================
# FILE MENU
# =========================================================

def file_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🗜 فشرده‌سازی GZIP",
                callback_data="gzip"
            )
        ],
        [
            InlineKeyboardButton(
                "📦 استخراج ZIP",
                callback_data="unzip"
            )
        ],
        [
            InlineKeyboardButton(
                "📝 تغییر نام فایل",
                callback_data="rename"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="main"
            )
        ]
    ])


async def file_page(update, context):
    await update.callback_query.edit_message_text(
        "📁 <b>ابزار فایل</b>\n\n"
        "گزینه موردنظر را انتخاب کن:",
        reply_markup=file_menu(),
        parse_mode="HTML"
    )


# =========================================================
# TEXT MENU
# =========================================================

def text_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔢 شمارش متن",
                callback_data="count"
            )
        ],
        [
            InlineKeyboardButton(
                "🧹 پاک‌سازی متن",
                callback_data="clean"
            )
        ],
        [
            InlineKeyboardButton(
                "🔀 برعکس کردن",
                callback_data="reverse"
            )
        ],
        [
            InlineKeyboardButton(
                "🔗 استخراج لینک",
                callback_data="links"
            )
        ],
        [
            InlineKeyboardButton(
                "🔠 حروف بزرگ/کوچک",
                callback_data="case"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="main"
            )
        ]
    ])


async def text_page(update, context):
    await update.callback_query.edit_message_text(
        "📝 <b>ابزار متن</b>\n\n"
        "ابزار موردنظر را انتخاب کن:",
        reply_markup=text_menu(),
        parse_mode="HTML"
    )


# =========================================================
# LINK MENU
# =========================================================

def link_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📱 ساخت QR",
                callback_data="qr"
            )
        ],
        [
            InlineKeyboardButton(
                "🔍 بررسی لینک",
                callback_data="urlcheck"
            )
        ],
        [
            InlineKeyboardButton(
                "🔗 استخراج لینک",
                callback_data="extracturl"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="main"
            )
        ]
    ])


async def link_page(update, context):
    await update.callback_query.edit_message_text(
        "🔗 <b>ابزار لینک</b>",
        reply_markup=link_menu(),
        parse_mode="HTML"
    )


# =========================================================
# FUN MENU
# =========================================================

def fun_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎲 تاس",
                callback_data="dice"
            ),
            InlineKeyboardButton(
                "🪙 شیر یا خط",
                callback_data="coin"
            )
        ],
        [
            InlineKeyboardButton(
                "🔢 عدد تصادفی",
                callback_data="random"
            )
        ],
        [
            InlineKeyboardButton(
                "🎯 انتخاب تصادفی",
                callback_data="choice"
            )
        ],
        [
            InlineKeyboardButton(
                "🧩 معما",
                callback_data="riddle"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="main"
            )
        ]
    ])


async def fun_page(update, context):
    await update.callback_query.edit_message_text(
        "🎲 <b>سرگرمی</b>\n\n"
        "یک گزینه انتخاب کن:",
        reply_markup=fun_menu(),
        parse_mode="HTML"
    )


# =========================================================
# SECURITY MENU
# =========================================================

def security_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔐 Base64 Encode",
                callback_data="encode"
            )
        ],
        [
            InlineKeyboardButton(
                "🔓 Base64 Decode",
                callback_data="decode"
            )
        ],
        [
            InlineKeyboardButton(
                "🔑 ساخت رمز تصادفی",
                callback_data="password"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="main"
            )
        ]
    ])


async def security_page(update, context):
    await update.callback_query.edit_message_text(
        "🔐 <b>ابزارهای امنیتی</b>",
        reply_markup=security_menu(),
        parse_mode="HTML"
    )


# =========================================================
# DOWNLOAD PHOTO
# =========================================================

async def get_photo(update, context):
    photo = update.message.photo[-1]

    file = await context.bot.get_file(
        photo.file_id
    )

    data = await file.download_as_bytearray()

    return bytes(data)


async def get_document(update, context):
    doc = update.message.document

    if doc.file_size and doc.file_size > MAX_SIZE:
        await update.message.reply_text(
            "❌ فایل خیلی بزرگ است."
        )
        return None

    file = await context.bot.get_file(
        doc.file_id
    )

    data = await file.download_as_bytearray()

    return bytes(data)


# =========================================================
# PHOTO PROCESSING
# =========================================================

async def process_photo(update, context):

    action = context.user_data.get("action")

    if not action:
        return

    data = await get_photo(
        update,
        context
    )

    try:
        image = Image.open(
            io.BytesIO(data)
        )

        # Compress
        if action == "compress":

            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")

            out = io.BytesIO()

            image.save(
                out,
                "JPEG",
                quality=45,
                optimize=True
            )

            out.seek(0)

            await update.message.reply_document(
                document=InputFile(
                    out,
                    filename="compressed.jpg"
                ),
                caption="✅ حجم عکس کاهش پیدا کرد."
            )

        # Convert
        elif action == "convert":

            fmt = context.user_data.get(
                "format",
                "PNG"
            )

            if fmt == "JPEG":
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")

            out = io.BytesIO()

            image.save(
                out,
                fmt
            )

            out.seek(0)

            ext = {
                "JPEG": "jpg",
                "PNG": "png",
                "WEBP": "webp"
            }.get(fmt, "png")

            await update.message.reply_document(
                document=InputFile(
                    out,
                    filename=f"converted.{ext}"
                ),
                caption="✅ فرمت عکس تغییر کرد."
            )

        # Resize
        elif action == "resize":

            width = context.user_data["width"]
            height = context.user_data["height"]

            image = image.resize(
                (width, height),
                Image.Resampling.LANCZOS
            )

            out = io.BytesIO()

            image.save(
                out,
                "PNG"
            )

            out.seek(0)

            await update.message.reply_document(
                document=InputFile(
                    out,
                    filename="resized.png"
                ),
                caption="✅ اندازه عکس تغییر کرد."
            )

        # Crop
        elif action == "crop":

            box = (
                context.user_data["left"],
                context.user_data["top"],
                context.user_data["right"],
                context.user_data["bottom"]
            )

            image = image.crop(box)

            out = io.BytesIO()

            image.save(
                out,
                "PNG"
            )

            out.seek(0)

            await update.message.reply_document(
                document=InputFile(
                    out,
                    filename="cropped.png"
                ),
                caption="✅ عکس برش خورد."
            )

        # Rotate
        elif action == "rotate":

            degrees = context.user_data["degrees"]

            image = image.rotate(
                degrees,
                expand=True
            )

            out = io.BytesIO()

            image.save(
                out,
                "PNG"
            )

            out.seek(0)

            await update.message.reply_document(
                document=InputFile(
                    out,
                    filename="rotated.png"
                ),
                caption="✅ عکس چرخانده شد."
            )

        # Black and white
        elif action == "bw":

            image = ImageOps.grayscale(
                image
            )

            out = io.BytesIO()

            image.save(
                out,
                "PNG"
            )

            out.seek(0)

            await update.message.reply_document(
                document=InputFile(
                    out,
                    filename="blackwhite.png"
                ),
                caption="✅ عکس سیاه‌وسفید شد."
            )

        # Image -> PDF
        elif action == "to_pdf":

            if image.mode != "RGB":
                image = image.convert("RGB")

            out = io.BytesIO()

            image.save(
                out,
                "PDF"
            )

            out.seek(0)

            await update.message.reply_document(
                document=InputFile(
                    out,
                    filename="image.pdf"
                ),
                caption="✅ عکس به PDF تبدیل شد."
            )

        context.user_data.clear()

    except Exception as e:

        print("PHOTO ERROR:", e)

        await update.message.reply_text(
            "❌ خطا هنگام پردازش عکس."
        )


# =========================================================
# DOCUMENT PROCESSING
# =========================================================

async def process_document(update, context):

    action = context.user_data.get("action")

    if not action:
        return

    data = await get_document(
        update,
        context
    )

    if data is None:
        return

    filename = (
        update.message.document.file_name
        or "file"
    )

    try:

        # GZIP
        if action == "gzip":

            out = io.BytesIO()

            with gzip.GzipFile(
                fileobj=out,
                mode="wb"
            ) as gz:

                gz.write(data)

            out.seek(0)

            await update.message.reply_document(
                document=InputFile(
                    out,
                    filename=filename + ".gz"
                ),
                caption="✅ فایل فشرده شد."
            )

        # ZIP extract
        elif action == "unzip":

            if not filename.lower().endswith(".zip"):

                await update.message.reply_text(
                    "❌ فایل ZIP ارسال کن."
                )

                return

            with zipfile.ZipFile(
                io.BytesIO(data)
            ) as z:

                files = z.namelist()

                if len(files) > 20:

                    await update.message.reply_text(
                        "❌ حداکثر ۲۰ فایل استخراج می‌شود."
                    )

                    return

                for name in files:

                    if name.endswith("/"):
                        continue

                    content = z.read(name)

                    safe_name = os.path.basename(
                        name
                    )

                    if not safe_name:
                        continue

                    await update.message.reply_document(
                        document=InputFile(
                            io.BytesIO(content),
                            filename=safe_name
                        )
                    )

            await update.message.reply_text(
                "✅ استخراج ZIP انجام شد."
            )

        # Rename
        elif action == "rename":

            new_name = context.user_data.get(
                "new_name"
            )

            if not new_name:
                return

            await update.message.reply_document(
                document=InputFile(
                    io.BytesIO(data),
                    filename=new_name
                ),
                caption="✅ نام فایل تغییر کرد."
            )

        context.user_data.clear()

    except Exception as e:

        print("FILE ERROR:", e)

        await update.message.reply_text(
            "❌ خطا هنگام پردازش فایل."
        )


# =========================================================
# TEXT PROCESSING
# =========================================================

async def process_text(update, context):

    action = context.user_data.get("action")

    if not action:
        return

    text = update.message.text

    # Count
    if action == "count":

        words = len(text.split())
        chars = len(text)
        lines = len(text.splitlines())

        await update.message.reply_text(
            "📊 <b>نتیجه</b>\n\n"
            f"🔤 کاراکتر: {chars}\n"
            f"📝 کلمه: {words}\n"
            f"📄 خط: {lines}",
            parse_mode="HTML"
        )

    # Clean
    elif action == "clean":

        result = re.sub(
            r"\s+",
            " ",
            text
        ).strip()

        await update.message.reply_text(
            f"🧹 متن پاک‌سازی‌شده:\n\n{result}"
        )

    # Reverse
    elif action == "reverse":

        await update.message.reply_text(
            text[::-1]
        )

    # Links
    elif action in (
        "links",
        "extracturl"
    ):

        links = re.findall(
            r'https?://[^\s]+',
            text
        )

        if links:

            await update.message.reply_text(
                "🔗 لینک‌ها:\n\n"
                + "\n".join(links)
            )

        else:

            await update.message.reply_text(
                "❌ لینکی پیدا نشد."
            )

    # Case
    elif action == "case":

        await update.message.reply_text(
            "🔠 Upper:\n"
            f"{text.upper()}\n\n"
            "🔡 Lower:\n"
            f"{text.lower()}"
        )

    # QR
    elif action == "qr":

        qr = qrcode.make(text)

        out = io.BytesIO()

        qr.save(
            out,
            "PNG"
        )

        out.seek(0)

        await update.message.reply_photo(
            photo=InputFile(
                out,
                filename="qr.png"
            ),
            caption="📱 QR ساخته شد."
        )

    # URL check
    elif action == "urlcheck":

        value = text.strip()

        if not re.match(
            r"^https?://",
            value,
            re.IGNORECASE
        ):
            value = "https://" + value

        from urllib.parse import urlparse

        parsed = urlparse(value)

        if parsed.netloc:

            await update.message.reply_text(
                "✅ ساختار لینک معتبر است.\n\n"
                f"🌐 دامنه: {parsed.netloc}\n"
                f"🔗 پروتکل: {parsed.scheme}"
            )

        else:

            await update.message.reply_text(
                "❌ ساختار لینک معتبر نیست."
            )

    # Base64 encode
    elif action == "encode":

        result = base64.b64encode(
            text.encode("utf-8")
        ).decode()

        await update.message.reply_text(
            "🔐 Base64:\n\n"
            f"<code>{result}</code>",
            parse_mode="HTML"
        )

    # Base64 decode
    elif action == "decode":

        try:

            result = base64.b64decode(
                text
            ).decode("utf-8")

            await update.message.reply_text(
                "🔓 متن:\n\n"
                f"{result}"
            )

        except:

            await update.message.reply_text(
                "❌ Base64 نامعتبر است."
            )

    # Random choice
    elif action == "choice":

        choices = [
            x.strip()
            for x in text.splitlines()
            if x.strip()
        ]

        if len(choices) < 2:

            await update.message.reply_text(
                "❌ حداقل دو گزینه بفرست؛"
                " هر گزینه در یک خط."
            )

        else:

            await update.message.reply_text(
                "🎯 انتخاب من:\n\n"
                f"✨ {random.choice(choices)}"
            )

    context.user_data.clear()


# =========================================================
# CALLBACK HANDLER
# =========================================================

async def buttons(update, context):

    query = update.callback_query

    await query.answer()

    data = query.data

    # Join
    if data == "check_join":

        if await is_member(
            context.bot,
            update.effective_user.id
        ):

            await query.edit_message_text(
                "✅ عضویت تأیید شد!"
            )

            await query.message.reply_text(
                "🛠 <b>منوی اصلی</b>",
                reply_markup=main_menu(),
                parse_mode="HTML"
            )

        else:

            await query.answer(
                "❌ هنوز عضو کانال نیستی.",
                show_alert=True
            )

        return

    # Access
    if not await is_member(
        context.bot,
        update.effective_user.id
    ):

        await query.edit_message_text(
            "🔐 ابتدا عضو @ByteTunnel شوید.",
            reply_markup=join_keyboard()
        )

        return

    # Main
    if data == "main":

        context.user_data.clear()

        await query.edit_message_text(
            "🛠 <b>منوی اصلی</b>",
            reply_markup=main_menu(),
            parse_mode="HTML"
        )

    # Pages
    elif data == "photo":
        await photo_page(update, context)

    elif data == "file":
        await file_page(update, context)

    elif data == "text":
        await text_page(update, context)

    elif data == "link":
        await link_page(update, context)

    elif data == "fun":
        await fun_page(update, context)

    elif data == "security":
        await security_page(update, context)

    # Photo actions
    elif data == "compress":

        context.user_data["action"] = "compress"

        await query.edit_message_text(
            "📉 عکس را ارسال کن."
        )

    elif data == "convert":

        await query.edit_message_text(
            "🔄 فرمت را انتخاب کن:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "JPG",
                        callback_data="jpg"
                    ),
                    InlineKeyboardButton(
                        "PNG",
                        callback_data="png"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "WEBP",
                        callback_data="webp"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="photo"
                    )
                ]
            ])
        )

    elif data in ("jpg", "png", "webp"):

        context.user_data["action"] = "convert"

        context.user_data["format"] = {
            "jpg": "JPEG",
            "png": "PNG",
            "webp": "WEBP"
        }[data]

        await query.edit_message_text(
            "📸 حالا عکس را ارسال کن."
        )

    elif data == "resize":

        context.user_data["action"] = "resize"

        await query.edit_message_text(
            "📐 عرض و ارتفاع را ارسال کن.\n\n"
            "مثال:\n"
            "<code>800 600</code>",
            parse_mode="HTML"
        )

    elif data == "crop":

        context.user_data["action"] = "crop"

        await query.edit_message_text(
            "✂️ مختصات را بفرست:\n\n"
            "<code>left top right bottom</code>\n\n"
            "مثال:\n"
            "<code>0 0 500 500</code>",
            parse_mode="HTML"
        )

    elif data == "rotate":

        context.user_data["action"] = "rotate"

        await query.edit_message_text(
            "🔃 زاویه را بفرست.\n\n"
            "مثال:\n"
            "<code>90</code>",
            parse_mode="HTML"
        )

    elif data == "bw":

        context.user_data["action"] = "bw"

        await query.edit_message_text(
            "🖤 عکس را ارسال کن."
        )

    elif data == "to_pdf":

        context.user_data["action"] = "to_pdf"

        await query.edit_message_text(
            "🖼 عکس را ارسال کن تا به PDF تبدیل شود."
        )

    # File
    elif data == "gzip":

        context.user_data["action"] = "gzip"

        await query.edit_message_text(
            "🗜 فایل را ارسال کن."
        )

    elif data == "unzip":

        context.user_data["action"] = "unzip"

        await query.edit_message_text(
            "📦 فایل ZIP را ارسال کن."
        )

    elif data == "rename":

        context.user_data["action"] = "rename"

        await query.edit_message_text(
            "📝 نام جدید فایل را ارسال کن.\n\n"
            "مثال:\n"
            "<code>test.txt</code>",
            parse_mode="HTML"
        )

    # Text
    elif data in (
        "count",
        "clean",
        "reverse",
        "links",
        "case"
    ):

        context.user_data["action"] = data

        await query.edit_message_text(
            "📝 متن را ارسال کن."
        )

    # Link
    elif data in (
        "qr",
        "urlcheck",
        "extracturl"
    ):

        context.user_data["action"] = data

        await query.edit_message_text(
            "🔗 متن یا لینک را ارسال کن."
        )

    # Security
    elif data in (
        "encode",
        "decode"
    ):

        context.user_data["action"] = data

        await query.edit_message_text(
            "🔐 متن را ارسال کن."
        )

    elif data == "password":

        alphabet = (
            string.ascii_letters
            + string.digits
            + "!@#$%^&*"
        )

        password = "".join(
            secrets.choice(alphabet)
            for _ in range(16)
        )

        await query.edit_message_text(
            "🔑 <b>رمز تصادفی:</b>\n\n"
            f"<code>{password}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔑 رمز جدید",
                        callback_data="password"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="security"
                    )
                ]
            ])
        )

    # Fun
    elif data == "dice":

        n = random.randint(1, 6)

        await query.edit_message_text(
            f"🎲 نتیجه تاس:\n\n<b>{n}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎲 دوباره",
                        callback_data="dice"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="fun"
                    )
                ]
            ])
        )

    elif data == "coin":

        result = random.choice(
            ["🟡 شیر", "⚪ خط"]
        )

        await query.edit_message_text(
            f"🪙 نتیجه:\n\n<b>{result}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🪙 دوباره",
                        callback_data="coin"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="fun"
                    )
                ]
            ])
        )

    elif data == "random":

        n = random.randint(1, 100)

        await query.edit_message_text(
            f"🔢 عدد تصادفی:\n\n<b>{n}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔢 دوباره",
                        callback_data="random"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="fun"
                    )
                ]
            ])
        )

    elif data == "choice":

        context.user_data["action"] = "choice"

        await query.edit_message_text(
            "🎯 گزینه‌ها را بفرست.\n"
            "هر گزینه در یک خط."
        )

    elif data == "riddle":

        riddles = [
            (
                "هرچه از آن برداری بزرگ‌تر می‌شود. چیست؟",
                "چاله"
            ),
            (
                "پا دارد ولی راه نمی‌رود. چیست؟",
                "میز"
            ),
            (
                "کلید دارد ولی قفل ندارد. چیست؟",
                "پیانو"
            )
        ]

        q, a = random.choice(riddles)

        await query.edit_message_text(
            f"🧩 <b>معما</b>\n\n{q}\n\n"
            f"💡 جواب: <tg-spoiler>{a}</tg-spoiler>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🧩 معمای جدید",
                        callback_data="riddle"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="fun"
                    )
                ]
            ])
        )

    # Admin
    elif data == "admin":

        if update.effective_user.id != ADMIN_ID:

            await query.answer(
                "⛔ دسترسی ندارید.",
                show_alert=True
            )

            return

        await query.edit_message_text(
            "👑 <b>پنل مدیریت</b>\n\n"
            "📊 آمار کاربران:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📊 نمایش آمار",
                        callback_data="stats"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="main"
                    )
                ]
            ]),
            parse_mode="HTML"
        )

    elif data == "stats":

        if update.effective_user.id != ADMIN_ID:
            return

        await query.edit_message_text(
            f"📊 <b>آمار بات</b>\n\n"
            f"👥 کاربران: <b>{user_count()}</b>",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 بازگشت",
                        callback_data="admin"
                    )
                ]
            ]),
            parse_mode="HTML"
        )


# =========================================================
# TEXT INPUT HANDLER
# =========================================================

async def text_handler(update, context):

    if not await check_access(
        update,
        context
    ):
        return

    action = context.user_data.get("action")

    if not action:
        return

    text = update.message.text.strip()

    # Resize
    if action == "resize":

        try:

            parts = text.split()

            if len(parts) != 2:
                raise ValueError

            width = int(parts[0])
            height = int(parts[1])

            if width < 1 or height < 1:
                raise ValueError

            if width > 5000 or height > 5000:
                raise ValueError

            context.user_data["width"] = width
            context.user_data["height"] = height

            await update.message.reply_text(
                "✅ ابعاد ثبت شد.\n"
                "📸 حالا عکس را ارسال کن."
            )

        except:

            await update.message.reply_text(
                "❌ فرمت اشتباه.\n\n"
                "مثال:\n800 600"
            )

        return

    # Crop
    if action == "crop":

        try:

            p = list(
                map(int, text.split())
            )

            if len(p) != 4:
                raise ValueError

            left, top, right, bottom = p

            if right <= left or bottom <= top:
                raise ValueError

            context.user_data.update({
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom
            })

            await update.message.reply_text(
                "✅ مختصات ثبت شد.\n"
                "📸 حالا عکس را ارسال کن."
            )

        except:

            await update.message.reply_text(
                "❌ فرمت اشتباه.\n\n"
                "مثال:\n0 0 500 500"
            )

        return

    # Rotate
    if action == "rotate":

        try:

            context.user_data["degrees"] = int(text)

            await update.message.reply_text(
                "✅ زاویه ثبت شد.\n"
                "📸 حالا عکس را ارسال کن."
            )

        except:

            await update.message.reply_text(
                "❌ یک عدد مثل 90 ارسال کن."
            )

        return

    # Rename
    if action == "rename":

        if len(text) > 100:

            await update.message.reply_text(
                "❌ نام خیلی طولانی است."
            )

            return

        context.user_data["new_name"] = text

        await update.message.reply_text(
            "✅ نام ثبت شد.\n"
            "📁 حالا فایل را ارسال کن."
        )

        return

    # Everything else
    await process_text(
        update,
        context
    )


# =========================================================
# START
# =========================================================

async def start(update, context):

    context.user_data.clear()

    save_user(
        update.effective_user
    )

    if not await check_access(
        update,
        context
    ):
        return

    await update.message.reply_text(
        "🛠 <b>بات ابزار همه‌کاره</b>\n\n"
        "یکی از گزینه‌ها را انتخاب کن:",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )


# =========================================================
# MEDIA HANDLERS
# =========================================================

async def photo_handler(update, context):

    if not await check_access(
        update,
        context
    ):
        return

    await process_photo(
        update,
        context
    )


async def document_handler(update, context):

    if not await check_access(
        update,
        context
    ):
        return

    await process_document(
        update,
        context
    )


# =========================================================
# ERROR
# =========================================================

async def error_handler(update, context):
    print("ERROR:", context.error)


# =========================================================
# MAIN
# =========================================================

def main():

    init_db()

    app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            buttons
        )
    )

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Document.ALL,
            document_handler
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    app.add_error_handler(
        error_handler
    )

    print("================================")
    print("🤖 Tool Bot is running...")
    print("📢 Channel:", CHANNEL)
    print("================================")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
