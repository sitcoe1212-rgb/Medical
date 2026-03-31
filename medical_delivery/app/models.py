from app import db
from flask_login import UserMixin
from datetime import datetime

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True)
    password_hash = db.Column(db.Text, nullable=False)
    role = db.Column(db.Enum('customer','store','admin'), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    address = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    orders = db.relationship('Order', backref='user', lazy=True)
    prescriptions = db.relationship('Prescription', backref='user', lazy=True)
    notifications = db.relationship('Notification', backref='user', lazy=True)

class Store(db.Model):
    __tablename__ = 'stores'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # Link to user account
    name = db.Column(db.String(150), nullable=False)
    address = db.Column(db.Text, nullable=False)
    phone = db.Column(db.String(20))
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    rating = db.Column(db.Float, default=4.0)
    delivery_charge = db.Column(db.Float, default=50.0)
    opening_time = db.Column(db.String(5), default='09:00')
    closing_time = db.Column(db.String(5), default='21:00')
    description = db.Column(db.String(200), default='Verified pharmacy')
    store_photo = db.Column(db.String(200), nullable=True)  # Path to store photo
    upi_id = db.Column(db.String(100), nullable=True)
    qr_code = db.Column(db.String(200), nullable=True)  # Path to QR code
    location_link = db.Column(db.String(500), nullable=True)
    holder_name = db.Column(db.String(150), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    orders = db.relationship('Order', backref='store', lazy=True)
    
    # Relationship with user
    user = db.relationship('User', backref=db.backref('store_profile', uselist=False))

class Prescription(db.Model):
    __tablename__ = 'prescriptions'

    id = db.Column(db.Integer, primary_key=True)
    file_path = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    orders = db.relationship('Order', backref='prescription', lazy=True)

class Order(db.Model):
    __tablename__ = 'orders'

    id = db.Column(db.Integer, primary_key=True)
    tracking_id = db.Column(db.String(50), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id'), nullable=True)
    prescription_id = db.Column(db.Integer, db.ForeignKey('prescriptions.id'), nullable=True)

    delivery_address = db.Column(db.Text, nullable=False)
    delivery_lat = db.Column(db.Float, nullable=True)
    delivery_lng = db.Column(db.Float, nullable=True)
    distance_km = db.Column(db.Float, nullable=True)
    
    delivery_charge = db.Column(db.Float, default=0)
    medicine_total = db.Column(db.Float, default=0)
    total_amount = db.Column(db.Float, default=0)

    payment_method = db.Column(db.Enum('COD', 'ONLINE'), nullable=True)
    payment_status = db.Column(db.Enum('PENDING', 'SUCCESS', 'FAILED'), default='PENDING')

    order_status = db.Column(db.Enum(
        'NEW', 'ACCEPTED', 'REJECTED',
        'PREPARING', 'OUT_FOR_DELIVERY',
        'DELIVERED', 'CANCELLED'
    ), default='NEW')

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Payment(db.Model):
    __tablename__ = 'payments'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    transaction_id = db.Column(db.String(150), nullable=True)
    payment_gateway = db.Column(db.String(50), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.Enum('INITIATED', 'SUCCESS', 'FAILED'), default='INITIATED')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Create indexes for better performance
db.Index('idx_orders_user_id', Order.user_id)
db.Index('idx_orders_store_id', Order.store_id)
db.Index('idx_orders_tracking_id', Order.tracking_id)
db.Index('idx_prescriptions_user_id', Prescription.user_id)