import os
import json
import datetime
from fastapi import FastAPI, Request, Response
from dotenv import load_dotenv
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import gspread
from google.oauth2.service_account import Credentials

# ---------------- LOAD ENVIRONMENT VARIABLES ----------------
load_dotenv()

# ---------------- FASTAPI APP ----------------
app = FastAPI()

# ---------------- GOOGLE SHEETS SETUP ----------------
SERVICE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

if not SERVICE_JSON or not SHEET_ID:
    raise ValueError("Please set GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID in your environment variables.")

creds_dict = json.loads(SERVICE_JSON)
creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)

gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID).sheet1

# ---------------- TWILIO SETUP ----------------
twilio_client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)
TWILIO_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

if not TWILIO_NUMBER:
    raise ValueError("Please set TWILIO_WHATSAPP_NUMBER in your environment variables.")

# ---------------- USER STATE MEMORY ----------------
user_state = {}
user_temp = {}
user_seen_welcome = set()  # Track users who already got welcome

# ---------------- MAIN MENU MESSAGE ----------------
def main_menu(show_welcome=False):
    message = ""
    if show_welcome:
        message += "Welcome to the Expense Tracker Bot\n"
    message += (
        "Please choose an option:\n"
        "1. Add Expense\n"
        "2. View Today Total\n"
        "3. View This Week Total\n"
        "4. View All-Time Total\n"
        "5. Exit"
    )
    return message

# ---------------- SAVE EXPENSE ----------------
def save_expense(sender, category, amount, notes):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    sheet.append_row([timestamp, sender, category, amount, notes])

# ---------------- TOTAL CALCULATIONS ----------------
def calculate_total(sender, days=None):
    rows = sheet.get_all_values()[1:]  # Skip header
    total = 0
    now = datetime.datetime.now()
    for row in rows:
        timestamp, phone, category, amount, notes = row
        if phone != sender:
            continue
        row_time = datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M")
        if days is not None and (now - row_time).days >= days:
            continue
        try:
            total += float(amount)
        except ValueError:
            continue
    return total

# ---------------- CATEGORY MAPPING ----------------
categories = {
    "1": "Accommodation",
    "2": "Meals & Catering",
    "3": "Transport (Flights, Car Rental, Local Taxis)",
    "4": "Venue Hire",
    "5": "Vendor Payments",
    "6": "Staff Hires (Temporary & Permanent)",
    "7": "Security & Logistics",
    "8": "Printing",
    "9": "Other"
}

def category_menu():
    menu = "Choose category:\n"
    for k, v in categories.items():
        menu += f"{k}. {v}\n"
    menu += "5. Exit"
    return menu

# ---------------- WEBHOOK ----------------
@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    form = await request.form()
    sender = form.get("From")
    message = form.get("Body", "").strip()

    resp = MessagingResponse()

    # Show welcome only once per user
    show_welcome = False
    if sender not in user_seen_welcome:
        show_welcome = True
        user_seen_welcome.add(sender)

    # Initialize state
    if sender not in user_state:
        user_state[sender] = "MAIN_MENU"

    state = user_state[sender]

    # ---------------- EXIT HANDLING ----------------
    if message.lower() in ["5", "exit"]:
        user_state[sender] = "MAIN_MENU"
        user_temp.pop(sender, None)
        resp.message("Thank you for using Expense Tracker. Goodbye!")
        return Response(content=str(resp), media_type="application/xml")

    # ---------------- MAIN MENU ----------------
    if state == "MAIN_MENU":
        if message == "1":
            user_state[sender] = "AWAIT_CATEGORY"
            resp.message(category_menu())
        elif message == "2":
            total = calculate_total(sender, days=1)
            resp.message(f"Today's total spending: {total}\n\n{main_menu()}")
        elif message == "3":
            total = calculate_total(sender, days=7)
            resp.message(f"This week's total spending: {total}\n\n{main_menu()}")
        elif message == "4":
            total = calculate_total(sender)
            resp.message(f"All-time total spending: {total}\n\n{main_menu()}")
        else:
            resp.message(main_menu(show_welcome))
        return Response(content=str(resp), media_type="application/xml")

    # ---------------- CATEGORY SELECTION ----------------
    elif state == "AWAIT_CATEGORY":
        if message not in categories:
            resp.message(category_menu())
            return Response(content=str(resp), media_type="application/xml")
        user_temp[sender] = {"category": categories[message]}
        user_state[sender] = "AWAIT_AMOUNT"
        resp.message(f"Enter the amount spent on {categories[message]} (or type 'Exit' to cancel):")
        return Response(content=str(resp), media_type="application/xml")

    # ---------------- AMOUNT ENTRY ----------------
    elif state == "AWAIT_AMOUNT":
        try:
            amount = float(message)
        except ValueError:
            resp.message("Invalid amount. Enter a number (or type 'Exit' to cancel):")
            return Response(content=str(resp), media_type="application/xml")
        user_temp[sender]["amount"] = amount
        user_state[sender] = "AWAIT_NOTES"
        resp.message("Enter a note or comment (or type '-' for none, 'Exit' to cancel):")
        return Response(content=str(resp), media_type="application/xml")

    # ---------------- NOTES ENTRY ----------------
    elif state == "AWAIT_NOTES":
        notes = message if message != "-" else ""
        category = user_temp[sender]["category"]
        amount = user_temp[sender]["amount"]
        save_expense(sender, category, amount, notes)

        # Switch to NEXT_ACTION state
        user_state[sender] = "NEXT_ACTION"
        user_temp.pop(sender, None)
        resp.message(
            f"Expense saved.\nYou spent {amount} on {category}.\n\n"
            "What would you like to do next:\n"
            "1. Add Another Expense\n"
            "2. View Today Total\n"
            "3. Main Menu\n"
            "5. Exit"
        )
        return Response(content=str(resp), media_type="application/xml")

    # ---------------- NEXT ACTION MENU ----------------
    elif state == "NEXT_ACTION":
        if message == "1":
            user_state[sender] = "AWAIT_CATEGORY"
            resp.message(category_menu())
        elif message == "2":
            total = calculate_total(sender, days=1)
            resp.message(f"Today's total spending: {total}\n\n{main_menu()}")
            user_state[sender] = "MAIN_MENU"
        elif message == "3":
            resp.message(main_menu())
            user_state[sender] = "MAIN_MENU"
        elif message.lower() in ["5", "exit"]:
            resp.message("Thank you for using Expense Tracker. Goodbye!")
            user_state[sender] = "MAIN_MENU"
        else:
            resp.message(
                "Invalid choice. What would you like to do next?\n"
                "1. Add Another Expense\n"
                "2. View Today Total\n"
                "3. Main Menu\n"
                "5. Exit"
            )
        return Response(content=str(resp), media_type="application/xml")

    # ---------------- FALLBACK ----------------
    else:
        user_state[sender] = "MAIN_MENU"
        resp.message(main_menu())
        return Response(content=str(resp), media_type="application/xml")
