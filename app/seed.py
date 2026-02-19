import random
from .db import get_db, now_iso
from .auth import hash_password

def seed_data():
    db = get_db()

    # Universities
    uni_names = [
        "Варшавский университет",
        "Политехника",
        "Гуманитарный институт",
    ]
    for n in uni_names:
        db.execute("INSERT OR IGNORE INTO universities(name) VALUES(?)", (n,))

    # Users
    # admin: admin/admin
    users = [
        ("admin", "admin", "admin"),
        ("org1", "org1", "organizer"),
        ("vol1", "vol1", "volunteer"),
        ("vol2", "vol2", "volunteer"),
    ]
    for username, pw, role in users:
        db.execute(
            "INSERT OR IGNORE INTO users(username,password_hash,role,created_at,full_name,group_name,faculty,age,university_id,points) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                username,
                hash_password(pw),
                role,
                now_iso(),
                f"{username.upper()} Пользователь",
                "Группа A",
                "Факультет Добрых Дел",
                20 if role != "admin" else 30,
                1,
                0,
            ),
        )

    # Subscribers
    for row in db.execute("SELECT id FROM users").fetchall():
        db.execute("INSERT OR IGNORE INTO subscribers(user_id,is_subscribed) VALUES(?,1)", (row["id"],))

    # Events
    org_id = db.execute("SELECT id FROM users WHERE username='org1'").fetchone()["id"]
    events = [
        ("Сбор вещей для приюта", "Нужна помощь в сортировке и упаковке.", "https://example.com", 10),
        ("Уборка парка", "Совместная уборка территории парка.", "https://example.com", 8),
    ]
    for name, desc, link, pts in events:
        db.execute(
            "INSERT INTO events(name,description,link,points,start_time,end_time,max_participants,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (name, desc, link, pts, "2026-01-15T10:00:00", "2026-01-15T14:00:00", 20, org_id, now_iso()),
        )

    # Tasks
    tasks = [
        ("Подготовить пост для соцсетей", "Сделать текст и картинку-анонс для мероприятия.", 5),
        ("Позвонить партнерам", "Список контактов будет предоставлен.", 4),
    ]
    for name, desc, pts in tasks:
        db.execute(
            "INSERT INTO tasks(name,description,points,start_time,end_time,max_participants,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (name, desc, pts, "2026-01-10T09:00:00", "2026-01-20T18:00:00", 5, org_id, now_iso()),
        )

    db.commit()
