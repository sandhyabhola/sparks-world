import os, re, json, time, secrets, hashlib, random
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

load_dotenv()
GROQ, GEMINI = os.getenv("GROQ_API_KEY"), os.getenv("GEMINI_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip().strip("\"'").replace("models/", "")
TIME_LIMIT, GRACE, KEEP_DAYS = 240, 15, 30
TOPICS = ["K-pop", "Dramas & movies", "Social life", "Content creation", "Environment", "Leadership", "Gaming", "Music"]

app = FastAPI(title="Sparks")
from db_compat import db, IntegrityError, OperationalError   # Neon PostgreSQL (see db_compat.py)
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, salt TEXT, pw TEXT, lang TEXT, xp INTEGER DEFAULT 0, gsub TEXT, consent_ver INTEGER DEFAULT 0, spent INTEGER DEFAULT 0, credits INTEGER DEFAULT 0, icon TEXT DEFAULT '🚀');
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, created REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY, user_id INTEGER, topic TEXT, level INTEGER, scenario TEXT, created REAL, answered INTEGER DEFAULT 0, part INTEGER DEFAULT 1, bank_id INTEGER);
CREATE TABLE IF NOT EXISTS answers(id INTEGER PRIMARY KEY, qid INTEGER, user_id INTEGER, topic TEXT, original TEXT, english TEXT, score REAL, xp INTEGER, feedback TEXT, created REAL, shared INTEGER DEFAULT 0, bank_id INTEGER);
""")

TERMS_VER = 1
FAILS = {}
def sh(t): return hashlib.sha256(t.encode()).hexdigest()

def new_session(uid):
    t = secrets.token_hex(24)
    db.execute("INSERT INTO sessions(token,user_id,created) VALUES(?,?,?)", (sh(t), uid, time.time())); db.commit()
    return t

def hp(pw, salt): return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()

def llm(system, user):
    """Try Groq, then Gemini. Returns parsed JSON dict or None."""
    try:
        if GROQ:
            r = httpx.post("https://api.groq.com/openai/v1/chat/completions", timeout=40,
                headers={"Authorization": f"Bearer {GROQ}"},
                json={"model": GROQ_MODEL, "temperature": 0.3, "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            r.raise_for_status()
            return json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception as e:
        print("groq failed:", e)
    try:
        if GEMINI:
            r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI}",
                timeout=40, json={"contents": [{"parts": [{"text": system + "\n\n" + user}]}],
                                  "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3}})
            r.raise_for_status()
            return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
    except Exception as e:
        print("gemini failed:", e)
    return None

def me(token):
    row = db.execute("SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.created>?", (sh(token or ""), time.time() - 7 * 86400)).fetchone()
    if not row: raise HTTPException(401, "Please log in")
    return row

class Auth(BaseModel):
    username: str
    password: str
    lang: str = "English"
    adult: bool = False
    terms: bool = False
    summary_ok: bool = False

@app.post("/api/signup")
def signup(a: Auth):
    raise HTTPException(410, "Please sign up with email verification")
    if not a.adult: raise HTTPException(400, "You must confirm you are 18 or older")
    if not (a.terms and a.summary_ok): raise HTTPException(400, "Please accept the terms and the anonymous-summary notice")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,15}", a.username) or a.username.isdigit():
        raise HTTPException(400, "Username: 4-16 characters, start with a letter, only letters, numbers, underscore. Example: NovaMind_07")
    p = a.password
    if not (len(p) >= 8 and re.search("[a-z]", p) and re.search("[A-Z]", p) and re.search(r"\d", p) and re.search(r"[^A-Za-z0-9]", p)):
        raise HTTPException(400, "Password: 8+ characters with an uppercase letter, a lowercase letter, a number and a symbol")
    check_text(a.username, 0, False)
    salt = secrets.token_hex(8)
    try:
        db.execute("INSERT INTO users(username,salt,pw,lang,consent_ver) VALUES(?,?,?,?,?)", (a.username, salt, hp(a.password, salt), a.lang, TERMS_VER))
        db.commit()
    except IntegrityError:
        raise HTTPException(400, "Username already occupied. Please choose another one.")
    return login(a)

@app.post("/api/login")
def login(a: Auth, request: Request):
    ip = request.client.host if request.client else "?"
    f = FAILS.setdefault(a.username.lower(), []); fi = FAILS.setdefault("ip:" + ip, [])
    f[:] = [x for x in f if x > time.time() - 600]; fi[:] = [x for x in fi if x > time.time() - 600]
    if len(f) >= 5 or len(fi) >= 20: raise HTTPException(429, "Too many tries. Wait 10 minutes.")
    u = db.execute("SELECT * FROM users WHERE username=?", (a.username,)).fetchone()
    ok = hp(a.password, u["salt"] if u else "0" * 16) == (u["pw"] if u else None)
    if not (u and ok):
        f.append(time.time()); fi.append(time.time()); raise HTTPException(401, "Wrong username or password")
    db.execute("DELETE FROM sessions WHERE created<?", (time.time() - 7 * 86400,))
    return {"token": new_session(u["id"]), "lang": u["lang"], "xp": u["xp"]}

@app.get("/api/topics")
def topics(): return TOPICS

@app.get("/api/me")
def profile(authorization: str = Header(None)):
    u = me(authorization)
    return {"username": u["username"], "xp": u["xp"], "level": min(5, 1 + u["xp"] // 150), "lang": u["lang"], "consent": u["consent_ver"] == TERMS_VER, "available": u["xp"] - u["spent"], "icon": u["icon"], "credits": u["credits"]}

@app.get("/api/scenario")
def scenario(topic: str, authorization: str = Header(None)):
    u = me(authorization)
    db.execute("DELETE FROM answers WHERE created<?", (time.time() - KEEP_DAYS * 86400,))
    b = db.execute("SELECT * FROM qbank WHERE topic=? AND lang=? AND id NOT IN (SELECT bank_id FROM questions WHERE user_id=? AND bank_id IS NOT NULL) ORDER BY RANDOM() LIMIT 1", (topic, u["lang"], u["id"])).fetchone()
    if b: bid, text = b["id"], b["text"]
    else:
        out = llm('You write short everyday-life story situations that make a teen think. Reply ONLY JSON: {"scenario": "...", "real_question": "..."}. '
                  "Write in the requested language, 50-80 words, simple words, fun, fully fictional, no real people or brands, no violence, NO math or physics, no homework feel. "
                  "Examples: a K-pop idol launching a song while unknown; a drama hero asking what love means; a captain's hard choice; a creator choosing a niche; housemates who won't keep the home clean; a polluted river. "
                  "Invent a NEW one for the topic. End with ONE opinion question answerable in 1-3 sentences. Also give real_question: the real-world question behind the story in one plain sentence, like How can housemates agree to keep a shared home clean? Use an empty string if the story has no real-world link.", f"Topic: {topic}\nLanguage: {u['lang']}")
        text = (out or {}).get("scenario") or "A young singer has a duet tonight, but her partner just lost his voice. The show starts in one hour. What is the first thing she should do?"
        rq_new = str((out or {}).get("real_question") or "")[:200]
        bid = db.execute("INSERT INTO qbank(topic,lang,text,real_q) VALUES(?,?,?,?)", (topic, u["lang"], text, rq_new)).lastrowid
    cur = db.execute("INSERT INTO questions(user_id,topic,level,scenario,created,part,bank_id) VALUES(?,?,?,?,?,1,?)", (u["id"], topic, 1, text, time.time(), bid))
    db.commit()
    return {"qid": cur.lastrowid, "scenario": text, "topic": topic, "seconds": TIME_LIMIT}

class Ans(BaseModel):
    qid: int
    text: str
    keystrokes: int = 0
    voice: bool = False
    timeout: bool = False

RUBRIC = ('Judge a teen\'s answer to a story question. Give ONE whole-number score from 0 to 10, strictly: 0 if the answer is unrelated to the question, empty of meaning or random; '
          '1-3 weak; 4-6 okay; 7-8 good and thoughtful; 9-10 excellent and original. Judge thinking, not grammar. The answer may be in any language. '
          'Reply ONLY JSON: {"cleaned": "the answer in its own language with only spelling and grammar slips fixed, same voice and slang, names of people replaced by [name], places and schools by [place]", '
          '"english": "clear English version of the cleaned text", "score": n, "relevance": n from 0 to 100 for how much the answer addresses the real-world question, "real_answer": "if relevance is 40 or more: one or two plain sentences answering the real-world question from their own viewpoint, using only their own ideas; otherwise an empty string", "feedback": "2 kind sentences with one tip", "title": "2-word thinker title"}')

@app.post("/api/answer")
def answer(a: Ans, authorization: str = Header(None)):
    u = me(authorization)
    q = db.execute("SELECT * FROM questions WHERE id=? AND user_id=?", (a.qid, u["id"])).fetchone()
    if not q or q["answered"]: raise HTTPException(400, "Question not available")
    text = a.text.strip()
    if len(text) < 15:
        if not a.timeout: raise HTTPException(400, "Write at least one full sentence in your own words")
        db.execute("UPDATE questions SET answered=1 WHERE id=?", (a.qid,)); db.commit()
        return {"score": 0, "xp": 0, "total_xp": u["xp"], "feedback": "Time ran out. Start a fresh question whenever you like.", "title": ""}
    check_text(text, u["id"]); text = redact(text)
    rqr = db.execute("SELECT real_q FROM qbank WHERE id=?", (q["bank_id"],)).fetchone()
    rq = (rqr["real_q"] if rqr else "") or ""
    late = time.time() - q["created"] > TIME_LIMIT + GRACE
    pasted = not a.voice and len(text) > 120 and a.keystrokes < len(text) / 3
    res = None
    if not (late or pasted):
        res = llm(RUBRIC, f"Story question: {q['scenario']}\nReal-world question: {rq or 'none'}\n\nAnswer: {text}")
        if not res: raise HTTPException(503, "The judge is busy. Please try again in a minute.")
    db.execute("UPDATE questions SET answered=1 WHERE id=?", (a.qid,))
    score, store, eng, title = 0, text, text, ""
    real_a = ""
    fb = "Time ran out, so no points this time." if late else "Please type your own thoughts in your own words."
    if res:
        try:
            score = max(0, min(10, round(float(res["score"]))))
            fb, eng, title, store = res.get("feedback", ""), res.get("english", text), res.get("title", ""), str(res.get("cleaned") or text)[:600]
            if rq and float(res.get("relevance") or 0) >= 40: real_a = str(res.get("real_answer") or "")[:400]
        except Exception: score, fb = 0, "The judge could not read that one. Try again."
    shared = 1 if (score >= 1 and real_a) else 0
    db.execute("INSERT INTO answers(qid,user_id,topic,original,english,score,xp,feedback,created,shared,bank_id,real_a) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
               (a.qid, u["id"], q["topic"], store, eng, score, score, fb, time.time(), shared, q["bank_id"], real_a))
    db.execute("UPDATE users SET xp=xp+? WHERE id=?", (score, u["id"])); db.commit()
    return {"score": score, "xp": score, "total_xp": u["xp"] + score, "feedback": fb, "title": title}

@app.get("/api/leaderboard")
def board(topic: str):
    rows = db.execute("SELECT u.username, SUM(a.xp) xp FROM answers a JOIN users u ON u.id=a.user_id WHERE a.topic=? AND a.created>? GROUP BY u.id ORDER BY xp DESC LIMIT 10", (topic, time.time() - 86400)).fetchall()
    return [dict(r) for r in rows]

@app.get("/")
def p_login(): return FileResponse("static/login.html")

@app.get("/favicon.ico", include_in_schema=False)
def favicon(): return FileResponse("static/favicon.ico")

# ---------- security headers ----------
@app.middleware("http")
async def sec(request, call_next):
    r = await call_next(request)
    r.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Permissions-Policy": "camera=(), geolocation=(), microphone=(self)",
        "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; script-src 'self' 'unsafe-inline' https://accounts.google.com https://cdn.jsdelivr.net https://challenges.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://accounts.google.com https://cdnjs.cloudflare.com https://fonts.googleapis.com; font-src https://cdnjs.cloudflare.com https://fonts.gstatic.com; frame-src https://accounts.google.com https://challenges.cloudflare.com; connect-src 'self'; base-uri 'none'; object-src 'none'; form-action 'self'"})
    return r

@app.post("/api/logout")
def logout(authorization: str = Header(None)):
    db.execute("DELETE FROM sessions WHERE token=?", (sh(authorization or ""),)); db.commit(); return {"ok": True}

# ---------- Google sign-in (no email stored, only a hash of Google's user id) ----------
GCID = os.getenv("GOOGLE_CLIENT_ID")
@app.get("/api/config")
def config(): return {"google": GCID or "", "turnstile": os.getenv("TURNSTILE_SITE_KEY") or ""}

class G(BaseModel):
    credential: str
    lang: str = "English"
    adult: bool = False
    terms: bool = False
    summary_ok: bool = False

@app.post("/api/google")
def google(g: G):
    if not GCID: raise HTTPException(503, "Google sign-in not set up")
    r = httpx.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": g.credential}, timeout=15)
    d = r.json() if r.status_code == 200 else {}
    if d.get("aud") != GCID or str(d.get("email_verified")).lower() != "true": raise HTTPException(401, "Google sign-in failed")
    sub = hashlib.sha256(d["sub"].encode()).hexdigest()
    u = db.execute("SELECT * FROM users WHERE gsub=?", (sub,)).fetchone()
    if not u:
        if not (g.adult and g.terms and g.summary_ok): raise HTTPException(400, "Please confirm you are 18+ and accept the terms")
        cur = db.execute("INSERT INTO users(username,salt,pw,lang,gsub,consent_ver) VALUES(?,?,?,?,?,?)", ("Player_" + secrets.token_hex(3), "", "!", g.lang, sub, TERMS_VER)); db.commit()
        u = db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return {"token": new_session(u["id"]), "lang": u["lang"], "xp": u["xp"]}

# ---------- creations ----------
import datetime
db.execute("CREATE TABLE IF NOT EXISTS images(token TEXT PRIMARY KEY, data BYTEA NOT NULL)")  # generated pictures are stored in the database, so they survive restarts
CF_ID, CF_TOKEN, DAILY_ART = os.getenv("CF_ACCOUNT_ID"), os.getenv("CF_API_TOKEN"), 3
BAD = re.compile(r"(fuck|shit|bitch|asshole|bastard|slut|dick|porn|nude|naked|sex|rape|nazi|racist|idiot|stupid)", re.I)
db.executescript("""
CREATE TABLE IF NOT EXISTS art(id INTEGER PRIMARY KEY, user_id INTEGER, token TEXT, prompt TEXT, challenge TEXT DEFAULT '', posted INTEGER DEFAULT 0, created REAL, level INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS likes(art_id INTEGER, user_id INTEGER, UNIQUE(art_id,user_id));
CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY, art_id INTEGER, user_id INTEGER, text TEXT, created REAL);
""")
DAILY = ["Nature design: a calming nature wallpaper for your phone with misty mountains at sunrise", "Wallpaper design: a minimal aesthetic phone wallpaper with soft pastel mountains and a gradient sky", "Seasons design: a cozy autumn forest scene with warm light and no people", "Book cover design: romantic cover art using only flowers, light and shapes, no people, no letters", "Fantasy design: a floating island garden with a lantern-lit palace, no people"]
WEEKLY = ["Fantasy design: a fantasy world with nature scenery and floating mansions, no people", "Seasons design: a spring blossom scene by a river at golden hour", "Nature design: a quiet waterfall hidden in a green forest with floating fireflies", "Book cover design: mystery cover art with keys, moths and moonlight, no people, no letters", "Wallpaper design: a dark neon galaxy wallpaper with swirling purple and cyan light"]
MONTHLY = ["Fantasy design: a full fantasy realm with forests, waterfalls and floating mansions in the sky", "Seasons design: all four seasons in one picture, with no people", "Nature design: a calm lake under a huge moon with lotus flowers, no people"]

def _challenges(uid):
    d = datetime.date.today(); iso = d.isocalendar()
    want = (("daily", d.isoformat()), ("weekly", f"{iso[0]}-W{iso[1]}"), ("monthly", f"{d.year}-{d.month}"))
    have = {}
    for r in db.execute("SELECT kind, task FROM task_log WHERE user_id=? AND ((kind='daily' AND ref=?) OR (kind='weekly' AND ref=?) OR (kind='monthly' AND ref=?)) ORDER BY id",
                        (uid, want[0][1], want[1][1], want[2][1])).fetchall():
        have[r["kind"]] = r["task"]  # newest row wins
    out = {}
    for kind, ref in want:
        t = have.get(kind)
        if not t: t = pick_task(uid); log_task(uid, kind, ref, t)
        out[kind] = t
    return out

@app.get("/api/challenges")
def challenges(authorization: str = Header(None)):
    return _challenges(me(authorization)["id"])

def rows(sql, args=()): return [dict(r) for r in db.execute(sql, args).fetchall()]
ART = "SELECT a.id,a.token,a.prompt,a.challenge,a.level,(SELECT COUNT(*) FROM likes l WHERE l.art_id=a.id) likes"

def prompt_ok(p):
    if bad_words(p): return False
    r = llm('Reply ONLY JSON {"allowed": true or false}. Allowed only if the image idea is a fictional object, place, pattern, creature or scene. '
            "Not allowed: any person, face, human, anime or cartoon character, real people, brands, copyrighted characters, weapons, "
            "religious or cultural symbols or figures, politics, or sexual, violent or hateful content.", p)
    return bool(r and r.get("allowed") is True)

def image_ok(b64):
    """True = safe, False = unsafe, None = could not check."""
    if not GEMINI: return True
    body = {"contents": [{"parts": [
        {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
        {"text": 'Reply ONLY JSON {"unsafe": true or false, "reason": "a few words"}. unsafe=true only if the image clearly shows a person, face, body part, humanoid figure, readable offensive words, religious symbol, weapon or sexual content. Decorative marks or unreadable scribbles on objects are fine.'}]}],
        "generationConfig": {"responseMimeType": "application/json"}}
    for model in dict.fromkeys([GEMINI_MODEL, "gemini-flash-lite-latest"]):
        for attempt in range(3):
            try:
                r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI}", timeout=40, json=body)
                print("gemini check status:", r.status_code, "model:", repr(model))
                if r.status_code == 200:
                    out = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                    print("image check:", out)
                    return out.get("unsafe") is False
                if r.status_code not in (429, 500, 503):
                    print(r.text[:200]); break
            except Exception as e:
                print("image check failed:", e)
            time.sleep(2 * (attempt + 1))
    return None

class Gen(BaseModel):
    prompt: str
    theme: str = ""

def squeeze(raw):
    """Shrink a generated picture (about 650 KB) to about 110 KB before saving it in the database."""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB"); im.thumbnail((1024, 1024))
        b = io.BytesIO(); im.save(b, "JPEG", quality=82, optimize=True, progressive=True)
        return b.getvalue()
    except Exception as e:
        print("image shrink skipped:", e); return raw

@app.post("/api/art/generate")
def generate(b: Gen, authorization: str = Header(None)):
    u = me(authorization)
    if not CF_ID or not CF_TOKEN: raise HTTPException(503, "Image service is not set up yet")
    p = b.prompt.strip()[:300]
    if len(p) < 5: raise HTTPException(400, "Describe your idea in a few words")
    if db.execute("SELECT COUNT(*) c FROM art WHERE user_id=? AND created>?", (u["id"], time.time() - 86400)).fetchone()["c"] >= 5:
        if u["credits"] <= 0: raise HTTPException(429, "QUOTA")
        db.execute("UPDATE users SET credits=credits-1 WHERE id=?", (u["id"],)); db.commit()
    check_text(p, u["id"])
    if not prompt_ok(p): raise HTTPException(400, "That idea is not allowed. Use fictional objects, places, patterns or creatures, with no people, brands or sensitive symbols.")
    try:
        r = httpx.post(f"https://api.cloudflare.com/client/v4/accounts/{CF_ID}/ai/run/@cf/black-forest-labs/flux-1-schnell", timeout=90,
                       headers={"Authorization": f"Bearer {CF_TOKEN}"}, json={"prompt": p + ", digital art, no people, no text", "steps": 4})
        img = r.json()["result"]["image"]
    except Exception as e:
        print("image gen failed:", e); raise HTTPException(503, "Image service is busy. Try again soon.")
    ok = image_ok(img)
    if ok is None: raise HTTPException(503, "The safety checker is busy right now. Nothing was saved. Please try again in a minute.")
    if not ok: raise HTTPException(400, "That picture did not pass our safety check. Try a different idea.")
    import base64
    tok = secrets.token_urlsafe(12)
    db.execute("INSERT INTO images(token,data) VALUES(?,?)", (tok, squeeze(base64.b64decode(img))))
    cur = db.execute("INSERT INTO art(user_id,token,prompt,created) VALUES(?,?,?,?)", (u["id"], tok, p, time.time())); db.commit()
    return {"id": cur.lastrowid, "token": tok}

@app.get("/img/{token}")
def img(token: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", token): raise HTTPException(404)
    r = db.execute("SELECT data FROM images WHERE token=?", (token,)).fetchone()
    if not r: raise HTTPException(404)
    return Response(content=bytes(r["data"]), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

# ---------- levels, quests, badges, account ----------
db.execute("CREATE TABLE IF NOT EXISTS qbank(id INTEGER PRIMARY KEY, topic TEXT, lang TEXT, text TEXT, real_q TEXT DEFAULT '')"); db.commit()
for _s in ("ALTER TABLE qbank ADD COLUMN real_q TEXT DEFAULT ''", "ALTER TABLE answers ADD COLUMN real_a TEXT DEFAULT ''"):
    try: db.execute(_s); db.commit()
    except OperationalError: pass  # column already exists
LEVELS = [(1, "Neon Lantern", "💡", "Nature design: a calming misty mountain valley at sunrise with a winding river, lit by glowing lanterns. No people.", 25, "Lantern Lighter"),
          (2, "Crystal Labyrinth", "💎", "Wallpaper design: a phone wallpaper of a living crystal maze, with soft glowing shapes and a gradient sky. No people.", 50, "Maze Maker"),
          (3, "Floating Citadel", "🏙️", "Fantasy design: a fantasy world of lush nature with grand mansions floating in the clouds, joined by vine bridges. No people.", 75, "Citadel Architect"),
          (4, "The Cyber-Grove", "🌿", "Seasons design: one glowing forest showing spring blossoms, summer green, autumn leaves and winter snow together. No people.", 100, "Grove Keeper"),
          (5, "Bio-Mech Abyss", "🧬", "Book cover design: mysterious cover art of a deep-sea world with glowing machines and moonlit fog. No people, no letters.", 150, "Abyss Explorer")]
ADJ = ["Glimmering","Whispering","Frozen","Golden","Hidden","Electric","Drifting","Ancient","Crimson","Silent","Radiant","Wandering"]
NOUN = ["Observatory","Harbor","Garden","Bridge","Workshop","Market","Lighthouse","Library","Canyon","Greenhouse","Carousel","Archive"]
LEVELS = [(1, "Neon Lantern", "💡", "Design a glowing lantern that lights a dark path.", 25, "Lantern Lighter"),
          (2, "Crystal Labyrinth", "💎", "Imagine a maze built from living crystal.", 50, "Maze Maker"),
          (3, "Floating Citadel", "🏙️", "Engineer a thriving metropolis that floats among the clouds.", 75, "Citadel Architect"),
          (4, "The Cyber-Grove", "🌿", "Design a forest where nature and technology grow together.", 100, "Grove Keeper"),
          (5, "Bio-Mech Abyss", "🧬", "Design a deep-sea world of living machines, with no creatures that have faces.", 150, "Abyss Explorer")]
ADJ = ["Glimmering","Whispering","Frozen","Golden","Hidden","Electric","Drifting","Ancient","Crimson","Silent","Radiant","Wandering"]
NOUN = ["Observatory","Harbor","Garden","Bridge","Workshop","Market","Lighthouse","Library","Canyon","Greenhouse","Carousel","Archive"]
def level_info(n):
    if n <= len(LEVELS): return LEVELS[n - 1]
    i = n - len(LEVELS) - 1; a, b = ADJ[i % 12], NOUN[(i // 12 + i) % 12]
    return (n, f"{a} {b}", "✨", f"Design a {a.lower()} {b.lower()} that feels completely new. Fictional only, no people.", 150 + 25 * (n - 5), f"{b} Keeper")
ICONS = ["🚀", "👾", "🪐", "🦊", "🐉", "🤖", "🌌", "⚡"]
def done_levels(uid): return {r["level"] for r in rows("SELECT DISTINCT level FROM art WHERE user_id=? AND posted=1 AND level>0", (uid,))}

# ---------- per-user design tasks: a user never gets the same task twice (levels + quests) ----------
db.executescript("""CREATE TABLE IF NOT EXISTS task_log(id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, ref TEXT, task TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS ix_task_user ON task_log(user_id);""")
def _build_pool():
    P = {}
    nat_s = ["a mountain valley", "a hidden waterfall", "a calm lake", "a wildflower meadow", "a winding river", "a quiet forest path", "a cliffside garden", "a lotus pond", "a rocky coastline", "a bamboo grove", "a tropical island lagoon", "a snowy ridge"]
    nat_l = ["at sunrise with glowing mist", "under a huge full moon", "at golden hour", "after fresh rain with a soft rainbow haze", "under a sky full of stars", "with floating fireflies at dusk", "in soft morning fog", "lit by warm lantern light"]
    P["Nature"] = [f"{a} {b}" for a in nat_s for b in nat_l]
    wp_s = ["soft pastel mountains", "a neon galaxy swirl", "layered ocean waves at sunset", "glowing moon phases", "a minimal gradient horizon", "a glowing crystal cave", "a quiet pine forest silhouette", "drifting clouds and stars", "an aurora over still water", "a lantern-lit night sky"]
    wp_l = ["a minimal phone wallpaper of", "a dreamy phone wallpaper of", "a dark aesthetic phone wallpaper of", "a bright pastel phone wallpaper of", "a calming phone wallpaper of", "a glowing neon phone wallpaper of"]
    P["Wallpaper"] = [f"{b} {a}" for a in wp_s for b in wp_l]
    fa_s = ["a floating island garden", "a giant glowing mushroom valley", "a crystal forest with waterfalls", "grand mansions floating in the sky joined by vine bridges", "a hidden sky kingdom of glowing towers", "a glowing tree city", "a moonlit waterfall realm", "a cloud harbor with floating lanterns", "an underwater coral castle with no creatures", "an upside-down mountain world"]
    fa_l = ["with lush nature", "lit by lantern light", "at twilight", "in soft pastel colours", "with glowing rivers", "under two moons"]
    P["Fantasy"] = [f"{a} {b}" for a in fa_s for b in fa_l]
    se_s = ["a spring blossom garden", "a summer flower meadow", "an autumn maple path", "a snowy pine forest", "a rainy monsoon forest", "a frozen lake", "a harvest field of golden grain", "a first-snow mountain slope"]
    se_l = ["at golden hour", "with warm lantern light", "at dawn", "under a glowing moon", "after the rain", "with drifting petals or leaves"]
    P["Seasons"] = [f"{a} {b}" for a in se_s for b in se_l]
    bk_s = ["two intertwined flowering vines", "an old key, moths and moonlit fog", "a heart-shaped glowing garden", "a floating castle above storm clouds", "a lighthouse in a stormy sea", "a mossy wall door with a glowing keyhole", "a lone sailboat under a huge moon", "a field of glowing lanterns", "a crystal rose in the snow", "an old library window with falling stars"]
    bk_l = ["romantic", "mysterious", "fantasy adventure", "dark thriller", "cozy"]
    P["Book cover"] = [f"{b} book cover art of {a}, no people, no letters" for a in bk_s for b in bk_l]
    return P
TASK_POOL = _build_pool()
def _task_text(cat, raw): return f"{cat} design: {raw}."

def used_tasks(uid): return {r[0] for r in db.execute("SELECT task FROM task_log WHERE user_id=?", (uid,)).fetchall()}
def current_task(uid, kind, ref):
    r = db.execute("SELECT task FROM task_log WHERE user_id=? AND kind=? AND ref=? ORDER BY id DESC LIMIT 1", (uid, kind, ref)).fetchone()
    return r[0] if r else None
def log_task(uid, kind, ref, task):
    db.execute("INSERT INTO task_log(user_id,kind,ref,task,ts) VALUES(?,?,?,?,?)", (uid, kind, ref, task, time.time())); db.commit()
def pick_task(uid, own=None):
    used = used_tasks(uid)
    free = {c: [t for t in (_task_text(c, x) for x in L) if t not in used] for c, L in TASK_POOL.items()}
    opts = [c for c, L in free.items() if L]
    if own and own not in used: opts.append("Level theme")
    if not opts:  # every task used (very unlikely): start over from the least recently used
        r = db.execute("SELECT task FROM task_log WHERE user_id=? GROUP BY task ORDER BY MIN(id) LIMIT 1", (uid,)).fetchone()
        return r[0] if r else _task_text("Nature", TASK_POOL["Nature"][0])
    c = random.choice(opts)
    return own if c == "Level theme" else random.choice(free[c])

@app.get("/api/level/{n}/task")
def level_task(n: int, reroll: int = 0, authorization: str = Header(None)):
    uid = me(authorization)["id"]
    if n < 1 or not (n == 1 or (n - 1) in done_levels(uid)): raise HTTPException(403, "Finish the previous level first.")
    own = level_info(n)[3]; cur = current_task(uid, "level", str(n))
    if n in done_levels(uid):  # completed: its task never changes
        if not cur: cur = own; log_task(uid, "level", str(n), cur)
        return {"task": cur, "locked": True}
    if cur and not reroll: return {"task": cur, "locked": False}
    t = pick_task(uid, own); log_task(uid, "level", str(n), t)
    return {"task": t, "locked": False}

@app.get("/api/levels")
def levels(authorization: str = Header(None)):
    d = done_levels(me(authorization)["id"]); top = max(5, max(d, default=0) + 3)
    return [{"n": n, "name": nm, "icon": ic, "desc": ds, "reward": rw, "badge": b, "done": n in d, "open": n == 1 or (n - 1) in d} for n, nm, ic, ds, rw, b in map(level_info, range(1, top + 1))]

class Post(BaseModel):
    challenge: str = ""
    level: int = 0

@app.post("/api/art/{aid}/post")
def post_art(aid: int, b: Post, authorization: str = Header(None)):
    u = me(authorization)
    if not db.execute("SELECT 1 FROM art WHERE id=? AND user_id=?", (aid, u["id"])).fetchone(): raise HTTPException(404)
    lv = max(0, b.level); d = done_levels(u["id"])
    if lv > 1 and (lv - 1) not in d: raise HTTPException(400, "Finish the previous level first")
    first = bool(lv) and lv not in d
    db.execute("UPDATE art SET posted=1, challenge=?, level=? WHERE id=? AND user_id=?", (b.challenge if b.challenge in ("daily", "weekly", "monthly") else "", lv, aid, u["id"]))
    if first: db.execute("UPDATE users SET xp=xp+? WHERE id=?", (level_info(lv)[4], u["id"]))
    db.commit(); return {"ok": True, "reward": level_info(lv)[4] if first else 0}

@app.post("/api/art/unlock")
def unlock(authorization: str = Header(None)):
    u = me(authorization)
    if u["xp"] - u["spent"] < 100: raise HTTPException(400, "You need at least 100 XP. Earn it in Brain Arena.")
    db.execute("UPDATE users SET spent=spent+100, credits=credits+1 WHERE id=?", (u["id"],)); db.commit(); return {"ok": True}

@app.get("/api/minds")
def minds(authorization: str = Header(None)):
    me(authorization)
    return rows("SELECT u.username, u.icon, u.xp points, (SELECT COUNT(*) FROM answers a WHERE a.user_id=u.id) answers FROM users u ORDER BY u.xp DESC LIMIT 10")

@app.get("/api/thinktank")
def thinktank(topic: str = "", authorization: str = Header(None)):
    me(authorization)
    qs = rows("SELECT b.id, b.topic, b.real_q AS text FROM qbank b WHERE b.real_q != '' AND EXISTS (SELECT 1 FROM answers a WHERE a.bank_id=b.id AND a.shared=1) AND (?='' OR b.topic=?) ORDER BY b.id DESC LIMIT 10", (topic, topic))
    by = {q["id"]: [] for q in qs}
    if qs:  # one query for all questions (was one query per question)
        for r in rows("SELECT * FROM (SELECT a.bank_id, a.id, a.real_a t, a.score, (SELECT COUNT(*) FROM alikes l WHERE l.ans_id=a.id) likes, "
                      "ROW_NUMBER() OVER (PARTITION BY a.bank_id ORDER BY a.score DESC, a.created DESC) rn FROM answers a "
                      "WHERE a.bank_id = ANY(?) AND a.shared=1 AND a.real_a != '') x WHERE rn <= 30 ORDER BY bank_id, rn", ([q["id"] for q in qs],)):
            by[r["bank_id"]].append(r)
    for q in qs:
        out = []
        for r in by[q["id"]]:
            w = set(re.findall(r"\w+", r["t"].lower()))
            if any(len(w & s) / max(1, len(w | s)) > 0.6 for s, _ in out): continue  # merge near-duplicates
            out.append((w, r))
            if len(out) == 4: break
        q["answers"] = [{k: v for k, v in r.items() if k not in ("bank_id", "rn")} for _, r in out]
    return qs

@app.get("/api/quests")
def quests(authorization: str = Header(None)):
    i = me(authorization)["id"]; n = time.time(); d, w, mo = n - 86400, n - 7 * 86400, n - 30 * 86400
    r = db.execute("SELECT (SELECT COUNT(*) FROM answers WHERE user_id=? AND created>?) a_d, (SELECT COUNT(*) FROM art WHERE user_id=? AND posted=1 AND created>?) p_d, "
                   "(SELECT COUNT(*) FROM comments WHERE user_id=? AND created>?) + (SELECT COUNT(*) FROM acomments WHERE user_id=? AND created>?) c_w, "
                   "(SELECT COUNT(*) FROM art WHERE user_id=? AND posted=1 AND created>?) p_w, (SELECT COUNT(*) FROM art WHERE user_id=? AND posted=1 AND created>?) p_m, "
                   "(SELECT COUNT(*) FROM answers WHERE user_id=? AND created>?) a_m", (i, d, i, d, i, w, i, w, i, w, i, mo, i, mo)).fetchone()
    q = lambda name, desc, cur, mx: {"name": name, "desc": desc, "cur": min(cur, mx), "max": mx}
    return {"daily": [q("Brain Starter", "Answer 1 question today", r["a_d"], 1), q("Share a Design", "Post 1 design today", r["p_d"], 1)],
            "weekly": [q("Community Supporter", "Write 5 kind comments", r["c_w"], 5), q("Project Contributor", "Post 3 designs", r["p_w"], 3)],
            "monthly": [q("Major Contributor", "Post 10 designs", r["p_m"], 10), q("Sparks Expert", "Answer 30 questions", r["a_m"], 30)], "themes": _challenges(i)}

@app.get("/api/badges")
def badges(authorization: str = Header(None)):
    u = me(authorization); i = u["id"]; dl = done_levels(i)
    r = db.execute("SELECT (SELECT COUNT(*) FROM answers WHERE user_id=?) a_all, (SELECT COUNT(*) FROM answers WHERE user_id=? AND shared=1) a_sh, "
                   "(SELECT COUNT(*) FROM answers WHERE user_id=? AND score>=9) a_hi, (SELECT COUNT(*) FROM comments WHERE user_id=?) + (SELECT COUNT(*) FROM acomments WHERE user_id=?) cm, "
                   "(SELECT COUNT(*) FROM art WHERE user_id=? AND posted=1) ps, (SELECT COUNT(*) FROM likes WHERE art_id IN (SELECT id FROM art WHERE user_id=?)) lk", (i,) * 7).fetchone()
    L = [("New User", "Joined Sparks World", True), ("First Quest", "Answered your first question", r["a_all"] >= 1),
         ("Forum Poster", "An answer reached Mind Voices", r["a_sh"] >= 1),
         ("High Scorer", "Scored 9 or 10 on an answer", r["a_hi"] >= 1),
         ("Community Helper", "Wrote 5 comments", r["cm"] >= 5),
         ("Contributor", "Posted 3 designs", r["ps"] >= 3),
         ("Well Liked", "Got 10 likes on designs", r["lk"] >= 10)]
    L += [(level_info(k)[5], f"Completed level {k}: {level_info(k)[1]}", k in dl) for k in range(1, max(5, max(dl, default=0)) + 1)]
    return [{"name": a, "desc": b, "earned": bool(e)} for a, b, e in L]

class NewName(BaseModel):
    username: str
class NewPw(BaseModel):
    old: str
    new: str
class Ic(BaseModel):
    icon: str

@app.post("/api/account/username")
def set_name(b: NewName, authorization: str = Header(None)):
    u = me(authorization); n = b.username.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,15}", n) or n.isdigit(): raise HTTPException(400, "Username: 4-16 characters, start with a letter, letters, numbers, underscore")
    check_text(n, 0, False)
    try: db.execute("UPDATE users SET username=? WHERE id=?", (n, u["id"])); db.commit()
    except IntegrityError: raise HTTPException(400, "Username taken")
    return {"ok": True}

@app.post("/api/account/password")
def set_pw(b: NewPw, authorization: str = Header(None)):
    u = me(authorization); p = b.new
    if not u["salt"] or hp(b.old, u["salt"]) != u["pw"]: raise HTTPException(400, "Current password is wrong")
    if not (len(p) >= 8 and re.search("[a-z]", p) and re.search("[A-Z]", p) and re.search(r"\d", p) and re.search(r"[^A-Za-z0-9]", p)): raise HTTPException(400, "New password needs 8+ characters with upper, lower, number and symbol")
    s = secrets.token_hex(8); db.execute("UPDATE users SET salt=?, pw=? WHERE id=?", (s, hp(p, s), u["id"])); db.commit(); return {"ok": True}

@app.post("/api/icon")
def set_icon(b: Ic, authorization: str = Header(None)):
    u = me(authorization)
    if b.icon in ICONS: db.execute("UPDATE users SET icon=? WHERE id=?", (b.icon, u["id"])); db.commit()
    return {"ok": True}

@app.get("/api/art/mine")
def mine(authorization: str = Header(None)):
    u = me(authorization); return rows(ART + ",posted FROM art a WHERE user_id=? ORDER BY created DESC", (u["id"],))

@app.delete("/api/art/{aid}")
def del_art(aid: int, authorization: str = Header(None)):
    u = me(authorization); a = db.execute("SELECT token FROM art WHERE id=? AND user_id=?", (aid, u["id"])).fetchone()
    if a:
        db.execute("DELETE FROM images WHERE token=?", (a["token"],))
        db.execute("DELETE FROM likes WHERE art_id=?", (aid,)); db.execute("DELETE FROM comments WHERE art_id=?", (aid,)); db.execute("DELETE FROM art WHERE id=?", (aid,)); db.commit()
    return {"ok": True}

@app.get("/api/gallery")  # anonymous: no author names while people judge
def gallery(c: str = "", authorization: str = Header(None)):
    u = me(authorization)
    return rows(ART + " FROM art a WHERE posted=1 AND user_id!=? AND (?='' OR challenge=?) ORDER BY a.created DESC LIMIT 24", (u["id"], c, c))

@app.get("/api/art/top")  # names shown only here, after the voting
def top(c: str = "", authorization: str = Header(None)):
    me(authorization)
    return rows(ART + ",(SELECT username FROM users WHERE id=a.user_id) username FROM art a WHERE posted=1 AND (?='' OR challenge=?) ORDER BY likes DESC, a.created DESC LIMIT 10", (c, c))

@app.post("/api/art/{aid}/like")
def like(aid: int, authorization: str = Header(None)):
    u = me(authorization); a = db.execute("SELECT user_id FROM art WHERE id=? AND posted=1", (aid,)).fetchone()
    if not a or a["user_id"] == u["id"]: raise HTTPException(400, "You can't like this one")
    try:
        db.execute("INSERT INTO likes VALUES(?,?)", (aid, u["id"]))
        db.execute("UPDATE users SET xp=xp+5 WHERE id=?", (a["user_id"],))  # +5 XP for the creator
    except IntegrityError: pass
    db.commit(); return {"likes": db.execute("SELECT COUNT(*) c FROM likes WHERE art_id=?", (aid,)).fetchone()["c"]}

class Cm(BaseModel):
    text: str

@app.get("/api/art/{aid}/comments")
def comments(aid: int, authorization: str = Header(None)):
    me(authorization)
    return rows("SELECT c.text,(SELECT username FROM users WHERE id=c.user_id) username FROM comments c WHERE art_id=? ORDER BY id DESC LIMIT 30", (aid,))

@app.post("/api/art/{aid}/comments")
def comment(aid: int, b: Cm, authorization: str = Header(None)):
    u = me(authorization); t = b.text.strip()[:200]
    if len(t) < 2: raise HTTPException(400, "Write a comment first")
    check_text(t, u["id"])
    db.execute("INSERT INTO comments(art_id,user_id,text,created) VALUES(?,?,?,?)", (aid, u["id"], t, time.time())); db.commit(); return {"ok": True}

@app.delete("/api/account")
def delete_account(authorization: str = Header(None)):
    u = me(authorization); uid = u["id"]
    for a in db.execute("SELECT id,token FROM art WHERE user_id=?", (uid,)).fetchall():
        db.execute("DELETE FROM images WHERE token=?", (a["token"],))
        db.execute("DELETE FROM likes WHERE art_id=?", (a["id"],)); db.execute("DELETE FROM comments WHERE art_id=?", (a["id"],))
    db.execute("DELETE FROM alikes WHERE ans_id IN (SELECT id FROM answers WHERE user_id=?)", (uid,)); db.execute("DELETE FROM acomments WHERE ans_id IN (SELECT id FROM answers WHERE user_id=?)", (uid,))
    for t in ("art", "answers", "questions", "sessions", "likes", "comments", "alikes", "acomments", "task_log"): db.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
    db.execute("DELETE FROM users WHERE id=?", (uid,)); db.commit(); return {"ok": True}


app.mount("/static", StaticFiles(directory="static"))

# ---------- moderation, redaction ----------
WORDS = ["fuck","fck","fuk","fack","phuck","fucker","fucking","motherfucker","shit","shitty","shithead","shitface","bullshit","dipshit","horseshit","bitch","asshole","bastard","slut",
 "dick","cunt","porn","nude","rape","nigger","nigga","whore","retard",
 "madarchod","maderchod","behenchod","bhenchod","benchod","bhenchodh","chutiya","chutiye","chutia","chut","gandu","gaandu","gaand","gand","harami","haramkhor","haramzada",
 "bhosdike","bhosdika","bhosdi","bhosda","bhosadike","lund","lauda","laude","lode","loda","lavda","lavde","lawda","lawde","randi","raand","kutta","kutti","kamina","kamine","bsdk","bkl",
 "puta","mierda","pendejo","cabron","dengey","dengu","lanja","lanjakodaka","puku","pooku","modda","punda","pundai","thevdiya","thevidiya","otha","koothi","sunni",
 "khanki","bokachoda","penchod","zavadya"]
# Long roots also caught when letters are run together or spaced out ("behen ki laude" -> behenkilaude)
ROOTS = ["fuck","madarchod","maderchod","behenchod","bhenchod","benchod","chutiya","chutiye","bhosdike","bhosdika","bhosadike","haramzada","haramkhor",
         "kilaude","kilauda","kelaude","kelauda","kilode","kelode","kilavde","kelavde","kilund","kelund","kichut","kechut"]
sq = lambda s: re.sub(r"(.)\1+", r"\1", s)          # fuuuck -> fuck, chuuutiya -> chutiya
BADRE = re.compile(r"\b(" + "|".join(sq(w) for w in WORDS) + r")(s|es)?\b")
ROOTS_Q = [sq(r) for r in ROOTS]
STRIKES = {}
def norm(t):
    return t.lower().translate(str.maketrans({"@":"a","$":"s","0":"o","1":"i","3":"e","4":"a","5":"s","7":"t","!":"i","*":None}))
def bad_words(t):
    n = sq(norm(t)); flat = re.sub(r"[^a-z]", "", n)
    return bool(BADRE.search(n)) or any(r in flat for r in ROOTS_Q)

def check_text(t, uid=0, use_llm=True):
    s = STRIKES.setdefault(uid, []); s[:] = [x for x in s if x > time.time() - 600]
    if uid and len(s) >= 5: raise HTTPException(429, "Too many blocked messages. Please wait 10 minutes.")
    bad = bad_words(t)
    if not bad and use_llm and len(t) > 3:
        r = llm('Reply ONLY JSON {"offensive": true or false}. true if the text has vulgar words or slurs in any language (including romanized Hindi, Telugu, Tamil), '
                "insults, harassment, hate, or sexual content. Friendly casual slang like bro or no cap is fine.", t)
        if r is None and (GROQ or GEMINI): raise HTTPException(503, "The safety check is busy right now. Please try again in a minute.")
        bad = bool(r and r.get("offensive") is True)
    if bad:
        s.append(time.time()); raise HTTPException(400, "Please rephrase without bad words or insults. Friendly slang is fine!")

def redact(t):
    t = re.sub(r"https?://\S+|www\.\S+", "[link]", t)
    t = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", t)
    t = re.sub(r"(?<!\w)@\w+", "[handle]", t)
    return re.sub(r"\+?\d[\d\s().-]{7,}\d", "[number]", t)

class Consent(BaseModel):
    terms: bool
    summary_ok: bool

@app.post("/api/consent")
def consent(c: Consent, authorization: str = Header(None)):
    u = me(authorization)
    if not (c.terms and c.summary_ok): raise HTTPException(400, "Both boxes are needed to continue")
    db.execute("UPDATE users SET consent_ver=? WHERE id=?", (TERMS_VER, u["id"])); db.commit(); return {"ok": True}

@app.post("/api/art/refine")
def refine(b: Gen, authorization: str = Header(None)):
    u = me(authorization); p = b.prompt.strip()[:400]
    if len(p) < 3: raise HTTPException(400, "Describe your idea first")
    check_text(p, u["id"])
    r = llm('Turn the idea (any language) into ONE clear English image prompt of at most 40 words. Keep the meaning, add style words like aesthetic, soft lighting, detailed. '
            'Only fictional objects, places, patterns or creatures; drop any people, brands, text or symbols. Reply ONLY JSON {"refined": "..."}', (f"Theme to fit: {b.theme}\n" if b.theme else "") + p)
    if not r or not r.get("refined"): raise HTTPException(503, "Refiner is busy. Try again.")
    return {"refined": str(r["refined"])[:300]}

# ---------- anonymous idea forum ----------
db.executescript("""
CREATE TABLE IF NOT EXISTS alikes(ans_id INTEGER, user_id INTEGER, UNIQUE(ans_id,user_id));
CREATE TABLE IF NOT EXISTS acomments(id INTEGER PRIMARY KEY, ans_id INTEGER, user_id INTEGER, text TEXT, created REAL);
""")
@app.get("/api/forum")
def forum(topic: str = "", authorization: str = Header(None)):
    me(authorization)
    return rows("SELECT a.id, a.topic, q.scenario, a.original text, (SELECT COUNT(*) FROM alikes l WHERE l.ans_id=a.id) likes FROM answers a JOIN questions q ON q.id=a.qid "
                "WHERE a.shared=1 AND (?='' OR a.topic=?) ORDER BY RANDOM() LIMIT 12", (topic, topic))

@app.post("/api/forum/{aid}/like")
def flike(aid: int, authorization: str = Header(None)):
    u = me(authorization); a = db.execute("SELECT user_id FROM answers WHERE id=? AND shared=1", (aid,)).fetchone()
    if not a or a["user_id"] == u["id"]: raise HTTPException(400, "You can't like this one")
    if db.execute("DELETE FROM alikes WHERE ans_id=? AND user_id=?", (aid, u["id"])).rowcount == 0: db.execute("INSERT INTO alikes VALUES(?,?)", (aid, u["id"]))
    db.commit(); return {"likes": db.execute("SELECT COUNT(*) c FROM alikes WHERE ans_id=?", (aid,)).fetchone()["c"]}

@app.get("/api/forum/{aid}/comments")  # anonymous: no names
def fcomments(aid: int, authorization: str = Header(None)):
    me(authorization); return rows("SELECT text FROM acomments WHERE ans_id=? ORDER BY id DESC LIMIT 30", (aid,))

@app.post("/api/forum/{aid}/comments")
def fcomment(aid: int, b: Cm, authorization: str = Header(None)):
    u = me(authorization); t = b.text.strip()[:200]
    if len(t) < 2: raise HTTPException(400, "Write a comment first")
    check_text(t, u["id"])
    db.execute("INSERT INTO acomments(ans_id,user_id,text,created) VALUES(?,?,?,?)", (aid, u["id"], t, time.time())); db.commit(); return {"ok": True}

@app.get("/api/stats")
def stats(authorization: str = Header(None)):
    i = me(authorization)["id"]
    r = db.execute("SELECT (SELECT COUNT(*) FROM answers WHERE user_id=?) a, (SELECT COUNT(*) FROM alikes WHERE ans_id IN (SELECT id FROM answers WHERE user_id=?)) al, "
                   "(SELECT COUNT(*) FROM art WHERE user_id=? AND posted=1) d, (SELECT COUNT(*) FROM likes WHERE art_id IN (SELECT id FROM art WHERE user_id=?)) dl", (i,) * 4).fetchone()
    return {"answers": r["a"], "answer_likes": r["al"], "designs": r["d"], "design_likes": r["dl"]}

@app.get("/terms")
def p_terms(): return FileResponse("static/terms.html")

@app.get("/app")
def p_app(): return FileResponse("static/app.html")

# ---------- email OTP: we only SEND a code. We never read the inbox. Only a keyed hash of the email is stored. ----------
import hmac, smtplib, ssl, random
from email.message import EmailMessage
SMTP_USER, SMTP_PASS = os.getenv("SMTP_EMAIL"), os.getenv("SMTP_APP_PASSWORD")
BREVO_KEY, MAIL_FROM = os.getenv("BREVO_API_KEY"), (os.getenv("MAIL_FROM_EMAIL") or os.getenv("SMTP_EMAIL"))
if os.getenv("EMAIL_SECRET"): EKEY = os.getenv("EMAIL_SECRET").strip()      # on the host: set EMAIL_SECRET so it never changes
elif os.path.exists(".email_secret"): EKEY = open(".email_secret").read().strip()
else:
    if os.getenv("RENDER"): print("WARNING: EMAIL_SECRET is not set. Existing accounts will not match after a restart.")
    EKEY = secrets.token_hex(32)
    with open(".email_secret", "w") as _f: _f.write(EKEY)
    try: os.chmod(".email_secret", 0o600)
    except OSError: pass
try: db.execute("ALTER TABLE users ADD COLUMN email_hash TEXT"); db.commit()
except OperationalError: pass
db.executescript("""CREATE TABLE IF NOT EXISTS pending(kind TEXT, key TEXT, data TEXT, otp TEXT, exp REAL, tries INTEGER DEFAULT 0, sent REAL, PRIMARY KEY(kind,key));
CREATE UNIQUE INDEX IF NOT EXISTS ux_email ON users(email_hash);""")
db.finish_setup()
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
SENDS = {}

def ehash(e):
    e = e.strip().lower(); l, d = e.rsplit("@", 1); l = l.split("+")[0]
    if d in ("gmail.com", "googlemail.com"): l, d = l.replace(".", ""), "gmail.com"
    return hmac.new(EKEY.encode(), f"{l}@{d}".encode(), "sha256").hexdigest()
def ohash(c): return hmac.new(EKEY.encode(), c.strip().encode(), "sha256").hexdigest()
COMMON = {"password1!", "qwerty123!", "welcome1!", "admin123!", "p@ssw0rd", "passw0rd!", "p@ssword1", "sparksworld1!", "iloveyou1!", "letmein1!"}
DISPOSABLE = {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "temp-mail.org", "yopmail.com", "trashmail.com", "sharklasers.com", "getnada.com", "dispostable.com", "maildrop.cc", "throwawaymail.com", "fakeinbox.com", "mintemail.com", "moakt.com"}
def strong(p): return len(p) >= 8 and re.search("[a-z]", p) and re.search("[A-Z]", p) and re.search(r"\d", p) and re.search(r"[^A-Za-z0-9]", p) and p.lower() not in COMMON
def mask(e): l, d = e.split("@", 1); return l[:1] + "***@" + d
def need_email(e):
    if not EMAIL_RE.match(e.strip()): raise HTTPException(400, "Enter a valid email address")
    if e.strip().lower().rsplit("@", 1)[1] in DISPOSABLE: raise HTTPException(400, "Please use a permanent email address")

TS_SECRET = os.getenv("TURNSTILE_SECRET")
SMTP_HOST, SMTP_PORT, MAX_DAILY = os.getenv("SMTP_HOST") or "smtp.gmail.com", int(os.getenv("SMTP_PORT") or 465), 300
def captcha_ok(token, request):
    if not TS_SECRET: return
    try:
        r = httpx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify", timeout=10, data={"secret": TS_SECRET, "response": token or "", "remoteip": request.client.host if request.client else ""})
        ok = r.json().get("success") is True
    except Exception: ok = False
    if not ok: raise HTTPException(400, "Please complete the human check and try again.")

def throttle(request, key):
    now = time.time(); ip = request.client.host if request.client else "?"
    g = SENDS.setdefault("all", []); g[:] = [x for x in g if x > now - 86400]
    if len(g) >= MAX_DAILY: raise HTTPException(503, "We are sending a lot of codes right now. Please try again tomorrow.")
    for k, lim, win in ((ip, 10, 3600), (key, 3, 86400)):
        s = SENDS.setdefault(k, []); s[:] = [x for x in s if x > now - win]
        if len(s) >= lim: raise HTTPException(429, "Too many codes requested. Try again later.")
    for k in (ip, key): SENDS[k].append(now)
    g.append(now)

def guard_ip(request):
    s = FAILS.setdefault("otp:" + (request.client.host if request.client else "?"), []); s[:] = [x for x in s if x > time.time() - 900]
    if len(s) >= 15: raise HTTPException(429, "Too many wrong codes from this device. Try again in 15 minutes.")
    return s

def send_code(kind, key, to, data, why):
    db.execute("DELETE FROM pending WHERE exp<?", (time.time(),))
    old = db.execute("SELECT sent FROM pending WHERE kind=? AND key=?", (kind, key)).fetchone()
    if old and time.time() - old["sent"] < 60: raise HTTPException(429, "Please wait a minute before asking for another code.")
    code = f"{random.SystemRandom().randrange(10**6):06d}"
    db.execute("INSERT OR REPLACE INTO pending(kind,key,data,otp,exp,tries,sent) VALUES(?,?,?,?,?,0,?)", (kind, key, data, ohash(code), time.time() + 600, time.time())); db.commit()
    body = f"Your Sparks World {why} code is {code}. It expires in 10 minutes and works once. Nobody from Sparks World will ever ask you for it. If you did not ask for it, ignore this email."
    if BREVO_KEY and MAIL_FROM:  # works on hosts that block SMTP ports (Render free)
        try:
            r = httpx.post("https://api.brevo.com/v3/smtp/email", timeout=15, headers={"api-key": BREVO_KEY, "accept": "application/json", "content-type": "application/json"},
                           json={"sender": {"name": "Sparks World", "email": MAIL_FROM}, "to": [{"email": to}], "subject": f"Sparks World code: {code}", "textContent": body})
            r.raise_for_status()
        except Exception as e:
            print("email failed:", type(e).__name__, str(getattr(getattr(e, "response", None), "text", e))[:200]); raise HTTPException(503, "Could not send the email right now. Please try again later.")
        return
    if not (SMTP_USER and SMTP_PASS):
        print(f"[DEV ONLY] {why} code for {mask(to)}: {code}  (set BREVO_API_KEY and MAIL_FROM_EMAIL, or SMTP_EMAIL and SMTP_APP_PASSWORD, to send real emails)"); return
    msg = EmailMessage(); msg["Subject"] = f"Sparks World code: {code}"; msg["From"] = SMTP_USER; msg["To"] = to
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=20) if SMTP_PORT == 465 else smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
        with s:
            if SMTP_PORT != 465: s.starttls(context=ctx)
            s.login(SMTP_USER, SMTP_PASS); s.send_message(msg)
    except Exception as e:
        print("email failed:", e); raise HTTPException(503, "Could not send the email right now. Please try again later.")

def check_otp(kind, key, code, ip=None):
    p = db.execute("SELECT * FROM pending WHERE kind=? AND key=?", (kind, key)).fetchone()
    if not p or p["exp"] < time.time(): raise HTTPException(400, "That code is not valid or has expired. Ask for a new one.")
    if p["tries"] >= 5: raise HTTPException(429, "Too many wrong tries. Ask for a new code.")
    if not hmac.compare_digest(p["otp"], ohash(code)):
        db.execute("UPDATE pending SET tries=tries+1 WHERE kind=? AND key=?", (kind, key)); db.commit()
        if ip is not None: ip.append(time.time())
        raise HTTPException(400, "Wrong code. Try again.")
    return p

class SU(BaseModel):
    username: str
    password: str
    email: str
    lang: str = "English"
    adult: bool = False
    terms: bool = False
    summary_ok: bool = False
    captcha: str = ""
class VF(BaseModel):
    email: str
    code: str
class FG(BaseModel):
    email: str
    captcha: str = ""
class RS(BaseModel):
    email: str
    code: str
    password: str

@app.post("/api/signup/start")
def signup_start(b: SU, request: Request):
    if not b.adult: raise HTTPException(400, "You must confirm you are 18 or older")
    if not (b.terms and b.summary_ok): raise HTTPException(400, "Please accept the terms and the anonymous-summary notice")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,15}", b.username) or b.username.isdigit(): raise HTTPException(400, "Username: 4-16 characters, start with a letter, letters, numbers, underscore. Example: NovaMind_07")
    if not strong(b.password) or b.username.lower() in b.password.lower(): raise HTTPException(400, "Password: 8+ characters with upper, lower, number and symbol. Avoid common passwords and your gamer name.")
    need_email(b.email); check_text(b.username, 0, False); captcha_ok(b.captcha, request); eh = ehash(b.email)
    if db.execute("SELECT 1 FROM users WHERE username=?", (b.username,)).fetchone(): raise HTTPException(400, "That gamer name is taken")
    if db.execute("SELECT 1 FROM users WHERE email_hash=?", (eh,)).fetchone(): raise HTTPException(400, "An account with this email already exists. Please log in, or use Forgot password. One email can only have one account.")
    throttle(request, eh)
    salt = secrets.token_hex(8)
    send_code("signup", eh, b.email.strip(), json.dumps({"u": b.username, "s": salt, "p": hp(b.password, salt), "l": b.lang}), "sign-up")
    return {"ok": True}

@app.post("/api/signup/resend")
def signup_resend(b: FG, request: Request):
    need_email(b.email); eh = ehash(b.email)
    p = db.execute("SELECT data FROM pending WHERE kind='signup' AND key=?", (eh,)).fetchone()
    if p: throttle(request, eh); send_code("signup", eh, b.email.strip(), p["data"], "sign-up")
    return {"ok": True}

@app.post("/api/signup/verify")
def signup_verify(b: VF, request: Request):
    need_email(b.email); eh = ehash(b.email); ipl = guard_ip(request); d = json.loads(check_otp("signup", eh, b.code, ipl)["data"])
    try: cur = db.execute("INSERT INTO users(username,salt,pw,lang,consent_ver,email_hash) VALUES(?,?,?,?,?,?)", (d["u"], d["s"], d["p"], d["l"], TERMS_VER, eh))
    except IntegrityError: raise HTTPException(400, "That gamer name or email is already in use")
    db.execute("DELETE FROM pending WHERE kind='signup' AND key=?", (eh,)); db.commit()
    return {"token": new_session(cur.lastrowid), "lang": d["l"], "xp": 0}

@app.post("/api/forgot/start")
def forgot_start(b: FG, request: Request):
    need_email(b.email); captcha_ok(b.captcha, request); eh = ehash(b.email); throttle(request, eh)
    if db.execute("SELECT 1 FROM users WHERE email_hash=?", (eh,)).fetchone(): send_code("reset", eh, b.email.strip(), "{}", "password reset")
    return {"ok": True}  # same answer whether or not the email has an account

@app.post("/api/forgot/verify")
def forgot_verify(b: RS, request: Request):
    if not strong(b.password): raise HTTPException(400, "New password needs 8+ characters with upper, lower, number and symbol, and must not be a common one")
    need_email(b.email); eh = ehash(b.email); ipl = guard_ip(request); check_otp("reset", eh, b.code, ipl)
    u = db.execute("SELECT id FROM users WHERE email_hash=?", (eh,)).fetchone()
    if not u: raise HTTPException(400, "That code is not valid or has expired. Ask for a new one.")
    s = secrets.token_hex(8)
    db.execute("UPDATE users SET salt=?, pw=? WHERE id=?", (s, hp(b.password, s), u["id"])); db.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
    db.execute("DELETE FROM pending WHERE kind='reset' AND key=?", (eh,)); db.commit(); return {"ok": True}

