import asyncio
import os
import requests
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from db import (
    init_db, save_order, update_checkout_id, get_order,
    mark_paid, get_paid_orders
)

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

BACHS_KEY = os.getenv("BACHS_KEY", "").strip()
BACHS_URL = "https://api.bachs.io/v1/checkout-sessions"

PRODUCTS = {
    "jendol":  {"name": "Jendol",        "price": 2000, "prod_id": "prod_667c2d773cc940ea86f3"},
    "bukku":   {"name": "Bukku",         "price": 2000, "prod_id": "prod_2f0ee6231795409ea0ae"},
    "market":  {"name": "Market Square", "price": 2500, "prod_id": "prod_d44d638ab9554202bd0f"},
}

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN not set. Run: export BOT_TOKEN='...'")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
sessions = {}

# ── HELPERS ─────────────────────────────────────────────
def bread_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{p['name']} — ₦{p['price']}", callback_data=f"bread:{slug}")]
        for slug, p in PRODUCTS.items()
    ])

def create_bachs_checkout(order):
    """Call Bachs API to create a checkout session. Returns checkout_url + checkout_id."""
    payload = {
        "product_cart": [
            {"product_id": PRODUCTS[order["bread_slug"]]["prod_id"], "quantity": order["qty"]}
        ],
        "payment_method_types": ["NGN_CARD", "NGN_BANK_TRANSFER"],
        "customer": {
            "email": f"student{order['telegram_id']}@example.com",
            "name": f"Student {order['telegram_id']}"
        },
        "reference": order["id"],
    }
    headers = {
        "Authorization": f"Bearer {BACHS_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(BACHS_URL, json=payload, headers=headers, timeout=20)

    # Bachs returns 201 Created on success
    if resp.status_code not in (200, 201):
        print("BACHS ERROR:", resp.status_code, resp.text)
        raise Exception(f"Bachs returned {resp.status_code}. Check terminal for details.")

    data = resp.json()
    return data["checkout_url"], data["checkout_id"]

async def notify_admin(order):
    if not ADMIN_CHAT_ID:
        return
    text = (
        f"🆕 PAID ORDER\n"
        f"ID: {order['id']}\n"
        f"Bread: {order['bread_name']} × {order['qty']}\n"
        f"Total: ₦{order['total']}\n"
        f"Deliver to: {order['location']}\n"
        f"Phone: {order['phone']}"
    )
    try:
        await bot.send_message(ADMIN_CHAT_ID, text)
    except Exception as e:
        print("admin notify failed:", e)

# ── COMMANDS ────────────────────────────────────────────
@dp.message(Command("myid"))
async def myid(m: Message):
    await m.answer(f"Your chat ID: {m.chat.id}")

@dp.message(Command("start"))
async def start(m: Message):
    sessions[m.from_user.id] = {"step": "bread"}
    await m.answer("🍞 Welcome! Pick a bread:", reply_markup=bread_kb())

@dp.callback_query(F.data.startswith("bread:"))
async def pick_bread(c: CallbackQuery):
    slug = c.data.split(":", 1)[1]
    sessions[c.from_user.id] = {"step": "qty", "bread_slug": slug}
    name = PRODUCTS[slug]["name"]
    await c.message.edit_text(f"How many {name}? Send a number 1-10.")
    await c.answer()

@dp.callback_query(F.data == "confirm")
async def confirm(c: CallbackQuery):
    s = sessions.get(c.from_user.id)
    if not s or s.get("step") != "confirm":
        await c.answer("Session expired. Send /start.", show_alert=True)
        return

    order_id = f"ORD-{datetime.now().strftime('%H%M%S')}-{c.from_user.id % 1000}"
    prod = PRODUCTS[s["bread_slug"]]
    order = {
        "id": order_id,
        "telegram_id": c.from_user.id,
        "bread_name": prod["name"],
        "bread_slug": s["bread_slug"],
        "qty": s["qty"],
        "total": prod["price"] * s["qty"],
        "location": s["location"],
        "phone": s["phone"],
    }

    try:
        checkout_url, checkout_id = create_bachs_checkout(order)
    except Exception as e:
        await c.message.edit_text(f"❌ Payment error: {e}\n\nPlease try again.")
        return

    order["checkout_id"] = checkout_id
    save_order(order)
    sessions.pop(c.from_user.id, None)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Pay with Bachs", url=checkout_url)],
        [InlineKeyboardButton(text="✅ I've paid — verify", callback_data=f"verify:{order_id}")],
    ])
    await c.message.edit_text(
        f"✅ Order {order_id}\n"
        f"{prod['name']} × {s['qty']}\n"
        f"Total: ₦{prod['price'] * s['qty']}\n\n"
        f"Tap the button below to pay on Bachs. After paying, tap 'I've paid' to confirm.",
        reply_markup=kb,
    )
    await c.answer()

@dp.callback_query(F.data.startswith("verify:"))
async def verify_payment(c: CallbackQuery):
    order_id = c.data.split(":", 1)[1]
    row = get_order(order_id)
    if not row:
        await c.answer("Order not found.", show_alert=True)
        return

    checkout_id = row[8]
    if not checkout_id:
        await c.answer("No payment session found.", show_alert=True)
        return

    try:
        url = f"https://api.bachs.io/v1/checkout-sessions/{checkout_id}"
        headers = {"Authorization": f"Bearer {BACHS_KEY}"}
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        await c.answer(f"Verification failed: {e}", show_alert=True)
        return

    if data.get("status") == "completed":
        mark_paid(order_id)
        await notify_admin({
            "id": order_id,
            "bread_name": row[2],
            "qty": row[3],
            "total": row[4],
            "location": row[5],
            "phone": row[6],
        })
        await c.message.edit_text(
            f"✅ Payment confirmed for {order_id}.\n"
            f"Your bread is being prepared for delivery to {row[5]}."
        )
    else:
        await c.answer("Payment not yet received. If you just paid, wait 10 seconds and try again.", show_alert=True)

@dp.callback_query(F.data == "cancel")
async def cancel(c: CallbackQuery):
    sessions.pop(c.from_user.id, None)
    await c.message.edit_text("Cancelled. Send /start to try again.")
    await c.answer()

@dp.message(Command("orders"))
async def list_orders(m: Message):
    if m.from_user.id != ADMIN_CHAT_ID:
        await m.answer("Not authorized.")
        return
    rows = get_paid_orders()
    if not rows:
        await m.answer("No paid orders yet.")
        return
    lines = [f"{r[0]} | {r[2]} x {r[3]} | NGN {r[4]} | {r[5]}" for r in rows]
    await m.answer("Paid orders:\n\n" + "\n".join(lines))

# ── TEXT HANDLER ────────────────────────────────────────
@dp.message(F.text & ~F.text.startswith("/"))
async def text(m: Message):
    s = sessions.get(m.from_user.id)
    if not s:
        await m.answer("Send /start to begin.")
        return

    if s["step"] == "qty":
        try:
            q = int(m.text.strip())
            if not 1 <= q <= 10:
                raise ValueError
        except ValueError:
            await m.answer("Send a number 1-10.")
            return
        s["qty"] = q
        s["step"] = "location"
        await m.answer("📍 Delivery location (e.g., Block C, Room 204):")

    elif s["step"] == "location":
        s["location"] = m.text.strip()
        s["step"] = "phone"
        await m.answer("📞 Phone number:")

    elif s["step"] == "phone":
        s["phone"] = m.text.strip()
        s["step"] = "confirm"
        prod = PRODUCTS[s["bread_slug"]]
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Confirm & Pay", callback_data="confirm"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="cancel"),
        ]])
        await m.answer(
            f"Order summary:\n"
            f"{prod['name']} x {s['qty']}\n"
            f"Total: NGN {prod['price'] * s['qty']}\n"
            f"Deliver to: {s['location']}\n"
            f"Phone: {s['phone']}",
            reply_markup=kb,
        )
    else:
        await m.answer("Send /start to begin.")

# ── MAIN ────────────────────────────────────────────────
async def main():
    init_db()
    print("Bot started. DB file: bread_orders.db")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
