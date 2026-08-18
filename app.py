"""HabitSphere's framework-free HTTP server and JSON API foundation."""

from __future__ import annotations

import json
from datetime import date
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from configuration import load_environment

load_environment()

from habit_tracker import Analytics, ChartGenerator, DashboardService, DatabaseManager, Habit, HabitTracker, JSONManager, ReportGenerator, SessionManager, User, logger
from reminder_scheduler import ReminderScheduler


PROJECT_ROOT = Path(__file__).resolve().parent
SESSION_COOKIE = "habitsphere_session"
session_manager = SessionManager()
habit_manager = Habit()
tracker_manager = HabitTracker()
analytics_manager = Analytics()
report_generator = ReportGenerator(analytics_manager)
chart_generator = ChartGenerator(analytics_manager)


class HabitSphereRequestHandler(SimpleHTTPRequestHandler):
    """Serves the SPA and provides a JSON endpoint without a web framework."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

    def end_headers(self) -> None:
        """Avoid serving stale SPA JavaScript or CSS while the local server is running."""
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK, cookie: str | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 10_000:
                raise ValueError("Request data is invalid.")
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Request data is invalid.")
            return payload
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Request data is invalid.") from error

    def session_token(self) -> str | None:
        cookie = SimpleCookie(self.headers.get("Cookie"))
        value = cookie.get(SESSION_COOKIE)
        return value.value if value else None

    def authenticated_user(self) -> dict | None:
        return session_manager.get_user(self.session_token())

    def habit_id_from_path(self, path: str) -> int:
        try:
            return int(path.rsplit("/", 1)[-1])
        except ValueError as error:
            raise ValueError("Invalid habit ID.") from error

    @staticmethod
    def session_cookie(token: str, max_age: int = 28_800) -> str:
        return f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path == "/api/health":
            try:
                connected = DatabaseManager().test_connection()
                self.send_json({"success": True, "database_connected": connected})
            except RuntimeError as error:
                logger.error("Health endpoint database check failed: %s", error)
                self.send_json({"success": False, "message": str(error)}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if path == "/api/auth/me":
            user = session_manager.get_user(self.session_token())
            if not user:
                self.send_json({"success": False, "message": "Authentication required."}, HTTPStatus.UNAUTHORIZED)
                return
            self.send_json({"success": True, "user": user})
            return
        if path == "/api/dashboard":
            user = session_manager.get_user(self.session_token())
            if not user:
                self.send_json({"success": False, "message": "Authentication required."}, HTTPStatus.UNAUTHORIZED)
                return
            try:
                statistics = DashboardService().get_statistics(user["user_id"])
                self.send_json({"success": True, "dashboard": statistics})
            except RuntimeError as error:
                logger.error("Dashboard endpoint failed: %s", error)
                self.send_json({"success": False, "message": str(error)}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if path in {"/api/tracker", "/api/analytics", "/api/charts"}:
            user = self.authenticated_user()
            if not user:
                self.send_json({"success": False, "message": "Authentication required."}, HTTPStatus.UNAUTHORIZED); return
            try:
                if path == "/api/tracker": self.send_json({"success": True, "tracker": tracker_manager.get_day(user["user_id"], parse_qs(parsed_url.query).get("date", [date.today().isoformat()])[0])})
                elif path == "/api/analytics":
                    period = int(parse_qs(parsed_url.query).get("period", ["0"])[0] or 0)
                    if period not in {0, 7, 30}: raise ValueError("Analytics period must be 7 or 30 days.")
                    self.send_json({"success": True, "analytics": analytics_manager.summary(user["user_id"], period or None)})
                else: self.send_json({"success": True, "charts": chart_generator.generate(user["user_id"])})
            except (ValueError, RuntimeError) as error:
                logger.error("Analytics/tracker request failed: %s", error); self.send_json({"success": False, "message": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/settings":
            if not self.authenticated_user(): self.send_json({"success":False,"message":"Authentication required."},HTTPStatus.UNAUTHORIZED); return
            self.send_json({"success":True,"preferences":JSONManager().preferences()}); return
        if path == "/api/habits" or path.startswith("/api/habits/"):
            user = self.authenticated_user()
            if not user:
                self.send_json({"success": False, "message": "Authentication required."}, HTTPStatus.UNAUTHORIZED)
                return
            try:
                if path == "/api/habits":
                    query = parse_qs(parsed_url.query)
                    filters = {key: values[0] for key, values in query.items() if key in {"search", "category", "goal_type", "status"} and values}
                    self.send_json({"success": True, "habits": habit_manager.get_all(user["user_id"], filters)})
                else:
                    self.send_json({"success": True, "habit": habit_manager.get_by_id(user["user_id"], self.habit_id_from_path(path))})
            except ValueError as error:
                self.send_json({"success": False, "message": str(error)}, HTTPStatus.BAD_REQUEST)
            except RuntimeError as error:
                logger.error("Habit retrieval request failed: %s", error)
                self.send_json({"success": False, "message": str(error)}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/auth/register":
                payload = self.read_json_body()
                user = User.register(payload.get("full_name", ""), payload.get("email", ""), payload.get("password", ""), DatabaseManager())
                self.send_json({"success": True, "message": "Registration successful. Please sign in.", "user": user.public_data()}, HTTPStatus.CREATED)
                return
            if path == "/api/auth/login":
                payload = self.read_json_body()
                user = User.authenticate(payload.get("email", ""), payload.get("password", ""), DatabaseManager())
                token = session_manager.create_session(user)
                self.send_json({"success": True, "message": "Login successful.", "user": user.public_data()}, cookie=self.session_cookie(token))
                return
            if path == "/api/auth/logout":
                session_manager.revoke_session(self.session_token())
                self.send_json({"success": True, "message": "You have been logged out."}, cookie=self.session_cookie("", 0))
                return
            if path == "/api/habits":
                user = self.authenticated_user()
                if not user:
                    self.send_json({"success": False, "message": "Authentication required."}, HTTPStatus.UNAUTHORIZED)
                    return
                habit = habit_manager.create(user["user_id"], self.read_json_body())
                self.send_json({"success": True, "message": "Habit created.", "habit": habit}, HTTPStatus.CREATED)
                return
            if path in {"/api/tracker/completions", "/api/reports", "/api/charts"}:
                user = self.authenticated_user()
                if not user:
                    self.send_json({"success": False, "message": "Authentication required."}, HTTPStatus.UNAUTHORIZED); return
                payload = self.read_json_body()
                if path == "/api/tracker/completions":
                    tracker_manager.record_completion(user["user_id"], payload); analytics_manager.calculate(user["user_id"]); self.send_json({"success": True, "message": "Completion saved."})
                elif path == "/api/reports": self.send_json({"success": True, "report": report_generator.generate(user["user_id"], payload.get("period", ""))})
                else: self.send_json({"success": True, "charts": chart_generator.generate(user["user_id"])})
                return
            self.send_json({"success": False, "message": "Endpoint not found."}, HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_json({"success": False, "message": str(error)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as error:
            logger.error("Authentication request failed: %s", error)
            self.send_json({"success": False, "message": str(error)}, HTTPStatus.SERVICE_UNAVAILABLE)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        try:
            user = self.authenticated_user()
            if not user:
                self.send_json({"success": False, "message": "Authentication required."}, HTTPStatus.UNAUTHORIZED)
                return
            if not path.startswith("/api/habits/"):
                if path == "/api/settings":
                    preferences=JSONManager().update_preferences(self.read_json_body()); self.send_json({"success":True,"message":"Settings saved.","preferences":preferences}); return
                self.send_json({"success": False, "message": "Endpoint not found."}, HTTPStatus.NOT_FOUND); return
            habit = habit_manager.update(user["user_id"], self.habit_id_from_path(path), self.read_json_body())
            self.send_json({"success": True, "message": "Habit updated.", "habit": habit})
        except ValueError as error:
            self.send_json({"success": False, "message": str(error)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as error:
            logger.error("Habit update request failed: %s", error)
            self.send_json({"success": False, "message": str(error)}, HTTPStatus.SERVICE_UNAVAILABLE)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        try:
            user = self.authenticated_user()
            if not user:
                self.send_json({"success": False, "message": "Authentication required."}, HTTPStatus.UNAUTHORIZED)
                return
            if not path.startswith("/api/habits/"):
                self.send_json({"success": False, "message": "Endpoint not found."}, HTTPStatus.NOT_FOUND)
                return
            habit_manager.delete(user["user_id"], self.habit_id_from_path(path))
            self.send_json({"success": True, "message": "Habit deleted."})
        except ValueError as error:
            self.send_json({"success": False, "message": str(error)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as error:
            logger.error("Habit deletion request failed: %s", error)
            self.send_json({"success": False, "message": str(error)}, HTTPStatus.SERVICE_UNAVAILABLE)


def run_server() -> None:
    settings = JSONManager().load_settings()["server"]
    server = ThreadingHTTPServer((settings["host"], settings["port"]), HabitSphereRequestHandler)
    reminder_scheduler = ReminderScheduler()
    reminder_scheduler.start()
    logger.info("HabitSphere server started at http://%s:%s", settings["host"], settings["port"])
    print(f"HabitSphere is running at http://{settings['host']}:{settings['port']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("HabitSphere server stopped")
    finally:
        reminder_scheduler.stop()
        server.server_close()


if __name__ == "__main__":
    run_server()
