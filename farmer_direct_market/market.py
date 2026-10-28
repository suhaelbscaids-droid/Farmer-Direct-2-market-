"""
market.py - Business logic layer of the Farmer Direct Market Platform.

All marketplace rules live here (validation, password hashing, stock control,
order life cycle, reviews, statistics) so they can be tested without a browser.
"""
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date
from urllib.parse import quote

from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(BASE_DIR, "farmer_market.db")

CATEGORIES = ["Vegetable", "Fruit", "Grain", "Other"]
PAYMENTS = ["UPI", "Cash on Delivery"]
STATUSES = ["Placed", "Confirmed", "Packed", "Delivered", "Cancelled"]
TRANSITIONS = {
    "Placed": ["Confirmed", "Cancelled"],
    "Confirmed": ["Packed", "Cancelled"],
    "Packed": ["Delivered"],
    "Delivered": [],
    "Cancelled": [],
}
LOW_STOCK_LIMIT = 10


class MarketError(ValueError):
    """Raised when a business rule is violated; the message is shown to the user."""


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------
def now():
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def connect(path=None):
    db = sqlite3.connect(path or DEFAULT_DB, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


# Columns added after the first release: older database files are upgraded automatically.
_MIGRATIONS = [
    ("users", "upi_id", "TEXT DEFAULT ''"),
    ("users", "is_active", "INTEGER NOT NULL DEFAULT 1"),
    ("orders", "payment_status", "TEXT NOT NULL DEFAULT 'Pending'"),
    ("orders", "payment_ref", "TEXT NOT NULL DEFAULT ''"),
]


def init_db(db):
    """Create the five tables from schema.sql if they do not exist (and upgrade old files)."""
    with open(os.path.join(BASE_DIR, "schema.sql"), encoding="utf-8") as f:
        db.executescript(f.read())
    for table, column, ddl in _MIGRATIONS:
        if column not in {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            if column == "payment_status":  # give old orders a sensible payment status
                db.execute("UPDATE orders SET payment_status = 'Paid' WHERE status = 'Delivered'")
                db.execute("UPDATE orders SET payment_status = 'COD' "
                           "WHERE payment_method = 'Cash on Delivery' AND status != 'Delivered'")


@contextmanager
def transaction(db):
    """Everything inside is saved together, or nothing is saved."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


# --------------------------------------------------------------------------
# 1. User authentication module
# --------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
UPI_RE = re.compile(r"^[A-Za-z0-9._\-]{2,64}@[A-Za-z][A-Za-z0-9]{1,63}$")


def _clean_upi(upi_id):
    upi_id = (upi_id or "").strip()
    if upi_id and not UPI_RE.match(upi_id):
        raise MarketError("Enter a valid UPI ID, for example name@oksbi.")
    return upi_id


def _check_phone(db, phone, own_id=None):
    if not re.fullmatch(r"\d{10}", phone):
        raise MarketError("Phone number must contain exactly 10 digits.")
    row = db.execute("SELECT user_id FROM users WHERE phone = ?", (phone,)).fetchone()
    if row and row["user_id"] != own_id:
        raise MarketError("This phone number is already registered.")


def register_user(db, name, email, phone, password, role, location="", address="", upi_id=""):
    name = (name or "").strip()
    email = (email or "").strip().lower()
    phone = (phone or "").strip()
    if not name:
        raise MarketError("Name is required.")
    if not EMAIL_RE.match(email):
        raise MarketError("Enter a valid e-mail address.")
    _check_phone(db, phone)
    if len(password or "") < 6:
        raise MarketError("Password must have at least 6 characters.")
    if role not in ("farmer", "customer", "admin"):
        raise MarketError("Role must be farmer or customer.")
    upi_id = _clean_upi(upi_id) if role == "farmer" else ""
    if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        raise MarketError("This e-mail address is already registered.")
    cur = db.execute(
        "INSERT INTO users (name,email,phone,password_hash,role,location,address,upi_id,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (name, email, phone, generate_password_hash(password), role,
         (location or "").strip(), (address or "").strip(), upi_id, now()),
    )
    return cur.lastrowid


def authenticate(db, email, password):
    """Return the user row when the credentials are correct, otherwise None."""
    user = db.execute("SELECT * FROM users WHERE email = ?",
                      ((email or "").strip().lower(),)).fetchone()
    if user and check_password_hash(user["password_hash"], password or ""):
        if not user["is_active"]:
            raise MarketError("This account has been deactivated. Please contact the administrator.")
        return user
    return None


def get_user(db, user_id):
    return db.execute("SELECT * FROM users WHERE user_id = ? AND is_active = 1", (user_id,)).fetchone()


def update_profile(db, user_id, name, phone, location, address, upi_id=""):
    user = db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not user:
        raise MarketError("User not found.")
    name, phone = (name or "").strip(), (phone or "").strip()
    if not name:
        raise MarketError("Name is required.")
    _check_phone(db, phone, own_id=user_id)
    upi_id = _clean_upi(upi_id) if user["role"] == "farmer" else ""
    db.execute("UPDATE users SET name=?, phone=?, location=?, address=?, upi_id=? WHERE user_id=?",
               (name, phone, (location or "").strip(), (address or "").strip(), upi_id, user_id))


def change_password(db, user_id, current, new):
    user = db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], current or ""):
        raise MarketError("Your current password is not correct.")
    if len(new or "") < 6:
        raise MarketError("Password must have at least 6 characters.")
    db.execute("UPDATE users SET password_hash = ? WHERE user_id = ?", (generate_password_hash(new), user_id))


def set_user_active(db, user_id, active):
    """Administrator: deactivate or re-activate an account (admins cannot be deactivated)."""
    user = db.execute("SELECT role FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not user:
        raise MarketError("User not found.")
    if user["role"] == "admin":
        raise MarketError("An administrator account cannot be deactivated.")
    db.execute("UPDATE users SET is_active = ? WHERE user_id = ?", (1 if active else 0, user_id))


# --------------------------------------------------------------------------
# 2. Product management module
# --------------------------------------------------------------------------
def _price(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        raise MarketError("Price must be greater than zero.")
    if price <= 0:
        raise MarketError("Price must be greater than zero.")
    return round(price, 2)


def _quantity(value):
    try:
        qty = int(value)
    except (TypeError, ValueError):
        raise MarketError("Quantity must be a whole number, zero or more.")
    if qty < 0:
        raise MarketError("Quantity cannot be negative.")
    return qty


def add_product(db, farmer_id, name, category, price, quantity, description="", harvest_date=""):
    name = (name or "").strip()
    if not name:
        raise MarketError("Product name is required.")
    if category not in CATEGORIES:
        raise MarketError("Choose a valid category.")
    price, quantity = _price(price), _quantity(quantity)
    harvest_date = (harvest_date or "").strip()
    if harvest_date:
        try:
            date.fromisoformat(harvest_date)
        except ValueError:
            raise MarketError("Harvest date must be a valid date.")
    cur = db.execute(
        "INSERT INTO products (farmer_id,product_name,category,price,quantity,description,"
        "harvest_date,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (farmer_id, name, category, price, quantity, (description or "").strip(), harvest_date, now()),
    )
    return cur.lastrowid


def update_product(db, farmer_id, product_id, price, quantity, name=None, category=None,
                   description=None, harvest_date=None):
    """Price and stock are always updated; the other details only when they are supplied."""
    product = db.execute("SELECT * FROM products WHERE product_id = ? AND farmer_id = ?",
                         (product_id, farmer_id)).fetchone()
    if not product:
        raise MarketError("Product not found.")
    price, quantity = _price(price), _quantity(quantity)
    name = product["product_name"] if name is None else name.strip()
    if not name:
        raise MarketError("Product name is required.")
    category = product["category"] if category is None else category
    if category not in CATEGORIES:
        raise MarketError("Choose a valid category.")
    description = product["description"] if description is None else description.strip()
    harvest_date = product["harvest_date"] if harvest_date is None else harvest_date.strip()
    if harvest_date:
        try:
            date.fromisoformat(harvest_date)
        except ValueError:
            raise MarketError("Harvest date must be a valid date.")
    db.execute("UPDATE products SET product_name=?, category=?, price=?, quantity=?, description=?, "
               "harvest_date=? WHERE product_id=?",
               (name, category, price, quantity, description, harvest_date, product_id))


def delete_product(db, farmer_id, product_id):
    """A farmer deletes own product; the administrator passes farmer_id=None to delete any."""
    sql, args = "SELECT * FROM products WHERE product_id = ?", [product_id]
    if farmer_id is not None:
        sql, args = sql + " AND farmer_id = ?", args + [farmer_id]
    if not db.execute(sql, args).fetchone():
        raise MarketError("Product not found.")
    if db.execute("SELECT 1 FROM order_items WHERE product_id = ?", (product_id,)).fetchone():
        raise MarketError("A product that has orders cannot be deleted.")
    db.execute("DELETE FROM products WHERE product_id = ?", (product_id,))


_PRODUCT_SELECT = """
    SELECT p.*, u.name AS farmer_name, u.location AS location, u.upi_id AS farmer_upi,
           (SELECT ROUND(AVG(rating),1) FROM reviews r WHERE r.product_id = p.product_id) AS avg_rating,
           (SELECT COUNT(*) FROM reviews r WHERE r.product_id = p.product_id) AS review_count
    FROM products p JOIN users u ON u.user_id = p.farmer_id
"""


def _like(text):
    """Escape the LIKE wildcards so a search for '%' or '_' matches those characters literally."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _search_sql(q, category, max_price, location):
    sql, args = " WHERE p.quantity > 0 AND u.is_active = 1", []
    q = (q or "").strip()
    if q:
        sql += " AND (p.product_name LIKE ? ESCAPE '\\' OR p.description LIKE ? ESCAPE '\\')"
        args += [_like(q), _like(q)]
    if category in CATEGORIES:
        sql += " AND p.category = ?"
        args.append(category)
    if max_price not in (None, ""):
        try:
            args.append(float(max_price))
            sql += " AND p.price <= ?"
        except ValueError:
            pass
    if (location or "").strip():
        sql += " AND u.location LIKE ? ESCAPE '\\'"
        args.append(_like(location.strip()))
    return sql, args


def search_products(db, q="", category="", max_price=None, location="", limit=None, offset=0):
    """3. Search and browse module: products with zero stock are not shown."""
    where, args = _search_sql(q, category, max_price, location)
    sql = _PRODUCT_SELECT + where + " ORDER BY p.created_at DESC, p.product_id DESC"
    if limit:
        sql, args = sql + " LIMIT ? OFFSET ?", args + [int(limit), int(offset)]
    return db.execute(sql, args).fetchall()


def count_products(db, q="", category="", max_price=None, location=""):
    where, args = _search_sql(q, category, max_price, location)
    return db.execute("SELECT COUNT(*) FROM products p JOIN users u ON u.user_id = p.farmer_id" + where,
                      args).fetchone()[0]


def all_products(db):
    return db.execute(_PRODUCT_SELECT + " ORDER BY p.product_id DESC").fetchall()


def get_product(db, product_id):
    return db.execute(_PRODUCT_SELECT + " WHERE p.product_id = ?", (product_id,)).fetchone()


def farmer_products(db, farmer_id):
    return db.execute(_PRODUCT_SELECT + " WHERE p.farmer_id = ? ORDER BY p.product_id DESC",
                      (farmer_id,)).fetchall()


def product_reviews(db, product_id):
    return db.execute(
        "SELECT r.*, u.name AS customer_name FROM reviews r JOIN users u ON u.user_id = r.customer_id "
        "WHERE r.product_id = ? ORDER BY r.review_id DESC", (product_id,)).fetchall()


# --------------------------------------------------------------------------
# 4. Order management module
# --------------------------------------------------------------------------
def place_order(db, customer_id, items, payment_method, delivery_address):
    """
    items: list of (product_id, quantity).  The order, its items and the stock
    reduction are saved in ONE transaction. Returns the new order id.
    UPI orders start with payment status 'Pending' (the customer then scans the QR code);
    Cash-on-Delivery orders start with 'COD'.
    """
    if payment_method not in PAYMENTS:
        raise MarketError("Choose a valid payment method: UPI or Cash on Delivery.")
    delivery_address = (delivery_address or "").strip()
    if not delivery_address:
        raise MarketError("Delivery address is required.")
    merged = {}
    for pid, qty in items:
        try:
            pid, qty = int(pid), int(qty)
        except (TypeError, ValueError):
            raise MarketError("Invalid product or quantity.")
        if qty < 1:
            raise MarketError("Quantity must be at least 1.")
        merged[pid] = merged.get(pid, 0) + qty
    if not merged:
        raise MarketError("Your cart is empty.")

    with transaction(db):
        customer = db.execute("SELECT role FROM users WHERE user_id = ? AND is_active = 1",
                              (customer_id,)).fetchone()
        if not customer or customer["role"] != "customer":
            raise MarketError("Only customers can place orders.")
        lines, farmers = [], set()
        for pid, qty in merged.items():
            product = db.execute("SELECT * FROM products WHERE product_id = ?", (pid,)).fetchone()
            if not product:
                raise MarketError("Product not found.")
            if product["quantity"] < qty:
                raise MarketError(
                    f"Insufficient stock for {product['product_name']}: only {product['quantity']} available.")
            farmers.add(product["farmer_id"])
            lines.append((product, qty))
        if len(farmers) != 1:
            raise MarketError("An order can contain products of one farmer only.")
        farmer_id = farmers.pop()
        farmer = db.execute("SELECT * FROM users WHERE user_id = ? AND is_active = 1", (farmer_id,)).fetchone()
        if not farmer:
            raise MarketError("This farmer is not available right now.")
        if payment_method == "UPI" and not (farmer["upi_id"] or "").strip():
            raise MarketError("This farmer has not added a UPI ID yet. Please choose Cash on Delivery.")
        total = round(sum(p["price"] * q for p, q in lines), 2)
        cur = db.execute(
            "INSERT INTO orders (customer_id,farmer_id,order_date,total_amount,status,payment_method,"
            "delivery_address,payment_status) VALUES (?,?,?,?,?,?,?,?)",
            (customer_id, farmer_id, now(), total, "Placed", payment_method, delivery_address,
             "Pending" if payment_method == "UPI" else "COD"))
        order_id = cur.lastrowid
        for product, qty in lines:
            db.execute("INSERT INTO order_items (order_id,product_id,quantity,unit_price) VALUES (?,?,?,?)",
                       (order_id, product["product_id"], qty, product["price"]))
            db.execute("UPDATE products SET quantity = quantity - ? WHERE product_id = ?",
                       (qty, product["product_id"]))
    return order_id


def _restock(db, order_id):
    for it in db.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall():
        db.execute("UPDATE products SET quantity = quantity + ? WHERE product_id = ?",
                   (it["quantity"], it["product_id"]))


def change_status(db, farmer_id, order_id, new_status):
    """Move an order to the next allowed status. Cancelling returns the stock.

    UPI orders can be confirmed only after the customer has submitted the payment
    reference, and delivered only after the farmer has verified the payment.
    """
    with transaction(db):
        order = db.execute("SELECT * FROM orders WHERE order_id = ? AND farmer_id = ?",
                           (order_id, farmer_id)).fetchone()
        if not order:
            raise MarketError("Order not found.")
        if new_status not in TRANSITIONS[order["status"]]:
            raise MarketError(f"Cannot change status from {order['status']} to {new_status}.")
        if order["payment_method"] == "UPI":
            if new_status == "Confirmed" and order["payment_status"] not in ("Submitted", "Paid"):
                raise MarketError("The customer has not submitted the UPI payment yet.")
            if new_status == "Delivered" and order["payment_status"] != "Paid":
                raise MarketError("Verify the UPI payment (Payment received) before marking delivered.")
        if new_status == "Cancelled":
            _restock(db, order_id)
        db.execute("UPDATE orders SET status = ? WHERE order_id = ?", (new_status, order_id))
        if new_status == "Delivered" and order["payment_method"] == "Cash on Delivery":
            db.execute("UPDATE orders SET payment_status = 'Paid' WHERE order_id = ?", (order_id,))


def customer_cancel(db, customer_id, order_id):
    """A customer may cancel an order until the farmer has confirmed it. Stock is returned."""
    with transaction(db):
        order = db.execute("SELECT * FROM orders WHERE order_id = ? AND customer_id = ?",
                           (order_id, customer_id)).fetchone()
        if not order:
            raise MarketError("Order not found.")
        if order["status"] != "Placed":
            raise MarketError("Only an order that is still 'Placed' can be cancelled by the customer.")
        if order["payment_status"] in ("Submitted", "Paid"):
            raise MarketError("You have already paid for this order. Please contact the farmer to cancel it.")
        _restock(db, order_id)
        db.execute("UPDATE orders SET status = 'Cancelled' WHERE order_id = ?", (order_id,))


UTR_RE = re.compile(r"^\d{12}$")


def submit_upi_payment(db, customer_id, order_id, utr):
    """The customer reports the UPI transaction reference (UTR) after scanning the QR code."""
    utr = (utr or "").strip()
    if not UTR_RE.match(utr):
        raise MarketError("Enter the 12-digit UPI transaction reference (UTR) from your payment app.")
    with transaction(db):
        order = db.execute("SELECT * FROM orders WHERE order_id = ? AND customer_id = ?",
                           (order_id, customer_id)).fetchone()
        if not order:
            raise MarketError("Order not found.")
        if order["payment_method"] != "UPI":
            raise MarketError("This order is not a UPI order.")
        if order["status"] == "Cancelled":
            raise MarketError("This order was cancelled.")
        if order["payment_status"] != "Pending":
            raise MarketError("The payment for this order has already been submitted.")
        if db.execute("SELECT 1 FROM orders WHERE payment_ref = ?", (utr,)).fetchone():
            raise MarketError("This transaction reference has already been used for another order.")
        db.execute("UPDATE orders SET payment_status = 'Submitted', payment_ref = ? WHERE order_id = ?",
                   (utr, order_id))


def confirm_payment(db, farmer_id, order_id):
    """The farmer verified the money in the bank / UPI app and marks the order as paid."""
    with transaction(db):
        order = db.execute("SELECT * FROM orders WHERE order_id = ? AND farmer_id = ?",
                           (order_id, farmer_id)).fetchone()
        if not order:
            raise MarketError("Order not found.")
        if order["payment_method"] != "UPI":
            raise MarketError("Only UPI payments need to be verified.")
        if order["status"] == "Cancelled":
            raise MarketError("This order was cancelled.")
        if order["payment_status"] == "Paid":
            raise MarketError("Payment is already marked as received.")
        db.execute("UPDATE orders SET payment_status = 'Paid' WHERE order_id = ?", (order_id,))


def upi_uri(order):
    """The payment link encoded in the QR code (works with GPay, PhonePe, Paytm, BHIM ...)."""
    return (f"upi://pay?pa={order['farmer_upi']}&pn={quote(order['farmer_name'])}"
            f"&am={order['total_amount']:.2f}&cu=INR&tn={quote('Order ' + str(order['order_id']))}")


_ORDER_SELECT = """
    SELECT o.*, c.name AS customer_name, c.phone AS customer_phone, f.name AS farmer_name,
           f.location AS farmer_location, f.phone AS farmer_phone, f.upi_id AS farmer_upi
    FROM orders o JOIN users c ON c.user_id = o.customer_id JOIN users f ON f.user_id = o.farmer_id
"""


def customer_orders(db, customer_id):
    return db.execute(_ORDER_SELECT + " WHERE o.customer_id = ? ORDER BY o.order_id DESC",
                      (customer_id,)).fetchall()


def farmer_orders(db, farmer_id):
    return db.execute(_ORDER_SELECT + " WHERE o.farmer_id = ? ORDER BY o.order_id DESC",
                      (farmer_id,)).fetchall()


def all_orders(db, limit=200):
    return db.execute(_ORDER_SELECT + " ORDER BY o.order_id DESC LIMIT ?", (limit,)).fetchall()


def order_items(db, order_id):
    return db.execute(
        "SELECT i.*, p.product_name, p.category, i.quantity * i.unit_price AS line_total "
        "FROM order_items i JOIN products p ON p.product_id = i.product_id WHERE i.order_id = ?",
        (order_id,)).fetchall()


def order_items_for(db, order_ids):
    """Items of many orders in ONE query: {order_id: [rows]} (avoids one query per order)."""
    result = {oid: [] for oid in order_ids}
    if result:
        marks = ",".join("?" * len(result))
        for row in db.execute(
                "SELECT i.*, p.product_name, p.category, i.quantity * i.unit_price AS line_total "
                "FROM order_items i JOIN products p ON p.product_id = i.product_id "
                f"WHERE i.order_id IN ({marks}) ORDER BY i.item_id", list(result)):
            result[row["order_id"]].append(row)
    return result


def get_order_for_user(db, order_id, user):
    """A user can open only his or her own orders (the admin can open all)."""
    order = db.execute(_ORDER_SELECT + " WHERE o.order_id = ?", (order_id,)).fetchone()
    if not order:
        return None
    if user["role"] == "admin" or user["user_id"] in (order["customer_id"], order["farmer_id"]):
        return order
    return None


# --------------------------------------------------------------------------
# 6. Review and rating module
# --------------------------------------------------------------------------
def add_review(db, customer_id, product_id, rating, comment=""):
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise MarketError("Rating must be between 1 and 5.")
    if not 1 <= rating <= 5:
        raise MarketError("Rating must be between 1 and 5.")
    delivered = db.execute(
        "SELECT 1 FROM orders o JOIN order_items i ON i.order_id = o.order_id "
        "WHERE o.customer_id = ? AND i.product_id = ? AND o.status = 'Delivered' LIMIT 1",
        (customer_id, product_id)).fetchone()
    if not delivered:
        raise MarketError("You can review only products that were delivered to you.")
    if db.execute("SELECT 1 FROM reviews WHERE customer_id = ? AND product_id = ?",
                  (customer_id, product_id)).fetchone():
        raise MarketError("You have already reviewed this product.")
    db.execute("INSERT INTO reviews (product_id,customer_id,rating,comment,created_at) VALUES (?,?,?,?,?)",
               (product_id, customer_id, rating, (comment or "").strip(), now()))


def reviewable_products(db, customer_id):
    """Delivered products that this customer has not reviewed yet."""
    return db.execute(
        "SELECT DISTINCT p.product_id, p.product_name, p.category FROM orders o "
        "JOIN order_items i ON i.order_id = o.order_id JOIN products p ON p.product_id = i.product_id "
        "WHERE o.customer_id = ? AND o.status = 'Delivered' AND p.product_id NOT IN "
        "(SELECT product_id FROM reviews WHERE customer_id = ?)", (customer_id, customer_id)).fetchall()


# --------------------------------------------------------------------------
# 7. Statistics and administration module
# --------------------------------------------------------------------------
def farmer_stats(db, farmer_id):
    delivered = db.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(total_amount),0) AS revenue FROM orders "
        "WHERE farmer_id = ? AND status = 'Delivered'", (farmer_id,)).fetchone()
    best = db.execute(
        "SELECT p.product_name, SUM(i.quantity) AS qty FROM order_items i "
        "JOIN orders o ON o.order_id = i.order_id JOIN products p ON p.product_id = i.product_id "
        "WHERE o.farmer_id = ? AND o.status = 'Delivered' GROUP BY p.product_id "
        "ORDER BY qty DESC LIMIT 1", (farmer_id,)).fetchone()
    low = db.execute("SELECT product_name, quantity FROM products WHERE farmer_id = ? AND quantity <= ? "
                     "ORDER BY quantity", (farmer_id, LOW_STOCK_LIMIT)).fetchall()
    pending = db.execute("SELECT COUNT(*) FROM orders WHERE farmer_id = ? AND status IN "
                         "('Placed','Confirmed','Packed')", (farmer_id,)).fetchone()[0]
    to_verify = db.execute("SELECT COUNT(*) FROM orders WHERE farmer_id = ? AND payment_method = 'UPI' "
                           "AND status != 'Cancelled' AND payment_status = 'Submitted'",
                           (farmer_id,)).fetchone()[0]
    return {"delivered": delivered["n"], "revenue": delivered["revenue"],
            "best_seller": best["product_name"] if best else None,
            "low_stock": low, "pending": pending, "to_verify": to_verify}


def customer_stats(db, customer_id):
    row = db.execute(
        "SELECT COUNT(*) AS orders, COALESCE(SUM(CASE WHEN status='Delivered' THEN total_amount END),0) AS spent, "
        "COALESCE(SUM(status='Delivered'),0) AS delivered FROM orders WHERE customer_id = ?",
        (customer_id,)).fetchone()
    return dict(row)


def admin_overview(db):
    count = lambda sql: db.execute(sql).fetchone()[0]
    return {
        "farmers": count("SELECT COUNT(*) FROM users WHERE role='farmer'"),
        "customers": count("SELECT COUNT(*) FROM users WHERE role='customer'"),
        "products": count("SELECT COUNT(*) FROM products"),
        "orders": count("SELECT COUNT(*) FROM orders"),
        "sales": count("SELECT COALESCE(SUM(total_amount),0) FROM orders WHERE status='Delivered'"),
    }


def all_users(db):
    return db.execute("SELECT * FROM users ORDER BY role, user_id").fetchall()
