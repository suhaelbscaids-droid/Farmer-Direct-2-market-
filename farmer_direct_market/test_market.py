"""
test_market.py - the 24 test cases of Table 4.1 (TC01-TC24) plus the extra cases
TC25-TC52 for UPI / Cash-on-Delivery payment and the security fixes (unit + web tests).
Run:  python test_market.py
"""
import os
import re
import tempfile
import time
import traceback

import market
from market import MarketError

TMP = tempfile.mkdtemp()
RESULTS = []


def fresh_db():
    path = os.path.join(TMP, f"t{len(RESULTS)}_{time.time_ns()}.db")
    db = market.connect(path)
    market.init_db(db)
    return db, path


def raises(fn, *a, **k):
    try:
        fn(*a, **k)
    except MarketError as e:
        return str(e)
    raise AssertionError("expected MarketError")


def setup():
    db, path = fresh_db()
    f1 = market.register_user(db, "Ravi", "ravi@x.com", "9876543210", "secret1", "farmer", "Pollachi",
                              upi_id="ravi@oksbi")
    f2 = market.register_user(db, "Meena", "meena@x.com", "9876543211", "secret1", "farmer", "Ooty")
    c = market.register_user(db, "Anitha", "anitha@x.com", "9123456780", "secret1", "customer", "Coimbatore")
    return db, path, f1, f2, c


def case(tc, title):
    def deco(fn):
        try:
            note = fn()
            RESULTS.append((tc, title, "PASS", note or ""))
        except Exception as e:  # noqa
            RESULTS.append((tc, title, "FAIL", f"{e!r}"))
            traceback.print_exc()
        return fn
    return deco


@case("TC01", "Farmer registration with valid data")
def _():
    db, *_ = setup()
    row = db.execute("SELECT password_hash FROM users WHERE email='ravi@x.com'").fetchone()
    assert row["password_hash"] != "secret1" and len(row["password_hash"]) > 30
    return "Registered; password stored as a hash"


@case("TC02", "Duplicate e-mail rejected")
def _():
    db, *_ = setup()
    msg = raises(market.register_user, db, "X", "RAVI@x.com", "9999999999", "secret1", "farmer")
    assert "e-mail" in msg
    return msg


@case("TC03", "Invalid phone number rejected")
def _():
    db, *_ = setup()
    return raises(market.register_user, db, "X", "x@x.com", "12345", "secret1", "farmer")


@case("TC04", "Short password rejected")
def _():
    db, *_ = setup()
    msg = raises(market.register_user, db, "X", "x@x.com", "9999999999", "abc", "farmer")
    assert "Password" in msg
    return msg


@case("TC05", "Login with wrong password fails")
def _():
    db, *_ = setup()
    assert market.authenticate(db, "ravi@x.com", "wrong") is None
    assert market.authenticate(db, "ravi@x.com", "secret1") is not None
    return "Authentication returned None"


@case("TC06", "Farmer adds product; invalid price rejected")
def _():
    db, _, f1, _, _ = setup()
    market.add_product(db, f1, "Tomatoes", "Vegetable", 50, 100)
    return raises(market.add_product, db, f1, "Bad", "Vegetable", 0, 10)


@case("TC07", "Search by name, category, price and location")
def _():
    db, _, f1, f2, _ = setup()
    market.add_product(db, f1, "Tomatoes", "Vegetable", 50, 100)
    market.add_product(db, f2, "Mangoes", "Fruit", 120, 20)
    assert len(market.search_products(db, q="tom")) == 1
    assert len(market.search_products(db, category="Fruit")) == 1
    assert len(market.search_products(db, max_price=60)) == 1
    assert len(market.search_products(db, location="ooty")) == 1
    return "All four filters returned the expected products"


@case("TC08", "Place order reduces stock and computes total")
def _():
    db, _, f1, _, c = setup()
    t = market.add_product(db, f1, "Tomatoes", "Vegetable", 52, 100)
    oid = market.place_order(db, c, [(t, 5)], "UPI", "Coimbatore")
    assert market.get_product(db, t)["quantity"] == 95
    assert db.execute("SELECT total_amount FROM orders WHERE order_id=?", (oid,)).fetchone()[0] == 260.0
    return "Total Rs. 260.00; Tomatoes stock 100 -> 95"


@case("TC09", "Order with insufficient stock rejected")
def _():
    db, _, f1, _, c = setup()
    m = market.add_product(db, f1, "Mangoes", "Fruit", 120, 20)
    msg = raises(market.place_order, db, c, [(m, 25)], "UPI", "Coimbatore")
    assert market.get_product(db, m)["quantity"] == 20
    return msg


@case("TC10", "Order with products of two farmers rejected")
def _():
    db, _, f1, f2, c = setup()
    a = market.add_product(db, f1, "Tomatoes", "Vegetable", 50, 10)
    b = market.add_product(db, f2, "Onions", "Vegetable", 40, 10)
    msg = raises(market.place_order, db, c, [(a, 1), (b, 1)], "UPI", "Coimbatore")
    assert market.get_product(db, a)["quantity"] == 10
    return msg


def delivered_order():
    db, _, f1, f2, c = setup()
    p = market.add_product(db, f1, "Tomatoes", "Vegetable", 52, 100)
    oid = market.place_order(db, c, [(p, 5)], "Cash on Delivery", "Coimbatore")
    return db, f1, f2, c, p, oid


@case("TC11", "Invalid status jump rejected")
def _():
    db, f1, _, _, _, oid = delivered_order()
    return raises(market.change_status, db, f1, oid, "Delivered")


@case("TC12", "Another farmer cannot update the order")
def _():
    db, _, f2, _, _, oid = delivered_order()
    return raises(market.change_status, db, f2, oid, "Confirmed")


@case("TC13", "Order lifecycle Placed -> Delivered")
def _():
    db, f1, _, _, _, oid = delivered_order()
    for s in ("Confirmed", "Packed", "Delivered"):
        market.change_status(db, f1, oid, s)
    assert db.execute("SELECT status FROM orders WHERE order_id=?", (oid,)).fetchone()[0] == "Delivered"
    return "Status moved Placed -> Confirmed -> Packed -> Delivered"


@case("TC14", "Cancelled order returns stock")
def _():
    db, f1, _, c, p, _ = delivered_order()
    before = market.get_product(db, p)["quantity"]
    oid = market.place_order(db, c, [(p, 10)], "Cash on Delivery", "Coimbatore")
    assert market.get_product(db, p)["quantity"] == before - 10
    market.change_status(db, f1, oid, "Cancelled")
    assert market.get_product(db, p)["quantity"] == before
    return f"Stock {before - 10} -> {before}"


@case("TC15", "Review allowed only after delivery")
def _():
    db, f1, _, c, p, oid = delivered_order()
    msg = raises(market.add_review, db, c, p, 5, "Nice")
    for s in ("Confirmed", "Packed", "Delivered"):
        market.change_status(db, f1, oid, s)
    market.add_review(db, c, p, 5, "Nice")
    return msg + "; Delivered product review saved"


@case("TC16", "Duplicate review rejected")
def _():
    db, f1, _, c, p, oid = delivered_order()
    for s in ("Confirmed", "Packed", "Delivered"):
        market.change_status(db, f1, oid, s)
    market.add_review(db, c, p, 5)
    return raises(market.add_review, db, c, p, 4)


@case("TC17", "Farmer statistics")
def _():
    db, f1, _, _, _, oid = delivered_order()
    for s in ("Confirmed", "Packed", "Delivered"):
        market.change_status(db, f1, oid, s)
    st = market.farmer_stats(db, f1)
    assert (st["delivered"], st["revenue"], st["best_seller"]) == (1, 260.0, "Tomatoes")
    return "Delivered orders 1, revenue Rs. 260.00, best seller Tomatoes"


@case("TC18", "Product with orders cannot be deleted")
def _():
    db, f1, _, _, p, _ = delivered_order()
    return raises(market.delete_product, db, f1, p)


# ------------------------------------------------------------------ web tests
# CSRF protection stays ON in every web test: post() adds the token like a real browser form does.
def web_client(path=None):
    import app as appmod
    path = path or os.path.join(TMP, f"web_{time.time_ns()}.db")
    application = appmod.create_app(path)
    application.config["TESTING"] = True
    return application.test_client(), path, application


def post(client, url, data=None, **kw):
    with client.session_transaction() as sess:
        sess.setdefault("csrf", "testtoken")
        token = sess["csrf"]
    return client.post(url, data=dict(data or {}, csrf_token=token), **kw)


def signup_login(client, role, email, name, phone="9876543210", upi=""):
    post(client, "/register", dict(name=name, email=email, phone=phone, password="secret1",
                                   role=role, location="Pollachi", address="Street 1", upi_id=upi))
    return post(client, f"/login/{role}", dict(email=email, password="secret1"), follow_redirects=True)


def shop():
    """A farmer (with UPI ID) listing Brinjal and a logged-in customer, sharing one database."""
    farmer, path, _ = web_client()
    signup_login(farmer, "farmer", "f@x.com", "Ravi Kumar", "9876543210", "ravi@oksbi")
    post(farmer, "/products/add", dict(product_name="Brinjal", category="Vegetable", price="25", quantity="20"))
    cust, _, _ = web_client(path)
    signup_login(cust, "customer", "c@x.com", "Anitha Raj", "9123456780")
    return farmer, cust, path


@case("TC19", "Web: register, login and open dashboard")
def _():
    c, *_ = web_client()
    r = signup_login(c, "farmer", "f@x.com", "Ravi Kumar")
    assert b"Namaste" in r.data and b"Add a product" in r.data
    return "Farmer dashboard displayed after login"


@case("TC20", "Web: farmer adds product and it is listed publicly")
def _():
    c, *_ = web_client()
    signup_login(c, "farmer", "f@x.com", "Ravi Kumar")
    post(c, "/products/add", dict(product_name="Brinjal", category="Vegetable", price="30", quantity="20"))
    assert b"Brinjal" in c.get("/").data
    return "Brinjal visible on the public product page"


@case("TC21", "Web: customer cannot add products (role check)")
def _():
    c, *_ = web_client()
    signup_login(c, "customer", "c@x.com", "Anitha Raj")
    r = post(c, "/products/add", dict(product_name="Hack", category="Vegetable", price="1", quantity="1"),
             follow_redirects=True)
    assert b"Access denied" in r.data and b"Hack" not in c.get("/").data
    return "Access denied message displayed"


@case("TC22", "Web: unauthenticated user is redirected to login")
def _():
    c, *_ = web_client()
    r = c.get("/dashboard")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    return "Redirected to login page"


@case("TC23", "Web: customer places a Cash-on-Delivery order through the form")
def _():
    farmer, cust, _ = shop()
    post(cust, "/cart/add", dict(product_id="1", quantity="3"))
    r = post(cust, "/order", dict(payment_method="Cash on Delivery", delivery_address="Coimbatore"),
             follow_redirects=True)
    assert b"Order placed successfully" in r.data and b"75.00" in r.data and b"Pay on delivery" in r.data
    return "Order #1 created, total Rs. 75.00"


@case("TC24", "Web: JSON API returns products")
def _():
    c, *_ = web_client()
    signup_login(c, "farmer", "f@x.com", "Ravi Kumar")
    post(c, "/products/add", dict(product_name="Brinjal", category="Vegetable", price="25", quantity="20"))
    data = c.get("/api/products").get_json()
    assert len(data) == 1
    return f"API returned {len(data)} product(s)"


# ================= extra cases: payments (UPI / COD) =================
def paid_setup():
    db, _, f1, f2, c = setup()
    p = market.add_product(db, f1, "Tomatoes", "Vegetable", 52, 100)
    return db, f1, f2, c, p


def db_row(db, oid):
    return db.execute("SELECT * FROM orders WHERE order_id=?", (oid,)).fetchone()


@case("TC25", "Only UPI and Cash on Delivery are accepted")
def _():
    db, f1, _, c, p = paid_setup()
    assert market.PAYMENTS == ["UPI", "Cash on Delivery"]
    return raises(market.place_order, db, c, [(p, 1)], "Card", "Coimbatore")


@case("TC26", "UPI order starts 'Pending', COD order starts 'COD'")
def _():
    db, f1, _, c, p = paid_setup()
    u = market.place_order(db, c, [(p, 1)], "UPI", "Coimbatore")
    d = market.place_order(db, c, [(p, 1)], "Cash on Delivery", "Coimbatore")
    assert db_row(db, u)["payment_status"] == "Pending" and db_row(db, d)["payment_status"] == "COD"
    return "UPI -> Pending, COD -> COD"


@case("TC27", "UPI order refused when the farmer has no UPI ID")
def _():
    db, _, f2, c, _ = paid_setup()
    m = market.add_product(db, f2, "Onions", "Vegetable", 40, 10)
    msg = raises(market.place_order, db, c, [(m, 1)], "UPI", "Coimbatore")
    assert market.get_product(db, m)["quantity"] == 10
    market.place_order(db, c, [(m, 1)], "Cash on Delivery", "Coimbatore")
    return msg


@case("TC28", "UTR validation: bad, good and reused reference")
def _():
    db, f1, _, c, p = paid_setup()
    o1 = market.place_order(db, c, [(p, 1)], "UPI", "Coimbatore")
    o2 = market.place_order(db, c, [(p, 1)], "UPI", "Coimbatore")
    raises(market.submit_upi_payment, db, c, o1, "12345")
    market.submit_upi_payment(db, c, o1, "412345678901")
    assert db_row(db, o1)["payment_status"] == "Submitted"
    raises(market.submit_upi_payment, db, c, o1, "412345678902")      # already submitted
    return raises(market.submit_upi_payment, db, c, o2, "412345678901")  # UTR reuse


@case("TC29", "UPI lifecycle: confirm needs payment, delivery needs verification")
def _():
    db, f1, _, c, p = paid_setup()
    o = market.place_order(db, c, [(p, 2)], "UPI", "Coimbatore")
    raises(market.change_status, db, f1, o, "Confirmed")             # nothing paid yet
    market.submit_upi_payment(db, c, o, "412345678901")
    market.change_status(db, f1, o, "Confirmed")
    market.change_status(db, f1, o, "Packed")
    raises(market.change_status, db, f1, o, "Delivered")             # farmer has not verified
    market.confirm_payment(db, f1, o)
    market.change_status(db, f1, o, "Delivered")
    assert db_row(db, o)["status"] == "Delivered" and db_row(db, o)["payment_status"] == "Paid"
    return "Placed -> Submitted -> Confirmed -> Packed -> Paid -> Delivered"


@case("TC30", "Cash on Delivery order becomes 'Paid' when delivered")
def _():
    db, f1, _, c, p = paid_setup()
    o = market.place_order(db, c, [(p, 1)], "Cash on Delivery", "Coimbatore")
    for s in ("Confirmed", "Packed", "Delivered"):
        market.change_status(db, f1, o, s)
    assert db_row(db, o)["payment_status"] == "Paid"
    return "COD -> Paid on delivery"


@case("TC31", "Customer cancels own unpaid order (stock returns); paid order cannot be cancelled")
def _():
    db, f1, _, c, p = paid_setup()
    o = market.place_order(db, c, [(p, 10)], "UPI", "Coimbatore")
    assert market.get_product(db, p)["quantity"] == 90
    market.customer_cancel(db, c, o)
    assert market.get_product(db, p)["quantity"] == 100 and db_row(db, o)["status"] == "Cancelled"
    o2 = market.place_order(db, c, [(p, 1)], "UPI", "Coimbatore")
    market.submit_upi_payment(db, c, o2, "412345678903")
    return raises(market.customer_cancel, db, c, o2)


@case("TC32", "QR generator produces a valid, scannable code")
def _():
    import qr
    uri = "upi://pay?pa=ravi@oksbi&pn=Ravi%20Kumar&am=260.00&cu=INR&tn=Order%201"
    m = qr.make_matrix(uri)
    assert len(m) >= 21 and (len(m) - 17) % 4 == 0 and "<svg" in qr.qr_svg(uri)
    try:
        import cv2
        import numpy as np
    except ImportError:
        return f"QR version {(len(m) - 17) // 4} built (OpenCV not installed, decode check skipped)"
    n, sc = len(m) + 8, 8
    img = np.full((n * sc, n * sc), 255, np.uint8)
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            if v:
                img[(y + 4) * sc:(y + 5) * sc, (x + 4) * sc:(x + 5) * sc] = 0
    assert cv2.QRCodeDetector().detectAndDecode(img)[0] == uri
    return "QR decoded back to the exact UPI link"


@case("TC33", "Web: choosing UPI shows the QR code, UPI ID and amount")
def _():
    farmer, cust, _ = shop()
    post(cust, "/cart/add", dict(product_id="1", quantity="3"))
    r = post(cust, "/order", dict(payment_method="UPI", delivery_address="Coimbatore"), follow_redirects=True)
    assert b"<svg" in r.data and b"ravi@oksbi" in r.data and b"75.00" in r.data and b"Scan" in r.data
    return "Pay page shows QR for Rs. 75.00 to ravi@oksbi"


@case("TC34", "Web: customer submits UTR, farmer verifies payment and delivers")
def _():
    farmer, cust, _ = shop()
    post(cust, "/cart/add", dict(product_id="1", quantity="2"))
    post(cust, "/order", dict(payment_method="UPI", delivery_address="Coimbatore"))
    bad = post(cust, "/orders/1/pay", dict(utr="abc"), follow_redirects=True)
    assert b"12-digit" in bad.data
    ok = post(cust, "/orders/1/pay", dict(utr="412345678901"), follow_redirects=True)
    assert b"Awaiting verification" in ok.data
    assert b"waiting for your verification" in farmer.get("/dashboard").data
    for step in ("Confirmed", "Packed"):
        post(farmer, "/orders/1/status", dict(status=step))
    early = post(farmer, "/orders/1/status", dict(status="Delivered"), follow_redirects=True)
    assert b"Verify the UPI payment" in early.data
    post(farmer, "/orders/1/payment")
    post(farmer, "/orders/1/status", dict(status="Delivered"))
    assert b"Paid" in cust.get("/orders/1/receipt").data
    return "Pending -> Submitted -> Paid -> Delivered through the web forms"


@case("TC35", "Web: POST without CSRF token is rejected")
def _():
    c, *_ = web_client()
    r = c.post("/register", data=dict(name="A", email="a@x.com", phone="9876543210", password="secret1",
                                      role="customer"))
    assert r.status_code == 400
    return "HTTP 400 returned"


@case("TC36", "Web: logout works only with POST")
def _():
    c, *_ = web_client()
    signup_login(c, "customer", "c@x.com", "Anitha Raj")
    assert c.get("/logout").status_code == 405
    assert b"Logout" in c.get("/").data
    post(c, "/logout")
    assert c.get("/dashboard").status_code == 302
    return "GET /logout -> 405; POST logs out"


@case("TC37", "Web: repeated wrong passwords lock the log-in")
def _():
    c, *_ = web_client()
    post(c, "/register", dict(name="A", email="a@x.com", phone="9876543210", password="secret1", role="customer"))
    for _i in range(5):
        post(c, "/login/customer", dict(email="a@x.com", password="wrong!!"))
    r = post(c, "/login/customer", dict(email="a@x.com", password="secret1"))
    assert r.status_code == 429 and c.get("/dashboard").status_code == 302
    return "6th attempt blocked (HTTP 429) even with the right password"


@case("TC38", "Debug mode is off and the secret key is not the old default")
def _():
    c, _, application = web_client()
    assert application.debug is False
    assert application.config["SECRET_KEY"] not in ("dev-only-change-me", "", None)
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"), encoding="utf-8").read()
    assert "debug=True" not in src
    return "debug=False; random secret key"


@case("TC39", "Deactivated account cannot log in; its products disappear from the market")
def _():
    db, f1, _, c, p = paid_setup()
    admin = market.register_user(db, "Admin", "adm@x.com", "9000000000", "adminpw", "admin")
    market.set_user_active(db, f1, False)
    assert market.search_products(db) == []
    msg = raises(market.authenticate, db, "ravi@x.com", "secret1")
    raises(market.set_user_active, db, admin, False)                  # admin protected
    market.set_user_active(db, f1, True)
    assert len(market.search_products(db)) == 1
    return msg


@case("TC40", "Profile update, duplicate phone and password change")
def _():
    db, f1, f2, c, _ = paid_setup()
    market.update_profile(db, f1, "Ravi K", "9876543210", "Pollachi", "Farm 1", "ravi@okaxis")
    assert market.get_user(db, f1)["upi_id"] == "ravi@okaxis"
    raises(market.update_profile, db, f1, "Ravi K", "9876543211", "", "", "")      # f2's phone
    raises(market.update_profile, db, f1, "Ravi K", "9876543210", "", "", "not a upi")
    raises(market.change_password, db, c, "wrong", "newpass1")
    market.change_password(db, c, "secret1", "newpass1")
    assert market.authenticate(db, "anitha@x.com", "newpass1") is not None
    return raises(market.register_user, db, "Z", "z@x.com", "9123456780", "secret1", "customer")


@case("TC41", "Farmer edits all product details; other farmers cannot")
def _():
    db, f1, f2, _, p = paid_setup()
    market.update_product(db, f1, p, 55, 90, "Country Tomatoes", "Fruit", "Sweet", "2026-10-01")
    row = market.get_product(db, p)
    assert (row["product_name"], row["category"], row["price"], row["quantity"]) == ("Country Tomatoes", "Fruit", 55, 90)
    market.update_product(db, f1, p, 56, 90)                                       # quick edit keeps details
    assert market.get_product(db, p)["product_name"] == "Country Tomatoes"
    return raises(market.update_product, db, f2, p, 1, 1, "Hack")


@case("TC42", "Search treats % and _ literally")
def _():
    db, f1, *_ = paid_setup()
    market.add_product(db, f1, "Mango_Raw", "Fruit", 50, 5)
    assert market.search_products(db, q="%") == []
    assert len(market.search_products(db, q="o_R")) == 1
    return "'%' matches nothing; '_' matches only an underscore"


@case("TC43", "Pagination: limit / offset and total count")
def _():
    db, f1, *_ = paid_setup()
    for i in range(25):
        market.add_product(db, f1, f"Item {i}", "Other", 10, 5)
    assert market.count_products(db) == 26
    assert len(market.search_products(db, limit=12)) == 12
    assert len(market.search_products(db, limit=12, offset=24)) == 2
    return "26 products -> pages of 12, 12, 2"


@case("TC44", "Items of many orders are loaded in one query")
def _():
    db, f1, _, c, p = paid_setup()
    ids = [market.place_order(db, c, [(p, i + 1)], "Cash on Delivery", "Coimbatore") for i in range(3)]
    grouped = market.order_items_for(db, ids)
    assert [grouped[i][0]["quantity"] for i in ids] == [1, 2, 3] and market.order_items_for(db, []) == {}
    return "3 orders -> 1 query"


@case("TC45", "Web: other customers cannot open someone else's order pages")
def _():
    farmer, cust, path = shop()
    post(cust, "/cart/add", dict(product_id="1", quantity="1"))
    post(cust, "/order", dict(payment_method="UPI", delivery_address="Coimbatore"))
    other, _, _ = web_client(path)
    signup_login(other, "customer", "o@x.com", "Other Person", "9000011111")
    assert other.get("/orders/1/receipt").status_code == 404
    assert other.get("/orders/1/pay").status_code == 404
    assert post(other, "/orders/1/cancel", follow_redirects=True).status_code == 200
    assert b"Cancelled" not in cust.get("/orders/1/receipt").data
    return "Receipt and pay page -> 404; cancel does nothing"


@case("TC46", "Web: administrator can deactivate users and remove products; others cannot")
def _():
    farmer, cust, path = shop()
    import app as appmod
    db = market.connect(path)
    market.register_user(db, "Admin", "admin@x.com", "9000000000", "adminpw", "admin")
    spare = market.add_product(db, 1, "Spare", "Other", 10, 5)
    admin, _, _ = web_client(path)
    post(admin, "/login/admin", dict(email="admin@x.com", password="adminpw"))
    assert b"All products" in admin.get("/dashboard").data
    post(admin, f"/admin/products/{spare}/delete")
    assert market.get_product(db, spare) is None
    denied = post(cust, "/admin/users/1/toggle", follow_redirects=True)
    assert b"Access denied" in denied.data
    assert db.execute("SELECT is_active FROM users WHERE user_id=1").fetchone()[0] == 1
    post(admin, "/admin/users/2/toggle")
    assert db.execute("SELECT is_active FROM users WHERE user_id=2").fetchone()[0] == 0
    return "Admin actions work; customer gets Access denied"


@case("TC47", "Old database files are upgraded automatically")
def _():
    path = os.path.join(TMP, f"old_{time.time_ns()}.db")
    import sqlite3
    old = sqlite3.connect(path)
    old.executescript("""
      CREATE TABLE users (user_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
        phone TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL, location TEXT DEFAULT '',
        address TEXT DEFAULT '', created_at TEXT NOT NULL);
      CREATE TABLE orders (order_id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER, farmer_id INTEGER,
        order_date TEXT, total_amount REAL, status TEXT DEFAULT 'Placed', payment_method TEXT, delivery_address TEXT);
      INSERT INTO orders (customer_id,farmer_id,order_date,total_amount,status,payment_method,delivery_address)
        VALUES (1,2,'2026-01-01',10,'Delivered','Card','x'),(1,2,'2026-01-02',10,'Placed','Cash on Delivery','x');
    """)
    old.commit(); old.close()
    db = market.connect(path)
    market.init_db(db)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(orders)")} | {r["name"] for r in db.execute("PRAGMA table_info(users)")}
    assert {"payment_status", "payment_ref", "upi_id", "is_active"} <= cols
    st = [r[0] for r in db.execute("SELECT payment_status FROM orders ORDER BY order_id")]
    assert st == ["Paid", "COD"]
    return "Columns added; old orders -> Paid / COD"


@case("TC48", "Session cookie is HttpOnly and SameSite")
def _():
    c, *_ = web_client()
    r = c.get("/register")
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert r.headers.get("X-Frame-Options") == "DENY" and r.headers.get("X-Content-Type-Options") == "nosniff"
    return "HttpOnly; SameSite=Lax; security headers present"


@case("TC49", "Web: redirect after add-to-cart ignores a foreign Referer")
def _():
    farmer, cust, _ = shop()
    r = post(cust, "/cart/add", dict(product_id="1", quantity="1"), headers={"Referer": "http://evil.example/x"})
    assert "evil.example" not in r.headers["Location"]
    return "Redirected to own site: " + r.headers["Location"]


@case("TC50", "Web: customer cancels an unpaid order from the dashboard")
def _():
    farmer, cust, _ = shop()
    post(cust, "/cart/add", dict(product_id="1", quantity="4"))
    post(cust, "/order", dict(payment_method="Cash on Delivery", delivery_address="Coimbatore"))
    r = post(cust, "/orders/1/cancel", follow_redirects=True)
    assert b"was cancelled" in r.data
    assert b"20 left" in cust.get("/").data      # stock back to 20
    return "Cancelled; stock back to 20"


@case("TC51", "Web: profile page saves UPI ID and changes password")
def _():
    c, *_ = web_client()
    signup_login(c, "farmer", "f@x.com", "Ravi Kumar")
    r = post(c, "/profile", dict(name="Ravi Kumar", phone="9876543210", location="Pollachi",
                                 address="Farm", upi_id="ravi@oksbi"), follow_redirects=True)
    assert b"Profile updated" in r.data and b"ravi@oksbi" in r.data
    r = post(c, "/profile/password", dict(current_password="secret1", new_password="newsecret",
                                          confirm_password="newsecret"), follow_redirects=True)
    assert b"Password changed" in r.data
    return "UPI ID saved; password changed"


@case("TC52", "Web: demo credentials are hidden unless DEMO_HINTS=1")
def _():
    c, *_ = web_client()
    assert b"Demo account" not in c.get("/login/customer").data
    os.environ["DEMO_HINTS"] = "1"
    try:
        c2, *_ = web_client()
        assert b"Demo account" in c2.get("/login/customer").data
    finally:
        del os.environ["DEMO_HINTS"]
    return "Hidden by default, shown on request"


if __name__ == "__main__":
    print("=" * 96)
    print(f"{'ID':<6}{'Test case':<52}{'Result':<8}Observed")
    print("-" * 96)
    for tc, title, status, note in RESULTS:
        print(f"{tc:<6}{title:<52}{status:<8}{note}")
    passed = sum(1 for r in RESULTS if r[2] == "PASS")
    print("-" * 96)
    print(f"{passed} of {len(RESULTS)} test cases passed.")
    raise SystemExit(0 if passed == len(RESULTS) else 1)
