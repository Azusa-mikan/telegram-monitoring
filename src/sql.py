import aiosqlite
from .log import sql_log
from .i18n import itr

__all__ = [
    "get_user_db",
    "get_all_user_db",
    "add_user_db",
    "update_user_db",
    "del_user_db",
    "check_allow_user_db",
    "add_allow_user_db",
    "check_ban_user_db",
    "add_ban_user_db",
    "list_allow_user_db",
    "list_ban_user_db",
    "del_allow_user_db",
    "del_ban_user_db",
]

db_file = "data.db"
_con: aiosqlite.Connection | None = None

async def get_con() -> aiosqlite.Connection:
    global _con
    if _con is None:
        _con = await aiosqlite.connect(db_file)
        await _con.execute("PRAGMA journal_mode=WAL")
        await _con.execute("PRAGMA synchronous=NORMAL")
        sql_log.info(itr.sqlite.connected)
    return _con

async def init_db() -> None:
    con = await get_con()
    await con.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            full_name TEXT NOT NULL
        )
        """
    )
    await con.execute(
        """
        CREATE TABLE IF NOT EXISTS allow_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL
        )
        """
    )
    await con.execute(
        """
        CREATE TABLE IF NOT EXISTS ban_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL
        )
        """
    )
    await con.commit()
    return

async def get_user_db(user_id: int) -> tuple[int, str, str] | None:
    con = await get_con()
    cur = await con.execute(
        """
        SELECT user_id, username, full_name FROM users WHERE user_id = :user_id
        """,
        {"user_id": user_id}
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return row[0], row[1], row[2]

async def get_all_user_db() -> list[tuple[int, str, str]]:
    con = await get_con()
    cur = await con.execute(
        """
        SELECT user_id, username, full_name FROM users
        """
    )
    return await cur.fetchall()  # type: ignore

async def add_user_db(user_id: int, username: str | None, full_name: str) -> bool:
    con = await get_con()
    users = await get_user_db(user_id)
    if users is not None:
        return False
    await con.execute(
        """
        INSERT OR IGNORE INTO users (user_id, username, full_name)
        VALUES (:user_id, :username, :full_name)
        """,
        {"user_id": user_id, "username": username, "full_name": full_name}
    )
    await con.commit()
    sql_log.info(itr.sqlite.add_user.format(user=user_id))
    return True

async def update_user_db(user_id: int, username: str | None, full_name: str) -> None:
    con = await get_con()
    users = await get_user_db(user_id)
    if users is None:
        return
    else:
        _, old_username, old_full_name = users
        if username == old_username and full_name == old_full_name:
            return
    await con.execute(
        """
        UPDATE users SET username = :username, full_name = :full_name WHERE user_id = :user_id
        """,
        {"user_id": user_id, "username": username, "full_name": full_name}
    )
    await con.commit()
    sql_log.info(itr.sqlite.update_user.format(user=user_id))
    return

async def del_user_db(user_id: int) -> None:
    con = await get_con()
    await con.execute(
        """
        DELETE FROM users WHERE user_id = :user_id
        """,
        {"user_id": user_id}
    )
    await con.commit()
    sql_log.info(itr.sqlite.del_user.format(user=user_id))
    return

async def check_allow_user_db(user_id: int) -> bool:
    con = await get_con()
    cur = await con.execute(
        """
        SELECT user_id FROM allow_users WHERE user_id = :user_id
        """,
        {"user_id": user_id}
    )
    row = await cur.fetchone()
    if row is None:
        return False
    return True

async def add_allow_user_db(user_id: int) -> bool:
    con = await get_con()
    users = await get_user_db(user_id)
    if users is None:
        return False
    await con.execute(
        """
        INSERT OR IGNORE INTO allow_users (user_id)
        VALUES (:user_id)
        """,
        {"user_id": user_id}
    )
    await con.commit()
    sql_log.info(itr.sqlite.add_allow_user.format(user=user_id))
    return True

async def check_ban_user_db(user_id: int) -> bool:
    con = await get_con()
    cur = await con.execute(
        """
        SELECT user_id FROM ban_users WHERE user_id = :user_id
        """,
        {"user_id": user_id}
    )
    row = await cur.fetchone()
    if row is None:
        return False
    return True

async def add_ban_user_db(user_id: int) -> bool:
    con = await get_con()
    users = await get_user_db(user_id)
    if users is None:
        return False
    await con.execute(
        """
        INSERT OR IGNORE INTO ban_users (user_id)
        VALUES (:user_id)
        """,
        {"user_id": user_id}
    )
    await con.commit()
    sql_log.info(itr.sqlite.add_ban_user.format(user=user_id))
    return True

async def list_allow_user_db() -> list[tuple[int, str, str]]:
    con = await get_con()
    cur = await con.execute(
        """
        SELECT allow_users.user_id, users.username, users.full_name FROM allow_users
        INNER JOIN users ON allow_users.user_id = users.user_id
        """
    ) # ON 的作用是将 allow_users 表和 users 表连接起来，根据 user_id 进行匹配
    # 等于 SELECT allow_users.user_id, users.username, users.full_name FROM allow_users, users WHERE allow_users.user_id = users.user_id
    return await cur.fetchall()  # type: ignore

async def list_ban_user_db() -> list[tuple[int, str, str]]:
    con = await get_con()
    cur = await con.execute(
        """
        SELECT ban_users.user_id, users.username, users.full_name FROM ban_users
        INNER JOIN users ON ban_users.user_id = users.user_id
        """
    )
    return await cur.fetchall()  # type: ignore

async def del_allow_user_db(user_id: int) -> bool:
    con = await get_con()
    users = await get_user_db(user_id)
    if users is None:
        return False
    await con.execute(
        """
        DELETE FROM allow_users WHERE user_id = :user_id
        """,
        {"user_id": user_id}
    )
    await con.commit()
    sql_log.info(itr.sqlite.del_allow_user.format(user=user_id))
    return True

async def del_ban_user_db(user_id: int) -> bool:
    con = await get_con()
    users = await get_user_db(user_id)
    if users is None:
        return False
    await con.execute(
        """
        DELETE FROM ban_users WHERE user_id = :user_id
        """,
        {"user_id": user_id}
    )
    await con.commit()
    sql_log.info(itr.sqlite.del_ban_user.format(user=user_id))
    return True

async def close_con():
    global _con
    if _con is not None:
        await _con.close()
        _con = None
        sql_log.info(itr.sqlite.close)
