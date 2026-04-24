#!/usr/bin/env python3
"""
====================================================================================================
     ORANGE CARRIER CLI MONITOR BOT - NO EXTERNAL DEPENDENCIES
====================================================================================================
"""

import asyncio
import json
import os
import re
import random
from datetime import datetime, timedelta
from collections import defaultdict
import logging
import urllib.request
import urllib.error

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes


# ====================================================================================================
# CONFIGURATION
# ====================================================================================================

BOT_TOKEN = '8797301264:AAGiRBRNGan5kHleOh319qTz4IOjtaJrIQk'
ADMIN_ID = '7064572216'

# CLI List
CLI_LIST = [
    '5731', '5730', '5732', '1315', '1646', '4983', '3375', '4473', '9989',
    '3598', '9891', '2917', '3706', '9890', '3737', '4857', '9639', '9899',
    '8617', '8615', '8613', '8618', '8619', '7863', '2348', '4822', '4845',
    '3462', '1425', '9981', '3247', '5715', '4915', '9725', '2332', '7708',
    '5591', '3933', '2011', '9178'
]
UNIQUE_CLI = sorted(set(CLI_LIST))

TIME_WINDOWS = {
    '2min': 120,
    '5min': 300,
    '10min': 600,
    '2hours': 7200
}

UPDATE_INTERVAL = 60

DATA_FILE = "range_data.json"
CLI_FILE = "cli_list.json"

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

application = None
range_data = {}
reports = {}
last_update = None
next_update = None
is_updating = False


def log_msg(msg):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"[{t}] {msg}")
    logger.info(msg)


def save_data():
    try:
        data = {name: [ts.isoformat() for ts in rd['timestamps']] for name, rd in range_data.items()}
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        log_msg(f"Save error: {e}")


def load_data():
    global range_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
            for name, timestamps in data.items():
                range_data[name] = {'timestamps': [datetime.fromisoformat(ts) for ts in timestamps]}
            log_msg(f"Loaded {len(range_data)} ranges")
    except Exception as e:
        log_msg(f"Load error: {e}")


def save_cli_list():
    try:
        with open(CLI_FILE, 'w') as f:
            json.dump(UNIQUE_CLI, f)
    except Exception as e:
        log_msg(f"CLI save error: {e}")


def load_cli_list():
    global UNIQUE_CLI
    try:
        if os.path.exists(CLI_FILE):
            with open(CLI_FILE, 'r') as f:
                UNIQUE_CLI = json.load(f)
            log_msg(f"Loaded {len(UNIQUE_CLI)} CLIs")
    except Exception as e:
        log_msg(f"CLI load error: {e}")


def get_country_from_range(range_name):
    if not range_name:
        return "Unknown"
    match = re.match(r'^([A-Z][A-Z\s]+?)\s+(?:MOBILE|FIXED|IPRN)', range_name, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return range_name.split()[0] if range_name.split() else "Unknown"


def get_time_ago_str(dt):
    if not dt:
        return "unknown"
    seconds = (datetime.now() - dt).total_seconds()
    if seconds < 60:
        return f"{int(seconds)}s ago"
    elif seconds < 3600:
        return f"{int(seconds//60)}m ago"
    else:
        return f"{int(seconds//3600)}h ago"


def parse_time_string(txt):
    if not txt:
        return None
    t = txt.lower().strip()
    if 'just now' in t or t == 'now':
        return 0
    match = re.search(r'(\d+)\s*(?:sec|min|hour)', t)
    if match:
        num = int(match.group(1))
        if 'min' in t:
            return num * 60
        elif 'hour' in t:
            return num * 3600
        return num
    return None


def extract_range_name(txt):
    patterns = [
        r'([A-Z][A-Z\s]+MOBILE\s+\d+)',
        r'([A-Z][A-Z\s]+FIXED\s+\d+)',
        r'([A-Z][A-Z\s]+IPRN\s+\d+)',
    ]
    for p in patterns:
        match = re.search(p, txt, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def get_country_summary(ranges):
    country_data = defaultdict(lambda: {'hits': 0, 'ranges': set()})
    for name, hits, _ in ranges:
        country = get_country_from_range(name)
        country_data[country]['hits'] += hits
        country_data[country]['ranges'].add(name)
    summary = [(c, d['hits'], len(d['ranges'])) for c, d in country_data.items()]
    summary.sort(key=lambda x: x[1], reverse=True)
    return summary[:15]


# Mock data for now - replace with real API
async def search_cli_api(cli):
    await asyncio.sleep(0.05)
    mock_ranges = [
        "SAUDI ARABIA MOBILE 594", "UNITED ARAB EMIRATES MOBILE 832",
        "NIGERIA MOBILE 8203", "TAIWAN MOBILE 1425", "MACEDONIA FIXED 89",
        "TAJIKISTAN FIXED 5371", "EGYPT MOBILE 6600", "SRI LANKA MOBILE 2280",
        "SOUTH AFRICA MOBILE 6293", "ETHIOPIA MOBILE 4610", "TURKEY MOBILE 437",
        "ZIMBABWE MOBILE 97", "COSTA RICA MOBILE 958", "SERBIA MOBILE 4169"
    ]
    results = []
    for rng in random.sample(mock_ranges, min(random.randint(2, 8), len(mock_ranges))):
        seconds = random.randint(1, 7200)
        results.append((rng, seconds))
    return results


async def collect_all_data():
    global range_data, last_update, next_update, is_updating
    
    if is_updating:
        return
    
    is_updating = True
    log_msg(f"📊 Collecting data from {len(UNIQUE_CLI)} CLIs...")
    start = datetime.now()
    
    try:
        now = datetime.now()
        
        for cli in UNIQUE_CLI:
            hits = await search_cli_api(cli)
            for rng, sec in hits:
                hit_time = now - timedelta(seconds=sec)
                if rng not in range_data:
                    range_data[rng] = {'timestamps': []}
                range_data[rng]['timestamps'].append(hit_time)
            await asyncio.sleep(0.1)
        
        cutoff = now - timedelta(seconds=7200)
        for rng in list(range_data.keys()):
            range_data[rng]['timestamps'] = [ts for ts in range_data[rng]['timestamps'] if ts > cutoff]
            if not range_data[rng]['timestamps']:
                del range_data[rng]
        
        last_update = now
        next_update = now + timedelta(seconds=UPDATE_INTERVAL)
        
        # Update reports
        for name, seconds in TIME_WINDOWS.items():
            top_ranges = []
            total_hits = 0
            for rng, data in range_data.items():
                cnt = sum(1 for ts in data['timestamps'] if ts > now - timedelta(seconds=seconds))
                if cnt > 0:
                    last = max(ts for ts in data['timestamps'] if ts > now - timedelta(seconds=seconds))
                    top_ranges.append((rng, cnt, last))
                    total_hits += cnt
            top_ranges.sort(key=lambda x: x[1], reverse=True)
            reports[name] = {
                'seconds': seconds,
                'ranges': top_ranges[:20],
                'total_hits': total_hits,
                'total_ranges': len(top_ranges),
                'last_update': last_update
            }
        
        duration = (datetime.now() - start).total_seconds()
        log_msg(f"✅ Data collection done: {len(range_data)} ranges, {duration:.1f}s")
        save_data()
        
    except Exception as e:
        log_msg(f"Collection error: {e}")
    
    finally:
        is_updating = False


def get_countdown():
    if not next_update:
        return "calculating..."
    remaining = (next_update - datetime.now()).seconds
    if remaining >= 60:
        return f"{remaining//60}m {remaining%60}s"
    return f"{remaining}s"


def get_report(window_name):
    if window_name not in reports:
        return "⏳ First update in progress, please wait..."
    
    r = reports[window_name]
    cd = get_countdown()
    
    if not r['ranges']:
        return f"📡 No active ranges found in last {TIME_WINDOWS[window_name]//60} minutes"
    
    win_display = {"2min":"2 Minutes","5min":"5 Minutes","10min":"10 Minutes","2hours":"2 Hours"}.get(window_name, window_name)
    country_summary = get_country_summary(r['ranges'])
    
    report = f"🔥 {win_display} REPORT 🔥\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"🕐 Time: {r['last_update'].strftime('%H:%M:%S')}\n"
    report += f"⏱️ Window: Last {win_display}\n"
    report += f"📊 Active Ranges: {r['total_ranges']}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if country_summary:
        report += f"📊 COUNTRY SUMMARY 📊\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for i, (country, hits, rc) in enumerate(country_summary[:10], 1):
            report += f"{i}. {country} | {hits} hits | {rc} ranges\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    report += f"🔥 TOP 20 RANGES 🔥\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, (name, cnt, last) in enumerate(r['ranges'][:20], 1):
        report += f"{i}. `{name}`\n   📊 {cnt} hits | ⏱️ {get_time_ago_str(last)}\n   ────────────────────\n"
    
    report += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n📈 Total Hits: {r['total_hits']}\n🔄 Next update in: {cd}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n💡 Tap any range name to copy it"
    return report


async def single_search(query):
    if not last_update:
        return "⏳ Data collection in progress, please wait..."
    
    query_lower = query.lower().strip()
    now = datetime.now()
    results = []
    
    for name, data in range_data.items():
        if query_lower in name.lower():
            cnt = sum(1 for ts in data['timestamps'] if ts > now - timedelta(seconds=7200))
            if cnt > 0:
                last = max(ts for ts in data['timestamps'] if ts > now - timedelta(seconds=7200))
                results.append((name, cnt, last))
    
    results.sort(key=lambda x: x[1], reverse=True)
    top = results[:20]
    
    if not top:
        return f"🔍 SEARCH: {query}\n━━━━━━━━━━━━━━━━━━━━\n📭 No results found in last 2 hours"
    
    country_summary = get_country_summary(top)
    
    report = f"🔍 {query} — 2 HOURS RESULTS 🔍\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏱️ Window: Last 2 hours\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if country_summary:
        report += f"📊 COUNTRY SUMMARY 📊\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for i, (country, hits, rc) in enumerate(country_summary[:10], 1):
            report += f"{i}. {country} | {hits} hits | {rc} ranges\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    report += f"🔥 TOP 20 RANGES 🔥\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, (name, cnt, last) in enumerate(top, 1):
        report += f"{i}. `{name}`\n   📊 {cnt} hits | ⏱️ {get_time_ago_str(last)}\n   ────────────────────\n"
    
    report += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n📈 Total Hits: {sum(c for _, c, _ in top)}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n💡 Tap any range name to copy it"
    return report


def get_stats():
    cd = get_countdown()
    now = datetime.now()
    active_2min = sum(1 for d in range_data.values() if sum(1 for ts in d['timestamps'] if ts > now - timedelta(seconds=120)) > 0)
    active_5min = sum(1 for d in range_data.values() if sum(1 for ts in d['timestamps'] if ts > now - timedelta(seconds=300)) > 0)
    active_10min = sum(1 for d in range_data.values() if sum(1 for ts in d['timestamps'] if ts > now - timedelta(seconds=600)) > 0)
    
    return f"📊 STATISTICS\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n📋 Total CLIs: {len(UNIQUE_CLI)}\n📍 Total Ranges: {len(range_data)}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n📊 Active Ranges:\n• 2 Minutes: {active_2min}\n• 5 Minutes: {active_5min}\n• 10 Minutes: {active_10min}\n• 2 Hours: {len(range_data)}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n🕐 Last update: {last_update.strftime('%H:%M:%S') if last_update else 'Never'}\n🔄 Next update in: {cd}\n━━━━━━━━━━━━━━━━━━━━━━━━━━"


def get_cli_list():
    chunks = [UNIQUE_CLI[i:i+20] for i in range(0, len(UNIQUE_CLI), 20)]
    msg = f"📋 CLI LIST\n━━━━━━━━━━━━━━━━━━━━\n📊 Total: {len(UNIQUE_CLI)} CLIs\n\n"
    for i, ch in enumerate(chunks, 1):
        msg += f"{i}. {', '.join(ch)}\n"
    return msg


def get_main_menu():
    keyboard = [
        [KeyboardButton("🟢 ACTIVE RANGE (2 MIN)")],
        [KeyboardButton("📊 5 MIN REPORT"), KeyboardButton("📊 10 MIN REPORT")],
        [KeyboardButton("📊 2 HOURS RESULT"), KeyboardButton("🔍 SINGLE SEARCH")],
        [KeyboardButton("📈 STATISTICS"), KeyboardButton("🆘 HELP")],
        [KeyboardButton("👑 ADMIN PANEL")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_search_menu(query):
    return ReplyKeyboardMarkup([[KeyboardButton(f"📊 2 HOURS RESULT - {query}")], [KeyboardButton("🔙 BACK TO MAIN")]], resize_keyboard=True)


def get_admin_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ ADD CLI"), KeyboardButton("➖ REMOVE CLI")],
        [KeyboardButton("📋 VIEW ALL CLIS"), KeyboardButton("🔄 FORCE UPDATE")],
        [KeyboardButton("🔙 BACK TO MAIN")]
    ], resize_keyboard=True)


def is_admin(user_id):
    return user_id == ADMIN_ID


async def cmd_start(update, context):
    await update.message.reply_text(f"🎉 WELCOME! 🎉\n\n🤖 Orange CLI Monitor Bot\n\n👇 Use the buttons below!", reply_markup=get_main_menu())


async def handle_message(update, context):
    global UNIQUE_CLI
    text = update.message.text
    user_id = str(update.effective_user.id)
    
    if context.user_data.get('awaiting_search'):
        context.user_data['awaiting_search'] = False
        context.user_data['last_query'] = text.strip()
        await update.message.reply_text(f"✅ Searching for: {text}\n\nSelect result type:", reply_markup=get_search_menu(text))
        return
    
    if context.user_data.get('awaiting_add'):
        context.user_data['awaiting_add'] = False
        if is_admin(user_id) and text not in UNIQUE_CLI:
            UNIQUE_CLI.append(text)
            UNIQUE_CLI.sort()
            save_cli_list()
            await update.message.reply_text(f"✅ CLI {text} added!\nTotal: {len(UNIQUE_CLI)}", reply_markup=get_admin_menu())
        return
    
    if context.user_data.get('awaiting_remove'):
        context.user_data['awaiting_remove'] = False
        if is_admin(user_id) and text in UNIQUE_CLI:
            UNIQUE_CLI.remove(text)
            UNIQUE_CLI.sort()
            save_cli_list()
            await update.message.reply_text(f"✅ CLI {text} removed!\nTotal: {len(UNIQUE_CLI)}", reply_markup=get_admin_menu())
        return
    
    if text == "🟢 ACTIVE RANGE (2 MIN)":
        await update.message.reply_text("⏳ Fetching report...")
        await update.message.reply_text(get_report('2min'), parse_mode='Markdown', reply_markup=get_main_menu())
    elif text == "📊 5 MIN REPORT":
        await update.message.reply_text("⏳ Fetching report...")
        await update.message.reply_text(get_report('5min'), parse_mode='Markdown', reply_markup=get_main_menu())
    elif text == "📊 10 MIN REPORT":
        await update.message.reply_text("⏳ Fetching report...")
        await update.message.reply_text(get_report('10min'), parse_mode='Markdown', reply_markup=get_main_menu())
    elif text == "📊 2 HOURS RESULT":
        await update.message.reply_text("⏳ Fetching report...")
        await update.message.reply_text(get_report('2hours'), parse_mode='Markdown', reply_markup=get_main_menu())
    elif text == "🔍 SINGLE SEARCH":
        context.user_data['awaiting_search'] = True
        await update.message.reply_text("📝 Send a CLI number OR Country name\n\nExamples:\n• CLI: 5731\n• Country: CAMBODIA", reply_markup=get_main_menu())
    elif text == "📈 STATISTICS":
        await update.message.reply_text(get_stats(), reply_markup=get_main_menu())
    elif text == "🆘 HELP":
        await update.message.reply_text("🆘 HELP\n\n📌 ACTIVE RANGE (2 MIN) - Last 2 minutes\n📌 5 MIN REPORT - Last 5 minutes\n📌 10 MIN REPORT - Last 10 minutes\n📌 2 HOURS RESULT - Last 2 hours\n📌 SINGLE SEARCH - Search CLI/Country\n\n🤖 Status: 🟢 Online", reply_markup=get_main_menu())
    elif text == "👑 ADMIN PANEL":
        await update.message.reply_text("👑 ADMIN PANEL", reply_markup=get_admin_menu() if is_admin(user_id) else get_main_menu())
    elif text == "🔙 BACK TO MAIN":
        await update.message.reply_text("Main Menu:", reply_markup=get_main_menu())
    elif text.startswith("📊 2 HOURS RESULT - "):
        query = text.replace("📊 2 HOURS RESULT - ", "").strip()
        await update.message.reply_text(f"⏳ Searching...")
        await update.message.reply_text(await single_search(query), parse_mode='Markdown', reply_markup=get_search_menu(query))
    elif text == "🔄 FORCE UPDATE" and is_admin(user_id):
        await update.message.reply_text("🔄 Force updating...")
        await collect_all_data()
        await update.message.reply_text("✅ Update complete!", reply_markup=get_admin_menu())
    elif text == "➕ ADD CLI" and is_admin(user_id):
        context.user_data['awaiting_add'] = True
        await update.message.reply_text("Send CLI number to add:", reply_markup=get_admin_menu())
    elif text == "➖ REMOVE CLI" and is_admin(user_id):
        context.user_data['awaiting_remove'] = True
        await update.message.reply_text("Send CLI number to remove:", reply_markup=get_admin_menu())
    elif text == "📋 VIEW ALL CLIS" and is_admin(user_id):
        await update.message.reply_text(get_cli_list(), reply_markup=get_admin_menu())
    else:
        await update.message.reply_text("Please use the buttons below 👇\n\nType /start to see the menu.", reply_markup=get_main_menu())


async def auto_update_loop():
    await collect_all_data()
    while True:
        await asyncio.sleep(UPDATE_INTERVAL)
        try:
            await collect_all_data()
        except Exception as e:
            log_msg(f"Auto update error: {e}")


async def main():
    global application
    
    print("\n" + "=" * 60)
    print("🔥 ORANGE CLI MONITOR BOT - DEPLOYMENT READY")
    print("=" * 60)
    print(f"📋 Total CLIs: {len(UNIQUE_CLI)}")
    print(f"🔄 Update interval: {UPDATE_INTERVAL} seconds")
    print("=" * 60 + "\n")
    
    load_data()
    load_cli_list()
    
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await application.bot.set_my_commands([BotCommand("start", "Show main menu")])
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    log_msg("✅ Telegram bot ONLINE!")
    asyncio.create_task(auto_update_loop())
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        log_msg("Shutting down...")
        if application:
            await application.stop()
        print("\n✅ Bot stopped!")


if __name__ == "__main__":
    asyncio.run(main())