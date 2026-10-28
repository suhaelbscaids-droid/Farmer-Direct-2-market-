"""
seed_demo.py - creates demonstration data: an administrator, farmers, customers,
products, orders in several statuses and a few reviews.

Run once:   python seed_demo.py          (use --reset to start from an empty database)
"""
import os
import sys
from datetime import date, timedelta

import market


def seed(db):
    if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        print("Database already has data. Run  python seed_demo.py --reset  to start fresh.")
        return
    reg = lambda *a, **k: market.register_user(db, *a, **k)
    reg("Administrator", "admin@farm.com", "9000000000", "admin123", "admin", "Coimbatore")
    ravi = reg("Ravi Kumar", "ravi@farm.com", "9876543210", "demo123", "farmer", "Pollachi", "12 Orchard Road, Pollachi", "ravikumar@oksbi")
    meena = reg("Meena Devi", "meena@farm.com", "9876500011", "demo123", "farmer", "Ooty", "Hill View Farm, Ooty", "meenadevi@okhdfcbank")
    selvam = reg("Selvam Pillai", "selvam@farm.com", "9876500022", "demo123", "farmer", "Thanjavur", "Delta Fields, Thanjavur", "selvampillai@okicici")
    anitha = reg("Anitha Raj", "anitha@mail.com", "9123456780", "demo123", "customer", "Coimbatore",
                 "45 Avinashi Road, Peelamedu, Coimbatore - 641 004")
    karthik = reg("Karthik S", "karthik@mail.com", "9123400099", "demo123", "customer", "Coimbatore",
                  "8 Race Course, Coimbatore - 641 018")

    today = date.today()
    d = lambda n: (today - timedelta(days=n)).isoformat()
    P = {}
    add = lambda key, *a: P.__setitem__(key, market.add_product(db, *a))
    add("tomato", ravi, "Organic Tomatoes", "Vegetable", 52, 100, "Juicy red tomatoes grown without pesticides.", d(1))
    add("brinjal", ravi, "Purple Brinjal", "Vegetable", 38, 60, "Tender farm-fresh brinjal, perfect for sambar.", d(1))
    add("carrot", ravi, "Sweet Carrots", "Vegetable", 45, 8, "Crunchy, sweet carrots rich in vitamin A.", d(2))
    add("mango", ravi, "Banganapalli Mangoes", "Fruit", 120, 20, "Sun-ripened mangoes, naturally sweet.", d(2))
    add("banana", ravi, "Nendran Bananas", "Fruit", 60, 45, "Farm-fresh bananas, sold per kg.", d(3))
    add("onion", meena, "Hill Onions", "Vegetable", 40, 150, "Strong flavour, long shelf life.", d(4))
    add("cabbage", meena, "Ooty Cabbage", "Vegetable", 30, 70, "Crisp green cabbage from the Nilgiri hills.", d(2))
    add("strawberry", meena, "Fresh Strawberries", "Fruit", 180, 25, "Sweet hill strawberries, picked at dawn.", d(1))
    add("milk", meena, "Farm Fresh Milk", "Dairy", 58, 40, "Pure cow milk, delivered the same day.", d(0))
    add("honey", meena, "Forest Honey", "Other", 320, 15, "Raw unprocessed honey from the Nilgiris.", d(10))
    add("rice", selvam, "Ponni Rice", "Grain", 62, 200, "Delta-grown ponni rice, per kg.", d(15))
    add("ragi", selvam, "Ragi (Finger Millet)", "Grain", 48, 90, "Nutritious whole ragi, per kg.", d(12))
    add("coconut", selvam, "Tender Coconuts", "Fruit", 35, 80, "Naturally sweet coconut water.", d(1))
    add("groundnut", selvam, "Groundnuts", "Other", 95, 6, "Fresh sun-dried groundnuts, per kg.", d(6))

    # o1: UPI order, paid and verified, delivered
    o1 = market.place_order(db, anitha, [(P["tomato"], 5)], "UPI", "45 Avinashi Road, Coimbatore")
    market.submit_upi_payment(db, anitha, o1, "412345678901")
    market.confirm_payment(db, ravi, o1)
    for s in ("Confirmed", "Packed", "Delivered"):
        market.change_status(db, ravi, o1, s)
    # o2: Cash on Delivery, delivered (paid on delivery)
    o2 = market.place_order(db, karthik, [(P["mango"], 2), (P["banana"], 3)], "Cash on Delivery", "8 Race Course, Coimbatore")
    for s in ("Confirmed", "Packed", "Delivered"):
        market.change_status(db, ravi, o2, s)
    # o3: Cash on Delivery, just placed
    market.place_order(db, anitha, [(P["brinjal"], 3), (P["carrot"], 2)], "Cash on Delivery", "45 Avinashi Road, Coimbatore")
    # o4: UPI paid + confirmed
    o4 = market.place_order(db, karthik, [(P["tomato"], 2)], "UPI", "8 Race Course, Coimbatore")
    market.submit_upi_payment(db, karthik, o4, "412345678902")
    market.change_status(db, ravi, o4, "Confirmed")
    # o5: UPI order that the customer cancelled before paying
    o5 = market.place_order(db, anitha, [(P["milk"], 4), (P["honey"], 1)], "UPI", "45 Avinashi Road, Coimbatore")
    market.customer_cancel(db, anitha, o5)
    # o6: UPI order waiting for payment -> log in as karthik and open "Pay now" to see the QR code
    market.place_order(db, karthik, [(P["ragi"], 3)], "UPI", "8 Race Course, Coimbatore")
    market.add_review(db, karthik, P["mango"], 5, "Super sweet and fresh! Tasted like the farm.")
    market.add_review(db, karthik, P["banana"], 4, "Good quality bananas.")
    market.add_review(db, anitha, P["tomato"], 5, "Best tomatoes I have bought in months.")
    print("Demo data created.")
    print("  Admin    : admin@farm.com   / admin123")
    print("  Farmers  : ravi@farm.com, meena@farm.com, selvam@farm.com   / demo123")
    print("  Customers: anitha@mail.com, karthik@mail.com                / demo123")


if __name__ == "__main__":
    path = os.environ.get("DATABASE") or market.DEFAULT_DB
    if "--reset" in sys.argv and os.path.exists(path):
        os.remove(path)
    conn = market.connect(path)
    market.init_db(conn)
    seed(conn)
    conn.close()
