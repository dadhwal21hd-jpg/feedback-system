from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.middleware.sessions import SessionMiddleware
import psycopg2
import psycopg2.extras
from urllib.parse import urlparse
import requests
import shutil
import os
import io
import csv
import logging
import hashlib
from datetime import datetime
import werkzeug.utils
import cloudinary
import cloudinary.uploader

# ========================
# CLOUDINARY SETUP
# ========================
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

# ========================
# LOGGING
# ========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========================
# ENV VARIABLES
# ========================
GREEN_API_ID    = os.getenv("GREEN_API_ID")
GREEN_API_TOKEN = os.getenv("GREEN_API_TOKEN")
BASE_URL        = os.getenv("BASE_URL", "https://feedback-system-1-299j.onrender.com")
SECRET_KEY      = os.getenv("SECRET_KEY", "supersecretkey")
ADMIN_PASSWORD  = os.getenv("ADMIN_PASSWORD", "admin123")
DATABASE_URL    = os.getenv("DATABASE_URL")

logger.info("GREEN_API_ID loaded: %s", bool(GREEN_API_ID))
logger.info("GREEN_API_TOKEN loaded: %s", bool(GREEN_API_TOKEN))
logger.info("DATABASE_URL loaded: %s", bool(DATABASE_URL))
logger.info("Cloudinary configured: %s", bool(os.getenv("CLOUDINARY_CLOUD_NAME")))

if not DATABASE_URL:
    raise Exception("DATABASE_URL environment variable is not set!")

GREEN_API_URL      = f"https://api.green-api.com/waInstance{GREEN_API_ID}/sendMessage/{GREEN_API_TOKEN}"
GREEN_API_FILE_URL = f"https://api.green-api.com/waInstance{GREEN_API_ID}/sendFileByUrl/{GREEN_API_TOKEN}"

# ========================
# FASTAPI INIT
# ========================
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

templates = Jinja2Templates(directory="templates")

os.makedirs("uploads", exist_ok=True)
os.makedirs("static", exist_ok=True)

app.mount("/static",  StaticFiles(directory="static"),  name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ========================
# DATABASE CONNECTION
# ========================
def get_db():
    """Get a new PostgreSQL connection"""
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        conn.set_session(autocommit=False)
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise

def dict_cursor(conn):
    """Execute query and return results as dictionaries"""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# ========================
# PASSWORD HASHING
# ========================
def hash_password(password: str) -> str:
    salted = f"feedflow_salt_{password}_feedflow"
    return hashlib.sha256(salted.encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hash_password(password) == hashed

# ========================
# DATABASE INITIALIZATION
# ========================
def init_db():
    """Initialize PostgreSQL database with required tables"""
    try:
        conn = get_db()
        cur = dict_cursor(conn)

        # Create feedback table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id SERIAL PRIMARY KEY,
            person TEXT NOT NULL,
            phone TEXT NOT NULL,
            message TEXT,
            voice TEXT,
            images TEXT,
            date DATE NOT NULL,
            priority TEXT DEFAULT 'Medium',
            status TEXT DEFAULT 'Open',
            submitted_by TEXT DEFAULT 'admin',
            followup_days INTEGER DEFAULT 15,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # Create settings table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        # Create users table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            created_at DATE DEFAULT CURRENT_DATE
        )
        """)

        # Insert default settings if not exist
        cur.execute(
            "INSERT INTO settings(key, value) VALUES(%s, %s) ON CONFLICT (key) DO NOTHING",
            ("followup_days", "15")
        )

        # Check if admin exists
        cur.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
        admin_count = cur.fetchone()[0]

        if admin_count == 0:
            cur.execute(
                "INSERT INTO users(username, password, role, created_at) VALUES(%s, %s, %s, CURRENT_DATE)",
                ("admin", hash_password(ADMIN_PASSWORD), "admin")
            )
            logger.info("Default admin user created")

        conn.commit()
        cur.close()
        conn.close()
        logger.info("Database initialized successfully")

    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        raise

# Initialize database on startup
try:
    init_db()
except Exception as e:
    logger.error(f"Failed to initialize database: {e}")

# ========================
# SETTINGS HELPERS
# ========================
def get_setting(key: str, default: str = "") -> str:
    """Get a setting from the database"""
    try:
        conn = get_db()
        cur = dict_cursor(conn)
        cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else default
    except Exception as e:
        logger.error(f"Error getting setting: {e}")
        return default

def set_setting(key: str, value: str):
    """Set a setting in the database"""
    try:
        conn = get_db()
        cur = dict_cursor(conn)
        cur.execute(
            "INSERT INTO settings(key, value) VALUES(%s, %s) ON CONFLICT (key) DO UPDATE SET value=%s",
            (key, value, value)
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"Setting {key} saved: {value}")
    except Exception as e:
        logger.error(f"Error setting value: {e}")

def get_followup_days() -> int:
    try:
        return int(get_setting("followup_days", "15"))
    except ValueError:
        return 15

# ========================
# CLOUDINARY FUNCTIONS
# ========================
def upload_to_cloudinary(file: UploadFile) -> str:
    """
    Upload image to Cloudinary
    Returns the secure URL of the uploaded image
    """
    try:
        # Read file content
        content = file.file.read()
        file.file.seek(0)
        
        # Upload to Cloudinary
        result = cloudinary.uploader.upload(
            file.file,
            folder="feedflow/feedback",
            resource_type="image",
            quality="auto",
            fetch_format="auto",
            secure=True
        )
        
        logger.info(f"Image uploaded to Cloudinary: {result['public_id']}")
        return result['secure_url']
    
    except Exception as e:
        logger.error(f"Cloudinary upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Image upload failed: {str(e)}")

def upload_voice_to_cloudinary(file: UploadFile) -> str:
    """
    Upload voice note to Cloudinary
    Returns the secure URL of the uploaded file
    """
    try:
        result = cloudinary.uploader.upload(
            file.file,
            folder="feedflow/voices",
            resource_type="auto",
            secure=True
        )
        
        logger.info(f"Voice uploaded to Cloudinary: {result['public_id']}")
        return result['secure_url']
    
    except Exception as e:
        logger.error(f"Cloudinary voice upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Voice upload failed: {str(e)}")

# ========================
# EMPLOYEE DIRECTORY
# ========================
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

# ========================
# AUTH HELPERS
# ========================
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

# ========================
# WHATSAPP HELPERS
# ========================
def send_whatsapp(phone, message):
    try:
        payload = {"chatId": f"{phone}@c.us", "message": message}
        r = requests.post(GREEN_API_URL, json=payload, timeout=10)
        logger.info("WhatsApp text sent, status: %s", r.status_code)
    except Exception as e:
        logger.error("WhatsApp send failed: %s", e)

def send_whatsapp_file(phone, file_url, file_type="image"):
    try:
        if file_type == "voice":
            payload = {"chatId": f"{phone}@c.us", "urlFile": file_url, "fileName": "voice.mp3"}
        else:
            payload = {"chatId": f"{phone}@c.us", "urlFile": file_url, "fileName": "feedback_image.jpg"}
        
        r = requests.post(GREEN_API_FILE_URL, json=payload, timeout=10)
        logger.info("WhatsApp %s sent, status: %s", file_type, r.status_code)
    except Exception as e:
        logger.error("WhatsApp %s send failed: %s", file_type, e)

# ========================
# LOGIN
# ========================
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if is_logged_in(request):
        return RedirectResponse("/", status_code=302)
    error = request.query_params.get("error", "")
    return templates.TemplateResponse("login.html", {"request": request, "error": error})

@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    try:
        conn = get_db()
        cur = dict_cursor(conn)
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
    except Exception as e:
        logger.error(f"Login error: {e}")
        return RedirectResponse("/login?error=Database+error.", status_code=302)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)

# ========================
# HOME PAGE
# ========================
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    redir = require_login(request)
    if redir: return redir

    try:
        conn = get_db()
        cur = dict_cursor(conn)
        
        if is_admin(request):
            cur.execute("SELECT * FROM feedback ORDER BY id DESC")
        else:
            cur.execute(
                "SELECT * FROM feedback WHERE submitted_by=%s ORDER BY id DESC",
                (get_current_user(request),)
            )
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
                date_obj = datetime.strptime(str(r["date"]), "%Y-%m-%d")
                days = (datetime.now() - date_obj).days
            except Exception:
                days = 0

            status       = r["status"]       if r["status"]       else "Open"
            priority     = r["priority"]     if r["priority"]     else "Medium"
            submitted_by = r["submitted_by"] if r["submitted_by"] else "—"

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

            if str(r["date"]) == today_str:
                today_count += 1

            feedback_list.append({
                "id":           r["id"],
                "person":       r["person"],
                "message":      r["message"],
                "voice":        r["voice"],
                "images":       r["images"],
                "date":         str(r["date"]),
                "days":         days,
                "status":       status,
                "priority":     priority,
                "submitted_by": submitted_by,
                "followup_days": fu_days,
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
    except Exception as e:
        logger.error(f"Home page error: {e}")
        raise HTTPException(status_code=500, detail="Database error")

# ========================
# SUBMIT FEEDBACK
# ========================
@app.post("/submit")
async def submit_feedback(
    request:      Request,
    person:       str        = Form(...),
    message:      str        = Form(...),
    priority:     str        = Form("Medium"),
    followup_days: int       = Form(15),
    voice:        UploadFile = File(None),
    images:       list[UploadFile] = File(None)
):
    redir = require_login(request)
    if redir: return redir

    if person not in EMPLOYEES:
        raise HTTPException(status_code=400, detail="Invalid employee")
    if priority not in ("High", "Medium", "Low"):
        priority = "Medium"
    followup_days = max(1, min(365, followup_days))

    phone        = EMPLOYEES[person]
    voice_url    = ""
    image_urls   = []
    submitted_by = get_current_user(request) or "unknown"

    # Process voice file (upload to Cloudinary)
    if voice and voice.filename:
        try:
            voice_url = upload_voice_to_cloudinary(voice)
            logger.info(f"Voice uploaded to Cloudinary: {voice_url}")
        except Exception as e:
            logger.error(f"Voice upload error: {e}")
            # Continue without voice if upload fails

    # Process image files (upload to Cloudinary)
    if images:
        for idx, image in enumerate(images):
            if image and image.filename:
                try:
                    image_url = upload_to_cloudinary(image)
                    image_urls.append(image_url)
                    logger.info(f"Image {idx+1} uploaded to Cloudinary: {image_url}")
                except Exception as e:
                    logger.error(f"Image {idx+1} upload error: {e}")
                    # Continue with other images if one fails

    images_json = ",".join(image_urls) if image_urls else None
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        conn = get_db()
        cur = dict_cursor(conn)
        cur.execute(
            """INSERT INTO feedback(person, phone, message, voice, images, date, priority, status, submitted_by, followup_days)
               VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (person, phone, message, voice_url, images_json, today, priority, "Open", submitted_by, followup_days)
        )
        conn.commit()
        cur.close()
        conn.close()

        # Send WhatsApp notification
        priority_emoji = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(priority, "")
        send_whatsapp(phone,
            f"📋 Feedback [{priority_emoji} {priority} Priority]:\n\n"
            f"{message}\n\n"
            f"⏰ Follow-up due in {followup_days} days if unresolved.\n"
            f"👤 Sent by: {submitted_by}"
        )

        # Send voice if available
        if voice_url:
            send_whatsapp_file(phone, voice_url, file_type="voice")

        # Send images if available
        for img_url in image_urls:
            send_whatsapp_file(phone, img_url, file_type="image")

        return RedirectResponse("/", status_code=303)
    except Exception as e:
        logger.error(f"Submit feedback error: {e}")
        raise HTTPException(status_code=500, detail="Error saving feedback")

# ========================
# RESOLVE / REOPEN
# ========================
@app.post("/resolve/{id}")
def resolve_feedback(request: Request, id: int):
    redir = require_login(request)
    if redir: return redir
    try:
        conn = get_db()
        cur = dict_cursor(conn)
        cur.execute("UPDATE feedback SET status=%s WHERE id=%s", ("Resolved", id))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Resolve error: {e}")
    return RedirectResponse("/", status_code=303)

@app.post("/reopen/{id}")
def reopen_feedback(request: Request, id: int):
    redir = require_login(request)
    if redir: return redir
    try:
        conn = get_db()
        cur = dict_cursor(conn)
        cur.execute("UPDATE feedback SET status=%s WHERE id=%s", ("Open", id))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Reopen error: {e}")
    return RedirectResponse("/", status_code=303)

# ========================
# EDIT FEEDBACK
# ========================
@app.post("/edit/{id}")
def edit_feedback(request: Request, id: int, message: str = Form(...)):
    redir = require_login(request)
    if redir: return redir
    try:
        conn = get_db()
        cur = dict_cursor(conn)
        cur.execute("UPDATE feedback SET message=%s WHERE id=%s", (message, id))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Edit error: {e}")
    return RedirectResponse("/", status_code=303)

# ========================
# DELETE FEEDBACK
# ========================
@app.post("/delete/{id}")
def delete_feedback(request: Request, id: int):
    redir = require_login(request)
    if redir: return redir
    try:
        conn = get_db()
        cur = dict_cursor(conn)
        cur.execute("DELETE FROM feedback WHERE id=%s", (id,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Delete error: {e}")
    return RedirectResponse("/", status_code=303)

# ========================
# EXPORT CSV
# ========================
@app.get("/export")
def export_csv(request: Request):
    redir = require_login(request)
    if redir: return redir

    try:
        conn = get_db()
        cur = dict_cursor(conn)
        
        if is_admin(request):
            cur.execute(
                "SELECT id, person, phone, message, date, priority, status, submitted_by, followup_days FROM feedback ORDER BY id DESC"
            )
        else:
            cur.execute(
                "SELECT id, person, phone, message, date, priority, status, submitted_by, followup_days FROM feedback WHERE submitted_by=%s ORDER BY id DESC",
                (get_current_user(request),)
            )
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
    except Exception as e:
        logger.error(f"Export error: {e}")
        raise HTTPException(status_code=500, detail="Export failed")

# ========================
# SETTINGS PAGE (admin only)
# ========================
@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    redir = require_admin(request)
    if redir: return redir

    try:
        conn = get_db()
        cur = dict_cursor(conn)
        cur.execute("SELECT id, username, role, created_at FROM users ORDER BY id")
        users = cur.fetchall()
        cur.close()
        conn.close()

        followup_days = get_followup_days()
        saved  = request.query_params.get("saved", "")
        error  = request.query_params.get("error", "")

        return templates.TemplateResponse("settings.html", {
            "request":       request,
            "followup_days": followup_days,
            "users":         users,
            "saved":         saved,
            "error":         error,
            "current_user":  get_current_user(request),
            "current_role":  get_current_role(request),
        })
    except Exception as e:
        logger.error(f"Settings page error: {e}")
        raise HTTPException(status_code=500, detail="Settings error")

@app.post("/settings")
def save_settings(request: Request, followup_days: int = Form(...)):
    redir = require_admin(request)
    if redir: return redir
    followup_days = max(1, min(365, followup_days))
    set_setting("followup_days", str(followup_days))
    return RedirectResponse("/settings?saved=1", status_code=303)

# ========================
# USER MANAGEMENT (admin only)
# ========================
@app.post("/users/add")
def add_user(
    request:  Request,
    username: str = Form(...),
    password: str = Form(...),
    role:     str = Form("user")
):
    redir = require_admin(request)
    if redir: return redir

    username = username.strip().lower()
    if not username or not password:
        return RedirectResponse("/settings?error=Username+and+password+required.", status_code=303)
    if role not in ("admin", "user"):
        role = "user"

    try:
        conn = get_db()
        cur = dict_cursor(conn)
        cur.execute(
            "INSERT INTO users(username, password, role, created_at) VALUES(%s, %s, %s, CURRENT_DATE)",
            (username, hash_password(password), role)
        )
        conn.commit()
        cur.close()
        conn.close()
        return RedirectResponse("/settings?saved=1", status_code=303)
    except psycopg2.IntegrityError:
        return RedirectResponse("/settings?error=Username+already+exists.", status_code=303)
    except Exception as e:
        logger.error(f"Add user error: {e}")
        return RedirectResponse("/settings?error=Database+error.", status_code=303)

@app.post("/users/delete/{id}")
def delete_user(request: Request, id: int):
    redir = require_admin(request)
    if redir: return redir

    try:
        conn = get_db()
        cur = dict_cursor(conn)
        cur.execute("SELECT username FROM users WHERE id=%s", (id,))
        user = cur.fetchone()
        
        if user and user["username"] == get_current_user(request):
            cur.close()
            conn.close()
            return RedirectResponse("/settings?error=You+cannot+delete+your+own+account.", status_code=303)

        cur.execute("SELECT COUNT(*) FROM users WHERE role=%s", ("admin",))
        admins = cur.fetchone()[0]
        
        cur.execute("SELECT role FROM users WHERE id=%s", (id,))
        target = cur.fetchone()
        
        if target and target["role"] == "admin" and admins <= 1:
            cur.close()
            conn.close()
            return RedirectResponse("/settings?error=Cannot+delete+the+last+admin.", status_code=303)

        cur.execute("DELETE FROM users WHERE id=%s", (id,))
        conn.commit()
        cur.close()
        conn.close()
        return RedirectResponse("/settings?saved=1", status_code=303)
    except Exception as e:
        logger.error(f"Delete user error: {e}")
        return RedirectResponse("/settings?error=Database+error.", status_code=303)

@app.post("/users/change-password")
def change_password(
    request:      Request,
    old_password: str = Form(...),
    new_password: str = Form(...),
):
    redir = require_login(request)
    if redir: return redir

    username = get_current_user(request)
    try:
        conn = get_db()
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()

        if not user or not verify_password(old_password, user["password"]):
            cur.close()
            conn.close()
            return RedirectResponse("/settings?error=Current+password+is+wrong.", status_code=303)

        cur.execute("UPDATE users SET password=%s WHERE username=%s", (hash_password(new_password), username))
        conn.commit()
        cur.close()
        conn.close()
        return RedirectResponse("/settings?saved=1", status_code=303)
    except Exception as e:
        logger.error(f"Change password error: {e}")
        return RedirectResponse("/settings?error=Database+error.", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)