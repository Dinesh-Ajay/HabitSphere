"""HabitSphere infrastructure: MySQL, JSON settings, and central logging."""

from __future__ import annotations

import json
import logging
import hashlib
import hmac
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import mysql.connector
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from mysql.connector import Error


PROJECT_ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = PROJECT_ROOT / "json" / "settings.json"
SCHEMA_PATH = PROJECT_ROOT / "database" / "schema.sql"
LOG_PATH = PROJECT_ROOT / "logs" / "app.log"
REPORT_CSV_PATH = PROJECT_ROOT / "reports" / "csv"
REPORT_TXT_PATH = PROJECT_ROOT / "reports" / "txt"
CHART_PATH = PROJECT_ROOT / "static" / "charts"


class LoggerManager:
    """Creates the application logger once and writes it to logs/app.log."""

    @staticmethod
    def get_logger() -> logging.Logger:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("habitsphere")
        if not logger.handlers:
            logger.setLevel(logging.INFO)
            handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
            logger.addHandler(handler)
        return logger


logger = LoggerManager.get_logger()


class JSONManager:
    """Loads and saves the application's JSON settings safely."""

    def __init__(self, settings_path: Path = SETTINGS_PATH) -> None:
        self.settings_path = settings_path

    def load_settings(self) -> dict[str, Any]:
        try:
            with self.settings_path.open("r", encoding="utf-8") as settings_file:
                return json.load(settings_file)
        except (OSError, json.JSONDecodeError) as error:
            logger.exception("Unable to read settings")
            raise RuntimeError(f"Unable to read settings: {error}") from error

    def save_settings(self, settings: dict[str, Any]) -> None:
        try:
            with self.settings_path.open("w", encoding="utf-8") as settings_file:
                json.dump(settings, settings_file, indent=2)
            logger.info("Application settings saved")
        except OSError as error:
            logger.exception("Unable to save settings")
            raise RuntimeError(f"Unable to save settings: {error}") from error

    def preferences(self) -> dict[str, Any]:
        return self.load_settings()["preferences"]

    def update_preferences(self, updates: dict[str, Any]) -> dict[str, Any]:
        settings = self.load_settings(); preferences = settings["preferences"]
        theme = updates.get("theme", preferences["theme"])
        default_view = updates.get("default_dashboard_view", preferences["default_dashboard_view"])
        export_format = updates.get("export_format", preferences["export_format"])
        try:
            weekly_goal, monthly_goal = int(updates.get("weekly_goal", preferences["weekly_goal"])), int(updates.get("monthly_goal", preferences["monthly_goal"]))
        except (TypeError, ValueError) as error:
            logger.warning("Invalid settings goal value")
            raise ValueError("Goals must be whole numbers.") from error
        if theme not in {"light", "dark"} or default_view not in {"dashboard", "habits", "tracker", "insights", "reports"} or export_format not in {"csv", "txt"} or weekly_goal < 1 or monthly_goal < 1:
            logger.warning("Invalid settings update")
            raise ValueError("Invalid settings values.")
        preferences.update({"theme":theme,"default_dashboard_view":default_view,"weekly_goal":weekly_goal,"monthly_goal":monthly_goal,"export_format":export_format})
        self.save_settings(settings); logger.info("Application preferences updated")
        return preferences


class DatabaseManager:
    """Provides MySQL connections and initializes the approved schema."""

    def __init__(self, json_manager: JSONManager | None = None) -> None:
        self.json_manager = json_manager or JSONManager()
        self.config = self.json_manager.load_settings()["database"]

    def connect(self, include_database: bool = True):
        connection_config = self.config.copy()
        if not include_database:
            connection_config.pop("database", None)
        try:
            connection = mysql.connector.connect(**connection_config)
            if not connection.is_connected():
                raise ConnectionError("MySQL did not establish a connection.")
            return connection
        except (Error, ConnectionError) as error:
            logger.exception("Database connection failed")
            raise RuntimeError(f"Database connection failed: {error}") from error

    def test_connection(self) -> bool:
        connection = None
        try:
            connection = self.connect()
            return connection.is_connected()
        finally:
            if connection and connection.is_connected():
                connection.close()

    def initialize_schema(self) -> None:
        """Runs the exact schema file supplied for the project's ER diagram."""
        try:
            statements = SCHEMA_PATH.read_text(encoding="utf-8").split(";")
            connection = self.connect(include_database=False)
            cursor = connection.cursor()
            for statement in statements:
                if statement.strip():
                    cursor.execute(statement)
                    if "CREATE TABLE IF NOT EXISTS HABITS" in statement.upper():
                        self._ensure_habit_owner_index(cursor)
            connection.commit()
            cursor.close()
            connection.close()
            logger.info("MySQL schema initialized")
        except (OSError, Error, RuntimeError) as error:
            logger.exception("Schema initialization failed")
            raise RuntimeError(f"Schema initialization failed: {error}") from error

    @staticmethod
    def _ensure_habit_owner_index(cursor) -> None:
        """Creates the composite key needed to keep reminder ownership consistent."""
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = 'HABITS'
              AND index_name = 'uq_habit_owner'
            LIMIT 1
            """
        )
        if cursor.fetchone() is None:
            cursor.execute(
                "ALTER TABLE HABITS ADD UNIQUE INDEX uq_habit_owner (habit_id, user_id)"
            )


class PasswordManager:
    """Hashes passwords with PBKDF2 and verifies them without storing plaintext."""

    ITERATIONS = 310_000

    @classmethod
    def hash_password(cls, password: str) -> str:
        salt = secrets.token_bytes(16)
        password_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, cls.ITERATIONS)
        return f"pbkdf2_sha256${cls.ITERATIONS}${salt.hex()}${password_hash.hex()}"

    @classmethod
    def verify_password(cls, password: str, stored_hash: str) -> bool:
        try:
            algorithm, iterations, salt_hex, hash_hex = stored_hash.split("$")
            if algorithm != "pbkdf2_sha256":
                return False
            candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
            return hmac.compare_digest(candidate.hex(), hash_hex)
        except (ValueError, AttributeError):
            logger.warning("An invalid stored password hash was encountered")
            return False


@dataclass
class User:
    """Encapsulates user registration, authentication, and public profile data."""

    user_id: int
    full_name: str
    email: str
    created_at: datetime | None = None

    EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

    def public_data(self) -> dict[str, Any]:
        return {"user_id": self.user_id, "full_name": self.full_name, "email": self.email}

    @classmethod
    def validate_registration(cls, full_name: str, email: str, password: str) -> tuple[str, str]:
        cleaned_name, cleaned_email = full_name.strip(), email.strip().lower()
        if len(cleaned_name) < 2 or len(cleaned_name) > 100:
            raise ValueError("Full name must contain 2 to 100 characters.")
        if not cls.EMAIL_PATTERN.fullmatch(cleaned_email):
            raise ValueError("Enter a valid email address.")
        if len(password) < 8:
            raise ValueError("Password must contain at least 8 characters.")
        return cleaned_name, cleaned_email

    @classmethod
    def register(cls, full_name: str, email: str, password: str, database: DatabaseManager) -> "User":
        cleaned_name, cleaned_email = cls.validate_registration(full_name, email, password)
        connection = cursor = None
        try:
            connection = database.connect()
            cursor = connection.cursor()
            cursor.execute("INSERT INTO USERS (full_name, email, password) VALUES (%s, %s, %s)", (cleaned_name, cleaned_email, PasswordManager.hash_password(password)))
            connection.commit()
            user = cls(cursor.lastrowid, cleaned_name, cleaned_email)
            logger.info("Registered user_id=%s", user.user_id)
            return user
        except Error as error:
            if getattr(error, "errno", None) == 1062:
                logger.warning("Registration rejected for an existing email")
                raise ValueError("An account with this email already exists.") from error
            logger.exception("User registration failed")
            raise RuntimeError("Unable to create the account. Please try again.") from error
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()

    @classmethod
    def authenticate(cls, email: str, password: str, database: DatabaseManager) -> "User":
        cleaned_email = email.strip().lower()
        if not cleaned_email or not password:
            raise ValueError("Email and password are required.")
        connection = cursor = None
        try:
            connection = database.connect()
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT user_id, full_name, email, password, created_at FROM USERS WHERE email = %s", (cleaned_email,))
            record = cursor.fetchone()
            if not record or not PasswordManager.verify_password(password, record["password"]):
                logger.warning("Invalid login attempt for email=%s", cleaned_email)
                raise ValueError("Invalid email or password.")
            user = cls(record["user_id"], record["full_name"], record["email"], record["created_at"])
            logger.info("User logged in: user_id=%s", user.user_id)
            return user
        except Error as error:
            logger.exception("User authentication failed")
            raise RuntimeError("Unable to sign in. Please try again.") from error
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()


class SessionManager:
    """Maintains short-lived, server-side sessions for authenticated browser users."""

    def __init__(self, lifetime_hours: int = 8) -> None:
        self.lifetime = timedelta(hours=lifetime_hours)
        self.sessions: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def create_session(self, user: User) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions[token] = {"user": user.public_data(), "expires_at": datetime.now() + self.lifetime}
        logger.info("Created session for user_id=%s", user.user_id)
        return token

    def get_user(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self.lock:
            session = self.sessions.get(token)
            if not session:
                return None
            if session["expires_at"] <= datetime.now():
                del self.sessions[token]
                return None
            return session["user"]

    def revoke_session(self, token: str | None) -> None:
        if token:
            with self.lock:
                self.sessions.pop(token, None)
            logger.info("Session revoked")


class DashboardService:
    """Builds dashboard statistics from the authenticated user's MySQL records."""

    def __init__(self, database: DatabaseManager | None = None, json_manager: JSONManager | None = None) -> None:
        self.database = database or DatabaseManager()
        self.json_manager = json_manager or JSONManager()

    @staticmethod
    def _expected_completions(goal_type: str, target_count: int, start_date) -> int:
        days = max(1, (datetime.now().date() - start_date).days + 1)
        if goal_type == "weekly":
            periods = (days + 6) // 7
        elif goal_type == "monthly":
            periods = max(1, (datetime.now().year - start_date.year) * 12 + datetime.now().month - start_date.month + 1)
        else:
            periods = days
        return periods * target_count

    @staticmethod
    def _streaks(completion_dates: list) -> tuple[int, int]:
        if not completion_dates:
            return 0, 0
        date_set = set(completion_dates)
        cursor = datetime.now().date()
        if cursor not in date_set:
            cursor -= timedelta(days=1)
        current = 0
        while cursor in date_set:
            current += 1
            cursor -= timedelta(days=1)
        longest = run = 0
        previous = None
        for completion_date in sorted(date_set):
            run = run + 1 if previous and completion_date == previous + timedelta(days=1) else 1
            longest = max(longest, run)
            previous = completion_date
        return current, longest

    def get_statistics(self, user_id: int) -> dict[str, Any]:
        """Return zero-safe totals, streaks, and goal progress for one user."""
        connection = cursor = None
        try:
            connection = self.database.connect()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """UPDATE HABIT_COMPLETION hc INNER JOIN HABITS h ON h.habit_id = hc.habit_id
                   SET hc.completed = 0 WHERE h.user_id = %s AND hc.completed = 1
                   AND hc.completion_count < h.target_count""",
                (user_id,),
            )
            connection.commit()
            cursor.execute(
                """SELECT h.habit_id, h.goal_type, h.target_count, h.start_date, h.status,
                          COALESCE(SUM(CASE WHEN hc.completed = 1 THEN hc.completion_count ELSE 0 END), 0) AS completed_count
                   FROM HABITS h LEFT JOIN HABIT_COMPLETION hc ON h.habit_id = hc.habit_id
                   WHERE h.user_id = %s GROUP BY h.habit_id, h.goal_type, h.target_count, h.start_date, h.status""",
                (user_id,),
            )
            habits = cursor.fetchall()
            active_habits = [habit for habit in habits if habit["status"].lower() == "active"]
            expected = sum(self._expected_completions(habit["goal_type"], habit["target_count"], habit["start_date"]) for habit in active_habits)
            completed_total = sum(int(habit["completed_count"]) for habit in active_habits)

            cursor.execute(
                """SELECT COUNT(DISTINCT hc.habit_id) AS count FROM HABIT_COMPLETION hc
                   INNER JOIN HABITS h ON h.habit_id = hc.habit_id
                   WHERE h.user_id = %s AND h.status = 'active' AND hc.completed = 1 AND hc.completion_date = CURDATE()""",
                (user_id,),
            )
            today_completed = int(cursor.fetchone()["count"])

            cursor.execute(
                """SELECT hc.completion_date FROM HABIT_COMPLETION hc
                   INNER JOIN HABITS h ON h.habit_id = hc.habit_id
                   WHERE h.user_id = %s AND h.status = 'active' AND hc.completed = 1
                   GROUP BY hc.completion_date ORDER BY hc.completion_date""",
                (user_id,),
            )
            current_streak, longest_streak = self._streaks([row["completion_date"] for row in cursor.fetchall()])

            cursor.execute(
                """SELECT COUNT(*) AS count FROM (
                       SELECT hc.habit_id, hc.completion_date FROM HABIT_COMPLETION hc
                       INNER JOIN HABITS h ON h.habit_id = hc.habit_id
                       WHERE h.user_id = %s AND h.status = 'active' AND hc.completed = 1
                       AND YEARWEEK(hc.completion_date, 1) = YEARWEEK(CURDATE(), 1)
                       GROUP BY hc.habit_id, hc.completion_date
                   ) AS completed_items""",
                (user_id,),
            )
            weekly_completed = int(cursor.fetchone()["count"])
            cursor.execute(
                """SELECT COUNT(*) AS count FROM (
                       SELECT hc.habit_id, hc.completion_date FROM HABIT_COMPLETION hc
                       INNER JOIN HABITS h ON h.habit_id = hc.habit_id
                       WHERE h.user_id = %s AND h.status = 'active' AND hc.completed = 1
                       AND YEAR(hc.completion_date) = YEAR(CURDATE())
                       AND MONTH(hc.completion_date) = MONTH(CURDATE())
                       GROUP BY hc.habit_id, hc.completion_date
                   ) AS completed_items""",
                (user_id,),
            )
            monthly_completed = int(cursor.fetchone()["count"])
            cursor.execute(
                """SELECT hc.completion_date, COUNT(DISTINCT hc.habit_id) AS completed
                   FROM HABIT_COMPLETION hc INNER JOIN HABITS h ON h.habit_id = hc.habit_id
                   WHERE h.user_id = %s AND h.status = 'active' AND hc.completed = 1
                   AND hc.completion_date BETWEEN DATE_SUB(CURDATE(), INTERVAL 6 DAY) AND CURDATE()
                   GROUP BY hc.completion_date ORDER BY hc.completion_date""",
                (user_id,),
            )
            activity_map = {row["completion_date"]: int(row["completed"]) for row in cursor.fetchall()}
            weekly_activity = [
                {"date": (date.today() - timedelta(days=offset)).isoformat(), "day": (date.today() - timedelta(days=offset)).strftime("%a"), "completed": activity_map.get(date.today() - timedelta(days=offset), 0)}
                for offset in range(6, -1, -1)
            ]
            preferences = self.json_manager.load_settings()["preferences"]
            weekly_goal, monthly_goal = int(preferences["weekly_goal"]), int(preferences["monthly_goal"])
            statistics = {
                "total_habits": len(habits), "active_habits": len(active_habits), "today_completed": today_completed,
                "current_streak": current_streak, "longest_streak": longest_streak,
                "overall_completion_percentage": min(100, round(completed_total / expected * 100, 1)) if expected else 0,
                "today_completion_percentage": round(today_completed / len(active_habits) * 100) if active_habits else 0,
                "weekly_goal": {"completed": weekly_completed, "target": weekly_goal, "percentage": min(100, round(weekly_completed / weekly_goal * 100)) if weekly_goal else 0},
                "monthly_goal": {"completed": monthly_completed, "target": monthly_goal, "percentage": min(100, round(monthly_completed / monthly_goal * 100)) if monthly_goal else 0},
                "weekly_activity": weekly_activity,
            }
            logger.info("Dashboard statistics generated for user_id=%s", user_id)
            return statistics
        except (Error, KeyError, TypeError, ValueError) as error:
            logger.exception("Dashboard statistics failed for user_id=%s", user_id)
            raise RuntimeError("Unable to load dashboard data. Please try again.") from error
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()


class Habit:
    """Validates and manages HABITS records for a single authenticated user."""

    VALID_GOAL_TYPES = {"daily", "weekly", "monthly"}
    VALID_STATUSES = {"active", "inactive"}

    def __init__(self, database: DatabaseManager | None = None) -> None:
        self.database = database or DatabaseManager()

    @staticmethod
    def _serialize(record: dict[str, Any]) -> dict[str, Any]:
        for key in ("start_date", "created_at"):
            if record.get(key) and hasattr(record[key], "isoformat"):
                record[key] = record[key].isoformat()
        return record

    @classmethod
    def _validate(cls, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("habit_name", "")).strip()
        description = str(payload.get("description", "")).strip()
        category = str(payload.get("category", "")).strip()
        goal_type = str(payload.get("goal_type", "")).strip().lower()
        status = str(payload.get("status", "active")).strip().lower()
        try:
            target_count = int(payload.get("target_count", 0))
        except (TypeError, ValueError) as error:
            raise ValueError("Target count must be a whole number.") from error
        if not name or len(name) > 120:
            raise ValueError("Habit name is required and must be at most 120 characters.")
        if not category or len(category) > 80:
            raise ValueError("Category is required and must be at most 80 characters.")
        if len(description) > 1000:
            raise ValueError("Description must be at most 1000 characters.")
        if goal_type not in cls.VALID_GOAL_TYPES:
            raise ValueError("Goal type must be daily, weekly, or monthly.")
        if target_count < 1 or target_count > 1000:
            raise ValueError("Target count must be between 1 and 1000.")
        if status not in cls.VALID_STATUSES:
            raise ValueError("Status must be active or inactive.")
        return {"habit_name": name, "description": description, "category": category, "goal_type": goal_type, "target_count": target_count, "status": status}

    def _duplicate_exists(self, user_id: int, habit_name: str, habit_id: int | None = None) -> bool:
        connection = cursor = None
        try:
            connection = self.database.connect()
            cursor = connection.cursor()
            query = "SELECT habit_id FROM HABITS WHERE user_id = %s AND LOWER(habit_name) = LOWER(%s) AND status = 'active'"
            values: tuple[Any, ...] = (user_id, habit_name)
            if habit_id is not None:
                query += " AND habit_id <> %s"
                values += (habit_id,)
            cursor.execute(query, values)
            return cursor.fetchone() is not None
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()


class HabitTracker:
    """Records daily habit completion and exposes date-specific tracker records."""

    def __init__(self, database: DatabaseManager | None = None) -> None:
        self.database = database or DatabaseManager()

    @staticmethod
    def _parse_date(value: str) -> date:
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError) as error:
            raise ValueError("A valid completion date is required.") from error

    def get_day(self, user_id: int, requested_date: str) -> dict[str, Any]:
        selected_date = self._parse_date(requested_date)
        connection = cursor = None
        try:
            connection = self.database.connect(); cursor = connection.cursor(dictionary=True)
            cursor.execute("""SELECT h.habit_id, h.habit_name, h.category, h.goal_type, h.target_count, h.status,
                              hc.completed, hc.completion_count, hc.notes
                              FROM HABITS h LEFT JOIN HABIT_COMPLETION hc ON h.habit_id=hc.habit_id AND hc.completion_date=%s
                              WHERE h.user_id=%s AND h.status='active' ORDER BY h.habit_name""", (selected_date, user_id))
            habits = []
            for record in cursor.fetchall():
                count = int(record["completion_count"] or 0)
                state = "completed" if record["completed"] else ("incomplete" if count > 0 else ("upcoming" if selected_date > date.today() else "missed"))
                habits.append({**record, "completed": bool(record["completed"]), "completion_count": count, "notes": record["notes"] or "", "state": state})
            return {"date": selected_date.isoformat(), "habits": habits}
        except Error as error:
            logger.exception("Daily tracker retrieval failed")
            raise RuntimeError("Unable to load tracker data. Please try again.") from error
        finally:
            if cursor: cursor.close()
            if connection and connection.is_connected(): connection.close()

    def record_completion(self, user_id: int, payload: dict[str, Any]) -> None:
        try:
            habit_id = int(payload.get("habit_id", 0))
            completion_count = int(payload.get("completion_count", 0))
        except (TypeError, ValueError) as error:
            raise ValueError("Habit ID and completion count must be whole numbers.") from error

        completion_date = self._parse_date(payload.get("completion_date", ""))
        completed = bool(payload.get("completed", False))

        # Save the exact time when the habit reaches its target.
        completion_time = datetime.now().time() if completed else None

        notes = str(payload.get("notes", "")).strip()

        if habit_id < 1 or completion_count < 0 or completion_count > 1000:
            raise ValueError("Invalid completion values.")

        if completed and completion_count < 1:
            raise ValueError("Completed habits need a count of at least 1.")

        if len(notes) > 1000:
            raise ValueError("Notes must be at most 1000 characters.")

        connection = cursor = None

        try:
            connection = self.database.connect()
            cursor = connection.cursor()

            cursor.execute(
                """
                SELECT target_count
                FROM HABITS
                WHERE habit_id=%s
                AND user_id=%s
                AND status='active'
                """,
                (habit_id, user_id)
            )

            habit = cursor.fetchone()

            if not habit:
                raise ValueError("Habit not found or inactive.")

            target_count = int(habit[0])

            # A habit is completed only when the entered count
            # reaches the target.
            completed = completed and completion_count >= target_count

            # If the target was not actually reached,
            # there should be no completion time.
            completion_time = datetime.now().time() if completed else None

            cursor.execute(
                """
                SELECT completion_id
                FROM HABIT_COMPLETION
                WHERE habit_id=%s
                AND completion_date=%s
                """,
                (habit_id, completion_date)
            )

            existing = cursor.fetchone()

            if existing:
                cursor.execute(
                    """
                    UPDATE HABIT_COMPLETION
                    SET completed=%s,
                        completion_time=%s,
                        completion_count=%s,
                        notes=%s
                    WHERE completion_id=%s
                    """,
                    (
                        completed,
                        completion_time,
                        completion_count,
                        notes,
                        existing[0]
                    )
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO HABIT_COMPLETION
                    (
                        habit_id,
                        completion_date,
                        completion_time,
                        completed,
                        completion_count,
                        notes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        habit_id,
                        completion_date,
                        completion_time,
                        completed,
                        completion_count,
                        notes
                    )
                )

            connection.commit()

            logger.info(
                "Completion recorded: habit_id=%s user_id=%s completed=%s time=%s",
                habit_id,
                user_id,
                completed,
                completion_time
            )

        except Error as error:
            logger.exception("Completion recording failed")
            raise RuntimeError("Unable to save completion. Please try again.") from error

        finally:
            if cursor:
                cursor.close()

            if connection and connection.is_connected():
                connection.close()

class Analytics:
    """Calculates and persists habit performance metrics with Pandas-ready records."""

    def __init__(self, database: DatabaseManager | None = None) -> None: self.database = database or DatabaseManager()

    def _habit_rows(self, user_id: int) -> list[dict[str, Any]]:
        connection = cursor = None
        try:
            connection=self.database.connect(); cursor=connection.cursor(dictionary=True)
            cursor.execute("SELECT habit_id, habit_name, goal_type, target_count, start_date, status FROM HABITS WHERE user_id=%s", (user_id,)); return cursor.fetchall()
        finally:
            if cursor: cursor.close()
            if connection and connection.is_connected(): connection.close()

    def _completion_rows(self, habit_id: int) -> list[dict[str, Any]]:
        connection=cursor=None
        try:
            connection=self.database.connect(); cursor=connection.cursor(dictionary=True)
            cursor.execute("SELECT completion_date, completion_count FROM HABIT_COMPLETION WHERE habit_id=%s AND completed=1 ORDER BY completion_date", (habit_id,)); return cursor.fetchall()
        finally:
            if cursor: cursor.close()
            if connection and connection.is_connected(): connection.close()

    def calculate(self, user_id: int, period_days: int | None = None) -> list[dict[str, Any]]:
        results=[]
        for habit in self._habit_rows(user_id):
            completion_rows=self._completion_rows(habit["habit_id"])
            if period_days: completion_rows=[row for row in completion_rows if row["completion_date"] >= date.today()-timedelta(days=period_days-1)]
            dates=[row["completion_date"] for row in completion_rows]
            completed=sum(int(row["completion_count"] or 0) for row in completion_rows)
            if period_days:
                periods=period_days if habit["goal_type"]=="daily" else ((period_days+6)//7 if habit["goal_type"]=="weekly" else max(1,round(period_days/30)))
                expected=periods*int(habit["target_count"]); days=period_days
            else:
                expected=DashboardService._expected_completions(habit["goal_type"], int(habit["target_count"]), habit["start_date"]); days=max(1,(date.today()-habit["start_date"]).days+1)
            current,longest=DashboardService._streaks(dates)
            percentage=min(100,round(completed/expected*100,2)) if expected else 0
            result={"habit_id":habit["habit_id"],"habit_name":habit["habit_name"],"status":habit["status"],"completion_percentage":percentage,"current_streak":current,"longest_streak":longest,"consistency_score":round(len(set(dates))/days*100,2),"success_rate":percentage,"missed_days":max(0,days-len(set(dates)))}
            if not period_days: self._save(result)
            results.append(result)
        logger.info("Analytics calculated for user_id=%s", user_id); return results

    def _save(self, metric: dict[str, Any]) -> None:
        connection=cursor=None
        try:
            connection=self.database.connect(); cursor=connection.cursor()
            cursor.execute("SELECT analytics_id FROM HABIT_ANALYTICS WHERE habit_id=%s ORDER BY last_updated DESC LIMIT 1", (metric["habit_id"],)); existing=cursor.fetchone()
            values=(metric["completion_percentage"],metric["current_streak"],metric["longest_streak"],metric["consistency_score"],metric["success_rate"],date.today().isocalendar().week,date.today().month,metric["habit_id"])
            if existing: cursor.execute("UPDATE HABIT_ANALYTICS SET completion_percentage=%s,current_streak=%s,longest_streak=%s,consistency_score=%s,success_rate=%s,report_week=%s,report_month=%s,last_updated=NOW() WHERE analytics_id=(SELECT analytics_id FROM (SELECT analytics_id FROM HABIT_ANALYTICS WHERE habit_id=%s ORDER BY last_updated DESC LIMIT 1) x)", values)
            else: cursor.execute("INSERT INTO HABIT_ANALYTICS (habit_id,completion_percentage,current_streak,longest_streak,consistency_score,success_rate,report_week,report_month,last_updated) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())", (metric["habit_id"],)+values[:-1])
            connection.commit()
        finally:
            if cursor: cursor.close()
            if connection and connection.is_connected(): connection.close()

    def summary(self, user_id: int, period_days: int | None = None) -> dict[str, Any]:
        habits=self.calculate(user_id, period_days)
        if not habits: return {"habits":[],"overall_completion_percentage":0,"most_consistent":None,"least_consistent":None}
        return {"habits":habits,"overall_completion_percentage":round(sum(h["completion_percentage"] for h in habits)/len(habits),2),"most_consistent":max(habits,key=lambda h:h["consistency_score"]),"least_consistent":min(habits,key=lambda h:h["consistency_score"])}

    def create(self, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        habit = self._validate(payload)
        if habit["status"] == "active" and self._duplicate_exists(user_id, habit["habit_name"]):
            logger.warning("Duplicate active habit rejected for user_id=%s", user_id)
            raise ValueError("An active habit with this name already exists.")
        connection = cursor = None
        try:
            connection = self.database.connect()
            cursor = connection.cursor()
            cursor.execute(
                """INSERT INTO HABITS (user_id, habit_name, description, category, goal_type, target_count, start_date, status)
                   VALUES (%s, %s, %s, %s, %s, %s, CURDATE(), %s)""",
                (user_id, habit["habit_name"], habit["description"], habit["category"], habit["goal_type"], habit["target_count"], habit["status"]),
            )
            connection.commit()
            logger.info("Habit created: habit_id=%s user_id=%s", cursor.lastrowid, user_id)
            return self.get_by_id(user_id, cursor.lastrowid)
        except Error as error:
            logger.exception("Habit creation failed for user_id=%s", user_id)
            raise RuntimeError("Unable to create the habit. Please try again.") from error
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()

    def get_all(self, user_id: int, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        allowed_filters = {"search", "category", "goal_type", "status"}
        clauses, values = ["user_id = %s"], [user_id]
        if filters.get("search"):
            clauses.append("habit_name LIKE %s")
            values.append(f"%{filters['search'].strip()}%")
        for field in ("category", "goal_type", "status"):
            value = filters.get(field, "").strip().lower()
            if value:
                if field == "goal_type" and value not in self.VALID_GOAL_TYPES:
                    raise ValueError("Invalid goal type filter.")
                if field == "status" and value not in self.VALID_STATUSES:
                    raise ValueError("Invalid status filter.")
                clauses.append(f"LOWER({field}) = %s")
                values.append(value)
        connection = cursor = None
        try:
            connection = self.database.connect()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(f"SELECT habit_id, habit_name, description, category, goal_type, target_count, start_date, status, created_at FROM HABITS WHERE {' AND '.join(clauses)} ORDER BY created_at DESC", tuple(values))
            return [self._serialize(record) for record in cursor.fetchall()]
        except Error as error:
            logger.exception("Habit retrieval failed for user_id=%s", user_id)
            raise RuntimeError("Unable to load habits. Please try again.") from error
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()

    def get_by_id(self, user_id: int, habit_id: int) -> dict[str, Any]:
        if habit_id < 1:
            raise ValueError("Invalid habit ID.")
        records = self.get_all(user_id, {})
        for record in records:
            if record["habit_id"] == habit_id:
                return record
        raise ValueError("Habit not found.")

    def update(self, user_id: int, habit_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.get_by_id(user_id, habit_id)
        habit = self._validate(payload)
        if habit["status"] == "active" and self._duplicate_exists(user_id, habit["habit_name"], habit_id):
            logger.warning("Duplicate active habit update rejected for user_id=%s", user_id)
            raise ValueError("An active habit with this name already exists.")
        connection = cursor = None
        try:
            connection = self.database.connect()
            cursor = connection.cursor()
            cursor.execute("""UPDATE HABITS SET habit_name=%s, description=%s, category=%s, goal_type=%s, target_count=%s, status=%s
                            WHERE habit_id=%s AND user_id=%s""", (habit["habit_name"], habit["description"], habit["category"], habit["goal_type"], habit["target_count"], habit["status"], habit_id, user_id))
            connection.commit()
            logger.info("Habit updated: habit_id=%s user_id=%s", habit_id, user_id)
            return self.get_by_id(user_id, habit_id)
        except Error as error:
            logger.exception("Habit update failed for habit_id=%s", habit_id)
            raise RuntimeError("Unable to update the habit. Please try again.") from error
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()

    def delete(self, user_id: int, habit_id: int) -> None:
        self.get_by_id(user_id, habit_id)
        connection = cursor = None
        try:
            connection = self.database.connect()
            cursor = connection.cursor()
            cursor.execute("DELETE FROM HABITS WHERE habit_id = %s AND user_id = %s", (habit_id, user_id))
            connection.commit()
            logger.info("Habit deleted: habit_id=%s user_id=%s", habit_id, user_id)
        except Error as error:
            logger.exception("Habit deletion failed for habit_id=%s", habit_id)
            raise RuntimeError("Unable to delete the habit. Please try again.") from error
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()


# Keep the existing Habit CRUD public contract intact after adding analytics services.
for _method_name in ("create", "get_all", "get_by_id", "update", "delete"):
    setattr(Habit, _method_name, getattr(Analytics, _method_name))


class ReportGenerator:
    """Creates real CSV and TXT reports from the stored analytics records."""
    def __init__(self, analytics: Analytics | None = None) -> None: self.analytics = analytics or Analytics()

    def generate(self, user_id: int, period: str) -> dict[str, Any]:
        if period not in {"daily", "weekly", "monthly"}: raise ValueError("Report period must be daily, weekly, or monthly.")
        summary=self.analytics.summary(user_id); frame=pd.DataFrame(summary["habits"])
        REPORT_CSV_PATH.mkdir(parents=True, exist_ok=True); REPORT_TXT_PATH.mkdir(parents=True, exist_ok=True)
        stamp=date.today().isoformat(); base=f"user_{user_id}_{period}_{stamp}"; csv_file=REPORT_CSV_PATH/f"{base}.csv"; txt_file=REPORT_TXT_PATH/f"{base}.txt"
        if frame.empty: frame=pd.DataFrame(columns=["habit_name","completion_percentage","current_streak","longest_streak","consistency_score","success_rate","missed_days"])
        frame.to_csv(csv_file,index=False)
        text=f"HabitSphere {period.title()} Report\nGenerated: {datetime.now():%Y-%m-%d %H:%M}\nOverall completion: {summary['overall_completion_percentage']}%\n\n"+frame.to_string(index=False)
        txt_file.write_text(text,encoding="utf-8"); logger.info("%s report generated for user_id=%s",period,user_id)
        return {"period":period,"summary":summary,"csv_url":f"/reports/csv/{csv_file.name}","txt_url":f"/reports/txt/{txt_file.name}"}


class ChartGenerator:
    """Creates Matplotlib visualisations from current MySQL analytics data."""
    def __init__(self, analytics: Analytics | None = None, database: DatabaseManager | None = None) -> None:
        self.analytics=analytics or Analytics(); self.database=database or DatabaseManager()

    def generate(self, user_id: int) -> dict[str, str]:
        summary=self.analytics.summary(user_id); habits=summary["habits"]; CHART_PATH.mkdir(parents=True,exist_ok=True)
        names=[h["habit_name"] for h in habits] or ["No habits"]; percentages=[h["completion_percentage"] for h in habits] or [0]
        paths={key:CHART_PATH/f"user_{user_id}_{key}.png" for key in ("bar","pie","line","monthly")}
        plt.figure(figsize=(7,3.5)); plt.bar(names,percentages,color="#4d9ce8"); plt.ylim(0,100); plt.ylabel("Completion %"); plt.title("Habit completion performance"); plt.xticks(rotation=25,ha="right"); plt.tight_layout(); plt.savefig(paths["bar"],dpi=140); plt.close()
        completed=sum(percentages); plt.figure(figsize=(5,3.5)); plt.pie([completed,max(0,len(habits)*100-completed)] if habits else [1],labels=["Completed","Missed"] if habits else ["No data"],autopct="%1.0f%%" if habits else None,colors=["#45b99a","#edf0f5"]); plt.title("Completed vs missed"); plt.tight_layout(); plt.savefig(paths["pie"],dpi=140); plt.close()
        connection=cursor=None
        try:
            connection=self.database.connect(); cursor=connection.cursor(dictionary=True)
            cursor.execute("""SELECT hc.completion_date, COALESCE(SUM(hc.completion_count),0) completed FROM HABIT_COMPLETION hc JOIN HABITS h ON h.habit_id=hc.habit_id WHERE h.user_id=%s AND hc.completed=1 AND hc.completion_date BETWEEN DATE_SUB(CURDATE(),INTERVAL 6 DAY) AND CURDATE() GROUP BY hc.completion_date ORDER BY hc.completion_date""",(user_id,)); weekly={r["completion_date"].isoformat():int(r["completed"]) for r in cursor.fetchall()}
            cursor.execute("""SELECT DATE_FORMAT(hc.completion_date,'%Y-%m') month, COALESCE(SUM(hc.completion_count),0) completed FROM HABIT_COMPLETION hc JOIN HABITS h ON h.habit_id=hc.habit_id WHERE h.user_id=%s AND hc.completed=1 AND hc.completion_date BETWEEN DATE_SUB(CURDATE(),INTERVAL 5 MONTH) AND CURDATE() GROUP BY month ORDER BY month""",(user_id,)); monthly=cursor.fetchall()
        finally:
            if cursor: cursor.close()
            if connection and connection.is_connected(): connection.close()
        days=[date.today()-timedelta(days=i) for i in range(6,-1,-1)]; plt.figure(figsize=(7,3.5)); plt.plot([d.strftime("%a") for d in days],[weekly.get(d.isoformat(),0) for d in days],marker="o",color="#7057e9"); plt.title("Weekly progress"); plt.ylabel("Completions"); plt.grid(axis="y",alpha=.25); plt.tight_layout(); plt.savefig(paths["line"],dpi=140); plt.close()
        monthly_map={row["month"]:int(row["completed"]) for row in monthly}; months=[]
        for offset in range(5,-1,-1):
            month_index=date.today().year*12+date.today().month-1-offset; months.append(f"{month_index//12:04d}-{month_index%12+1:02d}")
        labels=months; values=[monthly_map.get(month,0) for month in months]; plt.figure(figsize=(7,3.5)); plt.plot(labels,values,marker="o",color="#f39b58"); plt.title("Monthly trend"); plt.ylabel("Completions"); plt.ylim(bottom=0); plt.grid(axis="y",alpha=.25); plt.xticks(rotation=25,ha="right"); plt.tight_layout(); plt.savefig(paths["monthly"],dpi=140); plt.close(); logger.info("Charts generated for user_id=%s",user_id)
        return {key:f"/static/charts/{path.name}?v={int(datetime.now().timestamp())}" for key,path in paths.items()}
