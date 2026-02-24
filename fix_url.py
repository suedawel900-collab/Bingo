import os
import requests

# Get your Railway URL from environment or set manually
RAILWAY_URL = os.getenv('RAILWAY_PUBLIC_DOMAIN', 'https://your-app.railway.app')

print(f"✅ Using URL: {RAILWAY_URL}")

# Test if the payment page is accessible
try:
    response = requests.get(f"{RAILWAY_URL}/health")
    if response.status_code == 200:
        print("✅ Payment server is accessible")
    else:
        print("❌ Payment server not responding")
except Exception as e:
    print(f"❌ Error: {e}")