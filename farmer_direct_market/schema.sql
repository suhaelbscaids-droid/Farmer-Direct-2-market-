-- Farmer Direct Market Platform : database definition (SQLite)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    phone         TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('farmer','customer','admin')),
    location      TEXT DEFAULT '',
    address       TEXT DEFAULT '',
    upi_id        TEXT DEFAULT '',            -- farmer's UPI ID (receives UPI payments)
    is_active     INTEGER NOT NULL DEFAULT 1, -- 0 = deactivated by the administrator
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_id    INTEGER NOT NULL REFERENCES users(user_id),
    product_name TEXT NOT NULL,
    category     TEXT NOT NULL CHECK (category IN ('Vegetable','Fruit','Grain','Dairy','Other')),
    price        REAL NOT NULL CHECK (price > 0),
    quantity     INTEGER NOT NULL CHECK (quantity >= 0),
    description  TEXT DEFAULT '',
    harvest_date TEXT DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id      INTEGER NOT NULL REFERENCES users(user_id),
    farmer_id        INTEGER NOT NULL REFERENCES users(user_id),
    order_date       TEXT NOT NULL,
    total_amount     REAL NOT NULL,
    status           TEXT NOT NULL DEFAULT 'Placed'
                     CHECK (status IN ('Placed','Confirmed','Packed','Delivered','Cancelled')),
    payment_method   TEXT NOT NULL CHECK (payment_method IN ('UPI','Card','Cash on Delivery')),
    delivery_address TEXT NOT NULL,
    -- UPI: Pending -> Submitted (customer entered the UTR) -> Paid (farmer verified)
    -- COD: COD -> Paid (when delivered)
    payment_status   TEXT NOT NULL DEFAULT 'Pending',
    payment_ref      TEXT NOT NULL DEFAULT ''  -- 12-digit UPI transaction reference (UTR)
);

CREATE TABLE IF NOT EXISTS order_items (
    item_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL REFERENCES orders(order_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity   INTEGER NOT NULL CHECK (quantity >= 1),
    unit_price REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    review_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(product_id),
    customer_id INTEGER NOT NULL REFERENCES users(user_id),
    rating      INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment     TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE (product_id, customer_id)
);
