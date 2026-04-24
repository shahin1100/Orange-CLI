#!/usr/bin/env python3
"""
====================================================================================================
     ORANGE CARRIER LIVE RANGE MONITOR BOT - COMPLETE WORKING VERSION
====================================================================================================
"""

import asyncio
import re
import sys
import os
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
import logging

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright, Browser, Page, Playwright


# ====================================================================================================
# CONFIGURATION
# ====================================================================================================

BOT_TOKEN = '8797301264:AAGiRBRNGan5kHleOh319qTz4IOjtaJrIQk'
ADMIN_ID = '7064572216'

ORANGE_EMAIL = 'n.nazim1132@gmail.com'
ORANGE_PASSWORD = 'Abcd1234'

LOGIN_URL = 'https://www.orangecarrier.com/login'
CLI_ACCESS_URL = 'https://www.orangecarrier.com/services/cli/access'

CLI_LIST = [
    '5731', '5730', '5732', '1315', '1646', '4983', '3375', '4473', '9989',
    '3598', '9891', '2917', '3706', '9890', '3737', '9891', '9893', '4857',
    '9639', '9899', '8617', '8615', '8613', '8618', '8619', '7863', '2348',
    '4822', '4845', '4857', '3462', '1425', '9981', '3247', '9989', '5715',
    '4915', '9725', '2332', '7708', '4473', '5591', '3933', '2011', '9178'
]

UNIQUE_CLI = list(set(CLI_LIST))
UNIQUE_CLI.sort()

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


# ====================================================================================================
# GLOBAL VARIABLES
# ====================================================================================================

playwright: Optional[Playwright] = None
browser: Optional[Browser] = None
page: Optional[Page] = None
application: Optional[Application] = None

range_data: Dict[str, List[datetime]] = {}
range_cli_sources: Dict[str, Dict[str, int]] = {}
reports: Dict[str, Dict] = {}
last_data_collection: Optional[datetime] = None
next_collection: Optional[datetime] = None
is_collecting: bool = False
is_running: bool = True
total_searches: int = 0


def log_msg(msg: str, level: str = "INFO"):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"[{t}] {msg}")
    if level == "ERROR":
        logger.error(msg)
    elif level == "WARNING":
        logger.warning(msg)
    else:
        logger.info(msg)


def save_data():
    try:
        data = {
            'timestamps': {name: [ts.isoformat() for ts in timestamps] for name, timestamps in range_data.items()},
            'clis': range_cli_sources
        }
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        log_msg(f"Save error: {e}", "ERROR")


def load_data():
    global range_data, range_cli_sources
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
            range_data = {}
            for name, timestamps in data.get('timestamps', {}).items():
                range_data[name] = [datetime.fromisoformat(ts) for ts in timestamps]
            range_cli_sources = data.get('clis', {})
            log_msg(f"Loaded {len(range_data)} ranges")
    except Exception as e:
        log_msg(f"Load error: {e}", "WARNING")


def save_cli_list():
    try:
        with open(CLI_FILE, 'w') as f:
            json.dump(UNIQUE_CLI, f)
    except Exception as e:
        log_msg(f"CLI save error: {e}", "ERROR")


def load_cli_list():
    global UNIQUE_CLI
    try:
        if os.path.exists(CLI_FILE):
            with open(CLI_FILE, 'r') as f:
                UNIQUE_CLI = json.load(f)
            log_msg(f"Loaded {len(UNIQUE_CLI)} CLIs")
    except Exception as e:
        log_msg(f"CLI load error: {e}", "WARNING")


def extract_country_from_range(range_name: str) -> str:
    if not range_name:
        return "Unknown"
    match = re.match(r'^([A-Z][A-Z\s]+?)\s+(?:MOBILE|FIXED|IPRN)', range_name, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return range_name.split()[0] if range_name.split() else "Unknown"


def get_time_ago_str(dt: datetime) -> str:
    if not dt:
        return "unknown"
    seconds = (datetime.now() - dt).total_seconds()
    if seconds < 60:
        return f"{int(seconds)}s ago"
    elif seconds < 3600:
        return f"{int(seconds//60)}m ago"
    else:
        return f"{int(seconds//3600)}h ago"


def extract_range_name(txt: str) -> Optional[str]:
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


def parse_time_string(txt: str) -> Optional[int]:
    if not txt:
        return None
    t = txt.lower().strip()
    if 'just now' in t or t == 'now':
        return 0
    match = re.search(r'(\d+)\s*(?:sec|seconds?)', t)
    if match:
        return int(match.group(1))
    match = re.search(r'(\d+)\s*(?:min|minutes?)', t)
    if match:
        return int(match.group(1)) * 60
    match = re.search(r'(\d+)\s*(?:hour|hours?)', t)
    if match:
        return int(match.group(1)) * 3600
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


async def login() -> bool:
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
            log_msg(f"Login attempt {attempt+1} failed: {e}", "WARNING")
            await asyncio.sleep(5)
    
    return False


async def find_search_box():
    selectors = [
        'input[type="search"]',
        'input[placeholder*="Search"]',
        'input[placeholder*="search"]',
        'input[placeholder*="CLI"]',
        'input[name="search"]',
        'input'
    ]
    for sel in selectors:
        try:
            box = await page.query_selector(sel)
            if box and await box.is_visible():
                return box
        except:
            pass
    return None


async def search_cli(cli: str) -> List[Tuple[str, int]]:
    try:
        box = await find_search_box()
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
            seconds = parse_time_string(line)
            if seconds is not None:
                rng = None
                if i > 0:
                    rng = extract_range_name(lines[i-1])
                if not rng:
                    rng = extract_range_name(line)
                if rng:
                    results.append((rng, seconds))
        
        return results
        
    except Exception as e:
        log_msg(f"Search error for {cli}: {e}")
        return []


async def collect_all_data():
    global range_data, range_cli_sources, last_data_collection, next_collection, is_collecting, total_searches
    
    if is_collecting:
        return
    
    is_collecting = True
    log_msg(f"📊 Collecting data from {len(UNIQUE_CLI)} CLIs...")
    start = datetime.now()
    
    try:
        await page.reload(wait_until='networkidle', timeout=30000)
        await asyncio.sleep(2)
        await close_popups()
        
        now = datetime.now()
        new_hits = []
        
        for cli in UNIQUE_CLI:
            hits = await search_cli(cli)
            total_searches += 1
            
            for rng, sec in hits:
                hit_time = now - timedelta(seconds=sec)
                new_hits.append((rng, hit_time, cli))
            
            await asyncio.sleep(0.3)
        
        for rng, hit_time, cli in new_hits:
            if rng not in range_data:
                range_data[rng] = []
                range_cli_sources[rng] = {}
            range_data[rng].append(hit_time)
            range_cli_sources[rng][cli] = range_cli_sources[rng].get(cli, 0) + 1
        
        cutoff = now - timedelta(seconds=7200)
        for rng in list(range_data.keys()):
            range_data[rng] = [ts for ts in range_data[rng] if ts > cutoff]
            if not range_data[rng]:
                del range_data[rng]
                if rng in range_cli_sources:
                    del range_cli_sources[rng]
        
        last_data_collection = now
        next_collection = now + timedelta(seconds=UPDATE_INTERVAL)
        
        update_reports()
        
        duration = (datetime.now() - start).total_seconds()
        log_msg(f"✅ Data collection done: {len(range_data)} ranges, {duration:.1f}s")
        save_data()
        
    except Exception as e:
        log_msg(f"Collection error: {e}", "ERROR")
    
    finally:
        is_collecting = False


def update_reports():
    global reports
    
    now = datetime.now()
    
    for name, seconds in TIME_WINDOWS.items():
        top_ranges = []
        total_hits = 0
        
        for rng, timestamps in range_data.items():
            cnt = sum(1 for ts in timestamps if ts > now - timedelta(seconds=seconds))
            if cnt > 0:
                last_hit = max(ts for ts in timestamps if ts > now - timedelta(seconds=seconds))
                cli_count = len(range_cli_sources.get(rng, {}))
                top_ranges.append((rng, cnt, last_hit, cli_count))
                total_hits += cnt
        
        top_ranges.sort(key=lambda x: x[1], reverse=True)
        
        reports[name] = {
            'seconds': seconds,
            'ranges': top_ranges[:20],
            'total_hits': total_hits,
            'total_ranges': len(top_ranges),
            'last_update': last_data_collection or now
        }


def get_countdown() -> str:
    if not next_collection:
        return "calculating..."
    remaining = (next_collection - datetime.now()).seconds
    if remaining >= 60:
        return f"{remaining//60}m {remaining%60}s"
    return f"{remaining}s"


def format_window_name(seconds: int) -> str:
    if seconds == 120:
        return "2 Minutes"
    elif seconds == 300:
        return "5 Minutes"
    elif seconds == 600:
        return "10 Minutes"
    elif seconds == 7200:
        return "2 Hours"
    return f"{seconds//60} Minutes"


def get_country_summary(ranges: List[Tuple[str, int, datetime, int]]) -> List[Tuple[str, int, int]]:
    country_data = defaultdict(lambda: {'hits': 0, 'ranges': set()})
    for name, hits, _, _ in ranges:
        country = extract_country_from_range(name)
        country_data[country]['hits'] += hits
        country_data[country]['ranges'].add(name)
    summary = [(c, d['hits'], len(d['ranges'])) for c, d in country_data.items()]
    summary.sort(key=lambda x: x[1], reverse=True)
    return summary[:15]


def get_report_for_window(window_name: str) -> str:
    if window_name not in reports:
        return "⏳ First data collection in progress, please wait..."
    
    r = reports[window_name]
    cd = get_countdown()
    
    if not r['ranges']:
        return f"📡 No active ranges found in last {TIME_WINDOWS[window_name]//60} minutes"
    
    win_display = format_window_name(r['seconds'])
    country_summary = get_country_summary(r['ranges'])
    
    report = f"🔥 {win_display} REPORT 🔥\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"🕐 Time: {r['last_update'].strftime('%H:%M:%S')}\n"
    report += f"⏱️ Window: Last {win_display}\n"
    report += f"📊 Active Ranges: {r['total_ranges']}\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if country_summary:
        report += f"📊 COUNTRY SUMMARY 📊\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for i, (country, hits, rc) in enumerate(country_summary[:10], 1):
            report += f"{i}. {country} | {hits} hits | {rc} ranges\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    report += f"🔥 TOP 20 RANGES 🔥\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, (name, cnt, last, cli_count) in enumerate(r['ranges'][:20], 1):
        report += f"{i}. `{name}`\n"
        report += f"   📊 {cnt} hits | {cli_count} CLI | ⏱️ {get_time_ago_str(last)}\n"
        report += f"   ────────────────────\n"
    
    report += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"📈 Total Hits: {r['total_hits']}\n"
    report += f"🔄 Next update in: {cd}\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"💡 Tap any range name to copy it"
    
    return report


async def single_search_cli(query: str, window_seconds: int, window_name: str) -> str:
    if not last_data_collection:
        return "⏳ Data collection in progress, please wait..."
    
    query_lower = query.lower().strip()
    now = datetime.now()
    results = []
    
    for rng, timestamps in range_data.items():
        if query_lower in rng.lower():
            cnt = sum(1 for ts in timestamps if ts > now - timedelta(seconds=window_seconds))
            if cnt > 0:
                last_hit = max(ts for ts in timestamps if ts > now - timedelta(seconds=window_seconds))
                cli_count = len(range_cli_sources.get(rng, {}))
                results.append((rng, cnt, last_hit, cli_count))
    
    results.sort(key=lambda x: x[1], reverse=True)
    top = results[:20]
    
    if not top:
        return f"🔍 SEARCH: {query}\n━━━━━━━━━━━━━━━━━━━━\n📭 No results found in {window_name}"
    
    country_summary = get_country_summary(top)
    
    report = f"🔍 {query} — {window_name} RESULTS 🔍\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"⏱️ Window: {window_name}\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if country_summary:
        report += f"📊 COUNTRY SUMMARY 📊\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for i, (country, hits, rc) in enumerate(country_summary[:10], 1):
            report += f"{i}. {country} | {hits} hits | {rc} ranges\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    report += f"🔥 TOP 20 RANGES 🔥\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, (name, cnt, last, cli_count) in enumerate(top, 1):
        report += f"{i}. `{name}`\n"
        report += f"   📊 {cnt} hits | {cli_count} CLI | ⏱️ {get_time_ago_str(last)}\n"
        report += f"   ────────────────────\n"
    
    total_hits = sum(c for _, c, _, _ in top)
    report += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"📈 Total Hits: {total_hits}\n"
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"💡 Tap any range name to copy it"
    
    return report


def get_statistics() -> str:
    cd = get_countdown()
    now = datetime.now()
    
    active_2min = sum(1 for ts_list in range_data.values() if sum(1 for ts in ts_list if ts > now - timedelta(seconds=120)) > 0)
    active_5min = sum(1 for ts_list in range_data.values() if sum(1 for ts in ts_list if ts > now - timedelta(seconds=300)) > 0)
    active_10min = sum(1 for ts_list in range_data.values() if sum(1 for ts in ts_list if ts > now - timedelta(seconds=600)) > 0)
    active_2hours = len(range_data)
    
    return (
        f"📊 STATISTICS\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Total CLIs: {len(UNIQUE_CLI)}\n"
        f"📍 Total Ranges Tracked: {len(range_data)}\n"
        f"🎯 Total Searches: {total_searches}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Active Ranges:\n"
        f"• 2 Minutes: {active_2min}\n"
        f"• 5 Minutes: {active_5min}\n"
        f"• 10 Minutes: {active_10min}\n"
        f"• 2 Hours: {active_2hours}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Last update: {last_data_collection.strftime('%H:%M:%S') if last_data_collection else 'Never'}\n"
        f"🔄 Next update in: {cd}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


def get_cli_list_text() -> str:
    chunks = [UNIQUE_CLI[i:i+20] for i in range(0, len(UNIQUE_CLI), 20)]
    msg = f"📋 CLI LIST\n━━━━━━━━━━━━━━━━━━━━\n📊 Total: {len(UNIQUE_CLI)} CLIs\n\n"
    for i, ch in enumerate(chunks, 1):
        msg += f"{i}. {', '.join(ch)}\n"
    return msg


def get_help_text() -> str:
    return (
        f"🆘 HELP & SUPPORT\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 AVAILABLE BUTTONS:\n"
        f"• 🟢 ACTIVE RANGE (2 MIN) - Last 2 minutes\n"
        f"• 📊 5 MIN REPORT - Last 5 minutes\n"
        f"• 📊 10 MIN REPORT - Last 10 minutes\n"
        f"• 📊 2 HOURS RESULT - Last 2 hours\n"
        f"• 🔍 SINGLE SEARCH - Search CLI or Country\n"
        f"• 📈 STATISTICS - Bot statistics\n"
        f"• 👑 ADMIN PANEL - Admin features\n\n"
        f"📌 SINGLE SEARCH GUIDE:\n"
        f"1. Click SINGLE SEARCH\n"
        f"2. Send CLI number (e.g., 5731) or Country name (e.g., CAMBODIA)\n"
        f"3. Select 5 MIN RESULT or 2 HOURS RESULT\n\n"
        f"🤖 Status: 🟢 Online\n"
        f"🔄 Update Interval: Every 60 seconds"
    )


def get_main_menu():
    keyboard = [
        [KeyboardButton("🟢 ACTIVE RANGE (2 MIN)")],
        [KeyboardButton("📊 5 MIN REPORT"), KeyboardButton("📊 10 MIN REPORT")],
        [KeyboardButton("📊 2 HOURS RESULT"), KeyboardButton("🔍 SINGLE SEARCH")],
        [KeyboardButton("📈 STATISTICS"), KeyboardButton("🆘 HELP")],
        [KeyboardButton("👑 ADMIN PANEL")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_search_menu(query: str):
    keyboard = [
        [KeyboardButton(f"📊 5 MIN RESULT - {query}")],
        [KeyboardButton(f"📊 2 HOURS RESULT - {query}")],
        [KeyboardButton("🔙 BACK TO MAIN")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_admin_menu():
    keyboard = [
        [KeyboardButton("➕ ADD CLI"), KeyboardButton("➖ REMOVE CLI")],
        [KeyboardButton("📋 VIEW ALL CLIS"), KeyboardButton("🔄 FORCE UPDATE")],
        [KeyboardButton("🔙 BACK TO MAIN")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def is_admin(user_id: str) -> bool:
    return user_id == ADMIN_ID


async def auto_collection_loop():
    global is_running
    await collect_all_data()
    while is_running:
        await asyncio.sleep(UPDATE_INTERVAL)
        try:
            log_msg("🔄 Auto data collection...")
            await collect_all_data()
        except Exception as e:
            log_msg(f"Auto error: {e}", "ERROR")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name or "User"
    await update.message.reply_text(
        f"🎉 WELCOME {user_name} TO ORANGE CLI BOT! 🎉\n\n"
        f"🤖 Live CLI Range Monitor Bot\n\n"
        f"📌 FEATURES:\n"
        f"• Real-time CLI range monitoring\n"
        f"• Time windows: 2m, 5m, 10m, 2h\n"
        f"• Country summary with hit counts\n"
        f"• CLI count per range\n"
        f"• Tap any range name to copy\n\n"
        f"👇 Use the buttons below!",
        reply_markup=get_main_menu()
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global UNIQUE_CLI
    
    text = update.message.text
    user_id = str(update.effective_user.id)
    
    if context.user_data.get('awaiting_search'):
        context.user_data['awaiting_search'] = False
        query = text.strip()
        context.user_data['last_query'] = query
        await update.message.reply_text(
            f"✅ Searching for: {query}\n\nSelect result type:",
            reply_markup=get_search_menu(query)
        )
        return
    
    if context.user_data.get('awaiting_add'):
        context.user_data['awaiting_add'] = False
        if is_admin(user_id):
            if text not in UNIQUE_CLI:
                UNIQUE_CLI.append(text)
                UNIQUE_CLI.sort()
                save_cli_list()
                await update.message.reply_text(f"✅ CLI {text} added!\nTotal: {len(UNIQUE_CLI)}", reply_markup=get_admin_menu())
            else:
                await update.message.reply_text(f"⚠️ CLI {text} already exists!", reply_markup=get_admin_menu())
        return
    
    if context.user_data.get('awaiting_remove'):
        context.user_data['awaiting_remove'] = False
        if is_admin(user_id):
            if text in UNIQUE_CLI:
                UNIQUE_CLI.remove(text)
                UNIQUE_CLI.sort()
                save_cli_list()
                await update.message.reply_text(f"✅ CLI {text} removed!\nTotal: {len(UNIQUE_CLI)}", reply_markup=get_admin_menu())
            else:
                await update.message.reply_text(f"⚠️ CLI {text} not found!", reply_markup=get_admin_menu())
        return
    
    if text == "🟢 ACTIVE RANGE (2 MIN)":
        await update.message.reply_text("⏳ Fetching 2 minutes report...")
        await update.message.reply_text(get_report_for_window('2min'), parse_mode='Markdown', reply_markup=get_main_menu())
    
    elif text == "📊 5 MIN REPORT":
        await update.message.reply_text("⏳ Fetching 5 minutes report...")
        await update.message.reply_text(get_report_for_window('5min'), parse_mode='Markdown', reply_markup=get_main_menu())
    
    elif text == "📊 10 MIN REPORT":
        await update.message.reply_text("⏳ Fetching 10 minutes report...")
        await update.message.reply_text(get_report_for_window('10min'), parse_mode='Markdown', reply_markup=get_main_menu())
    
    elif text == "📊 2 HOURS RESULT":
        await update.message.reply_text("⏳ Fetching 2 hours report...")
        await update.message.reply_text(get_report_for_window('2hours'), parse_mode='Markdown', reply_markup=get_main_menu())
    
    elif text == "🔍 SINGLE SEARCH":
        context.user_data['awaiting_search'] = True
        await update.message.reply_text(
            "📝 Send a CLI number OR Country name\n\n"
            "Examples:\n"
            "• CLI: 5731\n"
            "• Country: CAMBODIA\n\n"
            "After sending, select result type.",
            reply_markup=get_main_menu()
        )
    
    elif text == "📈 STATISTICS":
        await update.message.reply_text(get_statistics(), reply_markup=get_main_menu())
    
    elif text == "🆘 HELP":
        await update.message.reply_text(get_help_text(), reply_markup=get_main_menu())
    
    elif text == "👑 ADMIN PANEL":
        if is_admin(user_id):
            await update.message.reply_text("👑 ADMIN PANEL\n━━━━━━━━━━━━━━━━━━━━\nWelcome Admin!", reply_markup=get_admin_menu())
        else:
            await update.message.reply_text("⛔ Access Denied!", reply_markup=get_main_menu())
    
    elif text == "🔙 BACK TO MAIN":
        await update.message.reply_text("Main Menu:", reply_markup=get_main_menu())
    
    elif text.startswith("📊 5 MIN RESULT - "):
        query = text.replace("📊 5 MIN RESULT - ", "").strip()
        await update.message.reply_text(f"⏳ Fetching 5 minutes result for {query}...")
        result = await single_search_cli(query, 300, "LAST 5 MINUTES")
        await update.message.reply_text(result, parse_mode='Markdown', reply_markup=get_search_menu(query))
    
    elif text.startswith("📊 2 HOURS RESULT - "):
        query = text.replace("📊 2 HOURS RESULT - ", "").strip()
        await update.message.reply_text(f"⏳ Fetching 2 hours result for {query}...")
        result = await single_search_cli(query, 7200, "LAST 2 HOURS")
        await update.message.reply_text(result, parse_mode='Markdown', reply_markup=get_search_menu(query))
    
    elif text == "🔄 FORCE UPDATE":
        if is_admin(user_id):
            await update.message.reply_text("🔄 Force updating data...")
            await collect_all_data()
            await update.message.reply_text("✅ Update complete!", reply_markup=get_admin_menu())
        else:
            await update.message.reply_text("⛔ Admin only!")
    
    elif text == "➕ ADD CLI":
        if is_admin(user_id):
            context.user_data['awaiting_add'] = True
            await update.message.reply_text("Send CLI number to add:\n\nExample: 5731", reply_markup=get_admin_menu())
        else:
            await update.message.reply_text("⛔ Admin only!")
    
    elif text == "➖ REMOVE CLI":
        if is_admin(user_id):
            context.user_data['awaiting_remove'] = True
            await update.message.reply_text("Send CLI number to remove:\n\nExample: 5731", reply_markup=get_admin_menu())
        else:
            await update.message.reply_text("⛔ Admin only!")
    
    elif text == "📋 VIEW ALL CLIS":
        if is_admin(user_id):
            await update.message.reply_text(get_cli_list_text(), reply_markup=get_admin_menu())
        else:
            await update.message.reply_text("⛔ Admin only!")
    
    else:
        await update.message.reply_text("Please use the buttons below 👇\n\nType /start to see the menu.", reply_markup=get_main_menu())


async def init_browser():
    global playwright, browser, page
    
    log_msg("🚀 Starting Chrome browser...")
    
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=True,
        args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
    )
    
    context = await browser.new_context(viewport={'width': 1280, 'height': 720})
    page = await context.new_page()
    
    log_msg("✅ Browser started")
    return True


async def main():
    global application, is_running
    
    print("\n" + "=" * 70)
    print("🔥 ORANGE CARRIER RANGE MONITOR BOT - FULL WORKING VERSION")
    print("=" * 70)
    print(f"📧 Email: {ORANGE_EMAIL}")
    print(f"📋 Total CLIs: {len(UNIQUE_CLI)}")
    print(f"⏱️ Windows: 2min, 5min, 10min, 2hours")
    print(f"🔄 Data collection: Every {UPDATE_INTERVAL} seconds")
    print("=" * 70 + "\n")
    
    load_data()
    load_cli_list()
    
    if not await init_browser():
        log_msg("Browser failed!", "ERROR")
        return
    
    login_ok = False
    for i in range(3):
        log_msg(f"Login {i+1}/3...")
        if await login():
            login_ok = True
            break
        await asyncio.sleep(5)
    
    if not login_ok:
        log_msg("Login failed!", "ERROR")
        return
    
    log_msg("✅ Ready!")
    
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await application.bot.set_my_commands([BotCommand("start", "Show main menu")])
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    log_msg("✅ Telegram bot ONLINE!")
    
    asyncio.create_task(auto_collection_loop())
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        is_running = False
        log_msg("Shutting down...")
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        if application:
            await application.stop()
        print("\n✅ Bot stopped!")


if __name__ == "__main__":
    asyncio.run(main())