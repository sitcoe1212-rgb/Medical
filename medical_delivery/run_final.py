import os
import uuid
from functools import wraps
import math
from datetime import datetime

from flask import (
    Flask, render_template, redirect, url_for, flash,
    request, current_app, send_from_directory, jsonify
)
from flask_login import (
    login_user, logout_user,
    login_required, current_user
)
from werkzeug.utils import secure_filename

from app.app_config_final import Config
from app.models import (
    User, Store, Prescription, Order,
    Payment, Notification
)
from sqlalchemy import func
from app.utils.security import hash_password, check_password

# Email and Payment imports
from flask_mail import Mail, Message
import razorpay

# --- extensions -------------------------------------------------------------

from app import db, login_manager, migrate

# create the Flask application
app = Flask(__name__, template_folder="app/templates", static_folder="app/static")
app.config.from_object(Config)

# enable debug logging for troubleshooting
app.logger.setLevel('DEBUG')

# initialise extensions (objects imported from app package)
db.init_app(app)
login_manager.init_app(app)
migrate.init_app(app, db)

# Initialize Flask-Mail
mail = Mail(app)

# Initialize Razorpay only if keys are provided
razorpay_key_id = os.getenv('RAZORPAY_KEY_ID', '')
razorpay_key_secret = os.getenv('RAZORPAY_KEY_SECRET', '')

if razorpay_key_id and razorpay_key_secret:
    razorpay_client = razorpay.Client(auth=(razorpay_key_id, razorpay_key_secret))
    app.logger.info("Razorpay initialized successfully")
else:
    razorpay_client = None
    app.logger.warning("Razorpay not configured - payment links will be disabled")

login_manager.login_view = "auth.login"

# Helper function to check if payments are enabled
def is_payment_enabled():
    """Check if online payments are enabled"""
    return razorpay_client is not None and razorpay_key_id and razorpay_key_secret

# user loader
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# =========================
# Email Helper Functions
# =========================

def send_email(to_email, subject, html_body):
    """Send email using Flask-Mail"""
    try:
        if not to_email or '@' not in to_email:
            app.logger.warning(f"Invalid email address: {to_email}")
            return False

        # Log email attempt with details
        app.logger.info("=" * 50)
        app.logger.info("📧 EMAIL SEND ATTEMPT")
        app.logger.info(f"To: {to_email}")
        app.logger.info(f"Subject: {subject}")
        app.logger.info(f"From: {app.config.get('MAIL_DEFAULT_SENDER')}")
        app.logger.info(f"Server: {app.config.get('MAIL_SERVER')}:{app.config.get('MAIL_PORT')}")
        app.logger.info(f"Username: {app.config.get('MAIL_USERNAME')}")
        app.logger.info(f"Password: {'✓ SET' if app.config.get('MAIL_PASSWORD') else '✗ NOT SET'}")
        app.logger.info("=" * 50)

        msg = Message(
            subject=subject,
            sender=os.getenv('MAIL_DEFAULT_SENDER', 'noreply@mediorder.com'),
            recipients=[to_email]
        )
        msg.html = html_body
        app.logger.info("⏳ Sending email...")
        mail.send(msg)
        app.logger.info(f"✅ Email sent successfully to {to_email}")
        return True
    except Exception as e:
        app.logger.error(f"❌ Failed to send email: {str(e)}")
        app.logger.error(f"Error type: {type(e).__name__}")
        
        # Provide helpful error messages
        error_msg = str(e).lower()
        if "535" in error_msg:
            app.logger.error("💡 Authentication failed. If using Gmail:")
            app.logger.error("   1. Enable 2-Step Verification")
            app.logger.error("   2. Generate App Password at https://myaccount.google.com/apppasswords")
            app.logger.error("   3. Use that 16-character password (without spaces)")
        elif "timeout" in error_msg:
            app.logger.error("💡 Connection timeout. Check your internet connection.")
        elif "tls" in error_msg:
            app.logger.error("💡 TLS issue. Try using SSL on port 465:")
            app.logger.error("   MAIL_PORT=465")
            app.logger.error("   MAIL_USE_SSL=True")
            app.logger.error("   MAIL_USE_TLS=False")
        
        return False

def create_razorpay_payment_link(order):
    """Create Razorpay payment link for an order"""
    if not is_payment_enabled():
        app.logger.warning("Razorpay not configured - skipping payment link creation")
        return None
    
    try:
        payment_link_data = {
            "amount": int(order.total_amount * 100),  # Amount in paise
            "currency": "INR",
            "accept_partial": False,
            "description": f"Payment for Order #{order.tracking_id}",
            "customer": {
                "name": order.user.full_name,
                "email": order.user.email or f"{order.user.phone}@temp.com",
                "contact": order.user.phone
            },
            "notify": {
                "sms": True,
                "email": True
            },
            "reminder_enable": True,
            "callback_url": url_for('payment.razorpay_callback', order_id=order.id, _external=True),
            "callback_method": "get"
        }

        payment_link = razorpay_client.payment_link.create(payment_link_data)

        # Store payment link in database
        payment = Payment(
            order_id=order.id,
            transaction_id=payment_link['id'],
            payment_gateway='RAZORPAY',
            amount=order.total_amount,
            status='INITIATED'
        )
        db.session.add(payment)
        db.session.commit()

        return payment_link['short_url']

    except Exception as e:
        app.logger.error(f"Failed to create Razorpay payment link: {str(e)}")
        return None


def send_order_confirmation_email(order):
    """Send order confirmation email to customer"""
    app.logger.info("=" * 60)
    app.logger.info("🔔 TRIGGERED: send_order_confirmation_email")
    app.logger.info(f"Order ID: {order.id}")
    app.logger.info(f"Customer: {order.user.full_name}")
    app.logger.info(f"Customer Email: {order.user.email}")
    app.logger.info("=" * 60)
    
    if not order.user.email:
        app.logger.error(f"❌ No email address for user {order.user.id}")
        return
    
    store = order.store

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
            .header {{ background: linear-gradient(135deg, #16a34a, #22c55e); color: white; padding: 30px; text-align: center; }}
            .content {{ padding: 30px; }}
            .order-id {{ background: #f0fdf4; border: 2px solid #86efac; border-radius: 8px; padding: 15px; margin: 20px 0; text-align: center; }}
            .order-id h2 {{ margin: 0; color: #16a34a; font-size: 24px; }}
            .details {{ background: #f9fafb; padding: 20px; border-radius: 8px; margin: 20px 0; }}
            .detail-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #e5e7eb; }}
            .footer {{ background: #f9fafb; padding: 20px; text-align: center; color: #6b7280; font-size: 12px; }}
            .btn {{ display: inline-block; background: #16a34a; color: white; padding: 12px 30px; text-decoration: none; border-radius: 8px; margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🎉 Order Confirmed!</h1>
                <p>Your medicine order has been placed successfully</p>
            </div>
            <div class="content">
                <div class="order-id">
                    <p style="margin: 0; color: #6b7280; font-size: 14px;">Order ID</p>
                    <h2>{order.tracking_id}</h2>
                </div>

                <div class="details">
                    <h3 style="margin-top: 0;">📋 Order Details</h3>
                    <div class="detail-row">
                        <span><strong>Store:</strong></span>
                        <span>{store.name if store else 'N/A'}</span>
                    </div>
                    <div class="detail-row">
                        <span><strong>Delivery Address:</strong></span>
                        <span>{order.delivery_address}</span>
                    </div>
                    <div class="detail-row">
                        <span><strong>Payment Method:</strong></span>
                        <span>{order.payment_method}</span>
                    </div>
                    <div class="detail-row">
                        <span><strong>Status:</strong></span>
                        <span style="color: #16a34a; font-weight: 600;">Order Placed</span>
                    </div>
                </div>

                <p style="color: #6b7280; text-align: center;">
                    The pharmacy will review your prescription and confirm your order shortly.
                    Expected delivery: <strong>2-4 hours</strong>
                </p>
            </div>
            <div class="footer">
                <p>© 2026 MediOrder - Medicine Delivery Service</p>
                <p>This is an automated email. Please do not reply.</p>
            </div>
        </div>
    </body>
    </html>
    """

    result = send_email(
        order.user.email,
        f"Order Confirmed - {order.tracking_id}",
        html_body
    )
    
    if result:
        app.logger.info(f"✅ Order confirmation email sent to {order.user.email}")
    else:
        app.logger.error(f"❌ Failed to send order confirmation email to {order.user.email}")


def send_order_accepted_email(order, payment_link=None):
    """Send order accepted email to customer with payment link"""
    app.logger.info("=" * 60)
    app.logger.info("🔔 TRIGGERED: send_order_accepted_email")
    app.logger.info(f"Order ID: {order.id}")
    app.logger.info(f"Customer: {order.user.full_name}")
    app.logger.info(f"Customer Email: {order.user.email}")
    app.logger.info(f"Payment Method: {order.payment_method}")
    app.logger.info(f"Payment Link: {payment_link}")
    app.logger.info("=" * 60)
    
    if not order.user.email:
        app.logger.error(f"❌ No email address for user {order.user.id}")
        return
    
    store = order.store

    payment_section = ""
    if order.payment_method == 'ONLINE' and payment_link:
        payment_section = f"""
        <div style="background: #eff6ff; border: 2px solid #3b82f6; border-radius: 8px; padding: 20px; margin: 20px 0; text-align: center;">
            <h3 style="color: #1e40af; margin-top: 0;">💳 Payment Required</h3>
            <p>Please complete the payment to proceed with your order.</p>
            <p style="font-size: 18px; font-weight: 600; color: #1e40af;">Amount: ₹{order.total_amount:.2f}</p>
            <a href="{payment_link}" class="btn" style="background: #2563eb; color: white; padding: 12px 30px; text-decoration: none; border-radius: 8px; display: inline-block;">Pay Now</a>
            <p style="font-size: 12px; color: #6b7280; margin-top: 15px;">
                Payment link expires in 24 hours
            </p>
        </div>
        """
    else:
        payment_section = f"""
        <div style="background: #fef9c3; border: 2px solid #fde047; border-radius: 8px; padding: 20px; margin: 20px 0; text-align: center;">
            <h3 style="color: #713f12; margin-top: 0;">💰 Cash on Delivery</h3>
            <p>Amount to pay: <strong>₹{order.total_amount:.2f}</strong></p>
            <p style="font-size: 14px; color: #6b7280;">Please keep exact change ready</p>
        </div>
        """

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
            .header {{ background: linear-gradient(135deg, #16a34a, #22c55e); color: white; padding: 30px; text-align: center; }}
            .content {{ padding: 30px; }}
            .order-id {{ background: #f0fdf4; border: 2px solid #86efac; border-radius: 8px; padding: 15px; margin: 20px 0; text-align: center; }}
            .order-id h2 {{ margin: 0; color: #16a34a; font-size: 24px; }}
            .details {{ background: #f9fafb; padding: 20px; border-radius: 8px; margin: 20px 0; }}
            .detail-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #e5e7eb; }}
            .footer {{ background: #f9fafb; padding: 20px; text-align: center; color: #6b7280; font-size: 12px; }}
            .btn {{ display: inline-block; background: #16a34a; color: white; padding: 12px 30px; text-decoration: none; border-radius: 8px; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1> Order Accepted!</h1>
                <p>Your order has been confirmed by the pharmacy</p>
            </div>
            <div class="content">
                <div class="order-id">
                    <p style="margin: 0; color: #6b7280; font-size: 14px;">Order ID</p>
                    <h2>{order.tracking_id}</h2>
                </div>

                <div class="details">
                    <h3 style="margin-top: 0;">📋 Order Summary</h3>
                    <div class="detail-row">
                        <span><strong>Medicine Cost:</strong></span>
                        <span>₹{order.medicine_total:.2f}</span>
                    </div>
                    <div class="detail-row">
                        <span><strong>Delivery Charge:</strong></span>
                        <span>₹{order.delivery_charge:.2f}</span>
                    </div>
                    <div class="detail-row" style="border-bottom: none; font-size: 18px; font-weight: 600; color: #16a34a;">
                        <span>Total Amount:</span>
                        <span>₹{order.total_amount:.2f}</span>
                    </div>
                </div>

                {payment_section}

                <div style="background: #f0fdf4; border-left: 4px solid #16a34a; padding: 15px; margin: 20px 0;">
                    <p style="margin: 0; color: #14532d;">
                        <strong>🏪 {store.name if store else 'Store'}</strong><br>
                        📍 {store.address if store else 'N/A'}<br>
                        📞 {store.phone if store else 'N/A'}
                    </p>
                </div>

                <p style="color: #6b7280; text-align: center; font-size: 14px;">
                    Your medicine is being prepared and will be delivered soon!
                </p>
            </div>
            <div class="footer">
                <p>© 2024 MediOrder - Medicine Delivery Service</p>
                <p>This is an automated email. Please do not reply.</p>
            </div>
        </div>
    </body>
    </html>
    """

    result = send_email(
        order.user.email,
        f"Order Accepted - Payment {order.payment_method} - {order.tracking_id}",
        html_body
    )
    
    if result:
        app.logger.info(f"✅ Order accepted email sent to {order.user.email}")
    else:
        app.logger.error(f"❌ Failed to send order accepted email to {order.user.email}")


def send_store_new_order_email(order, store):
    """Send new order notification to store"""
    if not store or not store.user or not store.user.email:
        app.logger.warning(f"Store or store user email missing for order {order.id}")
        return
    
    app.logger.info("=" * 60)
    app.logger.info("🔔 TRIGGERED: send_store_new_order_email")
    app.logger.info(f"Order ID: {order.id}")
    app.logger.info(f"Store: {store.name}")
    app.logger.info(f"Store Email: {store.user.email}")
    app.logger.info("=" * 60)

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
            .header {{ background: linear-gradient(135deg, #2563eb, #3b82f6); color: white; padding: 30px; text-align: center; }}
            .content {{ padding: 30px; }}
            .alert {{ background: #fef9c3; border: 2px solid #fde047; border-radius: 8px; padding: 20px; margin: 20px 0; text-align: center; }}
            .details {{ background: #f9fafb; padding: 20px; border-radius: 8px; margin: 20px 0; }}
            .detail-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #e5e7eb; }}
            .footer {{ background: #f9fafb; padding: 20px; text-align: center; color: #6b7280; font-size: 12px; }}
            .btn {{ display: inline-block; background: #2563eb; color: white; padding: 12px 30px; text-decoration: none; border-radius: 8px; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔔 New Order Received!</h1>
                <p>A customer has placed a new order</p>
            </div>
            <div class="content">
                <div class="alert">
                    <h2 style="margin: 0; color: #713f12;">Order #{order.tracking_id}</h2>
                    <p style="margin: 10px 0 0 0; color: #854d0e;">Waiting for your confirmation</p>
                </div>

                <div class="details">
                    <h3 style="margin-top: 0;">👤 Customer Details</h3>
                    <div class="detail-row">
                        <span><strong>Name:</strong></span>
                        <span>{order.user.full_name}</span>
                    </div>
                    <div class="detail-row">
                        <span><strong>Phone:</strong></span>
                        <span>{order.user.phone}</span>
                    </div>
                    <div class="detail-row">
                        <span><strong>Delivery Address:</strong></span>
                        <span>{order.delivery_address}</span>
                    </div>
                    <div class="detail-row">
                        <span><strong>Payment Method:</strong></span>
                        <span>{order.payment_method}</span>
                    </div>
                </div>

                <div style="text-align: center;">
                    <a href="{url_for('store.store_dashboard', _external=True)}" class="btn">
                        View Order & Accept
                    </a>
                </div>

                <p style="color: #6b7280; text-align: center; font-size: 14px; margin-top: 20px;">
                    Please review the prescription and accept the order as soon as possible.
                </p>
            </div>
            <div class="footer">
                <p>© 2026 MediOrder - Store Management Portal</p>
            </div>
        </div>
    </body>
    </html>
    """

    result = send_email(
        store.user.email,
        f"🔔 New Order #{order.tracking_id}",
        html_body
    )
    
    if result:
        app.logger.info(f"✅ Store notification email sent to {store.user.email}")
    else:
        app.logger.error(f"❌ Failed to send store notification email to {store.user.email}")


# =========================
# Helper Functions
# =========================

def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two points in km using Haversine formula"""
    R = 6371  # Earth's radius in kilometers

    # Convert latitude and longitude from degrees to radians
    lat1, lon1, lat2, lon2 = map(math.radians, [float(lat1), float(lon1), float(lat2), float(lon2)])

    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    distance = R * c

    return round(distance, 2)

def find_nearest_stores(user_lat, user_lon, limit=10):
    """Find nearest stores to user location"""
    if not user_lat or not user_lon:
        # If no user location, return all active stores
        return Store.query.filter_by(is_active=True).all()

    # Get all active stores
    stores = Store.query.filter_by(is_active=True).all()

    # Calculate distance for each store
    for store in stores:
        store.distance = calculate_distance(
            user_lat, user_lon,
            store.latitude, store.longitude
        )

    # Sort by distance and return
    stores.sort(key=lambda x: x.distance)
    return stores[:limit]

def find_nearest_store(user_lat, user_lon):
    """Find single nearest store to user location"""
    stores = find_nearest_stores(user_lat, user_lon, limit=1)
    if stores:
        store = stores[0]
        return store, store.delivery_charge
    return None, 50.0  # Default delivery charge if no store found


# basic home route to avoid 404 at /
@app.route("/")
def index():
    if current_user.is_authenticated:
        if current_user.role == "customer":
            return redirect(url_for("user.dashboard"))
        elif current_user.role == "store":
            return redirect(url_for("store.store_dashboard"))
        elif current_user.role == "admin":
            return redirect(url_for("admin.admin_dashboard"))
    return redirect(url_for("auth.login"))

# --- blueprints -------------------------------------------------------------

from flask import Blueprint

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
user_bp = Blueprint("user", __name__, url_prefix="/user")
store_bp = Blueprint("store", __name__, url_prefix="/store")
order_bp = Blueprint("order", __name__, url_prefix="/order")
payment_bp = Blueprint("payment", __name__, url_prefix="/payment")
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# =========================
# auth routes
# =========================

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name")
        phone = request.form.get("phone")
        email = request.form.get("email")
        password = request.form.get("password")
        role = request.form.get("role")

        # Check if user exists
        existing_user = User.query.filter_by(phone=phone).first()
        if existing_user:
            flash("Phone number already registered!", "danger")
            return redirect(url_for("auth.register"))

        new_user = User(
            full_name=full_name,
            phone=phone,
            email=email,
            password_hash=hash_password(password),
            role=role
        )

        db.session.add(new_user)
        db.session.commit()

        flash("Registration successful! Please login.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = request.form.get("phone")
        password = request.form.get("password")

        user = User.query.filter_by(phone=phone).first()

        if user and check_password(password, user.password_hash):
            login_user(user)
            flash("Login successful!", "success")

            # Redirect by role
            if user.role == "customer":
                return redirect(url_for("user.dashboard"))
            elif user.role == "store":
                return redirect(url_for("store.store_dashboard"))
            elif user.role == "admin":
                return redirect(url_for("admin.admin_dashboard"))

        flash("Invalid phone or password!", "danger")
        return redirect(url_for("auth.login"))

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out successfully!", "info")
    return redirect(url_for("auth.login"))

# =========================
# user routes
# =========================

@user_bp.route("/dashboard")
@login_required
def dashboard():
    # Allow only customers
    if current_user.role != "customer":
        flash("Access denied!", "danger")
        return redirect(url_for("auth.login"))

    # Fetch customer's orders
    orders = (
        Order.query
         .filter_by(user_id=current_user.id)
         .order_by(Order.created_at.desc())
         .all()
    )

    return render_template("dashboard_user.html", orders=orders)


@user_bp.route("/new-order")
@login_required
def new_order():
    if current_user.role != "customer":
        flash("Only customers can create orders!", "danger")
        return redirect(url_for("auth.login"))

    # Get user's location if available
    user_lat = getattr(current_user, 'latitude', None)
    user_lon = getattr(current_user, 'longitude', None)

    # Find nearest stores that are active and have complete profiles
    if user_lat and user_lon:
        stores = find_nearest_stores(user_lat, user_lon, limit=10)
    else:
        # If no user location, get all active stores
        stores = Store.query.filter_by(is_active=True).all()
        # Add approximate distance for display
        for store in stores:
            store.distance = f"{round(store.delivery_charge * 0.8, 1)} km"

    # Filter stores that have all required fields
    valid_stores = []
    for store in stores:
        if store.name and store.address and store.latitude and store.longitude:
            valid_stores.append(store)

    return render_template("user_order.html", stores=valid_stores, user=current_user)

# =========================
# store routes
# =========================

@store_bp.route("/dashboard")
@login_required
def store_dashboard():
    if current_user.role != "store":
        flash("Access denied!", "danger")
        return redirect(url_for("auth.login"))

    # Get store profile for this user
    store = Store.query.filter_by(user_id=current_user.id).first()

    # Get orders for this store
    if store:
        orders = Order.query.filter_by(store_id=store.id)\
                           .order_by(Order.created_at.desc()).all()
    else:
        orders = []

    save_success = request.args.get('save_success', False)

    return render_template("medical_dashboard.html",
                         orders=orders,
                         store=store,
                         save_success=save_success)


@store_bp.route("/save-profile", methods=["POST"])
@login_required
def save_profile():
    """Save or update store profile information"""
    if current_user.role != "store":
        flash("Access denied!", "danger")
        return redirect(url_for("auth.login"))

    try:
        # Check if store profile already exists
        store = Store.query.filter_by(user_id=current_user.id).first()

        if not store:
            # Create new store profile
            store = Store(user_id=current_user.id)

        # Update store information from form
        store.name = request.form.get('store_name')
        store.holder_name = request.form.get('holder_name')
        store.address = request.form.get('address')
        store.phone = request.form.get('phone')
        store.delivery_charge = float(request.form.get('delivery_charge', 50.0))
        store.opening_time = request.form.get('opening_time', '09:00')
        store.closing_time = request.form.get('closing_time', '21:00')
        store.latitude = float(request.form.get('latitude', 0))
        store.longitude = float(request.form.get('longitude', 0))
        store.description = request.form.get('description', 'Verified pharmacy')
        store.location_link = request.form.get('location_link')
        store.upi_id = request.form.get('upi_id')
        store.is_active = True

        # Create upload directories if they don't exist
        upload_folder = current_app.config['UPLOAD_FOLDER']
        stores_folder = os.path.join(upload_folder, 'stores')
        qr_folder = os.path.join(upload_folder, 'qr_codes')
        os.makedirs(stores_folder, exist_ok=True)
        os.makedirs(qr_folder, exist_ok=True)

        # Handle file uploads
        if 'store_photo' in request.files:
            file = request.files['store_photo']
            if file and file.filename:
                filename = secure_filename(file.filename)
                unique_name = str(uuid.uuid4()) + "_" + filename
                upload_path = os.path.join(stores_folder, unique_name)
                file.save(upload_path)
                store.store_photo = os.path.join('uploads', 'stores', unique_name)

        if 'qr_code' in request.files:
            file = request.files['qr_code']
            if file and file.filename:
                filename = secure_filename(file.filename)
                unique_name = str(uuid.uuid4()) + "_" + filename
                upload_path = os.path.join(qr_folder, unique_name)
                file.save(upload_path)
                store.qr_code = os.path.join('uploads', 'qr_codes', unique_name)

        db.session.add(store)
        db.session.commit()

        flash("Store profile saved successfully!", "success")
        return redirect(url_for("store.store_dashboard", save_success=True))

    except Exception as e:
        app.logger.error(f"Error saving store profile: {str(e)}")
        flash("Error saving store profile. Please try again.", "danger")
        return redirect(url_for("store.store_dashboard"))


@store_bp.route("/accept/<int:order_id>", methods=["POST"])
@login_required
def accept_order(order_id):
    if current_user.role != "store":
        flash("Access denied!", "danger")
        return redirect(url_for("auth.login"))

    order = Order.query.get_or_404(order_id)
    order.order_status = "ACCEPTED"
    db.session.commit()

    # Create payment link if online payment and Razorpay is configured
    payment_link = None
    if order.payment_method == 'ONLINE' and is_payment_enabled():
        payment_link = create_razorpay_payment_link(order)

    # Send email to customer with payment link
    if order.user.email:
        send_order_accepted_email(order, payment_link)

    flash("Order accepted successfully! Customer notified via email.", "success")
    return redirect(url_for("store.store_dashboard"))


@store_bp.route("/reject/<int:order_id>", methods=["POST"])
@login_required
def reject_order(order_id):
    if current_user.role != "store":
        flash("Access denied!", "danger")
        return redirect(url_for("auth.login"))

    order = Order.query.get_or_404(order_id)
    order.order_status = "REJECTED"
    db.session.commit()

    flash("Order rejected!", "warning")
    return redirect(url_for("store.store_dashboard"))


@store_bp.route("/update_status/<int:order_id>", methods=["POST"])
@login_required
def update_status(order_id):
    if current_user.role != "store":
        flash("Access denied!", "danger")
        return redirect(url_for("auth.login"))

    order = Order.query.get_or_404(order_id)
    new_status = request.form.get("status")
    order.order_status = new_status
    db.session.commit()

    flash("Order status updated!", "success")
    return redirect(url_for("store.store_dashboard"))


@store_bp.route("/mark_paid/<int:order_id>", methods=["POST"])
@login_required
def mark_paid(order_id):
    if current_user.role != "store":
        flash("Access denied!", "danger")
        return ("", 403)

    order = Order.query.get_or_404(order_id)
    try:
        order.payment_status = "SUCCESS"
        order.updated_at = datetime.utcnow()
        db.session.commit()
        return ("", 204)
    except Exception as e:
        app.logger.error(f"Error marking payment paid for order {order_id}: {e}")
        return ("", 500)


@store_bp.route("/update_price/<int:order_id>", methods=["POST"])
@login_required
def update_price(order_id):
    if current_user.role != "store":
        flash("Access denied!", "danger")
        return redirect(url_for("auth.login"))

    order = Order.query.get_or_404(order_id)
    medicine_total = float(request.form.get("medicine_total"))
    order.medicine_total = medicine_total
    order.total_amount = medicine_total + order.delivery_charge

    db.session.commit()

    flash("Medicine price updated!", "success")
    return redirect(url_for("store.store_dashboard"))


@store_bp.route("/process/<int:order_id>", methods=["POST"])
@login_required
def process_order(order_id):
    """API used by the enhanced dashboard modal to accept and price an order."""
    if current_user.role != "store":
        return ("", 403)

    order = Order.query.get_or_404(order_id)
    med = float(request.form.get("medicine_total") or 0)
    delivery = float(request.form.get("delivery_charge") or order.delivery_charge)
    payment_method = request.form.get("payment_method") or order.payment_method

    order.medicine_total = med
    order.delivery_charge = delivery
    order.total_amount = med + delivery
    order.payment_method = payment_method
    order.order_status = "ACCEPTED"
    db.session.commit()

    # Create payment link if online payment and Razorpay is configured
    payment_link = None
    if order.payment_method == 'ONLINE' and is_payment_enabled():
        payment_link = create_razorpay_payment_link(order)
        if not payment_link:
            app.logger.warning(f"Could not create payment link for order {order.id}")

    # Send email to customer
    if order.user.email:
        send_order_accepted_email(order, payment_link)

    return ("", 204)

# =========================
# order routes
# =========================

@order_bp.route("/track", methods=["GET", "POST"])
def track_order():
    order = None
    if request.method == "POST":
        tracking_id = request.form.get("tracking_id")
        order = Order.query.filter_by(tracking_id=tracking_id).first()
    return render_template("order_tracking.html", order=order)


@order_bp.route("/create", methods=["GET","POST"])
@login_required
def create_order():
    if current_user.role != "customer":
        flash("Only customers can create orders!", "danger")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        # handle file upload
        file = request.files.get("prescription")
        delivery_address = request.form.get("delivery_address")
        payment_method = request.form.get("payment_method")
        store_id = request.form.get("store_id")
        user_lat = request.form.get("user_lat")
        user_lng = request.form.get("user_lng")

        # debug incoming payload with key values (after we have values)
        app.logger.debug(
            "create_order POST form=%s delivery_address=%s payment_method=%s store_id=%s files=%s",
            request.form,
            delivery_address,
            payment_method,
            store_id,
            list(request.files.keys())
        )

        # basic server-side validation – front end may be bypassed so we must guard here as well
        if not file:
            flash("Please upload prescription!", "danger")
            return redirect(url_for("user.new_order"))

        # delivery address is essential; unlike store and payment we cannot guess it
        if not delivery_address:
            flash("Please provide a delivery address.", "danger")
            return redirect(url_for("user.new_order"))

        # fall back to nearest store if none selected
        if not store_id:
            if user_lat and user_lng:
                nearest_store, delivery_charge = find_nearest_store(
                    float(user_lat), float(user_lng)
                )
            else:
                nearest_store, delivery_charge = find_nearest_store(
                    getattr(current_user, 'latitude', None),
                    getattr(current_user, 'longitude', None)
                )
            store_id = nearest_store.id if nearest_store else None
            app.logger.debug("No store chosen; falling back to nearest (%s, charge=%s)",
                     store_id, delivery_charge)

        # default payment to COD if Razorpay not configured
        if not payment_method:
            payment_method = "COD"
            app.logger.debug("No payment method chosen; defaulting to COD")
        elif payment_method == "ONLINE" and not is_payment_enabled():
            payment_method = "COD"
            app.logger.info("Online payment disabled, defaulting to COD")
            flash("Online payment is currently unavailable. Order placed with Cash on Delivery.", "info")

        # Ensure upload directory exists
        upload_folder = current_app.config['UPLOAD_FOLDER']
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder, exist_ok=True)
            app.logger.info(f"Created upload folder: {upload_folder}")

        filename = secure_filename(file.filename)
        unique_name = str(uuid.uuid4()) + "_" + filename
        upload_path = os.path.join(upload_folder, unique_name)
        file.save(upload_path)
        app.logger.info(f"File saved to: {upload_path}")

        prescription = Prescription(
            file_path=unique_name,
            user_id=current_user.id
        )
        db.session.add(prescription)
        db.session.commit()

        tracking_id = "MED" + str(uuid.uuid4().hex[:8]).upper()

        # determine which store will fulfill the order
        if store_id:
            selected_store = Store.query.get(int(store_id))
            delivery_charge = selected_store.delivery_charge if selected_store else 50.0
        else:
            if user_lat and user_lng:
                selected_store, delivery_charge = find_nearest_store(float(user_lat), float(user_lng))
            else:
                selected_store, delivery_charge = find_nearest_store(
                    getattr(current_user, 'latitude', None),
                    getattr(current_user, 'longitude', None)
                )

        order = Order(
            tracking_id=tracking_id,
            user_id=current_user.id,
            store_id=selected_store.id if selected_store else None,
            prescription_id=prescription.id,
            delivery_address=delivery_address,
            delivery_charge=delivery_charge,
            medicine_total=0,
            total_amount=delivery_charge,
            payment_method=payment_method,
            payment_status="PENDING",
            order_status="NEW"
        )

        db.session.add(order)
        db.session.commit()

        app.logger.info("=" * 60)
        app.logger.info(f"Order created successfully: {tracking_id}")
        app.logger.info(f"Customer email: {current_user.email}")
        app.logger.info(f"Should send confirmation email to: {current_user.email}")
        app.logger.info("=" * 60)

        # Send order confirmation email to customer
        if current_user.email:
            app.logger.info("📧 Attempting to send order confirmation email...")
            send_order_confirmation_email(order)
        else:
            app.logger.warning(f"⚠️ User {current_user.id} has no email address!")

        # Send new order notification to store
        if selected_store:
            app.logger.info(f"📧 Attempting to send store notification to {selected_store.user.email if selected_store.user else 'No email'}")
            send_store_new_order_email(order, selected_store)

        flash(f"Order created successfully! Tracking ID: {tracking_id}", "success")
        return redirect(url_for("user.dashboard"))

    # if user navigates to /order/create directly, send them to wizard
    return redirect(url_for("user.new_order"))

# =========================
# payment routes
# =========================

@payment_bp.route("/pay/<int:order_id>")
@login_required
def pay(order_id):
    order = Order.query.get_or_404(order_id)

    if order.payment_method != "ONLINE":
        flash("Invalid payment method!", "danger")
        return redirect(url_for("user.dashboard"))
    
    if not is_payment_enabled():
        flash("Online payment is currently disabled. Please use Cash on Delivery or contact support.", "warning")
        return redirect(url_for("user.dashboard"))

    # Create Razorpay payment link
    payment_link = create_razorpay_payment_link(order)

    if payment_link:
        return redirect(payment_link)
    else:
        flash("Failed to create payment link. Please try again or use Cash on Delivery.", "danger")
        return redirect(url_for("user.dashboard"))


@payment_bp.route("/razorpay/callback")
def razorpay_callback():
    """Razorpay payment callback handler"""
    try:
        order_id = request.args.get('order_id')
        razorpay_payment_id = request.args.get('razorpay_payment_id')
        razorpay_payment_link_id = request.args.get('razorpay_payment_link_id')
        razorpay_payment_link_status = request.args.get('razorpay_payment_link_status')

        order = Order.query.get(int(order_id))

        if razorpay_payment_link_status == 'paid':
            # Update payment status
            payment = Payment.query.filter_by(order_id=order.id).first()
            if payment:
                payment.status = 'SUCCESS'
                payment.transaction_id = razorpay_payment_id

            order.payment_status = 'SUCCESS'
            db.session.commit()

            flash("Payment successful! Your order is being prepared.", "success")
        else:
            flash("Payment failed or cancelled. Please try again.", "danger")

        return redirect(url_for("user.dashboard"))

    except Exception as e:
        app.logger.error(f"Razorpay callback error: {str(e)}")
        flash("Payment processing error. Please contact support.", "danger")
        return redirect(url_for("user.dashboard"))


@payment_bp.route("/razorpay/webhook", methods=["POST"])
def razorpay_webhook():
    """Razorpay webhook handler for payment status updates"""
    try:
        webhook_secret = os.getenv('RAZORPAY_WEBHOOK_SECRET', '')
        webhook_signature = request.headers.get('X-Razorpay-Signature')

        # Verify webhook signature
        if webhook_secret and is_payment_enabled():
            razorpay_client.utility.verify_webhook_signature(
                request.get_data().decode('utf-8'),
                webhook_signature,
                webhook_secret
            )

        payload = request.json
        event = payload.get('event')

        if event == 'payment_link.paid':
            payment_link_id = payload['payload']['payment_link']['entity']['id']
            payment_id = payload['payload']['payment']['entity']['id']

            # Find payment record
            payment = Payment.query.filter_by(transaction_id=payment_link_id).first()
            if payment:
                payment.status = 'SUCCESS'
                payment.transaction_id = payment_id

                order = payment.order
                order.payment_status = 'SUCCESS'

                db.session.commit()

                app.logger.info(f"Payment successful for order {order.tracking_id}")

        return jsonify({'status': 'ok'}), 200

    except Exception as e:
        app.logger.error(f"Webhook error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 400

# =========================
# admin routes
# =========================

# Admin only decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # role is stored in lowercase in the User model
        if not current_user.is_authenticated or current_user.role != "admin":
            flash("Access denied! Admin privileges required.", "danger")
            return redirect(url_for("user.dashboard"))
        return f(*args, **kwargs)
    return decorated_function


@admin_bp.route("/dashboard")
@login_required
@admin_required
def admin_dashboard():
    total_users = User.query.filter_by(role="customer").count()
    total_stores = User.query.filter_by(role="store").count()
    total_orders = Order.query.count()
    revenue_result = db.session.query(func.sum(Order.total_amount)).filter(Order.payment_status == "SUCCESS").scalar()
    revenue = revenue_result or 0.0
    recent_orders = Order.query.options(db.joinedload(Order.user)).order_by(Order.id.desc()).limit(10).all()
    order_stats = {
        'NEW': Order.query.filter_by(order_status='NEW').count(),
        'ACCEPTED': Order.query.filter_by(order_status='ACCEPTED').count(),
        'DELIVERED': Order.query.filter_by(order_status='DELIVERED').count(),
        'REJECTED': Order.query.filter_by(order_status='REJECTED').count()
    }
    recent_users = User.query.filter_by(role='customer').order_by(User.id.desc()).limit(5).all()
    return render_template(
        "dashboard_admin.html",
        total_users=total_users,
        total_stores=total_stores,
        total_orders=total_orders,
        revenue=revenue,
        orders=recent_orders,
        order_stats=order_stats,
        recent_users=recent_users
    )


@admin_bp.route("/order/<int:order_id>")
@login_required
@admin_required
def view_order(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template("admin/view_order.html", order=order)


@admin_bp.route("/manage-users")
@login_required
@admin_required
def manage_users():
    users = User.query.filter_by(role="customer").all()
    return render_template("admin/users.html", users=users)


@admin_bp.route("/manage-stores")
@login_required
@admin_required
def manage_stores():
    stores = Store.query.all()
    return render_template("admin/stores.html", stores=stores)


@admin_bp.route("/approve-store/<int:store_id>", methods=["POST"])
@login_required
@admin_required
def approve_store(store_id):
    store = Store.query.get_or_404(store_id)
    store.is_active = True
    db.session.commit()
    flash(f"Store {store.name} has been approved!", "success")
    return redirect(url_for("admin.manage_stores"))


@admin_bp.route("/reject-store/<int:store_id>", methods=["POST"])
@login_required
@admin_required
def reject_store(store_id):
    store = Store.query.get_or_404(store_id)
    store.is_active = False
    db.session.commit()
    flash(f"Store {store.name} has been rejected!", "warning")
    return redirect(url_for("admin.manage_stores"))

# route for serving uploaded files
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)

# ================================================================
# ADD THIS ROUTE to your store_bp section in routes.py
# Place it after the existing process_order route
# ================================================================

@store_bp.route("/send_bill/<int:order_id>", methods=["POST"])
@login_required
def send_bill(order_id):
    """
    Receives bill data as JSON from the frontend,
    builds an HTML bill email and sends it to the patient.
    No PDF attachment needed — the bill is rendered as rich HTML.
    """
    if current_user.role != "store":
        return jsonify({"error": "Access denied"}), 403

    order = Order.query.get_or_404(order_id)

    try:
        data = request.get_json(force=True)

        patient_name    = data.get("patient_name", "Customer")
        tracking_id     = data.get("tracking_id", order.tracking_id)
        address         = data.get("address", order.delivery_address)
        patient_email   = data.get("email", "")
        payment_method  = data.get("payment_method", "COD")
        medicine_total  = float(data.get("medicine_total", 0))
        delivery_charge = float(data.get("delivery_charge", 0))
        grand_total     = float(data.get("grand_total", 0))
        medicines       = data.get("medicines", [])       # list of {name, qty, rate, subtotal}
        store_name      = data.get("store_name", "Medical Store")
        store_address   = data.get("store_address", "")
        store_phone     = data.get("store_phone", "")
        store_upi       = data.get("store_upi", "")

        if not patient_email or "@" not in patient_email:
            return jsonify({"error": "No valid email address for this patient."}), 400

        from datetime import datetime as dt
        bill_date = dt.now().strftime("%d %b %Y, %I:%M %p")

        # Build medicine rows HTML
        medicine_rows_html = ""
        for i, m in enumerate(medicines, 1):
            name     = m.get("name", "—")
            qty      = m.get("qty", 1)
            rate     = float(m.get("rate", 0))
            subtotal = float(m.get("subtotal", 0))
            medicine_rows_html += f"""
            <tr>
                <td style="padding:8px 10px; border-bottom:1px solid #e5e7eb; color:#374151;">{i}</td>
                <td style="padding:8px 10px; border-bottom:1px solid #e5e7eb; color:#111827; font-weight:500;">{name}</td>
                <td style="padding:8px 10px; border-bottom:1px solid #e5e7eb; color:#374151; text-align:center;">{qty}</td>
                <td style="padding:8px 10px; border-bottom:1px solid #e5e7eb; color:#374151; text-align:right;">&#8377;{rate:.2f}</td>
                <td style="padding:8px 10px; border-bottom:1px solid #e5e7eb; color:#16a34a; font-weight:600; text-align:right;">&#8377;{subtotal:.2f}</td>
            </tr>
            """

        if not medicine_rows_html:
            medicine_rows_html = """
            <tr>
                <td colspan="5" style="padding:16px; text-align:center; color:#9ca3af;">
                    No medicines listed
                </td>
            </tr>
            """

        # UPI section
        upi_section = ""
        if payment_method == "ONLINE" and store_upi:
            upi_section = f"""
            <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:8px; padding:14px; margin:16px 0; text-align:center;">
                <p style="margin:0 0 6px; color:#1e40af; font-weight:700; font-size:15px;">💳 Pay via UPI</p>
                <p style="margin:0; font-size:16px; font-weight:700; color:#1d4ed8; letter-spacing:0.5px;">{store_upi}</p>
            </div>
            """
        elif payment_method == "COD":
            upi_section = f"""
            <div style="background:#fefce8; border:1px solid #fde047; border-radius:8px; padding:14px; margin:16px 0; text-align:center;">
                <p style="margin:0 0 4px; color:#713f12; font-weight:700;">💵 Cash on Delivery</p>
                <p style="margin:0; color:#854d0e; font-size:13px;">Please keep exact change ready at delivery</p>
            </div>
            """

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="margin:0; padding:0; background:#f3f4f6; font-family:'Segoe UI', Arial, sans-serif;">
            <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6; padding:30px 16px;">
                <tr>
                    <td align="center">
                        <table width="600" cellpadding="0" cellspacing="0"
                               style="background:#ffffff; border-radius:14px; overflow:hidden;
                                      box-shadow:0 4px 24px rgba(0,0,0,0.10); max-width:100%;">

                            <!-- Header -->
                            <tr>
                                <td style="background:linear-gradient(135deg,#14532d,#16a34a);
                                           padding:32px 30px; text-align:center;">
                                    <p style="margin:0 0 4px; color:rgba(255,255,255,0.85); font-size:13px;
                                               text-transform:uppercase; letter-spacing:2px;">Medicine Bill / Pavti</p>
                                    <h1 style="margin:0; color:#ffffff; font-size:24px; font-weight:800;
                                                letter-spacing:0.5px;">{store_name}</h1>
                                    {f'<p style="margin:6px 0 0; color:rgba(255,255,255,0.8); font-size:13px;">&#128506; {store_address}</p>' if store_address else ''}
                                    {f'<p style="margin:4px 0 0; color:rgba(255,255,255,0.8); font-size:13px;">&#128222; {store_phone}</p>' if store_phone else ''}
                                </td>
                            </tr>

                            <!-- Order meta -->
                            <tr>
                                <td style="padding:24px 30px 0;">
                                    <table width="100%" cellpadding="0" cellspacing="0"
                                           style="background:#f0fdf4; border:1px solid #bbf7d0;
                                                  border-radius:10px; overflow:hidden;">
                                        <tr>
                                            <td style="padding:14px 18px; border-right:1px solid #bbf7d0;">
                                                <p style="margin:0; font-size:11px; color:#6b7280;
                                                           text-transform:uppercase; letter-spacing:1px;">Order ID</p>
                                                <p style="margin:4px 0 0; font-size:15px; font-weight:700;
                                                           color:#15803d;">{tracking_id}</p>
                                            </td>
                                            <td style="padding:14px 18px; border-right:1px solid #bbf7d0;">
                                                <p style="margin:0; font-size:11px; color:#6b7280;
                                                           text-transform:uppercase; letter-spacing:1px;">Date</p>
                                                <p style="margin:4px 0 0; font-size:14px; color:#374151;">{bill_date}</p>
                                            </td>
                                            <td style="padding:14px 18px;">
                                                <p style="margin:0; font-size:11px; color:#6b7280;
                                                           text-transform:uppercase; letter-spacing:1px;">Payment</p>
                                                <p style="margin:4px 0 0; font-size:14px; font-weight:600;
                                                           color:#374151;">{payment_method}</p>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>

                            <!-- Bill to -->
                            <tr>
                                <td style="padding:20px 30px 0;">
                                    <p style="margin:0 0 4px; font-size:11px; color:#9ca3af;
                                               text-transform:uppercase; letter-spacing:1px;">Bill To</p>
                                    <p style="margin:0; font-size:16px; font-weight:700; color:#111827;">{patient_name}</p>
                                    <p style="margin:4px 0 0; font-size:13px; color:#6b7280;">&#127968; {address}</p>
                                </td>
                            </tr>

                            <!-- Medicine table -->
                            <tr>
                                <td style="padding:20px 30px 0;">
                                    <p style="margin:0 0 10px; font-size:13px; font-weight:700;
                                               color:#374151; text-transform:uppercase; letter-spacing:1px;">
                                        &#128138; Medicines
                                    </p>
                                    <table width="100%" cellpadding="0" cellspacing="0"
                                           style="border:1px solid #e5e7eb; border-radius:8px; overflow:hidden;">
                                        <thead>
                                            <tr style="background:#f9fafb;">
                                                <th style="padding:10px 10px; text-align:left; font-size:11px;
                                                            color:#6b7280; text-transform:uppercase; letter-spacing:1px;
                                                            border-bottom:1px solid #e5e7eb;">#</th>
                                                <th style="padding:10px 10px; text-align:left; font-size:11px;
                                                            color:#6b7280; text-transform:uppercase; letter-spacing:1px;
                                                            border-bottom:1px solid #e5e7eb;">Medicine</th>
                                                <th style="padding:10px 10px; text-align:center; font-size:11px;
                                                            color:#6b7280; text-transform:uppercase; letter-spacing:1px;
                                                            border-bottom:1px solid #e5e7eb;">Qty</th>
                                                <th style="padding:10px 10px; text-align:right; font-size:11px;
                                                            color:#6b7280; text-transform:uppercase; letter-spacing:1px;
                                                            border-bottom:1px solid #e5e7eb;">Rate</th>
                                                <th style="padding:10px 10px; text-align:right; font-size:11px;
                                                            color:#6b7280; text-transform:uppercase; letter-spacing:1px;
                                                            border-bottom:1px solid #e5e7eb;">Amount</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {medicine_rows_html}
                                        </tbody>
                                    </table>
                                </td>
                            </tr>

                            <!-- Totals -->
                            <tr>
                                <td style="padding:16px 30px 0;">
                                    <table width="100%" cellpadding="0" cellspacing="0"
                                           style="background:#f9fafb; border:1px solid #e5e7eb;
                                                  border-radius:8px; overflow:hidden;">
                                        <tr>
                                            <td style="padding:10px 16px; font-size:14px; color:#374151;">
                                                Medicine Total
                                            </td>
                                            <td style="padding:10px 16px; font-size:14px; color:#374151;
                                                        text-align:right; font-weight:600;">
                                                &#8377;{medicine_total:.2f}
                                            </td>
                                        </tr>
                                        <tr style="border-top:1px solid #e5e7eb;">
                                            <td style="padding:10px 16px; font-size:14px; color:#374151;">
                                                Delivery Charge
                                            </td>
                                            <td style="padding:10px 16px; font-size:14px; color:#374151;
                                                        text-align:right; font-weight:600;">
                                                &#8377;{delivery_charge:.2f}
                                            </td>
                                        </tr>
                                        <tr style="background:#14532d; border-top:2px solid #15803d;">
                                            <td style="padding:14px 16px; font-size:16px; font-weight:800;
                                                        color:#ffffff;">
                                                Grand Total
                                            </td>
                                            <td style="padding:14px 16px; font-size:18px; font-weight:800;
                                                        color:#ffffff; text-align:right;">
                                                &#8377;{grand_total:.2f}
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>

                            <!-- Payment info -->
                            <tr>
                                <td style="padding:16px 30px 0;">
                                    {upi_section}
                                </td>
                            </tr>

                            <!-- Footer -->
                            <tr>
                                <td style="padding:24px 30px 30px; text-align:center; border-top:1px solid #e5e7eb; margin-top:16px;">
                                    <p style="margin:16px 0 0; font-size:11px; color:#9ca3af;">
                                        This is a computer-generated bill from {store_name}.<br>
                                        For queries, contact us at {store_phone or 'the store'}.
                                    </p>
                                </td>
                            </tr>

                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """

        success = send_email(
            patient_email,
            f"Your Medicine Bill - {tracking_id} | {store_name}",
            html_body
        )

        if success:
            app.logger.info(f"✅ Bill email sent to {patient_email} for order {tracking_id}")
            return jsonify({"success": True, "message": f"Bill sent to {patient_email}"}), 200
        else:
            return jsonify({"error": "Failed to send email. Check server logs."}), 500

    except Exception as e:
        app.logger.error(f"❌ send_bill error for order {order_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500

# register blueprints on app
app.register_blueprint(auth_bp)
app.register_blueprint(user_bp)
app.register_blueprint(store_bp)
app.register_blueprint(order_bp)
app.register_blueprint(payment_bp)
app.register_blueprint(admin_bp)

# Add this after app.config.from_object(Config)
print("=== Email Configuration Debug ===")
print(f"MAIL_SERVER: {app.config.get('MAIL_SERVER')}")
print(f"MAIL_PORT: {app.config.get('MAIL_PORT')}")
print(f"MAIL_USERNAME: {app.config.get('MAIL_USERNAME')}")
print(f"MAIL_PASSWORD: {'SET' if app.config.get('MAIL_PASSWORD') else 'NOT SET'}")
print(f"MAIL_DEFAULT_SENDER: {app.config.get('MAIL_DEFAULT_SENDER')}")
print("=================================")

if __name__ == "__main__":
    app.run(debug=True)