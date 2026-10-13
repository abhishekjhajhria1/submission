"""Patil Farms "farm office": the sandbox business system FarmHand operates.

A real HTTP/JSON API with validation, business rules and state, plus a small
live-updating HTML page so a human can watch what the agent changes.
Nothing in here knows about the agent.

Chaos mode (level 0..1) injects the failures real systems produce:
  - random 503s and slow responses on any API call
  - writes that fail BEFORE commit (nothing saved) or AFTER commit (saved, but the
    client sees a 504) - the classic duplicate-order trap
POST endpoints honour an Idempotency-Key header, like payment APIs do.
/__admin/* is for the eval harness only; it is hidden from the API schema.
"""
from __future__ import annotations

import asyncio
import html
import random
import threading
from datetime import date, datetime
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from . import seed
from .make_files import ensure_files

app = FastAPI(
    title="Patil Farms - Farm Office API",
    description="Stock, mandi prices, buyer messages, sales orders/invoices and expenses for Patil Farms, Ozar (Nashik).",
    generate_unique_id_function=lambda route: route.name,
)


# --------------------------------------------------------------------------- state

class State:
    lock = threading.RLock()

    def reset(self, chaos: float = 0.0, chaos_seed: int = 0) -> None:
        with self.lock:
            self.contacts = {c[0]: dict(zip(("id", "name", "person", "type", "phone"), c)) for c in seed.CONTACTS}
            self.stock = {
                p: {"product": p, "variety": v, "available_kg": kg, "reserved_kg": 0.0, "grade": g, "location": loc}
                for p, v, kg, g, loc in seed.STOCK
            }
            self.messages = [
                {"id": i, "contact_id": c, "direction": d, "text": t, "timestamp": ts}
                for i, (c, d, t, ts) in enumerate(seed.MESSAGES, start=1)
            ]
            self.orders = []
            for buyer, items, delivery, status, created in seed.ORDERS:
                self._add_order(buyer, [{"product": p, "qty_kg": q, "price_per_kg": r} for p, q, r in items],
                                delivery, "", status=status, created=created)
            self.expenses = [
                dict(zip(("id", "date", "vendor", "category", "description", "amount", "payment_mode", "reference"),
                         (i, *e)))
                for i, e in enumerate(seed.EXPENSES, start=1)
            ]
            self.idempotency: dict[str, tuple[int, dict]] = {}
            self.chaos = chaos
            self.rng = random.Random(chaos_seed)
            self.chaos_log: list[dict] = []

    def _add_order(self, buyer_id, items, delivery_date, notes, status="confirmed", created=None) -> dict:
        lines = [{**i, "amount": round(i["qty_kg"] * i["price_per_kg"], 2)} for i in items]
        order_id = len(self.orders) + 1
        order = {
            "id": order_id,
            "buyer_id": buyer_id,
            "buyer_name": self.contacts[buyer_id]["name"],
            "items": lines,
            "total": round(sum(line["amount"] for line in lines), 2),
            "delivery_date": delivery_date,
            "status": status,
            "invoice_number": f"PF-INV-{2040 + order_id}",
            "created_at": created or _now(),
            "notes": notes,
        }
        self.orders.append(order)
        return order


S = State()
S.reset()
ensure_files()


def _now() -> str:
    return f"{seed.TODAY} {datetime.now():%H:%M}"


def _valid_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- chaos

def _roll(weight: float) -> bool:
    return S.chaos > 0 and S.rng.random() < min(1.0, S.chaos * weight)


def _log_fault(request: Request, fault: str) -> None:
    S.chaos_log.append({"time": datetime.now().strftime("%H:%M:%S"), "method": request.method,
                        "path": request.url.path, "fault": fault})


async def chaos_gate(request: Request) -> None:
    """Applied to every API route: slow responses and transient 503s."""
    if _roll(0.3):
        _log_fault(request, "slow response")
        await asyncio.sleep(S.rng.uniform(0.8, 2.0))
    if _roll(0.2):
        _log_fault(request, "503 unavailable")
        raise HTTPException(503, "Service temporarily unavailable. Please retry.")


def _write_fault(request: Request) -> str | None:
    """For writes: 'before' = fail without saving, 'after' = save, then fail the response."""
    if _roll(0.35):
        fault = "before" if S.rng.random() < 0.5 else "after"
        _log_fault(request, "500 before commit" if fault == "before" else "504 after commit (write was saved)")
        return fault
    return None


def _replay(key: str | None) -> JSONResponse | None:
    """Return the stored response if this Idempotency-Key was already processed (checked before validation)."""
    if key and key in S.idempotency:
        status, body = S.idempotency[key]
        return JSONResponse(body, status_code=status, headers={"Idempotent-Replay": "true"})
    return None


def _idempotent_write(request: Request, key: str | None, do_write) -> JSONResponse:
    with S.lock:
        fault = _write_fault(request)
        if fault == "before":
            raise HTTPException(500, "Internal server error")
        body = do_write()
        if key:
            S.idempotency[key] = (201, body)
        if fault == "after":
            raise HTTPException(504, "Gateway timeout")
        return JSONResponse(body, status_code=201)


api = [Depends(chaos_gate)]


# --------------------------------------------------------------------------- models

class OrderItem(BaseModel):
    product: str = Field(description="Product name exactly as listed by get_stock, e.g. 'tomato'")
    qty_kg: float = Field(gt=0, description="Quantity in kg")
    price_per_kg: float = Field(gt=0, description="Selling price in Rs per kg")


class OrderIn(BaseModel):
    buyer_id: int = Field(description="Contact id of the buyer (see list_contacts)")
    items: list[OrderItem] = Field(min_length=1)
    delivery_date: str = Field(description="YYYY-MM-DD; today or later")
    notes: str = ""


class MessageIn(BaseModel):
    contact_id: int = Field(description="Contact id to send the WhatsApp message to")
    text: str = Field(min_length=1)


class ExpenseIn(BaseModel):
    date: str = Field(description="Bill date, YYYY-MM-DD")
    vendor: str = Field(description="Shop / vendor name as printed on the bill")
    category: Literal["seeds", "fertilizer", "pesticide", "labour", "fuel", "transport", "equipment",
                      "utilities", "other"]
    description: str = Field(description="What was bought")
    amount: float = Field(gt=0, description="Bill total in Rs")
    payment_mode: Literal["cash", "upi", "bank", "credit"]
    reference: str = Field(description="Bill / receipt number as printed")


# --------------------------------------------------------------------------- API

@app.get("/contacts", dependencies=api)
def list_contacts(type: str | None = None):
    """List buyers, suppliers and groups. Optional filter by type (hotel, retailer, trader, supplier, group)."""
    return [c for c in S.contacts.values() if not type or c["type"] == type]


@app.get("/messages", dependencies=api)
def list_messages(contact_id: int | None = None, direction: Literal["in", "out"] | None = None, limit: int = 20):
    """WhatsApp messages, newest first. Optional filters: contact_id, direction ('in' received / 'out' sent)."""
    rows = [m for m in reversed(S.messages)
            if (contact_id is None or m["contact_id"] == contact_id) and (not direction or m["direction"] == direction)]
    return [{**m, "contact_name": S.contacts[m["contact_id"]]["name"]} for m in rows[:limit]]


@app.post("/messages", dependencies=api, status_code=201)
def send_message(body: MessageIn, request: Request, idempotency_key: str | None = Header(default=None)):
    """Send a WhatsApp message to a contact (e.g. confirm an order to a buyer)."""
    if replay := _replay(idempotency_key):
        return replay
    if body.contact_id not in S.contacts:
        raise HTTPException(404, f"No contact with id {body.contact_id}")

    def write():
        msg = {"id": len(S.messages) + 1, "contact_id": body.contact_id, "direction": "out",
               "text": body.text, "timestamp": _now()}
        S.messages.append(msg)
        return msg
    return _idempotent_write(request, idempotency_key, write)


@app.get("/stock", dependencies=api)
def get_stock():
    """Produce currently in storage. available_kg can be sold; reserved_kg is held for confirmed orders."""
    return list(S.stock.values())


@app.get("/market-prices", dependencies=api)
def get_market_prices(product: str | None = None):
    """Today's mandi (APMC) prices. NOTE: prices are in Rs per QUINTAL (100 kg)."""
    rows = [dict(zip(("product", "mandi", "min_price", "max_price", "modal_price"), p)) for p in seed.MARKET_PRICES]
    if product:
        rows = [r for r in rows if r["product"] == product.lower().strip()]
    return {"date": seed.TODAY, "unit": "Rs per quintal (100 kg)", "prices": rows}


@app.get("/orders", dependencies=api)
def list_orders(buyer_id: int | None = None, status: str | None = None):
    """Sales orders with their invoice, newest first. Optional filters: buyer_id, status."""
    return [o for o in reversed(S.orders)
            if (buyer_id is None or o["buyer_id"] == buyer_id) and (not status or o["status"] == status)]


@app.get("/orders/{order_id}", dependencies=api)
def get_order(order_id: int):
    """One sales order with its invoice."""
    if not 1 <= order_id <= len(S.orders):
        raise HTTPException(404, f"No order with id {order_id}")
    return S.orders[order_id - 1]


@app.post("/orders", dependencies=api, status_code=201)
def create_order(body: OrderIn, request: Request, idempotency_key: str | None = Header(default=None)):
    """Create a confirmed sales order. Reserves the stock and generates the invoice (fresh produce: no GST)."""
    if replay := _replay(idempotency_key):
        return replay
    buyer = S.contacts.get(body.buyer_id)
    if not buyer or buyer["type"] not in ("hotel", "retailer", "trader"):
        raise HTTPException(422, f"Contact {body.buyer_id} is not a buyer")
    due = _valid_date(body.delivery_date)
    if not due:
        raise HTTPException(422, "delivery_date must be YYYY-MM-DD")
    if due < date.fromisoformat(seed.TODAY):
        raise HTTPException(422, f"delivery_date {body.delivery_date} is in the past (today is {seed.TODAY})")
    products = [i.product for i in body.items]
    if len(set(products)) != len(products):
        raise HTTPException(422, "Each product may appear only once per order")
    for item in body.items:
        if item.product not in S.stock:
            raise HTTPException(422, f"Unknown product '{item.product}'. Known products: {', '.join(S.stock)}")
        available = S.stock[item.product]["available_kg"]
        if item.qty_kg > available:
            raise HTTPException(409, f"Insufficient stock for {item.product}: requested {item.qty_kg:g} kg, "
                                     f"available {available:g} kg")

    def write():
        for item in body.items:
            S.stock[item.product]["available_kg"] -= item.qty_kg
            S.stock[item.product]["reserved_kg"] += item.qty_kg
        return S._add_order(body.buyer_id, [i.model_dump() for i in body.items], body.delivery_date, body.notes)
    return _idempotent_write(request, idempotency_key, write)


@app.post("/orders/{order_id}/cancel", dependencies=api)
def cancel_order(order_id: int):
    """Cancel a confirmed order and return its stock to available."""
    order = get_order(order_id)
    if order["status"] != "confirmed":
        raise HTTPException(409, f"Order {order_id} is {order['status']}, only confirmed orders can be cancelled")
    with S.lock:
        for line in order["items"]:
            S.stock[line["product"]]["available_kg"] += line["qty_kg"]
            S.stock[line["product"]]["reserved_kg"] -= line["qty_kg"]
        order["status"] = "cancelled"
    return order


@app.get("/expenses", dependencies=api)
def list_expenses():
    """Farm expense book, newest first."""
    return list(reversed(S.expenses))


@app.post("/expenses", dependencies=api, status_code=201)
def create_expense(body: ExpenseIn, request: Request, idempotency_key: str | None = Header(default=None)):
    """Record a farm expense from a bill/receipt."""
    if replay := _replay(idempotency_key):
        return replay
    when = _valid_date(body.date)
    if not when:
        raise HTTPException(422, "date must be YYYY-MM-DD")
    if when > date.fromisoformat(seed.TODAY):
        raise HTTPException(422, f"date {body.date} is in the future (today is {seed.TODAY})")
    for e in S.expenses:
        if e["reference"].strip().lower() == body.reference.strip().lower() and \
                e["vendor"].strip().lower() == body.vendor.strip().lower():
            raise HTTPException(409, f"Expense {body.reference} from {body.vendor} is already recorded (id {e['id']})")

    def write():
        expense = {"id": len(S.expenses) + 1, **body.model_dump()}
        S.expenses.append(expense)
        return expense
    return _idempotent_write(request, idempotency_key, write)


# --------------------------------------------------------------------------- admin (eval harness only)

class ResetIn(BaseModel):
    chaos: float = 0.0
    seed: int = 0


@app.post("/__admin/reset", include_in_schema=False)
def admin_reset(body: ResetIn):
    S.reset(body.chaos, body.seed)
    return {"ok": True, "chaos": S.chaos}


@app.get("/__admin/state", include_in_schema=False)
def admin_state():
    return {"stock": list(S.stock.values()), "orders": S.orders, "messages": S.messages,
            "expenses": S.expenses, "contacts": list(S.contacts.values()), "today": seed.TODAY,
            "chaos": S.chaos, "chaos_log": S.chaos_log[-50:]}


# --------------------------------------------------------------------------- human view

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def office_view():
    esc = html.escape

    def table(headers, rows):
        head = "".join(f"<th>{h}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{esc(str(c))}</td>" for c in r) + "</tr>" for r in rows)
        return f"<table><tr>{head}</tr>{body}</table>"

    stock = table(["Product", "Available kg", "Reserved kg", "Location"],
                  [(s["product"], f"{s['available_kg']:g}", f"{s['reserved_kg']:g}", s["location"])
                   for s in S.stock.values()])
    orders = table(["#", "Buyer", "Items", "Total Rs", "Delivery", "Status", "Invoice"],
                   [(o["id"], o["buyer_name"],
                     ", ".join(f"{i['product']} {i['qty_kg']:g}kg @{i['price_per_kg']:g}" for i in o["items"]),
                     f"{o['total']:,.2f}", o["delivery_date"], o["status"], o["invoice_number"])
                    for o in reversed(S.orders)])
    msgs = table(["Time", "Contact", "", "Message"],
                 [(m["timestamp"], S.contacts[m["contact_id"]]["name"], "→" if m["direction"] == "out" else "←",
                   m["text"]) for m in list(reversed(S.messages))[:8]])
    expenses = table(["Date", "Vendor", "Category", "Amount Rs", "Ref"],
                     [(e["date"], e["vendor"], e["category"], f"{e['amount']:,.2f}", e["reference"])
                      for e in reversed(S.expenses)])
    chaos = f"<span class='chaos'>chaos {S.chaos:.0%}</span>" if S.chaos else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Patil Farms Office</title><style>
body{{font:14px system-ui,sans-serif;margin:0;background:#f6f5ef;color:#1f2a1f}}
header{{background:#2f5d34;color:#fff;padding:12px 20px;font-size:18px;font-weight:600}}
.chaos{{background:#c0392b;border-radius:10px;padding:2px 8px;font-size:12px;margin-left:10px}}
main{{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px}}
section{{background:#fff;border-radius:8px;padding:12px;box-shadow:0 1px 3px #0001}}
h2{{font-size:14px;margin:0 0 8px;color:#2f5d34}} table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:4px 6px;border-bottom:1px solid #eee;vertical-align:top}} th{{color:#666;font-weight:500}}
.flash td{{animation:flash 2.5s ease-out}} @keyframes flash{{0%{{background:#fff1a8}}100%{{background:transparent}}}}
</style></head><body><header>Patil Farms · Farm Office {chaos}</header><main>
<section><h2>Stock</h2>{stock}</section><section><h2>Sales orders</h2>{orders}</section>
<section><h2>WhatsApp (latest)</h2>{msgs}</section><section><h2>Expenses</h2>{expenses}</section>
</main>
<script>
// Live update without reloading: fetch this same page, swap in only what changed, highlight new rows.
setInterval(async () => {{
  try {{
    const doc = new DOMParser().parseFromString(await (await fetch(location.href, {{cache: "no-store"}})).text(), "text/html");
    for (const sel of ["header", "main"]) {{
      const cur = document.querySelector(sel), next = doc.querySelector(sel);
      if (!next || cur.innerHTML === next.innerHTML) continue;
      const before = new Set([...cur.querySelectorAll("tr")].map((r) => r.textContent));
      cur.innerHTML = next.innerHTML;
      cur.querySelectorAll("tr").forEach((r) => {{ if (before.size && !before.has(r.textContent)) r.classList.add("flash"); }});
    }}
  }} catch (e) {{ /* server restarting: try again next tick */ }}
}}, 1500);
</script></body></html>"""
