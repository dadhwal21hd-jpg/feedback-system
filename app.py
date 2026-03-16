from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.middleware.sessions import SessionMiddleware
import sqlite3
import requests
import shutil
import os
import io
import csv
import logging
from datetime import datetime
import werkzeug.utils

# -------------------------------
# LOGGING
# -------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------------
# ENV VARIABLES
# -------------------------------
GREEN_API_ID    = os.getenv("GREEN_API_ID")
GREEN_API_TOKEN = os.getenv("GREEN_API_TOKEN")
BASE_URL        = os.getenv("BASE_URL", "https://feedback-system-1-299j.onrender.com")
LOGIN_PASSWORD  = os.getenv("LOGIN_PASSWORD", "admin123")    # Set this in Render env vars!
SECRET_KEY      = os.getenv("SECRET_KEY", "supersecretkey") # Set this in Render env vars!

logger.info("GREEN_API_ID loaded: %s", bool(GREEN_API_ID))
logger.info("GREEN_API_TOKEN loaded: %s", bool(GREEN_API_TOKEN))

GREEN_API_URL      = f"https://api.green-api.com/waInstance{GREEN_API_ID}/sendMessage/{GREEN_API_TOKEN}"
GREEN_API_FILE_URL = f"https://api.green-api.com/waInstance{GREEN_API_ID}/sendFileByUrl/{GREEN_API_TOKEN}"

# -------------------------------
# FASTAPI INIT
# -------------------------------
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

templates = Jinja2Templates(directory="templates")

os.makedirs("uploads", exist_ok=True)
os.makedirs("static", exist_ok=True)

app.mount("/static",  StaticFiles(directory="static"),  name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# -------------------------------
# DATABASE
# -------------------------------
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            person   TEXT,
            phone    TEXT,
            message  TEXT,
            voice    TEXT,
            date     TEXT,
            priority TEXT DEFAULT 'Medium',
            status   TEXT DEFAULT 'Open'
        )
    """)
    # Safe migration: add columns to existing databases
    for col, default in [("priority", "'Medium'"), ("status", "'Open'")]:
        try:
            conn.execute(f"ALTER TABLE feedback ADD COLUMN {col} TEXT DEFAULT {default}")
            logger.info("Migration: added column %s", col)
        except Exception:
            pass  # Already exists
    conn.commit()
    conn.close()

init_db()

# -------------------------------
# EMPLOYEE DIRECTORY
# -------------------------------
EMPLOYEES = {
    "Shubhneet Khurana": "919855562123",
    "Pramod": "919454181890",
    "Preeti": "919569888004",
    "Ratti": "919915977885",
    "Kriti Mam": "919876566555",
    "Lalhan Mishra HR": "919417713023",
    "Monty": "919592761974",
    "Raju Ji (Account)": "918728902080",
    "Joshi": "918146474566",
    "Sanjay Mishra": "919990848585",
    "Hari Prakash": "919878051243",
    "Deepak": "919914123301",
    "Mishra Ji": "919417713023",
    "Jyoti Mam": "919815466555",
    "Pathania": "919915025517",
    "Dheeraj": "919915025507",
    "Raja Sir": "919815266555",
    "Pooja": "918360559762",
    "Anil Sharma": "919501501524",
    "Om Dadhwal": "919878019868",
    "Suraj": "916280404745",
    "Raghav Sir": "919872166555",
    "Manish": "917009412112",
    "Jyoti Chauhan": "916284179661",
    "Purnima": "917508109704",
    "Dimpy Designer": "917508978054",
    "Ashish Designer": "916307746071",
    "Reetu Designer": "918427316217",
    "Pinky": "919915777055",
    "Sampita Designer": "917973324109",
    "Junaid Designer": "917626964671",
    "Ajit Lalu": "919779246668",
    "Garima Designer": "919988093070",
    "Rajshree": "917903732955",
    "Nishu": "918728098983",
    "Vijay": "918677805147",
    "Sonam": "919717411293",
    "Juhi": "919779188296",
    "Amisha": "919718286214",
    "Chanda": "918360777824",
    "Charanjit Sir Ac": "919463532277",
    "Sheelu": "918699261388",
    "Kamal": "917973870083",
    "Archit": "919915184763",
    "Anika": "919779304072",
    "Ritika Verma": "917626864357",
    "Jyoti Mittal": "917973611932",
    "Taniya": "918146918930",
    "Loveleen": "917340712478",
    "Ruhi": "917087099991",
    "Amrendra": "918677805147",
    "Manisha": "918699540589",
    "Nisha": "917696366101",
    "Sneha": "919814782494",
    "Nisha Singh": "916284179661",
    "Vishal Bhalla": "919915164202",
    "Tanuja Designer": "918534051443",
    "Sukhdev Master": "919465529426",
    "Sarvesh Master": "919872573254",
    "Kalpana Designer": "919560919628",
    "Rashmi EA": "919915990000",
    "Ram Niwas": "918591624713",
    "Raj Designer": "916283080516",
    "Palak": "917973373732",
    "Muskan": "916283801612",
    "Maluk Master": "918847041709",
    "Madhukar": "916387073378",
    "Kishan Master": "917986869939",
    "Kanhaiya Boiler": "919888030893",
    "Kailash": "919915025514",
    "Khushi": "917986436698",
    "Komal": "918968709850",
    "Monish Designer": "918437599681",
    "Deepak Sangini": "919914123301",
    "Bittu Sir": "919878430000",
    "Deepak CA": "919872588396",
    "Anwar Master": "918360429569",
}

# -------------------------------
# AUTH HELPERS
# -------------------------------
def is_logged_in(request: Request) -> bool:
    return request.session.get("logged_in") is True

def require_login(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=302)
    return None

# -------------------------------
# WHATSAPP HELPERS
# -------------------------------
def send_whatsapp(phone, message):
    try:
        payload = {"chatId": f"{phone}@c.us", "message": message}
        r = requests.post(GREEN_API_URL, json=payload, timeout=10)
        logger.info("WhatsApp text sent, status: %s", r.status_code)
    except Exception as e:
        logger.error("WhatsApp send failed: %s", e)

def send_whatsapp_voice(phone, file_url):
    try:
        payload = {"chatId": f"{phone}@c.us", "urlFile": file_url, "fileName": "voice.mp3"}
        r = requests.post(GREEN_API_FILE_URL, json=payload, timeout=10)
        logger.info("WhatsApp voice sent, status: %s", r.status_code)
    except Exception as e:
        logger.error("WhatsApp voice send failed: %s", e)

# -------------------------------
# LOGIN PAGE
# -------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if is_logged_in(request):
        return RedirectResponse("/", status_code=302)
    error = request.query_params.get("error", "")
    return templates.TemplateResponse("login.html", {"request": request, "error": error})

@app.post("/login")
def login(request: Request, password: str = Form(...)):
    if password == LOGIN_PASSWORD:
        request.session["logged_in"] = True
        return RedirectResponse("/", status_code=302)
    return RedirectResponse("/login?error=Wrong+password.+Try+again.", status_code=302)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)

# -------------------------------
# HOME PAGE
# -------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    redir = require_login(request)
    if redir: return redir

    conn = get_db()
    rows = conn.execute("SELECT * FROM feedback ORDER BY id DESC").fetchall()
    conn.close()

    feedback_list = []
    today_str     = datetime.now().strftime("%Y-%m-%d")
    total_feedback = len(rows)
    today_count = overdue = pending = resolved_count = 0

    for r in rows:
        try:
            date_obj = datetime.strptime(r["date"], "%Y-%m-%d")
            days = (datetime.now() - date_obj).days
        except Exception:
            days = 0

        status   = r["status"]   if r["status"]   else "Open"
        priority = r["priority"] if r["priority"] else "Medium"

        # Auto-flag as Follow Up if 15+ days old and still Open
        if days >= 15 and status == "Open":
            status = "Follow Up"

        if status == "Resolved":
            resolved_count += 1
        elif days >= 15:
            overdue += 1
        else:
            pending += 1

        if r["date"] == today_str:
            today_count += 1

        feedback_list.append({
            "id":       r["id"],
            "person":   r["person"],
            "message":  r["message"],
            "voice":    r["voice"],
            "date":     r["date"],
            "days":     days,
            "status":   status,
            "priority": priority,
        })

    return templates.TemplateResponse("index.html", {
        "request":   request,
        "feedback":  feedback_list,
        "employees": EMPLOYEES,
        "total":     total_feedback,
        "pending":   pending,
        "overdue":   overdue,
        "today":     today_count,
        "resolved":  resolved_count,
        "now":       datetime.now().strftime("%A, %d %B %Y"),
    })

# -------------------------------
# SUBMIT FEEDBACK
# -------------------------------
@app.post("/submit")
async def submit_feedback(
    request:  Request,
    person:   str        = Form(...),
    message:  str        = Form(...),
    priority: str        = Form("Medium"),
    voice:    UploadFile = File(None)
):
    redir = require_login(request)
    if redir: return redir

    if person not in EMPLOYEES:
        raise HTTPException(status_code=400, detail="Invalid employee")
    if priority not in ("High", "Medium", "Low"):
        priority = "Medium"

    phone      = EMPLOYEES[person]
    voice_path = ""

    if voice and voice.filename:
        import ffmpeg
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save original file first (whatever format browser sends)
        raw_path = f"uploads/{timestamp}_raw"
        with open(raw_path, "wb") as buf:
            shutil.copyfileobj(voice.file, buf)
        
        # Convert to proper MP3 using ffmpeg
        voice_path = f"uploads/{timestamp}_voice.mp3"
        try:
            ffmpeg.input(raw_path).output(voice_path, acodec='libmp3lame', ar='44100').run(quiet=True, overwrite_output=True)
            os.remove(raw_path)  # delete the raw file
        except Exception as e:
            logger.error("ffmpeg conversion failed: %s", e)
            voice_path = raw_path  # fallback to raw if conversion fails

    today = datetime.now().strftime("%Y-%m-%d")

    conn = get_db()
    conn.execute(
        "INSERT INTO feedback(person, phone, message, voice, date, priority, status) VALUES(?,?,?,?,?,?,?)",
        (person, phone, message, voice_path, today, priority, "Open")
    )
    conn.commit()
    conn.close()

    priority_emoji = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(priority, "")
    send_whatsapp(phone, f"📋 Feedback [{priority_emoji} {priority} Priority]:\n\n{message}")

    if voice_path:
        send_whatsapp_voice(phone, f"{BASE_URL}/{voice_path}")

    return RedirectResponse("/", status_code=303)

# -------------------------------
# RESOLVE / REOPEN
# -------------------------------
@app.post("/resolve/{id}")
def resolve_feedback(request: Request, id: int):
    redir = require_login(request)
    if redir: return redir
    conn = get_db()
    conn.execute("UPDATE feedback SET status='Resolved' WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)

@app.post("/reopen/{id}")
def reopen_feedback(request: Request, id: int):
    redir = require_login(request)
    if redir: return redir
    conn = get_db()
    conn.execute("UPDATE feedback SET status='Open' WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)

# -------------------------------
# EDIT FEEDBACK
# -------------------------------
@app.post("/edit/{id}")
def edit_feedback(request: Request, id: int, message: str = Form(...)):
    redir = require_login(request)
    if redir: return redir
    conn = get_db()
    conn.execute("UPDATE feedback SET message=? WHERE id=?", (message, id))
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)

# -------------------------------
# DELETE FEEDBACK
# -------------------------------
@app.post("/delete/{id}")
def delete_feedback(request: Request, id: int):
    redir = require_login(request)
    if redir: return redir
    conn = get_db()
    conn.execute("DELETE FROM feedback WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)

# -------------------------------
# EXPORT CSV
# -------------------------------
@app.get("/export")
def export_csv(request: Request):
    redir = require_login(request)
    if redir: return redir

    conn = get_db()
    rows = conn.execute(
        "SELECT id, person, phone, message, date, priority, status FROM feedback ORDER BY id DESC"
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Employee", "Phone", "Message", "Date", "Priority", "Status"])
    for r in rows:
        writer.writerow([r["id"], r["person"], r["phone"], r["message"],
                         r["date"], r["priority"], r["status"]])
    output.seek(0)

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=feedback_export.csv"}
    )