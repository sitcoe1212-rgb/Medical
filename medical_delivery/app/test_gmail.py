# test_gmail.py
import os
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Load .env file
load_dotenv()

print("=== Testing Gmail SMTP Connection ===")
print(f"MAIL_USERNAME: {os.getenv('MAIL_USERNAME')}")
print(f"MAIL_PASSWORD: {'SET' if os.getenv('MAIL_PASSWORD') else 'NOT SET'}")

# Test SMTP connection
try:
    print("\n1. Connecting to Gmail SMTP server...")
    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    
    print("2. Attempting to login...")
    username = os.getenv('MAIL_USERNAME')
    password = os.getenv('MAIL_PASSWORD')
    
    server.login(username, password)
    print("✓ Login successful!")
    
    print("\n3. Sending test email...")
    msg = MIMEMultipart()
    msg['From'] = username
    msg['To'] = username  # Send to yourself for testing
    msg['Subject'] = "Test Email from MediOrder"
    
    body = "This is a test email to verify Gmail SMTP configuration."
    msg.attach(MIMEText(body, 'plain'))
    
    server.send_message(msg)
    print("✓ Test email sent successfully!")
    print(f"  Sent to: {username}")
    print("  Please check your inbox (and spam folder)")
    
    server.quit()
    
except Exception as e:
    print(f"✗ Error: {e}")
    
    if "535" in str(e):
        print("\n⚠️ Authentication failed. For Gmail:")
        print("1. Go to https://myaccount.google.com/apppasswords")
        print("2. Select 'Mail' as the app")
        print("3. Select 'Windows Computer' as the device")
        print("4. Click 'Generate'")
        print("5. Use the 16-character password (without spaces)")
    elif "Timeout" in str(e):
        print("\n⚠️ Connection timeout. Check your internet connection.")