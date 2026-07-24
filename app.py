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
        leave_type TEXT,
        leave_dates TEXT,
        reason TEXT,
        status INTEGER DEFAULT 0,
        requested_on TEXT
    )
    """)

    # ---------------- SAFE MIGRATION ----------------
    cur.execute("""
    ALTER TABLE leave_requests
    ADD COLUMN IF NOT EXISTS from_date DATE
    """)

    cur.execute("""
    ALTER TABLE leave_requests
    ADD COLUMN IF NOT EXISTS to_date DATE
    """)

    # ---------------- ACTIVITIES ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS activities (
        id SERIAL PRIMARY KEY,
        username TEXT,
        activity_date DATE,
        clock_in TIME,
        activity_name TEXT,
        start_time TIME,
        end_time TIME,
        duration INTEGER,
        clock_out TIME,
        submitted_at TIMESTAMP
    )
    """)


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
                "INSERT INTO users (username, password) VALUES (%s, %s)",
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
                SET password = %s, reset_requested = 0
                WHERE username = %s
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
        WHERE username = %s
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
        WHERE username = %s
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
                "INSERT INTO users (username, password) VALUES (%s, %s)",
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

    conn = get_db()
    cur = conn.cursor()

    # -------- Check Comp-Off Eligibility --------
    cur.execute("""
        SELECT COUNT(*) AS total
        FROM activities
        WHERE username = %s
          AND activity_name = 'Comp-Off Earned'
    """, (username,))

    result = cur.fetchone()
    is_comp_off_eligible = result["total"] > 0

    if request.method == "POST":

        leave_type = request.form.get("leave_type")
        reason = (request.form.get("reason") or "").strip()

        # Weekly Off & Holiday don't need reason
        if leave_type in ["weeklyoff", "holiday"]:
            reason = ""

        selected_dates = []

        # ---------------- Single Date Types ----------------
        if leave_type in [
            "single",
            "halfday",
            "compoff",
            "weeklyoff",
            "holiday"
        ]:

            d = request.form.get("single_date")

            if not d:
                flash("❌ Please select a date.")
                conn.close()
                return redirect("/leave")

            selected_dates = [d]
            dates_text = d

        # ---------------- Multiple Leave ----------------
        elif leave_type == "multiple":

            from_date = request.form.get("from_date")
            to_date = request.form.get("to_date")

            if not from_date or not to_date:
                flash("❌ Please select From and To dates.")
                conn.close()
                return redirect("/leave")

            d1 = datetime.strptime(from_date, "%Y-%m-%d")
            d2 = datetime.strptime(to_date, "%Y-%m-%d")

            if d2 < d1:
                flash("❌ To Date cannot be before From Date.")
                conn.close()
                return redirect("/leave")

            while d1 <= d2:
                selected_dates.append(d1.strftime("%Y-%m-%d"))
                d1 += timedelta(days=1)

            dates_text = f"{from_date} to {to_date}"

        else:
            flash("❌ Invalid Leave Type.")
            conn.close()
            return redirect("/leave")

        # ---------------- Duplicate Validation ----------------
        cur.execute("""
            SELECT leave_dates
            FROM leave_requests
            WHERE username=%s
              AND status IN (0,2)
        """, (username,))

        existing = cur.fetchall()

        for row in existing:

            booked = []
            txt = row["leave_dates"]

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
                flash("🚫 Leave already exists for selected date(s).")
                conn.close()
                return redirect("/leave")

        # ---------------- Auto Approval ----------------
        if leave_type in ["weeklyoff", "holiday"]:
            status = 2
        else:
            status = 0

        cur.execute("""
            INSERT INTO leave_requests
            (
                username,
                leave_type,
                leave_dates,
                status,
                requested_on,
                reason
            )
            VALUES
            (
                %s,%s,%s,%s,%s,%s
            )
        """, (
            username,
            leave_type,
            dates_text,
            status,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            reason
        ))

        conn.commit()

        flash("✅ Leave submitted successfully.")

    # ---------------- History ----------------
    cur.execute("""
        SELECT
            id,
            leave_type,
            leave_dates,
            reason,
            requested_on,
            status
        FROM leave_requests
        WHERE username=%s
        ORDER BY id DESC
    """, (username,))

    history = cur.fetchall()

    conn.close()

    return render_template(
        "leave.html",
        history=history,
        is_comp_off_eligible=is_comp_off_eligible
    )

# ------------------ CANCEL LEAVE ------------------
@app.route("/cancel-leave", methods=["POST"])
def cancel_leave():
    if "username" not in session:
        return redirect("/employee")

    leave_id = request.form.get("id")
    username = session["username"]

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT leave_type, status
        FROM leave_requests
        WHERE id=%s
          AND username=%s
    """, (leave_id, username))

    leave = cur.fetchone()

    if not leave:
        conn.close()
        flash("❌ Leave request not found.")
        return redirect("/leave")

    if leave["status"] not in (0, 2):
        conn.close()
        flash("❌ Only Pending or Approved leave can be cancelled.")
        return redirect("/leave")

    cur.execute("""
        UPDATE leave_requests
        SET status = 4
        WHERE id=%s
          AND username=%s
    """, (leave_id, username))

    conn.commit()
    conn.close()

    flash("🗑 Leave cancelled successfully.")
    return redirect("/leave")


# ------------------ MANAGER LEAVE REQUEST ------------------
@app.route("/manager/leave-requests")
def manager_leave_requests():
    if "manager" not in session:
        return redirect("/manager")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM leave_requests
        WHERE status = 0
        ORDER BY requested_on ASC
    """)

    requests = cur.fetchall()

    conn.close()

    return render_template(
        "manager_leave_requests.html",
        requests=requests
    )

# ------------------ MANAGER APPROVE / REJECT ------------------
@app.route("/manager/handle-leave", methods=["POST"])
def handle_leave():
    if "manager" not in session:
        return redirect("/manager")

    leave_id = request.form["id"]
    action = request.form["action"]

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT leave_type
        FROM leave_requests
        WHERE id = %s
    """, (leave_id,))

    leave = cur.fetchone()

    if not leave:
        conn.close()
        return redirect("/manager/leave-requests")

    leave_type = (leave["leave_type"] or "").lower()

    if action == "approve":

        # Weekly Off & Holiday are auto-approved.
        # If manager somehow receives them, approve directly.
        if leave_type in ["weeklyoff", "holiday"]:
            status = 2
        else:
            status = 2

    else:
        status = 3

    cur.execute("""
        UPDATE leave_requests
        SET status = %s
        WHERE id = %s
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

    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)

    today = now_ist.date()
    now_time = now_ist.replace(second=0, microsecond=0).time()

    conn = get_db()
    cur = conn.cursor()

    # ---------------- FIND LAST ALLOWED WORKING DATE ----------------

    leave_dates = set()

    cur.execute("""
        SELECT leave_dates
        FROM leave_requests
        WHERE username = %s
          AND status = 2
    """, (username,))

    approved = cur.fetchall()

    for row in approved:
        txt = row["leave_dates"]

        if "to" in txt:
            s, e = txt.split(" to ")
            d1 = datetime.strptime(s, "%Y-%m-%d").date()
            d2 = datetime.strptime(e, "%Y-%m-%d").date()

            while d1 <= d2:
                leave_dates.add(d1)
                d1 += timedelta(days=1)
        else:
            leave_dates.add(datetime.strptime(txt, "%Y-%m-%d").date())

    allowed_date = today - timedelta(days=1)

    while allowed_date in leave_dates:
        allowed_date -= timedelta(days=1)

    if request.method == "POST":

        activity_date = request.form.get("activity_date")
        clock_in = request.form.get("clock_in")
        clock_out = request.form.get("clock_out")

        activity_names = request.form.getlist("activity_name[]")
        start_times = request.form.getlist("start_time[]")
        end_times = request.form.getlist("end_time[]")

        selected_date = datetime.strptime(activity_date, "%Y-%m-%d").date()
        selected_str = selected_date.strftime("%Y-%m-%d")

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

        # ---------------- BLOCK APPROVED LEAVE ----------------
        cur.execute("""
            SELECT leave_dates
            FROM leave_requests
            WHERE username = %s
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
                    min_date=allowed_date.isoformat(),
                    form_data=form_data
                )

        # ---------------- BLOCK FUTURE / OLD DATE ----------------
        if selected_date > today:
            flash("⛔ Future date is not allowed.")
            conn.close()
            return render_template(
                "activity.html",
                selected=username,
                max_date=today.isoformat(),
                min_date=allowed_date.isoformat(),
                form_data=form_data
            )

        if selected_date < allowed_date:
            flash(f"⛔ You can submit only for {allowed_date.strftime('%d-%m-%Y')} or today.")
            conn.close()
            return render_template(
                "activity.html",
                selected=username,
                max_date=today.isoformat(),
                min_date=allowed_date.isoformat(),
                form_data=form_data
            )

        # ---------------- FIX CLOCK-IN (FIRST TIME ONLY) ----------------
        cur.execute("""
            SELECT clock_in
            FROM activities
            WHERE username = %s
              AND activity_date = %s
            ORDER BY submitted_at ASC
            LIMIT 1
        """, (username, activity_date))

        first_entry = cur.fetchone()

        if first_entry and first_entry["clock_in"]:
            clock_in = first_entry["clock_in"]

        # ---------------- FORCE CLOCK-OUT (LAST SUBMISSION WINS) ----------------
        cur.execute("""
            UPDATE activities
            SET clock_out = %s
            WHERE username = %s
              AND activity_date = %s
        """, (clock_out, username, activity_date))

        # ---------------- PROCESS ACTIVITIES ----------------
        for i in range(len(activity_names)):

            if not activity_names[i].strip():
                continue

            start_t = datetime.strptime(start_times[i], "%H:%M").time()
            end_t = datetime.strptime(end_times[i], "%H:%M").time()

            if end_t <= start_t:
                flash("⛔ End time must be after start time")
                conn.close()
                return render_template(
                    "activity.html",
                    selected=username,
                    max_date=today.isoformat(),
                    min_date=allowed_date.isoformat(),
                    form_data=form_data
                )

            if selected_date == today:
                if start_t > now_time or end_t > now_time:
                    flash("⛔ Activity start or end time cannot be in the future")
                    conn.close()
                    return render_template(
                        "activity.html",
                        selected=username,
                        max_date=today.isoformat(),
                        min_date=allowed_date.isoformat(),
                        form_data=form_data
                    )

            # ---------------- PREVENT OVERLAPPING ACTIVITIES ----------------
            cur.execute("""
                SELECT start_time, end_time
                FROM activities
                WHERE username = %s
                  AND activity_date = %s
            """, (username, activity_date))

            existing_rows = cur.fetchall()

            for existing in existing_rows:
                ex_start = datetime.combine(selected_date, existing["start_time"])
                ex_end = datetime.combine(selected_date, existing["end_time"])
                new_start = datetime.combine(selected_date, start_t)
                new_end = datetime.combine(selected_date, end_t)

                if new_start < ex_end and new_end > ex_start:
                    flash("⛔ Activity time overlaps with existing activity")
                    conn.close()
                    return render_template(
                        "activity.html",
                        selected=username,
                        max_date=today.isoformat(),
                        min_date=allowed_date.isoformat(),
                        form_data=form_data
                    )

            duration = int(
                (
                    datetime.combine(selected_date, end_t)
                    - datetime.combine(selected_date, start_t)
                ).total_seconds() / 60
            )

            cur.execute("""
                DELETE FROM activities
                WHERE username = %s
                  AND activity_date = %s
                  AND start_time = %s
                  AND end_time = %s
            """, (
                username,
                activity_date,
                start_times[i],
                end_times[i]
            ))

            cur.execute("""
                INSERT INTO activities (
                    username, activity_date, clock_in,
                    activity_name, start_time, end_time,
                    duration, clock_out, submitted_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                username,
                activity_date,
                clock_in,
                activity_names[i],
                start_times[i],
                end_times[i],
                duration,
                clock_out,
                datetime.now(pytz.timezone("Asia/Kolkata"))
            ))

        conn.commit()
        conn.close()

        return redirect("/success")

    return render_template(
        "activity.html",
        selected=username,
        max_date=today.isoformat(),
        min_date=allowed_date.isoformat(),
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

    from datetime import datetime, timedelta

    conn = get_db()
    cur = conn.cursor()

    # ---------------- FILTERS ----------------
    selected_month = request.args.get("month", datetime.now().strftime("%m"))
    selected_year = request.args.get("year", datetime.now().strftime("%Y"))
    years = ["2024", "2025", "2026"]

    # ---------------- RESET REQUEST COUNT ----------------
    cur.execute("SELECT COUNT(*) AS count FROM users WHERE reset_requested = 1")
    pending_count = cur.fetchone()["count"]

    # ---------------- LEAVE REQUEST COUNT ----------------
    cur.execute("SELECT COUNT(*) AS count FROM leave_requests WHERE status = 0")
    leave_pending_count = cur.fetchone()["count"]

    # ---------------- PRODUCTIVITY ----------------
    cur.execute("""
        SELECT
            username,
            SUM(duration) AS productive_minutes,
            COUNT(DISTINCT activity_date) AS days
        FROM activities
        WHERE TO_CHAR(activity_date, 'MM') = %s
          AND TO_CHAR(activity_date, 'YYYY') = %s
        GROUP BY username
    """, (selected_month, selected_year))

    activity_rows = cur.fetchall()

    # ---------------- APPROVED LEAVES ----------------
    cur.execute("""
        SELECT
            username,
            leave_type,
            leave_dates
        FROM leave_requests
        WHERE status = 2
    """)

    leave_rows = cur.fetchall()

    conn.close()

    # ---------------- LEAVE MAPS ----------------
    leave_map = {}
    compoff_map = {}
    weeklyoff_map = {}

    selected_month_int = int(selected_month)
    selected_year_int = int(selected_year)

    for r in leave_rows:

        user = r["username"]
        leave_type = (r["leave_type"] or "").strip().lower()
        dates = (r["leave_dates"] or "").strip()

        def process_day(current_date):

            value = 0.5 if leave_type == "halfday" else 1

            if leave_type == "compoff":
                compoff_map[user] = compoff_map.get(user, 0) + value

            elif leave_type in ["weekly off", "weeklyoff", "holiday"]:
                weeklyoff_map[user] = weeklyoff_map.get(user, 0) + value

            else:
                leave_map[user] = leave_map.get(user, 0) + value

        # -------- SINGLE DATE --------
        if "to" not in dates:

            try:
                d = datetime.strptime(dates, "%Y-%m-%d")

                if d.month == selected_month_int and d.year == selected_year_int:
                    process_day(d)

            except:
                pass

        # -------- DATE RANGE --------
        else:

            try:
                start, end = dates.split(" to ")

                current = datetime.strptime(start.strip(), "%Y-%m-%d")
                last = datetime.strptime(end.strip(), "%Y-%m-%d")

                while current <= last:

                    if (
                        current.month == selected_month_int
                        and current.year == selected_year_int
                    ):
                        process_day(current)

                    current += timedelta(days=1)

            except:
                pass

    # ---------------- FINAL DATA ----------------
    data = []

    total_productive_all = 0
    total_available_all = 0
    total_available_with_leave_all = 0

    for r in activity_rows:

        username = r["username"]

        productive_hours = round((r["productive_minutes"] or 0) / 60.0, 2)

        working_days = r["days"] or 0

        leave_days = leave_map.get(username, 0)

        compoff_days = compoff_map.get(username, 0)

        weeklyoff_days = weeklyoff_map.get(username, 0)

        half_days = 0

        if leave_days % 1 != 0:
            half_days = 1

        # ---------------- HOURS ----------------
        available_hours = working_days * 7

        # Only actual Leave affects productivity.
        # Weekly Off & Comp-Off are treated as Non Working Days.
        available_hours_with_leave = (working_days + leave_days) * 7

        ideal_hours = max(available_hours - productive_hours, 0)
        ideal_hours = round(ideal_hours, 2)

        productivity = (
            (productive_hours / available_hours) * 100
            if available_hours > 0 else 0
        )
        productivity = round(productivity, 1)

        productivity_with_leave = (
            (productive_hours / available_hours_with_leave) * 100
            if available_hours_with_leave > 0 else 0
        )
        productivity_with_leave = round(productivity_with_leave, 1)

        total_productive_all += productive_hours
        total_available_all += available_hours
        total_available_with_leave_all += available_hours_with_leave

        data.append({
            "name": username,
            "productive": productive_hours,
            "days": working_days,
            "available": available_hours,
            "available_with_leave": available_hours_with_leave,
            "ideal": ideal_hours,
            "productivity": productivity,
            "productivity_with_leave": productivity_with_leave,
            "leave_days": leave_days,
            "compoff_days": compoff_days,
            "weeklyoff_days": weeklyoff_days,
            "half_days": half_days
        })

    overall_productivity = (
        (total_productive_all / total_available_all) * 100
        if total_available_all > 0 else 0
    )
    overall_productivity = round(overall_productivity, 1)

    overall_productivity_with_leave = (
        (total_productive_all / total_available_with_leave_all) * 100
        if total_available_with_leave_all > 0 else 0
    )
    overall_productivity_with_leave = round(
        overall_productivity_with_leave,
        1
    )

    return render_template(
        "manager_dashboard.html",
        data=data,
        pending_count=pending_count,
        leave_pending_count=leave_pending_count,
        selected_month=selected_month,
        selected_year=selected_year,
        years=years,
        overall_productivity=overall_productivity,
        overall_productivity_with_leave=overall_productivity_with_leave
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
        WHERE username = %s
          AND TO_CHAR(activity_date, 'MM') = %s
          AND TO_CHAR(activity_date, 'YYYY') = %s
        ORDER BY activity_date, start_time
    """, (username, selected_month, selected_year))

    rows = cur.fetchall()
    conn.close()

    grouped = {}
    for r in rows:
        date_key = r["activity_date"]

        formatted_submitted = ""
        if r["submitted_at"]:
            try:
                formatted_submitted = r["submitted_at"].strftime("%d %b %Y • %I:%M %p")
            except:
                formatted_submitted = str(r["submitted_at"])

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
        WHERE username = %s
          AND TO_CHAR(activity_date, 'MM') = %s
          AND TO_CHAR(activity_date, 'YYYY') = %s
        ORDER BY activity_date, start_time
    """, (username, month, year))

    rows = cur.fetchall()
    conn.close()

    month_name = datetime.strptime(month, "%m").strftime("%B")
    filename = f"{username}_{month_name}_{year}_Activities.pdf"

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30
    )

    elements = []
    styles = getSampleStyleSheet()

    # Header
    elements.append(Paragraph(f"<b>Employee Activity Report</b>", styles["Heading1"]))
    elements.append(Spacer(1, 0.3 * inch))
    elements.append(Paragraph(f"<b>Employee:</b> {username}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Month:</b> {month_name} {year}", styles["Normal"]))
    elements.append(Spacer(1, 0.4 * inch))

    # Table header
    data = [[
        "Date",
        "Activity",
        "Start",
        "End",
        "Submitted"
    ]]

    for r in rows:

        activity_date = r["activity_date"].strftime("%d-%b-%Y") if r["activity_date"] else "-"
        start = r["start_time"].strftime("%H:%M") if r["start_time"] else "-"
        end = r["end_time"].strftime("%H:%M") if r["end_time"] else "-"

        if r["submitted_at"]:
            try:
                submitted = r["submitted_at"].strftime("%d-%b-%Y %I:%M %p")
            except:
                submitted = str(r["submitted_at"])
        else:
            submitted = "-"

        data.append([
            activity_date,
            Paragraph(r["activity_name"], styles["Normal"]),
            start,
            end,
            submitted
        ])

    table = Table(
        data,
        repeatRows=1,
        colWidths=[80, 200, 60, 60, 100]  # FIXED WIDTHS
    )

    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ALIGN', (2, 1), (3, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
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

    # -------- FETCH ALL ACTIVITIES --------
    cur.execute("""
        SELECT activity_date, activity_name, start_time, end_time,
               duration, clock_in, clock_out
        FROM activities
        WHERE username = %s
    """, (username,))
    rows = cur.fetchall()

    daily_minutes = {}
    daily_clock = {}
    available_years = set()

    for row in rows:

        try:
            d = row["activity_date"]
        except:
            continue

        available_years.add(d.year)

        if d.month == int(selected_month) and d.year == int(selected_year):

            daily_minutes.setdefault(d, 0)
            daily_minutes[d] += row["duration"]

            if d not in daily_clock:
                daily_clock[d] = {
                    "clock_in": row["clock_in"],
                    "clock_out": row["clock_out"]
                }
            else:
                if not daily_clock[d]["clock_in"] and row["clock_in"]:
                    daily_clock[d]["clock_in"] = row["clock_in"]

                if row["clock_out"]:
                    daily_clock[d]["clock_out"] = row["clock_out"]

    # -------- FETCH APPROVED LEAVES --------
    cur.execute("""
        SELECT leave_dates, leave_type
        FROM leave_requests
        WHERE username = %s
          AND status = 2
    """, (username,))
    leave_rows = cur.fetchall()

    leave_dates_dict = {}

    leave_count = 0
    non_working_days = 0

    for r in leave_rows:

        txt = (r["leave_dates"] or "").strip()
        leave_type = (r["leave_type"] or "").strip().lower()

        if leave_type == "weeklyoff":
            display_text = "Weekly Off"
        elif leave_type == "weekly off":
            display_text = "Weekly Off"
        elif leave_type == "holiday":
            display_text = "Holiday"
        elif leave_type == "compoff":
            display_text = "Comp-Off"
        elif leave_type == "halfday":
            display_text = "Half Day"
        else:
            display_text = "Leave"

        def process_day(d):

            nonlocal leave_count, non_working_days

            if d.month != int(selected_month) or d.year != int(selected_year):
                return

            leave_dates_dict[d] = display_text

            if leave_type in ["weeklyoff", "weekly off", "holiday", "compoff"]:
                non_working_days += 1
            else:
                leave_count += 1

        if "to" in txt:

            s, e = txt.split(" to ")

            d1 = datetime.strptime(s.strip(), "%Y-%m-%d").date()
            d2 = datetime.strptime(e.strip(), "%Y-%m-%d").date()

            while d1 <= d2:
                process_day(d1)
                d1 += timedelta(days=1)

        else:

            d = datetime.strptime(txt, "%Y-%m-%d").date()
            process_day(d)

    # -------- BUILD REPORT TABLE --------
    report_data = []

    total_minutes = 0

    all_dates = set(daily_minutes.keys()) | set(leave_dates_dict.keys())

    for d in sorted(all_dates):

        if d in leave_dates_dict:

            report_data.append({
                "date": d,
                "time": leave_dates_dict[d],
                "productivity": "-",
                "clock_in": "-",
                "clock_out": "-"
            })

        else:

            mins = daily_minutes.get(d, 0)

            hrs = mins // 60
            rem = mins % 60

            productivity_day = (mins / (7 * 60)) * 100 if mins > 0 else 0

            report_data.append({
                "date": d,
                "time": f"{hrs} hours {rem} min",
                "productivity": f"{productivity_day:.2f}%",
                "clock_in": daily_clock.get(d, {}).get("clock_in"),
                "clock_out": daily_clock.get(d, {}).get("clock_out")
            })

            total_minutes += mins

    # -------- CARDS --------
    productive_hours = total_minutes / 60

    working_days = len(daily_minutes)

    available_hours = working_days * 7

    available_hours_with_leave = (working_days + leave_count) * 7

    idle_hours = max(available_hours - productive_hours, 0)

    productivity = (
        (productive_hours / available_hours_with_leave) * 100
        if available_hours_with_leave > 0 else 0
    )
    non_working_count = non_working_days

    cards = {
        "productive": f"{int(productive_hours)} hrs {int((productive_hours % 1) * 60)} min",
        "working_days": working_days,
        "available": f"{available_hours_with_leave} hrs",
        "idle": f"{int(idle_hours)} hrs {int((idle_hours % 1) * 60)} min",
        "productivity": f"{productivity:.2f}%",
        "leaves": leave_count,
        "non_working": non_working_count
    }

    # -------- SELECTED DAY ACTIVITIES --------
    day_activities = []

    if selected_day:
        cur.execute("""
            SELECT activity_name, start_time, end_time, duration
            FROM activities
            WHERE username = %s
              AND activity_date = %s
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
