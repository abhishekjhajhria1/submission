"""Seed data for Patil Farms (Ozar, Nashik) - the sandbox business FarmHand works for.

The data is deliberately a little messy, like real life:
  - mandi prices are quoted per quintal (100 kg), orders are priced per kg
  - FreshMart asks for more grapes than are in stock (needs the farmer's call)
  - Sai Traders offers a price below the farmer's floor (needs approval)
  - an older Annapurna message and a group chat are distractors
  - receipts are phone scans with meaningless file names
"""

TODAY = "2026-10-13"  # Tuesday. Fixed so tasks like "this Friday" are reproducible.

CONTACTS = [
    # id, name, person, type, phone
    (1, "Hotel Annapurna", "Ramesh Kulkarni", "hotel", "+91 98220 11223"),
    (2, "FreshMart Retail", "Neha Shah", "retailer", "+91 99700 44556"),
    (3, "Sai Traders", "Vijay Pawar", "trader", "+91 97640 77889"),
    (4, "Sharma Agro Kendra", "Mahesh Sharma", "supplier", "+91 98220 41177"),
    (5, "Nashik Grape Growers Group", "Group chat", "group", ""),
]

STOCK = [
    # product, variety, available kg, grade, location
    ("tomato", "Hybrid red", 350.0, "A", "Cold room 1"),
    ("onion", "Red onion (Nashik)", 1200.0, "A", "Onion chawl"),
    ("grapes", "Thompson Seedless", 80.0, "A", "Cold room 2"),
    ("green chilli", "G4", 40.0, "A", "Packing shed"),
]

# Nashik APMC, Rs per quintal (100 kg) - the way Indian mandis actually quote.
MARKET_PRICES = [
    # product, mandi, min, max, modal
    ("tomato", "Nashik APMC", 1800, 2900, 2400),
    ("onion", "Nashik APMC", 1500, 2100, 1800),
    ("grapes", "Nashik APMC", 5200, 7000, 6200),
    ("green chilli", "Nashik APMC", 3000, 4000, 3500),
    ("onion", "Lasalgaon APMC", 1550, 2150, 1850),
]

MESSAGES = [
    # contact id, direction, text, timestamp
    (1, "in", "Thank you Sureshji, last week's tomatoes were very good quality.", "2026-10-07 19:20"),
    (5, "in", "Reminder: grape growers meeting on Sunday 10am at the society office.", "2026-10-11 07:45"),
    (4, "in", "Sureshji, bill for the urea and DAP is in your WhatsApp. Please pay by month end. - Mahesh",
     "2026-10-11 12:30"),
    (3, "in", "Bhau, 1000 kilo kanda chahiye, Rs 14 per kilo final rate. Kal subah pickup karenge. - Vijay",
     "2026-10-12 18:05"),
    (1, "in", "Namaste Sureshji, we need 200 kg tomatoes (grade A) for this Friday. Please confirm the rate "
              "and the order. - Ramesh, Hotel Annapurna", "2026-10-13 08:12"),
    (2, "in", "Hi Suresh, can you supply 300 kg red onions and 100 kg grapes by Thursday? It's for our "
              "weekend sale. Thanks, Neha (FreshMart)", "2026-10-13 09:40"),
]

ORDERS = [
    # buyer id, items [(product, kg, price/kg)], delivery date, status, created
    (1, [("tomato", 150.0, 26.0)], "2026-10-06", "delivered", "2026-10-03 10:00"),
]

# Farm inputs on hand (not for sale). daily_use is the owner's typical consumption in the season.
SUPPLIES = [
    # item, quantity, unit, location, daily_use, last_restocked
    ("diesel", 26.0, "litres", "Tractor tank + spare drum", 3.5, "2026-10-09"),
    ("urea", 9.0, "bags (45 kg)", "Input store", 0.0, "2026-10-11"),
    ("DAP", 1.0, "bags (50 kg)", "Input store", 0.0, "2026-10-11"),
    ("crop spray (Emamectin)", 0.6, "litres", "Input store", 0.0, "2026-09-28"),
    ("plastic crates", 140.0, "crates", "Packing shed", 0.0, "2026-08-15"),
]

EXPENSES = [
    # date, vendor, category, description, amount, payment mode, reference
    ("2026-10-05", "Labour - harvest crew", "labour", "Tomato picking, 6 workers x 1 day", 2400.0, "cash", "LAB-1005"),
    ("2026-10-09", "Shree Sai Petroleum (HP)", "fuel", "Diesel 40 L for tractor", 3768.0, "cash", "HP-88213"),
]

# Phone scans in the farmer's documents folder (rendered to PDF by make_files.py).
RECEIPTS = {
    "IMG_20261009_scan.pdf": [
        ("B", 16, "SHREE SAI PETROLEUM (HP)"),
        ("", 10, "NH-3, Ozar, Dist. Nashik"),
        ("", 10, "-------------------------------------------"),
        ("", 10, "Bill No: HP-88213          Date: 09/10/2026"),
        ("", 10, "Product: High Speed Diesel"),
        ("", 10, "Qty: 40.00 L   Rate: 94.20   Amount: 3,768.00"),
        ("", 10, "-------------------------------------------"),
        ("B", 12, "TOTAL  Rs. 3,768.00      Mode: CASH"),
    ],
    "IMG_20261011_scan.pdf": [
        ("B", 16, "SHARMA AGRO KENDRA"),
        ("", 10, "Fertilizers | Seeds | Pesticides"),
        ("", 10, "Main Road, Pimpalgaon Baswant, Dist. Nashik 422209  Ph: 98220 41177"),
        ("", 10, "----------------------------------------------------------"),
        ("B", 11, "CASH / CREDIT MEMO"),
        ("", 10, "Bill No: SAK/1047                      Date: 11/10/2026"),
        ("", 10, "Customer: Suresh Patil, Patil Farms, Ozar"),
        ("", 10, "----------------------------------------------------------"),
        ("", 10, "Item                     Qty    Rate        Amount"),
        ("", 10, "Urea (45 kg bag)         10     266.50      2,665.00"),
        ("", 10, "DAP (50 kg bag)           1     1,350.00    1,350.00"),
        ("", 10, "----------------------------------------------------------"),
        ("B", 12, "TOTAL                                  Rs. 4,015.00"),
        ("", 10, "Payment: UPI  (UPI Ref 6281 3390 1124)"),
        ("", 10, "Thank you! Visit again."),
    ],
}
