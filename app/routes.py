from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, send_from_directory
import os
from werkzeug.utils import secure_filename

from .db import get_db, now_iso
from .auth import hash_password, verify_password, current_user, login_required, roles_required

bp = Blueprint("main", __name__)

# Application statuses
APP_PENDING = "на рассмотрении"
APP_APPROVED = "подтверждена"
APP_REJECTED = "отклонена"


# --- Audit logging (admin/organizer actions) ---
def audit_log(action: str, target: str = "", extra: str = "") -> None:
    """Append an audit line to data/audit.log. Best-effort, never raises."""
    try:
        u = current_user()
        actor = f"{u['id']}:{u['username']}:{u['role']}" if u else "anon"
        db_path = current_app.config.get("DB_PATH", "")
        data_dir = os.path.dirname(db_path) if db_path else os.path.join(os.path.dirname(__file__), "..", "data")
        os.makedirs(data_dir, exist_ok=True)
        log_path = os.path.join(data_dir, "audit.log")
        line = f"{now_iso()}\t{actor}\t{action}\t{target}\t{extra}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        return

def _is_manager_for_item(item_row):
    """Admin can manage everything; organizer can manage only items they created."""
    u = current_user()
    if not u or not item_row:
        return False
    if u["role"] == "admin":
        return True
    if u["role"] != "organizer":
        return False
    # sqlite3.Row has no .get(); use keys()
    try:
        keys = item_row.keys()
    except Exception:
        keys = []
    if "created_by" not in keys:
        return False
    return int(item_row["created_by"] or 0) == int(u["id"])

def _active_app_count(db, kind: str, item_id: int) -> int:
    """Counts applications excluding rejected ones."""
    if kind == "event":
        row = db.execute(
            "SELECT COUNT(1) c FROM event_applications WHERE event_id=? AND status IN (?, ?)",
            (item_id, APP_PENDING, APP_APPROVED),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT COUNT(1) c FROM task_applications WHERE task_id=? AND status IN (?, ?)",
            (item_id, APP_PENDING, APP_APPROVED),
        ).fetchone()
    return int(row["c"] or 0)

def _upload_dir():
    p = os.path.join(os.path.dirname(current_app.config["DB_PATH"]), "uploads")
    os.makedirs(p, exist_ok=True)
    return p

@bp.context_processor
def inject_user():
    return {"current_user": current_user()}

@bp.route("/")
def index():
    db = get_db()
    events = db.execute(
        "SELECT e.*, (SELECT COUNT(1) FROM event_applications a WHERE a.event_id=e.id AND a.status IN (?, ?)) as appl_count FROM events e ORDER BY e.start_time IS NULL, e.start_time DESC, e.id DESC LIMIT 20",
        (APP_PENDING, APP_APPROVED),
    ).fetchall()
    tasks = db.execute(
        "SELECT t.*, (SELECT COUNT(1) FROM task_applications a WHERE a.task_id=t.id AND a.status IN (?, ?)) as appl_count FROM tasks t ORDER BY t.start_time IS NULL, t.start_time DESC, t.id DESC LIMIT 20",
        (APP_PENDING, APP_APPROVED),
    ).fetchall()
    return render_template("index.html", events=events, tasks=tasks)



@bp.route("/about")
def about():
    return render_template("about.html")



@bp.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        role = request.form.get("role") or "volunteer"
        if role not in ("volunteer","organizer"):
            role = "volunteer"
        if len(username) < 3 or len(password) < 4:
            flash("Введите логин (>=3 символа) и пароль (>=4 символа).", "error")
            return render_template("register.html")
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users(username,password_hash,role,created_at,points) VALUES(?,?,?,?,0)",
                (username, hash_password(password), role, now_iso()),
            )
            db.commit()
            flash("Регистрация успешна. Теперь войдите.", "success")
            return redirect(url_for("main.login"))
        except Exception:
            flash("Такой логин уже существует.", "error")
    return render_template("register.html")

@bp.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not user or not verify_password(user["password_hash"], password):
            flash("Неверный логин или пароль.", "error")
            return render_template("login.html")
        if user["is_blocked"]:
            flash("Ваш аккаунт заблокирован. Обратитесь к администратору.", "error")
            return render_template("login.html")
        session.clear()
        session["user_id"] = user["id"]
        flash("Вы вошли в систему.", "success")
        nxt = request.args.get("next") or url_for("main.index")
        return redirect(nxt)
    return render_template("login.html")

@bp.route("/logout")
def logout():
    session.clear()
    flash("Вы вышли.", "success")
    return redirect(url_for("main.index"))

@bp.route("/profile", methods=["GET","POST"])
@login_required
def profile():
    db = get_db()
    u = current_user()

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        age = request.form.get("age") or None
        age_int = int(age) if age and str(age).isdigit() else None

        if u["role"] == "volunteer":
            group_name = (request.form.get("group_name") or "").strip()
            faculty = (request.form.get("faculty") or "").strip()
            university_id = request.form.get("university_id") or None
            uni_int = int(university_id) if university_id and str(university_id).isdigit() else None
            db.execute(
                "UPDATE users SET full_name=?, group_name=?, faculty=?, age=?, university_id=? WHERE id=?",
                (full_name, group_name, faculty, age_int, uni_int, u["id"]),
            )
        else:
            education_text = (request.form.get("education_text") or "").strip()
            bio_text = (request.form.get("bio_text") or "").strip()
            db.execute(
                "UPDATE users SET full_name=?, age=?, education_text=?, bio_text=? WHERE id=?",
                (full_name, age_int, education_text, bio_text, u["id"]),
            )

        db.commit()
        flash("Профиль обновлён.", "success")
        return redirect(url_for("main.profile"))

    universities = db.execute("SELECT * FROM universities ORDER BY name").fetchall()
    my_event_apps = db.execute(
        "SELECT a.*, e.name as event_name, e.points as event_points, e.id as event_id FROM event_applications a JOIN events e ON e.id=a.event_id WHERE a.user_id=? ORDER BY a.id DESC",
        (u["id"],),
    ).fetchall()
    my_task_apps = db.execute(
        "SELECT a.*, t.name as task_name, t.points as task_points, t.id as task_id FROM task_applications a JOIN tasks t ON t.id=a.task_id WHERE a.user_id=? ORDER BY a.id DESC",
        (u["id"],),
    ).fetchall()
    my_event_reports = db.execute(
        "SELECT r.*, e.name as event_name FROM event_reports r JOIN events e ON e.id=r.event_id WHERE r.user_id=? ORDER BY r.id DESC",
        (u["id"],),
    ).fetchall()
    my_task_reports = db.execute(
        "SELECT r.*, t.name as task_name FROM task_reports r JOIN tasks t ON t.id=r.task_id WHERE r.user_id=? ORDER BY r.id DESC",
        (u["id"],),
    ).fetchall()

    return render_template(
        "profile.html",
        universities=universities,
        my_event_apps=my_event_apps,
        my_task_apps=my_task_apps,
        my_event_reports=my_event_reports,
        my_task_reports=my_task_reports,
    )

@bp.route("/events")
def events():
    db = get_db()
    events = db.execute(
        "SELECT e.*, (SELECT COUNT(1) FROM event_applications a WHERE a.event_id=e.id AND a.status IN (?, ?)) as appl_count FROM events e ORDER BY e.start_time IS NULL, e.start_time DESC, e.id DESC",
        (APP_PENDING, APP_APPROVED),
    ).fetchall()
    return render_template("events.html", events=events)

@bp.route("/events/<int:event_id>")
def event_detail(event_id: int):
    db = get_db()
    e = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not e:
        flash("Мероприятие не найдено.", "error")
        return redirect(url_for("main.events"))
    appl_count = db.execute(
        "SELECT COUNT(1) c FROM event_applications WHERE event_id=? AND status IN (?, ?)",
        (event_id, APP_PENDING, APP_APPROVED),
    ).fetchone()["c"]
    user = current_user()
    my_app = None
    if user:
        my_app = db.execute("SELECT * FROM event_applications WHERE event_id=? AND user_id=?", (event_id, user["id"])).fetchone()
    return render_template("event_detail.html", e=e, appl_count=appl_count, my_app=my_app)

@bp.route("/events/<int:event_id>/apply", methods=["POST"])
@login_required
@roles_required("volunteer")
def event_apply(event_id: int):
    db = get_db()
    u = current_user()
    e = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not e:
        flash("Мероприятие не найдено.", "error")
        return redirect(url_for("main.events"))
    # capacity
    appl_count = _active_app_count(db, "event", event_id)
    if e["max_participants"] and appl_count >= e["max_participants"]:
        flash("Достигнут лимит участников.", "error")
        return redirect(url_for("main.event_detail", event_id=event_id))

    needs_release = 1 if request.form.get("needs_release") == "1" else 0
    needs_hours = 1 if request.form.get("needs_volunteer_hours") == "1" else 0
    try:
        db.execute(
            "INSERT INTO event_applications(event_id,user_id,needs_release,needs_volunteer_hours,status,created_at) VALUES(?,?,?,?,?,?)",
            (event_id, u["id"], needs_release, needs_hours, APP_PENDING, now_iso()),
        )
        db.commit()
        flash("Заявка отправлена и ожидает подтверждения.", "success")
    except Exception:
        flash("Заявка уже существует.", "error")
    return redirect(url_for("main.event_detail", event_id=event_id))

@bp.route("/tasks")
def tasks():
    db = get_db()
    tasks = db.execute(
        "SELECT t.*, (SELECT COUNT(1) FROM task_applications a WHERE a.task_id=t.id AND a.status IN (?, ?)) as appl_count FROM tasks t ORDER BY t.start_time IS NULL, t.start_time DESC, t.id DESC",
        (APP_PENDING, APP_APPROVED),
    ).fetchall()
    return render_template("tasks.html", tasks=tasks)

@bp.route("/tasks/<int:task_id>")
def task_detail(task_id: int):
    db = get_db()
    t = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not t:
        flash("Задание не найдено.", "error")
        return redirect(url_for("main.tasks"))
    appl_count = db.execute(
        "SELECT COUNT(1) c FROM task_applications WHERE task_id=? AND status IN (?, ?)",
        (task_id, APP_PENDING, APP_APPROVED),
    ).fetchone()["c"]
    user = current_user()
    my_app = None
    if user:
        my_app = db.execute("SELECT * FROM task_applications WHERE task_id=? AND user_id=?", (task_id, user["id"])).fetchone()
    return render_template("task_detail.html", t=t, appl_count=appl_count, my_app=my_app)

@bp.route("/tasks/<int:task_id>/apply", methods=["POST"])
@login_required
@roles_required("volunteer")
def task_apply(task_id: int):
    db = get_db()
    u = current_user()
    t = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not t:
        flash("Задание не найдено.", "error")
        return redirect(url_for("main.tasks"))
    appl_count = _active_app_count(db, "task", task_id)
    if t["max_participants"] and appl_count >= t["max_participants"]:
        flash("Достигнут лимит участников.", "error")
        return redirect(url_for("main.task_detail", task_id=task_id))
    try:
        db.execute(
            "INSERT INTO task_applications(task_id,user_id,status,created_at) VALUES(?,?,?,?)",
            (task_id, u["id"], APP_PENDING, now_iso()),
        )
        db.commit()
        flash("Заявка отправлена и ожидает подтверждения.", "success")
    except Exception:
        flash("Заявка уже существует.", "error")
    return redirect(url_for("main.task_detail", task_id=task_id))

@bp.route("/reports/event/<int:event_id>", methods=["GET","POST"])
@login_required
@roles_required("volunteer")
def report_event(event_id: int):
    db = get_db()
    u = current_user()
    # must have application
    app_row = db.execute("SELECT * FROM event_applications WHERE event_id=? AND user_id=?", (event_id, u["id"])).fetchone()
    if not app_row:
        flash("Сначала подайте заявку на мероприятие.", "error")
        return redirect(url_for("main.event_detail", event_id=event_id))
    if app_row["status"] != APP_APPROVED:
        flash("Заявка должна быть подтверждена организатором/админом, чтобы отправить отчёт.", "error")
        return redirect(url_for("main.event_detail", event_id=event_id))
    e = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if request.method == "POST":
        report_text = (request.form.get("report_text") or "").strip()
        file = request.files.get("media")
        media_path = None
        if file and file.filename:
            fn = secure_filename(file.filename)
            media_path = os.path.join(_upload_dir(), f"event_{event_id}_user_{u['id']}_{fn}")
            file.save(media_path)
        try:
            db.execute(
                "INSERT INTO event_reports(event_id,user_id,report_text,media_path,created_at) VALUES(?,?,?,?,?)",
                (event_id, u["id"], report_text, media_path, now_iso()),
            )
            db.commit()
            flash("Отчёт отправлен.", "success")
        except Exception:
            flash("Отчёт уже существует. Обновление пока не реализовано в MVP.", "error")
        return redirect(url_for("main.profile"))
    return render_template("report_form.html", kind="event", item=e)

@bp.route("/reports/task/<int:task_id>", methods=["GET","POST"])
@login_required
@roles_required("volunteer")
def report_task(task_id: int):
    db = get_db()
    u = current_user()
    app_row = db.execute("SELECT * FROM task_applications WHERE task_id=? AND user_id=?", (task_id, u["id"])).fetchone()
    if not app_row:
        flash("Сначала подайте заявку на задание.", "error")
        return redirect(url_for("main.task_detail", task_id=task_id))
    if app_row["status"] != APP_APPROVED:
        flash("Заявка должна быть подтверждена организатором/админом, чтобы отправить отчёт.", "error")
        return redirect(url_for("main.task_detail", task_id=task_id))
    t = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if request.method == "POST":
        report_text = (request.form.get("report_text") or "").strip()
        file = request.files.get("media")
        media_path = None
        if file and file.filename:
            fn = secure_filename(file.filename)
            media_path = os.path.join(_upload_dir(), f"task_{task_id}_user_{u['id']}_{fn}")
            file.save(media_path)
        try:
            db.execute(
                "INSERT INTO task_reports(task_id,user_id,report_text,media_path,created_at) VALUES(?,?,?,?,?)",
                (task_id, u["id"], report_text, media_path, now_iso()),
            )
            db.commit()
            flash("Отчёт отправлен.", "success")
        except Exception:
            flash("Отчёт уже существует. Обновление пока не реализовано в MVP.", "error")
        return redirect(url_for("main.profile"))
    return render_template("report_form.html", kind="task", item=t)

@bp.route("/uploads/<path:filename>")
@login_required
def uploads(filename):
    # Protected download: only the report owner, the item organizer, or admin can access.
    # Accept either a stored path or a plain filename; always normalize to a safe basename.
    if not filename:
        flash("Некорректное имя файла.", "error")
        return redirect(url_for("main.index"))

    safe_name = os.path.basename(filename).strip()
    if not safe_name or safe_name in (".", ".."):
        flash("Некорректное имя файла.", "error")
        return redirect(url_for("main.index"))

    db = get_db()
    me = current_user()

    # Reports may store media_path as a full path or as a filename; cover both.
    like = f"%/{safe_name}"
    row = db.execute(
        """
        SELECT r.user_id as owner_id, e.created_by as created_by
        FROM event_reports r
        JOIN events e ON e.id=r.event_id
        WHERE r.media_path = ? OR r.media_path LIKE ?
        UNION ALL
        SELECT r.user_id as owner_id, t.created_by as created_by
        FROM task_reports r
        JOIN tasks t ON t.id=r.task_id
        WHERE r.media_path = ? OR r.media_path LIKE ?
        LIMIT 1
        """,
        (filename, like, filename, like),
    ).fetchone()

    if not row:
        # As a fallback, try by basename only (older data / templates)
        row = db.execute(
            """
            SELECT r.user_id as owner_id, e.created_by as created_by
            FROM event_reports r
            JOIN events e ON e.id=r.event_id
            WHERE r.media_path LIKE ?
            UNION ALL
            SELECT r.user_id as owner_id, t.created_by as created_by
            FROM task_reports r
            JOIN tasks t ON t.id=r.task_id
            WHERE r.media_path LIKE ?
            LIMIT 1
            """,
            (like, like),
        ).fetchone()

    if not row:
        flash("Файл не найден.", "error")
        return redirect(url_for("main.index"))

    allowed = False
    if me and me["role"] == "admin":
        allowed = True
    elif me and int(me["id"]) == int(row["owner_id"]):
        allowed = True
    elif me and me["role"] == "organizer" and int(me["id"]) == int(row["created_by"]):
        allowed = True

    if not allowed:
        flash("Недостаточно прав для доступа к файлу.", "error")
        return redirect(url_for("main.index"))

    # Only serve from uploads directory using a normalized basename (prevents traversal).
    return send_from_directory(_upload_dir(), safe_name)

# Organizer/Admin: create content
@bp.route("/manage")
@login_required
@roles_required("admin","organizer")
def manage():
    db = get_db()
    u = current_user()
    pending_apps = 0
    pending_reports = 0

    if u and u["role"] in ("admin","organizer"):
        if u["role"] == "admin":
            # all items
            pending_apps = db.execute(
                "SELECT (SELECT COUNT(1) FROM event_applications WHERE status=?) + (SELECT COUNT(1) FROM task_applications WHERE status=?) as c",
                (APP_PENDING, APP_PENDING),
            ).fetchone()["c"]
            pending_reports = db.execute(
                "SELECT (SELECT COUNT(1) FROM event_reports WHERE status NOT IN ('принят')) + (SELECT COUNT(1) FROM task_reports WHERE status NOT IN ('принят')) as c"
            ).fetchone()["c"]
        else:
            # only own items
            pending_apps = db.execute(
                """
                SELECT
                  (SELECT COUNT(1) FROM event_applications a
                     JOIN events e ON e.id=a.event_id
                    WHERE a.status=? AND e.created_by=?)
                + (SELECT COUNT(1) FROM task_applications a
                     JOIN tasks t ON t.id=a.task_id
                    WHERE a.status=? AND t.created_by=?)
                AS c
                """,
                (APP_PENDING, u["id"], APP_PENDING, u["id"]),
            ).fetchone()["c"]
            pending_reports = db.execute(
                """
                SELECT
                  (SELECT COUNT(1) FROM event_reports r
                     JOIN events e ON e.id=r.event_id
                    WHERE r.status NOT IN ('принят') AND e.created_by=?)
                + (SELECT COUNT(1) FROM task_reports r
                     JOIN tasks t ON t.id=r.task_id
                    WHERE r.status NOT IN ('принят') AND t.created_by=?)
                AS c
                """,
                (u["id"], u["id"]),
            ).fetchone()["c"]

    return render_template("manage.html", pending_apps=pending_apps, pending_reports=pending_reports)

@bp.route("/manage/applications")
@login_required
@roles_required("admin","organizer")
def manage_applications():
    db = get_db()
    u = current_user()
    status = (request.args.get("status") or "pending").strip().lower()
    status_map = {
        "pending": APP_PENDING,
        "approved": APP_APPROVED,
        "rejected": APP_REJECTED,
        "all": None,
    }
    status_value = status_map.get(status, APP_PENDING)

    # For organizers: only applications for their own items. For admin: all.
    event_filter = "" if u["role"] == "admin" else "AND e.created_by = ?"
    task_filter = "" if u["role"] == "admin" else "AND t.created_by = ?"
    params_e_owner = () if u["role"] == "admin" else (u["id"],)
    params_t_owner = () if u["role"] == "admin" else (u["id"],)

    where_status_e = "" if status_value is None else "AND a.status = ?"
    where_status_t = "" if status_value is None else "AND a.status = ?"
    params_e_status = () if status_value is None else (status_value,)
    params_t_status = () if status_value is None else (status_value,)

    event_apps = db.execute(
        f"""
        SELECT a.*, e.name as item_name, e.start_time, e.end_time, u.username as username
        FROM event_applications a
        JOIN events e ON e.id=a.event_id
        JOIN users u ON u.id=a.user_id
        WHERE 1=1 {where_status_e} {event_filter}
        ORDER BY a.id DESC
        """,
        params_e_status + params_e_owner,
    ).fetchall()

    task_apps = db.execute(
        f"""
        SELECT a.*, t.name as item_name, u.username as username
        FROM task_applications a
        JOIN tasks t ON t.id=a.task_id
        JOIN users u ON u.id=a.user_id
        WHERE 1=1 {where_status_t} {task_filter}
        ORDER BY a.id DESC
        """,
        params_t_status + params_t_owner,
    ).fetchall()

    # Counters for tabs
    def _count_event(st):
        if u["role"] == "admin":
            if st is None:
                return db.execute("SELECT COUNT(1) as c FROM event_applications").fetchone()["c"]
            return db.execute("SELECT COUNT(1) as c FROM event_applications WHERE status=?", (st,)).fetchone()["c"]
        if st is None:
            return db.execute(
                "SELECT COUNT(1) as c FROM event_applications a JOIN events e ON e.id=a.event_id WHERE e.created_by=?",
                (u["id"],),
            ).fetchone()["c"]
        return db.execute(
            "SELECT COUNT(1) as c FROM event_applications a JOIN events e ON e.id=a.event_id WHERE a.status=? AND e.created_by=?",
            (st, u["id"]),
        ).fetchone()["c"]

    def _count_task(st):
        if u["role"] == "admin":
            if st is None:
                return db.execute("SELECT COUNT(1) as c FROM task_applications").fetchone()["c"]
            return db.execute("SELECT COUNT(1) as c FROM task_applications WHERE status=?", (st,)).fetchone()["c"]
        if st is None:
            return db.execute(
                "SELECT COUNT(1) as c FROM task_applications a JOIN tasks t ON t.id=a.task_id WHERE t.created_by=?",
                (u["id"],),
            ).fetchone()["c"]
        return db.execute(
            "SELECT COUNT(1) as c FROM task_applications a JOIN tasks t ON t.id=a.task_id WHERE a.status=? AND t.created_by=?",
            (st, u["id"]),
        ).fetchone()["c"]

    counts = {
        "pending": {"events": _count_event(APP_PENDING), "tasks": _count_task(APP_PENDING)},
        "approved": {"events": _count_event(APP_APPROVED), "tasks": _count_task(APP_APPROVED)},
        "rejected": {"events": _count_event(APP_REJECTED), "tasks": _count_task(APP_REJECTED)},
        "all": {"events": _count_event(None), "tasks": _count_task(None)},
    }

    return render_template(
        "manage_applications.html",
        event_apps=event_apps,
        task_apps=task_apps,
        status=status,
        counts=counts,
    )


@bp.route("/manage/reports")
@login_required
@roles_required("admin","organizer")
def manage_reports():
    db = get_db()
    u = current_user()

    status = (request.args.get("status") or "pending").strip().lower()
    # Report statuses in DB: 'принят' or 'отклонен'/'отклонён' or any other value meaning "на проверке".
    approved = "принят"
    rejected_vals = ("отклонен", "отклонён")

    def build_where(alias: str):
        if status == "approved":
            return f"AND {alias}.status = 'принят'"
        if status == "rejected":
            return f"AND {alias}.status IN ('отклонен','отклонён')"
        if status == "all":
            return ""
        # pending by default
        return f"AND {alias}.status NOT IN ('принят','отклонен','отклонён')"

    owner_event = "" if u["role"] == "admin" else "AND e.created_by = ?"
    owner_task = "" if u["role"] == "admin" else "AND t.created_by = ?"
    owner_params = () if u["role"] == "admin" else (u["id"],)

    w_event = build_where("r")
    w_task = build_where("r")

    event_reports = db.execute(
        f"""
        SELECT r.*, e.name as item_name, u.username as username, e.created_by as created_by
        FROM event_reports r
        JOIN events e ON e.id=r.event_id
        JOIN users u ON u.id=r.user_id
        WHERE 1=1 {w_event} {owner_event}
        ORDER BY r.id DESC
        """,
        owner_params,
    ).fetchall()

    task_reports = db.execute(
        f"""
        SELECT r.*, t.name as item_name, u.username as username, t.created_by as created_by
        FROM task_reports r
        JOIN tasks t ON t.id=r.task_id
        JOIN users u ON u.id=r.user_id
        WHERE 1=1 {w_task} {owner_task}
        ORDER BY r.id DESC
        """,
        owner_params,
    ).fetchall()

    # For UI: keep accepted reports compact (latest 10 per section) on 'all' or 'approved' views.
    if status in ("all", "approved"):
        event_reports = event_reports[:10] if status == "approved" else (
            [r for r in event_reports if r["status"] != "принят"] + [r for r in event_reports if r["status"] == "принят"][:10]
        )
        task_reports = task_reports[:10] if status == "approved" else (
            [r for r in task_reports if r["status"] != "принят"] + [r for r in task_reports if r["status"] == "принят"][:10]
        )

    # Counts for tabs
    def count_reports(kind: str, st: str):
        if kind == "event":
            base = "FROM event_reports r JOIN events e ON e.id=r.event_id WHERE 1=1"
            owner = "" if u["role"] == "admin" else "AND e.created_by=?"
        else:
            base = "FROM task_reports r JOIN tasks t ON t.id=r.task_id WHERE 1=1"
            owner = "" if u["role"] == "admin" else "AND t.created_by=?"
        params = () if u["role"] == "admin" else (u["id"],)
        if st == "approved":
            where = "AND r.status='принят'"
        elif st == "rejected":
            where = "AND r.status IN ('отклонен','отклонён')"
        elif st == "all":
            where = ""
        else:
            where = "AND r.status NOT IN ('принят','отклонен','отклонён')"
        return db.execute(f"SELECT COUNT(1) as c {base} {where} {owner}", params).fetchone()["c"]

    counts = {
        "pending": {"events": count_reports("event", "pending"), "tasks": count_reports("task", "pending")},
        "approved": {"events": count_reports("event", "approved"), "tasks": count_reports("task", "approved")},
        "rejected": {"events": count_reports("event", "rejected"), "tasks": count_reports("task", "rejected")},
        "all": {"events": count_reports("event", "all"), "tasks": count_reports("task", "all")},
    }

    return render_template("manage_reports.html", event_reports=event_reports, task_reports=task_reports, status=status, counts=counts)

def _can_moderate_report(created_by: int) -> bool:
    me = current_user()
    if not me:
        return False
    if me["role"] == "admin":
        return True
    return me["role"] == "organizer" and int(created_by or 0) == int(me["id"])

@bp.route("/manage/reports/event/<int:report_id>/approve", methods=["POST"])
@login_required
@roles_required("admin", "organizer")
def manage_approve_event_report(report_id: int):
    db = get_db()
    row = db.execute(
        """
        SELECT r.id, r.user_id, r.status, e.points, e.created_by
        FROM event_reports r
        JOIN events e ON e.id = r.event_id
        WHERE r.id = ?
        """,
        (report_id,),
    ).fetchone()

    if not row or not _can_moderate_report(row["created_by"]):
        flash("Недостаточно прав.", "error")
        audit_log("manage_approve_event_report_denied", str(report_id))
        return redirect(url_for("main.manage_reports"))

    awarded = _award_points_once(
        "event_reports",
        row["id"],
        row["user_id"],
        int(row["points"] or 0),
    )

    audit_log("manage_approve_event_report", str(report_id))
    flash("Отчёт принят." + (" Баллы начислены." if awarded else ""), "success")
    return redirect(url_for("main.manage_reports"))

@bp.route("/manage/reports/task/<int:report_id>/approve", methods=["POST"])
@login_required
@roles_required("admin", "organizer")
def manage_approve_task_report(report_id: int):
    db = get_db()
    row = db.execute(
        """
        SELECT r.id, r.user_id, r.status, t.points, t.created_by
        FROM task_reports r
        JOIN tasks t ON t.id = r.task_id
        WHERE r.id = ?
        """,
        (report_id,),
    ).fetchone()

    if not row or not _can_moderate_report(row["created_by"]):
        flash("Недостаточно прав.", "error")
        audit_log("manage_approve_task_report_denied", str(report_id))
        return redirect(url_for("main.manage_reports"))

    awarded = _award_points_once(
        "task_reports",
        row["id"],
        row["user_id"],
        int(row["points"] or 0),
    )

    audit_log("manage_approve_task_report", str(report_id))
    flash("Отчёт принят." + (" Баллы начислены." if awarded else ""), "success")
    return redirect(url_for("main.manage_reports"))

@bp.route("/manage/reports/event/<int:report_id>/reject", methods=["POST"])
@login_required
@roles_required("admin", "organizer")
def manage_reject_event_report(report_id: int):
    db = get_db()
    row = db.execute(
        """SELECT r.id, e.created_by
           FROM event_reports r
           JOIN events e ON e.id=r.event_id
           WHERE r.id=?""",
        (report_id,),
    ).fetchone()
    if not row or not _can_moderate_report(row["created_by"]):
        flash("Недостаточно прав.", "error")
        audit_log("manage_reject_event_report_denied", str(report_id))
        return redirect(url_for("main.manage_reports"))
    db.execute("UPDATE event_reports SET status='отклонён' WHERE id=?", (report_id,))
    db.commit()
    audit_log("manage_reject_event_report", str(report_id))
    flash("Отчёт отклонён.", "success")
    return redirect(url_for("main.manage_reports"))

@bp.route("/manage/reports/task/<int:report_id>/reject", methods=["POST"])
@login_required
@roles_required("admin", "organizer")
def manage_reject_task_report(report_id: int):
    db = get_db()
    row = db.execute(
        """SELECT r.id, t.created_by
           FROM task_reports r
           JOIN tasks t ON t.id=r.task_id
           WHERE r.id=?""",
        (report_id,),
    ).fetchone()
    if not row or not _can_moderate_report(row["created_by"]):
        flash("Недостаточно прав.", "error")
        audit_log("manage_reject_task_report_denied", str(report_id))
        return redirect(url_for("main.manage_reports"))
    db.execute("UPDATE task_reports SET status='отклонён' WHERE id=?", (report_id,))
    db.commit()
    audit_log("manage_reject_task_report", str(report_id))
    flash("Отчёт отклонён.", "success")
    return redirect(url_for("main.manage_reports"))


def _delete_report_file(table: str, report_id: int) -> bool:
    """Delete attached media file for a report (event_reports/task_reports).

    Returns True if DB was updated (file reference cleared), False otherwise.
    File deletion is best-effort.
    """
    db = get_db()
    if table not in ("event_reports", "task_reports"):
        return False

    if table == "event_reports":
        row = db.execute(
            """SELECT r.id, r.media_path, e.created_by
               FROM event_reports r
               JOIN events e ON e.id=r.event_id
               WHERE r.id=?""",
            (report_id,),
        ).fetchone()
    else:
        row = db.execute(
            """SELECT r.id, r.media_path, t.created_by
               FROM task_reports r
               JOIN tasks t ON t.id=r.task_id
               WHERE r.id=?""",
            (report_id,),
        ).fetchone()

    if not row:
        return False
    if not _can_moderate_report(row["created_by"]):
        return False
    media_path = row["media_path"]
    if not media_path:
        # nothing to delete, but keep it idempotent
        return True

    # Compute safe path inside uploads dir.
    uploads_dir = os.path.realpath(_upload_dir())
    candidate = media_path
    # If stored as filename, join uploads dir; if full path, keep but validate.
    if os.path.basename(candidate) == candidate:
        candidate = os.path.join(uploads_dir, candidate)
    candidate_real = os.path.realpath(candidate)
    if candidate_real.startswith(uploads_dir + os.sep) or candidate_real == uploads_dir:
        try:
            if os.path.exists(candidate_real) and os.path.isfile(candidate_real):
                os.remove(candidate_real)
        except OSError:
            pass

    db.execute(f"UPDATE {table} SET media_path=NULL WHERE id=?", (report_id,))
    db.commit()
    return True


@bp.route("/manage/reports/event/<int:report_id>/delete_file", methods=["POST"])
@login_required
@roles_required("admin", "organizer")
def manage_delete_event_report_file(report_id: int):
    ok = _delete_report_file("event_reports", report_id)
    flash("Файл отчёта удалён." if ok else "Недостаточно прав или отчёт не найден.", "success" if ok else "error")
    return redirect(url_for("main.manage_reports"))


@bp.route("/manage/reports/task/<int:report_id>/delete_file", methods=["POST"])
@login_required
@roles_required("admin", "organizer")
def manage_delete_task_report_file(report_id: int):
    ok = _delete_report_file("task_reports", report_id)
    flash("Файл отчёта удалён." if ok else "Недостаточно прав или отчёт не найден.", "success" if ok else "error")
    return redirect(url_for("main.manage_reports"))


def _approve_event_application(app_id: int):
    db = get_db()
    row = db.execute(
        "SELECT a.*, e.max_participants, e.created_by FROM event_applications a JOIN events e ON e.id=a.event_id WHERE a.id=?",
        (app_id,),
    ).fetchone()
    if not row:
        return False, "Заявка не найдена."
    if not _is_manager_for_item({"created_by": row["created_by"]}):
        return False, "Недостаточно прав."
    if row["status"] != APP_PENDING:
        return False, "Заявка уже обработана."
    # capacity check counts only approved
    approved_count = db.execute(
        "SELECT COUNT(1) c FROM event_applications WHERE event_id=? AND status=?",
        (row["event_id"], APP_APPROVED),
    ).fetchone()["c"]
    if row["max_participants"] and int(approved_count or 0) >= int(row["max_participants"] or 0):
        return False, "Лимит участников уже заполнен."
    db.execute("UPDATE event_applications SET status=? WHERE id=?", (APP_APPROVED, app_id))
    db.commit()
    return True, "Заявка подтверждена."


def _approve_task_application(app_id: int):
    db = get_db()
    row = db.execute(
        "SELECT a.*, t.max_participants, t.created_by FROM task_applications a JOIN tasks t ON t.id=a.task_id WHERE a.id=?",
        (app_id,),
    ).fetchone()
    if not row:
        return False, "Заявка не найдена."
    if not _is_manager_for_item({"created_by": row["created_by"]}):
        return False, "Недостаточно прав."
    if row["status"] != APP_PENDING:
        return False, "Заявка уже обработана."
    approved_count = db.execute(
        "SELECT COUNT(1) c FROM task_applications WHERE task_id=? AND status=?",
        (row["task_id"], APP_APPROVED),
    ).fetchone()["c"]
    if row["max_participants"] and int(approved_count or 0) >= int(row["max_participants"] or 0):
        return False, "Лимит участников уже заполнен."
    db.execute("UPDATE task_applications SET status=? WHERE id=?", (APP_APPROVED, app_id))
    db.commit()
    return True, "Заявка подтверждена."


@bp.route("/manage/applications/event/<int:app_id>/approve", methods=["POST"])
@login_required
@roles_required("admin","organizer")
def manage_approve_event_application(app_id: int):
    ok, msg = _approve_event_application(app_id)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("main.manage_applications"))


@bp.route("/manage/applications/event/<int:app_id>/reject", methods=["POST"])
@login_required
@roles_required("admin","organizer")
def manage_reject_event_application(app_id: int):
    db = get_db()
    row = db.execute(
        "SELECT a.id, a.status, e.created_by FROM event_applications a JOIN events e ON e.id=a.event_id WHERE a.id=?",
        (app_id,),
    ).fetchone()
    if not row:
        flash("Заявка не найдена.", "error")
        return redirect(url_for("main.manage_applications"))
    if not _is_manager_for_item({"created_by": row["created_by"]}):
        flash("Недостаточно прав.", "error")
        return redirect(url_for("main.manage_applications"))
    db.execute("UPDATE event_applications SET status=? WHERE id=?", (APP_REJECTED, app_id))
    db.commit()
    flash("Заявка отклонена.", "success")
    return redirect(url_for("main.manage_applications"))


@bp.route("/manage/applications/task/<int:app_id>/approve", methods=["POST"])
@login_required
@roles_required("admin","organizer")
def manage_approve_task_application(app_id: int):
    ok, msg = _approve_task_application(app_id)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("main.manage_applications"))


@bp.route("/manage/applications/task/<int:app_id>/reject", methods=["POST"])
@login_required
@roles_required("admin","organizer")
def manage_reject_task_application(app_id: int):
    db = get_db()
    row = db.execute(
        "SELECT a.id, a.status, t.created_by FROM task_applications a JOIN tasks t ON t.id=a.task_id WHERE a.id=?",
        (app_id,),
    ).fetchone()
    if not row:
        flash("Заявка не найдена.", "error")
        return redirect(url_for("main.manage_applications"))
    if not _is_manager_for_item({"created_by": row["created_by"]}):
        flash("Недостаточно прав.", "error")
        return redirect(url_for("main.manage_applications"))
    db.execute("UPDATE task_applications SET status=? WHERE id=?", (APP_REJECTED, app_id))
    db.commit()
    flash("Заявка отклонена.", "success")
    return redirect(url_for("main.manage_applications"))

@bp.route("/manage/events/new", methods=["GET","POST"])
@login_required
@roles_required("admin","organizer")
def event_new():
    db = get_db()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip()
        link = (request.form.get("link") or "").strip() or None
        points = int(request.form.get("points") or 0)
        start_time = (request.form.get("start_time") or "").strip() or None
        end_time = (request.form.get("end_time") or "").strip() or None
        max_participants = int(request.form.get("max_participants") or 0)
        if not name:
            flash("Название обязательно.", "error")
            return render_template("event_edit.html", e=None)
        db.execute(
            "INSERT INTO events(name,description,link,points,start_time,end_time,max_participants,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (name, description, link, points, start_time, end_time, max_participants, current_user()["id"], now_iso()),
        )
        db.commit()
        flash("Мероприятие создано.", "success")
        return redirect(url_for("main.events"))
    return render_template("event_edit.html", e=None)


@bp.route("/manage/events/<int:event_id>/edit", methods=["GET","POST"])
@login_required
@roles_required("admin","organizer")
def event_edit(event_id: int):
    db = get_db()
    e = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not e:
        flash("Мероприятие не найдено.", "error")
        return redirect(url_for("main.events"))
    if not _is_manager_for_item(e):
        flash("Недостаточно прав.", "error")
        return redirect(url_for("main.event_detail", event_id=event_id))
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip()
        link = (request.form.get("link") or "").strip() or None
        points = int(request.form.get("points") or 0)
        start_time = (request.form.get("start_time") or "").strip() or None
        end_time = (request.form.get("end_time") or "").strip() or None
        max_participants = int(request.form.get("max_participants") or 0)
        if not name:
            flash("Название обязательно.", "error")
            return render_template("event_edit.html", e=e)
        db.execute(
            "UPDATE events SET name=?, description=?, link=?, points=?, start_time=?, end_time=?, max_participants=? WHERE id=?",
            (name, description, link, points, start_time, end_time, max_participants, event_id),
        )
        db.commit()
        flash("Мероприятие обновлено.", "success")
        return redirect(url_for("main.event_detail", event_id=event_id))
    return render_template("event_edit.html", e=e)


@bp.route("/manage/events/<int:event_id>/delete", methods=["POST"])
@login_required
@roles_required("admin","organizer")
def event_delete(event_id: int):
    db = get_db()
    e = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not e:
        flash("Мероприятие не найдено.", "error")
        return redirect(url_for("main.events"))
    if not _is_manager_for_item(e):
        flash("Недостаточно прав.", "error")
        return redirect(url_for("main.event_detail", event_id=event_id))
    db.execute("DELETE FROM events WHERE id=?", (event_id,))
    db.commit()
    flash("Мероприятие удалено.", "success")
    return redirect(url_for("main.events"))

@bp.route("/manage/tasks/new", methods=["GET","POST"])
@login_required
@roles_required("admin","organizer")
def task_new():
    db = get_db()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip()
        points = int(request.form.get("points") or 0)
        start_time = (request.form.get("start_time") or "").strip() or None
        end_time = (request.form.get("end_time") or "").strip() or None
        max_participants = int(request.form.get("max_participants") or 0)
        if not name:
            flash("Название обязательно.", "error")
            return render_template("task_edit.html", t=None)
        db.execute(
            "INSERT INTO tasks(name,description,points,start_time,end_time,max_participants,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (name, description, points, start_time, end_time, max_participants, current_user()["id"], now_iso()),
        )
        db.commit()
        flash("Задание создано.", "success")
        return redirect(url_for("main.tasks"))
    return render_template("task_edit.html", t=None)


@bp.route("/manage/tasks/<int:task_id>/edit", methods=["GET","POST"])
@login_required
@roles_required("admin","organizer")
def task_edit(task_id: int):
    db = get_db()
    t = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not t:
        flash("Задание не найдено.", "error")
        return redirect(url_for("main.tasks"))
    if not _is_manager_for_item(t):
        flash("Недостаточно прав.", "error")
        return redirect(url_for("main.task_detail", task_id=task_id))
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip()
        points = int(request.form.get("points") or 0)
        start_time = (request.form.get("start_time") or "").strip() or None
        end_time = (request.form.get("end_time") or "").strip() or None
        max_participants = int(request.form.get("max_participants") or 0)
        if not name:
            flash("Название обязательно.", "error")
            return render_template("task_edit.html", t=t)
        db.execute(
            "UPDATE tasks SET name=?, description=?, points=?, start_time=?, end_time=?, max_participants=? WHERE id=?",
            (name, description, points, start_time, end_time, max_participants, task_id),
        )
        db.commit()
        flash("Задание обновлено.", "success")
        return redirect(url_for("main.task_detail", task_id=task_id))
    return render_template("task_edit.html", t=t)


@bp.route("/manage/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
@roles_required("admin","organizer")
def task_delete(task_id: int):
    db = get_db()
    t = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not t:
        flash("Задание не найдено.", "error")
        return redirect(url_for("main.tasks"))
    if not _is_manager_for_item(t):
        flash("Недостаточно прав.", "error")
        return redirect(url_for("main.task_detail", task_id=task_id))
    db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    db.commit()
    flash("Задание удалено.", "success")
    return redirect(url_for("main.tasks"))

# Admin: universities/users/reports moderation
@bp.route("/admin")
@login_required
@roles_required("admin")
def admin_panel():
    db = get_db()
    q = (request.args.get("q") or "").strip()
    if q:
        like = f"%{q.lower()}%"
        users = db.execute(
            """
            SELECT u.*, COALESCE(un.name,'') as university_name
            FROM users u
            LEFT JOIN universities un ON un.id=u.university_id
            WHERE LOWER(u.username) LIKE ? OR LOWER(COALESCE(u.full_name,'')) LIKE ?
            ORDER BY u.id DESC
            LIMIT 200
            """,
            (like, like),
        ).fetchall()
    else:
        users = db.execute(
            "SELECT u.*, COALESCE(un.name,'') as university_name FROM users u LEFT JOIN universities un ON un.id=u.university_id ORDER BY u.id DESC LIMIT 200"
        ).fetchall()
    unis = db.execute("SELECT * FROM universities ORDER BY name").fetchall()
    event_reports = db.execute(
        "SELECT r.*, e.name as item_name, u.username as username, e.points as item_points FROM event_reports r JOIN events e ON e.id=r.event_id JOIN users u ON u.id=r.user_id ORDER BY r.status, r.id DESC"
    ).fetchall()
    task_reports = db.execute(
        "SELECT r.*, t.name as item_name, u.username as username, t.points as item_points FROM task_reports r JOIN tasks t ON t.id=r.task_id JOIN users u ON u.id=r.user_id ORDER BY r.status, r.id DESC"
    ).fetchall()
    return render_template("admin.html", users=users, unis=unis, event_reports=event_reports, task_reports=task_reports, q=q)

@bp.route("/admin/universities/add", methods=["POST"])
@login_required
@roles_required("admin")
def admin_university_add():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Введите название.", "error")
        return redirect(url_for("main.admin_panel"))
    db = get_db()
    try:
        db.execute("INSERT INTO universities(name) VALUES(?)", (name,))
        db.commit()
        flash("Учебное заведение добавлено.", "success")
    except Exception:
        flash("Не удалось добавить (возможно, уже существует).", "error")
    return redirect(url_for("main.admin_panel"))

@bp.route("/admin/universities/<int:uni_id>/delete", methods=["POST"])
@login_required
@roles_required("admin")
def admin_university_delete(uni_id: int):
    db = get_db()
 
    try:
        # 1. Проверяем, существует ли университет
        university = db.execute(
            "SELECT name FROM universities WHERE id = ?", 
            (uni_id,)
        ).fetchone()
 
        if not university:
            flash("Университет не найден.", "error")
            return redirect(url_for("main.admin_panel"))
 
        # 2. Проверяем, есть ли пользователи, связанные с этим университетом
        user_count = db.execute(
            "SELECT COUNT(*) as count FROM users WHERE university_id = ?",
            (uni_id,)
        ).fetchone()['count']
 
        if user_count > 0:
            # Вариант 1: Спрашиваем подтверждение на каскадное удаление/обнуление
            # Вариант 2: Обнуляем university_id у пользователей
 
            # Рекомендую обнулить university_id у пользователей
            db.execute(
                "UPDATE users SET university_id = NULL WHERE university_id = ?",
                (uni_id,)
            )
 
            flash(
                f"У {user_count} пользователей обнулен университет. "
                f"Теперь можно удалить университет.",
                "warning"
            )
 
        # 3. Удаляем университет
        db.execute("DELETE FROM universities WHERE id = ?", (uni_id,))
        db.commit()
 
        flash(f"Университет '{university['name']}' успешно удален.", "success")
 
    except sqlite3.IntegrityError as e:
        db.rollback()
        flash(f"Ошибка при удалении: {str(e)}", "error")
        print(f"Ошибка удаления университета {uni_id}: {e}")
 
    except Exception as e:
        db.rollback()
        flash(f"Произошла ошибка: {str(e)}", "error")
        print(f"Ошибка: {e}")
 
    return redirect(url_for("main.admin_panel"))

@bp.route("/admin/users/<int:user_id>/warn", methods=["POST"])
@login_required
@roles_required("admin")
def admin_user_warn(user_id: int):
    db = get_db()
    actor = current_user()
    # Safety: an admin must not be able to warn themselves.
    if actor and actor["id"] == user_id:
        flash("Нельзя выносить предупреждение самому себе.", "error")
        audit_log("admin_user_warn_denied_self", str(user_id))
        return redirect(url_for("main.admin_panel"))
    u = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        flash("Пользователь не найден.", "error")
        return redirect(url_for("main.admin_panel"))
    new_count = int(u["warnings_count"] or 0) + 1
    is_blocked = 1 if new_count >= 3 else u["is_blocked"]
    db.execute(
        "UPDATE users SET warnings_count=?, last_warning_at=?, is_blocked=? WHERE id=?",
        (new_count, now_iso(), is_blocked, user_id),
    )
    db.commit()
    flash("Предупреждение вынесено. После 3 предупреждений пользователь блокируется.", "success")
    return redirect(url_for("main.admin_panel"))

@bp.route("/admin/users/<int:user_id>/toggle_block", methods=["POST"])
@login_required
@roles_required("admin")
def admin_user_toggle_block(user_id: int):
    db = get_db()
    actor = current_user()
    # Safety: an admin must not be able to block/unblock themselves.
    if actor and actor["id"] == user_id:
        flash("Нельзя менять статус блокировки для самого себя.", "error")
        return redirect(url_for("main.admin_panel"))
    u = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        flash("Пользователь не найден.", "error")
        return redirect(url_for("main.admin_panel"))
    new_val = 0 if u["is_blocked"] else 1
    db.execute("UPDATE users SET is_blocked=? WHERE id=?", (new_val, user_id))
    db.commit()
    flash("Статус блокировки изменён.", "success")
    return redirect(url_for("main.admin_panel"))

@bp.route("/admin/users/<int:user_id>/role", methods=["POST"])
@login_required
@roles_required("admin")
def admin_user_role(user_id: int):
    role = request.form.get("role")
    if role not in ("admin","organizer","volunteer"):
        flash("Некорректная роль.", "error")
        audit_log("admin_user_role_invalid", f"{user_id}:{role}")
        return redirect(url_for("main.admin_panel"))
    db = get_db()
    # Protection: if there is only one active admin left, they cannot demote themselves.
    target = db.execute("SELECT id, role, is_blocked FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        flash("Пользователь не найден.", "error")
        return redirect(url_for("main.admin_panel"))
    actor = current_user()
    if actor and actor["id"] == user_id and target["role"] == "admin" and role != "admin":
        active_admins = db.execute(
            "SELECT COUNT(1) c FROM users WHERE role='admin' AND is_blocked=0"
        ).fetchone()["c"]
        if int(active_admins or 0) <= 1:
            flash("Нельзя сменить роль: вы единственный действующий администратор.", "error")
            return redirect(url_for("main.admin_panel"))

    db.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    db.commit()
    flash("Роль обновлена.", "success")
    return redirect(url_for("main.admin_panel"))

def _award_points_once(table: str, report_id: int, user_id: int, points: int) -> bool:
    """Award points once per report. Returns True if points were awarded."""
    db = get_db()
    # points_awarded column is added via lightweight migration.
    row = db.execute(f"SELECT points_awarded FROM {table} WHERE id=?", (report_id,)).fetchone()
    if not row:
        return False

    # Atomically flip points_awarded from 0->1; only then add points.
    cur = db.execute(
        f"UPDATE {table} SET status='принят', points_awarded=1 WHERE id=? AND COALESCE(points_awarded,0)=0",
        (report_id,),
    )
    if cur.rowcount == 1:
        db.execute("UPDATE users SET points = points + ? WHERE id=?", (points, user_id))
        db.commit()
        return True

    # Ensure status is 'принят' even if already awarded earlier.
    db.execute(f"UPDATE {table} SET status='принят' WHERE id=?", (report_id,))
    db.commit()
    return False


@bp.route("/admin/reports/event/<int:report_id>/approve", methods=["POST"])
@login_required
@roles_required("admin")
def admin_approve_event_report(report_id: int):
    db = get_db()
    row = db.execute(
        "SELECT r.id, r.user_id, e.points FROM event_reports r JOIN events e ON e.id=r.event_id WHERE r.id=?",
        (report_id,),
    ).fetchone()
    if row:
        awarded = _award_points_once("event_reports", row["id"], row["user_id"], int(row["points"] or 0))
        flash("Отчёт принят." + (" Баллы начислены." if awarded else ""), "success")
    return redirect(url_for("main.admin_panel"))

@bp.route("/admin/reports/task/<int:report_id>/approve", methods=["POST"])
@login_required
@roles_required("admin")
def admin_approve_task_report(report_id: int):
    db = get_db()
    row = db.execute(
        "SELECT r.id, r.user_id, t.points FROM task_reports r JOIN tasks t ON t.id=r.task_id WHERE r.id=?",
        (report_id,),
    ).fetchone()
    if row:
        awarded = _award_points_once("task_reports", row["id"], row["user_id"], int(row["points"] or 0))
        flash("Отчёт принят." + (" Баллы начислены." if awarded else ""), "success")
    return redirect(url_for("main.admin_panel"))

@bp.route("/admin/reports/event/<int:report_id>/reject", methods=["POST"])
@login_required
@roles_required("admin")
def admin_reject_event_report(report_id: int):
    db = get_db()
    db.execute("UPDATE event_reports SET status='отклонён' WHERE id=?", (report_id,))
    db.commit()
    flash("Отчёт отклонён.", "success")
    return redirect(url_for("main.admin_panel"))

@bp.route("/admin/reports/task/<int:report_id>/reject", methods=["POST"])
@login_required
@roles_required("admin")
def admin_reject_task_report(report_id: int):
    db = get_db()
    db.execute("UPDATE task_reports SET status='отклонён' WHERE id=?", (report_id,))
    db.commit()
    flash("Отчёт отклонён.", "success")
    return redirect(url_for("main.admin_panel"))


# --- Admin exports (CSV) ---
def _csv_response(rows, headers, filename: str):
    import io, csv
    si = io.StringIO()
    w = csv.writer(si)
    w.writerow(headers)
    for r in rows:
        w.writerow([r.get(h, "") if isinstance(r, dict) else r[h] for h in headers])
    output = si.getvalue().encode("utf-8-sig")
    from flask import Response
    return Response(output, mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})

@bp.route("/admin/export/users.csv")
@login_required
@roles_required("admin")
def admin_export_users():
    db = get_db()
    rows = db.execute(
        "SELECT id, username, full_name, role, points, is_blocked, warnings_count, created_at FROM users ORDER BY id DESC"
    ).fetchall()
    audit_log("export_users", "users.csv")
    return _csv_response(
        rows, 
        ["id", "username", "full_name", "role", "points", "is_blocked", "warnings_count", "created_at"], 
        "users.csv"
    )

@bp.route("/admin/export/events.csv")
@login_required
@roles_required("admin")
def admin_export_events():
    db = get_db()
    rows = db.execute("SELECT id, name, points, start_time, end_time, max_participants, created_by, created_at FROM events ORDER BY id DESC").fetchall()
    audit_log("export_events", "events.csv")
    return _csv_response(rows, ["id","name","points","start_time","end_time","max_participants","created_by","created_at"], "events.csv")

@bp.route("/admin/export/reports.csv")
@login_required
@roles_required("admin")
def admin_export_reports():
    db = get_db()
    rows = db.execute(
        """
        SELECT 'event' as kind, r.id as id, r.user_id as user_id, u.username as username, r.status as status, e.name as item_name, r.report_text as report_text, r.media_path as media_path, r.created_at as created_at
        FROM event_reports r JOIN events e ON e.id=r.event_id JOIN users u ON u.id=r.user_id
        UNION ALL
        SELECT 'task' as kind, r.id as id, r.user_id as user_id, u.username as username, r.status as status, t.name as item_name, r.report_text as report_text, r.media_path as media_path, r.created_at as created_at
        FROM task_reports r JOIN tasks t ON t.id=r.task_id JOIN users u ON u.id=r.user_id
        ORDER BY created_at DESC
        """
    ).fetchall()
    audit_log("export_reports", "reports.csv")
    return _csv_response(rows, ["kind","id","user_id","username","status","item_name","report_text","media_path","created_at"], "reports.csv")

