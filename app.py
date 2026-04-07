from flask import (
    Flask, render_template, request, redirect,
    url_for, jsonify, flash, session
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from config import Config
import json
import os
import stripe

# ── App setup ──────────────────────────────────────────────
app = Flask(__name__)
app.config.from_object(Config)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"

stripe.api_key = app.config.get("STRIPE_SECRET_KEY")

# ── Models ─────────────────────────────────────────────────
class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    address = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    orders = db.relationship("Order", backref="customer", lazy=True)

    # flask-login fields
    @property
    def is_active(self):
        return True

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    guest_name = db.Column(db.String(120), nullable=True)
    guest_email = db.Column(db.String(120), nullable=True)
    guest_phone = db.Column(db.String(20), nullable=True)
    items_json = db.Column(db.Text, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)
    discount = db.Column(db.Float, default=0.0)
    delivery_fee = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, nullable=False)
    order_type = db.Column(db.String(20), default="pickup")  # pickup / delivery
    delivery_address = db.Column(db.String(300), nullable=True)
    special_instructions = db.Column(db.Text, nullable=True)
    coupon_code = db.Column(db.String(50), nullable=True)
    payment_status = db.Column(db.String(50), default="pending")
    stripe_session_id = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


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
    {"id": "lengua",  "name": "Lengua (Tongue)",              "price": 1.00},
]

TOPPINGS = [
    {"id": "cebolla",    "name": "Cebolla y Cilantro",                      "price": 0},
    {"id": "guac",       "name": "Guacamole",                               "price": 0},
    {"id": "red_sauce",  "name": "Red Sauce",                               "price": 0},
    {"id": "green_sauce","name": "Green Sauce",                              "price": 0},
    {"id": "pickled",    "name": "Red Pickled Onion w/ Chopped Habaneros",   "price": 0},
    {"id": "grilled_on", "name": "Grilled Onions",                          "price": 0},
    {"id": "grilled_jal","name": "Grilled Jalapenos",                       "price": 0},
    {"id": "lime",       "name": "Lime",                                     "price": 0},
]

MENU_CATEGORIES = [
    {
        "id": "tacos",
        "name": "Tacos",
        "desc": "Classic street tacos with your choice of protein and toppings.",
        "base_price": 2.25,
        "img": "https://tacos-la26.com/wp-content/uploads/2026/02/000001-7.png",
        "has_protein": True,
        "has_toppings": True,
        "multi_protein": True,
    },
    {
        "id": "quesa_tacos",
        "name": "Quesa Tacos",
        "desc": "Crispy grilled cheese tacos loaded with your protein of choice.",
        "base_price": 3.25,
        "img": "https://tacos-la26.com/wp-content/uploads/2026/02/000001-1.png",
        "has_protein": True,
        "has_toppings": True,
        "multi_protein": True,
    },
    {
        "id": "quesadillas",
        "name": "Quesadillas",
        "desc": "Melted cheese folded in a fresh tortilla with your protein.",
        "base_price": 5.25,
        "img": "https://tacos-la26.com/wp-content/uploads/2026/02/000001-5.png",
        "has_protein": True,
        "has_toppings": True,
        "multi_protein": True,
    },
    {
        "id": "mulitas",
        "name": "Mulitas",
        "desc": "Double tortilla pressed with cheese and meat -- crispy perfection.",
        "base_price": 6.25,
        "img": "https://tacos-la26.com/wp-content/uploads/2026/02/000001.png",
        "has_protein": True,
        "has_toppings": True,
        "multi_protein": True,
    },
    {
        "id": "burritos",
        "name": "Burritos",
        "desc": "Oversized flour tortilla packed with rice, beans, protein, and toppings.",
        "base_price": 11.00,
        "img": "https://tacos-la26.com/wp-content/uploads/2026/02/000001-4.png",
        "has_protein": True,
        "has_toppings": True,
        "multi_protein": True,
    },
]

SIDES = [
    {"id": "frijol",      "name": "Frijol (Beans)",          "price": 1.00},
    {"id": "arroz",       "name": "Arroz (Rice)",            "price": 1.00},
    {"id": "frijol_arroz","name": "Frijol + Arroz (Combo)",  "price": 2.00},
]

BEVERAGES = [
    {"id": "horchata",   "name": "Agua Fresca (Horchata)",   "price": 3.00},
    {"id": "pineapple",  "name": "Agua Fresca (Pineapple)",  "price": 3.00},
    {"id": "coca_glass", "name": "Coca Cola (Glass)",        "price": 2.50},
    {"id": "coca_can",   "name": "Coca Cola (Can)",          "price": 2.00},
    {"id": "fanta_glass", "name": "Orange Fanta (Glass)",    "price": 2.50},
    {"id": "squirt_can", "name": "Squirt (Can)",             "price": 2.00},
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
        proteins=PROTEINS,
        toppings=TOPPINGS,
        sides=SIDES,
        beverages=BEVERAGES,
        sides_img="https://tacos-la26.com/wp-content/uploads/2026/02/000001-2.png",
        beverages_img="https://tacos-la26.com/wp-content/uploads/2026/02/000001-6.png",
        logo_url="https://tacos-la26.com/wp-content/uploads/2024/07/cropped-tacosla26logo.webp",
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
    item = next((c for c in MENU_CATEGORIES if c["id"] == item_id), None)
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
    order_id = request.args.get("order_id")
    return render_template("success.html", order_id=order_id)


# ── Auth routes ────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
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
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
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


# ── API routes ─────────────────────────────────────────────
@app.route("/api/apply-coupon", methods=["POST"])
def apply_coupon():
    data = request.get_json()
    code = (data.get("code", "") or "").strip().upper()
    subtotal = float(data.get("subtotal", 0))
    if code in COUPONS:
        c = COUPONS[code]
        if c["type"] == "percent":
            disc = round(subtotal * c["value"] / 100, 2)
        else:
            disc = min(c["value"], subtotal)
        return jsonify({"success": True, "discount": disc, "label": c["label"]})
    return jsonify({"success": False, "error": "Invalid coupon code."})


@app.route("/api/place-order", methods=["POST"])
def place_order():
    try:
        data = request.get_json()
        items = data.get("items", [])
        if not items:
            return jsonify({"error": "Cart is empty."}), 400

        subtotal = float(data.get("subtotal", 0))
        discount = float(data.get("discount", 0))
        order_type = data.get("order_type", "pickup")
        delivery_fee = 2.99 if order_type == "delivery" else 0.0
        total = round(max(0, subtotal - discount) + delivery_fee, 2)

        order = Order(
            user_id=current_user.id if current_user.is_authenticated else None,
            guest_name=data.get("name", ""),
            guest_email=data.get("email", ""),
            guest_phone=data.get("phone", ""),
            items_json=json.dumps(items),
            subtotal=subtotal,
            discount=discount,
            delivery_fee=delivery_fee,
            total=total,
            order_type=order_type,
            delivery_address=data.get("address", ""),
            special_instructions=data.get("instructions", ""),
            coupon_code=data.get("coupon_code", ""),
            payment_status="confirmed",
        )
        db.session.add(order)
        db.session.commit()

        return jsonify({
            "success": True,
            "order_id": order.id,
            "redirect": url_for("success", order_id=order.id),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Admin orders (simple) ─────────────────────────────────
@app.route("/admin/orders")
def admin_orders():
    all_orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template("admin_orders.html", orders=all_orders)


# ── Init DB + run ──────────────────────────────────────────
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)
