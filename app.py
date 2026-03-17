from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.middleware.sessions import SessionMiddleware

import psycopg2
import psycopg2.extras
import os
import requests
import shutil
import csv
import io
import hashlib
import logging
from datetime import datetime
from urllib.parse import urlparse
import werkzeug.utils


# -------------------------------
# LOGGING
# -------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -------------------------------
# ENV VARIABLES
# -------------------------------

GREEN_API_ID = os.getenv("GREEN_API_ID")
GREEN_API_TOKEN = os.getenv("GREEN_API_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

BASE_URL = os.getenv(
    "BASE_URL",
    "https://feedback-system-1-299j.onrender.com"
)

SECRET_KEY = os.getenv("SECRET_KEY", "secret123")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")


GREEN_API_URL = f"https://api.green-api.com/waInstance{GREEN_API_ID}/sendMessage/{GREEN_API_TOKEN}"
GREEN_API_FILE_URL = f"https://api.green-api.com/waInstance{GREEN_API_ID}/sendFileByUrl/{GREEN_API_TOKEN}"


# -------------------------------
# FASTAPI INIT
# -------------------------------

app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

templates = Jinja2Templates(directory="templates")

os.makedirs("uploads", exist_ok=True)
os.makedirs("static", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


# -------------------------------
# DATABASE CONNECTION
# -------------------------------

url = urlparse(DATABASE_URL)

DB_NAME = url.path[1:]
DB_USER = url.username
DB_PASSWORD = url.password
DB_HOST = url.hostname
DB_PORT = url.port


def get_db():

    conn = psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        sslmode="require"
    )

    return conn


# -------------------------------
# PASSWORD HASH
# -------------------------------

def hash_password(password: str):

    salted = f"feedflow_salt_{password}_feedflow"

    return hashlib.sha256(salted.encode()).hexdigest()


def verify_password(password, hashed):

    return hash_password(password) == hashed


# -------------------------------
# DATABASE INIT
# -------------------------------

def init_db():

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS feedback (
        id SERIAL PRIMARY KEY,
        person TEXT,
        phone TEXT,
        message TEXT,
        voice TEXT,
        date TEXT,
        priority TEXT DEFAULT 'Medium',
        status TEXT DEFAULT 'Open',
        submitted_by TEXT DEFAULT 'admin',
        followup_days INTEGER DEFAULT 15
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT,
        created_at TEXT
    )
    """)

    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]

    if count == 0:

        cur.execute(
            "INSERT INTO users(username,password,role,created_at) VALUES(%s,%s,%s,%s)",
            (
                "admin",
                hash_password(ADMIN_PASSWORD),
                "admin",
                datetime.now().strftime("%Y-%m-%d")
            )
        )

    conn.commit()
    cur.close()
    conn.close()


init_db()


# -------------------------------
# EMPLOYEE DIRECTORY
# -------------------------------

EMPLOYEES = {
    "Om Dadhwal": "919878019868",
    "Pinky": "919915777055",
    "Pramod": "919454181890",
    "Deepak": "919914123301"
}


# -------------------------------
# WHATSAPP HELPERS
# -------------------------------

def send_whatsapp(phone, message):

    payload = {
        "chatId": f"{phone}@c.us",
        "message": message
    }

    try:

        r = requests.post(GREEN_API_URL, json=payload)

        logger.info("WhatsApp text sent: %s", r.status_code)

    except Exception as e:

        logger.error("WhatsApp error: %s", e)


def send_whatsapp_voice(phone, file_url):

    payload = {
        "chatId": f"{phone}@c.us",
        "urlFile": file_url,
        "fileName": "voice.mp3"
    }

    try:

        r = requests.post(GREEN_API_FILE_URL, json=payload)

        logger.info("WhatsApp voice sent: %s", r.status_code)

    except Exception as e:

        logger.error("Voice error: %s", e)


# -------------------------------
# AUTH HELPERS
# -------------------------------

def is_logged_in(request):

    return request.session.get("logged_in")


def require_login(request):

    if not is_logged_in(request):

        return RedirectResponse("/login")

    return None


# -------------------------------
# LOGIN PAGE
# -------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):

    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        "SELECT * FROM users WHERE username=%s",
        (username,)
    )

    user = cur.fetchone()

    cur.close()
    conn.close()

    if user and verify_password(password, user["password"]):

        request.session["logged_in"] = True
        request.session["username"] = username

        return RedirectResponse("/", status_code=302)

    return RedirectResponse("/login", status_code=302)


@app.get("/logout")
def logout(request: Request):

    request.session.clear()

    return RedirectResponse("/login")


# -------------------------------
# HOME PAGE
# -------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):

    redir = require_login(request)

    if redir:
        return redir

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM feedback ORDER BY id DESC")

    rows = cur.fetchall()

    cur.close()
    conn.close()

    feedback_list = []

    for r in rows:

        date_obj = datetime.strptime(r["date"], "%Y-%m-%d")

        days = (datetime.now() - date_obj).days

        feedback_list.append({
            "id": r["id"],
            "person": r["person"],
            "message": r["message"],
            "date": r["date"],
            "days": days,
            "status": r["status"]
        })

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "feedback": feedback_list,
            "employees": EMPLOYEES
        }
    )


# -------------------------------
# SUBMIT FEEDBACK
# -------------------------------

@app.post("/submit")
async def submit_feedback(

    request: Request,
    person: str = Form(...),
    message: str = Form(...),
    voice: UploadFile = File(None)

):

    redir = require_login(request)

    if redir:
        return redir

    phone = EMPLOYEES[person]

    voice_path = ""

    if voice and voice.filename:

        filename = werkzeug.utils.secure_filename(voice.filename)

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

        filename = f"{timestamp}_{filename}"

        voice_path = f"uploads/{filename}"

        with open(voice_path, "wb") as buffer:

            shutil.copyfileobj(voice.file, buffer)

    today = datetime.now().strftime("%Y-%m-%d")

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO feedback(person,phone,message,voice,date)
        VALUES(%s,%s,%s,%s,%s)
        """,
        (person, phone, message, voice_path, today)
    )

    conn.commit()

    cur.close()
    conn.close()

    send_whatsapp(phone, f"Feedback:\n\n{message}")

    if voice_path:

        voice_url = f"{BASE_URL}/{voice_path}"

        send_whatsapp_voice(phone, voice_url)

    return RedirectResponse("/", status_code=303)


# -------------------------------
# DELETE
# -------------------------------

@app.post("/delete/{id}")
def delete_feedback(request: Request, id: int):

    redir = require_login(request)

    if redir:
        return redir

    conn = get_db()
    cur = conn.cursor()

    cur.execute("DELETE FROM feedback WHERE id=%s", (id,))

    conn.commit()

    cur.close()
    conn.close()

    return RedirectResponse("/", status_code=303)


# -------------------------------
# EXPORT CSV
# -------------------------------

@app.get("/export")
def export_csv(request: Request):

    redir = require_login(request)

    if redir:
        return redir

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM feedback ORDER BY id DESC")

    rows = cur.fetchall()

    cur.close()
    conn.close()

    output = io.StringIO()

    writer = csv.writer(output)

    writer.writerow(["ID", "Employee", "Message", "Date", "Status"])

    for r in rows:

        writer.writerow([
            r["id"],
            r["person"],
            r["message"],
            r["date"],
            r["status"]
        ])

    output.seek(0)

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=feedback.csv"}
    )