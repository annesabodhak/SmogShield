# SmogShield: Urban Air Quality Assistant

Helps families manage winter smog with low-emission routines, cheap indoor air fixes and sustainable swaps, with estimated impact numbers. Built for Prompt Wars Challenge 6.

## Features
- **Login and dashboard.** Sign up with username, email and password, then land on "Hey <username>".
- **Hyper-local profile.** City or neighbourhood, household (kids, elderly, asthma, pregnancy), commute, cooking fuel, windows.
- **Live air quality.** US AQI, PM2.5 and PM10 from Open-Meteo (free, no key).
- **Low-emission routine planner.** Hour-by-hour PM2.5 forecast with the best windows for exercise, school run, ventilation and cooking.
- **Indoor air hacks.** Corsi-Rosenthal box, damp mopping, exhaust timing, sealing gaps, N95 tips, filtered by budget.
- **Sustainable swaps.** Metro, bus, e-rickshaw, carpool, walking, induction, no waste burning, each with PM2.5 (µg/m³) and CO₂ (kg/week) estimates plus a chart.
- **AI chat.** Gemini answers with your profile and live AQI as context. Without a key it switches to offline advice mode.
- **Responsive and accessible.** Works on phones, tablets and laptops with keyboard and screen-reader support.

## Tech stack
Flask, SQLite, requests, python-dotenv. Plain HTML, CSS and JavaScript on the frontend. Open-Meteo APIs for air data. Gemini API for chat.

## Setup
1. Install Python 3.10+.
2. Open a terminal in this folder and install packages:
   - Windows: `py -m pip install -r requirements.txt`
   - Mac/Linux: `python3 -m pip install -r requirements.txt`
3. Create a file named `.env` in the same folder:
   ```
   GEMINI_API_KEY=paste_your_key_here
   SECRET_KEY=any_long_random_text
   ```
   Get a free key at https://aistudio.google.com/apikey. Optional: `GEMINI_MODEL=gemini-2.5-flash`, `PORT=5000`.
4. Run: `python app.py` (Windows: `py app.py`).
5. Open http://localhost:5000. To use it on your phone, join the same Wi-Fi and open `http://<your-computer-IP>:5000`.

The app starts without a key and uses offline advice mode. The key stays on the server and never reaches the browser.

## Tests
`python app.py --test` checks AQI categories, impact maths, profile validation and the routine planner.

## Deploy (Render or Railway)
Push the folder to GitHub, create a Web Service, set the start command to `gunicorn app:app`, and add `GEMINI_API_KEY`, `SECRET_KEY` and `COOKIE_SECURE=1` as environment variables. SQLite data resets on free hosts that wipe disk, so use a persistent disk if you need accounts to last.

## API routes
| Route | Purpose |
|---|---|
| POST /api/signup, /api/login, /api/logout | Account session |
| GET /api/me | Current user and profile |
| POST /api/profile | Save profile |
| GET /api/air | Live AQI, advice and hourly routine |
| GET /api/swaps | Swap impact estimates |
| POST /api/chat | AI or offline answer |

## Impact assumptions (estimates)
- CO₂ per passenger-km: car 0.17, two-wheeler 0.04, bus 0.03, metro 0.02, e-rickshaw 0.015 kg.
- Commute exposure above ambient (µg/m³): car 15, two-wheeler 35, bus 25, metro 10, e-rickshaw 30. Exposure reduced = difference × commute hours ÷ 24, at 20 km/h.
- Cooking: indoor uplift while cooking 2 h/day: wood 120, LPG 25, induction 0 µg/m³. Daily CO₂: wood 2.0, LPG 1.5, induction 0.85 kg.
- Carpool and no-burning values are fixed neighbourhood-level guesses. All constants sit at the top of `app.py`.

## Security
Hashed passwords, parameterised SQL, HttpOnly session cookies, rate limits on login and chat, input validation, and text rendered with `textContent` to prevent XSS.

## Limitations
- AQI uses the US scale from model-based data, not a local monitor.
- Impact numbers are rough and not medical advice. See a doctor for breathing difficulty.
- Neighbourhood search works best with well-known place names.
