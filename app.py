from fastapi import FastAPI, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import sqlite3
import requests
from datetime import datetime
import shutil

app = FastAPI()

templates = Jinja2Templates(directory="templates")

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
# -----------------------------
# GREEN API CONFIG
# -----------------------------

GREEN_API_URL = "https://api.green-api.com/waInstance7107518478/sendMessage/e197605d1eb74b76b6d7e6ba8be8582e54f5a03501c94a7c8e"

# -----------------------------
# EMPLOYEE DIRECTORY
# -----------------------------

EMPLOYEES = {

"Shubhneet Khurana":"919855562123",
"Pramod":"919454181890",
"Preeti":"919569888004",
"Ratti":"919915977885",
"Kriti Mam":"919876566555",
"Lalhan Mishra HR":"919417713023",
"Monty":"919592761974",
"Raju Ji (Account)":"918728902080",
"Joshi":"918146474566",
"Sanjay Mishra":"919990848585",
"Hari Prakash":"919878051243",
"Deepak":"919914123301",
"Mishra Ji":"919417713023",
"Jyoti Mam":"919815466555",
"Pathania":"919915025517",
"Dheeraj":"919915025507",
"Raja Sir":"919815266555",
"Pooja":"918360559762",
"Anil Sharma":"919501501524",
"Om Dadhwal":"919878019868",
"Suraj":"916280404745",
"RAGHAV SIR":"919872166555",
"Manish":"917009412112",
"Jyoti Chahuan":"916284179660",
"Purnima":"917508109704",
"Dimpy Designer":"917508978054",
"Ashish Designer":"916307746071",
"Reetu Designer":"918427316217",
"Pinky":"919915777055",
"Sampita Designer":"917973324109",
"Junaid Designer":"917626964671",
"AJIT LALU":"919779246668",
"Garima Designer":"919988093070",
"Rajshree":"917903732955",
"Nishu":"918728098983",
"Vijay":"918677805147",
"Sonam":"919717411293",
"Juhi":"919779188296",
"Amisha":"919718286214",
"Chanda":"918360777824",
"Charanjit Sir Ac":"919463532277",
"Sheelu":"918699261388",
"Kamal":"917973870083",
"Archit":"919915184763",
"Anika":"919779304072",
"Ritika Verma":"917626864357",
"Jyoti Mittal":"917973611932",
"Taniya":"918146918930",
"Loveleen":"917340712478",
"Ruhi":"917087099991",
"Amrendra":"918677805147",
"Manisha":"918699540589",
"Nisha":"9176963661015",
"Sneha":"919814782494",
"Nisha Singh":"916284179661",
"Raju":"918728902080",
"Vishal Bhalla":"919915164202",
"Tanuja Designer":"918534051443",
"Sukhdev Master":"919465529426",
"Sarvesh Master":"919872573254",
"Kalpana Designer":"919560919628",
"Rashmi EA":"919915990000",
"Ram Niwas":"918591624713",
"Raj Designer":"916283080516",
"Palak":"917973373732",
"Muskan":"916283801612",
"Maluk Master":"918847041709",
"Madhukar":"916387073378",
"Kishan Master":"917986869939",
"Kanhaiya Boiler":"919888030893",
"Kailash":"919915025514",
"Khushi":"917986436698",
"Komal":"918968709850",
"Monish Designer":"918437599681",
"Deepak Sangini":"919914123301",
"Bittu Sir":"919878430000",
"Deepak CA":"919872588396",
"Anwar Master":"918360429569"

}
# -----------------------------
# DATABASE
# -----------------------------

conn = sqlite3.connect("database.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS feedback (
id INTEGER PRIMARY KEY AUTOINCREMENT,
person TEXT,
phone TEXT,
message TEXT,
voice TEXT,
date TEXT
)
""")

conn.commit()

# -----------------------------
# WHATSAPP FUNCTION
# -----------------------------

def send_whatsapp(phone, message):

    payload = {
        "chatId": f"{phone}@c.us",
        "message": message
    }

    response = requests.post(GREEN_API_URL, json=payload)

    print("WhatsApp response:", response.text)
def send_whatsapp_voice(phone, file_url):

    url = "https://api.green-api.com/waInstance7107518478/sendFileByUrl/e197605d1eb74b76b6d7e6ba8be8582e54f5a03501c94a7c8e"

    payload = {
        "chatId": f"{phone}@c.us",
        "urlFile": file_url,
        "fileName": "voice.mp3"
    }

    response = requests.post(url, json=payload)

    print("Voice response:", response.text)
# -----------------------------
# HOME PAGE
# -----------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):

    rows = cursor.execute("SELECT * FROM feedback").fetchall()

    feedback_list = []

    for r in rows:

        date_obj = datetime.strptime(r[5], "%Y-%m-%d")
        days = (datetime.now() - date_obj).days

        feedback_list.append({
            "id": r[0],
            "person": r[1],
            "message": r[3],
            "date": r[5],
            "days": days
        })

    total_feedback = len(rows)

    today_count = 0
    overdue = 0
    pending = 0

    for r in rows:

        date_obj = datetime.strptime(r[5], "%Y-%m-%d")
        days = (datetime.now() - date_obj).days

        if days > 15:
            overdue += 1
        else:
            pending += 1

        if r[5] == datetime.now().strftime("%Y-%m-%d"):
            today_count += 1

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "feedback": feedback_list,
            "employees": EMPLOYEES,
            "total": total_feedback,
            "pending": pending,
            "overdue": overdue,
            "today": today_count
        }
    )
# -----------------------------
# SUBMIT FEEDBACK
# -----------------------------

@app.post("/submit")
async def submit_feedback(
    person: str = Form(...),
    message: str = Form(...),
    voice: UploadFile = File(None)
):

    phone = EMPLOYEES[person]

    voice_path = ""

    if voice and voice.filename:

        voice_path = f"uploads/{voice.filename}"

        with open(voice_path, "wb") as buffer:
            shutil.copyfileobj(voice.file, buffer)

    today = datetime.now().strftime("%Y-%m-%d")

    cursor.execute(
        "INSERT INTO feedback(person,phone,message,voice,date) VALUES(?,?,?,?,?)",
        (person, phone, message, voice_path, today)
    )

    conn.commit()

    send_whatsapp(phone, f"Feedback:\n\n{message}")

    if voice_path:
        voice_url = f"http://127.0.0.1:8000/{voice_path}"
        send_whatsapp_voice(phone, voice_url)

    return RedirectResponse("/", status_code=303)
@app.get("/delete/{id}")
def delete_feedback(id: int):

    cursor.execute("DELETE FROM feedback WHERE id=?", (id,))
    conn.commit()

    return RedirectResponse("/", status_code=303)