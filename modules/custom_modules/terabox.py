import re
import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message

from utils.db import db
from utils.misc import modules_help, prefix
from utils.scripts import format_exc

import json
from pathlib import Path
import aiofiles

# === CONSTANTS ===
TERABOX_REGEX = re.compile(
    r"https?://[^\s]*?(?:terabox|teraboxapp|teraboxshare|nephobox|1024tera)\.[^\s]+",
    re.IGNORECASE
)

TERABOX_KEY = "terabox"

# === NEW UNIFIED DB STRUCTURE ===
def get_terabox_config():
    """Return the full terabox config dict"""
    return db.get(TERABOX_KEY, "config", {
        "enabled": False,
        "target": None,
        "sources": [],
        "seen_links": [],
    })

def save_terabox_config(config):
    """Save the full terabox config dict"""
    db.set(TERABOX_KEY, "config", config)

# === CONFIG HELPERS ===
def is_terabox_enabled():
    return get_terabox_config().get("enabled", False)

def toggle_terabox():
    cfg = get_terabox_config()
    cfg["enabled"] = not cfg.get("enabled", False)
    save_terabox_config(cfg)
    return cfg["enabled"]

def get_target_chat():
    return get_terabox_config().get("target")

def set_target_chat(chat_id):
    cfg = get_terabox_config()
    cfg["target"] = chat_id
    save_terabox_config(cfg)

def get_sources():
    return get_terabox_config().get("sources", [])

def add_source(chat_id):
    cfg = get_terabox_config()
    if chat_id not in cfg["sources"]:
        cfg["sources"].append(chat_id)
        save_terabox_config(cfg)

def remove_source(chat_id):
    cfg = get_terabox_config()
    if chat_id in cfg["sources"]:
        cfg["sources"].remove(chat_id)
        save_terabox_config(cfg)

# === LINK STORAGE ===
def normalize_link(link: str) -> str:
    """Normalize TeraBox links for comparison"""
    return link.rstrip("/").lower()

def record_link(link: str):
    """Store link in DB to prevent duplicate forwarding"""
    cfg = get_terabox_config()
    normalized = normalize_link(link)
    seen_normalized = [normalize_link(l) for l in cfg["seen_links"]]
    
    if normalized not in seen_normalized:
        cfg["seen_links"].append(link)
        save_terabox_config(cfg)
        return True
    return False

def clear_terabox_db():
    cfg = get_terabox_config()
    cfg["seen_links"] = []
    save_terabox_config(cfg)
    return True

# === HELPERS ===
def extract_terabox_links(text: str):
    if not text:
        return []
    return TERABOX_REGEX.findall(text)

# === COMMANDS ===
@Client.on_message(filters.command("autoterabox", prefix) & filters.me)
async def toggle_autoterabox(client: Client, message: Message):
    state = toggle_terabox()
    await message.edit(f"{'✅' if state else '❌'} <b>Auto TeraBox Forward</b> {'enabled' if state else 'disabled'}.")

@Client.on_message(filters.command("settb", prefix) & filters.me)
async def set_tbox_target(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.edit(f"Usage: <code>{prefix}settb [chat_id]</code>")
    
    try:
        chat_id = int(message.command[1])
        set_target_chat(chat_id)
        await message.edit(f"✅ Set TeraBox target to <code>{chat_id}</code>")
    except ValueError:
        await message.edit("❌ Invalid chat ID. Must be a number.")

@Client.on_message(filters.command("addtb", prefix) & filters.me)
async def add_tbox_source(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.edit(f"Usage: <code>{prefix}addtb [chat_id]</code>")
    
    try:
        chat_id = int(message.command[1])
        add_source(chat_id)
        await message.edit(f"✅ Added TeraBox source <code>{chat_id}</code>")
    except ValueError:
        await message.edit("❌ Invalid chat ID. Must be a number.")

@Client.on_message(filters.command("deltb", prefix) & filters.me)
async def del_tbox_source(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.edit(f"Usage: <code>{prefix}deltb [chat_id]</code>")
    
    try:
        chat_id = int(message.command[1])
        remove_source(chat_id)
        await message.edit(f"🗑 Removed TeraBox source <code>{chat_id}</code>")
    except ValueError:
        await message.edit("❌ Invalid chat ID. Must be a number.")

@Client.on_message(filters.command("listtb", prefix) & filters.me)
async def list_tbox_sources(client: Client, message: Message):
    cfg = get_terabox_config()
    status = "✅ Enabled" if cfg.get("enabled") else "❌ Disabled"
    text = f"<b>📦 TeraBox Auto-Forward</b>\n\n<b>Status:</b> {status}\n"
    text += f"<b>Target:</b> <code>{cfg.get('target')}</code>\n\n<b>Sources:</b>\n"
    if not cfg["sources"]:
        text += "• None"
    else:
        text += "\n".join(f"• <code>{x}</code>" for x in cfg["sources"])
    text += f"\n\n<b>Seen Links:</b> {len(cfg.get('seen_links', []))}"
    await message.edit(text)

@Client.on_message(filters.command("cleartbdb", prefix) & filters.me)
async def clear_tb_db_cmd(client: Client, message: Message):
    clear_terabox_db()
    await message.edit("🧹 Cleared TeraBox forwarded link database!")

# === AUTO FORWARD (media + links only) ===
@Client.on_message(~filters.me)
async def terabox_auto_forward(client: Client, message: Message):
    if not is_terabox_enabled():
        return

    sources = get_sources()
    target = get_target_chat()
    if not sources or not target:
        return

    if message.chat.id not in sources:
        return

    text = message.text or message.caption
    if not text:
        return

    links = extract_terabox_links(text)
    if not links:
        return

    new_links = [link for link in links if record_link(link)]
    if not new_links:
        return

    link_text = "\n".join(new_links)

    try:
        if getattr(message, "media", None):
            await message.copy(int(target), caption=link_text)
        else:
            await client.send_message(int(target), link_text)
        await asyncio.sleep(2.5)
    except Exception as e:
        print(f"[Terabox AutoForward] Error: {e}")


# === BULK FORWARD (media + links only) ===
@Client.on_message(filters.command("bulktb", prefix) & filters.me)
async def bulk_terabox(client: Client, message: Message):
    """
    Usage: .bulktb [source_id] [limit|all] [delay]
    Example: .bulktb -1001234567890 100 2
             .bulktb -1001234567890 all 2
    """
    status_msg = None
    try:
        args = message.text.split()
        if len(args) < 2:
            return await message.edit(
                f"<b>Usage:</b> <code>{prefix}bulktb [source_chat_id] [limit|all] [delay]</code>\n"
                f"<b>Example:</b> <code>{prefix}bulktb -1001234567890 100 3</code>\n"
                f"<b>Example:</b> <code>{prefix}bulktb -1001234567890 all 2</code>"
            )

        source_id = int(args[1])
        limit_arg = args[2] if len(args) > 2 else "50"
        delay = float(args[3]) if len(args) > 3 else 2.5

        if limit_arg.lower() == "all":
            limit = None
        else:
            limit = int(limit_arg)

        target_id = get_target_chat()
        if not target_id:
            return await message.edit("❌ No target chat set! Use <code>.settb [chat_id]</code> first.")

        status_msg = await message.edit(f"🔍 Fetching messages from <code>{source_id}</code>...")

        # --- Fetch messages upfront (with limit to prevent memory issues) ---
        fetched_messages = []
        fetch_count = 0
        max_fetch = limit if limit else 10000  # Cap at 10k for "all" to prevent freezing
        
        async for msg in client.get_chat_history(source_id, limit=limit):
            fetch_count += 1
            text = msg.text or msg.caption
            if text and extract_terabox_links(text):
                fetched_messages.append(msg)
            
            # Update every 100 messages during fetch
            if fetch_count % 100 == 0:
                try:
                    await status_msg.edit(f"🔍 Fetched {fetch_count} messages... Found {len(fetched_messages)} with links")
                except Exception:
                    pass
            
            # Safety limit
            if fetch_count >= max_fetch:
                break

        total_messages = len(fetched_messages)
        total_links = sum(len(extract_terabox_links(m.text or m.caption)) for m in fetched_messages)

        if total_messages == 0:
            return await message.edit("⚠️ No messages with TeraBox links found.")

        # Reverse to process oldest → newest
        fetched_messages.reverse()

        # --- Load seen links in memory (set) ---
        cfg = get_terabox_config()
        seen_links = {normalize_link(l) for l in cfg.get("seen_links", [])}

        sent_messages = 0
        skipped_messages = 0
        forwarded_links = 0
        start_time = time.time()

        status_msg = await message.edit(
            f"📦 Found {total_messages} messages with {total_links} links.\nStarting forward..."
        )

        # --- Process messages sequentially ---
        for idx, msg in enumerate(fetched_messages, 1):
            try:
                text = msg.text or msg.caption
                links = extract_terabox_links(text)

                if not links:
                    skipped_messages += 1
                    continue

                # Check which links are new
                new_links = []
                for link in links:
                    normalized = normalize_link(link)
                    if normalized not in seen_links:
                        seen_links.add(normalized)
                        new_links.append(link)

                if not new_links:
                    skipped_messages += 1
                    continue

                link_text = "\n".join(new_links)

                # Forward message
                if getattr(msg, "media", None):
                    await msg.copy(int(target_id), caption=link_text)
                else:
                    await client.send_message(int(target_id), link_text)

                sent_messages += 1
                forwarded_links += len(new_links)

            except Exception as e:
                print(f"[BulkTBox] Failed msg {msg.id}: {e}")
                skipped_messages += 1

            # Update progress every 20 messages
            if idx % 20 == 0 or idx == total_messages:
                elapsed = int(time.time() - start_time)
                eta = int((total_messages - idx) * delay)
                progress = (
                    f"📦 Processing {idx}/{total_messages} | Sent: {sent_messages} | "
                    f"Skipped: {skipped_messages} | Links forwarded: {forwarded_links} | "
                    f"Elapsed: {elapsed}s | ETA: {eta}s"
                )
                try:
                    await status_msg.edit(progress)
                except Exception:
                    pass

            await asyncio.sleep(delay)

        # --- Batch write seen links to DB ---
        # Simply store normalized links as a list (much faster)
        cfg["seen_links"] = list(seen_links)
        save_terabox_config(cfg)

        total_time = int(time.time() - start_time)
        await status_msg.edit(
            f"✅ Bulk forward completed!\n\n"
            f"Source: <code>{source_id}</code>\n"
            f"Target: <code>{target_id}</code>\n"
            f"Total messages: {total_messages}\n"
            f"Messages forwarded: {sent_messages}\n"
            f"Messages skipped: {skipped_messages}\n"
            f"Total links forwarded: {forwarded_links}\n"
            f"Total time: {total_time}s"
        )

    except ValueError as e:
        error_msg = f"❌ Invalid input: {str(e)}"
        if status_msg:
            await status_msg.edit(error_msg)
        else:
            await message.edit(error_msg)
    except Exception as e:
        error_msg = f"❌ Error in bulk forward:\n<code>{format_exc(e)}</code>"
        if status_msg:
            try:
                await status_msg.edit(error_msg)
            except:
                await message.reply(error_msg)
        else:
            await message.edit(error_msg)


@Client.on_message(filters.command("exporttb", prefix) & filters.me)
async def scrapetb_send(client: Client, message: Message):
    """
    Fast scrape TeraBox links from a source chat, save to JSON, and send the file.
    Usage: .scrapetb_send [source_chat_id] [limit|all]
    """
    try:
        args = message.text.split()
        if len(args) < 2:
            return await message.edit(
                f"<b>Usage:</b> <code>{prefix}scrapetb_send [source_chat_id] [limit|all]</code>"
            )

        source_id = int(args[1])
        limit_arg = args[2] if len(args) > 2 else "1000"
        limit = None if limit_arg.lower() == "all" else int(limit_arg)

        links = []
        msg_count = 0

        status_msg = await message.edit(f"🔍 Fetching messages from <code>{source_id}</code>...")

        # Fetch messages
        async for msg in client.get_chat_history(source_id, limit=limit):
            msg_count += 1
            text = msg.text or msg.caption
            if text:
                msg_links = extract_terabox_links(text)
                if msg_links:
                    links.extend(msg_links)

            # Update progress every 50 messages
            if msg_count % 50 == 0:
                try:
                    await status_msg.edit(f"📤 Fetched {msg_count} messages | Links found: {len(links)}")
                except Exception:
                    pass

        if not links:
            return await message.edit(f"⚠️ No TeraBox links found in {msg_count} messages.")

        # Save to JSON async
        save_path = Path("terabox_links.json")
        async with aiofiles.open(save_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(links, ensure_ascii=False, indent=2))

        # Send the file (correct way for Pyrogram)
        await client.send_document(
            chat_id=message.chat.id,
            document=str(save_path),  # ✅ Just pass the path as string
            caption=f"✅ Scraped {len(links)} TeraBox links from {msg_count} messages."
        )

        # Delete the status message after sending file
        await status_msg.delete()
        
        # Clean up the file
        save_path.unlink(missing_ok=True)

    except Exception as e:
        await message.edit(format_exc(e))
        
# === HELP MENU ===
modules_help["terabox"] = {
    "autoterabox": "Toggle automatic TeraBox link forwarding on/off",
    "settb [chat_id]": "Set target chat for TeraBox forwards",
    "addtb [chat_id]": "Add a source channel/group for TeraBox links",
    "deltb [chat_id]": "Remove a source channel/group",
    "listtb": "Show TeraBox forwarding config and status",
    "bulktb [source_id] [limit] [delay]": "Bulk-forward last N TeraBox messages with delay",
    "scrapetb_send [source_id] [limit]": "Scrape TeraBox links to JSON file",
    "cleartbdb": "Clear stored TeraBox links from DB to allow re-forwarding",
}
