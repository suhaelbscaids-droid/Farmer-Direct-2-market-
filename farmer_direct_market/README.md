# 🌾 Farmer Direct Market Platform — Phase II Working Model
Flask + SQLite + Jinja2 web application (farmer-to-consumer marketplace) with **UPI QR-code payment** and **Cash on Delivery**.

## Folder structure
```
farmer_direct_market/
├── app.py            ← Flask routes, sessions, role checks, payment pages
├── market.py         ← business rules (validation, stock, orders, payments, reviews, stats)
├── qr.py             ← built-in QR-code generator (no extra package needed)
├── schema.sql        ← 5 database tables
├── seed_demo.py      ← demo data
├── test_market.py    ← 52 test cases (TC01-TC24 of Table 4.1 + TC25-TC52 new)
├── requirements.txt
├── static/style.css
└── templates/        ← base, _macros, index, product, login*, register, profile,
                        farmer, customer, admin, cart, pay, order_success, receipt, error
```

## Run (VS Code terminal, inside the project folder)
```
pip install flask
python seed_demo.py --reset     # optional: sample data
python app.py                   # open http://127.0.0.1:5000
python test_market.py           # run all 52 tests
```
Optional switches (environment variables):

| Variable | Meaning |
|---|---|
| `DEMO_HINTS=1` | show the demo e-mail/password box on the login pages |
| `FLASK_DEBUG=1` | development debugger — **only on your own computer, never on a server** |
| `SECRET_KEY=...` | session-signing key (if not set, a random key is created once in `.secret_key`) |
| `HTTPS=1` | mark the session cookie `Secure` when the site is served over https |

Windows PowerShell: `$env:DEMO_HINTS="1"; python app.py`   ·   Mac/Linux: `DEMO_HINTS=1 python app.py`

## Demo accounts
| Role | E-mail | Password | Login page |
|---|---|---|---|
| Farmer | ravi@farm.com (also meena@, selvam@) | demo123 | /login/farmer |
| Customer | anitha@mail.com (also karthik@) | demo123 | /login/customer |
| Admin | admin@farm.com | admin123 | /login/admin |

Demo farmers have sample UPI IDs (e.g. `ravikumar@oksbi`). **To test with a real UPI app, log in as a farmer →
Profile → enter your own UPI ID**, then place a UPI order as a customer and scan the QR code.
Log in as **karthik@mail.com** and open *Pay now* on order #6 to see a ready-made QR code.

## Payment flow
**UPI**
1. Customer chooses **UPI** at checkout and clicks *Place order*.
2. The **Pay** page shows a **QR code** (amount + farmer's UPI ID + order number built in). The customer scans it with
   GPay / PhonePe / Paytm / BHIM (on a phone there is also an *Open in my UPI app* button).
3. After paying, the customer enters the 12-digit **UTR** (transaction ID) → payment status *Awaiting verification*.
4. The farmer checks the money in his bank/UPI app and clicks **💰 Payment received** → status *Paid*.
   (A UPI order can be *Confirmed* only after the UTR is submitted and *Delivered* only after the farmer verified it.)

**Cash on Delivery** — order is placed directly; payment status becomes *Paid* when the farmer marks it *Delivered*.

A customer can cancel an order while it is still *Placed* and not yet paid (stock is returned).

> The money moves directly from the customer's bank to the farmer's UPI ID; the platform never holds it.
> Automatic confirmation of payments would need a payment-gateway account (Razorpay/Cashfree/PhonePe PG) — future work.

## Production note
Use a real server instead of `python app.py`, e.g. `waitress-serve --call app:create_app`, set `SECRET_KEY` and
`HTTPS=1`, keep `FLASK_DEBUG` unset. Replace SQLite by PostgreSQL if many users are expected.
