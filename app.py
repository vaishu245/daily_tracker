from flask import Flask, render_template, request, redirect, session, flash
from datetime import datetime, date
import pytz
import psycopg2
import psycopg2.extras
from datetime import timedelta

from flask import send_file
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.pagesizes import A4
import os
from io import BytesIO

app = Flask(__name__)
app.secret_key = "daily_tracker_secret"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "daily_tracker.db")

# ------------------ DATABASE HELPERS ------------------
def get_db():
    conn = psycopg2.connect(
        os.environ.get("DATABASE_URL"),
        cursor_factory=psycopg2.extras.RealDictCursor
    )
    return conn


def column_exists(table, column):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    exists = any(row["name"] == column for row in cur.fetchall())
    conn.close()
    return exists

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # ---------------- USERS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT,
        reset_requested INTEGER DEFAULT 0
    )
    """)
# ---------------- LEAVE REQUESTS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS leave_requests (
        id SERIAL PRIMARY KEY,
        username TEXT,
        leave_type TEXT,          -- 'single' or 'multiple'
        leave_dates TEXT,         -- comma-separated dates
        status INTEGER DEFAULT 0, -- 0=pending, 2=approved, 3=rejected
        requested_on TEXT
    )
    """)
    cur.execute("PRAGMA table_info(leave_requests)")
    columns = [column[1] for column in cur.fetchall()]

    if "reason" not in columns:
        cur.execute("ALTER TABLE leave_requests ADD COLUMN reason TEXT")


    # ---------------- ACTIVITIES ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS activities (
        id SERIAL PRIMARY KEY,
        username TEXT,
        activity_date TEXT,
        clock_in TEXT,
        activity_name TEXT,
        start_time TEXT,
        end_time TEXT,
        duration INTEGER,
        clock_out TEXT
    )
    """)

    cur.execute("PRAGMA table_info(activities)")
    columns = [column[1] for column in cur.fetchall()]

    if "submitted_at" not in columns:
        cur.execute("ALTER TABLE activities ADD COLUMN submitted_at TEXT")


    # 🔑 IMPORTANT: Index for fast replacement check
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_activity_unique
    ON activities(username, activity_date, start_time, end_time)
    """)

    conn.commit()
    conn.close()

init_db()

# ------------------ WELCOME PAGE ------------------
@app.route("/")
def welcome():
    return render_template("welcome.html")

# ------------------ EMPLOYEE LOGIN ------------------
@app.route("/employee", methods=["GET", "POST"])
def employee_login():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT username, password, reset_requested FROM users")
    users = {
        row["username"]: {
            "password": row["password"],
            "reset_requested": row["reset_requested"]
        }
        for row in cur.fetchall()
    }
    conn.close()

    # Step 1: Username selection
    if request.method == "POST" and "username" in request.form and "password" not in request.form:
        username = request.form.get("username")
        session["temp_user"] = username

        if username in users:
            if users[username]["reset_requested"] == 1:
                return render_template("index.html", step="pending")

            if users[username]["reset_requested"] == 2:
                return render_template("index.html", step="create")

            return render_template("index.html", step="password")

        else:
            return render_template("index.html", step="create")

    # Step 2: Password create/login/reset
    if request.method == "POST" and "password" in request.form:
        username = session.get("temp_user")
        password = request.form.get("password")

        # New user
        if username not in users:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username, password)
            )
            conn.commit()
            conn.close()

            session["username"] = username
            session.pop("temp_user")
            return redirect("/dashboard")

        # Reset approved
        if users[username]["reset_requested"] == 2:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("""
                UPDATE users
                SET password = ?, reset_requested = 0
                WHERE username = ?
            """, (password, username))
            conn.commit()
            conn.close()

            session["username"] = username
            session.pop("temp_user")
            return redirect("/dashboard")

        # Normal login
        if users[username]["password"] == password:
            session["username"] = username
            session.pop("temp_user")
            return redirect("/dashboard")

        flash("Wrong password")
        return render_template("index.html", step="password")

    return render_template("index.html")

# ------------------ PASSWORD RESET REQUEST ------------------
@app.route("/request-reset", methods=["POST"])
def request_reset():
    username = session.get("temp_user")

    if not username:
        return "", 403

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET reset_requested = 1
        WHERE username = ?
    """, (username,))
    conn.commit()
    conn.close()

    flash("Reset request sent to manager")
    return "", 204

# ------------------ MANAGER RESET REQUESTS ------------------
@app.route("/manager/reset-requests")
def manager_reset_requests():
    if not session.get("manager"):
        return redirect("/manager")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT username
        FROM users
        WHERE reset_requested = 1
    """)
    requests = cur.fetchall()
    conn.close()

    return render_template("manager_reset_requests.html", requests=requests)

# ------------------ MANAGER APPROVES RESET ------------------
@app.route("/manager/approve-reset", methods=["POST"])
def manager_approve_reset():
    if not session.get("manager"):
        return redirect("/manager")

    username = request.form["username"]

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET reset_requested = 2,
            password = NULL
        WHERE username = ?
    """, (username,))
    conn.commit()
    conn.close()

    return redirect("/manager/reset-requests")

# ------------------ MANAGER LOGIN ------------------
@app.route("/manager", methods=["GET", "POST"])
def manager_login():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT username, password FROM users")
    users = {row["username"]: row["password"] for row in cur.fetchall()}
    conn.close()

    if request.method == "POST" and "manager_name" in request.form and "password" not in request.form:
        manager_name = request.form.get("manager_name")
        username = f"manager_{manager_name}"
        session["temp_manager"] = username

        if username in users:
            return render_template("manager_login.html", step="password")
        return render_template("manager_login.html", step="create")

    if request.method == "POST" and "password" in request.form:
        username = session.get("temp_manager")
        password = request.form.get("password")

        if username not in users:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username, password)
            )
            conn.commit()
            conn.close()

            session["manager"] = username
            session.pop("temp_manager")
            return redirect("/manager/dashboard")

        if users[username] == password:
            session["manager"] = username
            session.pop("temp_manager")
            return redirect("/manager/dashboard")

        flash("Wrong password")
        return render_template("manager_login.html", step="password")

    return render_template("manager_login.html")

# ------------------ DASHBOARD ------------------
@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect("/employee")
    return render_template("dashboard.html", name=session["username"])


# ------------------ LEAVE REQUEST ------------------
@app.route("/leave", methods=["GET", "POST"])
def leave():
    if "username" not in session:
        return redirect("/employee")

    username = session["username"]

    if request.method == "POST":
        leave_type = request.form.get("leave_type")
        reason = request.form.get("reason")
        selected_dates = []

        if leave_type == "single":
            d = request.form.get("single_date")
            selected_dates = [d]
            dates_text = d
        else:
            from_date = request.form.get("from_date")
            to_date = request.form.get("to_date")

            d1 = datetime.strptime(from_date, "%Y-%m-%d")
            d2 = datetime.strptime(to_date, "%Y-%m-%d")

            if d2 < d1:
                flash("❌ To date cannot be before From date")
                return redirect("/leave")

            while d1 <= d2:
                selected_dates.append(d1.strftime("%Y-%m-%d"))
                d1 += timedelta(days=1)

            dates_text = f"{from_date} to {to_date}"

        conn = get_db()
        cur = conn.cursor()

        # Ignore rejected (3) and cancelled (4)
        cur.execute("""
            SELECT leave_dates
            FROM leave_requests
            WHERE username = ?
              AND status IN (0,2)
        """, (username,))

        existing = cur.fetchall()

        for r in existing:
            txt = r["leave_dates"]
            booked = []

            if "to" in txt:
                s, e = txt.split(" to ")
                d1 = datetime.strptime(s, "%Y-%m-%d")
                d2 = datetime.strptime(e, "%Y-%m-%d")
                while d1 <= d2:
                    booked.append(d1.strftime("%Y-%m-%d"))
                    d1 += timedelta(days=1)
            else:
                booked.append(txt)

            if set(booked) & set(selected_dates):
                flash("🚫 You already applied leave for these date(s).")
                conn.close()
                return redirect("/leave")

        cur.execute("""
            INSERT INTO leave_requests
            (username, leave_type, leave_dates, status, requested_on, reason)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            username,
    		leave_type,
            dates_text,
    		0,  # status
    		datetime.now().strftime("%Y-%m-%d %H:%M"),
    		reason
        ))

        conn.commit()
        conn.close()

        flash("✅ Leave request sent successfully")
        return redirect("/leave")

    # ---- history ----
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, leave_dates, status, requested_on, reason
        FROM leave_requests
        WHERE username = ?
        ORDER BY id DESC
    """, (username,))
    history = cur.fetchall()
    conn.close()

    return render_template("leave.html", history=history)

# ------------------ CANCEL LEAVE ------------------
@app.route("/cancel-leave", methods=["POST"])
def cancel_leave():
    if "username" not in session:
        return redirect("/employee")

    leave_id = request.form.get("id")
    username = session["username"]

    conn = get_db()
    cur = conn.cursor()

    # Allow cancel if Pending OR Approved
    cur.execute("""
        UPDATE leave_requests
        SET status = 4
        WHERE id = ?
          AND username = ?
          AND status IN (0,2)
    """, (leave_id, username))

    conn.commit()
    conn.close()

    flash("🗑 Leave cancelled successfully")
    return redirect("/leave")

# ------------------ MANAGER LEAVE REQUEST ------------------
@app.route("/manager/leave-requests")
def manager_leave_requests():
    if "manager" not in session:
        return redirect("/manager")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM leave_requests WHERE status = 0
    """)
    requests = cur.fetchall()
    conn.close()

    return render_template("manager_leave_requests.html", requests=requests)

# ------------------ MANAGER APPROVE / REJECT ------------------
@app.route("/manager/handle-leave", methods=["POST"])
def handle_leave():
    if "manager" not in session:
        return redirect("/manager")

    leave_id = request.form["id"]
    action = request.form["action"]

    status = 2 if action == "approve" else 3

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE leave_requests
        SET status = ?
        WHERE id = ?
    """, (status, leave_id))
    conn.commit()
    conn.close()

    return redirect("/manager/leave-requests")

# ------------------ ACTIVITY ------------------
@app.route("/activity", methods=["GET", "POST"])
def activity():
    if "username" not in session:
        return redirect("/employee")

    username = session["username"]

    if request.method == "POST":
        activity_date = request.form.get("activity_date")
        clock_in = request.form.get("clock_in")
        clock_out = request.form.get("clock_out")

        activity_names = request.form.getlist("activity_name[]")
        start_times = request.form.getlist("start_time[]")
        end_times = request.form.getlist("end_time[]")


        ist = pytz.timezone("Asia/Kolkata")
        now_ist = datetime.now(ist)

        today = now_ist.date()
        now_time = now_ist.replace(second=0, microsecond=0).time()

        selected_date = datetime.strptime(activity_date, "%Y-%m-%d").date()
        selected_str = selected_date.strftime("%Y-%m-%d")

        # Prepare form data to send back if validation fails
        form_data = {
            "activity_date": activity_date,
            "clock_in": clock_in,
            "clock_out": clock_out,
            "activities": []
        }

        for i in range(len(activity_names)):
            form_data["activities"].append({
                "name": activity_names[i],
                "start": start_times[i],
                "end": end_times[i]
            })

        conn = get_db()
        cur = conn.cursor()

        # 🚫 BLOCK APPROVED LEAVE DATE
        cur.execute("""
            SELECT leave_dates
            FROM leave_requests
            WHERE username = ?
              AND status = 2
        """, (username,))
        approved_leaves = cur.fetchall()

        for r in approved_leaves:
            txt = r["leave_dates"]
            leave_days = []

            if "to" in txt:
                s, e = txt.split(" to ")
                d1 = datetime.strptime(s, "%Y-%m-%d")
                d2 = datetime.strptime(e, "%Y-%m-%d")
                while d1 <= d2:
                    leave_days.append(d1.strftime("%Y-%m-%d"))
                    d1 += timedelta(days=1)
            else:
                leave_days.append(txt)

            if selected_str in leave_days:
                flash("⛔ You are on approved leave for this date. Activity not allowed.")
                conn.close()
                return render_template(
                    "activity.html",
                    selected=username,
                    max_date=today.isoformat(),
                    form_data=form_data
                )

        # 🚫 BLOCK FUTURE DATE
        if selected_date > today:
            flash("⛔ You cannot submit activity for future date")
            conn.close()
            return render_template(
                "activity.html",
                selected=username,
                max_date=today.isoformat(),
                form_data=form_data
            )

        # 🔁 PROCESS ACTIVITIES
        for i in range(len(activity_names)):

            if not activity_names[i].strip():
                continue

            start_t = datetime.strptime(start_times[i], "%H:%M").time()
            end_t = datetime.strptime(end_times[i], "%H:%M").time()

            # 🚫 End must be after start
            if end_t <= start_t:
                flash("⛔ End time must be after start time")
                conn.close()
                return render_template(
                    "activity.html",
                    selected=username,
                    max_date=today.isoformat(),
                    form_data=form_data
                )

            # 🚫 FUTURE ACTIVITY TIME CHECK (ONLY FOR TODAY)
            if selected_date == today:
                if start_t > now_time or end_t > now_time:
                    flash("⛔ Activity start or end time cannot be in the future")
                    conn.close()
                    return render_template(
                        "activity.html",
                        selected=username,
                        max_date=today.isoformat(),
                        form_data=form_data
                    )

            duration = int(
                (
                    datetime.combine(selected_date, end_t)
                    - datetime.combine(selected_date, start_t)
                ).total_seconds() / 60
            )

            # Delete same slot if exists
            cur.execute("""
                DELETE FROM activities
                WHERE username = ?
                AND activity_date = ?
                AND start_time = ?
                AND end_time = ?
            """, (
                username,
                activity_date,
                start_times[i],
                end_times[i]
            ))

            # Insert activity
            cur.execute("""
                INSERT INTO activities (
                    username, activity_date, clock_in,
                    activity_name, start_time, end_time,
                    duration, clock_out, submitted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                username,
                activity_date,
                clock_in,
                activity_names[i],
                start_times[i],
                end_times[i],
                duration,
                clock_out,
                datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
            ))

        conn.commit()
        conn.close()

        return redirect("/success")

    return render_template(
        "activity.html",
        selected=username,
        max_date=date.today().isoformat(),
        form_data=None
    )

# ------------------ SUCCESS ------------------
@app.route("/success")
def success():
    if "username" not in session:
        return redirect("/employee")
    return render_template("success.html")

# ------------------ LOGOUT ------------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ------------------ MANAGER DASHBOARD ------------------
@app.route("/manager/dashboard")
def manager_dashboard():
    if "manager" not in session:
        return redirect("/manager")

    from datetime import datetime

    conn = get_db()
    cur = conn.cursor()

    # ---------------- FILTERS ----------------
    selected_month = request.args.get("month", datetime.now().strftime("%m"))
    selected_year = request.args.get("year", datetime.now().strftime("%Y"))
    years = ["2024", "2025", "2026"]

    # ---------------- RESET REQUEST COUNT ----------------
    cur.execute("SELECT COUNT(*) FROM users WHERE reset_requested = 1")
    pending_count = cur.fetchone()[0]

    # ---------------- LEAVE REQUEST COUNT ----------------
    cur.execute("SELECT COUNT(*) FROM leave_requests WHERE status = 0")
    leave_pending_count = cur.fetchone()[0]

    # ---------------- PRODUCTIVITY ----------------
    cur.execute("""
        SELECT
            username,
            SUM(duration) AS productive_minutes,
            COUNT(DISTINCT activity_date) AS days
        FROM activities
        WHERE strftime('%m', activity_date) = ?
          AND strftime('%Y', activity_date) = ?
        GROUP BY username
    """, (selected_month, selected_year))

    activity_rows = cur.fetchall()

    # ---------------- APPROVED LEAVES ----------------
    cur.execute("""
        SELECT username, leave_type, leave_dates
        FROM leave_requests
        WHERE status = 2
    """)
    leave_rows = cur.fetchall()

    conn.close()

    # -------- CALCULATE LEAVES PER USER --------
    leave_map = {}

    for r in leave_rows:
        user = r["username"]
        dates = r["leave_dates"]

        if "to" in dates:
            start, end = dates.split(" to ")
            d1 = datetime.strptime(start, "%Y-%m-%d")
            d2 = datetime.strptime(end, "%Y-%m-%d")
            days = (d2 - d1).days + 1
        else:
            days = 1

        leave_map[user] = leave_map.get(user, 0) + days

    # -------- FINAL DATA BUILD --------
    data = []
    total_leave_percent = []

    # 🔥 NEW: totals for overall calculation
    total_productive_all = 0
    total_available_all = 0

    for r in activity_rows:
        username = r["username"]

        productive_hours = (r["productive_minutes"] or 0) / 60.0
        productive_hours = round(productive_hours, 2)

        working_days = r["days"] or 0
        available_hours = working_days * 7

        ideal_hours = available_hours - productive_hours
        if ideal_hours < 0:
            ideal_hours = 0
        ideal_hours = round(ideal_hours, 2)

        productivity = (
            (productive_hours / available_hours) * 100
        ) if available_hours > 0 else 0
        productivity = round(productivity, 1)

        leave_days = leave_map.get(username, 0)

        leave_percent = (
            (leave_days / working_days) * 100
        ) if working_days > 0 else 0
        leave_percent = round(leave_percent, 1)

        total_leave_percent.append(leave_percent)

        # 🔥 accumulate totals for overall
        total_productive_all += productive_hours
        total_available_all += available_hours

        data.append({
            "name": username,
            "productive": productive_hours,
            "days": working_days,
            "available": available_hours,
            "ideal": ideal_hours,
            "productivity": productivity,
            "leave_days": leave_days,
            "leave_percent": leave_percent
        })

    # ---------------- OVERALL PRODUCTIVITY (FIXED) ----------------
    overall_productivity = (
        (total_productive_all / total_available_all) * 100
    ) if total_available_all > 0 else 0
    overall_productivity = round(overall_productivity, 1)

    # ---------------- AVG LEAVE % ----------------
    avg_leave_percent = round(
        sum(total_leave_percent) / (len(total_leave_percent) or 1), 1
    )

    return render_template(
        "manager_dashboard.html",
        data=data,
        pending_count=pending_count,
        leave_pending_count=leave_pending_count,
        selected_month=selected_month,
        selected_year=selected_year,
        years=years,
        avg_leave_percent=avg_leave_percent,
        overall_productivity=overall_productivity  # 🔥 NEW
    )

# ------------------ MANAGER EMPLOYEE DETAIL ------------------
@app.route("/manager/employee/<username>")
def manager_employee_detail(username):
    if "manager" not in session:
        return redirect("/manager")

    selected_month = request.args.get("month")
    selected_year = request.args.get("year")

    today = date.today()
    if not selected_month:
        selected_month = f"{today.month:02d}"
    if not selected_year:
        selected_year = str(today.year)

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT activity_date, activity_name, start_time, end_time, submitted_at
        FROM activities
        WHERE username = ?
          AND strftime('%m', activity_date) = ?
          AND strftime('%Y', activity_date) = ?
        ORDER BY activity_date, start_time
    """, (username, selected_month, selected_year))

    rows = cur.fetchall()
    conn.close()

    # ---- Group by date ----
    grouped = {}
    for r in rows:
        date_key = r["activity_date"]

        formatted_submitted = ""
        if r["submitted_at"]:
            try:
                dt = datetime.strptime(r["submitted_at"], "%Y-%m-%d %H:%M:%S")
                formatted_submitted = dt.strftime("%d %b %Y • %I:%M %p")
            except:
                formatted_submitted = r["submitted_at"]

        grouped.setdefault(date_key, []).append({
            "activity": r["activity_name"],
            "start": r["start_time"],
            "end": r["end_time"],
            "submitted": formatted_submitted
        })

    return render_template(
        "manager_employee_report.html",
        username=username,
        grouped=grouped,
        selected_month=selected_month,
        selected_year=selected_year
    )

# ------------------ EXPORT EMPLOYEE PDF ------------------
@app.route("/manager/export_pdf/<username>")
def export_employee_pdf(username):

    if "manager" not in session:
        return redirect("/manager")

    month = request.args.get("month")
    year = request.args.get("year")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT activity_date, activity_name,
               start_time, end_time, submitted_at
        FROM activities
        WHERE username = ?
          AND strftime('%m', activity_date) = ?
          AND strftime('%Y', activity_date) = ?
        ORDER BY activity_date, start_time
    """, (username, month, year))

    rows = cur.fetchall()
    conn.close()

    month_name = datetime.strptime(month, "%m").strftime("%B")
    filename = f"{username}_{month_name}_{year}_Activities.pdf"

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph(f"Employee: {username}", styles["Heading1"]))
    elements.append(Spacer(1, 0.3 * inch))
    elements.append(Paragraph(f"Month: {month_name} {year}", styles["Heading2"]))
    elements.append(Spacer(1, 0.3 * inch))

    data = [["Date", "Activity", "Start", "End", "Submitted"]]

    for r in rows:
        data.append([
            r["activity_date"],
            r["activity_name"],
            r["start_time"],
            r["end_time"],
            r["submitted_at"] or "-"
        ])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
    ]))

    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf"
    )


# ------------------ REPORT ------------------
@app.route("/report")
def report():
    if "username" not in session:
        return redirect("/employee")

    username = session["username"]

    selected_month = request.args.get("month")
    selected_year = request.args.get("year")
    selected_day = request.args.get("day")

    today = date.today()
    if not selected_month:
        selected_month = f"{today.month:02d}"
    if not selected_year:
        selected_year = str(today.year)

    conn = get_db()
    cur = conn.cursor()

    # --- fetch all activities ---
    cur.execute("""
        SELECT activity_date, activity_name, start_time, end_time, duration
        FROM activities
        WHERE username = ?
    """, (username,))
    rows = cur.fetchall()

    daily_minutes = {}
    available_years = set()

    for row in rows:
        try:
            d = datetime.strptime(row["activity_date"], "%Y-%m-%d")
        except:
            continue

        available_years.add(d.year)

        if d.month == int(selected_month) and d.year == int(selected_year):
            daily_minutes.setdefault(row["activity_date"], 0)
            daily_minutes[row["activity_date"]] += row["duration"]

    # --- fetch approved leaves ---
    cur.execute("""
        SELECT leave_dates
        FROM leave_requests
        WHERE username = ?
          AND status = 2
    """, (username,))
    leave_rows = cur.fetchall()

    leave_dates_set = set()

    for r in leave_rows:
        txt = r["leave_dates"]

        if "to" in txt:
            s, e = txt.split(" to ")
            d1 = datetime.strptime(s, "%Y-%m-%d")
            d2 = datetime.strptime(e, "%Y-%m-%d")
            while d1 <= d2:
                if d1.month == int(selected_month) and d1.year == int(selected_year):
                    leave_dates_set.add(d1.strftime("%Y-%m-%d"))
                d1 += timedelta(days=1)
        else:
            d = datetime.strptime(txt, "%Y-%m-%d")
            if d.month == int(selected_month) and d.year == int(selected_year):
                leave_dates_set.add(txt)

    # --- build report table ---
    report_data = []
    total_minutes = 0
    leave_count = len(leave_dates_set)

    all_dates = set(daily_minutes.keys()) | leave_dates_set

    for d in sorted(all_dates):
        if d in leave_dates_set:
            report_data.append({
                "date": d,
                "time": "Leave",
                "productivity": "-"
            })
        else:
            mins = daily_minutes.get(d, 0)
            hrs = mins // 60
            rem = mins % 60

            productivity_day = (mins / (7 * 60)) * 100 if mins > 0 else 0

            report_data.append({
                "date": d,
                "time": f"{hrs} hours {rem} min",
                "productivity": f"{productivity_day:.2f}%"
            })

            total_minutes += mins

    # --- cards calculations ---
    productive_hours = total_minutes / 60
    working_days = len(daily_minutes)
    available_hours = working_days * 7
    idle_hours = max(available_hours - productive_hours, 0)

    productivity = (
        (productive_hours / available_hours) * 100
        if available_hours > 0 else 0
    )

    cards = {
        "productive": f"{int(productive_hours)} hrs {int((productive_hours % 1) * 60)} min",
        "working_days": working_days,
        "available": f"{available_hours} hrs",
        "idle": f"{int(idle_hours)} hrs {int((idle_hours % 1) * 60)} min",
        "productivity": f"{productivity:.2f}%",
        "leaves": leave_count
    }

    # --- selected day activities ---
    day_activities = []
    if selected_day:
        cur.execute("""
            SELECT activity_name, start_time, end_time, duration
            FROM activities
            WHERE username = ?
              AND activity_date = ?
            ORDER BY start_time
        """, (username, selected_day))
        day_activities = cur.fetchall()

    conn.close()

    return render_template(
        "report.html",
        data=report_data,
        cards=cards,
        name=username,
        selected_month=selected_month,
        selected_year=selected_year,
        selected_day=selected_day,
        day_activities=day_activities,
        years=sorted(available_years)
    )


# ------------------ RUN APP ------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
