import os
import time
import json
import uuid
import random
import sqlite3
import threading
from flask import Flask, render_template_string, request, jsonify, redirect, url_for, session
import requests
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, RateLimitError

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "scar_x_vivek_domain_ultra_secret_2026")

# Admin Credentials
ADMIN_USER = "SAHIL"
ADMIN_PASS = "SAHILXVIVEK"

DB_FILE = "database.db"

DOC_ID = "29088580780787855"
IG_APP_ID = "936619743392459"

# ================= DATABASE SETUP =================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT,
                    session_id TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS stats (
                    key TEXT PRIMARY KEY,
                    value INTEGER
                )''')
    
    # Default stats
    c.execute("INSERT OR IGNORE INTO stats (key, value) VALUES ('messages_sent', 0)")
    c.execute("INSERT OR IGNORE INTO stats (key, value) VALUES ('titles_renamed', 0)")
    
    # Default settings
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('target_ids', '')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('messages', '')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('titles', '')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('delay', '10')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('rename_interval', '5')")
    
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# ================= GLOBAL STATE =================
IS_RUNNING = False
LOGS = []

def add_log(msg):
    timestamp = time.strftime("%H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    LOGS.append(formatted)
    if len(LOGS) > 150:
        LOGS.pop(0)

def increment_stat(key, amount=1):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE stats SET value = value + ? WHERE key = ?", (amount, key))
    conn.commit()
    conn.close()

# ================= INSTAGRAM FINGERPRINT & HELPERS =================
def setup_mobile_fingerprint(cl):
    cl.set_user_agent(
        "Instagram 312.0.0.22.114 Android "
        "(33/13; 420dpi; 1080x2400; OnePlus; "
        "GM1913; OnePlus7Pro; qcom; en_US)"
    )
    cl.set_locale("en_US")
    cl.set_country_code(1)
    cl.set_timezone_offset(-18000)

    uuids = {
        "phone_id": str(uuid.uuid4()),
        "uuid": str(uuid.uuid4()),
        "client_session_id": str(uuid.uuid4()),
        "advertising_id": str(uuid.uuid4()),
        "device_id": "android-" + uuid.uuid4().hex[:16]
    }
    cl.set_uuids(uuids)

    cl.private.headers.update({
        "X-IG-App-ID": IG_APP_ID,
        "X-IG-Device-ID": uuids["uuid"],
        "X-IG-Android-ID": uuids["device_id"],
        "X-IG-Timezone-Offset": "-18000",
        "Accept-Language": "en-US",
        "Connection": "keep-alive"
    })

def fetch_all_groups_from_acc(session_id):
    cl = Client()
    setup_mobile_fingerprint(cl)
    cl.login_by_sessionid(session_id)
    threads = cl.direct_threads(amount=100)
    group_ids = [
        str(t.id) for t in threads
        if getattr(t, "is_group", False) and len(t.users) >= 2
    ]
    return group_ids

def graphql_rename(cl, thread_id, title):
    csrf = cl.private.cookies.get("csrftoken", "")
    cl.private.headers.update({
        "X-CSRFToken": csrf,
        "Referer": f"https://www.instagram.com/direct/t/{thread_id}/"
    })

    payload = {
        "doc_id": DOC_ID,
        "variables": json.dumps({
            "thread_fbid": str(thread_id),
            "new_title": title
        })
    }

    r = cl.private.post(
        "https://www.instagram.com/api/graphql/",
        data=payload,
        timeout=10
    )
    return r.status_code == 200

def rename_thread_safe(cl, thread_id, title):
    try:
        cl.private_request(
            f"direct_v2/threads/{thread_id}/update_title/",
            data={"title": title}
        )
        return True
    except RateLimitError:
        return graphql_rename(cl, thread_id, title)
    except Exception as e:
        if "rate" in str(e).lower():
            return graphql_rename(cl, thread_id, title)
        return False

# ================= WORKER THREAD =================
def bot_worker():
    global IS_RUNNING
    add_log("[SYSTEM] ⚡ Sahil's Void Execution Engine STARTED")

    # Counter tracking message count per group ID
    msg_counter = {}

    while IS_RUNNING:
        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT * FROM accounts")
        accounts = c.fetchall()

        c.execute("SELECT value FROM settings WHERE key='target_ids'")
        target_ids_raw = c.fetchone()['value']
        target_ids = [t.strip() for t in target_ids_raw.split('\n') if t.strip()]

        c.execute("SELECT value FROM settings WHERE key='messages'")
        msgs_raw = c.fetchone()['value']
        messages = [m.strip() for m in msgs_raw.split('\n') if m.strip()]

        c.execute("SELECT value FROM settings WHERE key='titles'")
        titles_raw = c.fetchone()['value']
        titles = [t.strip() for t in titles_raw.split('\n') if t.strip()]

        c.execute("SELECT value FROM settings WHERE key='delay'")
        delay = int(c.fetchone()['value'] or 10)

        c.execute("SELECT value FROM settings WHERE key='rename_interval'")
        rename_interval = int(c.fetchone()['value'] or 5)

        conn.close()

        if not accounts or not target_ids or not messages:
            add_log("[WARN] ⚠️ Accounts, Group IDs or Messages missing! Waiting...")
            time.sleep(10)
            continue

        for acc in accounts:
            if not IS_RUNNING:
                break

            cl = Client()
            setup_mobile_fingerprint(cl)

            try:
                cl.login_by_sessionid(acc['session_id'])
                add_log(f"[LOGIN] ✅ Account Active: {acc['username']}")
            except Exception as e:
                add_log(f"[ERROR] ❌ Session Login Failed ({acc['username']}): {e}")
                continue

            for target_id in target_ids:
                if not IS_RUNNING:
                    break

                if target_id not in msg_counter:
                    msg_counter[target_id] = 0

                # Send Message
                msg = random.choice(messages)
                try:
                    cl.direct_send(msg, thread_ids=[target_id])
                    increment_stat('messages_sent')
                    msg_counter[target_id] += 1
                    add_log(f"[SENT] 📨 [{target_id}] ({msg_counter[target_id]}/{rename_interval}): {msg[:25]}...")
                except Exception as e:
                    add_log(f"[ERROR] ❌ Send Failed to {target_id}: {e}")

                # Rename Title when specified count is reached
                if titles and msg_counter[target_id] >= rename_interval:
                    title = random.choice(titles)
                    try:
                        success = rename_thread_safe(cl, target_id, title)
                        if success:
                            increment_stat('titles_renamed')
                            add_log(f"[RENAME] 📝 Title Updated [{target_id}] -> {title}")
                            msg_counter[target_id] = 0
                        else:
                            add_log(f"[WARN] ⚠️ Rename Failed for {target_id}")
                    except Exception as e:
                        add_log(f"[ERROR] ❌ Title Rename Error: {e}")

                time.sleep(delay)

    add_log("[SYSTEM] 🛑 Sahil's Void Execution Engine STOPPED")

# ================= UI / DASHBOARD TEMPLATE =================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sahil's Void</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #06070c;
            --panel-bg: rgba(18, 18, 28, 0.75);
            --primary: #ff0055;
            --primary-glow: rgba(255, 0, 85, 0.4);
            --cyan: #00f0ff;
            --cyan-glow: rgba(0, 240, 255, 0.3);
            --text-color: #e2e8f0;
            --border-color: rgba(255, 0, 85, 0.25);
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        
        body { 
            background: var(--bg-color); 
            background-image: 
                radial-gradient(circle at 15% 15%, rgba(255, 0, 85, 0.12) 0%, transparent 40%),
                radial-gradient(circle at 85% 85%, rgba(0, 240, 255, 0.08) 0%, transparent 40%);
            color: var(--text-color); 
            font-family: 'Rajdhani', sans-serif; 
            min-height: 100vh;
            padding: 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }

        .container {
            width: 100%;
            max-width: 1100px;
        }

        header {
            text-align: center;
            margin-bottom: 25px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 15px;
        }

        header h1 {
            font-family: 'Orbitron', sans-serif;
            font-size: 2.2rem;
            font-weight: 900;
            letter-spacing: 2px;
            background: linear-gradient(90deg, #ff0055, #00f0ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-shadow: 0 0 20px var(--primary-glow);
        }

        header p {
            color: #8a8a9e;
            font-size: 0.95rem;
            letter-spacing: 1px;
            margin-top: 5px;
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 25px;
        }

        .stat-card {
            background: var(--panel-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 15px;
            text-align: center;
            backdrop-filter: blur(10px);
            box-shadow: 0 8px 24px rgba(0,0,0,0.5);
            transition: transform 0.2s ease;
        }

        .stat-card:hover { transform: translateY(-3px); }
        .stat-card .title { font-size: 0.85rem; color: #a0a0b8; text-transform: uppercase; }
        .stat-card .value { font-family: 'Orbitron', sans-serif; font-size: 1.8rem; font-weight: 700; color: #fff; margin-top: 5px; }

        .status-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: bold;
        }
        .status-running { background: rgba(0, 255, 102, 0.15); color: #00ff66; border: 1px solid #00ff66; box-shadow: 0 0 10px rgba(0,255,102,0.3); }
        .status-stopped { background: rgba(255, 0, 85, 0.15); color: #ff0055; border: 1px solid #ff0055; box-shadow: 0 0 10px rgba(255,0,85,0.3); }

        .controls {
            display: flex;
            gap: 15px;
            margin-bottom: 25px;
        }

        .btn {
            flex: 1;
            padding: 14px;
            border: none;
            border-radius: 8px;
            font-family: 'Orbitron', sans-serif;
            font-size: 1rem;
            font-weight: 700;
            cursor: pointer;
            text-transform: uppercase;
            letter-spacing: 1px;
            transition: all 0.3s ease;
            text-decoration: none;
            text-align: center;
            display: block;
        }

        .btn-start {
            background: linear-gradient(135deg, #ff0055, #ff5500);
            color: #fff;
            box-shadow: 0 0 15px var(--primary-glow);
        }
        .btn-start:hover { box-shadow: 0 0 25px var(--primary); transform: scale(1.01); }

        .btn-stop {
            background: linear-gradient(135deg, #3a3a4c, #1a1a24);
            color: #ff4444;
            border: 1px solid #ff4444;
        }
        .btn-stop:hover { background: #ff4444; color: #fff; box-shadow: 0 0 15px rgba(255,68,68,0.5); }

        .btn-fetch {
            background: linear-gradient(135deg, #00f0ff, #0077ff);
            color: #000;
            font-size: 0.85rem;
            padding: 8px 15px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: bold;
            display: inline-block;
        }

        .grid-layout {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 25px;
        }

        @media(max-width: 768px) {
            .grid-layout { grid-template-columns: 1fr; }
        }

        .card {
            background: var(--panel-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            backdrop-filter: blur(10px);
            box-shadow: 0 8px 24px rgba(0,0,0,0.5);
        }

        .card h3 {
            font-family: 'Orbitron', sans-serif;
            color: var(--cyan);
            font-size: 1.1rem;
            margin-bottom: 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid rgba(0, 240, 255, 0.15);
            padding-bottom: 8px;
        }

        label {
            display: block;
            font-size: 0.9rem;
            color: #b0b0cc;
            margin-top: 10px;
            margin-bottom: 4px;
        }

        input[type="text"], input[type="number"], textarea {
            width: 100%;
            padding: 10px 12px;
            background: rgba(10, 10, 16, 0.8);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            color: #fff;
            font-family: 'Rajdhani', sans-serif;
            font-size: 0.95rem;
            outline: none;
            transition: border 0.3s ease;
        }

        input:focus, textarea:focus {
            border-color: var(--cyan);
            box-shadow: 0 0 8px var(--cyan-glow);
        }

        button[type="submit"] {
            width: 100%;
            margin-top: 15px;
            padding: 12px;
            background: linear-gradient(90deg, var(--primary), #cc0044);
            border: none;
            border-radius: 6px;
            color: #fff;
            font-family: 'Orbitron', sans-serif;
            font-weight: 700;
            cursor: pointer;
            box-shadow: 0 4px 12px var(--primary-glow);
        }

        .acc-list {
            list-style: none;
            margin-top: 10px;
            max-height: 150px;
            overflow-y: auto;
        }

        .acc-item {
            display: flex;
            justify-content: space-between;
            background: rgba(255,255,255,0.03);
            padding: 8px 12px;
            border-radius: 6px;
            margin-bottom: 6px;
            font-size: 0.9rem;
        }

        .acc-item a { color: #ff4444; text-decoration: none; font-weight: bold; }

        .logs-panel {
            background: #030305;
            border: 1px solid rgba(0, 240, 255, 0.2);
            border-radius: 12px;
            padding: 15px;
            font-family: monospace;
            font-size: 0.85rem;
            height: 280px;
            overflow-y: auto;
            color: #00f0ff;
            box-shadow: inset 0 0 15px rgba(0,0,0,0.8);
        }

        .log-entry { margin-bottom: 4px; line-height: 1.4; }

        footer {
            margin-top: 30px;
            text-align: center;
            font-family: 'Orbitron', sans-serif;
            font-size: 0.85rem;
            letter-spacing: 2px;
            color: #ff0055;
            text-shadow: 0 0 10px var(--primary-glow);
            padding: 15px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Sahil's Void</h1>
            <p>HIGH PERFORMANCE INSTAGRAM MULTI-THREAD COMMAND CENTER</p>
        </header>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="title">Active Fleet</div>
                <div class="value">{{ acc_count }}</div>
            </div>
            <div class="stat-card">
                <div class="title">Engine Status</div>
                <div class="value">
                    <span class="status-badge {{ 'status-running' if is_running else 'status-stopped' }}">
                        {{ 'ONLINE' if is_running else 'OFFLINE' }}
                    </span>
                </div>
            </div>
            <div class="stat-card">
                <div class="title">Messages Sent</div>
                <div class="value">{{ stats['messages_sent'] }}</div>
            </div>
            <div class="stat-card">
                <div class="title">Titles Renamed</div>
                <div class="value">{{ stats['titles_renamed'] }}</div>
            </div>
        </div>

        <div class="controls">
            {% if not is_running %}
                <a href="/start" class="btn btn-start">► LAUNCH ATTACK ENGINE</a>
            {% else %}
                <a href="/stop" class="btn btn-stop">🛑 TERMINATE EXECUTION</a>
            {% endif %}
        </div>

        <div class="grid-layout">
            <div class="card">
                <h3>
                    Fleet Management
                    <a href="/fetch_all_groups" class="btn-fetch">📡 AUTO-FETCH ALL GROUPS</a>
                </h3>
                <form action="/add_account" method="POST">
                    <label>Account Tag/Name:</label>
                    <input type="text" name="username" placeholder="e.g. Account_01" required>
                    <label>Session ID:</label>
                    <input type="text" name="session_id" placeholder="Paste Instagram Session ID" required>
                    <button type="submit">+ ADD FLEET ACCOUNT</button>
                </form>

                <ul class="acc-list">
                {% for acc in accounts %}
                    <li class="acc-item">
                        <span>👤 {{ acc.username }}</span>
                        <a href="/del_account/{{ acc.id }}">REMOVE</a>
                    </li>
                {% endfor %}
                </ul>
            </div>

            <div class="card">
                <h3>Engine Configurations</h3>
                <form action="/save_settings" method="POST">
                    <label>Target Group / Thread IDs (1 per line):</label>
                    <textarea name="target_ids" rows="3" placeholder="Thread IDs">{{ settings['target_ids'] }}</textarea>
                    
                    <label>Messages Collection (1 per line):</label>
                    <textarea name="messages" rows="3" placeholder="Message lines">{{ settings['messages'] }}</textarea>
                    
                    <label>Group Name Titles (1 per line):</label>
                    <textarea name="titles" rows="2" placeholder="Titles to cycle">{{ settings['titles'] }}</textarea>

                    <div style="display: flex; gap: 10px;">
                        <div style="flex: 1;">
                            <label>Delay (Secs):</label>
                            <input type="number" name="delay" value="{{ settings['delay'] }}">
                        </div>
                        <div style="flex: 1;">
                            <label>Rename After (Msgs):</label>
                            <input type="number" name="rename_interval" value="{{ settings['rename_interval'] }}">
                        </div>
                    </div>

                    <button type="submit">SAVE ENGINE CONFIGS</button>
                </form>
            </div>
        </div>

        <div class="card">
            <h3>Live Telemetry Feed</h3>
            <div class="logs-panel" id="logBox">
                {% for log in logs %}
                    <div class="log-entry">{{ log }}</div>
                {% endfor %}
            </div>
        </div>

        <footer>
            DEVELOPER : SAHIL
        </footer>
    </div>

<script>
    var logBox = document.getElementById("logBox");
    logBox.scrollTop = logBox.scrollHeight;
</script>
</body>
</html>
"""

# ================= LOGIN TEMPLATE =================
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>LOGIN - Sahil's Void</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@700&family=Rajdhani:wght@600&display=swap" rel="stylesheet">
    <style>
        body {
            background: #06070c;
            color: #fff;
            font-family: 'Rajdhani', sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }
        .login-box {
            background: rgba(18, 18, 28, 0.85);
            border: 1px solid #ff0055;
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0 0 25px rgba(255, 0, 85, 0.3);
            width: 320px;
            text-align: center;
            backdrop-filter: blur(10px);
        }
        h2 { font-family: 'Orbitron', sans-serif; color: #ff0055; margin-bottom: 20px; font-size: 1.3rem; }
        input {
            width: 100%;
            padding: 12px;
            margin: 8px 0;
            background: #0c0c14;
            border: 1px solid #333;
            color: #fff;
            border-radius: 6px;
            box-sizing: border-box;
            outline: none;
        }
        input:focus { border-color: #00f0ff; }
        button {
            width: 100%;
            padding: 12px;
            margin-top: 15px;
            background: #ff0055;
            border: none;
            color: #fff;
            font-family: 'Orbitron', sans-serif;
            font-weight: bold;
            border-radius: 6px;
            cursor: pointer;
            box-shadow: 0 0 10px rgba(255, 0, 85, 0.5);
        }
    </style>
</head>
<body>
    <div class="login-box">
        <h2>Sahil's Void</h2>
        <form method="post">
            <input type="text" name="username" placeholder="Admin Username" required><br>
            <input type="password" name="password" placeholder="Password" required><br>
            <button type="submit">AUTHENTICATE</button>
        </form>
    </div>
</body>
</html>
"""

# ================= ROUTES =================
@app.route('/login', methods=['GET', 'POST'])
def auth_login():
    if request.method == 'POST':
        if request.form.get('username') == ADMIN_USER and request.form.get('password') == ADMIN_PASS:
            session['logged_in'] = True
            return redirect('/')
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/')
def dashboard():
    if not session.get('logged_in'):
        return redirect('/login')
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM accounts")
    accounts = c.fetchall()

    c.execute("SELECT * FROM settings")
    settings = {row['key']: row['value'] for row in c.fetchall()}

    c.execute("SELECT * FROM stats")
    stats = {row['key']: row['value'] for row in c.fetchall()}
    conn.close()

    return render_template_string(
        HTML_TEMPLATE, 
        is_running=IS_RUNNING, 
        accounts=accounts, 
        acc_count=len(accounts),
        settings=settings, 
        stats=stats,
        logs=LOGS
    )

@app.route('/add_account', methods=['POST'])
def add_account():
    username = request.form.get('username')
    session_id = request.form.get('session_id')
    if username and session_id:
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO accounts (username, session_id) VALUES (?, ?)", (username, session_id))
        conn.commit()
        conn.close()
        add_log(f"[SYSTEM] ➕ Fleet account added: {username}")
    return redirect('/')

@app.route('/del_account/<int:acc_id>')
def del_account(acc_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM accounts WHERE id = ?", (acc_id,))
    conn.commit()
    conn.close()
    return redirect('/')

@app.route('/fetch_all_groups')
def fetch_all_groups():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM accounts LIMIT 1")
    acc = c.fetchone()
    
    if not acc:
        add_log("[ERROR] ❌ No active account available to fetch groups!")
        conn.close()
        return redirect('/')

    try:
        add_log(f"[SYSTEM] 📡 Fetching group threads using account: {acc['username']}...")
        groups = fetch_all_groups_from_acc(acc['session_id'])
        if groups:
            groups_text = "\n".join(groups)
            c.execute("REPLACE INTO settings (key, value) VALUES ('target_ids', ?)", (groups_text,))
            conn.commit()
            add_log(f"[SUCCESS] ✅ Auto-fetched {len(groups)} group threads!")
        else:
            add_log("[WARN] ⚠️ No group chats found on this account.")
    except Exception as e:
        add_log(f"[ERROR] ❌ Failed to auto-fetch groups: {e}")

    conn.close()
    return redirect('/')

@app.route('/save_settings', methods=['POST'])
def save_settings():
    conn = get_db()
    c = conn.cursor()
    for key in ['target_ids', 'messages', 'titles', 'delay', 'rename_interval']:
        val = request.form.get(key, '')
        c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, val))
    conn.commit()
    conn.close()
    add_log("[SYSTEM] 💾 Engine configurations saved.")
    return redirect('/')

@app.route('/start')
def start_bot():
    global IS_RUNNING
    if not IS_RUNNING:
        IS_RUNNING = True
        t = threading.Thread(target=bot_worker)
        t.daemon = True
        t.start()
    return redirect('/')

@app.route('/stop')
def stop_bot():
    global IS_RUNNING
    IS_RUNNING = False
    return redirect('/')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)