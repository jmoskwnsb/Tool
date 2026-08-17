
import os, io, re, gzip, asyncio, base64, zipfile, sqlite3, secrets, string, random, json, math, urllib.parse
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageOps, ImageDraw, ImageFont

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, filters
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL = os.getenv("FORCE_CHANNEL", "@ByteTunnel")
DB_NAME = "bot.db"
MAX_FILE = 45 * 1024 * 1024

if not TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده")

# ---------------- DATABASE ----------------
def db():
    return sqlite3.connect(DB_NAME)

def init_db():
    c = db()
    cur = c.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        joined_at TEXT, last_seen TEXT, uses INTEGER DEFAULT 0,
        banned INTEGER DEFAULT 0)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS usage(
        user_id INTEGER, tool TEXT, count INTEGER DEFAULT 0,
        PRIMARY KEY(user_id, tool))""")
    c.commit(); c.close()

def save_user(u):
    c=db(); cur=c.cursor(); now=datetime.now(timezone.utc).isoformat()
    cur.execute("""INSERT INTO users(user_id,username,first_name,joined_at,last_seen)
        VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
        username=excluded.username, first_name=excluded.first_name, last_seen=excluded.last_seen""",
        (u.id,u.username or "",u.first_name or "",now,now))
    c.commit(); c.close()

def add_use(user_id, tool):
    c=db(); cur=c.cursor()
    cur.execute("UPDATE users SET uses=uses+1,last_seen=? WHERE user_id=?",
                (datetime.now(timezone.utc).isoformat(),user_id))
    cur.execute("""INSERT INTO usage(user_id,tool,count) VALUES(?,?,1)
        ON CONFLICT(user_id,tool) DO UPDATE SET count=count+1""",(user_id,tool))
    c.commit(); c.close()

def stats():
    c=db(); cur=c.cursor()
    cur.execute("SELECT COUNT(*) FROM users"); total=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE banned=1"); banned=cur.fetchone()[0]
    cur.execute("SELECT tool,SUM(count) FROM usage GROUP BY tool ORDER BY SUM(count) DESC LIMIT 10")
    tools=cur.fetchall()
    c.close(); return total,banned,tools

def all_users():
    c=db(); cur=c.cursor(); cur.execute("SELECT user_id FROM users WHERE banned=0")
    x=[r[0] for r in cur.fetchall()]; c.close(); return x

def set_ban(uid, value):
    c=db(); c.execute("UPDATE users SET banned=? WHERE user_id=?",(value,uid)); c.commit(); c.close()

def is_banned(uid):
    c=db(); cur=c.cursor(); cur.execute("SELECT banned FROM users WHERE user_id=?",(uid,))
    r=cur.fetchone(); c.close(); return bool(r and r[0])

# ---------------- ACCESS ----------------
async def member(bot, uid):
    try:
        m=await bot.get_chat_member(CHANNEL,uid)
        return m.status in ("member","administrator","creator")
    except Exception:
        return False

def join_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton("✅ بررسی عضویت", callback_data="joincheck")]
    ])

async def access(update, ctx):
    uid=update.effective_user.id
    if is_banned(uid):
        if update.callback_query: await update.callback_query.answer("⛔ شما مسدود هستید.", show_alert=True)
        else: await update.message.reply_text("⛔ دسترسی شما مسدود شده است.")
        return False
    if not await member(ctx.bot,uid):
        text="🔐 برای استفاده از بات ابتدا عضو کانال شوید."
        if update.callback_query:
            await update.callback_query.edit_message_text(text,reply_markup=join_kb())
        else: await update.message.reply_text(text,reply_markup=join_kb())
        return False
    return True

# ---------------- KEYBOARDS ----------------
def back(cb="main"): return [[InlineKeyboardButton("🔙 بازگشت",callback_data=cb)]]

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 تصویر",callback_data="photo"),InlineKeyboardButton("📁 فایل",callback_data="file")],
        [InlineKeyboardButton("📝 متن",callback_data="text"),InlineKeyboardButton("🔗 لینک",callback_data="link")],
        [InlineKeyboardButton("🧮 کاربردی",callback_data="util"),InlineKeyboardButton("🎲 سرگرمی",callback_data="fun")],
        [InlineKeyboardButton("🔐 امنیت",callback_data="security"),InlineKeyboardButton("🎨 خلاقانه",callback_data="creative")],
        [InlineKeyboardButton("👑 مدیریت",callback_data="admin")]
    ])

def photo_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 کاهش حجم",callback_data="compress"),InlineKeyboardButton("🔄 تبدیل فرمت",callback_data="convert")],
        [InlineKeyboardButton("📐 تغییر اندازه",callback_data="resize"),InlineKeyboardButton("✂️ برش",callback_data="crop")],
        [InlineKeyboardButton("🔃 چرخش",callback_data="rotate"),InlineKeyboardButton("🖤 سیاه‌وسفید",callback_data="bw")],
        [InlineKeyboardButton("🖼 اطلاعات عکس",callback_data="imginfo"),InlineKeyboardButton("🧹 حذف متادیتا",callback_data="strip")],
        [InlineKeyboardButton("🪪 پروفایل ساده",callback_data="profile"),InlineKeyboardButton("🖼 عکس به PDF",callback_data="topdf")],
        *back()
    ])

def file_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 ساخت ZIP",callback_data="makezip"),InlineKeyboardButton("🗜 GZIP",callback_data="gzip")],
        [InlineKeyboardButton("📂 استخراج ZIP",callback_data="unzip"),InlineKeyboardButton("📝 تغییر نام",callback_data="rename")],
        [InlineKeyboardButton("📊 مشخصات فایل",callback_data="fileinfo")],
        *back()
    ])

def text_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔢 شمارش",callback_data="count"),InlineKeyboardButton("🧹 پاک‌سازی",callback_data="clean")],
        [InlineKeyboardButton("🔄 برعکس",callback_data="reverse"),InlineKeyboardButton("🔗 استخراج لینک",callback_data="links")],
        [InlineKeyboardButton("🔤 تغییر حالت حروف",callback_data="case"),InlineKeyboardButton("📋 حذف خطوط تکراری",callback_data="dedupe")],
        [InlineKeyboardButton("↕️ مرتب‌سازی خطوط",callback_data="sortlines"),InlineKeyboardButton("🔀 متن استایل‌دار",callback_data="styles")],
        *back()
    ])

def link_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 QR",callback_data="qr"),InlineKeyboardButton("🔍 بررسی URL",callback_data="urlcheck")],
        [InlineKeyboardButton("🧹 پاک‌سازی URL",callback_data="cleanurl"),InlineKeyboardButton("🔗 استخراج لینک",callback_data="extracturl")],
        *back()
    ])

def util_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧮 ماشین حساب",callback_data="calc"),InlineKeyboardButton("📏 تبدیل واحد",callback_data="units")],
        [InlineKeyboardButton("📅 تاریخ و زمان",callback_data="date"),InlineKeyboardButton("⏱ تایمر",callback_data="timer")],
        [InlineKeyboardButton("🔑 رمز تصادفی",callback_data="password")],
        *back()
    ])

def fun_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎲 تاس",callback_data="dice"),InlineKeyboardButton("🪙 شیر/خط",callback_data="coin")],
        [InlineKeyboardButton("🔢 عدد تصادفی",callback_data="random"),InlineKeyboardButton("🎯 انتخاب تصادفی",callback_data="choice")],
        [InlineKeyboardButton("🧩 معما",callback_data="riddle"),InlineKeyboardButton("🎮 حدس عدد",callback_data="guess")],
        [InlineKeyboardButton("🧠 سوال عمومی",callback_data="quiz"),InlineKeyboardButton("🏆 امتیاز من",callback_data="score")],
        *back()
    ])

def security_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Base64 Encode",callback_data="encode"),InlineKeyboardButton("🔓 Base64 Decode",callback_data="decode")],
        [InlineKeyboardButton("🔑 رمز تصادفی",callback_data="password")],
        *back()
    ])

def creative_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 متن روی عکس",callback_data="textimage"),InlineKeyboardButton("🧩 کلاژ",callback_data="collage")],
        [InlineKeyboardButton("🔳 عکس به ASCII",callback_data="ascii")],
        *back()
    ])

# ---------------- HELPERS ----------------
async def send_bytes(update, data, name, caption=None):
    await update.message.reply_document(InputFile(io.BytesIO(data), filename=name), caption=caption)

def img_open(data):
    return Image.open(io.BytesIO(data))

async def photo_bytes(update,ctx):
    p=update.message.photo[-1]; f=await ctx.bot.get_file(p.file_id)
    return bytes(await f.download_as_bytearray())

async def doc_bytes(update,ctx):
    d=update.message.document
    if d.file_size and d.file_size>MAX_FILE:
        await update.message.reply_text("❌ فایل بزرگ‌تر از حد مجاز است."); return None
    f=await ctx.bot.get_file(d.file_id); return bytes(await f.download_as_bytearray())

# ---------------- START ----------------
async def start(update,ctx):
    save_user(update.effective_user)
    ctx.user_data.clear()
    if not await access(update,ctx): return
    await update.message.reply_text("🛠 <b>ToolBox همه‌کاره</b>\n\nیک ابزار را انتخاب کن:",reply_markup=main_kb(),parse_mode="HTML")

# ---------------- CALLBACK ----------------
async def cb(update,ctx):
    q=update.callback_query; await q.answer(); d=q.data
    if d=="joincheck":
        if await member(ctx.bot,update.effective_user.id):
            await q.edit_message_text("✅ عضویت تأیید شد.")
            await q.message.reply_text("🛠 منوی اصلی:",reply_markup=main_kb())
        else: await q.answer("❌ هنوز عضو کانال نیستید.",show_alert=True)
        return
    if not await access(update,ctx): return

    pages={"photo":("🖼 ابزار تصویر",photo_kb),"file":("📁 ابزار فایل",file_kb),
           "text":("📝 ابزار متن",text_kb),"link":("🔗 ابزار لینک",link_kb),
           "util":("🧮 ابزار کاربردی",util_kb),"fun":("🎲 سرگرمی",fun_kb),
           "security":("🔐 امنیت",security_kb),"creative":("🎨 خلاقانه",creative_kb)}
    if d=="main":
        ctx.user_data.clear(); await q.edit_message_text("🛠 منوی اصلی:",reply_markup=main_kb()); return
    if d in pages:
        ctx.user_data.clear(); t,k=pages[d]; await q.edit_message_text(t,reply_markup=k()); return

    simple_photo={"compress":"📉 عکس را ارسال کن.","bw":"🖤 عکس را ارسال کن.","topdf":"🖼 عکس را ارسال کن.",
                  "strip":"🧹 عکس را ارسال کن.","profile":"🪪 عکس را ارسال کن.","imginfo":"🖼 عکس را ارسال کن.",
                  "ascii":"🔳 عکس را ارسال کن.","textimage":"📝 ابتدا متن را بفرست.","collage":"🧩 دو یا چند عکس را یکی‌یکی بفرست."}
    if d in simple_photo:
        ctx.user_data["action"]=d; ctx.user_data["photos"]=[]; await q.edit_message_text(simple_photo[d]); return

    if d=="convert":
        await q.edit_message_text("فرمت را انتخاب کن:",reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("JPG",callback_data="jpg"),InlineKeyboardButton("PNG",callback_data="png"),InlineKeyboardButton("WEBP",callback_data="webp")],*back("photo")]))
        return
    if d in ("jpg","png","webp"):
        ctx.user_data.update(action="convert",format={"jpg":"JPEG","png":"PNG","webp":"WEBP"}[d]); await q.edit_message_text("📸 عکس را ارسال کن."); return
    if d in ("resize","crop","rotate"):
        ctx.user_data["action"]=d
        prompts={"resize":"📐 عرض و ارتفاع را مثل `800 600` بفرست.","crop":"✂️ چهار عدد `left top right bottom` بفرست.","rotate":"🔃 زاویه را مثل `90` بفرست."}
        await q.edit_message_text(prompts[d],parse_mode="Markdown"); return

    file_actions={"gzip":"gzip","unzip":"unzip","rename":"rename","fileinfo":"fileinfo","makezip":"makezip"}
    if d in file_actions:
        ctx.user_data["action"]=file_actions[d]; ctx.user_data["files"]=[]
        await q.edit_message_text("📁 فایل را ارسال کن." if d!="makezip" else "📦 فایل‌ها را یکی‌یکی ارسال کن؛ در پایان /done را بزن."); return

    text_actions={"count","clean","reverse","links","case","dedupe","sortlines","styles","qr","urlcheck","cleanurl","extracturl","encode","decode","calc","units","date","timer","choice","textimage"}
    if d in text_actions:
        ctx.user_data["action"]=d
        prompts={"qr":"📱 متن یا لینک را بفرست.","calc":"🧮 عبارت ریاضی ساده را بفرست؛ مثال: `12*(5+2)`.",
                 "units":"📏 تبدیل را مثل `10 km miles` بفرست.","date":"📅 `now` یا تاریخ میلادی مثل `2026-08-17` بفرست.",
                 "timer":"⏱ زمان را به ثانیه بفرست.","choice":"🎯 گزینه‌ها را هرکدام در یک خط بفرست.",
                 "textimage":"📝 متنی که باید روی عکس نوشته شود را بفرست."}
        await q.edit_message_text(prompts.get(d,"📝 متن را ارسال کن."),parse_mode="Markdown"); return

    if d=="password":
        alphabet=string.ascii_letters+string.digits+"!@#$%^&*"
        p="".join(secrets.choice(alphabet) for _ in range(18)); add_use(update.effective_user.id,"password")
        await q.edit_message_text(f"🔑 <code>{p}</code>",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔑 جدید",callback_data="password")],*back("util")]))
        return

    if d in ("dice","coin","random"):
        add_use(update.effective_user.id,d)
        result = random.randint(1,6) if d=="dice" else random.choice(["🟡 شیر","⚪ خط"]) if d=="coin" else random.randint(1,100)
        await q.edit_message_text(f"🎲 نتیجه: <b>{result}</b>",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 دوباره",callback_data=d)],*back("fun")]))
        return

    if d=="riddle":
        riddles=[("چه چیزی هرچه بیشتر از آن برداری بزرگ‌تر می‌شود؟","چاله"),("پا دارد ولی راه نمی‌رود؟","میز"),("کلید دارد ولی قفل ندارد؟","پیانو")]
        a,b=random.choice(riddles); await q.edit_message_text(f"🧩 {a}\n\n💡 <tg-spoiler>{b}</tg-spoiler>",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧩 جدید",callback_data="riddle")],*back("fun")]))
        return

    if d in ("guess","quiz"):
        ctx.user_data["action"]=d
        if d=="guess":
            ctx.user_data["target"]=random.randint(1,20); await q.edit_message_text("🎮 یک عدد بین 1 تا 20 حدس بزن.")
        else:
            qs=[("پایتخت فرانسه؟","پاریس"),("بزرگ‌ترین سیاره منظومه شمسی؟","مشتری"),("آب در چند درجه سانتی‌گراد می‌جوشد؟","100")]
            qq,aa=random.choice(qs); ctx.user_data["answer"]=aa.lower(); await q.edit_message_text(f"🧠 {qq}")
        return

    if d=="score":
        await q.edit_message_text("🏆 امتیازدهی پیشرفته در نسخه بعدی قابل توسعه است.",reply_markup=InlineKeyboardMarkup(back("fun"))); return

    if d=="admin":
        if update.effective_user.id!=ADMIN_ID: await q.answer("⛔ دسترسی ندارید.",show_alert=True); return
        await q.edit_message_text("👑 پنل مدیریت",reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 آمار",callback_data="stats")],[InlineKeyboardButton("📢 ارسال همگانی",callback_data="broadcast")],
            [InlineKeyboardButton("🚫 مسدود کردن",callback_data="ban")],[InlineKeyboardButton("✅ رفع مسدودی",callback_data="unban")],*back()]))
        return
    if d=="stats":
        total,banned,tools=stats(); s="\n".join(f"• {t}: {n}" for t,n in tools) or "هنوز استفاده‌ای ثبت نشده"
        await q.edit_message_text(f"📊 کاربران: {total}\n🚫 مسدود: {banned}\n\n🛠 ابزارهای پرکاربرد:\n{s}",reply_markup=InlineKeyboardMarkup(back("admin"))); return
    if d in ("broadcast","ban","unban"):
        if update.effective_user.id!=ADMIN_ID: return
        ctx.user_data["action"]=d
        await q.edit_message_text("📢 متن پیام را بفرست." if d=="broadcast" else "🆔 آیدی عددی کاربر را بفرست.")
        return

# ---------------- TEXT HANDLER ----------------
async def text_handler(update,ctx):
    if not await access(update,ctx): return
    action=ctx.user_data.get("action")
    if not action: return
    text=update.message.text.strip()

    if action=="resize":
        try:
            a=list(map(int,text.split())); assert len(a)==2 and all(0<x<=5000 for x in a)
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
        except: await update.message.reply_text("❌ یک عدد بفرست.")
        return
    if action=="rename":
        ctx.user_data["new_name"]=text; await update.message.reply_text("📁 حالا فایل را بفرست."); return
    if action=="makezip":
        await update.message.reply_text("برای ساخت ZIP فایل‌ها را ارسال کن و در پایان /done بزن."); return

    if action=="guess":
        try:
            n=int(text); target=ctx.user_data["target"]
            if n==target: await update.message.reply_text("🎉 درست حدس زدی!"); ctx.user_data.clear()
            elif n<target: await update.message.reply_text("⬆️ بزرگ‌تر")
            else: await update.message.reply_text("⬇️ کوچک‌تر")
        except: await update.message.reply_text("❌ عدد وارد کن.")
        return
    if action=="quiz":
        if text.lower()==ctx.user_data.get("answer"):
            await update.message.reply_text("🎉 درست!")
        else: await update.message.reply_text("❌ جواب اشتباه بود.")
        ctx.user_data.clear(); return

    if action=="broadcast" and update.effective_user.id==ADMIN_ID:
        ok=0
        for uid in all_users():
            try: await ctx.bot.send_message(uid,text); ok+=1
            except: pass
        await update.message.reply_text(f"📢 ارسال شد: {ok}")
        ctx.user_data.clear(); return
    if action in ("ban","unban") and update.effective_user.id==ADMIN_ID:
        try: set_ban(int(text),1 if action=="ban" else 0); await update.message.reply_text("✅ انجام شد.")
        except: await update.message.reply_text("❌ آیدی نامعتبر.")
        ctx.user_data.clear(); return

    add_use(update.effective_user.id,action)
    if action=="count":
        await update.message.reply_text(f"🔢 کاراکتر: {len(text)}\n📝 کلمه: {len(text.split())}\n📄 خط: {len(text.splitlines())}")
    elif action=="clean": await update.message.reply_text(re.sub(r"\s+"," ",text).strip())
    elif action=="reverse": await update.message.reply_text(text[::-1])
    elif action in ("links","extracturl"):
        x=re.findall(r'https?://[^\s]+',text); await update.message.reply_text("\n".join(x) if x else "❌ لینکی پیدا نشد.")
    elif action=="case": await update.message.reply_text(f"🔠 {text.upper()}\n\n🔡 {text.lower()}")
    elif action=="dedupe":
        lines=list(dict.fromkeys(text.splitlines())); await update.message.reply_text("\n".join(lines))
    elif action=="sortlines": await update.message.reply_text("\n".join(sorted(text.splitlines(),key=str.lower)))
    elif action=="styles": await update.message.reply_text(f"**{text}**\n__{text}__\n`{text}`",parse_mode="Markdown")
    elif action=="qr":
        im=__import__("qrcode").make(text); out=io.BytesIO(); im.save(out,"PNG"); out.seek(0)
        await update.message.reply_photo(InputFile(out,"qr.png")); 
    elif action=="urlcheck":
        v=text if re.match(r"^https?://",text,re.I) else "https://"+text
        p=urllib.parse.urlparse(v); await update.message.reply_text(f"✅ دامنه: {p.netloc}\n🔗 پروتکل: {p.scheme}" if p.netloc else "❌ URL نامعتبر")
    elif action=="cleanurl":
        p=urllib.parse.urlsplit(text if "://" in text else "https://"+text); await update.message.reply_text(urllib.parse.urlunsplit((p.scheme,p.netloc,p.path,"","")))
    elif action=="encode": await update.message.reply_text(base64.b64encode(text.encode()).decode())
    elif action=="decode":
        try: await update.message.reply_text(base64.b64decode(text).decode())
        except: await update.message.reply_text("❌ Base64 نامعتبر.")
    elif action=="calc":
        if not re.fullmatch(r"[0-9+\-*/(). %]+",text): await update.message.reply_text("❌ فقط محاسبه ساده مجاز است.")
        else:
            try: await update.message.reply_text(str(eval(text,{"__builtins__":{}},{})))
            except: await update.message.reply_text("❌ عبارت نامعتبر.")
    elif action=="units":
        m=re.fullmatch(r"\s*([0-9.]+)\s*(km|m|cm|mm|kg|g|c|f)\s+(km|m|cm|mm|kg|g|c|f)\s*",text.lower())
        if not m: await update.message.reply_text("مثال: 10 km miles (فعلاً km/m/cm/mm و kg/g و C/F)")
        else:
            v=float(m.group(1)); a,b=m.group(2),m.group(3)
            factors={"km":1000,"m":1,"cm":.01,"mm":.001,"kg":1000,"g":1}
            try: await update.message.reply_text(str(v*factors[a]/factors[b]))
            except:
                if a=="c" and b=="f": await update.message.reply_text(str(v*9/5+32))
                elif a=="f" and b=="c": await update.message.reply_text(str((v-32)*5/9))
                else: await update.message.reply_text("❌ واحدها قابل تبدیل نیستند.")
    elif action=="date":
        if text.lower()=="now": await update.message.reply_text(datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"))
        else:
            try: await update.message.reply_text(datetime.strptime(text,"%Y-%m-%d").strftime("%A, %d %B %Y"))
            except: await update.message.reply_text("❌ فرمت: YYYY-MM-DD")
    elif action=="timer":
        try:
            sec=max(1,min(int(text),3600)); await update.message.reply_text(f"⏱ تایمر {sec} ثانیه‌ای شروع شد.")
            await asyncio.sleep(sec); await update.message.reply_text("⏰ زمان تمام شد!")
        except: await update.message.reply_text("❌ عدد ثانیه را بفرست.")
    elif action=="choice":
        opts=[x.strip() for x in text.splitlines() if x.strip()]; await update.message.reply_text("🎯 "+(random.choice(opts) if opts else "گزینه‌ای نیست."))
    elif action=="textimage":
        ctx.user_data["caption_text"]=text; ctx.user_data["action"]="textimage_photo"; await update.message.reply_text("🖼 حالا عکس را بفرست.")
    ctx.user_data.clear() if action not in ("resize","crop","rotate","rename","guess","quiz","textimage") else None

# ---------------- PHOTO HANDLER ----------------
async def photo_handler(update,ctx):
    if not await access(update,ctx): return
    action=ctx.user_data.get("action")
    if not action: return
    data=await photo_bytes(update,ctx); im=img_open(data)
    add_use(update.effective_user.id,action)
    try:
        if action=="compress":
            if im.mode not in ("RGB","L"): im=im.convert("RGB")
            out=io.BytesIO(); im.save(out,"JPEG",quality=45,optimize=True); out.seek(0); await update.message.reply_document(InputFile(out,"compressed.jpg"))
        elif action=="convert":
            fmt=ctx.user_data["format"]; 
            if fmt=="JPEG" and im.mode not in ("RGB","L"): im=im.convert("RGB")
            out=io.BytesIO(); im.save(out,fmt); out.seek(0); await update.message.reply_document(InputFile(out,"converted."+fmt.lower().replace("jpeg","jpg")))
        elif action=="resize":
            im=im.resize((ctx.user_data["width"],ctx.user_data["height"]),Image.Resampling.LANCZOS); out=io.BytesIO(); im.save(out,"PNG"); out.seek(0); await update.message.reply_document(InputFile(out,"resized.png"))
        elif action=="crop":
            im=im.crop((ctx.user_data["left"],ctx.user_data["top"],ctx.user_data["right"],ctx.user_data["bottom"])); out=io.BytesIO(); im.save(out,"PNG"); out.seek(0); await update.message.reply_document(InputFile(out,"cropped.png"))
        elif action=="rotate":
            im=im.rotate(ctx.user_data["degrees"],expand=True); out=io.BytesIO(); im.save(out,"PNG"); out.seek(0); await update.message.reply_document(InputFile(out,"rotated.png"))
        elif action=="bw":
            im=ImageOps.grayscale(im); out=io.BytesIO(); im.save(out,"PNG"); out.seek(0); await update.message.reply_document(InputFile(out,"bw.png"))
        elif action in ("strip","profile"):
            if action=="profile":
                im=ImageOps.fit(im,(800,800))
            else:
                im=im.convert("RGB")
            out=io.BytesIO(); im.save(out,"JPEG",quality=92); out.seek(0); await update.message.reply_document(InputFile(out,"clean.jpg"))
        elif action=="imginfo":
            await update.message.reply_text(f"📐 {im.width}×{im.height}\n🗂 فرمت: {im.format or 'unknown'}\n🎨 حالت: {im.mode}")
        elif action=="topdf":
            if im.mode!="RGB": im=im.convert("RGB")
            out=io.BytesIO(); im.save(out,"PDF"); out.seek(0); await update.message.reply_document(InputFile(out,"image.pdf"))
        elif action=="ascii":
            im.thumbnail((80,80)); im=ImageOps.grayscale(im); chars=" .:-=+*#%@"; px=im.load(); lines=[]
            for y in range(im.height):
                lines.append("".join(chars[px[x,y]*len(chars)//256] for x in range(im.width)))
            await update.message.reply_text("```text\n"+"\n".join(lines)+"\n```",parse_mode="Markdown")
        elif action=="textimage_photo":
            draw=ImageDraw.Draw(im); draw.rectangle((0,im.height-100,im.width,im.height),fill=(0,0,0)); draw.text((20,im.height-75),ctx.user_data["caption_text"],fill=(255,255,255))
            out=io.BytesIO(); im.save(out,"JPEG",quality=90); out.seek(0); await update.message.reply_photo(InputFile(out,"text-image.jpg"))
        elif action=="collage":
            ctx.user_data.setdefault("photos",[]).append(data)
            if len(ctx.user_data["photos"])<2: await update.message.reply_text("🧩 عکس بعدی را بفرست."); return
            ims=[img_open(x).convert("RGB") for x in ctx.user_data["photos"]]; w=max(x.width for x in ims); h=sum(x.height for x in ims); canvas=Image.new("RGB",(w,h),"white"); y=0
            for x in ims: canvas.paste(x,(0,y)); y+=x.height
            out=io.BytesIO(); canvas.save(out,"JPEG"); out.seek(0); await update.message.reply_photo(InputFile(out,"collage.jpg"))
        ctx.user_data.clear()
    except Exception as e:
        print("PHOTO:",e); await update.message.reply_text("❌ پردازش عکس انجام نشد.")

# ---------------- DOCUMENT HANDLER ----------------
async def document_handler(update,ctx):
    if not await access(update,ctx): return
    action=ctx.user_data.get("action")
    if not action: return
    data=await doc_bytes(update,ctx)
    if data is None:return
    name=update.message.document.file_name or "file"
    add_use(update.effective_user.id,action)
    try:
        if action=="gzip":
            out=io.BytesIO()
            with gzip.GzipFile(fileobj=out,mode="wb") as z:z.write(data)
            out.seek(0); await update.message.reply_document(InputFile(out,name+".gz"))
        elif action=="unzip":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for n in z.namelist()[:20]:
                    if not n.endswith("/"): await update.message.reply_document(InputFile(io.BytesIO(z.read(n)),os.path.basename(n) or "file"))
        elif action=="rename":
            await update.message.reply_document(InputFile(io.BytesIO(data),ctx.user_data["new_name"]))
        elif action=="fileinfo":
            await update.message.reply_text(f"📁 نام: {name}\n📦 حجم: {len(data)/1024:.1f} KB")
        elif action=="makezip":
            ctx.user_data.setdefault("files",[]).append((name,data)); await update.message.reply_text("✅ اضافه شد. فایل بعدی را بفرست یا /done.")
    except Exception as e: print("FILE:",e); await update.message.reply_text("❌ پردازش فایل انجام نشد.")

async def done(update,ctx):
    if ctx.user_data.get("action")!="makezip": return
    files=ctx.user_data.get("files",[])
    if not files: await update.message.reply_text("❌ فایلی اضافه نشده."); return
    out=io.BytesIO()
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        for n,d in files:z.writestr(n,d)
    out.seek(0); await update.message.reply_document(InputFile(out,"files.zip")); ctx.user_data.clear()

# ---------------- ERROR/MAIN ----------------
async def err(update,ctx): print("ERROR:",ctx.error)

def main():
    init_db()
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("done",done))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.PHOTO,photo_handler))
    app.add_handler(MessageHandler(filters.Document.ALL,document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))
    app.add_error_handler(err)
    print("🤖 ToolBox is running...")
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
