#!/usr/bin/env python3
"""
====================================================================================================
     ORANGE CARRIER CLI MONITOR BOT - FULLY WORKING
====================================================================================================
"""

import asyncio
import re
import os
import json
from datetime import datetime, timedelta
from collections import defaultdict
import logging

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright


# ====================================================================================================
# CONFIGURATION
# ====================================================================================================

BOT_TOKEN = os.environ.get('BOT_TOKEN', '8797301264:AAGiRBRNGan5kHleOh319qTz4IOjtaJrIQk')
ADMIN_ID = os.environ.get('ADMIN_ID', '7064572216')

ORANGE_EMAIL = os.environ.get('ORANGE_EMAIL', 'n.nazim1132@gmail.com')
ORANGE_PASSWORD = os.environ.get('ORANGE_PASSWORD', 'Abcd1234')

LOGIN_URL = 'https://www.orangecarrier.com/login'
CLI_ACCESS_URL = 'https://www.orangecarrier.com/services/cli/access'

CLI_LIST = [
    '5731', '5730', '5732', '1315', '1646', '4983', '3375', '4473', '9989',
    '3598', '9891', '2917', '3706', '9890', '3737', '9891', '9893', '4857',
    '9639', '9899', '8617', '8615', '8613', '8618', '8619', '7863', '2348',
    '4822', '4845', '4857', '3462', '1425', '9981', '3247', '9989', '5715',
    '4915', '9725', '2332', '7708', '4473', '5591', '3933', '2011', '9178'
]

UNIQUE_CLI = sorted(set(CLI_LIST))

TIME_WINDOWS = {
    '2min': 120,
    '5min': 300,
    '10min': 600,
    '2hours': 7200
}

UPDATE_INTERVAL = 60

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


# ====================================================================================================
# GLOBAL VARIABLES
# ====================================================================================================

playwright = None
browser = None
page = None
application = None

range_data = {}
range_cli_sources = {}
last_update = None
next_update = None
is_updating = False
total_searches = 0


def log_msg(msg):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"[{t}] {msg}")
    logger.info(msg)


def save_data():
    try:
        data = {name: [ts.isoformat() for ts in timestamps] for name, timestamps in range_data.items()}
        with open("data.json", 'w') as f:
            json.dump(data, f)
    except:
        pass


def load_data():
    global range_data
    try:
        if os.path.exists("data.json"):
            with open("data.json", 'r') as f:
                data = json.load(f)
            for name, timestamps in data.items():
                range_data[name] = [datetime.fromisoformat(ts) for ts in timestamps]
            log_msg(f"Loaded {len(range_data)} ranges")
    except:
        pass


def get_country(name):
    match = re.match(r'^([A-Z][A-Z\s]+?)\s+(?:MOBILE|FIXED|IPRN)', name, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return name.split()[0] if name.split() else "Unknown"


def format_time(dt):
    if not dt:
        return "unknown"
    sec = (datetime.now() - dt).total_seconds()
    if sec < 60:
        return f"{int(sec)}s ago"
    elif sec < 3600:
        return f"{int(sec//60)}m ago"
    return f"{int(sec//3600)}h ago"


def extract_range(txt):
    patterns = [
        r'([A-Z][A-Z\s]+MOBILE\s+\d+)',
        r'([A-Z][A-Z\s]+FIXED\s+\d+)',
        r'([A-Z][A-Z\s]+IPRN\s+\d+)',
    ]
    for p in patterns:
        m = re.search(p, txt, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def parse_seconds(txt):
    if not txt:
        return None
    t = txt.lower().strip()
    if 'just now' in t or t == 'now':
        return 0
    m = re.search(r'(\d+)\s*(?:sec|seconds?)', t)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+)\s*(?:min|minutes?)', t)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r'(\d+)\s*(?:hour|hours?)', t)
    if m:
        return int(m.group(1)) * 3600
    return None


async def close_popups():
    try:
        btns = await page.query_selector_all('button')
        for btn in btns:
            if await btn.is_visible():
                txt = await btn.inner_text()
                if txt.lower() in ['next', 'done', 'ok', 'close', 'continue', 'got it']:
                    await btn.click()
                    await asyncio.sleep(0.3)
        await page.keyboard.press('Escape')
    except:
        pass


async def login():
    log_msg("Logging in...")
    
    for attempt in range(3):
        try:
            await page.goto(LOGIN_URL, wait_until='networkidle', timeout=60000)
            await asyncio.sleep(2)
            await close_popups()
            
            email_input = await page.query_selector('input[type="email"]')
            if not email_input:
                email_input = await page.query_selector('input[name="email"]')
            if email_input:
                await email_input.click(click_count=3)
                await email_input.fill('')
                await email_input.type(ORANGE_EMAIL, delay=30)
            
            await asyncio.sleep(0.5)
            
            pass_input = await page.query_selector('input[type="password"]')
            if pass_input:
                await pass_input.click(click_count=3)
                await pass_input.fill('')
                await pass_input.type(ORANGE_PASSWORD, delay=30)
            
            await asyncio.sleep(0.5)
            
            login_btn = await page.query_selector('button[type="submit"]')
            if login_btn:
                await login_btn.click()
            else:
                await page.keyboard.press('Enter')
            
            await asyncio.sleep(5)
            await close_popups()
            
            await page.goto(CLI_ACCESS_URL, wait_until='networkidle', timeout=60000)
            await asyncio.sleep(3)
            await close_popups()
            
            log_msg("✅ Login successful")
            return True
            
        except Exception as e:
            log_msg(f"Login attempt {attempt+1} failed: {e}")
            await asyncio.sleep(5)
    
    return False


async def search_cli(cli):
    try:
        box = await page.query_selector('input[type="search"]')
        if not box:
            return []
        
        await box.click(click_count=3)
        await box.fill('')
        await asyncio.sleep(0.2)
        await box.type(cli, delay=30)
        await asyncio.sleep(0.5)
        await page.keyboard.press('Enter')
        await asyncio.sleep(2)
        
        text = await page.inner_text('body')
        results = []
        lines = text.split('\n')
        
        for i, line in enumerate(lines):
            sec = parse_seconds(line)
            if sec is not None:
                rng = extract_range(lines[i-1] if i > 0 else line)
                if rng:
                    results.append((rng, sec))
        
        return results
        
    except Exception as e:
        log_msg(f"Search error for {cli}: {e}")
        return []


async def collect_data():
    global range_data, range_cli_sources, last_update, next_update, is_updating, total_searches
    
    if is_updating:
        return
    
    is_updating = True
    log_msg(f"📊 Collecting from {len(UNIQUE_CLI)} CLIs...")
    start = datetime.now()
    
    try:
        await page.reload(wait_until='networkidle', timeout=30000)
        await asyncio.sleep(2)
        await close_popups()
        
        now = datetime.now()
        
        for cli in UNIQUE_CLI:
            hits = await search_cli(cli)
            total_searches += 1
            
            for rng, sec in hits:
                hit_time = now - timedelta(seconds=sec)
                if rng not in range_data:
                    range_data[rng] = []
                    range_cli_sources[rng] = {}
                range_data[rng].append(hit_time)
                range_cli_sources[rng][cli] = range_cli_sources[rng].get(cli, 0) + 1
            
            await asyncio.sleep(0.3)
        
        cutoff = now - timedelta(seconds=7200)
        for rng in list(range_data.keys()):
            range_data[rng] = [ts for ts in range_data[rng] if ts > cutoff]
            if not range_data[rng]:
                del range_data[rng]
                if rng in range_cli_sources:
                    del range_cli_sources[rng]
        
        last_update = now
        next_update = now + timedelta(seconds=UPDATE_INTERVAL)
        
        duration = (datetime.now() - start).total_seconds()
        log_msg(f"✅ Done: {len(range_data)} ranges, {duration:.1f}s")
        save_data()
        
    except Exception as e:
        log_msg(f"Collection error: {e}")
    
    finally:
        is_updating = False


def get_countdown():
    if not next_update:
        return "calculating..."
    rem = (next_update - datetime.now()).seconds
    if rem >= 60:
        return f"{rem//60}m {rem%60}s"
    return f"{rem}s"


def get_report(window):
    if not last_update:
        return "⏳ First update in progress, please wait..."
    
    seconds = TIME_WINDOWS[window]
    now = datetime.now()
    
    ranges = []
    total = 0
    
    for name, timestamps in range_data.items():
        cnt = sum(1 for ts in timestamps if ts > now - timedelta(seconds=seconds))
        if cnt > 0:
            last = max(ts for ts in timestamps if ts > now - timedelta(seconds=seconds))
            cli_cnt = len(range_cli_sources.get(name, {}))
            ranges.append((name, cnt, last, cli_cnt))
            total += cnt
    
    ranges.sort(key=lambda x: x[1], reverse=True)
    top = ranges[:20]
    
    if not top:
        return "📭 No active ranges found"
    
    # Country summary
    country_data = defaultdict(lambda: {'hits': 0, 'ranges': set()})
    for name, cnt, _, _ in top:
        country = get_country(name)
        country_data[country]['hits'] += cnt
        country_data[country]['ranges'].add(name)
    
    country_list = [(c, d['hits'], len(d['ranges'])) for c, d in country_data.items()]
    country_list.sort(key=lambda x: x[1], reverse=True)
    
    win_name = {"2min":"2 Minutes","5min":"5 Minutes","10min":"10 Minutes","2hours":"2 Hours"}[window]
    
    report = f"🔥 {win_name} REPORT 🔥\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"🕐 Time: {last_update.strftime('%H:%M:%S')}\n"
    report += f"⏱️ Window: Last {win_name}\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if country_list:
        report += f"📊 COUNTRY SUMMARY 📊\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for i, (c, h, rc) in enumerate(country_list[:10], 1):
            report += f"{i}. {c} | {h} hits | {rc} ranges\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    report += f"🔥 TOP 20 RANGES 🔥\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, (name, cnt, last, cli_cnt) in enumerate(top[:20], 1):
        report += f"{i}. `{name}`\n"
        report += f"   📊 {cnt} hits | {cli_cnt} CLI | ⏱️ {format_time(last)}\n"
        report += f"   ────────────────────\n"
    
    report += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"📈 Total Hits: {total}\n"
    report += f"🔄 Next update in: {get_countdown()}\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"💡 Tap any range name to copy it"
    
    return report


async def single_search(query, seconds, win_name):
    if not last_update:
        return "⏳ Data collection in progress..."
    
    query_lower = query.lower().strip()
    now = datetime.now()
    results = []
    
    for name, timestamps in range_data.items():
        if query_lower in name.lower():
            cnt = sum(1 for ts in timestamps if ts > now - timedelta(seconds=seconds))
            if cnt > 0:
                last = max(ts for ts in timestamps if ts > now - timedelta(seconds=seconds))
                cli_cnt = len(range_cli_sources.get(name, {}))
                results.append((name, cnt, last, cli_cnt))
    
    results.sort(key=lambda x: x[1], reverse=True)
    top = results[:20]
    
    if not top:
        return f"🔍 SEARCH: {query}\n━━━━━━━━━━━━━━━━━━━━\n📭 No results found"
    
    country_data = defaultdict(lambda: {'hits': 0, 'ranges': set()})
    for name, cnt, _, _ in top:
        country = get_country(name)
        country_data[country]['hits'] += cnt
        country_data[country]['ranges'].add(name)
    
    country_list = [(c, d['hits'], len(d['ranges'])) for c, d in country_data.items()]
    country_list.sort(key=lambda x: x[1], reverse=True)
    
    report = f"🔍 {query} — {win_name} RESULTS 🔍\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"⏱️ Window: {win_name}\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if country_list:
        report += f"📊 COUNTRY SUMMARY 📊\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for i, (c, h, rc) in enumerate(country_list[:10], 1):
            report += f"{i}. {c} | {h} hits | {rc} ranges\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    report += f"🔥 TOP 20 RANGES 🔥\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, (name, cnt, last, cli_cnt) in enumerate(top[:20], 1):
        report += f"{i}. `{name}`\n"
        report += f"   📊 {cnt} hits | {cli_cnt} CLI | ⏱️ {format_time(last)}\n"
        report += f"   ────────────────────\n"
    
    total_hits = sum(c for _, c, _, _ in top)
    report += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"📈 Total Hits: {total_hits}\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"💡 Tap any range name to copy it"
    
    return report


def get_stats():
    now = datetime.now()
    active_2 = sum(1 for ts_list in range_data.values() if sum(1 for ts in ts_list if ts > now - timedelta(seconds=120)) > 0)
    active_5 = sum(1 for ts_list in range_data.values() if sum(1 for ts in ts_list if ts > now - timedelta(seconds=300)) > 0)
    active_10 = sum(1 for ts_list in range_data.values() if sum(1 for ts in ts_list if ts > now - timedelta(seconds=600)) > 0)
    
    return (
        f"📊 STATISTICS\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Total CLIs: {len(UNIQUE_CLI)}\n"
        f"📍 Total Ranges: {len(range_data)}\n"
        f"🎯 Total Searches: {total_searches}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Active Ranges:\n"
        f"• 2 Minutes: {active_2}\n"
        f"• 5 Minutes: {active_5}\n"
        f"• 10 Minutes: {active_10}\n"
        f"• 2 Hours: {len(range_data)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Last update: {last_update.strftime('%H:%M:%S') if last_update else 'Never'}\n"
        f"🔄 Next update in: {get_countdown()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


def get_cli_text():
    chunks = [UNIQUE_CLI[i:i+20] for i in range(0, len(UNIQUE_CLI), 20)]
    msg = f"📋 CLI LIST\n━━━━━━━━━━━━━━━━━━━━\n📊 Total: {len(UNIQUE_CLI)} CLIs\n\n"
    for i, ch in enumerate(chunks, 1):
        msg += f"{i}. {', '.join(ch)}\n"
    return msg


# ====================================================================================================
# TELEGRAM MENUS
# ====================================================================================================

def get_main_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🟢 ACTIVE RANGE (2 MIN)")],
        [KeyboardButton("📊 5 MIN REPORT"), KeyboardButton("📊 10 MIN REPORT")],
        [KeyboardButton("📊 2 HOURS RESULT"), KeyboardButton("🔍 SINGLE SEARCH")],
        [KeyboardButton("📈 STATISTICS"), KeyboardButton("🆘 HELP")],
        [KeyboardButton("👑 ADMIN PANEL")]
    ], resize_keyboard=True)


def get_search_menu(q):
    return ReplyKeyboardMarkup([
        [KeyboardButton(f"📊 5 MIN RESULT - {q}")],
        [KeyboardButton(f"📊 2 HOURS RESULT - {q}")],
        [KeyboardButton("🔙 BACK TO MAIN")]
    ], resize_keyboard=True)


def get_admin_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ ADD CLI"), KeyboardButton("➖ REMOVE CLI")],
        [KeyboardButton("📋 VIEW ALL CLIS"), KeyboardButton("🔄 FORCE UPDATE")],
        [KeyboardButton("🔙 BACK TO MAIN")]
    ], resize_keyboard=True)


def is_admin(uid):
    return uid == ADMIN_ID


# ====================================================================================================
# HANDLERS
# ====================================================================================================

async def auto_loop():
    await collect_data()
    while True:
        await asyncio.sleep(UPDATE_INTERVAL)
        try:
            await collect_data()
        except Exception as e:
            log_msg(f"Auto error: {e}")


async def start(update, context):
    await update.message.reply_text(
        "🎉 WELCOME TO ORANGE CLI BOT! 🎉\n\n"
        "🤖 Live CLI Range Monitor Bot\n\n"
        "👇 Use the buttons below!",
        reply_markup=get_main_menu()
    )


async def handle(update, context):
    global UNIQUE_CLI
    
    text = update.message.text
    uid = str(update.effective_user.id)
    
    if context.user_data.get('awaiting_search'):
        context.user_data['awaiting_search'] = False
        q = text.strip()
        context.user_data['last_query'] = q
        await update.message.reply_text(f"✅ Searching for: {q}\n\nSelect result type:", reply_markup=get_search_menu(q))
        return
    
    if context.user_data.get('awaiting_add'):
        context.user_data['awaiting_add'] = False
        if is_admin(uid) and text not in UNIQUE_CLI:
            UNIQUE_CLI.append(text)
            UNIQUE_CLI.sort()
            await update.message.reply_text(f"✅ CLI {text} added!\nTotal: {len(UNIQUE_CLI)}", reply_markup=get_admin_menu())
        return
    
    if context.user_data.get('awaiting_remove'):
        context.user_data['awaiting_remove'] = False
        if is_admin(uid) and text in UNIQUE_CLI:
            UNIQUE_CLI.remove(text)
            UNIQUE_CLI.sort()
            await update.message.reply_text(f"✅ CLI {text} removed!\nTotal: {len(UNIQUE_CLI)}", reply_markup=get_admin_menu())
        return
    
    if text == "🟢 ACTIVE RANGE (2 MIN)":
        await update.message.reply_text(get_report('2min'), parse_mode='Markdown', reply_markup=get_main_menu())
    elif text == "📊 5 MIN REPORT":
        await update.message.reply_text(get_report('5min'), parse_mode='Markdown', reply_markup=get_main_menu())
    elif text == "📊 10 MIN REPORT":
        await update.message.reply_text(get_report('10min'), parse_mode='Markdown', reply_markup=get_main_menu())
    elif text == "📊 2 HOURS RESULT":
        await update.message.reply_text(get_report('2hours'), parse_mode='Markdown', reply_markup=get_main_menu())
    elif text == "🔍 SINGLE SEARCH":
        context.user_data['awaiting_search'] = True
        await update.message.reply_text("📝 Send CLI number OR Country name\n\nExamples:\n• CLI: 5731\n• Country: CAMBODIA", reply_markup=get_main_menu())
    elif text == "📈 STATISTICS":
        await update.message.reply_text(get_stats(), reply_markup=get_main_menu())
    elif text == "🆘 HELP":
        await update.message.reply_text(
            "🆘 HELP\n\n📌 ACTIVE RANGE (2 MIN) - Last 2 minutes\n"
            "📌 5 MIN REPORT - Last 5 minutes\n📌 10 MIN REPORT - Last 10 minutes\n"
            "📌 2 HOURS RESULT - Last 2 hours\n📌 SINGLE SEARCH - Search CLI/Country\n\n"
            "🤖 Status: 🟢 Online", reply_markup=get_main_menu())
    elif text == "👑 ADMIN PANEL":
        await update.message.reply_text("👑 ADMIN PANEL", reply_markup=get_admin_menu() if is_admin(uid) else get_main_menu())
    elif text == "🔙 BACK TO MAIN":
        await update.message.reply_text("Main Menu:", reply_markup=get_main_menu())
    elif text.startswith("📊 5 MIN RESULT - "):
        q = text.replace("📊 5 MIN RESULT - ", "").strip()
        await update.message.reply_text(await single_search(q, 300, "LAST 5 MINUTES"), parse_mode='Markdown', reply_markup=get_search_menu(q))
    elif text.startswith("📊 2 HOURS RESULT - "):
        q = text.replace("📊 2 HOURS RESULT - ", "").strip()
        await update.message.reply_text(await single_search(q, 7200, "LAST 2 HOURS"), parse_mode='Markdown', reply_markup=get_search_menu(q))
    elif text == "🔄 FORCE UPDATE" and is_admin(uid):
        await update.message.reply_text("🔄 Updating...")
        await collect_data()
        await update.message.reply_text("✅ Done!", reply_markup=get_admin_menu())
    elif text == "➕ ADD CLI" and is_admin(uid):
        context.user_data['awaiting_add'] = True
        await update.message.reply_text("Send CLI number to add:", reply_markup=get_admin_menu())
    elif text == "➖ REMOVE CLI" and is_admin(uid):
        context.user_data['awaiting_remove'] = True
        await update.message.reply_text("Send CLI number to remove:", reply_markup=get_admin_menu())
    elif text == "📋 VIEW ALL CLIS" and is_admin(uid):
        await update.message.reply_text(get_cli_text(), reply_markup=get_admin_menu())
    else:
        await update.message.reply_text("Use the buttons below 👇\n\nType /start", reply_markup=get_main_menu())


async def init_browser():
    global playwright, browser, page
    log_msg("🚀 Starting browser...")
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
    page = await browser.new_page()
    log_msg("✅ Browser ready")
    return True


async def main():
    global application
    
    print("\n" + "=" * 50)
    print("🔥 ORANGE CLI BOT")
    print("=" * 50)
    print(f"📋 CLIs: {len(UNIQUE_CLI)}")
    print("=" * 50 + "\n")
    
    load_data()
    
    if not await init_browser():
        return
    
    if not await login():
        return
    
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    
    await application.bot.set_my_commands([BotCommand("start", "Show menu")])
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    log_msg("✅ Bot is RUNNING!")
    
    asyncio.create_task(auto_loop())
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        if application:
            await application.stop()


if __name__ == "__main__":
    asyncio.run(main())