
import os, io, re, gzip, base64, zipfile, sqlite3, secrets, string, random, urllib.parse, asyncio
from datetime import datetime, timezone
from PIL import Image, ImageOps, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL = os.getenv("FORCE_CHANNEL", "@ByteTunnel")
DB_NAME = "bot.db"
MAX_FILE = 45 * 1024 * 1024

if not TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده")

# ---------- DATABASE ----------
def db():
    return sqlite3.connect(DB_NAME)

def init_db():
    c = db(); cur = c.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        joined_at TEXT DEFAULT '',
        last_seen TEXT DEFAULT '',
        uses INTEGER DEFAULT 0,
        banned INTEGER DEFAULT 0
    )""")
    # Upgrade old databases safely
    cur.execute("PRAGMA table_info(users)")
    cols = {r[1] for r in cur.fetchall()}
    for name, typ, default in [
        ("username","TEXT","''"), ("first_name","TEXT","''"),
        ("joined_at","TEXT","''"), ("last_seen","TEXT","''"),
        ("uses","INTEGER","0"), ("banned","INTEGER","0")
    ]:
        if name not in cols:
            cur.execute(f"ALTER TABLE users ADD COLUMN {name} {typ} DEFAULT {default}")
    cur.execute("""CREATE TABLE IF NOT EXISTS usage(
        user_id INTEGER, tool TEXT, count INTEGER DEFAULT 0,
        PRIMARY KEY(user_id, tool)
    )""")
    c.commit(); c.close()

def save_user(u):
    now = datetime.now(timezone.utc).isoformat()
    c=db(); cur=c.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (u.id,))
    exists=cur.fetchone()
    if exists:
        cur.execute("UPDATE users SET username=?,first_name=?,last_seen=? WHERE user_id=?",
                    (u.username or "",u.first_name or "",now,u.id))
    else:
        cur.execute("""INSERT INTO users(user_id,username,first_name,joined_at,last_seen)
                       VALUES(?,?,?,?,?)""",(u.id,u.username or "",u.first_name or "",now,now))
    c.commit(); c.close()

def add_use(uid, tool):
    c=db(); cur=c.cursor()
    cur.execute("UPDATE users SET uses=uses+1,last_seen=? WHERE user_id=?",
                (datetime.now(timezone.utc).isoformat(),uid))
    cur.execute("""INSERT INTO usage(user_id,tool,count) VALUES(?,?,1)
                   ON CONFLICT(user_id,tool) DO UPDATE SET count=count+1""",(uid,tool))
    c.commit(); c.close()

def user_stats(uid):
    c=db(); cur=c.cursor()
    cur.execute("SELECT uses,joined_at FROM users WHERE user_id=?",(uid,))
    r=cur.fetchone()
    cur.execute("SELECT tool,count FROM usage WHERE user_id=? ORDER BY count DESC LIMIT 8",(uid,))
    tools=cur.fetchall(); c.close()
    return (r or (0,"")), tools

def global_stats():
    c=db(); cur=c.cursor()
    cur.execute("SELECT COUNT(*) FROM users"); total=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE banned=1"); banned=cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(uses),0) FROM users"); uses=cur.fetchone()[0]
    cur.execute("SELECT tool,SUM(count) FROM usage GROUP BY tool ORDER BY SUM(count) DESC LIMIT 10")
    tools=cur.fetchall(); c.close()
    return total,banned,uses,tools

def users_for_broadcast():
    c=db(); cur=c.cursor(); cur.execute("SELECT user_id FROM users WHERE banned=0")
    r=[x[0] for x in cur.fetchall()]; c.close(); return r

def set_ban(uid,val):
    c=db(); c.execute("UPDATE users SET banned=? WHERE user_id=?",(val,uid)); c.commit(); c.close()

def is_banned(uid):
    c=db(); cur=c.cursor(); cur.execute("SELECT banned FROM users WHERE user_id=?",(uid,))
    r=cur.fetchone(); c.close(); return bool(r and r[0])

# ---------- ACCESS ----------
async def is_member(bot, uid):
    try:
        m=await bot.get_chat_member(CHANNEL,uid)
        return m.status in ("member","administrator","creator")
    except Exception:
        return False

def join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال",url=f"https://t.me/{CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton("✅ بررسی عضویت",callback_data="joincheck")]
    ])

async def allowed(update,ctx):
    uid=update.effective_user.id
    if is_banned(uid):
        if update.callback_query: await update.callback_query.answer("⛔ دسترسی شما مسدود است.",show_alert=True)
        else: await update.message.reply_text("⛔ دسترسی شما مسدود است.")
        return False
    if not await is_member(ctx.bot,uid):
        msg="🔐 برای استفاده از <b>ToolBox</b> ابتدا عضو کانال شوید."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg,parse_mode="HTML",reply_markup=join_keyboard())
        else:
            await update.message.reply_text(msg,parse_mode="HTML",reply_markup=join_keyboard())
        return False
    return True

# ---------- UI ----------
def nav(back="home"):
    return [InlineKeyboardButton("🔙 بازگشت",callback_data=back),InlineKeyboardButton("🏠 خانه",callback_data="home")]

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 تصویر",callback_data="photo"),InlineKeyboardButton("📁 فایل",callback_data="file")],
        [InlineKeyboardButton("📝 متن",callback_data="text"),InlineKeyboardButton("🔗 لینک",callback_data="link")],
        [InlineKeyboardButton("🧮 کاربردی",callback_data="util"),InlineKeyboardButton("🎲 سرگرمی",callback_data="fun")],
        [InlineKeyboardButton("🔐 امنیت",callback_data="security"),InlineKeyboardButton("🎨 خلاقانه",callback_data="creative")],
        [InlineKeyboardButton("👤 پروفایل",callback_data="profile_user"),InlineKeyboardButton("📊 آمار",callback_data="my_stats")],
        [InlineKeyboardButton("ℹ️ راهنما",callback_data="help"),InlineKeyboardButton("👑 مدیریت",callback_data="admin")]
    ])

def category(title, buttons):
    return InlineKeyboardMarkup(buttons + [nav()])

def photo_keyboard():
    return category("تصویر",[
        [InlineKeyboardButton("📉 کاهش حجم",callback_data="compress"),InlineKeyboardButton("🔄 تبدیل فرمت",callback_data="convert")],
        [InlineKeyboardButton("📐 تغییر اندازه",callback_data="resize"),InlineKeyboardButton("✂️ برش",callback_data="crop")],
        [InlineKeyboardButton("🔃 چرخش",callback_data="rotate"),InlineKeyboardButton("🖤 سیاه‌وسفید",callback_data="bw")],
        [InlineKeyboardButton("🖼 اطلاعات عکس",callback_data="imginfo"),InlineKeyboardButton("🧹 پاک‌سازی متادیتا",callback_data="strip")],
        [InlineKeyboardButton("🪪 عکس پروفایل",callback_data="profile_img"),InlineKeyboardButton("📄 عکس به PDF",callback_data="topdf")],
    ])

def file_keyboard():
    return category("فایل",[
        [InlineKeyboardButton("📦 ساخت ZIP",callback_data="makezip"),InlineKeyboardButton("🗜 GZIP",callback_data="gzip")],
        [InlineKeyboardButton("📂 استخراج ZIP",callback_data="unzip"),InlineKeyboardButton("📝 تغییر نام",callback_data="rename")],
        [InlineKeyboardButton("📊 مشخصات فایل",callback_data="fileinfo")],
    ])

def text_keyboard():
    return category("متن",[
        [InlineKeyboardButton("🔢 شمارش",callback_data="count"),InlineKeyboardButton("🧹 پاک‌سازی",callback_data="clean")],
        [InlineKeyboardButton("🔄 برعکس",callback_data="reverse"),InlineKeyboardButton("🔗 استخراج لینک",callback_data="links")],
        [InlineKeyboardButton("🔤 حروف بزرگ/کوچک",callback_data="case"),InlineKeyboardButton("📋 حذف تکراری",callback_data="dedupe")],
        [InlineKeyboardButton("↕️ مرتب‌سازی",callback_data="sortlines"),InlineKeyboardButton("✨ استایل متن",callback_data="styles")],
    ])

def link_keyboard():
    return category("لینک",[
        [InlineKeyboardButton("📱 ساخت QR",callback_data="qr"),InlineKeyboardButton("🔍 بررسی URL",callback_data="urlcheck")],
        [InlineKeyboardButton("🧹 پاک‌سازی URL",callback_data="cleanurl"),InlineKeyboardButton("🔗 استخراج لینک",callback_data="extracturl")],
    ])

def util_keyboard():
    return category("کاربردی",[
        [InlineKeyboardButton("🧮 ماشین حساب",callback_data="calc"),InlineKeyboardButton("📏 تبدیل واحد",callback_data="units")],
        [InlineKeyboardButton("📅 تاریخ و زمان",callback_data="date"),InlineKeyboardButton("⏱ تایمر",callback_data="timer")],
        [InlineKeyboardButton("🔑 رمز تصادفی",callback_data="password")],
    ])

def fun_keyboard():
    return category("سرگرمی",[
        [InlineKeyboardButton("🎲 تاس",callback_data="dice"),InlineKeyboardButton("🪙 شیر/خط",callback_data="coin")],
        [InlineKeyboardButton("🔢 عدد تصادفی",callback_data="random"),InlineKeyboardButton("🎯 انتخاب تصادفی",callback_data="choice")],
        [InlineKeyboardButton("🧩 معما",callback_data="riddle"),InlineKeyboardButton("🎮 حدس عدد",callback_data="guess")],
        [InlineKeyboardButton("🧠 سوال عمومی",callback_data="quiz")],
    ])

def security_keyboard():
    return category("امنیت",[
        [InlineKeyboardButton("🔐 Base64 Encode",callback_data="encode"),InlineKeyboardButton("🔓 Base64 Decode",callback_data="decode")],
        [InlineKeyboardButton("🔑 رمز تصادفی",callback_data="password")],
    ])

def creative_keyboard():
    return category("خلاقانه",[
        [InlineKeyboardButton("📝 متن روی عکس",callback_data="textimage"),InlineKeyboardButton("🧩 کلاژ",callback_data="collage")],
        [InlineKeyboardButton("🔳 عکس به ASCII",callback_data="ascii")],
    ])

# ---------- START / HOME ----------
def welcome_text(u):
    return (f"╭━━━━━━━━━━━━━━━━━━━━╮\n"
            f"       🛠 <b>TOOLBOX</b>\n"
            f"    ابزار همه‌کاره تلگرام\n"
            f"╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            f"👋 سلام <b>{u.first_name or 'دوست من'}</b>!\n"
            f"⚡ یک ابزار انتخاب کن:\n\n"
            f"🛠 سریع، ساده و کاربردی")

async def show_home(update,ctx,edit=False):
    text=welcome_text(update.effective_user)
    if edit: await update.callback_query.edit_message_text(text,parse_mode="HTML",reply_markup=main_keyboard())
    else: await update.message.reply_text(text,parse_mode="HTML",reply_markup=main_keyboard())

async def start(update,ctx):
    save_user(update.effective_user); ctx.user_data.clear()
    if not await allowed(update,ctx): return
    await show_home(update,ctx)

# ---------- CALLBACK ----------
async def callback(update,ctx):
    q=update.callback_query; d=q.data
    await q.answer()
    if d=="joincheck":
        if await is_member(ctx.bot,update.effective_user.id):
            await q.edit_message_text("✅ عضویت تأیید شد.")
            await q.message.reply_text(welcome_text(update.effective_user),parse_mode="HTML",reply_markup=main_keyboard())
        else: await q.answer("❌ هنوز عضویت تأیید نشده.",show_alert=True)
        return
    if not await allowed(update,ctx): return

    pages={"photo":("🖼 <b>ابزار تصویر</b>",photo_keyboard),
           "file":("📁 <b>ابزار فایل</b>",file_keyboard),
           "text":("📝 <b>ابزار متن</b>",text_keyboard),
           "link":("🔗 <b>ابزار لینک</b>",link_keyboard),
           "util":("🧮 <b>ابزار کاربردی</b>",util_keyboard),
           "fun":("🎲 <b>سرگرمی</b>",fun_keyboard),
           "security":("🔐 <b>امنیت</b>",security_keyboard),
           "creative":("🎨 <b>ابزار خلاقانه</b>",creative_keyboard)}
    if d=="home":
        ctx.user_data.clear(); await show_home(update,ctx,True); return
    if d in pages:
        ctx.user_data.clear(); t,k=pages[d]; await q.edit_message_text(t,parse_mode="HTML",reply_markup=k()); return
    if d in ("profile_user","my_stats"):
        (uses,joined),tools=user_stats(update.effective_user.id)
        top="\n".join(f"• {t}: {n}" for t,n in tools) or "هنوز استفاده‌ای ثبت نشده"
        if d=="profile_user":
            text=f"👤 <b>پروفایل</b>\n\n🆔 {update.effective_user.id}\n👤 @{update.effective_user.username or 'ندارد'}\n📅 عضویت: {joined[:10] if joined else '-'}\n🛠 استفاده: {uses}"
        else:
            text=f"📊 <b>آمار شما</b>\n\n🛠 مجموع استفاده: {uses}\n\n🔥 ابزارهای پرکاربرد:\n{top}"
        await q.edit_message_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([nav()])); return
    if d=="help":
        text=("ℹ️ <b>راهنمای ToolBox</b>\n\n"
              "از منوی اصلی یک دسته را انتخاب کن و سپس ابزار موردنظر را بزن.\n"
              "برای بیشتر ابزارها کافی است متن، عکس یا فایل را ارسال کنی.\n\n"
              "🔙 بازگشت و 🏠 خانه همیشه در دسترس هستند.")
        await q.edit_message_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([nav()])); return

    if d=="admin":
        if update.effective_user.id!=ADMIN_ID:
            await q.answer("⛔ فقط ادمین.",show_alert=True); return
        await q.edit_message_text("👑 <b>پنل مدیریت</b>",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 آمار کلی",callback_data="stats")],
            [InlineKeyboardButton("📢 ارسال همگانی",callback_data="broadcast")],
            [InlineKeyboardButton("🚫 مسدود کردن",callback_data="ban"),InlineKeyboardButton("✅ رفع مسدودی",callback_data="unban")],
            [InlineKeyboardButton("🏠 خانه",callback_data="home")]
        ])); return
    if d=="stats":
        total,banned,uses,tools=global_stats()
        top="\n".join(f"• {t}: {n}" for t,n in tools) or "-"
        text=f"📊 <b>آمار کلی</b>\n\n👥 کاربران: {total}\n🚫 مسدود: {banned}\n🛠 کل استفاده: {uses}\n\n🔥 ابزارهای محبوب:\n{top}"
        await q.edit_message_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 مدیریت",callback_data="admin")],[InlineKeyboardButton("🏠 خانه",callback_data="home")]])); return
    if d in ("broadcast","ban","unban"):
        if update.effective_user.id!=ADMIN_ID:return
        ctx.user_data["action"]=d
        await q.edit_message_text("📢 پیام را بفرست." if d=="broadcast" else "🆔 آیدی عددی کاربر را بفرست.",reply_markup=InlineKeyboardMarkup([nav("admin")]))
        return

    photo_actions={"compress","bw","strip","profile_img","topdf","imginfo","ascii","collage","textimage"}
    if d in photo_actions:
        ctx.user_data={"action":d,"photos":[]}
        prompts={"compress":"📸 عکس را ارسال کن.","bw":"📸 عکس را ارسال کن.","strip":"📸 عکس را ارسال کن.",
                 "profile_img":"📸 عکس را ارسال کن.","topdf":"📸 عکس را ارسال کن.","imginfo":"📸 عکس را ارسال کن.",
                 "ascii":"📸 عکس را ارسال کن.","collage":"🧩 عکس‌ها را یکی‌یکی بفرست؛ بعد از حداقل ۲ عکس، /done را بزن.",
                 "textimage":"📝 اول متن را بفرست."}
        await q.edit_message_text(prompts[d]); return
    if d=="convert":
        await q.edit_message_text("🔄 فرمت خروجی:",reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("JPG",callback_data="jpg"),InlineKeyboardButton("PNG",callback_data="png"),InlineKeyboardButton("WEBP",callback_data="webp")],
            nav("photo")]))
        return
    if d in ("jpg","png","webp"):
        ctx.user_data={"action":"convert","format":{"jpg":"JPEG","png":"PNG","webp":"WEBP"}[d]}
        await q.edit_message_text("📸 عکس را ارسال کن."); return
    if d in ("resize","crop","rotate"):
        ctx.user_data["action"]=d
        prompts={"resize":"📐 عرض و ارتفاع را مثل `800 600` بفرست.","crop":"✂️ چهار عدد مثل `0 0 500 500` بفرست.","rotate":"🔃 زاویه مثل `90` بفرست."}
        await q.edit_message_text(prompts[d],parse_mode="Markdown"); return

    if d in ("makezip","gzip","unzip","rename","fileinfo"):
        ctx.user_data={"action":d,"files":[]}
        await q.edit_message_text("📁 فایل را ارسال کن." if d!="makezip" else "📦 فایل‌ها را ارسال کن و در پایان /done بزن."); return

    text_actions={"count","clean","reverse","links","case","dedupe","sortlines","styles","qr","urlcheck","cleanurl","extracturl","encode","decode","calc","units","date","timer","choice","textimage"}
    if d in text_actions:
        ctx.user_data={"action":d}
        prompts={"qr":"📱 متن یا لینک را بفرست.","calc":"🧮 مثال: `12*(5+2)`","units":"📏 مثال: `10 km m`","date":"📅 `now` یا `2026-08-17`",
                 "timer":"⏱ زمان را به ثانیه بفرست.","choice":"🎯 گزینه‌ها را هرکدام در یک خط بفرست.","textimage":"📝 متن را بفرست."}
        await q.edit_message_text(prompts.get(d,"📝 متن را بفرست."),parse_mode="Markdown"); return

    if d=="password":
        alphabet=string.ascii_letters+string.digits+"!@#$%^&*"
        p="".join(secrets.choice(alphabet) for _ in range(18)); add_use(update.effective_user.id,"password")
        await q.edit_message_text(f"🔑 <code>{p}</code>",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 رمز جدید",callback_data="password")],nav("security")]))
        return
    if d in ("dice","coin","random"):
        add_use(update.effective_user.id,d)
        r=random.randint(1,6) if d=="dice" else random.choice(["🟡 شیر","⚪ خط"]) if d=="coin" else random.randint(1,100)
        await q.edit_message_text(f"🎲 نتیجه: <b>{r}</b>",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 دوباره",callback_data=d)],nav("fun")]))
        return
    if d=="riddle":
        a,b=random.choice([("چه چیزی هرچه بیشتر از آن برداری بزرگ‌تر می‌شود؟","چاله"),("پا دارد ولی راه نمی‌رود؟","میز"),("کلید دارد ولی قفل ندارد؟","پیانو")])
        await q.edit_message_text(f"🧩 {a}\n\n💡 <tg-spoiler>{b}</tg-spoiler>",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧩 جدید",callback_data="riddle")],nav("fun")]))
        return
    if d=="guess":
        ctx.user_data={"action":"guess","target":random.randint(1,20)}; await q.edit_message_text("🎮 عدد بین 1 تا 20 را حدس بزن."); return
    if d=="quiz":
        a,b=random.choice([("پایتخت فرانسه؟","پاریس"),("بزرگ‌ترین سیاره منظومه شمسی؟","مشتری"),("آب در چند درجه می‌جوشد؟","100")])
        ctx.user_data={"action":"quiz","answer":b.lower()}; await q.edit_message_text(f"🧠 {a}"); return

# ---------- TEXT ----------
async def text_handler(update,ctx):
    if not await allowed(update,ctx): return
    action=ctx.user_data.get("action")
    if not action:return
    text=update.message.text.strip()

    if action=="resize":
        try:
            a=list(map(int,text.split())); assert len(a)==2 and all(1<=x<=5000 for x in a)
            ctx.user_data.update(width=a[0],height=a[1]); await update.message.reply_text("📸 حالا عکس را بفرست.")
        except: await update.message.reply_text("❌ مثال: 800 600")
        return
    if action=="crop":
        try:
            a=list(map(int,text.split())); assert len(a)==4 and a[2]>a[0] and a[3]>a[1]
            ctx.user_data.update(left=a[0],top=a[1],right=a[2],bottom=a[3]); await update.message.reply_text("📸 حالا عکس را بفرست.")
        except: await update.message.reply_text("❌ مثال: 0 0 500 500")
        return
    if action=="rotate":
        try: ctx.user_data["degrees"]=int(text); await update.message.reply_text("📸 حالا عکس را بفرست.")
        except: await update.message.reply_text("❌ عدد بفرست.")
        return
    if action=="rename":
        ctx.user_data["new_name"]=text; await update.message.reply_text("📁 حالا فایل را بفرست."); return
    if action=="textimage":
        ctx.user_data["caption_text"]=text; ctx.user_data["action"]="textimage_photo"; await update.message.reply_text("🖼 حالا عکس را بفرست."); return
    if action=="guess":
        try:
            n=int(text); t=ctx.user_data["target"]
            if n==t: await update.message.reply_text("🎉 درست حدس زدی!"); ctx.user_data.clear()
            elif n<t: await update.message.reply_text("⬆️ بزرگ‌تر")
            else: await update.message.reply_text("⬇️ کوچک‌تر")
        except: await update.message.reply_text("❌ عدد وارد کن.")
        return
    if action=="quiz":
        await update.message.reply_text("🎉 درست!" if text.lower()==ctx.user_data["answer"] else "❌ جواب اشتباه بود.")
        ctx.user_data.clear(); return
    if action=="broadcast" and update.effective_user.id==ADMIN_ID:
        ok=0
        for uid in users_for_broadcast():
            try: await ctx.bot.send_message(uid,text); ok+=1
            except: pass
        await update.message.reply_text(f"📢 ارسال شد: {ok} کاربر"); ctx.user_data.clear(); return
    if action in ("ban","unban") and update.effective_user.id==ADMIN_ID:
        try: set_ban(int(text),1 if action=="ban" else 0); await update.message.reply_text("✅ انجام شد.")
        except: await update.message.reply_text("❌ آیدی نامعتبر.")
        ctx.user_data.clear(); return

    add_use(update.effective_user.id,action)
    await update.message.reply_text("⏳ در حال پردازش...")
    try:
        if action=="count": r=f"🔢 حروف: {len(text)}\n📝 کلمات: {len(text.split())}\n📄 خطوط: {len(text.splitlines())}"
        elif action=="clean": r=re.sub(r"\s+"," ",text).strip()
        elif action=="reverse": r=text[::-1]
        elif action in ("links","extracturl"): r="\n".join(re.findall(r"https?://[^\s]+",text)) or "❌ لینکی پیدا نشد."
        elif action=="case": r=f"🔠 {text.upper()}\n\n🔡 {text.lower()}"
        elif action=="dedupe": r="\n".join(dict.fromkeys(text.splitlines()))
        elif action=="sortlines": r="\n".join(sorted(text.splitlines(),key=str.lower))
        elif action=="styles": r=f"**{text}**\n__{text}__\n`{text}`"
        elif action=="urlcheck":
            v=text if re.match(r"^https?://",text,re.I) else "https://"+text; p=urllib.parse.urlparse(v)
            r=f"✅ پروتکل: {p.scheme}\n🌐 دامنه: {p.netloc}\n📍 مسیر: {p.path}" if p.netloc else "❌ URL نامعتبر"
        elif action=="cleanurl":
            p=urllib.parse.urlsplit(text if "://" in text else "https://"+text); r=urllib.parse.urlunsplit((p.scheme,p.netloc,p.path,"",""))
        elif action=="encode": r=base64.b64encode(text.encode()).decode()
        elif action=="decode": r=base64.b64decode(text).decode()
        elif action=="calc":
            if not re.fullmatch(r"[0-9+\-*/(). %]+",text): raise ValueError
            r=str(eval(text,{"__builtins__":{}},{}))
        elif action=="units":
            m=re.fullmatch(r"\s*([0-9.]+)\s*(km|m|cm|mm|kg|g)\s+(km|m|cm|mm|kg|g)\s*",text.lower())
            if not m: r="❌ مثال: 10 km m"
            else:
                f={"km":1000,"m":1,"cm":.01,"mm":.001,"kg":1000,"g":1}; r=str(float(m.group(1))*f[m.group(2)]/f[m.group(3)])
        elif action=="date":
            if text.lower()=="now": r=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            else: r=datetime.strptime(text,"%Y-%m-%d").strftime("%A, %d %B %Y")
        elif action=="choice":
            opts=[x for x in text.splitlines() if x.strip()]; r="🎯 "+(random.choice(opts) if opts else "❌ گزینه‌ای نیست.")
        else: r="❌ دستور نامعتبر."
        await update.message.reply_text(r,parse_mode="Markdown" if action=="styles" else None)
        if action!="timer": ctx.user_data.clear()
    except Exception: await update.message.reply_text("❌ ورودی نامعتبر یا پردازش ناموفق بود."); ctx.user_data.clear()

# ---------- PHOTOS ----------
async def photo_data(update,ctx):
    f=await ctx.bot.get_file(update.message.photo[-1].file_id)
    return bytes(await f.download_as_bytearray())

async def photo_handler(update,ctx):
    if not await allowed(update,ctx): return
    action=ctx.user_data.get("action")
    if not action:return
    data=await photo_data(update,ctx)
    add_use(update.effective_user.id,action)
    if action=="collage":
        ctx.user_data.setdefault("photos",[]).append(data)
        await update.message.reply_text("✅ عکس اضافه شد. عکس بعدی را بفرست؛ حداقل ۲ عکس و سپس /done.")
        return
    try:
        await update.message.reply_text("⏳ در حال پردازش...")
        im=Image.open(io.BytesIO(data))
        if action=="compress":
            im=im.convert("RGB"); out=io.BytesIO(); im.save(out,"JPEG",quality=45,optimize=True); out.seek(0); await update.message.reply_document(InputFile(out,"compressed.jpg"))
        elif action=="convert":
            fmt=ctx.user_data["format"]; 
            if fmt=="JPEG": im=im.convert("RGB")
            out=io.BytesIO(); im.save(out,fmt); out.seek(0); await update.message.reply_document(InputFile(out,"converted."+fmt.lower().replace("jpeg","jpg")))
        elif action=="resize":
            im=im.resize((ctx.user_data["width"],ctx.user_data["height"]),Image.Resampling.LANCZOS); out=io.BytesIO(); im.save(out,"PNG"); out.seek(0); await update.message.reply_document(InputFile(out,"resized.png"))
        elif action=="crop":
            im=im.crop((ctx.user_data["left"],ctx.user_data["top"],ctx.user_data["right"],ctx.user_data["bottom"])); out=io.BytesIO(); im.save(out,"PNG"); out.seek(0); await update.message.reply_document(InputFile(out,"cropped.png"))
        elif action=="rotate":
            im=im.rotate(ctx.user_data["degrees"],expand=True); out=io.BytesIO(); im.save(out,"PNG"); out.seek(0); await update.message.reply_document(InputFile(out,"rotated.png"))
        elif action=="bw":
            im=ImageOps.grayscale(im); out=io.BytesIO(); im.save(out,"PNG"); out.seek(0); await update.message.reply_document(InputFile(out,"bw.png"))
        elif action in ("strip","profile_img"):
            if action=="profile_img": im=ImageOps.fit(im,(800,800))
            else: im=im.convert("RGB")
            out=io.BytesIO(); im.save(out,"JPEG",quality=92); out.seek(0); await update.message.reply_document(InputFile(out,"clean.jpg"))
        elif action=="imginfo": await update.message.reply_text(f"📐 {im.width}×{im.height}\n🗂 فرمت: {im.format or '-'}\n🎨 حالت: {im.mode}")
        elif action=="topdf":
            im=im.convert("RGB"); out=io.BytesIO(); im.save(out,"PDF"); out.seek(0); await update.message.reply_document(InputFile(out,"image.pdf"))
        elif action=="ascii":
            im.thumbnail((80,80)); im=ImageOps.grayscale(im); chars=" .:-=+*#%@"; px=im.load()
            s="\n".join("".join(chars[px[x,y]*len(chars)//256] for x in range(im.width)) for y in range(im.height))
            await update.message.reply_text("```text\n"+s+"\n```",parse_mode="Markdown")
        elif action=="textimage_photo":
            draw=ImageDraw.Draw(im); draw.rectangle((0,im.height-100,im.width,im.height),fill=(0,0,0))
            draw.text((20,im.height-75),ctx.user_data["caption_text"],fill=(255,255,255))
            out=io.BytesIO(); im.convert("RGB").save(out,"JPEG",quality=90); out.seek(0); await update.message.reply_photo(InputFile(out,"text-image.jpg"))
        ctx.user_data.clear()
    except Exception as e:
        print("PHOTO ERROR:",e); await update.message.reply_text("❌ پردازش عکس انجام نشد."); ctx.user_data.clear()

# ---------- FILES ----------
async def document_handler(update,ctx):
    if not await allowed(update,ctx): return
    action=ctx.user_data.get("action")
    if not action:return
    d=update.message.document
    if d.file_size and d.file_size>MAX_FILE: await update.message.reply_text("❌ فایل بیش از حد مجاز است."); return
    f=await ctx.bot.get_file(d.file_id); data=bytes(await f.download_as_bytearray())
    name=d.file_name or "file"; add_use(update.effective_user.id,action)
    try:
        await update.message.reply_text("⏳ در حال پردازش...")
        if action=="gzip":
            out=io.BytesIO()
            with gzip.GzipFile(fileobj=out,mode="wb") as z:z.write(data)
            out.seek(0); await update.message.reply_document(InputFile(out,name+".gz")); ctx.user_data.clear()
        elif action=="unzip":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names=[n for n in z.namelist() if not n.endswith("/")][:20]
                for n in names: await update.message.reply_document(InputFile(io.BytesIO(z.read(n)),os.path.basename(n) or "file"))
            ctx.user_data.clear()
        elif action=="rename":
            await update.message.reply_document(InputFile(io.BytesIO(data),ctx.user_data["new_name"])); ctx.user_data.clear()
        elif action=="fileinfo":
            await update.message.reply_text(f"📁 نام: {name}\n📦 حجم: {len(data)/1024:.1f} KB"); ctx.user_data.clear()
        elif action=="makezip":
            ctx.user_data.setdefault("files",[]).append((name,data)); await update.message.reply_text("✅ اضافه شد. فایل بعدی یا /done.")
    except Exception as e: print("FILE ERROR:",e); await update.message.reply_text("❌ پردازش فایل انجام نشد.")

async def done(update,ctx):
    action=ctx.user_data.get("action")
    if action=="makezip":
        files=ctx.user_data.get("files",[])
        if not files: await update.message.reply_text("❌ فایلی اضافه نشده."); return
        out=io.BytesIO()
        with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
            for n,d in files:z.writestr(n,d)
        out.seek(0); await update.message.reply_document(InputFile(out,"files.zip")); ctx.user_data.clear()
    elif action=="collage":
        files=ctx.user_data.get("photos",[])
        if len(files)<2: await update.message.reply_text("❌ حداقل ۲ عکس لازم است."); return
        ims=[Image.open(io.BytesIO(x)).convert("RGB") for x in files]
        w=max(x.width for x in ims); h=sum(x.height for x in ims); canvas=Image.new("RGB",(w,h),"white"); y=0
        for im in ims: canvas.paste(im,(0,y)); y+=im.height
        out=io.BytesIO(); canvas.save(out,"JPEG",quality=90); out.seek(0); await update.message.reply_photo(InputFile(out,"collage.jpg")); ctx.user_data.clear()

# ---------- ERROR / RUN ----------
async def error_handler(update,ctx):
    print("ERROR:",ctx.error)

def main():
    init_db()
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("done",done))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.PHOTO,photo_handler))
    app.add_handler(MessageHandler(filters.Document.ALL,document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))
    app.add_error_handler(error_handler)
    print("🤖 TOOLBOX PROFESSIONAL IS RUNNING...")
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
