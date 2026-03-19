from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.middleware.sessions import SessionMiddleware
import psycopg2
import psycopg2.extras
import requests as req
import shutil
import os
import io
import csv
import logging
import hashlib
from datetime import datetime
import werkzeug.utils
from typing import List
import cloudinary
import cloudinary.uploader

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
BASE_URL        = os.getenv("BASE_URL", "https://feedback-system-jdy8.onrender.com")
SECRET_KEY      = os.getenv("SECRET_KEY", "supersecretkey")
ADMIN_PASSWORD  = os.getenv("ADMIN_PASSWORD", "admin123")
DATABASE_URL    = os.getenv("DATABASE_URL")

CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY    = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

# Configure Cloudinary
cloudinary.config(
    cloud_name = CLOUDINARY_CLOUD_NAME,
    api_key    = CLOUDINARY_API_KEY,
    api_secret = CLOUDINARY_API_SECRET,
    secure     = True
)

logger.info("GREEN_API_ID loaded: %s", bool(GREEN_API_ID))
logger.info("DATABASE_URL loaded: %s", bool(DATABASE_URL))
logger.info("CLOUDINARY loaded: %s", bool(CLOUDINARY_CLOUD_NAME))

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
# PASSWORD HASHING
# -------------------------------
def hash_password(password: str) -> str:
    salted = f"feedflow_salt_{password}_feedflow"
    return hashlib.sha256(salted.encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hash_password(password) == hashed

# -------------------------------
# DATABASE (PostgreSQL)
# -------------------------------
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # Feedback table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id            SERIAL PRIMARY KEY,
            person        TEXT,
            phone         TEXT,
            message       TEXT,
            voice         TEXT,
            date          TEXT,
            priority      TEXT DEFAULT 'Medium',
            status        TEXT DEFAULT 'Open',
            submitted_by  TEXT DEFAULT 'admin',
            followup_days INTEGER DEFAULT 15,
            image_url     TEXT DEFAULT ''
        )
    """)

    # Settings table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Users table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id         SERIAL PRIMARY KEY,
            username   TEXT UNIQUE NOT NULL,
            password   TEXT NOT NULL,
            role       TEXT DEFAULT 'user',
            created_at TEXT
        )
    """)

    # Images table — separate section for standalone image uploads
    cur.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id           SERIAL PRIMARY KEY,
            title        TEXT,
            image_url    TEXT,
            public_id    TEXT,
            uploaded_by  TEXT,
            uploaded_at  TEXT
        )
    """)

    # Safe migrations for feedback table
    for col, col_type in [
        ("priority",      "TEXT DEFAULT 'Medium'"),
        ("status",        "TEXT DEFAULT 'Open'"),
        ("submitted_by",  "TEXT DEFAULT 'admin'"),
        ("followup_days", "INTEGER DEFAULT 15"),
        ("image_url",     "TEXT DEFAULT ''"),
    ]:
        try:
            cur.execute(f"ALTER TABLE feedback ADD COLUMN IF NOT EXISTS {col} {col_type}")
        except Exception as e:
            logger.warning("Migration warning for %s: %s", col, e)

    # Default settings
    cur.execute("INSERT INTO settings(key, value) VALUES('followup_days', '15') ON CONFLICT (key) DO NOTHING")

    # Create default admin if no users exist
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    if count == 0:
        cur.execute(
            "INSERT INTO users(username, password, role, created_at) VALUES(%s,%s,%s,%s)",
            ("admin", hash_password(ADMIN_PASSWORD), "admin", datetime.now().strftime("%Y-%m-%d"))
        )
        logger.info("Default admin user created")

    conn.commit()
    cur.close()
    conn.close()

init_db()

# -------------------------------
# SETTINGS HELPERS
# -------------------------------
def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else default

def set_setting(key: str, value: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO settings(key, value) VALUES(%s,%s) ON CONFLICT (key) DO UPDATE SET value=%s",
                (key, value, value))
    conn.commit()
    cur.close()
    conn.close()

def get_followup_days() -> int:
    try:
        return int(get_setting("followup_days", "15"))
    except ValueError:
        return 15

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
def get_current_user(request: Request):
    return request.session.get("username")

def get_current_role(request: Request):
    return request.session.get("role", "user")

def is_logged_in(request: Request) -> bool:
    return request.session.get("logged_in") is True

def is_admin(request: Request) -> bool:
    return request.session.get("role") == "admin"

def require_login(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=302)
    return None

def require_admin(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=302)
    if not is_admin(request):
        return RedirectResponse("/", status_code=302)
    return None

# -------------------------------
# WHATSAPP HELPERS
# -------------------------------
def send_whatsapp(phone, message):
    try:
        payload = {"chatId": f"{phone}@c.us", "message": message}
        r = req.post(GREEN_API_URL, json=payload, timeout=10)
        logger.info("WhatsApp text sent, status: %s", r.status_code)
    except Exception as e:
        logger.error("WhatsApp send failed: %s", e)

def send_whatsapp_voice(phone, file_url):
    try:
        payload = {"chatId": f"{phone}@c.us", "urlFile": file_url, "fileName": "voice.mp3"}
        r = req.post(GREEN_API_FILE_URL, json=payload, timeout=10)
        logger.info("WhatsApp voice sent, status: %s", r.status_code)
    except Exception as e:
        logger.error("WhatsApp voice send failed: %s", e)

def send_whatsapp_image(phone, image_url, caption=""):
    try:
        payload = {
            "chatId": f"{phone}@c.us",
            "urlFile": image_url,
            "fileName": "image.jpg",
            "caption": caption
        }
        r = req.post(GREEN_API_FILE_URL, json=payload, timeout=10)
        logger.info("WhatsApp image sent, status: %s", r.status_code)
    except Exception as e:
        logger.error("WhatsApp image send failed: %s", e)

# -------------------------------
# CLOUDINARY UPLOAD HELPER
# -------------------------------
def upload_to_cloudinary(file_bytes, filename: str, folder: str = "feedflow"):
    try:
        result = cloudinary.uploader.upload(
            file_bytes,
            folder=folder,
            public_id=f"{folder}/{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}",
            overwrite=True,
            resource_type="image"
        )
        return result.get("secure_url"), result.get("public_id")
    except Exception as e:
        logger.error("Cloudinary upload failed: %s", e)
        return None, None

# -------------------------------
# LOGIN
# -------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if is_logged_in(request):
        return RedirectResponse("/", status_code=302)
    error = request.query_params.get("error", "")
    return templates.TemplateResponse("login.html", {"request": request, "error": error})

@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE username=%s", (username.strip().lower(),))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if user and verify_password(password, user["password"]):
        request.session["logged_in"] = True
        request.session["username"]  = user["username"]
        request.session["role"]      = user["role"]
        return RedirectResponse("/", status_code=302)

    return RedirectResponse("/login?error=Wrong+username+or+password.", status_code=302)

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
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if is_admin(request):
        cur.execute("SELECT * FROM feedback ORDER BY id DESC")
    else:
        cur.execute("SELECT * FROM feedback WHERE submitted_by=%s ORDER BY id DESC",
                    (get_current_user(request),))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    global_followup = get_followup_days()
    today_str        = datetime.now().strftime("%Y-%m-%d")
    total_feedback   = len(rows)
    today_count = overdue = pending = resolved_count = 0
    feedback_list = []

    for r in rows:
        try:
            date_obj = datetime.strptime(r["date"], "%Y-%m-%d")
            days = (datetime.now() - date_obj).days
        except Exception:
            days = 0

        status       = r["status"]       or "Open"
        priority     = r["priority"]     or "Medium"
        submitted_by = r["submitted_by"] or "—"
        image_url    = r["image_url"]    or ""

        try:
            fu_days = int(r["followup_days"]) if r["followup_days"] else global_followup
        except Exception:
            fu_days = global_followup

        if days >= fu_days and status == "Open":
            status = "Follow Up"

        if status == "Resolved":
            resolved_count += 1
        elif days >= fu_days:
            overdue += 1
        else:
            pending += 1

        if r["date"] == today_str:
            today_count += 1

        feedback_list.append({
            "id":           r["id"],
            "person":       r["person"],
            "message":      r["message"],
            "voice":        r["voice"],
            "date":         r["date"],
            "days":         days,
            "status":       status,
            "priority":     priority,
            "submitted_by": submitted_by,
            "followup_days": fu_days,
            "image_url":    image_url,
        })

    return templates.TemplateResponse("index.html", {
        "request":       request,
        "feedback":      feedback_list,
        "employees":     EMPLOYEES,
        "total":         total_feedback,
        "pending":       pending,
        "overdue":       overdue,
        "today":         today_count,
        "resolved":      resolved_count,
        "followup_days": global_followup,
        "now":           datetime.now().strftime("%A, %d %B %Y"),
        "current_user":  get_current_user(request),
        "current_role":  get_current_role(request),
    })

# -------------------------------
# SUBMIT FEEDBACK
# -------------------------------
@app.post("/submit")
async def submit_feedback(
    request:       Request,
    person:        str        = Form(...),
    message:       str        = Form(...),
    priority:      str        = Form("Medium"),
    followup_days: int        = Form(15),
    voice:         UploadFile = File(None),
    images:        List[UploadFile] = File(None)
):
    redir = require_login(request)
    if redir: return redir

    if person not in EMPLOYEES:
        raise HTTPException(status_code=400, detail="Invalid employee")
    if priority not in ("High", "Medium", "Low"):
        priority = "Medium"
    followup_days = max(1, min(365, followup_days))

    phone        = EMPLOYEES[person]
    voice_path   = ""
    image_url    = ""
    submitted_by = get_current_user(request) or "unknown"

    # Handle voice note
    if voice and voice.filename:
        timestamp       = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_filename   = werkzeug.utils.secure_filename(voice.filename) or "voice.mp3"
        unique_filename = f"{timestamp}_{safe_filename}"
        voice_path      = f"uploads/{unique_filename}"
        with open(voice_path, "wb") as buf:
            shutil.copyfileobj(voice.file, buf)

    # Handle multiple image uploads to Cloudinary
    image_urls = []
    if images:
        for image in images:
            if image and image.filename:
                image_bytes = await image.read()
                safe_name   = werkzeug.utils.secure_filename(image.filename) or "image.jpg"
                url, pub_id = upload_to_cloudinary(image_bytes, safe_name, folder="feedflow/feedback")
                if url:
                    image_urls.append(url)
    image_url = ",".join(image_urls)

    today = datetime.now().strftime("%Y-%m-%d")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO feedback(person, phone, message, voice, date, priority, status, submitted_by, followup_days, image_url) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (person, phone, message, voice_path, today, priority, "Open", submitted_by, followup_days, image_url)
    )
    conn.commit()
    cur.close()
    conn.close()

    priority_emoji = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(priority, "")
    send_whatsapp(phone,
        f"📋 Feedback [{priority_emoji} {priority} Priority]:\n\n"
        f"{message}\n\n"
        f"⏰ Follow-up in {followup_days} days if unresolved.\n"
        f"👤 Sent by: {submitted_by}"
    )

    if voice_path:
        send_whatsapp_voice(phone, f"{BASE_URL}/{voice_path}")

    for url in image_urls:
        send_whatsapp_image(phone, url, caption=f"📎 Image attached to feedback from {submitted_by}")

    return RedirectResponse("/", status_code=303)

# -------------------------------
# IMAGE GALLERY PAGE
# -------------------------------
@app.get("/images", response_class=HTMLResponse)
def images_page(request: Request):
    redir = require_login(request)
    if redir: return redir

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if is_admin(request):
        cur.execute("SELECT * FROM images ORDER BY id DESC")
    else:
        cur.execute("SELECT * FROM images WHERE uploaded_by=%s ORDER BY id DESC",
                    (get_current_user(request),))
    images = cur.fetchall()
    cur.close()
    conn.close()

    return templates.TemplateResponse("images.html", {
        "request":      request,
        "images":       images,
        "employees":    EMPLOYEES,
        "current_user": get_current_user(request),
        "current_role": get_current_role(request),
        "now":          datetime.now().strftime("%A, %d %B %Y"),
    })

@app.post("/images/upload")
async def upload_image(
    request:     Request,
    title:       str        = Form(""),
    image:       UploadFile = File(...),
    send_to_wa:  str        = Form(""),
    wa_employee: str        = Form(""),
):
    redir = require_login(request)
    if redir: return redir

    if not image or not image.filename:
        return RedirectResponse("/images?error=No+image+selected", status_code=303)

    image_bytes = await image.read()
    safe_name   = werkzeug.utils.secure_filename(image.filename) or "image.jpg"
    url, pub_id = upload_to_cloudinary(image_bytes, safe_name, folder="feedflow/gallery")

    if not url:
        return RedirectResponse("/images?error=Upload+failed", status_code=303)

    uploaded_by = get_current_user(request) or "unknown"
    uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO images(title, image_url, public_id, uploaded_by, uploaded_at) VALUES(%s,%s,%s,%s,%s)",
        (title or safe_name, url, pub_id, uploaded_by, uploaded_at)
    )
    conn.commit()
    cur.close()
    conn.close()

    # Send to WhatsApp if requested
    if send_to_wa and wa_employee and wa_employee in EMPLOYEES:
        phone = EMPLOYEES[wa_employee]
        send_whatsapp_image(phone, url, caption=f"📸 {title or 'Image'} — shared by {uploaded_by}")

    return RedirectResponse("/images", status_code=303)

@app.post("/images/delete/{id}")
def delete_image(request: Request, id: int):
    redir = require_login(request)
    if redir: return redir

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM images WHERE id=%s", (id,))
    img = cur.fetchone()

    if img:
        # Delete from Cloudinary too
        try:
            if img["public_id"]:
                cloudinary.uploader.destroy(img["public_id"])
        except Exception as e:
            logger.error("Cloudinary delete failed: %s", e)

        cur.execute("DELETE FROM images WHERE id=%s", (id,))
        conn.commit()

    cur.close()
    conn.close()
    return RedirectResponse("/images", status_code=303)

# -------------------------------
# RESOLVE / REOPEN
# -------------------------------
@app.post("/resolve/{id}")
def resolve_feedback(request: Request, id: int):
    redir = require_login(request)
    if redir: return redir
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE feedback SET status='Resolved' WHERE id=%s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    return RedirectResponse("/", status_code=303)

@app.post("/reopen/{id}")
def reopen_feedback(request: Request, id: int):
    redir = require_login(request)
    if redir: return redir
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE feedback SET status='Open' WHERE id=%s", (id,))
    conn.commit()
    cur.close()
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
    cur = conn.cursor()
    cur.execute("UPDATE feedback SET message=%s WHERE id=%s", (message, id))
    conn.commit()
    cur.close()
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
    if redir: return redir

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if is_admin(request):
        cur.execute("SELECT id, person, phone, message, date, priority, status, submitted_by, followup_days FROM feedback ORDER BY id DESC")
    else:
        cur.execute("SELECT id, person, phone, message, date, priority, status, submitted_by, followup_days FROM feedback WHERE submitted_by=%s ORDER BY id DESC",
                    (get_current_user(request),))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Employee", "Phone", "Message", "Date", "Priority", "Status", "Submitted By", "Follow Up Days"])
    for r in rows:
        writer.writerow([r["id"], r["person"], r["phone"], r["message"],
                         r["date"], r["priority"], r["status"], r["submitted_by"], r["followup_days"]])
    output.seek(0)

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=feedback_export.csv"}
    )

# -------------------------------
# SETTINGS (admin only)
# -------------------------------
@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    redir = require_admin(request)
    if redir: return redir

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, username, role, created_at FROM users ORDER BY id")
    users = cur.fetchall()
    cur.close()
    conn.close()

    return templates.TemplateResponse("settings.html", {
        "request":       request,
        "followup_days": get_followup_days(),
        "users":         users,
        "saved":         request.query_params.get("saved", ""),
        "error":         request.query_params.get("error", ""),
        "current_user":  get_current_user(request),
        "current_role":  get_current_role(request),
    })

@app.post("/settings")
def save_settings(request: Request, followup_days: int = Form(...)):
    redir = require_admin(request)
    if redir: return redir
    set_setting("followup_days", str(max(1, min(365, followup_days))))
    return RedirectResponse("/settings?saved=1", status_code=303)

# -------------------------------
# USER MANAGEMENT (admin only)
# -------------------------------
@app.post("/users/add")
def add_user(request: Request, username: str = Form(...), password: str = Form(...), role: str = Form("user")):
    redir = require_admin(request)
    if redir: return redir

    username = username.strip().lower()
    if not username or not password:
        return RedirectResponse("/settings?error=Username+and+password+required.", status_code=303)
    if role not in ("admin", "user"):
        role = "user"

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users(username, password, role, created_at) VALUES(%s,%s,%s,%s)",
            (username, hash_password(password), role, datetime.now().strftime("%Y-%m-%d"))
        )
        conn.commit()
        cur.close()
        conn.close()
        return RedirectResponse("/settings?saved=1", status_code=303)
    except psycopg2.IntegrityError:
        return RedirectResponse("/settings?error=Username+already+exists.", status_code=303)

@app.post("/users/delete/{id}")
def delete_user(request: Request, id: int):
    redir = require_admin(request)
    if redir: return redir

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT username, role FROM users WHERE id=%s", (id,))
    user = cur.fetchone()

    if user and user["username"] == get_current_user(request):
        cur.close(); conn.close()
        return RedirectResponse("/settings?error=You+cannot+delete+your+own+account.", status_code=303)

    cur.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
    admin_count = cur.fetchone()["count"]
    if user and user["role"] == "admin" and admin_count <= 1:
        cur.close(); conn.close()
        return RedirectResponse("/settings?error=Cannot+delete+the+last+admin.", status_code=303)

    cur.execute("DELETE FROM users WHERE id=%s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    return RedirectResponse("/settings?saved=1", status_code=303)

@app.post("/users/change-password")
def change_password(request: Request, old_password: str = Form(...), new_password: str = Form(...)):
    redir = require_login(request)
    if redir: return redir

    username = get_current_user(request)
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE username=%s", (username,))
    user = cur.fetchone()

    if not user or not verify_password(old_password, user["password"]):
        cur.close(); conn.close()
        return RedirectResponse("/settings?error=Current+password+is+wrong.", status_code=303)

    cur.execute("UPDATE users SET password=%s WHERE username=%s", (hash_password(new_password), username))
    conn.commit()
    cur.close()
    conn.close()
    return RedirectResponse("/settings?saved=1", status_code=303)