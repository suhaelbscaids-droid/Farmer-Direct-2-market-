"""
app.py - Flask application of the Farmer Direct Market Platform.
Routes, session handling, role-based access control, UPI / Cash-on-Delivery payment pages.

Run:  python app.py      then open  http://127.0.0.1:5000
      (set FLASK_DEBUG=1 only on your own computer while developing)
"""
import os
import secrets
import time
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

import market
from market import MarketError
from qr import qr_svg

PER_PAGE = 12

EMOJI = [
    ("tomato", "🍅"), ("mango", "🥭"), ("carrot", "🥕"), ("brinjal", "🍆"), ("eggplant", "🍆"),
    ("banana", "🍌"), ("onion", "🧅"), ("potato", "🥔"), ("corn", "🌽"), ("maize", "🌽"),
    ("apple", "🍎"), ("grape", "🍇"), ("orange", "🍊"), ("lemon", "🍋"), ("coconut", "🥥"),
    ("chilli", "🌶️"), ("chili", "🌶️"), ("pepper", "🫑"), ("capsicum", "🫑"), ("garlic", "🧄"),
    ("spinach", "🥬"), ("cabbage", "🥬"), ("keerai", "🥬"), ("lettuce", "🥬"), ("broccoli", "🥦"),
    ("cucumber", "🥒"), ("pumpkin", "🎃"), ("papaya", "🍈"), ("melon", "🍉"), ("guava", "🍐"),
    ("pineapple", "🍍"), ("strawberr", "🍓"), ("peanut", "🥜"), ("groundnut", "🥜"),
    ("milk", "🥛"), ("ghee", "🧈"), ("butter", "🧈"), ("curd", "🥣"), ("egg", "🥚"),
    ("honey", "🍯"), ("rice", "🍚"), ("wheat", "🌾"), ("millet", "🌾"), ("ragi", "🌾"),
    ("dal", "🫘"), ("bean", "🫘"), ("mushroom", "🍄"),
]
CATEGORY_EMOJI = {"Vegetable": "🥬", "Fruit": "🍎", "Grain": "🌾", "Other": "🧺"}


def _secret_key(persist):
    """SECRET_KEY from the environment, otherwise a random key saved once in .secret_key."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    if not persist:
        return secrets.token_hex(32)
    path = os.path.join(market.BASE_DIR, ".secret_key")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            saved = f.read().strip()
        if saved:
            return saved
    key = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key)
    return key


def create_app(db_path=None):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = _secret_key(persist=db_path is None)
    app.config["DATABASE"] = db_path or os.environ.get("DATABASE") or market.DEFAULT_DB
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("HTTPS") == "1",   # set HTTPS=1 behind https
        LOGIN_MAX_FAILS=5,          # failed log-ins allowed ...
        LOGIN_WINDOW=600,           # ... within this many seconds (also the lock time)
        SHOW_DEMO_HINTS=os.environ.get("DEMO_HINTS") == "1",
    )

    with market.connect(app.config["DATABASE"]) as boot:
        market.init_db(boot)

    # ------------------------------------------------------------------ helpers
    def get_db():
        if "db" not in g:
            g.db = market.connect(app.config["DATABASE"])
        return g.db

    @app.teardown_appcontext
    def close_db(_):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def current_user():
        if "user" not in g:
            uid = session.get("user_id")
            g.user = market.get_user(get_db(), uid) if uid else None
        return g.user

    def login_required(*roles):
        def deco(view):
            @wraps(view)
            def wrapper(*a, **kw):
                user = current_user()
                if not user:
                    session.pop("user_id", None)
                    flash("Please log in to continue.", "error")
                    page = {("farmer",): "login_farmer", ("admin",): "login_admin",
                            ("customer",): "login_customer"}.get(roles, "login_choice")
                    return redirect(url_for(page))
                if roles and user["role"] not in roles:
                    flash("Access denied: this page is not available for your account type.", "error")
                    return redirect(url_for("dashboard"))
                return view(*a, **kw)
            return wrapper
        return deco

    def back(default="index"):
        """Redirect target: the previous page, but only if it is on this same site."""
        ref = request.referrer
        if ref and urlparse(ref).netloc == request.host:
            return redirect(ref)
        return redirect(url_for(default))

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return session["csrf"]

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            if not secrets.compare_digest(request.form.get("csrf_token", ""), session.get("csrf", "x")):
                abort(400, "Security token expired. Go back, refresh the page and try again.")

    # ------------------------------------------- log-in throttling (in memory)
    failures = {}

    def _login_key(email):
        return (request.remote_addr, (email or "").strip().lower())

    def _recent_failures(key):
        window = app.config["LOGIN_WINDOW"]
        stamps = [t for t in failures.get(key, []) if time.time() - t < window]
        failures[key] = stamps
        return stamps

    # ----------------------------------------------------- cart (stored in session)
    def cart_lines():
        cart = session.get("cart", {})
        lines, total = [], 0.0
        for pid, qty in list(cart.items()):
            p = market.get_product(get_db(), int(pid))
            if not p or p["quantity"] < 1:
                cart.pop(pid, None)
                continue
            qty = min(int(qty), p["quantity"])
            cart[pid] = qty
            lines.append({"product": p, "qty": qty, "line_total": round(p["price"] * qty, 2)})
            total += p["price"] * qty
        session["cart"] = cart
        return lines, round(total, 2)

    @app.context_processor
    def inject():
        user = current_user()
        count = sum(session.get("cart", {}).values()) if user and user["role"] == "customer" else 0
        return {"me": user, "csrf_token": csrf_token(), "cart_count": count,
                "CATEGORIES": market.CATEGORIES, "PAYMENTS": market.PAYMENTS,
                "show_demo": app.config["SHOW_DEMO_HINTS"]}

    @app.template_filter("money")
    def money(v):
        return "₹{:,.2f}".format(v or 0)

    @app.template_filter("emoji")
    def emoji(name, category="Other"):
        low = (name or "").lower()
        for key, icon in EMOJI:
            if key in low:
                return icon
        return CATEGORY_EMOJI.get(category, "🧺")

    @app.template_filter("dt")
    def dt(v):
        from datetime import datetime
        try:
            return datetime.fromisoformat(v).strftime("%d %b %Y, %I:%M %p")
        except (TypeError, ValueError):
            return v or "—"

    @app.template_filter("day")
    def day(v):
        from datetime import date
        try:
            return date.fromisoformat(v).strftime("%d %b %Y")
        except (TypeError, ValueError):
            return v or "—"

    # ------------------------------------------------------------------ public pages
    def page_number():
        try:
            return max(1, int(request.args.get("page", 1)))
        except ValueError:
            return 1

    @app.route("/")
    def index():
        f = {k: request.args.get(k, "") for k in ("q", "category", "max_price", "location")}
        total = market.count_products(get_db(), **f)
        pages = max(1, -(-total // PER_PAGE))
        page = min(page_number(), pages)
        products = market.search_products(get_db(), **f, limit=PER_PAGE, offset=(page - 1) * PER_PAGE)
        return render_template("index.html", products=products, f=f, filtered=any(f.values()),
                               total=total, page=page, pages=pages)

    @app.route("/product/<int:product_id>")
    def product_detail(product_id):
        p = market.get_product(get_db(), product_id)
        if not p:
            abort(404)
        return render_template("product.html", p=p, reviews=market.product_reviews(get_db(), product_id))

    @app.route("/api/products")
    def api_products():
        f = (request.args.get("q", ""), request.args.get("category", ""),
             request.args.get("max_price"), request.args.get("location", ""))
        rows = market.search_products(get_db(), *f, limit=PER_PAGE * 4, offset=(page_number() - 1) * PER_PAGE * 4)
        return jsonify([{k: r[k] for k in ("product_id", "product_name", "category", "price", "quantity",
                                           "description", "harvest_date", "farmer_name", "location",
                                           "avg_rating", "review_count")} for r in rows])

    # ------------------------------------------------------------------ accounts
    @app.route("/register", methods=["GET", "POST"])
    def register():
        role = request.values.get("role", "customer")
        if request.method == "POST":
            form = request.form
            try:
                if form.get("role") not in ("farmer", "customer"):
                    raise MarketError("Role must be farmer or customer.")
                market.register_user(get_db(), form.get("name"), form.get("email"), form.get("phone"),
                                     form.get("password"), form.get("role"), form.get("location"),
                                     form.get("address"), form.get("upi_id"))
                flash("Account created! Please log in.", "success")
                return redirect(url_for("login_" + form["role"]))
            except MarketError as e:
                flash(str(e), "error")
                role = form.get("role", "customer")
        return render_template("register.html", role=role, form=request.form)

    @app.route("/login")
    def login_choice():
        if current_user():
            return redirect(url_for("dashboard"))
        return render_template("login_choice.html")

    def do_login(role):
        if current_user():
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = request.form.get("email")
            key = _login_key(email)
            if len(_recent_failures(key)) >= app.config["LOGIN_MAX_FAILS"]:
                minutes = -(-app.config["LOGIN_WINDOW"] // 60)
                flash(f"Too many failed log-in attempts. Please try again after {minutes} minutes.", "error")
                return render_template("login.html", role=role), 429
            try:
                user = market.authenticate(get_db(), email, request.form.get("password"))
            except MarketError as e:
                flash(str(e), "error")
                return render_template("login.html", role=role)
            if user and user["role"] != role:
                flash(f"This is the {role} login. Your account is a {user['role']} account — "
                      f"please use the {user['role']} login page.", "error")
            elif user:
                failures.pop(key, None)
                session.clear()
                session["user_id"] = user["user_id"]
                flash(f"Welcome back, {user['name']}!", "success")
                return redirect(url_for("dashboard"))
            else:
                failures.setdefault(key, []).append(time.time())
                flash("Wrong e-mail or password.", "error")
        return render_template("login.html", role=role)

    @app.route("/login/customer", methods=["GET", "POST"])
    def login_customer():
        return do_login("customer")

    @app.route("/login/farmer", methods=["GET", "POST"])
    def login_farmer():
        return do_login("farmer")

    @app.route("/login/admin", methods=["GET", "POST"])
    def login_admin():
        return do_login("admin")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("You have been logged out. See you soon!", "success")
        return redirect(url_for("index"))

    @app.route("/profile", methods=["GET", "POST"])
    @login_required()
    def profile():
        user = current_user()
        if request.method == "POST":
            f = request.form
            try:
                market.update_profile(get_db(), user["user_id"], f.get("name"), f.get("phone"),
                                      f.get("location"), f.get("address"), f.get("upi_id"))
                flash("Profile updated.", "success")
                return redirect(url_for("profile"))
            except MarketError as e:
                flash(str(e), "error")
        return render_template("profile.html", form=request.form if request.method == "POST" else user)

    @app.route("/profile/password", methods=["POST"])
    @login_required()
    def profile_password():
        f = request.form
        try:
            if f.get("new_password") != f.get("confirm_password"):
                raise MarketError("The new password and its confirmation do not match.")
            market.change_password(get_db(), current_user()["user_id"], f.get("current_password"),
                                   f.get("new_password"))
            flash("Password changed.", "success")
        except MarketError as e:
            flash(str(e), "error")
        return redirect(url_for("profile"))

    # ------------------------------------------------------------------ dashboards
    @app.route("/dashboard")
    @login_required()
    def dashboard():
        user, db = current_user(), get_db()
        if user["role"] == "farmer":
            orders = market.farmer_orders(db, user["user_id"])
            return render_template("farmer.html", stats=market.farmer_stats(db, user["user_id"]),
                                   products=market.farmer_products(db, user["user_id"]), orders=orders,
                                   items=market.order_items_for(db, [o["order_id"] for o in orders]),
                                   transitions=market.TRANSITIONS)
        if user["role"] == "customer":
            return render_template("customer.html", stats=market.customer_stats(db, user["user_id"]),
                                   orders=market.customer_orders(db, user["user_id"]),
                                   reviewable=market.reviewable_products(db, user["user_id"]))
        return render_template("admin.html", ov=market.admin_overview(db), users=market.all_users(db),
                               orders=market.all_orders(db), products=market.all_products(db))

    # ------------------------------------------------------------------ farmer actions
    @app.route("/products/add", methods=["POST"])
    @login_required("farmer")
    def product_add():
        f = request.form
        try:
            market.add_product(get_db(), current_user()["user_id"], f.get("product_name"), f.get("category"),
                               f.get("price"), f.get("quantity"), f.get("description"), f.get("harvest_date"))
            flash("Product listed successfully.", "success")
        except MarketError as e:
            flash(str(e), "error")
        return redirect(url_for("dashboard") + "#products")

    @app.route("/products/<int:product_id>/update", methods=["POST"])
    @login_required("farmer")
    def product_update(product_id):
        f = request.form
        try:
            market.update_product(get_db(), current_user()["user_id"], product_id, f.get("price"),
                                  f.get("quantity"), f.get("product_name"), f.get("category"),
                                  f.get("description"), f.get("harvest_date"))
            flash("Product updated.", "success")
        except MarketError as e:
            flash(str(e), "error")
        return redirect(url_for("dashboard") + "#products")

    @app.route("/products/<int:product_id>/delete", methods=["POST"])
    @login_required("farmer")
    def product_delete(product_id):
        try:
            market.delete_product(get_db(), current_user()["user_id"], product_id)
            flash("Product deleted.", "success")
        except MarketError as e:
            flash(str(e), "error")
        return redirect(url_for("dashboard") + "#products")

    @app.route("/orders/<int:order_id>/status", methods=["POST"])
    @login_required("farmer")
    def order_status(order_id):
        new = request.form.get("status")
        try:
            market.change_status(get_db(), current_user()["user_id"], order_id, new)
            flash(f"Order #{order_id} is now {new}."
                  + (" If the customer had paid by UPI, please refund the amount." if new == "Cancelled" else ""),
                  "success")
        except MarketError as e:
            flash(str(e), "error")
        return redirect(url_for("dashboard") + "#orders")

    @app.route("/orders/<int:order_id>/payment", methods=["POST"])
    @login_required("farmer")
    def order_payment(order_id):
        try:
            market.confirm_payment(get_db(), current_user()["user_id"], order_id)
            flash(f"Payment for order #{order_id} marked as received.", "success")
        except MarketError as e:
            flash(str(e), "error")
        return redirect(url_for("dashboard") + "#orders")

    # ------------------------------------------------------------------ cart + ordering
    @app.route("/cart")
    @login_required("customer")
    def cart():
        lines, total = cart_lines()
        return render_template("cart.html", lines=lines, total=total)

    @app.route("/cart/add", methods=["POST"])
    @login_required("customer")
    def cart_add():
        db = get_db()
        try:
            pid = int(request.form.get("product_id", 0))
            qty = int(request.form.get("quantity", 1))
        except ValueError:
            flash("Invalid product or quantity.", "error")
            return back()
        p = market.get_product(db, pid)
        cart_ = session.get("cart", {})
        if not p:
            flash("Product not found.", "error")
        elif qty < 1:
            flash("Quantity must be at least 1.", "error")
        else:
            in_cart = [market.get_product(db, int(k)) for k in cart_]
            other_farmer = any(c and c["farmer_id"] != p["farmer_id"] for c in in_cart)
            new_qty = cart_.get(str(pid), 0) + qty
            if other_farmer:
                flash("Your cart has products of another farmer. An order can contain products of one "
                      "farmer only — clear the cart or check out first.", "error")
            elif new_qty > p["quantity"]:
                flash(f"Insufficient stock for {p['product_name']}: only {p['quantity']} available.", "error")
            else:
                cart_[str(pid)] = new_qty
                session["cart"] = cart_
                flash(f"{p['product_name']} added to your cart.", "success")
                if request.form.get("buy_now"):
                    return redirect(url_for("cart"))
        return back()

    @app.route("/cart/remove", methods=["POST"])
    @login_required("customer")
    def cart_remove():
        cart_ = session.get("cart", {})
        cart_.pop(request.form.get("product_id", ""), None)
        session["cart"] = cart_
        return redirect(url_for("cart"))

    @app.route("/cart/clear", methods=["POST"])
    @login_required("customer")
    def cart_clear():
        session["cart"] = {}
        return redirect(url_for("cart"))

    @app.route("/order", methods=["POST"])
    @login_required("customer")
    def order():
        lines, _ = cart_lines()
        user = current_user()
        method = request.form.get("payment_method")
        try:
            order_id = market.place_order(get_db(), user["user_id"],
                                          [(l["product"]["product_id"], l["qty"]) for l in lines],
                                          method, request.form.get("delivery_address"))
        except MarketError as e:
            flash(str(e), "error")
            return redirect(url_for("cart"))
        session["cart"] = {}
        if method == "UPI":      # show the QR code to scan
            return redirect(url_for("order_pay", order_id=order_id))
        return redirect(url_for("order_success", order_id=order_id))

    @app.route("/orders/<int:order_id>/pay", methods=["GET", "POST"])
    @login_required("customer")
    def order_pay(order_id):
        db, user = get_db(), current_user()
        o = market.get_order_for_user(db, order_id, user)
        if not o or o["customer_id"] != user["user_id"] or o["payment_method"] != "UPI":
            abort(404)
        if request.method == "POST":
            try:
                market.submit_upi_payment(db, user["user_id"], order_id, request.form.get("utr"))
                flash("Payment reference received. The farmer will verify it shortly.", "success")
                return redirect(url_for("order_success", order_id=order_id))
            except MarketError as e:
                flash(str(e), "error")
            o = market.get_order_for_user(db, order_id, user)
        show_qr = o["status"] != "Cancelled" and o["payment_status"] == "Pending" and o["farmer_upi"]
        uri = market.upi_uri(o) if show_qr else None
        return render_template("pay.html", o=o, items=market.order_items(db, order_id), uri=uri,
                               qr=qr_svg(uri) if uri else None)

    @app.route("/orders/<int:order_id>/cancel", methods=["POST"])
    @login_required("customer")
    def order_cancel(order_id):
        try:
            market.customer_cancel(get_db(), current_user()["user_id"], order_id)
            flash(f"Order #{order_id} was cancelled.", "success")
        except MarketError as e:
            flash(str(e), "error")
        return redirect(url_for("dashboard") + "#orders")

    @app.route("/orders/<int:order_id>/success")
    @login_required("customer")
    def order_success(order_id):
        o = market.get_order_for_user(get_db(), order_id, current_user())
        if not o or o["customer_id"] != current_user()["user_id"]:
            abort(404)
        return render_template("order_success.html", o=o, items=market.order_items(get_db(), order_id))

    @app.route("/orders/<int:order_id>/receipt")
    @login_required("farmer", "customer", "admin")
    def receipt(order_id):
        o = market.get_order_for_user(get_db(), order_id, current_user())
        if not o:
            abort(404)
        return render_template("receipt.html", o=o, items=market.order_items(get_db(), order_id))

    @app.route("/reviews", methods=["POST"])
    @login_required("customer")
    def review():
        try:
            market.add_review(get_db(), current_user()["user_id"], request.form.get("product_id"),
                              request.form.get("rating"), request.form.get("comment"))
            flash("Thank you! Your review was saved.", "success")
        except MarketError as e:
            flash(str(e), "error")
        return redirect(url_for("dashboard") + "#reviews")

    # ------------------------------------------------------------------ administrator actions
    @app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
    @login_required("admin")
    def admin_toggle_user(user_id):
        db = get_db()
        target = market.get_user(db, user_id) or db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        try:
            if not target:
                raise MarketError("User not found.")
            market.set_user_active(db, user_id, not target["is_active"])
            flash(f"{target['name']} was {'deactivated' if target['is_active'] else 're-activated'}.", "success")
        except MarketError as e:
            flash(str(e), "error")
        return redirect(url_for("dashboard") + "#users")

    @app.route("/admin/products/<int:product_id>/delete", methods=["POST"])
    @login_required("admin")
    def admin_delete_product(product_id):
        try:
            market.delete_product(get_db(), None, product_id)
            flash("Product removed.", "success")
        except MarketError as e:
            flash(str(e), "error")
        return redirect(url_for("dashboard") + "#products")

    # ------------------------------------------------------------------ errors
    def error_page(e):
        return render_template("error.html", e=e), e.code

    for code in (400, 404, 405):
        app.register_error_handler(code, error_page)

    return app


if __name__ == "__main__":
    # Debug mode (interactive debugger) is OFF unless you ask for it with FLASK_DEBUG=1.
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1")
