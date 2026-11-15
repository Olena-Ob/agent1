"""Кістяк AI-агента в Telegram: меню, про мене, продукти, кабінет, питання власниці, розсилки, фото, подарунок,
підписка на канал (за бажанням), звіт власниці о 9:00, «Агента оновлено».
Запуск: python bot.py. Тексти в texts.py, налаштування в config.py, база в db.py.
Додаток продажів (launch.py + wfp.py + texts_launch.py) підключається сам, якщо ці файли є в папці."""
import asyncio
import html
import logging
import os
from collections import Counter
from datetime import datetime, timedelta

from telegram import (BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import (Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import config
import db
import texts as T

# Додаток продажів: якщо в папці є launch.py, агент уміє запуски й оплату. Помилки в ньому не ховаємо.
if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch.py")):
    import launch
else:
    launch = None

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("agent")
HTML = ParseMode.HTML
HERE = os.path.dirname(os.path.abspath(__file__))


# ================== клавіатури ==================

def main_button():
    return T.BTN_LAUNCH if launch else T.BTN_MAIN


def menu_buttons():
    b = [main_button(), T.BTN_MAIN, T.BTN_PRODUCTS, T.BTN_ASK, T.BTN_CABINET, T.BTN_CONTACTS, T.BTN_ADMIN]
    return set(b)


def kb_bottom(uid):
    """Нижнє меню: широка головна кнопка зверху, далі по 2 в ряд. Власниці ще «Керування»."""
    rows = [[main_button()], [T.BTN_PRODUCTS, T.BTN_ASK], [T.BTN_CABINET, T.BTN_CONTACTS]]
    if config.is_admin(uid):
        rows.append([T.BTN_ADMIN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def kb(rows):
    """rows: [[("Текст", "callback або https://посилання"), ...], ...]"""
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, url=d) if d.startswith("http") else
                                  InlineKeyboardButton(t, callback_data=d) for t, d in r] for r in rows if r])


def kb_nav(back=None):
    row = [(T.BTN_BACK, back)] if back else []
    return kb([row + [(T.BTN_HOME, "home")]])


def esc(s):
    return html.escape(str(s or ""))


def who(u):
    name = esc(u.full_name if hasattr(u, "full_name") else u.get("name"))
    nick = u.username if hasattr(u, "username") else u.get("username")
    return f"{name} (@{nick})" if nick else name


# ================== стан людини (зберігається в базі, не губиться після оновлення) ==================

def state(uid, value=None):
    if value is None:
        return db.kv(f"st:{uid}") or ""
    return db.kv(f"st:{uid}", value)


# ================== надсилання з фото / відео ==================

def media_path(rel):
    p = os.path.join(HERE, rel or "")
    return p if rel and os.path.isfile(p) else ""


async def send(bot, chat_id, text, markup=None, media=""):
    """Текст, а якщо є файл у images/, то фото чи відео з текстом. Telegram дає під фото до 1024 знаків:
    довший текст іде окремим повідомленням після фото. file_id запам'ятовуємо, щоб не вантажити файл щоразу."""
    path = media_path(media)
    if path:
        fid_key = f"fid:{media}:{int(os.path.getmtime(path))}"
        fid = db.kv(fid_key)
        short = len(text) <= 1000
        is_video = path.lower().endswith((".mp4", ".mov"))
        try:
            src = fid or open(path, "rb")
            k = dict(caption=text if short else None, parse_mode=HTML, reply_markup=markup if short else None)
            m = await (bot.send_video(chat_id, src, **k) if is_video else bot.send_photo(chat_id, src, **k))
            if not fid:
                got = m.video.file_id if is_video and m.video else (m.photo[-1].file_id if m.photo else "")
                if got:
                    db.kv(fid_key, got)
            if short:
                return m
        except Exception as e:
            log.warning("media %s: %s", media, e)
    return await bot.send_message(chat_id, text, parse_mode=HTML, reply_markup=markup, disable_web_page_preview=True)


# ================== власниці ==================

async def notify_admins(bot, text, **k):
    for a in config.ADMIN_IDS:
        try:
            await bot.send_message(a, text, parse_mode=HTML, disable_web_page_preview=True, **k)
        except Exception as e:
            log.warning("admin notify %s: %s", a, e)


# ================== підписка на канал (за бажанням) ==================

async def gate_ok(bot, chat_id, uid, src=None, is_new=False):
    """True, якщо можна далі. Якщо канал не задано, людина вже підписана або агент не може перевірити, пропускаємо."""
    if not T.CHANNEL or config.is_admin(uid) or db.kv(f"gate:{uid}") == "1":
        return True
    try:
        m = await bot.get_chat_member(T.CHANNEL, uid)
        if m.status in ("member", "administrator", "creator", "restricted"):
            db.kv(f"gate:{uid}", "1")
            db.log_ev(uid, "gate_ok")
            return True
    except Exception as e:
        log.warning("gate %s: %s", T.CHANNEL, e)
        day = datetime.now(config.TZ).strftime("%Y-%m-%d")
        if db.kv("gate_warn") != day:  # власниці раз на добу
            db.kv("gate_warn", day)
            await notify_admins(bot, T.ADMIN_GATE_WARN.format(channel=esc(T.CHANNEL)))
        return True
    if src is not None:  # запам'ятати, куди людина йшла, щоб продовжити після підписки
        db.kv(f"gate_src:{uid}", src)
        db.kv(f"gate_new:{uid}", "1" if is_new else "")
    url = "https://t.me/" + T.CHANNEL.lstrip("@")
    await bot.send_message(chat_id, T.GATE, parse_mode=HTML,
                           reply_markup=kb([[(T.BTN_GATE_SUB, url)], [(T.BTN_GATE_CHECK, "gate")]]))
    return False


# ================== старт і меню ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    chat_id = update.effective_chat.id
    src = (context.args[0] if context.args else "")[:40]
    is_new = db.touch_user(u.id, u.full_name, u.username or "", src)
    state(u.id, "")
    db.log_ev(u.id, "start", src)
    if is_new:
        await send(context.bot, chat_id, T.WELCOME.format(name=esc(u.first_name), owner=T.OWNER_ROD),
                   kb_bottom(u.id), T.PHOTOS.get("welcome"))
        total, _, _ = db.stats()
        if not config.is_admin(u.id):
            await notify_admins(context.bot, T.ADMIN_NEW_USER.format(who=who(u), src=esc(src or "напряму"), total=total))
    else:
        await update.message.reply_text(T.WELCOME_BACK.format(name=esc(u.first_name)), parse_mode=HTML,
                                        reply_markup=kb_bottom(u.id))
    if not await gate_ok(context.bot, chat_id, u.id, src, is_new):
        return
    await after_start(context, chat_id, u.id, src, is_new)


async def after_start(context, chat_id, uid, src, is_new):
    """Що робити після старту (і після підписки на канал): подарунок новим і розділ за міткою ?start=..."""
    if (is_new or src == "gift") and T.GIFT_FILE:
        await send_gift(context.bot, chat_id, uid)
    # Мітка ?start=... може одразу відкривати розділ: ?start=p1 → продукт p1, ?start=ask → питання, ?start=about
    if launch and await launch.on_start(context, chat_id, uid, src):
        pass  # продажі: картка запуску, воронка-магніт або тест за міткою
    elif src in T.PRODUCTS:
        await show_product(chat_id, context, src)
    elif src == "ask":
        await ask_start(chat_id, context, uid)
    elif src == "about":
        await show_about(chat_id, context, uid)


async def send_gift(bot, chat_id, uid):
    path = media_path(T.GIFT_FILE)
    if not path:
        return
    fid_key = f"fid:{T.GIFT_FILE}:{int(os.path.getmtime(path))}"
    fid = db.kv(fid_key)
    try:
        m = await bot.send_document(chat_id, fid or open(path, "rb"), caption=T.GIFT_TEXT, parse_mode=HTML)
        if not fid and m.document:
            db.kv(fid_key, m.document.file_id)
        db.log_ev(uid, "gift")
    except Exception as e:
        log.warning("gift: %s", e)


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state(update.effective_user.id, "")
    await update.message.reply_text(T.MENU_HINT, reply_markup=kb_bottom(update.effective_user.id))


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ваш Telegram ID: <code>{update.effective_user.id}</code>", parse_mode=HTML)


# ================== розділи ==================

async def show_about(chat_id, context, uid=0):
    db.log_ev(uid, "about")
    await send(context.bot, chat_id, T.ABOUT, kb_nav(), T.PHOTOS.get("about"))


async def show_products(chat_id, context, edit_msg=None, uid=0):
    db.log_ev(uid, "products")
    rows = [[(name, f"prod:{key}")] for key, (name, _) in T.PRODUCTS.items()]
    if launch:
        rows = launch.product_rows() + rows
    rows.append([(T.BTN_HOME, "home")])
    if edit_msg and edit_msg.text:
        try:
            await edit_msg.edit_text(T.PRODUCTS_TITLE, parse_mode=HTML, reply_markup=kb(rows))
            return
        except Exception:
            pass
    await context.bot.send_message(chat_id, T.PRODUCTS_TITLE, parse_mode=HTML, reply_markup=kb(rows))


async def show_product(chat_id, context, key, edit_msg=None):
    name, text = T.PRODUCTS[key]
    markup = kb([[(T.BTN_WRITE_ME, f"write:{key}")], [(T.BTN_BACK, "products"), (T.BTN_HOME, "home")]])
    media = T.PRODUCT_PHOTOS.get(key)
    if edit_msg and edit_msg.text and not media_path(media):
        try:
            await edit_msg.edit_text(text, parse_mode=HTML, reply_markup=markup, disable_web_page_preview=True)
            return
        except Exception:
            pass
    await send(context.bot, chat_id, text, markup, media)


async def show_cabinet(chat_id, context, uid):
    db.log_ev(uid, "cabinet")
    u = db.user(uid) or {}
    since = datetime.fromtimestamp(u.get("joined") or 0, config.TZ).strftime("%d.%m.%Y") if u.get("joined") else "сьогодні"
    extra, markup = "", None
    if launch:
        extra = launch.cabinet_line(uid)
        m = launch.member(uid)
        markup = (launch.pay_more_kb(m) if m else None) or kb([[(T.BTN_LAUNCH, "lc:card")], [(T.BTN_HOME, "home")]])
    markup = markup or kb([[(T.BTN_PRODUCTS, "products")], [(T.BTN_HOME, "home")]])
    await context.bot.send_message(chat_id, T.CABINET.format(name=esc(u.get("name")), since=since, extra=extra or T.CABINET_EMPTY_EXTRA),
                                   parse_mode=HTML, reply_markup=markup)


async def show_contacts(chat_id, context, uid=0):
    db.log_ev(uid, "contacts")
    rows = [[(T.BTN_WRITE_ME, "write:contacts")], [(T.BTN_HOME, "home")]]
    if launch:  # у агенті з продажами «Про мене» живе тут, бо головна кнопка меню зайнята запуском
        rows[1].insert(0, (T.BTN_MAIN, "about"))
    await send(context.bot, chat_id, T.CONTACTS, kb(rows), T.PHOTOS.get("contacts"))


async def ask_start(chat_id, context, uid, topic=""):
    state(uid, f"ask:{topic}")
    await context.bot.send_message(chat_id, T.ASK, parse_mode=HTML,
                                   reply_markup=kb([[(T.BTN_CANCEL, "ask_cancel")]]))


async def show_admin(chat_id, context, edit_msg=None):
    total, week, src = db.stats()
    sources = "\n".join(f"• {esc(s)}: {n}" for s, n in src) or "• поки нікого"
    text = T.ADMIN_MENU.format(total=total, week=week, sources=sources)
    rows = [[(T.BTN_BROADCAST, "bc"), (T.BTN_REPORT, "report")], [(T.BTN_STATS, "adm")]]
    if launch:
        rows = launch.admin_rows() + rows
    markup = kb(rows)
    if edit_msg:
        try:
            await edit_msg.edit_text(text, parse_mode=HTML, reply_markup=markup)
            return
        except Exception:
            pass
    await context.bot.send_message(chat_id, text, parse_mode=HTML, reply_markup=markup)


# ================== кнопки під повідомленнями ==================

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass
    uid, chat_id, data = update.effective_user.id, q.message.chat_id, q.data or ""
    if data == "gate":
        if T.CHANNEL and not config.is_admin(uid):
            db.kv(f"gate:{uid}", "")
        if await gate_ok(context.bot, chat_id, uid):
            await context.bot.send_message(chat_id, T.GATE_OK, reply_markup=kb_bottom(uid))
            src = db.kv(f"gate_src:{uid}") or ""
            fresh = db.kv(f"gate_new:{uid}") == "1"
            db.kv(f"gate_src:{uid}", "")
            db.kv(f"gate_new:{uid}", "")
            await after_start(context, chat_id, uid, src, fresh)
        else:
            await context.bot.send_message(chat_id, T.GATE_NOT_YET)
        return
    if not config.is_admin(uid) and not await gate_ok(context.bot, chat_id, uid):
        return
    if launch and data.startswith("lc:"):
        await launch.on_callback(update, context, data)
    elif data == "home":
        state(uid, "")
        await context.bot.send_message(chat_id, T.MENU_HINT, reply_markup=kb_bottom(uid))
    elif data == "about":
        await show_about(chat_id, context, uid)
    elif data == "products":
        await show_products(chat_id, context, q.message, uid)
    elif data.startswith("prod:") and data[5:] in T.PRODUCTS:
        db.log_ev(uid, "product", data[5:])
        await show_product(chat_id, context, data[5:], q.message)
    elif data.startswith("write:"):
        await ask_start(chat_id, context, uid, data[6:])
    elif data == "ask_cancel":
        state(uid, "")
        await context.bot.send_message(chat_id, T.ASK_CANCELLED, reply_markup=kb_bottom(uid))
    elif config.is_admin(uid) and data == "adm":
        await show_admin(chat_id, context, q.message)
    elif config.is_admin(uid) and data == "report":
        await context.bot.send_message(chat_id, report_text(), parse_mode=HTML)
    elif config.is_admin(uid) and launch and data == "members":
        await context.bot.send_message(chat_id, launch.members_text(), parse_mode=HTML)
    elif config.is_admin(uid) and launch and data == "wfptest":
        await launch.wfptest(context.bot, chat_id, uid)
    elif config.is_admin(uid) and data == "bc":
        state(uid, "bc_wait")
        await context.bot.send_message(chat_id, T.BC_ASK, reply_markup=kb([[(T.BTN_CANCEL, "bc_no")]]))
    elif config.is_admin(uid) and data == "bc_go":
        await broadcast(context, chat_id, uid)
    elif config.is_admin(uid) and data == "bc_edit":
        state(uid, "bc_wait")
        await context.bot.send_message(chat_id, T.BC_ASK, reply_markup=kb([[(T.BTN_CANCEL, "bc_no")]]))
    elif config.is_admin(uid) and data == "bc_no":
        state(uid, "")
        db.kv(f"bc_src:{uid}", "")
        await context.bot.send_message(chat_id, T.BC_CANCELLED, reply_markup=kb_bottom(uid))


# ================== повідомлення людей ==================


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or update.effective_chat.type != "private":
        return
    u = update.effective_user
    uid, chat_id = u.id, msg.chat_id
    db.touch_user(uid, u.full_name, u.username or "")
    text = msg.text or ""

    # 1) Кнопки нижнього меню завжди першими: людина не «застрягає» в питанні чи розсилці
    if text in menu_buttons():
        state(uid, "")
        if not await gate_ok(context.bot, chat_id, uid):
            return
        if launch and text == T.BTN_LAUNCH:
            await launch.show_card(context.bot, chat_id, uid)
        elif text == T.BTN_MAIN:
            await show_about(chat_id, context, uid)
        elif text == T.BTN_PRODUCTS:
            await show_products(chat_id, context, uid=uid)
        elif text == T.BTN_ASK:
            await ask_start(chat_id, context, uid)
        elif text == T.BTN_CABINET:
            await show_cabinet(chat_id, context, uid)
        elif text == T.BTN_CONTACTS:
            await show_contacts(chat_id, context, uid)
        elif text == T.BTN_ADMIN and config.is_admin(uid):
            await show_admin(chat_id, context)
        return

    # 2) Власниця відповідає на питання: «Відповісти» на повідомлення з питанням
    if config.is_admin(uid) and msg.reply_to_message:
        target = db.kv(f"q:{msg.reply_to_message.message_id}")
        if target:
            try:
                await context.bot.send_message(int(target), T.ANSWER_FROM_OWNER.format(owner=T.OWNER_ROD, text=esc(text or msg.caption)),
                                               parse_mode=HTML)
                if not text:  # голосове, фото, відео: пересилаємо як є
                    await context.bot.copy_message(int(target), chat_id, msg.message_id)
                await msg.reply_text(T.ADMIN_ANSWER_OK)
            except Exception:
                await msg.reply_text(T.ADMIN_ANSWER_FAIL)
            return

    st = state(uid)

    # 3) Власниця готує розсилку: показуємо прев'ю
    if config.is_admin(uid) and st == "bc_wait":
        db.kv(f"bc_src:{uid}", f"{chat_id}:{msg.message_id}")
        state(uid, "bc_ready")
        await context.bot.copy_message(chat_id, chat_id, msg.message_id)
        n = len([i for i in db.active_ids() if not config.is_admin(i)])
        await context.bot.send_message(chat_id, T.BC_PREVIEW.format(n=n),
                                       reply_markup=kb([[(T.BTN_SEND, "bc_go")], [(T.BTN_EDIT, "bc_edit"), (T.BTN_NOT_NOW, "bc_no")]]))
        return

    # 4) Людина ставить питання: передаємо власниці
    if st.startswith("ask"):
        topic = st.split(":", 1)[1] if ":" in st else ""
        state(uid, "")
        db.log_ev(uid, "question", topic)
        head = T.ADMIN_QUESTION.format(who=who(u), uid=uid, text=esc(text or msg.caption or "(голосове чи файл нижче)"))
        if topic:
            title = launch.topic_title(topic) if launch else ""
            title = title or T.PRODUCTS.get(topic, (topic,))[0]
            head = f"Тема: <b>{esc(title)}</b>\n" + head
        for a in config.ADMIN_IDS:
            try:
                m = await context.bot.send_message(a, head, parse_mode=HTML)
                db.kv(f"q:{m.message_id}", uid)
                if not text:
                    m2 = await context.bot.copy_message(a, chat_id, msg.message_id)
                    db.kv(f"q:{m2.message_id}", uid)
            except Exception as e:
                log.warning("question to admin: %s", e)
        await msg.reply_text(T.ASK_SENT, reply_markup=kb_bottom(uid))
        return

    # 5) Кодові слова продажів (воронки, тест) і службові повідомлення власниці, якщо підключено систему продажів
    if launch and await launch.on_message(context, msg, u):
        return

    # 6) Усе інше: м'яко повертаємо в меню
    await msg.reply_text(T.UNKNOWN, reply_markup=kb_bottom(uid))


# ================== розсилка ==================

async def broadcast(context, chat_id, uid):
    src = db.kv(f"bc_src:{uid}") or ""
    if state(uid) != "bc_ready" or ":" not in src:
        await context.bot.send_message(chat_id, T.BC_CANCELLED)
        return
    state(uid, "")
    db.kv(f"bc_src:{uid}", "")  # одразу: повторне натискання нічого не розішле
    from_chat, mid = src.split(":")
    ok = fail = 0
    for target in db.active_ids():
        if config.is_admin(target):
            continue
        try:
            await context.bot.copy_message(target, int(from_chat), int(mid))
            ok += 1
        except Forbidden:
            db.set_blocked(target)
            fail += 1
        except Exception as e:
            log.warning("broadcast %s: %s", target, e)
            fail += 1
        await asyncio.sleep(0.06)
    await context.bot.send_message(chat_id, T.BC_DONE.format(ok=ok, fail=fail), reply_markup=kb_bottom(uid))


# ================== звіт власниці о 9:00 ==================

def report_text(day=None):
    """Звіт за вчорашню добу (за Києвом): хто заходив, нові і звідки, що робили. Плюс рядки продажів, якщо є launch."""
    today = (day or datetime.now(config.TZ)).replace(hour=0, minute=0, second=0, microsecond=0)
    a = int((today - timedelta(days=1)).timestamp())
    b = a + 86400
    with db.conn() as c:
        new = [dict(r) for r in c.execute("SELECT src FROM users WHERE joined>=? AND joined<?", (a, b)).fetchall()]
        evs = [dict(r) for r in c.execute("SELECT user_id, ev, extra FROM events WHERE at>=? AND at<?", (a, b)).fetchall()]
    admins = set(config.ADMIN_IDS)
    evs = [e for e in evs if e["user_id"] not in admins and e["user_id"]]
    src = Counter((r["src"] if r["src"] not in ("", "direct") else "напряму") for r in new)
    acts = {}
    for e in evs:
        name = T.EV_NAMES.get(e["ev"], e["ev"])
        if e["ev"] == "product" and e["extra"] in T.PRODUCTS:
            name = f"{T.EV_NAMES['product']} «{T.PRODUCTS[e['extra']][0]}»"
        acts.setdefault(name, set()).add(e["user_id"])
    top = sorted(acts.items(), key=lambda x: -len(x[1]))[:10]
    text = T.REPORT.format(
        date=(today - timedelta(days=1)).strftime("%d.%m"), active=len({e["user_id"] for e in evs}), new=len(new),
        questions=sum(1 for e in evs if e["ev"] == "question"),
        sources="\n".join(f"• {esc(k)}: {v}" for k, v in src.most_common(8)) or T.REPORT_NONE,
        acts="\n".join(f"• {esc(k)}: {len(v)}" for k, v in top) or T.REPORT_NONE)
    if launch:
        text += "\n\n" + launch.report_lines(a, b)
    return text


async def report_job(context):
    now = datetime.now(config.TZ)
    if now.hour < T.REPORT_HOUR or not config.ADMIN_IDS:
        return
    day = now.strftime("%Y-%m-%d")
    if db.kv("report_day") == day:
        return
    db.kv("report_day", day)  # ДО відправки: після перезапуску другого звіту не буде
    await notify_admins(context.bot, report_text(now))


# ================== помилки, запуск ==================

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("error", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_chat and update.effective_chat.type == "private":
            await context.bot.send_message(update.effective_chat.id, T.ERROR_USER)
    except Exception:
        pass
    await notify_admins(context.bot, T.ADMIN_ERROR.format(err=esc(str(context.error)[:300])))


async def post_init(app: Application):
    db.init()
    if launch:
        launch.setup()
    await app.bot.set_my_commands([BotCommand("start", "Почати"), BotCommand("menu", "Меню")])
    for a in config.ADMIN_IDS:
        try:
            await app.bot.set_my_commands([BotCommand("start", "Почати"), BotCommand("menu", "Меню"),
                                           BotCommand("myid", "Мій Telegram ID")], scope=BotCommandScopeChat(a))
        except Exception as e:
            log.warning("admin commands %s: %s", a, e)
    # «Агента оновлено»: один раз на кожну нову версію з texts.VERSION
    if db.kv("version") != T.VERSION:
        db.kv("version", T.VERSION)
        await notify_admins(app.bot, T.ADMIN_UPDATED.format(version=T.VERSION, notes="\n".join(f"• {n}" for n in T.NOTES)))


def main():
    if not config.BOT_TOKEN:
        raise SystemExit("Немає BOT_TOKEN. Додайте його в Railway → Variables.")
    app = ApplicationBuilder().token(config.BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CallbackQueryHandler(on_callback))
    if launch:
        launch.add_handlers(app)  # /grupa і задачі продажів за розкладом
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_message))
    app.job_queue.run_repeating(report_job, interval=600, first=60)  # звіт о 9:00, один раз на день
    app.add_error_handler(on_error)
    log.info("Агент запускається")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
