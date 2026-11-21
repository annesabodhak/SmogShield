"""SmogShield backend: auth, live AQI (Open-Meteo), routine planner,
swap impact estimates and an AI chat (Gemini) with an offline fallback.
Run: python app.py      Tests: python app.py --test"""
import json, os, re, secrets, sqlite3, sys, time
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()
BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "smogshield.db")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
TIMEOUT, CACHE_TTL = 8, 600

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE") == "1",
    MAX_CONTENT_LENGTH=32 * 1024)

# ---- Impact model constants (all values are rough ESTIMATES) -------------
EF_CO2 = {"car": 0.17, "two_wheeler": 0.04, "bus": 0.03, "metro": 0.02,
          "erickshaw": 0.015, "bicycle": 0.0, "walk": 0.0}    # kg CO2 / passenger-km
COMMUTE_UPLIFT = {"car": 15, "two_wheeler": 35, "bus": 25, "metro": 10,
                  "erickshaw": 30, "bicycle": 40, "walk": 30}  # ug/m3 above ambient while commuting
COMMUTE_SPEED_KMH = 20
COOK_HOURS = 2
COOK_PM_UPLIFT = {"wood": 120, "lpg": 25, "electric": 0}       # ug/m3 indoors while cooking
COOK_CO2_KG_DAY = {"wood": 2.0, "lpg": 1.5, "electric": 0.85}
CARPOOL_PM_PER_CAR_REMOVED = 0.8                               # ug/m3 daily average, neighbourhood
NO_BURN_PM, NO_BURN_CO2_WEEK = 4.0, 2.5
SENSITIVE = {"kids", "elderly", "asthma", "pregnant"}
HOUSEHOLD = SENSITIVE | {"none"}
AQI_BANDS = [(50, "Good", 1), (100, "Moderate", 2), (150, "Unhealthy for sensitive groups", 3),
             (200, "Unhealthy", 4), (300, "Very unhealthy", 5)]

# ---- Helpers ---------------------------------------------------------------
def q(sql, args=(), one=False, commit=False):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, args)
        if commit:
            con.commit()
        return cur.fetchone() if one else cur.fetchall()
    finally:
        con.close()

def init_db():
    q("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT NOT NULL,
         email TEXT UNIQUE NOT NULL, pw TEXT NOT NULL, profile TEXT DEFAULT '{}')""", commit=True)

HITS = {}
def rate_limit(name, limit, window=60):
    def deco(f):
        @wraps(f)
        def wrap(*a, **k):
            key, now = (name, request.remote_addr), time.time()
            hits = [t for t in HITS.get(key, []) if now - t < window]
            if len(hits) >= limit:
                return jsonify(error="Too many requests. Please wait a minute and try again."), 429
            HITS[key] = hits + [now]
            return f(*a, **k)
        return wrap
    return deco

def login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not session.get("uid"):
            return jsonify(error="Please log in."), 401
        return f(*a, **k)
    return wrap

def aqi_category(aqi):
    for top, label, level in AQI_BANDS:
        if aqi <= top:
            return label, level
    return "Hazardous", 6

def offline_advice(aqi, household):
    sens = bool(set(household or []) & SENSITIVE)
    if aqi <= 50:
        return "Air is good. Normal outdoor activity and open windows are fine."
    if aqi <= 100:
        return "Air is acceptable. " + ("Keep strenuous outdoor play light for sensitive family members."
                                        if sens else "Ventilate in the afternoon and enjoy outdoor time.")
    if aqi <= 150:
        return "Unhealthy for sensitive groups. " + (
            "Limit outdoor play and exercise for kids, elders and anyone with asthma; wear an N95 if you go out."
            if sens else "Shorten intense outdoor workouts and ventilate at the cleanest hour.")
    if aqi <= 200:
        return ("Unhealthy. Move exercise indoors, keep windows shut except at the cleanest hour, "
                "run a purifier or Corsi-Rosenthal box, and wear an N95 outside.")
    return ("Very unhealthy or hazardous. Stay indoors, seal door gaps, avoid cooking smoke and dry sweeping, "
            "wear an N95 for essential trips and keep asthma medication handy. Seek medical help for breathing trouble.")

def clean_profile(d):
    d = d or {}
    try:
        p = {
            "city": str(d.get("city", "")).strip()[:80],
            "household": [h for h in d.get("household", []) if h in HOUSEHOLD][:5],
            "commute": d.get("commute") if d.get("commute") in EF_CO2 else "car",
            "home": d.get("home") if d.get("home") in ("closed", "open") else "closed",
            "exhaust": bool(d.get("exhaust")),
            "distance_km": min(max(float(d.get("distance_km", 8)), 0.1), 100),
            "days": min(max(int(d.get("days", 5)), 1), 7),
            "people": min(max(int(d.get("people", 3)), 1), 6),
            "fuel": d.get("fuel") if d.get("fuel") in COOK_PM_UPLIFT else "lpg"}
    except (TypeError, ValueError):
        return None
    return p if p["city"] else None

def get_profile():
    row = q("SELECT profile FROM users WHERE id=?", (session["uid"],), one=True)
    return json.loads(row["profile"]) if row else {}

# ---- Live air data ---------------------------------------------------------
CACHE = {}
def get_air(city):
    key = city.lower()
    hit = CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    parts = [city] + [s.strip() for s in city.split(",") if s.strip()]
    place = None
    for name in dict.fromkeys(parts):
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": name, "count": 1}, timeout=TIMEOUT)
        r.raise_for_status()
        res = r.json().get("results")
        if res:
            place = res[0]
            break
    if not place:
        raise LookupError("We couldn't find that place. Try a nearby city name.")
    r = requests.get("https://air-quality-api.open-meteo.com/v1/air-quality", params={
        "latitude": place["latitude"], "longitude": place["longitude"],
        "current": "us_aqi,pm2_5,pm10", "hourly": "pm2_5",
        "forecast_days": 2, "timezone": "auto"}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    out = {"place": ", ".join(x for x in (place.get("name"), place.get("admin1"), place.get("country")) if x),
           "current": data["current"], "hourly": data["hourly"]}
    CACHE[key] = (time.time(), out)
    return out

def build_routine(times, pm, now_iso, household):
    today = now_iso[:10]
    day = [(int(t[11:13]), v) for t, v in zip(times, pm) if t[:10] == today and v is not None]
    day = [r for r in day if 5 <= r[0] <= 22]
    if not day:
        return None
    pmap = dict(day)
    lo, hi = min(pmap.values()), max(pmap.values())
    span = max(hi - lo, 1)

    def level(v):
        if v <= 25: return "good"
        if v >= 90: return "avoid"
        return "good" if v <= lo + span * .35 else "avoid" if v >= lo + span * .65 else "ok"

    def best(hours, width=1):
        c = [h for h in hours if all(h + i in pmap for i in range(width))]
        if not c: return None
        h = min(c, key=lambda x: sum(pmap[x + i] for i in range(width)))
        return {"start": h, "end": h + width, "pm25": round(sum(pmap[h + i] for i in range(width)) / width)}

    sens = bool(set(household or []) & SENSITIVE)
    return {
        "hours": [{"hour": h, "pm25": round(v), "level": level(v)} for h, v in day],
        "indoor_day": sens and lo > 55,
        "picks": {"exercise": best(range(6, 20), 2), "school": best(range(7, 10)),
                  "ventilate": best(range(10, 17)),
                  "cooking": best(list(range(7, 9)) + list(range(12, 14)) + list(range(18, 21)))}}

# ---- Swap impact -----------------------------------------------------------
def compute_swaps(p):
    mode, dist, days, ppl, fuel = p["commute"], p["distance_km"], p["days"], p["people"], p["fuel"]
    km_week, hrs = dist * 2 * days, dist * 2 / COMMUTE_SPEED_KMH
    out = []

    def add(short, name, desc, pm, co2):
        out.append({"short": short, "name": name, "description": desc,
                    "pm25_ugm3": round(max(pm, 0), 1), "co2_kg_week": round(max(co2, 0), 1)})

    for new, short, name in (("metro", "Metro", "Take the metro"), ("bus", "Bus", "Take the bus"),
                             ("erickshaw", "E-rickshaw", "Use an e-rickshaw")):
        if new != mode:
            pm = (COMMUTE_UPLIFT[mode] - COMMUTE_UPLIFT[new]) * hrs / 24
            co2 = km_week * (EF_CO2[mode] - EF_CO2[new])
            if co2 > 0 or pm > 0:
                add(short, name, "Lower tailpipe emissions per passenger and less time in dense traffic.", pm, co2)
    if dist <= 3 and mode in ("car", "two_wheeler"):
        add("Walk", "Walk short trips", "Zero emissions. Choose the cleaner hours in your routine plan.",
            0, km_week * EF_CO2[mode])
    if mode == "car" and ppl >= 2:
        add("Carpool", f"Carpool with {ppl - 1} other{'s' if ppl > 2 else ''}",
            "Fewer cars on your route for the same trip.",
            CARPOOL_PM_PER_CAR_REMOVED * (1 - 1 / ppl), km_week * EF_CO2["car"] * (1 - 1 / ppl))
    if fuel != "electric":
        add("Induction", "Cook on induction",
            "Removes combustion smoke from the kitchen, the biggest indoor source in many homes.",
            COOK_PM_UPLIFT[fuel] * COOK_HOURS / 24, (COOK_CO2_KG_DAY[fuel] - COOK_CO2_KG_DAY["electric"]) * 7)
    add("No burning", "Don't burn leaves or waste",
        "Open burning spikes neighbourhood PM2.5 on winter evenings.", NO_BURN_PM, NO_BURN_CO2_WEEK)
    out.sort(key=lambda s: -(s["pm25_ugm3"] + s["co2_kg_week"]))
    return {"swaps": out,
            "total_pm25": round(sum(s["pm25_ugm3"] for s in out), 1),
            "total_co2": round(sum(s["co2_kg_week"] for s in out), 1)}

# ---- AI chat ---------------------------------------------------------------
SYSTEM = ("You are SmogShield, a practical air-quality assistant for Indian families in winter smog. "
          "Give short, specific, safe advice (max 120 words) using the live AQI and household details. "
          "For kids, elders, pregnancy or asthma, be cautious and mention seeing a doctor for breathing "
          "difficulty. Prefer low-cost actions. Do not claim certainty about health outcomes.")

def ask_gemini(message, context):
    if not GEMINI_KEY:
        return None
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            headers={"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"},
            json={"system_instruction": {"parts": [{"text": SYSTEM}]},
                  "contents": [{"role": "user", "parts": [{"text": f"Context: {context}\n\nQuestion: {message}"}]}],
                  "generationConfig": {"maxOutputTokens": 500, "temperature": 0.4}}, timeout=15)
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(x.get("text", "") for x in parts).strip() or None
    except Exception as exc:  # network, quota, bad key, safety block -> offline fallback
        app.logger.warning("Gemini call failed: %s", exc)
        return None

# ---- Routes ----------------------------------------------------------------
@app.after_request
def secure(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp

@app.get("/")
def index(): return send_from_directory(BASE, "index.html")
@app.get("/style.css")
def css(): return send_from_directory(BASE, "style.css")
@app.get("/script.js")
def js(): return send_from_directory(BASE, "script.js")

@app.post("/api/signup")
@rate_limit("auth", 10)
def signup():
    d = request.get_json(silent=True) or {}
    name, email, pw = str(d.get("username", "")).strip(), str(d.get("email", "")).strip().lower(), str(d.get("password", ""))
    if not 2 <= len(name) <= 30 or not re.fullmatch(r"[\w .\-]+", name):
        return jsonify(error="Username must be 2-30 letters, numbers or spaces."), 400
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 120:
        return jsonify(error="Enter a valid email address."), 400
    if len(pw) < 8:
        return jsonify(error="Password must be at least 8 characters."), 400
    if q("SELECT 1 FROM users WHERE email=?", (email,), one=True):
        return jsonify(error="That email is already registered. Log in instead."), 409
    q("INSERT INTO users(username,email,pw) VALUES(?,?,?)", (name, email, generate_password_hash(pw)), commit=True)
    row = q("SELECT id FROM users WHERE email=?", (email,), one=True)
    session.clear(); session["uid"] = row["id"]
    return jsonify(user={"username": name}, profile={})

@app.post("/api/login")
@rate_limit("auth", 10)
def login():
    d = request.get_json(silent=True) or {}
    row = q("SELECT * FROM users WHERE email=?", (str(d.get("email", "")).strip().lower(),), one=True)
    if not row or not check_password_hash(row["pw"], str(d.get("password", ""))):
        return jsonify(error="Email or password is incorrect."), 401
    session.clear(); session["uid"] = row["id"]
    return jsonify(user={"username": row["username"]}, profile=json.loads(row["profile"]))

@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)

@app.get("/api/me")
def me():
    if not session.get("uid"):
        return jsonify(user=None)
    row = q("SELECT username, profile FROM users WHERE id=?", (session["uid"],), one=True)
    if not row:
        session.clear()
        return jsonify(user=None)
    return jsonify(user={"username": row["username"]}, profile=json.loads(row["profile"]))

@app.post("/api/profile")
@login_required
def save_profile():
    p = clean_profile(request.get_json(silent=True))
    if not p:
        return jsonify(error="Enter your city and check the numbers in the form."), 400
    q("UPDATE users SET profile=? WHERE id=?", (json.dumps(p), session["uid"]), commit=True)
    return jsonify(profile=p)

@app.get("/api/air")
@login_required
def air():
    p = get_profile()
    if not p.get("city"):
        return jsonify(error="Save your profile with a city first."), 400
    try:
        a = get_air(p["city"])
    except LookupError as e:
        return jsonify(error=str(e)), 404
    except (requests.RequestException, KeyError, ValueError):
        return jsonify(error="Live air data is unavailable right now. Please retry in a moment."), 502
    cur = a["current"]
    aqi = round(cur.get("us_aqi") or 0)
    label, level = aqi_category(aqi)
    return jsonify(place=a["place"], aqi=aqi, category=label, level=level,
                   pm25=cur.get("pm2_5"), pm10=cur.get("pm10"),
                   advice=offline_advice(aqi, p.get("household")),
                   routine=build_routine(a["hourly"]["time"], a["hourly"]["pm2_5"], cur["time"], p.get("household")))

@app.get("/api/swaps")
@login_required
def swaps():
    p = get_profile()
    if not p.get("city"):
        return jsonify(error="Save your profile first."), 400
    return jsonify(compute_swaps(p))

@app.post("/api/chat")
@login_required
@rate_limit("chat", 15)
def chat():
    msg = str((request.get_json(silent=True) or {}).get("message", "")).strip()[:500]
    if not msg:
        return jsonify(error="Type a question first."), 400
    p, aqi, ctx = get_profile(), None, "No live data yet."
    if p.get("city"):
        try:
            a = get_air(p["city"])
            aqi = round(a["current"].get("us_aqi") or 0)
            ctx = (f"City {a['place']}; US AQI {aqi} ({aqi_category(aqi)[0]}); PM2.5 {a['current'].get('pm2_5')} ug/m3; "
                   f"household {', '.join(p.get('household') or ['none'])}; commute {p['commute']}; "
                   f"windows {p['home']}; kitchen exhaust {'yes' if p['exhaust'] else 'no'}.")
        except Exception:
            pass
    reply = ask_gemini(msg, ctx)
    if reply:
        return jsonify(reply=reply, mode="ai")
    base = offline_advice(aqi, p.get("household")) if aqi is not None else \
        "Save your city in Overview so I can use live air data. Until then: keep windows shut at smog peaks, wear an N95 outside and avoid dry sweeping."
    return jsonify(reply=base, mode="offline")

# ---- Tests -----------------------------------------------------------------
def run_tests():
    assert aqi_category(30) == ("Good", 1) and aqi_category(160)[0] == "Unhealthy" and aqi_category(400)[0] == "Hazardous"
    p = clean_profile({"city": "Delhi", "commute": "car", "distance_km": 10, "days": 5, "people": 2, "fuel": "wood"})
    r = compute_swaps(p)
    carpool = next(s for s in r["swaps"] if s["short"] == "Carpool")
    assert abs(carpool["co2_kg_week"] - 100 * 0.17 * 0.5) < 0.1
    induction = next(s for s in r["swaps"] if s["short"] == "Induction")
    assert abs(induction["pm25_ugm3"] - 10.0) < 0.1
    assert clean_profile({"city": ""}) is None and clean_profile({"city": "X", "distance_km": "abc"}) is None
    rt = build_routine([f"2026-11-21T{h:02d}:00" for h in range(24)], [100 - abs(h - 13) * 4 for h in range(24)],
                       "2026-11-21T10:00", [])
    assert rt["picks"]["exercise"] is not None
    print("All tests passed.")

init_db()
if __name__ == "__main__":
    if "--test" in sys.argv:
        run_tests()
    else:
        print("AI key loaded" if GEMINI_KEY else "No GEMINI_API_KEY found: using offline advice mode")
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
