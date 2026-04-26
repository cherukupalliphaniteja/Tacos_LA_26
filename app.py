from flask import (
    Flask, render_template, request, redirect,
    url_for, jsonify, flash, session, Response, stream_with_context
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)
from flask_mail import Mail, Message
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
from config import Config
from functools import wraps
from sqlalchemy import text
import json, os, re, stripe, threading, time, logging

# ── App setup ──────────────────────────────────────────────
app = Flask(__name__)
app.config.from_object(Config)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# ── Custom Jinja2 filters ──────────────────────────────────
# (used in templates)
import json as _json_mod

def _fromjson(s):
    try:
        return _json_mod.loads(s)
    except Exception:
        return []

app.jinja_env.filters["fromjson"] = _fromjson

db          = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view          = "login"
login_manager.login_message_category = "info"
mail        = Mail(app)
csrf        = CSRFProtect(app)
limiter     = Limiter(
    get_remote_address,
    app=app,
    default_limits=["500 per day", "100 per hour"],
    storage_uri="memory://",
)

stripe.api_key = app.config.get("STRIPE_SECRET_KEY", "")


# ── Models ─────────────────────────────────────────────────
class User(db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(120), nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    phone         = db.Column(db.String(20),  nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    address       = db.Column(db.String(300), nullable=True)
    created_at    = db.Column(db.DateTime,    default=datetime.utcnow)
    orders        = db.relationship("Order",  backref="customer", lazy=True)

    @property
    def is_active(self):       return True
    @property
    def is_authenticated(self): return True
    @property
    def is_anonymous(self):    return False
    def get_id(self):          return str(self.id)
    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)


class Order(db.Model):
    __tablename__         = "orders"
    id                    = db.Column(db.Integer,  primary_key=True)
    user_id               = db.Column(db.Integer,  db.ForeignKey("users.id"), nullable=True)
    guest_name            = db.Column(db.String(120), nullable=True)
    guest_email           = db.Column(db.String(120), nullable=True)
    guest_phone           = db.Column(db.String(20),  nullable=True)
    items_json            = db.Column(db.Text,    nullable=False)
    subtotal              = db.Column(db.Float,   nullable=False)
    discount              = db.Column(db.Float,   default=0.0)
    delivery_fee          = db.Column(db.Float,   default=0.0)
    total                 = db.Column(db.Float,   nullable=False)
    order_type            = db.Column(db.String(20),  default="pickup")
    delivery_address      = db.Column(db.String(300), nullable=True)
    special_instructions  = db.Column(db.Text,    nullable=True)
    coupon_code           = db.Column(db.String(50),  nullable=True)
    payment_status        = db.Column(db.String(50),  default="pending")
    status                = db.Column(db.String(30),  default="received")
    stripe_session_id     = db.Column(db.String(255), nullable=True)
    created_at            = db.Column(db.DateTime,    default=datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ── Menu data ──────────────────────────────────────────────
PROTEINS = [
    {"id": "asada",   "name": "Asada (Beef)",                 "price": 0},
    {"id": "pastor",  "name": "Pastor (Shepherd Style Pork)", "price": 0},
    {"id": "buche",   "name": "Buche (Pork Belly)",           "price": 0},
    {"id": "suadero", "name": "Suadero (Brisket)",            "price": 0},
    {"id": "cabeza",  "name": "Cabeza (Head)",                "price": 0},
    {"id": "pollo",   "name": "Pollo (Chicken)",              "price": 0},
    {"id": "chorizo", "name": "Chorizo (Pork Sausage)",       "price": 0},
    {"id": "lengua",  "name": "Lengua (Tongue)",              "price": 0},
]

TOPPINGS = [
    {"id": "cebolla",     "name": "Cebolla y Cilantro",                     "price": 0},
    {"id": "guac",        "name": "Guacamole",                              "price": 0},
    {"id": "red_sauce",   "name": "Red Sauce",                              "price": 0},
    {"id": "green_sauce", "name": "Green Sauce",                            "price": 0},
    {"id": "pickled",     "name": "Red Pickled Onion w/ Chopped Habaneros", "price": 0},
    {"id": "grilled_on",  "name": "Grilled Onions",                         "price": 0},
    {"id": "grilled_jal", "name": "Grilled Jalapenos",                      "price": 0},
    {"id": "lime",        "name": "Lime",                                   "price": 0},
]

MENU_CATEGORIES = [
    {
        "id": "tacos", "name": "Tacos",
        "desc": "Classic street tacos with your choice of protein and toppings.",
        "base_price": 2.25,
        "img": "/static/images/tacos.jpeg",
        "has_protein": True, "has_toppings": True, "multi_protein": True, "max_qty": 8,
        "extras": [],
    },
    {
        "id": "quesa_tacos", "name": "Quesa Tacos",
        "desc": "Crispy grilled cheese tacos loaded with your protein of choice.",
        "base_price": 3.25,
        "img": "/static/images/quesa_tacos.jpeg",
        "has_protein": True, "has_toppings": True, "multi_protein": True, "max_qty": 4,
        "extras": [],
    },
    {
        "id": "quesadillas", "name": "Quesadillas",
        "desc": "Melted cheese folded in a fresh tortilla with your protein.",
        "base_price": 10.25,
        "img": "https://tacos-la26.com/wp-content/uploads/2026/02/000001-5.png",
        "has_protein": True, "has_toppings": True, "multi_protein": True, "max_qty": 2,
        "extras": [
            {"id": "extra_queso",  "name": "Extra Queso (Extra Cheese)", "price": 2.00},
            {"id": "frijol",       "name": "Frijol (Beans)",             "price": 1.00},
            {"id": "arroz",        "name": "Arroz (Rice)",               "price": 1.00},
            {"id": "extra_carne",  "name": "Extra Carne (Extra Meat)",   "price": 5.00},
        ],
    },
    {
        "id": "mulitas", "name": "Mulitas",
        "desc": "Double tortilla pressed with cheese and meat — crispy perfection.",
        "base_price": 6.25,
        "img": "https://tacos-la26.com/wp-content/uploads/2026/02/000001.png",
        "has_protein": True, "has_toppings": True, "multi_protein": True, "max_qty": 4,
        "extras": [
            {"id": "extra_queso", "name": "Extra Queso (Extra Cheese)", "price": 1.00},
            {"id": "frijol",      "name": "Frijol (Beans)",             "price": 1.00},
            {"id": "arroz",       "name": "Arroz (Rice)",               "price": 1.00},
        ],
    },
    {
        "id": "burritos", "name": "Burritos",
        "desc": "Oversized flour tortilla packed with rice, beans, protein, and toppings.",
        "base_price": 11.00,
        "img": "https://tacos-la26.com/wp-content/uploads/2026/02/000001-4.png",
        "has_protein": True, "has_toppings": True, "multi_protein": True, "max_qty": 10,
        "extras": [
            {"id": "queso",       "name": "Queso (Cheese)",             "price": 1.00},
            {"id": "extra_queso", "name": "Extra Queso (Extra Cheese)", "price": 2.00},
            {"id": "extra_carne", "name": "Extra Carne (Extra Meat)",   "price": 5.00},
        ],
    },
]

VEGETARIAN_ITEMS = [
    {
        "id": "beans_rice", "name": "B/ Beans & Rice Burrito",
        "desc": "Vegetarian burrito with beans and rice.",
        "base_price": 10.25,
        "img": "https://tacos-la26.com/wp-content/uploads/2026/02/000001-4.png",
        "has_protein": False, "has_toppings": True, "multi_protein": False, "max_qty": 10,
        "extras": [
            {"id": "queso",       "name": "Queso (Cheese)",             "price": 1.00},
            {"id": "extra_queso", "name": "Extra Queso (Extra Cheese)", "price": 2.00},
        ],
    },
    {
        "id": "queso_quesadilla", "name": "Q/ Queso Quesadilla",
        "desc": "Vegetarian quesadilla with cheese.",
        "base_price": 9.25,
        "img": "https://tacos-la26.com/wp-content/uploads/2026/02/000001-5.png",
        "has_protein": False, "has_toppings": True, "multi_protein": False, "max_qty": 2,
        "extras": [
            {"id": "extra_queso", "name": "Extra Queso (Extra Cheese)", "price": 1.00},
            {"id": "frijol",      "name": "Frijol (Beans)",             "price": 1.00},
            {"id": "arroz",       "name": "Arroz (Rice)",               "price": 1.00},
        ],
    },
    {
        "id": "queso_mulita", "name": "M/ Queso Mulita",
        "desc": "Vegetarian mulita with cheese.",
        "base_price": 5.25,
        "img": "https://tacos-la26.com/wp-content/uploads/2026/02/000001.png",
        "has_protein": False, "has_toppings": True, "multi_protein": False, "max_qty": 4,
        "extras": [
            {"id": "extra_queso", "name": "Extra Queso (Extra Cheese)", "price": 1.00},
            {"id": "frijol",      "name": "Frijol (Beans)",             "price": 1.00},
            {"id": "arroz",       "name": "Arroz (Rice)",               "price": 1.00},
        ],
    },
]

SIDES = [
    {"id": "frijol",       "name": "Frijol (Beans)",         "price": 1.00},
    {"id": "arroz",        "name": "Arroz (Rice)",           "price": 1.00},
    {"id": "frijol_arroz", "name": "Frijol + Arroz (Combo)", "price": 2.00},
]

BEVERAGES = [
    {"id": "horchata",    "name": "Agua Fresca — Horchata",    "price": 5.00},
    {"id": "pineapple",   "name": "Agua Fresca — Pineapple",   "price": 5.00},
    {"id": "coca_can",    "name": "Can Coke",                  "price": 2.00},
    {"id": "squirt_can",  "name": "Can Squirt",                "price": 2.00},
    {"id": "coca_glass",  "name": "Glass Coke",                "price": 4.00},
    {"id": "fanta_glass", "name": "Glass Orange Fanta",        "price": 4.00},
]

COUPONS = {
    "TACO10":  {"type": "percent", "value": 10, "label": "10% off"},
    "FIRST5":  {"type": "flat",    "value": 5,  "label": "$5 off first order"},
    "LUNCH20": {"type": "percent", "value": 20, "label": "20% off"},
}


# ── Template context ───────────────────────────────────────
@app.context_processor
def inject_menu():
    return dict(
        menu_categories=MENU_CATEGORIES,
        vegetarian_items=VEGETARIAN_ITEMS,
        proteins=PROTEINS,
        toppings=TOPPINGS,
        sides=SIDES,
        beverages=BEVERAGES,
        sides_img="https://tacos-la26.com/wp-content/uploads/2026/02/000001-2.png",
        beverages_img="https://tacos-la26.com/wp-content/uploads/2026/02/000001-6.png",
        logo_url="/static/images/tpla26logo.png",
        stripe_pub_key=app.config.get("STRIPE_PUBLISHABLE_KEY", ""),
        stripe_enabled=bool(app.config.get("STRIPE_SECRET_KEY", "")),
    )


# ── Admin auth ─────────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


# ── Email helpers ──────────────────────────────────────────
def _send_order_emails(order_id):
    """Run in background thread — needs its own app context."""
    with app.app_context():
        try:
            order = db.session.get(Order, order_id)
            if not order:
                return
            items        = json.loads(order.items_json)
            cust_name    = order.customer.name  if order.customer else (order.guest_name  or "Customer")
            cust_email   = order.customer.email if order.customer else order.guest_email
            cust_phone   = order.customer.phone if order.customer else order.guest_phone

            # Customer confirmation
            if cust_email:
                try:
                    msg = Message(
                        subject=f"Order Confirmed #{order.id} — Tacos LA 26",
                        recipients=[cust_email],
                        html=render_template(
                            "email/order_confirmation.html",
                            order=order, items=items, customer_name=cust_name,
                        ),
                    )
                    mail.send(msg)
                    app.logger.info(f"Confirmation email sent to {cust_email} for order #{order.id}")
                except Exception as e:
                    app.logger.error(f"Customer email failed order #{order.id}: {e}")

            # Restaurant alert
            rest_email = app.config.get("RESTAURANT_EMAIL")
            if rest_email:
                try:
                    msg = Message(
                        subject=f"New Order #{order.id} \u2014 {order.order_type.title()} \u2014 ${order.total:.2f}",
                        recipients=[rest_email],
                        html=render_template(
                            "email/new_order_alert.html",
                            order=order, items=items,
                            customer_name=cust_name,
                            customer_email=cust_email,
                            customer_phone=cust_phone,
                        ),
                    )
                    mail.send(msg)
                    app.logger.info(f"Restaurant alert sent for order #{order.id}")
                except Exception as e:
                    app.logger.error(f"Restaurant email failed order #{order.id}: {e}")
        except Exception as e:
            app.logger.error(f"_send_order_emails error: {e}")


def send_order_emails(order_id):
    """Fire-and-forget email dispatch."""
    t = threading.Thread(target=_send_order_emails, args=(order_id,), daemon=True)
    t.start()


# ── Order builder helper ───────────────────────────────────
def _build_order(data, payment_status="cash"):
    items        = data.get("items", [])
    subtotal     = float(data.get("subtotal", 0))
    discount     = float(data.get("discount", 0))
    order_type   = data.get("order_type", "pickup")
    delivery_fee = 2.99 if order_type == "delivery" else 0.0
    total        = round(max(0, subtotal - discount) + delivery_fee, 2)
    order_status = "pending_payment" if payment_status == "pending" else "received"

    return Order(
        user_id              = current_user.id if current_user.is_authenticated else None,
        guest_name           = data.get("name", ""),
        guest_email          = data.get("email", ""),
        guest_phone          = data.get("phone", ""),
        items_json           = json.dumps(items),
        subtotal             = subtotal,
        discount             = discount,
        delivery_fee         = delivery_fee,
        total                = total,
        order_type           = order_type,
        delivery_address     = data.get("address", ""),
        special_instructions = data.get("instructions", ""),
        coupon_code          = data.get("coupon_code", ""),
        payment_status       = payment_status,
        status               = order_status,
    )


# ── Page routes ────────────────────────────────────────────
@app.route("/")
def home():
    return render_template("home.html")


@app.route("/menu")
def menu():
    return render_template("menu.html")


@app.route("/build/<item_id>")
def build(item_id):
    all_items = MENU_CATEGORIES + VEGETARIAN_ITEMS
    item = next((c for c in all_items if c["id"] == item_id), None)
    if not item:
        flash("Item not found.", "error")
        return redirect(url_for("menu"))
    return render_template("build.html", item=item)


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/offers")
def offers():
    return render_template("offers.html")


@app.route("/cart")
def cart():
    return render_template("cart.html")


@app.route("/checkout")
def checkout():
    return render_template("checkout.html")


@app.route("/success")
def success():
    order_id = request.args.get("order_id", type=int)
    order    = None
    items    = []
    if order_id:
        order = db.session.get(Order, order_id)
        if order:
            items = json.loads(order.items_json)
    return render_template("success.html", order=order, items=items)


# ── Auth routes ────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user     = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            flash("Welcome back!", "success")
            nxt = request.args.get("next")
            return redirect(nxt or url_for("home"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        email    = request.form.get("email", "").strip().lower()
        phone    = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")
        if not name or not email or not password:
            flash("All fields are required.", "error")
            return redirect(url_for("signup"))
        if password != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("signup"))
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return redirect(url_for("signup"))
        user = User(name=name, email=email, phone=phone)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Account created!", "success")
        return redirect(url_for("home"))
    return render_template("signup.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("home"))


@app.route("/orders")
@login_required
def orders():
    user_orders = Order.query.filter_by(user_id=current_user.id)\
        .order_by(Order.created_at.desc()).all()
    return render_template("orders.html", orders=user_orders)


# ── API: coupon ────────────────────────────────────────────
@app.route("/api/apply-coupon", methods=["POST"])
@limiter.limit("20 per minute")
def apply_coupon():
    data     = request.get_json()
    code     = (data.get("code", "") or "").strip().upper()
    subtotal = float(data.get("subtotal", 0))
    if code in COUPONS:
        c    = COUPONS[code]
        disc = round(subtotal * c["value"] / 100, 2) if c["type"] == "percent" else min(c["value"], subtotal)
        return jsonify({"success": True, "discount": disc, "label": c["label"]})
    return jsonify({"success": False, "error": "Invalid coupon code."})


# ── API: place order (cash / pay-on-arrival) ───────────────
@app.route("/api/place-order", methods=["POST"])
@limiter.limit("10 per minute")
def place_order():
    try:
        data  = request.get_json()
        items = data.get("items", [])
        if not items:
            return jsonify({"error": "Cart is empty."}), 400

        # Validate protein quantities per item
        all_cats = MENU_CATEGORIES + VEGETARIAN_ITEMS
        for cart_item in items:
            cat = next((c for c in all_cats if c["id"] == cart_item.get("category")), None)
            if cat and cat.get("has_protein"):
                total_prot = 0
                for p_label in cart_item.get("proteins", []):
                    m = re.search(r'x(\d+)$', p_label)
                    total_prot += int(m.group(1)) if m else 1
                if total_prot > cat["max_qty"]:
                    return jsonify({"error": f"Max {cat['max_qty']} {cat['name']} per order."}), 400

        order = _build_order(data, payment_status="cash")
        db.session.add(order)
        db.session.commit()

        send_order_emails(order.id)

        return jsonify({
            "success": True,
            "order_id": order.id,
            "redirect": url_for("success", order_id=order.id),
        })
    except Exception as e:
        app.logger.error(f"place_order error: {e}")
        return jsonify({"error": "Something went wrong. Please try again."}), 500


# ── API: Stripe checkout session ───────────────────────────
@app.route("/api/checkout/stripe", methods=["POST"])
@limiter.limit("10 per minute")
def stripe_checkout():
    if not app.config.get("STRIPE_SECRET_KEY"):
        return jsonify({"error": "Online payment not configured."}), 503
    try:
        data  = request.get_json()
        items = data.get("items", [])
        if not items:
            return jsonify({"error": "Cart is empty."}), 400

        # Persist order first (pending payment)
        order = _build_order(data, payment_status="pending")
        db.session.add(order)
        db.session.commit()

        # Build Stripe line items
        line_items = []
        for item in items:
            label = item["name"]
            if item.get("proteins"):
                label += " (" + ", ".join(item["proteins"]) + ")"
            line_items.append({
                "price_data": {
                    "currency":     "usd",
                    "product_data": {"name": label},
                    "unit_amount":  int(round(item["unitPrice"] * 100)),
                },
                "quantity": item["qty"],
            })

        if order.delivery_fee > 0:
            line_items.append({
                "price_data": {
                    "currency":     "usd",
                    "product_data": {"name": "Delivery Fee"},
                    "unit_amount":  int(round(order.delivery_fee * 100)),
                },
                "quantity": 1,
            })

        session_params = {
            "payment_method_types": ["card"],
            "line_items":           line_items,
            "mode":                 "payment",
            "success_url":          url_for("success", order_id=order.id, _external=True),
            "cancel_url":           url_for("checkout", _external=True),
            "metadata":             {"order_id": str(order.id)},
            "customer_email":       (order.customer.email if order.customer else order.guest_email) or None,
        }

        # Apply discount as a one-off Stripe coupon
        if order.discount > 0:
            coupon = stripe.Coupon.create(
                amount_off=int(round(order.discount * 100)),
                currency="usd",
                duration="once",
            )
            session_params["discounts"] = [{"coupon": coupon.id}]

        checkout_session = stripe.checkout.Session.create(**session_params)
        order.stripe_session_id = checkout_session.id
        db.session.commit()

        return jsonify({"url": checkout_session.url})

    except stripe.error.StripeError as e:
        app.logger.error(f"Stripe error: {e}")
        return jsonify({"error": "Payment service error. Please try again."}), 502
    except Exception as e:
        app.logger.error(f"stripe_checkout error: {e}")
        return jsonify({"error": "Something went wrong."}), 500


# ── Stripe webhook ─────────────────────────────────────────
@app.route("/stripe/webhook", methods=["POST"])
@csrf.exempt
def stripe_webhook():
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")
    secret     = app.config.get("STRIPE_WEBHOOK_SECRET", "")

    try:
        if secret:
            event = stripe.Webhook.construct_event(payload, sig_header, secret)
        else:
            event = json.loads(payload)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        app.logger.warning(f"Webhook signature error: {e}")
        return jsonify({"error": "Invalid payload"}), 400

    if event.get("type") == "checkout.session.completed":
        sess_obj = event["data"]["object"]
        order_id = (sess_obj.get("metadata") or {}).get("order_id")
        if order_id:
            order = db.session.get(Order, int(order_id))
            if order and order.payment_status == "pending":
                order.payment_status = "paid"
                order.status         = "received"
                db.session.commit()
                send_order_emails(order.id)
                app.logger.info(f"Order #{order.id} payment confirmed via Stripe webhook")

    return jsonify({"status": "ok"})


# ── Admin: login / logout ──────────────────────────────────
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_orders"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if (username == app.config["ADMIN_USERNAME"] and
                password == app.config["ADMIN_PASSWORD"]):
            session["admin_logged_in"] = True
            session.permanent = True
            app.logger.info(f"Admin login from {request.remote_addr}")
            return redirect(url_for("admin_orders"))
        flash("Invalid credentials.", "error")
        app.logger.warning(f"Failed admin login attempt from {request.remote_addr}")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


# ── Admin: dashboard ───────────────────────────────────────
@app.route("/admin/orders")
@admin_required
def admin_orders():
    today   = date.today()
    orders  = Order.query.order_by(Order.created_at.desc()).limit(200).all()
    today_orders  = [o for o in orders if o.created_at.date() == today]
    today_revenue = sum(o.total for o in today_orders if o.payment_status in ("paid", "cash"))
    pending_count = sum(1 for o in orders if o.status in ("received", "preparing"))
    last_id = orders[0].id if orders else 0

    return render_template(
        "admin_orders.html",
        orders=orders,
        last_order_id=last_id,
        stats={
            "today_orders":  len(today_orders),
            "today_revenue": today_revenue,
            "pending":       pending_count,
        },
    )


# ── Admin: SSE real-time stream ────────────────────────────
@app.route("/admin/stream")
@admin_required
def admin_stream():
    last_id = request.args.get("last_id", 0, type=int)

    def generate(last_id):
        while True:
            try:
                new_orders = Order.query.filter(Order.id > last_id)\
                    .order_by(Order.id).all()
                for o in new_orders:
                    cust  = o.customer.name if o.customer else (o.guest_name or "Guest")
                    phone = o.customer.phone if o.customer else (o.guest_phone or "")
                    email = o.customer.email if o.customer else (o.guest_email or "")
                    payload = {
                        "id":           o.id,
                        "customer":     cust,
                        "phone":        phone,
                        "email":        email,
                        "order_type":   o.order_type,
                        "total":        o.total,
                        "items":        json.loads(o.items_json),
                        "status":       o.status,
                        "payment":      o.payment_status,
                        "instructions": o.special_instructions or "",
                        "time":         o.created_at.strftime("%I:%M %p"),
                        "date":         o.created_at.strftime("%b %d"),
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                    last_id = o.id
                yield ": heartbeat\n\n"
            except Exception as e:
                app.logger.error(f"SSE stream error: {e}")
                yield ": error\n\n"
            time.sleep(3)

    resp = Response(
        stream_with_context(generate(last_id)),
        mimetype="text/event-stream",
    )
    resp.headers["Cache-Control"]    = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Connection"]       = "keep-alive"
    return resp


# ── Admin: update order status ─────────────────────────────
@app.route("/api/admin/order-status", methods=["POST"])
@admin_required
def update_order_status():
    data     = request.get_json()
    order_id = data.get("order_id")
    status   = data.get("status")
    allowed  = {"received", "preparing", "ready", "completed", "cancelled"}
    if status not in allowed:
        return jsonify({"error": "Invalid status."}), 400
    order = db.session.get(Order, order_id)
    if not order:
        return jsonify({"error": "Order not found."}), 404
    order.status = status
    db.session.commit()
    app.logger.info(f"Order #{order_id} status → {status}")
    return jsonify({"success": True})


# ── Init DB ────────────────────────────────────────────────
with app.app_context():
    db.create_all()
    # Add status column for existing databases that predate this migration
    with db.engine.connect() as conn:
        for stmt in [
            "ALTER TABLE orders ADD COLUMN status VARCHAR(30) DEFAULT 'received'",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # column already exists


if __name__ == "__main__":
    app.run(debug=True, threaded=True)
