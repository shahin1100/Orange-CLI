#!/usr/bin/env python3
"""
ORANGE CARRIER CLI MONITOR BOT - STABLE VERSION
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


# ====================================================================
# CONFIG
# ====================================================================

BOT_TOKEN = os.environ.get('BOT_TOKEN', '8797301264:AAGiRBRNGan5kHleOh319qTz4IOjtaJrIQk')
ADMIN_ID = os.environ.get('ADMIN_ID', '7064572216')
ORANGE_EMAIL = os.environ.get('ORANGE_EMAIL', 'n.nazim1132@gmail.com')
ORANGE_PASSWORD = os.environ.get('ORANGE_PASSWORD', 'Abcd1234')

CLI_LIST = ['5731','5730','5732','1315','1646','4983','3375','4473','9989',
            '3598','9891','2917','3706','9890','3737','9893','4857','9639',
            '9899','8617','8615','8613','8618','8619','7863','2348','4822',
            '4845','3462','1425','9981','3247','5715','4915','9725','2332',
            '7708','5591','3933','2011','9178']
UNIQUE_CLI = sorted(set(CLI_LIST))

UPDATE_INTERVAL = 60

logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Global vars
playwright = None
browser = None
page = None
app = None
range_data = {}
range_clis = {}
last_update = None
next_update = None
is_busy = False


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def get_country(name):
    m = re.match(r'^([A-Z][A-Z\s]+?)\s+(?:MOBILE|FIXED|IPRN)', name, re.I)
    return m.group(1).strip() if m else name.split()[0] if name else "Unknown"


def time_str(dt):
    if not dt:
        return "unknown"
    s = (datetime.now() - dt).total_seconds()
    if s < 60:
        return f"{int(s)}s ago"
    elif s < 3600:
        return f"{int(s//60)}m ago"
    return f"{int(s//3600)}h ago"


def extract_range(txt):
    for p in [r'([A-Z][A-Z\s]+MOBILE\s+\d+)', r'([A-Z][A-Z\s]+FIXED\s+\d+)']:
        m = re.search(p, txt, re.I)
        if m:
            return m.group(1).strip()
    return None


def parse_seconds(txt):
    if not txt:
        return None
    t = txt.lower()
    if 'just now' in t:
        return 0
    m = re.search(r'(\d+)\s*(?:sec|seconds?)', t)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+)\s*(?:min|minutes?)', t)
    if m:
        return int(m.group(1)) * 60
    return None


async def close_popups():
    try:
        for btn in await page.query_selector_all('button'):
            if await btn.is_visible() and await btn.inner_text() in ['next', 'done', 'ok', 'close']:
                await btn.click()
                await asyncio.sleep(0.3)
        await page.keyboard.press('Escape')
    except:
        pass


async def login():
    log("Logging in...")
    for _ in range(3):
        try:
            await page.goto('https://www.orangecarrier.com/login', timeout=60000)
            await asyncio.sleep(2)
            await close_popups()
            
            email = await page.query_selector('input[type="email"]')
            if email:
                await email.click()
                await email.fill(ORANGE_EMAIL)
            
            pwd = await page.query_selector('input[type="password"]')
            if pwd:
                await pwd.click()
                await pwd.fill(ORANGE_PASSWORD)
            
            await page.keyboard.press('Enter')
            await asyncio.sleep(5)
            await close_popups()
            
            await page.goto('https://www.orangecarrier.com/services/cli/access', timeout=60000)
            await asyncio.sleep(3)
            
            log("✅ Login OK")
            return True
        except Exception as e:
            log(f"Login error: {e}")
            await asyncio.sleep(5)
    return False


async def search_cli(cli):
    try:
        box = await page.query_selector('input[type="search"]')
        if not box:
            return []
        await box.click()
        await box.fill(cli)
        await page.keyboard.press('Enter')
        await asyncio.sleep(2)
        
        text = await page.inner_text('body')
        results = []
        for i, line in enumerate(text.split('\n')):
            sec = parse_seconds(line)
            if sec is not None:
                rng = extract_range(text.split('\n')[i-1] if i > 0 else line)
                if rng:
                    results.append((rng, sec))
        return results
    except:
        return []


async def collect():
    global range_data, range_clis, last_update, next_update, is_busy
    
    if is_busy:
        return
    is_busy = True
    
    log(f"📊 Collecting {len(UNIQUE_CLI)} CLIs...")
    start = datetime.now()
    
    try:
        await page.reload()
        await asyncio.sleep(2)
        await close_popups()
        
        now = datetime.now()
        
        for cli in UNIQUE_CLI:
            hits = await search_cli(cli)
            for rng, sec in hits:
                hit_time = now - timedelta(seconds=sec)
                if rng not in range_data:
                    range_data[rng] = []
                    range_clis[rng] = {}
                range_data[rng].append(hit_time)
                range_clis[rng][cli] = range_clis[rng].get(cli, 0) + 1
            await asyncio.sleep(0.2)
        
        cutoff = now - timedelta(seconds=7200)
        for rng in list(range_data.keys()):
            range_data[rng] = [ts for ts in range_data[rng] if ts > cutoff]
            if not range_data[rng]:
                del range_data[rng]
                if rng in range_clis:
                    del range_clis[rng]
        
        last_update = now
        next_update = now + timedelta(seconds=UPDATE_INTERVAL)
        
        log(f"✅ Done: {len(range_data)} ranges")
        
    except Exception as e:
        log(f"Error: {e}")
    finally:
        is_busy = False


def get_countdown():
    if not next_update:
        return "waiting..."
    rem = (next_update - datetime.now()).seconds
    return f"{rem//60}m {rem%60}s" if rem >= 60 else f"{rem}s"


def get_report(window):
    if not last_update:
        return "⏳ First update in progress..."
    
    seconds = {'2min':120, '5min':300, '10min':600, '2hours':7200}[window]
    now = datetime.now()
    win_name = {'2min':'2 Minutes','5min':'5 Minutes','10min':'10 Minutes','2hours':'2 Hours'}[window]
    
    ranges = []
    total = 0
    
    for name, ts_list in range_data.items():
        cnt = sum(1 for ts in ts_list if ts > now - timedelta(seconds=seconds))
        if cnt > 0:
            last = max(ts for ts in ts_list if ts > now - timedelta(seconds=seconds))
            cli_cnt = len(range_clis.get(name, {}))
            ranges.append((name, cnt, last, cli_cnt))
            total += cnt
    
    ranges.sort(key=lambda x: x[1], reverse=True)
    top = ranges[:20]
    
    if not top:
        return "📭 No active ranges"
    
    # Country summary
    country = defaultdict(lambda: {'hits':0, 'ranges':set()})
    for name, cnt, _, _ in top:
        c = get_country(name)
        country[c]['hits'] += cnt
        country[c]['ranges'].add(name)
    
    country_list = [(c, d['hits'], len(d['ranges'])) for c, d in country.items()]
    country_list.sort(key=lambda x: x[1], reverse=True)
    
    report = f"🔥 {win_name} REPORT 🔥\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"🕐 {last_update.strftime('%H:%M:%S')}\n"
    report += f"⏱️ Last {win_name}\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if country_list:
        report += f"📊 COUNTRY SUMMARY 📊\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for i, (c, h, rc) in enumerate(country_list[:10], 1):
            report += f"{i}. {c} | {h} hits | {rc} ranges\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    report += f"🔥 TOP 20 RANGES 🔥\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, (name, cnt, last, cli_cnt) in enumerate(top, 1):
        report += f"{i}. `{name}`\n"
        report += f"   📊 {cnt} hits | {cli_cnt} CLI | ⏱️ {time_str(last)}\n"
        report += f"   ────────────────────\n"
    
    report += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"📈 Total Hits: {total}\n"
    report += f"🔄 Next: {get_countdown()}\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"💡 Tap any range name to copy"
    
    return report


async def single_search(query, sec, win):
    if not last_update:
        return "⏳ Data collection in progress..."
    
    q = query.lower()
    now = datetime.now()
    results = []
    
    for name, ts_list in range_data.items():
        if q in name.lower():
            cnt = sum(1 for ts in ts_list if ts > now - timedelta(seconds=sec))
            if cnt > 0:
                last = max(ts for ts in ts_list if ts > now - timedelta(seconds=sec))
                cli_cnt = len(range_clis.get(name, {}))
                results.append((name, cnt, last, cli_cnt))
    
    results.sort(key=lambda x: x[1], reverse=True)
    top = results[:20]
    
    if not top:
        return f"🔍 {query}\n━━━━━━━━━━━━━━━━━━━━\n📭 No results"
    
    country = defaultdict(lambda: {'hits':0, 'ranges':set()})
    for name, cnt, _, _ in top:
        c = get_country(name)
        country[c]['hits'] += cnt
        country[c]['ranges'].add(name)
    
    country_list = [(c, d['hits'], len(d['ranges'])) for c, d in country.items()]
    country_list.sort(key=lambda x: x[1], reverse=True)
    
    report = f"🔍 {query} — {win} RESULTS 🔍\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"⏱️ Window: {win}\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if country_list:
        report += f"📊 COUNTRY SUMMARY 📊\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for i, (c, h, rc) in enumerate(country_list[:10], 1):
            report += f"{i}. {c} | {h} hits | {rc} ranges\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    report += f"🔥 TOP 20 RANGES 🔥\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, (name, cnt, last, cli_cnt) in enumerate(top, 1):
        report += f"{i}. `{name}`\n"
        report += f"   📊 {cnt} hits | {cli_cnt} CLI | ⏱️ {time_str(last)}\n"
        report += f"   ────────────────────\n"
    
    report += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"📈 Total Hits: {sum(c for _,c,_,_ in top)}\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"💡 Tap any range name to copy"
    
    return report


def get_stats():
    now = datetime.now()
    a2 = sum(1 for ts in range_data.values() if sum(1 for t in ts if t > now - timedelta(seconds=120)) > 0)
    a5 = sum(1 for ts in range_data.values() if sum(1 for t in ts if t > now - timedelta(seconds=300)) > 0)
    a10 = sum(1 for ts in range_data.values() if sum(1 for t in ts if t > now - timedelta(seconds=600)) > 0)
    
    return (f"📊 STATISTICS\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 CLIs: {len(UNIQUE_CLI)}\n📍 Ranges: {len(range_data)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Active:\n• 2m: {a2}\n• 5m: {a5}\n• 10m: {a10}\n• 2h: {len(range_data)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 Last: {last_update.strftime('%H:%M:%S') if last_update else 'Never'}\n"
            f"🔄 Next: {get_countdown()}\n━━━━━━━━━━━━━━━━━━━━━━━━━━")


def get_cli_text():
    chunks = [UNIQUE_CLI[i:i+20] for i in range(0, len(UNIQUE_CLI), 20)]
    msg = f"📋 CLI LIST\n━━━━━━━━━━━━━━━━━━━━\n📊 Total: {len(UNIQUE_CLI)}\n\n"
    for i, ch in enumerate(chunks, 1):
        msg += f"{i}. {', '.join(ch)}\n"
    return msg


def main_menu():
    return ReplyKeyboardMarkup([
        ["🟢 ACTIVE RANGE (2 MIN)"],
        ["📊 5 MIN REPORT", "📊 10 MIN REPORT"],
        ["📊 2 HOURS RESULT", "🔍 SINGLE SEARCH"],
        ["📈 STATISTICS", "🆘 HELP"],
        ["👑 ADMIN PANEL"]
    ], resize_keyboard=True)


def search_menu(q):
    return ReplyKeyboardMarkup([
        [f"📊 5 MIN RESULT - {q}"],
        [f"📊 2 HOURS RESULT - {q}"],
        ["🔙 BACK TO MAIN"]
    ], resize_keyboard=True)


def admin_menu():
    return ReplyKeyboardMarkup([
        ["➕ ADD CLI", "➖ REMOVE CLI"],
        ["📋 VIEW ALL CLIS", "🔄 FORCE UPDATE"],
        ["🔙 BACK TO MAIN"]
    ], resize_keyboard=True)


def is_admin(uid):
    return uid == ADMIN_ID


async def auto_collect():
    await collect()
    while True:
        await asyncio.sleep(UPDATE_INTERVAL)
        try:
            await collect()
        except Exception as e:
            log(f"Auto error: {e}")


async def start(update, context):
    await update.message.reply_text("🎉 WELCOME TO ORANGE CLI BOT!\n\n👇 Use the buttons below!", reply_markup=main_menu())


async def handle(update, context):
    global UNIQUE_CLI
    
    text = update.message.text
    uid = str(update.effective_user.id)
    
    if context.user_data.get('awaiting_search'):
        context.user_data['awaiting_search'] = False
        q = text.strip()
        context.user_data['last_query'] = q
        await update.message.reply_text(f"✅ Searching: {q}\n\nSelect result:", reply_markup=search_menu(q))
        return
    
    if context.user_data.get('awaiting_add'):
        context.user_data['awaiting_add'] = False
        if is_admin(uid) and text not in UNIQUE_CLI:
            UNIQUE_CLI.append(text)
            UNIQUE_CLI.sort()
            await update.message.reply_text(f"✅ {text} added! Total: {len(UNIQUE_CLI)}", reply_markup=admin_menu())
        return
    
    if context.user_data.get('awaiting_remove'):
        context.user_data['awaiting_remove'] = False
        if is_admin(uid) and text in UNIQUE_CLI:
            UNIQUE_CLI.remove(text)
            await update.message.reply_text(f"✅ {text} removed! Total: {len(UNIQUE_CLI)}", reply_markup=admin_menu())
        return
    
    if text == "🟢 ACTIVE RANGE (2 MIN)":
        await update.message.reply_text(get_report('2min'), parse_mode='Markdown', reply_markup=main_menu())
    elif text == "📊 5 MIN REPORT":
        await update.message.reply_text(get_report('5min'), parse_mode='Markdown', reply_markup=main_menu())
    elif text == "📊 10 MIN REPORT":
        await update.message.reply_text(get_report('10min'), parse_mode='Markdown', reply_markup=main_menu())
    elif text == "📊 2 HOURS RESULT":
        await update.message.reply_text(get_report('2hours'), parse_mode='Markdown', reply_markup=main_menu())
    elif text == "🔍 SINGLE SEARCH":
        context.user_data['awaiting_search'] = True
        await update.message.reply_text("📝 Send CLI number or Country name\n\nExample: 5731 or CAMBODIA", reply_markup=main_menu())
    elif text == "📈 STATISTICS":
        await update.message.reply_text(get_stats(), reply_markup=main_menu())
    elif text == "🆘 HELP":
        await update.message.reply_text("🆘 HELP\n\n🟢 ACTIVE RANGE (2 MIN) - Last 2m\n📊 5 MIN REPORT - Last 5m\n📊 10 MIN REPORT - Last 10m\n📊 2 HOURS RESULT - Last 2h\n🔍 SINGLE SEARCH - Search CLI/Country\n\n🤖 Status: 🟢 Online", reply_markup=main_menu())
    elif text == "👑 ADMIN PANEL":
        await update.message.reply_text("👑 ADMIN PANEL", reply_markup=admin_menu() if is_admin(uid) else main_menu())
    elif text == "🔙 BACK TO MAIN":
        await update.message.reply_text("Main Menu:", reply_markup=main_menu())
    elif text.startswith("📊 5 MIN RESULT - "):
        q = text.replace("📊 5 MIN RESULT - ", "").strip()
        await update.message.reply_text(await single_search(q, 300, "LAST 5 MINUTES"), parse_mode='Markdown', reply_markup=search_menu(q))
    elif text.startswith("📊 2 HOURS RESULT - "):
        q = text.replace("📊 2 HOURS RESULT - ", "").strip()
        await update.message.reply_text(await single_search(q, 7200, "LAST 2 HOURS"), parse_mode='Markdown', reply_markup=search_menu(q))
    elif text == "🔄 FORCE UPDATE" and is_admin(uid):
        await update.message.reply_text("🔄 Updating...")
        await collect()
        await update.message.reply_text("✅ Done!", reply_markup=admin_menu())
    elif text == "➕ ADD CLI" and is_admin(uid):
        context.user_data['awaiting_add'] = True
        await update.message.reply_text("Send CLI number to add:", reply_markup=admin_menu())
    elif text == "➖ REMOVE CLI" and is_admin(uid):
        context.user_data['awaiting_remove'] = True
        await update.message.reply_text("Send CLI number to remove:", reply_markup=admin_menu())
    elif text == "📋 VIEW ALL CLIS" and is_admin(uid):
        await update.message.reply_text(get_cli_text(), reply_markup=admin_menu())
    else:
        await update.message.reply_text("Use the buttons below 👇\n\nType /start", reply_markup=main_menu())


async def init_browser():
    global playwright, browser, page
    log("🚀 Starting browser...")
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
    page = await browser.new_page()
    log("✅ Browser ready")
    return True


async def main():
    global app
    print("\n" + "=" * 50)
    print("🔥 ORANGE CLI BOT")
    print("=" * 50)
    print(f"📋 CLIs: {len(UNIQUE_CLI)}")
    print("=" * 50 + "\n")
    
    if not await init_browser():
        return
    if not await login():
        return
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    
    await app.bot.set_my_commands([BotCommand("start", "Show menu")])
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    log("✅ Bot RUNNING!")
    
    asyncio.create_task(auto_collect())
    
    try:
        while True:
            await asyncio.sleep(1)
    except:
        pass
    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        if app:
            await app.stop()


if __name__ == "__main__":
    asyncio.run(main())