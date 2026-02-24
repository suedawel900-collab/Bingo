import os
import stripe
import logging
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import uvicorn

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY')
BASE_URL = os.getenv('RAILWAY_PUBLIC_DOMAIN', 'http://localhost:8000')

# Create FastAPI app
app = FastAPI(title="Bingo Payment Gateway")

# Setup templates and static files
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Import database
from models import Database
db = Database()

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/payment/page", response_class=HTMLResponse)
async def payment_page(request: Request, user_id: int, amount: int):
    """Serve payment page with Stripe Element"""
    try:
        # Convert amount to cents if needed
        if amount < 100:  # If amount is in dollars
            amount = amount * 100
            
        # Create payment intent
        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency='usd',
            metadata={
                'user_id': str(user_id),
                'type': 'deposit'
            },
            automatic_payment_methods={
                'enabled': True,
            }
        )
        
        # Store in database
        db.create_transaction(
            user_id=user_id,
            amount=amount,
            type='deposit',
            payment_intent_id=intent.id
        )
        
        return templates.TemplateResponse(
            "payment.html",
            {
                "request": request,
                "stripe_public_key": STRIPE_PUBLIC_KEY,
                "client_secret": intent.client_secret,
                "amount": amount,
                "user_id": user_id
            }
        )
        
    except Exception as e:
        logger.error(f"Payment page error: {str(e)}")
        return HTMLResponse(
            content=f"<h1>Error</h1><p>{str(e)}</p>",
            status_code=500
        )

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        # Verify webhook signature
        event = stripe.Webhook.construct_event(
            payload, sig_header, os.getenv('STRIPE_WEBHOOK_SECRET')
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    # Handle the event
    if event['type'] == 'payment_intent.succeeded':
        payment_intent = event['data']['object']
        await handle_successful_payment(payment_intent)
    elif event['type'] == 'payment_intent.payment_failed':
        payment_intent = event['data']['object']
        await handle_failed_payment(payment_intent)
    
    return JSONResponse(content={"status": "success"})

async def handle_successful_payment(payment_intent):
    """Handle successful payment"""
    try:
        intent_id = payment_intent['id']
        metadata = payment_intent['metadata']
        user_id = int(metadata.get('user_id', 0))
        amount = payment_intent['amount']
        
        if user_id:
            # Update balance
            db.update_balance(
                user_id=user_id,
                amount=amount,
                transaction_type='deposit',
                payment_intent_id=intent_id,
                description=f'Stripe deposit: ${amount/100:.2f}'
            )
            logger.info(f"Payment succeeded for user {user_id}: ${amount/100:.2f}")
            
    except Exception as e:
        logger.error(f"Error handling successful payment: {str(e)}")

async def handle_failed_payment(payment_intent):
    """Handle failed payment"""
    intent_id = payment_intent['id']
    logger.warning(f"Payment failed: {intent_id}")

@app.get("/payment/success", response_class=HTMLResponse)
async def payment_success():
    """Payment success page"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Successful</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                margin: 0;
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
            }
            .card {
                background: white;
                border-radius: 20px;
                padding: 40px;
                max-width: 400px;
                width: 90%;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                text-align: center;
            }
            .success-icon {
                width: 80px;
                height: 80px;
                background: #4CAF50;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 30px;
                color: white;
                font-size: 40px;
                animation: scaleIn 0.5s ease;
            }
            @keyframes scaleIn {
                from { transform: scale(0); }
                to { transform: scale(1); }
            }
            h1 {
                color: #333;
                margin-bottom: 20px;
                font-size: 28px;
            }
            p {
                color: #666;
                line-height: 1.6;
                margin-bottom: 30px;
            }
            .button {
                background: #4CAF50;
                color: white;
                border: none;
                padding: 15px 30px;
                border-radius: 10px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                text-decoration: none;
                display: inline-block;
                transition: background 0.3s;
            }
            .button:hover {
                background: #45a049;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <div class="success-icon">✓</div>
            <h1>Payment Successful!</h1>
            <p>Your deposit has been processed and your balance will be updated within 1-2 minutes.</p>
            <a href="https://t.me/YOUR_BOT_USERNAME" class="button">Return to Bot</a>
        </div>
        <script>
            setTimeout(function() {
                window.location.href = "https://t.me/YOUR_BOT_USERNAME";
            }, 5000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html.replace("YOUR_BOT_USERNAME", os.getenv('BOT_USERNAME', 'your_bot')))

@app.get("/payment/cancel", response_class=HTMLResponse)
async def payment_cancel():
    """Payment cancelled page"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Cancelled</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #f5f5f5;
                margin: 0;
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
            }
            .card {
                background: white;
                border-radius: 20px;
                padding: 40px;
                max-width: 400px;
                width: 90%;
                box-shadow: 0 10px 40px rgba(0,0,0,0.1);
                text-align: center;
            }
            h1 {
                color: #dc3545;
                margin-bottom: 20px;
            }
            p {
                color: #666;
                margin-bottom: 30px;
            }
            .button {
                background: #0088cc;
                color: white;
                border: none;
                padding: 12px 30px;
                border-radius: 8px;
                text-decoration: none;
                display: inline-block;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>❌ Payment Cancelled</h1>
            <p>Your payment was cancelled. No charges were made.</p>
            <a href="https://t.me/YOUR_BOT_USERNAME" class="button">Return to Bot</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html.replace("YOUR_BOT_USERNAME", os.getenv('BOT_USERNAME', 'your_bot')))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))