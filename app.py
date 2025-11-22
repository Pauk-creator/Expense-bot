import os
import datetime
from fastapi import FastAPI, Request, Response
from dotenv import load_dotenv
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import gspread

load_dotenv()

app = FastAPI()

# ---------------- GOOGLE SHEETS SETUP ----------------
SERVICE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

gc = gspread.service_account(filename=SERVICE_JSON)
sheet = gc.open_by_key(SHEET_ID).sheet1

# ---------------- TWILIO SETUP ----------------
twilio_client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)
TWILIO_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

# ---------------- USER STATE MEMORY ----------------
user_state = {}
user_temp = {}

# ---------------- MAIN MENU MESSAGE ----------------
def main_menu():
    return (
        "Welcome to the Expense Tracker Bot\n"
        "Please choose an option:\n\n"
        "1. Add Expense\n"
        "2. View Today Total\n"
        "3. View This Week Total\n"
        "4. View All-Time Total\n"
        "5. Exit"
    )

# ---------------- SAVE EXPENSE ----------------
def save_expense(sender, category, amount, notes):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    phone = sender
    sheet.append_row([timestamp, phone, category, amount, notes])

# ---------------- TOTAL CALCULATIONS ----------------
def calculate_total(sender, days=None):
    rows = sheet.get_all_values()[1:]
    total = 0
    now = datetime.datetime.now()

    for row in rows:
        timestamp, phone, category, amount, notes = row

        if phone != sender:
            continue

        row_time = datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M")

        if days and (now - row_time).days >= days:
            continue

        total += float(amount)

    return total

# ---------------- WEBHOOK ----------------
@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    form = await request.form()
    sender = form.get("From")
    message = form.get("Body", "").strip()

    resp = MessagingResponse()

    if sender not in user_state:
        user_state[sender] = "MAIN_MENU"

    state = user_state[sender]

    # ---------------- MAIN MENU ----------------
    if state == "MAIN_MENU":
        if message == "1":
            user_state[sender] = "AWAIT_CATEGORY"
            resp.message(
                "Choose category:\n"
                "1. Accommodation\n"
                "2. Meals & Catering\n"
                "3. Transport (Flights, Car Rental, Local Taxis)\n"
                "4. Venue Hire\n"
                "5. Vendor Payments\n"
                "6. Staff Hires (Temporary & Permanent)\n"
                "7. Security & Logistics\n"
                "8. Printing\n"
                "9. Other"
            )

        elif message == "2":
            total = calculate_total(sender, days=1)
            resp.message(f"Today's total spending: {total}")

        elif message == "3":
            total = calculate_total(sender, days=7)
            resp.message(f"This week's total spending: {total}")

        elif message == "4":
            total = calculate_total(sender)
            resp.message(f"All-time total spending: {total}")

        elif message == "5":
            resp.message("Thank you for using Expense Tracker.")
        else:
            resp.message(main_menu())

        return Response(content=str(resp), media_type="application/xml")

    # ---------------- CATEGORY SELECTION ----------------
    elif state == "AWAIT_CATEGORY":
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

        if message not in categories:
            resp.message(
                "Invalid choice. Choose category:\n"
                "1. Accommodation\n"
                "2. Meals & Catering\n"
                "3. Transport (Flights, Car Rental, Local Taxis)\n"
                "4. Venue Hire\n"
                "5. Vendor Payments\n"
                "6. Staff Hires\n"
                "7. Security & Logistics\n"
                "8. Printing\n"
                "9. Other"
            )
            return Response(content=str(resp), media_type="application/xml")

        user_temp[sender] = {"category": categories[message]}
        user_state[sender] = "AWAIT_AMOUNT"
        resp.message(f"Enter the amount spent on {categories[message]}:")
        return Response(content=str(resp), media_type="application/xml")

    # ---------------- AMOUNT ENTRY ----------------
    elif state == "AWAIT_AMOUNT":
        try:
            amount = float(message)
        except:
            resp.message("Invalid amount. Enter a number:")
            return Response(content=str(resp), media_type="application/xml")

        user_temp[sender]["amount"] = amount
        user_state[sender] = "AWAIT_NOTES"
        resp.message("Enter a note or comment (or type '-' for none):")
        return Response(content=str(resp), media_type="application/xml")

    # ---------------- NOTES ENTRY ----------------
    elif state == "AWAIT_NOTES":
        notes = message if message != "-" else ""

        category = user_temp[sender]["category"]
        amount = user_temp[sender]["amount"]

        save_expense(sender, category, amount, notes)

        user_state[sender] = "MAIN_MENU"
        user_temp.pop(sender, None)

        resp.message(
            f"Expense saved.\nYou spent {amount} on {category}.\n\n"
            "What would you like to do next:\n"
            "1. Add Another Expense\n"
            "2. View Today Total\n"
            "3. Main Menu"
        )
        return Response(content=str(resp), media_type="application/xml")

    # ---------------- FALLBACK ----------------
    else:
        user_state[sender] = "MAIN_MENU"
        resp.message(main_menu())
        return Response(content=str(resp), media_type="application/xml")
