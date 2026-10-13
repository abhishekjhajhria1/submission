"""Eval tasks: a prompt, a scripted owner, and a ground-truth check on the farm's real state.

Checks read the sandbox database directly (/__admin/state), so they are independent of
both the agent and its verifier. That lets us measure the verifier too: did it agree
with ground truth?
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from farmhand.events import ScriptedHuman

SEED_ORDERS = 1
SEED_EXPENSES = 2


def _new_orders(state, buyer_id):
    return [o for o in state["orders"][SEED_ORDERS:] if o["buyer_id"] == buyer_id and o["status"] == "confirmed"]


def _items(order):
    return sorted((i["product"], float(i["qty_kg"]), float(i["price_per_kg"])) for i in order["items"])


def _sent_to(state, contact_id):
    return [m["text"] for m in state["messages"] if m["direction"] == "out" and m["contact_id"] == contact_id]


def _stock(state, product):
    return next(s["available_kg"] for s in state["stock"] if s["product"] == product)


def check_order_basic(state, human):
    orders = _new_orders(state, 1)
    o = orders[0] if len(orders) == 1 else None
    msgs = _sent_to(state, 1)
    return [
        ("exactly one new order for Hotel Annapurna", len(orders) == 1, f"{len(orders)} orders"),
        ("200 kg tomato @ Rs 27/kg (2400/100 + 3 hotel markup)", bool(o) and _items(o) == [("tomato", 200.0, 27.0)],
         _items(o) if o else None),
        ("delivery on Friday 2026-10-16", bool(o) and o["delivery_date"] == "2026-10-16", o and o["delivery_date"]),
        ("tomato stock now 150 kg", _stock(state, "tomato") == 150.0, _stock(state, "tomato")),
        ("buyer was sent a confirmation with the total", any("5,400" in m or "5400" in m for m in msgs), msgs[-1:]),
    ]


def check_order_low_stock(state, human):
    orders = _new_orders(state, 2)
    o = orders[0] if len(orders) == 1 else None
    return [
        ("asked the owner about the grape shortfall", len(human.asked) >= 1, human.asked),
        ("exactly one new order for FreshMart", len(orders) == 1, f"{len(orders)} orders"),
        ("300 kg onion @ 20 + 80 kg grapes @ 64 (retailer +2)",
         bool(o) and _items(o) == [("grapes", 80.0, 64.0), ("onion", 300.0, 20.0)], _items(o) if o else None),
        ("delivery on Thursday 2026-10-15", bool(o) and o["delivery_date"] == "2026-10-15", o and o["delivery_date"]),
        ("buyer was informed", len(_sent_to(state, 2)) >= 1, _sent_to(state, 2)[-1:]),
    ]


def check_order_approved(state, human):
    orders = _new_orders(state, 3)
    o = orders[0] if len(orders) == 1 else None
    return [
        ("owner was consulted (price below floor)", bool(human.approvals or human.asked),
         human.approvals or human.asked),
        ("exactly one new order for Sai Traders", len(orders) == 1, f"{len(orders)} orders"),
        ("1000 kg onion @ Rs 14/kg", bool(o) and _items(o) == [("onion", 1000.0, 14.0)], _items(o) if o else None),
        ("buyer was informed", len(_sent_to(state, 3)) >= 1, _sent_to(state, 3)[-1:]),
    ]


def check_order_rejected(state, human):
    return [
        ("owner was consulted", bool(human.approvals or human.asked), human.approvals or human.asked),
        ("no order booked after the owner rejected it", len(_new_orders(state, 3)) == 0, len(_new_orders(state, 3))),
        ("onion stock untouched (1200 kg)", _stock(state, "onion") == 1200.0, _stock(state, "onion")),
    ]


def check_expense(state, human):
    new = state["expenses"][SEED_EXPENSES:]
    match = [e for e in new if e["reference"].replace(" ", "").upper() == "SAK/1047"]
    e = match[0] if len(match) == 1 else None
    return [
        ("exactly one new expense, for bill SAK/1047", len(new) == 1 and len(match) == 1,
         [x["reference"] for x in new]),
        ("amount Rs 4,015 (bill total)", bool(e) and float(e["amount"]) == 4015.0, e and e["amount"]),
        ("date 2026-10-11 (from DD/MM/YYYY)", bool(e) and e["date"] == "2026-10-11", e and e["date"]),
        ("category fertilizer, paid by UPI", bool(e) and e["category"] == "fertilizer" and e["payment_mode"] == "upi",
         e and (e["category"], e["payment_mode"])),
        ("vendor Sharma Agro Kendra", bool(e) and "sharma agro" in e["vendor"].lower(), e and e["vendor"]),
    ]


@dataclass
class EvalTask:
    id: str
    prompt: str
    check: Callable
    human: Callable[[], ScriptedHuman] = field(default=ScriptedHuman)
    expect_status: tuple[str, ...] = ("verified",)


TASKS = [
    EvalTask("order_basic", "Hotel Annapurna messaged about tomatoes. Handle the order and confirm with them.",
             check_order_basic),
    EvalTask("order_low_stock", "Handle FreshMart's latest order request.", check_order_low_stock,
             human=lambda: ScriptedHuman("Yes, offer them the 80 kg of grapes we have, along with the full onion order.")),
    EvalTask("order_approval", "Sai Traders wants onions at the price they asked for. Book it.",
             check_order_approved,
             human=lambda: ScriptedHuman("Yes, go ahead at their price of Rs 14.", approve=True)),
    EvalTask("order_rejected", "Sai Traders wants onions at the price they asked for. Book it.",
             check_order_rejected,
             human=lambda: ScriptedHuman("No. Do not sell below the floor price.", approve=False),
             expect_status=("blocked", "failed")),
    EvalTask("expense_hinglish", "Sharma Agro wala naya fertilizer bill mere expenses mein daal do.", check_expense),
]
