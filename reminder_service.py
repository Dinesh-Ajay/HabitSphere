"""Manual daily habit reminder service for HabitSphere.

This module deliberately does not start a scheduler or background worker. A
future scheduler can explicitly call ``ReminderService.check_pending_daily_habits``.
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from html import escape as escape_html
from typing import Any, Protocol

from mysql.connector import Error

from habit_tracker import DatabaseManager, User, logger


DEFAULT_APP_BASE_URL = "http://127.0.0.1:8000"


class EmailSender(Protocol):
    """Defines the dependency used by the reminder service to deliver email."""

    def send(self, recipient: str, subject: str, plain_text_body: str, html_body: str) -> bool:
        """Returns True only after a message is accepted by the SMTP server."""


@dataclass(frozen=True)
class SMTPConfiguration:
    """Loads the SMTP values without persisting or logging secrets."""

    server: str
    port: int
    sender_email: str
    sender_password: str

    @classmethod
    def from_environment(cls) -> "SMTPConfiguration":
        server = os.getenv("SMTP_SERVER", "").strip()
        port_value = os.getenv("SMTP_PORT", "").strip()
        sender_email = os.getenv("SENDER_EMAIL", "").strip()
        sender_password = os.getenv("SENDER_PASSWORD", "")

        if not all((server, port_value, sender_email, sender_password)):
            raise ValueError("SMTP configuration is incomplete.")
        if not User.EMAIL_PATTERN.fullmatch(sender_email):
            raise ValueError("The sender email address is invalid.")
        try:
            port = int(port_value)
        except ValueError as error:
            raise ValueError("SMTP port must be a whole number.") from error
        if not 1 <= port <= 65535:
            raise ValueError("SMTP port is outside the valid range.")

        return cls(server, port, sender_email, sender_password)


class SMTPEmailSender:
    """Delivers a multipart (plain-text + HTML) message through configured SMTP."""

    def send(self, recipient: str, subject: str, plain_text_body: str, html_body: str) -> bool:
        if not User.EMAIL_PATTERN.fullmatch(recipient.strip()):
            logger.warning("Reminder skipped because the recipient email is invalid")
            return False

        try:
            config = SMTPConfiguration.from_environment()
        except ValueError as error:
            logger.error("Reminder SMTP configuration error: %s", error)
            return False

        message = EmailMessage()
        message["From"] = config.sender_email
        message["To"] = recipient.strip()
        message["Subject"] = subject
        message.set_content(plain_text_body)
        message.add_alternative(html_body, subtype="html")

        try:
            with smtplib.SMTP(config.server, config.port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(config.sender_email, config.sender_password)
                server.send_message(message)
            return True
        except (OSError, smtplib.SMTPException) as error:
            logger.error("Reminder email delivery failed: %s", error)
            return False


class ReminderService:
    """Finds pending daily habits and records only successfully sent reminders."""

    # HabitSphere website design system (see static/css/style.css :root).
    _INK = "#25243a"
    _MUTED = "#82819a"
    _LINE = "#e9e8f0"
    _PAGE = "#f7f7fb"
    _CARD = "#ffffff"
    _VIOLET = "#7057e9"
    _VIOLET_LIGHT = "#f0edff"
    _VIOLET_DARK = "#6c52e6"
    _VIOLET_LIGHT_GRADIENT_END = "#9679ef"

    # Deterministic, code-only accent rotation for habit category dots.
    # No new database column or persisted mapping is introduced.
    _ACCENT_COLORS: tuple[str, ...] = ("#7057e9", "#f39b58", "#4d9ce8", "#45b99a")

    def __init__(
        self,
        database: DatabaseManager | None = None,
        email_sender: EmailSender | None = None,
        app_base_url: str | None = None,
    ) -> None:
        self.database = database or DatabaseManager()
        self.email_sender = email_sender or SMTPEmailSender()
        self.app_base_url = (app_base_url or os.getenv("APP_BASE_URL", DEFAULT_APP_BASE_URL)).rstrip("/")

    @staticmethod
    def _parse_check_date(check_date: date | str) -> date:
        if isinstance(check_date, datetime):
            return check_date.date()
        if isinstance(check_date, date):
            return check_date
        try:
            return date.fromisoformat(check_date)
        except (TypeError, ValueError) as error:
            raise ValueError("check_date must be a valid ISO date.") from error

    def _find_pending_daily_habits(
        self, check_date: date, user_ids: list[int] | None = None
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Returns unsent pending habits, examined count, and already-sent count."""
        connection = cursor = None
        try:
            connection = self.database.connect()
            cursor = connection.cursor(dictionary=True)
            query = """
                SELECT
                    h.habit_id,
                    h.user_id,
                    h.habit_name,
                    h.category,
                    h.goal_type,
                    h.target_count,
                    u.full_name,
                    u.email,
                    COALESCE(hc.completion_count, 0) AS completion_count,
                    COALESCE(hc.completed, 0) AS completed,
                    hr.reminder_id
                FROM HABITS AS h
                INNER JOIN USERS AS u ON u.user_id = h.user_id
                LEFT JOIN HABIT_COMPLETION AS hc
                    ON hc.habit_id = h.habit_id
                    AND hc.completion_date = %s
                LEFT JOIN HABIT_REMINDERS AS hr
                    ON hr.habit_id = h.habit_id
                    AND hr.reminder_type = 'daily'
                    AND hr.reminder_date = %s
                WHERE h.goal_type = 'daily'
                    AND h.status = 'active'
                    AND h.start_date <= %s
            """
            parameters: list[Any] = [check_date, check_date, check_date]
            if user_ids is not None:
                valid_user_ids = sorted({int(user_id) for user_id in user_ids if int(user_id) > 0})
                if not valid_user_ids:
                    return [], 0, 0
                query += " AND h.user_id IN (" + ", ".join(["%s"] * len(valid_user_ids)) + ")"
                parameters.extend(valid_user_ids)
            query += " ORDER BY h.user_id, h.habit_id"
            cursor.execute(query, parameters)

            examined = 0
            already_sent = 0
            pending_habits: list[dict[str, Any]] = []
            for habit in cursor.fetchall():
                examined += 1
                target_count = int(habit["target_count"])
                completion_count = int(habit["completion_count"] or 0)
                is_completed = bool(habit["completed"]) and completion_count >= target_count
                if is_completed:
                    continue
                if habit["reminder_id"] is not None:
                    already_sent += 1
                    logger.info(
                        "Reminder skipped because it was already sent: habit_id=%s check_date=%s",
                        habit["habit_id"],
                        check_date,
                    )
                    continue

                habit["target_count"] = target_count
                habit["completion_count"] = completion_count
                pending_habits.append(habit)

            return pending_habits, examined, already_sent
        except Error as error:
            logger.exception("Reminder pending-habit query failed for check_date=%s", check_date)
            raise RuntimeError("Unable to retrieve pending daily habits.") from error
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()

    def _find_pending_weekly_habits(
        self, check_date: date, user_ids: list[int] | None = None
    ) -> tuple[list[dict[str, Any]], int, int]:
        """
        Returns unsent pending weekly habits for the week containing check_date.

        Weekly progress is calculated as the sum of completion_count values
        from Monday through check_date.

        A weekly reminder is considered already sent when a
        HABIT_REMINDERS row exists for the same habit, reminder_type='weekly',
        and reminder_date=check_date.
        """
        connection = cursor = None

        try:
            connection = self.database.connect()
            cursor = connection.cursor(dictionary=True)

            week_start = check_date - timedelta(days=check_date.weekday())

            query = """
                SELECT
                    h.habit_id,
                    h.user_id,
                    h.habit_name,
                    h.category,
                    h.goal_type,
                    h.target_count,
                    h.start_date,
                    u.full_name,
                    u.email,
                    COALESCE(SUM(
                        CASE
                            WHEN hc.completed = 1
                            THEN hc.completion_count
                            ELSE 0
                        END
                    ), 0) AS completion_count,
                    hr.reminder_id
                FROM HABITS AS h
                INNER JOIN USERS AS u
                    ON u.user_id = h.user_id
                LEFT JOIN HABIT_COMPLETION AS hc
                    ON hc.habit_id = h.habit_id
                    AND hc.completion_date >= %s
                    AND hc.completion_date <= %s
                LEFT JOIN HABIT_REMINDERS AS hr
                    ON hr.habit_id = h.habit_id
                    AND hr.reminder_type = 'weekly'
                    AND hr.reminder_date = %s
                WHERE h.goal_type = 'weekly'
                    AND h.status = 'active'
                    AND h.start_date <= %s
            """

            parameters: list[Any] = [
                week_start,
                check_date,
                check_date,
                check_date,
            ]

            if user_ids is not None:
                valid_user_ids = sorted(
                    {
                        int(user_id)
                        for user_id in user_ids
                        if int(user_id) > 0
                    }
                )

                if not valid_user_ids:
                    return [], 0, 0

                query += (
                    " AND h.user_id IN ("
                    + ", ".join(["%s"] * len(valid_user_ids))
                    + ")"
                )
                parameters.extend(valid_user_ids)

            query += """
                GROUP BY
                    h.habit_id,
                    h.user_id,
                    h.habit_name,
                    h.category,
                    h.goal_type,
                    h.target_count,
                    h.start_date,
                    u.full_name,
                    u.email,
                    hr.reminder_id
                ORDER BY h.user_id, h.habit_id
            """

            cursor.execute(query, parameters)

            examined = 0
            already_sent = 0
            pending_habits: list[dict[str, Any]] = []

            for habit in cursor.fetchall():
                examined += 1

                target_count = int(habit["target_count"])
                completion_count = int(habit["completion_count"] or 0)

                is_completed = completion_count >= target_count

                if is_completed:
                    continue

                if habit["reminder_id"] is not None:
                    already_sent += 1

                    logger.info(
                        "Weekly reminder skipped because it was already sent: "
                        "habit_id=%s check_date=%s",
                        habit["habit_id"],
                        check_date,
                    )
                    continue

                habit["target_count"] = target_count
                habit["completion_count"] = completion_count
                habit["week_start"] = week_start
                habit["week_end"] = check_date

                pending_habits.append(habit)

            return pending_habits, examined, already_sent

        except Error as error:
            logger.exception(
                "Weekly reminder pending-habit query failed for check_date=%s",
                check_date,
            )
            raise RuntimeError(
                "Unable to retrieve pending weekly habits."
            ) from error

        finally:
            if cursor:
                cursor.close()

            if connection and connection.is_connected():
                connection.close()

    def _find_pending_monthly_habits(
        self, check_date: date, user_ids: list[int] | None = None
    ) -> tuple[list[dict[str, Any]], int, int]:
        """
        Returns unsent pending monthly habits for the month containing check_date.

        Monthly progress is calculated as the sum of completion_count values
        from the first day of the month through check_date.

        A monthly reminder is considered already sent when a
        HABIT_REMINDERS row exists for the same habit, reminder_type='monthly',
        and reminder_date=check_date.
        """
        connection = cursor = None

        try:
            connection = self.database.connect()
            cursor = connection.cursor(dictionary=True)

            month_start = check_date.replace(day=1)

            query = """
                SELECT
                    h.habit_id,
                    h.user_id,
                    h.habit_name,
                    h.category,
                    h.goal_type,
                    h.target_count,
                    h.start_date,
                    u.full_name,
                    u.email,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN hc.completed = 1
                                THEN hc.completion_count
                                ELSE 0
                            END
                        ),
                        0
                    ) AS completion_count,
                    hr.reminder_id
                FROM HABITS AS h
                INNER JOIN USERS AS u
                    ON u.user_id = h.user_id
                LEFT JOIN HABIT_COMPLETION AS hc
                    ON hc.habit_id = h.habit_id
                    AND hc.completion_date >= %s
                    AND hc.completion_date <= %s
                LEFT JOIN HABIT_REMINDERS AS hr
                    ON hr.habit_id = h.habit_id
                    AND hr.reminder_type = 'monthly'
                    AND hr.reminder_date = %s
                WHERE h.goal_type = 'monthly'
                    AND h.status = 'active'
                    AND h.start_date <= %s
            """

            parameters: list[Any] = [
                month_start,
                check_date,
                check_date,
                check_date,
            ]

            if user_ids is not None:
                valid_user_ids = sorted(
                    {
                        int(user_id)
                        for user_id in user_ids
                        if int(user_id) > 0
                    }
                )

                if not valid_user_ids:
                    return [], 0, 0

                query += (
                    " AND h.user_id IN ("
                    + ", ".join(["%s"] * len(valid_user_ids))
                    + ")"
                )

                parameters.extend(valid_user_ids)

            query += """
                GROUP BY
                    h.habit_id,
                    h.user_id,
                    h.habit_name,
                    h.category,
                    h.goal_type,
                    h.target_count,
                    h.start_date,
                    u.full_name,
                    u.email,
                    hr.reminder_id
                ORDER BY h.user_id, h.habit_id
            """

            cursor.execute(query, parameters)

            examined = 0
            already_sent = 0
            pending_habits: list[dict[str, Any]] = []

            for habit in cursor.fetchall():
                examined += 1

                target_count = int(habit["target_count"])
                completion_count = int(habit["completion_count"] or 0)

                is_completed = completion_count >= target_count

                if is_completed:
                    continue

                if habit["reminder_id"] is not None:
                    already_sent += 1

                    logger.info(
                        "Monthly reminder skipped because it was already sent: "
                        "habit_id=%s check_date=%s",
                        habit["habit_id"],
                        check_date,
                    )

                    continue

                habit["target_count"] = target_count
                habit["completion_count"] = completion_count
                habit["month_start"] = month_start
                habit["month_end"] = check_date

                pending_habits.append(habit)

            return pending_habits, examined, already_sent

        except Error as error:
            logger.exception(
                "Monthly reminder pending-habit query failed for check_date=%s",
                check_date,
            )

            raise RuntimeError(
                "Unable to retrieve pending monthly habits."
            ) from error

        finally:
            if cursor:
                cursor.close()

            if connection and connection.is_connected():
                connection.close()

    @staticmethod
    def _group_by_user(habits: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
        grouped: dict[int, list[dict[str, Any]]] = {}
        for habit in habits:
            grouped.setdefault(int(habit["user_id"]), []).append(habit)
        return grouped

    # ------------------------------------------------------------------
    # Email content helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_progress_percentage(completion_count: Any, target_count: Any) -> int:
        """Safely computes a 0-100 progress percentage, never dividing by zero."""
        try:
            completion = int(completion_count or 0)
        except (TypeError, ValueError):
            completion = 0
        try:
            target = int(target_count or 0)
        except (TypeError, ValueError):
            target = 0
        if target <= 0:
            return 0
        percentage = round((completion / target) * 100)
        return max(0, min(100, percentage))

    @classmethod
    def _category_accent(cls, category: Any) -> str:
        """Deterministically maps a category name to one of the website's accent colors."""
        normalized = str(category or "").strip().lower()
        if not normalized:
            return cls._ACCENT_COLORS[0]
        digest = sum(ord(character) for character in normalized)
        return cls._ACCENT_COLORS[digest % len(cls._ACCENT_COLORS)]

    @staticmethod
    def _escape(value: Any) -> str:
        """Escapes a value for safe insertion into HTML markup or attributes."""
        return escape_html(str(value if value is not None else ""), quote=True)

    def _build_plain_text_email(
        self, user_name: str, habits: list[dict[str, Any]], check_date: date
    ) -> str:
        """Builds the plain-text fallback body used by mail clients without HTML support."""
        count = len(habits)
        habit_lines = []
        for number, habit in enumerate(habits, start=1):
            category = habit["category"] or "Uncategorized"
            habit_lines.append(
                f"{number}. {habit['habit_name']}\n"
                f"   Category: {category}\n"
                f"   Goal: {habit['goal_type']}\n"
                f"   Target: {habit['target_count']}\n"
                f"   Progress: {habit['completion_count']} / {habit['target_count']}\n"
                f"   Date: {check_date.isoformat()}\n"
                "   Status: Pending"
            )

        return (
            f"Hi {user_name},\n\n"
            f"You still have {count} pending daily habit{'s' if count != 1 else ''}.\n\n"
            + "\n\n".join(habit_lines)
            + "\n\nPlease visit HabitSphere and complete your habits to maintain your consistency.\n\n"
            f"Open HabitSphere: {self.app_base_url}\n\n"
            "Keep going,\nHabitSphere"
        )

    def _build_weekly_plain_text_email(
        self,
        user_name: str,
        habits: list[dict[str, Any]],
        check_date: date,
    ) -> str:
        """Builds the plain-text fallback body for weekly reminders."""

        count = len(habits)

        habit_lines = []

        for number, habit in enumerate(habits, start=1):
            category = habit["category"] or "Uncategorized"

            week_start = habit.get("week_start", check_date)
            week_end = habit.get("week_end", check_date)

            habit_lines.append(
                f"{number}. {habit['habit_name']}\n"
                f"   Category: {category}\n"
                f"   Goal: {habit['goal_type']}\n"
                f"   Weekly Target: {habit['target_count']}\n"
                f"   This Week: "
                f"{habit['completion_count']} / {habit['target_count']}\n"
                f"   Week: "
                f"{week_start.isoformat()} to {week_end.isoformat()}\n"
                f"   Status: Pending"
            )

        return (
            f"Hi {user_name},\n\n"
            f"You still have {count} pending weekly habit"
            f"{'s' if count != 1 else ''}.\n\n"
            + "\n\n".join(habit_lines)
            + "\n\n"
            "Keep working toward your weekly goals and maintain your consistency.\n\n"
            f"Open HabitSphere: {self.app_base_url}\n\n"
            "Keep going,\n"
            "HabitSphere"
        )

    def _build_monthly_plain_text_email(
        self,
        user_name: str,
        habits: list[dict[str, Any]],
        check_date: date,
    ) -> str:
        """Builds the plain-text fallback body for monthly reminders."""

        count = len(habits)

        habit_lines = []

        for number, habit in enumerate(habits, start=1):
            category = habit["category"] or "Uncategorized"

            month_start = habit.get("month_start", check_date)
            month_end = habit.get("month_end", check_date)

            habit_lines.append(
                f"{number}. {habit['habit_name']}\n"
                f"   Category: {category}\n"
                f"   Goal: {habit['goal_type']}\n"
                f"   Monthly Target: {habit['target_count']}\n"
                f"   This Month: "
                f"{habit['completion_count']} / {habit['target_count']}\n"
                f"   Month: "
                f"{month_start.isoformat()} to {month_end.isoformat()}\n"
                f"   Status: Pending"
            )

        return (
            f"Hi {user_name},\n\n"
            f"You still have {count} pending monthly habit"
            f"{'s' if count != 1 else ''}.\n\n"
            + "\n\n".join(habit_lines)
            + "\n\n"
            "Keep working toward your monthly goals and maintain your consistency.\n\n"
            f"Open HabitSphere: {self.app_base_url}\n\n"
            "Keep going,\n"
            "HabitSphere"
        )



    def _build_habit_card_html(self, habit: dict[str, Any]) -> str:
        """Renders one pending habit as a self-contained, table-based HTML card."""
        habit_name = self._escape(habit.get("habit_name"))
        category = self._escape(habit.get("category") or "Uncategorized")
        goal_type = str(habit.get("goal_type") or "").strip()
        goal_suffix = f" &middot; {self._escape(goal_type.title())} goal" if goal_type else ""
        completion_count = habit.get("completion_count")
        target_count = habit.get("target_count")
        percentage = self._safe_progress_percentage(completion_count, target_count)
        accent = self._category_accent(habit.get("category"))

        try:
            safe_completion = int(completion_count or 0)
        except (TypeError, ValueError):
            safe_completion = 0
        try:
            safe_target = int(target_count or 0)
        except (TypeError, ValueError):
            safe_target = 0

        return f"""
            <tr>
              <td style="padding:0 32px 14px 32px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{self._CARD};border:1px solid {self._LINE};border-radius:12px;">
                  <tr>
                    <td style="padding:16px 18px;font-family:Arial,Helvetica,sans-serif;">
                      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                        <tr>
                          <td width="16" style="vertical-align:top;padding-top:5px;">
                            <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background-color:{accent};font-size:0;line-height:0;">&nbsp;</span>
                          </td>
                          <td style="padding-left:8px;vertical-align:top;">
                            <p style="margin:0;font-size:14px;font-weight:700;color:{self._INK};">{habit_name}</p>
                            <p style="margin:2px 0 0 0;font-size:11px;color:{self._MUTED};">{category}{goal_suffix}</p>
                          </td>
                          <td align="right" style="vertical-align:top;white-space:nowrap;">
                            <span style="display:inline-block;padding:3px 9px;border-radius:20px;font-size:10px;font-weight:700;color:#dc7b35;background-color:#fff1e5;">Pending</span>
                          </td>
                        </tr>
                      </table>
                      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:12px;">
                        <tr>
                          <td style="font-size:11px;color:{self._MUTED};">Progress</td>
                          <td align="right" style="font-size:11px;color:{self._INK};font-weight:700;">{safe_completion} / {safe_target} &middot; {percentage}%</td>
                        </tr>
                      </table>
                      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:6px;">
                        <tr>
                          <td style="background-color:#efedf3;border-radius:10px;">
                            <table role="presentation" width="{percentage}%" cellpadding="0" cellspacing="0" border="0" style="width:{percentage}%;">
                              <tr>
                                <td style="background-color:{accent};height:7px;line-height:7px;border-radius:10px;font-size:0;">&nbsp;</td>
                              </tr>
                            </table>
                          </td>
                        </tr>
                      </table>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""

    def _build_html_email(
        self, user_name: str, habits: list[dict[str, Any]], check_date: date
    ) -> str:
        """Builds the full HTML email that visually matches the HabitSphere website."""
        count = len(habits)
        safe_user_name = self._escape(user_name)
        safe_app_url = self._escape(self.app_base_url)
        plural = "s" if count != 1 else ""

        habit_cards_html = "".join(self._build_habit_card_html(habit) for habit in habits)

        return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="color-scheme" content="light" />
    <title>HabitSphere Reminder</title>
  </head>
  <body style="margin:0;padding:0;background-color:{self._PAGE};">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{self._PAGE};">
      <tr>
        <td align="center" style="padding:32px 12px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;background-color:{self._CARD};border-radius:18px;overflow:hidden;">
            <tr>
              <td style="padding:26px 32px 0 32px;font-family:Arial,Helvetica,sans-serif;">
                <span style="display:inline-block;width:26px;height:26px;line-height:26px;text-align:center;border-radius:8px;background-color:{self._VIOLET};color:#ffffff;font-size:14px;font-weight:700;vertical-align:middle;">&#10022;</span>
                <span style="font-size:18px;font-weight:700;color:{self._INK};vertical-align:middle;margin-left:8px;">Habit<span style="color:{self._VIOLET};">Sphere</span></span>
              </td>
            </tr>
            <tr>
              <td style="padding:20px 32px 0 32px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{self._VIOLET};background-image:linear-gradient(135deg,{self._VIOLET_DARK},{self._VIOLET_LIGHT_GRADIENT_END});border-radius:16px;">
                  <tr>
                    <td style="padding:28px 28px 32px 28px;font-family:Arial,Helvetica,sans-serif;color:#ffffff;">
                      <p style="margin:0 0 8px 0;font-size:11px;font-weight:700;letter-spacing:1.5px;color:#e3ddff;text-transform:uppercase;">Your Daily Focus</p>
                      <p style="margin:0 0 10px 0;font-size:22px;font-weight:700;line-height:1.3;">Make today count.</p>
                      <p style="margin:0;font-size:14px;line-height:1.5;color:#efeaff;">You have <strong style="color:#ffffff;">{count} pending habit{plural}</strong> waiting for you today.</p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 32px 0 32px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{self._VIOLET_LIGHT};border-radius:12px;">
                  <tr>
                    <td style="padding:14px 20px;font-family:Arial,Helvetica,sans-serif;">
                      <p style="margin:0;font-size:10px;font-weight:700;letter-spacing:1.2px;color:{self._VIOLET};text-transform:uppercase;">Pending Habits</p>
                      <p style="margin:2px 0 0 0;font-size:24px;font-weight:700;color:{self._INK};">{count}</p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:24px 32px 4px 32px;font-family:Arial,Helvetica,sans-serif;">
                <p style="margin:0 0 4px 0;font-size:16px;font-weight:700;color:{self._INK};">Hi {safe_user_name} &#128075;</p>
                <p style="margin:0;font-size:13px;color:{self._MUTED};line-height:1.5;">A few habits are still waiting for you. Keep your consistency going!</p>
              </td>
            </tr>
            <tr>
              <td style="padding-top:14px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                  {habit_cards_html}
                </table>
              </td>
            </tr>
            <tr>
              <td align="center" style="padding:8px 32px 30px 32px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                  <tr>
                    <td style="border-radius:10px;background-color:{self._VIOLET};">
                      <a href="{safe_app_url}" style="display:inline-block;padding:13px 28px;font-family:Arial,Helvetica,sans-serif;font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:10px;">Open HabitSphere &rarr;</a>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:20px 32px 28px 32px;border-top:1px solid {self._LINE};font-family:Arial,Helvetica,sans-serif;text-align:center;">
                <p style="margin:0 0 10px 0;font-size:12px;color:{self._MUTED};line-height:1.6;font-style:italic;">&ldquo;Small disciplines repeated with consistency lead to great achievements.&rdquo;</p>
                <p style="margin:0;font-size:12px;color:{self._MUTED};">Keep going,<br><strong style="color:{self._INK};">HabitSphere</strong></p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""

    def _build_weekly_html_email(
        self,
        user_name: str,
        habits: list[dict[str, Any]],
        check_date: date,
    ) -> str:
        """Builds the full HTML email for weekly habit reminders."""

        count = len(habits)

        safe_user_name = self._escape(user_name)
        safe_app_url = self._escape(self.app_base_url)

        plural = "s" if count != 1 else ""

        habit_cards_html = "".join(
            self._build_habit_card_html(habit)
            for habit in habits
        )

        return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="color-scheme" content="light" />
    <title>HabitSphere Weekly Reminder</title>
  </head>

  <body style="margin:0;padding:0;background-color:{self._PAGE};">

    <table role="presentation"
           width="100%"
           cellpadding="0"
           cellspacing="0"
           border="0"
           style="background-color:{self._PAGE};">

      <tr>
        <td align="center" style="padding:32px 12px;">

          <table role="presentation"
                 width="100%"
                 cellpadding="0"
                 cellspacing="0"
                 border="0"
                 style="max-width:600px;width:100%;
                        background-color:{self._CARD};
                        border-radius:18px;
                        overflow:hidden;">

            <!-- Logo -->
            <tr>
              <td style="padding:26px 32px 0 32px;
                         font-family:Arial,Helvetica,sans-serif;">

                <span style="
                    display:inline-block;
                    width:26px;
                    height:26px;
                    line-height:26px;
                    text-align:center;
                    border-radius:8px;
                    background-color:{self._VIOLET};
                    color:#ffffff;
                    font-size:14px;
                    font-weight:700;
                    vertical-align:middle;">
                    &#10022;
                </span>

                <span style="
                    font-size:18px;
                    font-weight:700;
                    color:{self._INK};
                    vertical-align:middle;
                    margin-left:8px;">
                    Habit<span style="color:{self._VIOLET};">Sphere</span>
                </span>

              </td>
            </tr>

            <!-- Hero -->
            <tr>
              <td style="padding:20px 32px 0 32px;">

                <table role="presentation"
                       width="100%"
                       cellpadding="0"
                       cellspacing="0"
                       border="0"
                       style="
                         background-color:{self._VIOLET};
                         background-image:
                           linear-gradient(
                             135deg,
                             {self._VIOLET_DARK},
                             {self._VIOLET_LIGHT_GRADIENT_END}
                           );
                         border-radius:16px;">

                  <tr>
                    <td style="
                        padding:28px 28px 32px 28px;
                        font-family:Arial,Helvetica,sans-serif;
                        color:#ffffff;">

                      <p style="
                          margin:0 0 8px 0;
                          font-size:11px;
                          font-weight:700;
                          letter-spacing:1.5px;
                          color:#e3ddff;
                          text-transform:uppercase;">
                        Your Weekly Focus
                      </p>

                      <p style="
                          margin:0 0 10px 0;
                          font-size:22px;
                          font-weight:700;
                          line-height:1.3;">
                        Keep your week on track.
                      </p>

                      <p style="
                          margin:0;
                          font-size:14px;
                          line-height:1.5;
                          color:#efeaff;">
                        You have
                        <strong style="color:#ffffff;">
                          {count} pending weekly habit{plural}
                        </strong>
                        waiting for you.
                      </p>

                    </td>
                  </tr>

                </table>

              </td>
            </tr>

            <!-- Count -->
            <tr>
              <td style="padding:16px 32px 0 32px;">

                <table role="presentation"
                       width="100%"
                       cellpadding="0"
                       cellspacing="0"
                       border="0"
                       style="
                         background-color:{self._VIOLET_LIGHT};
                         border-radius:12px;">

                  <tr>
                    <td style="
                        padding:14px 20px;
                        font-family:Arial,Helvetica,sans-serif;">

                      <p style="
                          margin:0;
                          font-size:10px;
                          font-weight:700;
                          letter-spacing:1.2px;
                          color:{self._VIOLET};
                          text-transform:uppercase;">
                        Pending Weekly Habits
                      </p>

                      <p style="
                          margin:2px 0 0 0;
                          font-size:24px;
                          font-weight:700;
                          color:{self._INK};">
                        {count}
                      </p>

                    </td>
                  </tr>

                </table>

              </td>
            </tr>

            <!-- Greeting -->
            <tr>
              <td style="
                  padding:24px 32px 4px 32px;
                  font-family:Arial,Helvetica,sans-serif;">

                <p style="
                    margin:0 0 4px 0;
                    font-size:16px;
                    font-weight:700;
                    color:{self._INK};">
                  Hi {safe_user_name} &#128075;
                </p>

                <p style="
                    margin:0;
                    font-size:13px;
                    color:{self._MUTED};
                    line-height:1.5;">
                  Your weekly goals still have some progress left.
                  Keep going!
                </p>

              </td>
            </tr>

            <!-- Habit Cards -->
            <tr>
              <td style="padding-top:14px;">

                <table role="presentation"
                       width="100%"
                       cellpadding="0"
                       cellspacing="0"
                       border="0">

                  {habit_cards_html}

                </table>

              </td>
            </tr>

            <!-- CTA -->
            <tr>
              <td align="center"
                  style="padding:8px 32px 30px 32px;">

                <table role="presentation"
                       cellpadding="0"
                       cellspacing="0"
                       border="0">

                  <tr>
                    <td style="
                        border-radius:10px;
                        background-color:{self._VIOLET};">

                      <a href="{safe_app_url}"
                         style="
                           display:inline-block;
                           padding:13px 28px;
                           font-family:Arial,Helvetica,sans-serif;
                           font-size:14px;
                           font-weight:700;
                           color:#ffffff;
                           text-decoration:none;
                           border-radius:10px;">
                        Open HabitSphere &rarr;
                      </a>

                    </td>
                  </tr>

                </table>

              </td>
            </tr>

            <!-- Footer -->
            <tr>
              <td style="
                  padding:20px 32px 28px 32px;
                  border-top:1px solid {self._LINE};
                  font-family:Arial,Helvetica,sans-serif;
                  text-align:center;">

                <p style="
                    margin:0 0 10px 0;
                    font-size:12px;
                    color:{self._MUTED};
                    line-height:1.6;
                    font-style:italic;">
                  &ldquo;Small disciplines repeated with consistency
                  lead to great achievements.&rdquo;
                </p>

                <p style="
                    margin:0;
                    font-size:12px;
                    color:{self._MUTED};">
                  Keep going,<br>
                  <strong style="color:{self._INK};">
                    HabitSphere
                  </strong>
                </p>

              </td>
            </tr>

          </table>

        </td>
      </tr>

    </table>

  </body>
</html>"""

    def _build_monthly_html_email(
        self,
        user_name: str,
        habits: list[dict[str, Any]],
        check_date: date,
    ) -> str:
        """Builds the full HTML email for monthly habit reminders."""

        count = len(habits)

        safe_user_name = self._escape(user_name)
        safe_app_url = self._escape(self.app_base_url)

        plural = "s" if count != 1 else ""

        habit_cards_html = "".join(
            self._build_habit_card_html(habit)
            for habit in habits
        )

        return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="color-scheme" content="light" />
    <title>HabitSphere Monthly Reminder</title>
  </head>

  <body style="margin:0;padding:0;background-color:{self._PAGE};">

    <table
      role="presentation"
      width="100%"
      cellpadding="0"
      cellspacing="0"
      border="0"
      style="background-color:{self._PAGE};"
    >
      <tr>
        <td align="center" style="padding:32px 12px;">

          <table
            role="presentation"
            width="100%"
            cellpadding="0"
            cellspacing="0"
            border="0"
            style="
              max-width:600px;
              width:100%;
              background-color:{self._CARD};
              border-radius:18px;
              overflow:hidden;
            "
          >

            <!-- Logo -->
            <tr>
              <td
                style="
                  padding:26px 32px 0 32px;
                  font-family:Arial,Helvetica,sans-serif;
                "
              >
                <span
                  style="
                    display:inline-block;
                    width:26px;
                    height:26px;
                    line-height:26px;
                    text-align:center;
                    border-radius:8px;
                    background-color:{self._VIOLET};
                    color:#ffffff;
                    font-size:14px;
                    font-weight:700;
                    vertical-align:middle;
                  "
                >
                  &#10022;
                </span>

                <span
                  style="
                    font-size:18px;
                    font-weight:700;
                    color:{self._INK};
                    vertical-align:middle;
                    margin-left:8px;
                  "
                >
                  Habit<span style="color:{self._VIOLET};">Sphere</span>
                </span>
              </td>
            </tr>

            <!-- Hero -->
            <tr>
              <td style="padding:20px 32px 0 32px;">

                <table
                  role="presentation"
                  width="100%"
                  cellpadding="0"
                  cellspacing="0"
                  border="0"
                  style="
                    background-color:{self._VIOLET};
                    background-image:
                      linear-gradient(
                        135deg,
                        {self._VIOLET_DARK},
                        {self._VIOLET_LIGHT_GRADIENT_END}
                      );
                    border-radius:16px;
                  "
                >
                  <tr>
                    <td
                      style="
                        padding:28px 28px 32px 28px;
                        font-family:Arial,Helvetica,sans-serif;
                        color:#ffffff;
                      "
                    >

                      <p
                        style="
                          margin:0 0 8px 0;
                          font-size:11px;
                          font-weight:700;
                          letter-spacing:1.5px;
                          color:#e3ddff;
                          text-transform:uppercase;
                        "
                      >
                        Your Monthly Focus
                      </p>

                      <p
                        style="
                          margin:0 0 10px 0;
                          font-size:22px;
                          font-weight:700;
                          line-height:1.3;
                        "
                      >
                        Keep your month on track.
                      </p>

                      <p
                        style="
                          margin:0;
                          font-size:14px;
                          line-height:1.5;
                          color:#efeaff;
                        "
                      >
                        You have
                        <strong style="color:#ffffff;">
                          {count} pending monthly habit{plural}
                        </strong>
                        waiting for you.
                      </p>

                    </td>
                  </tr>
                </table>

              </td>
            </tr>

            <!-- Count -->
            <tr>
              <td style="padding:16px 32px 0 32px;">

                <table
                  role="presentation"
                  width="100%"
                  cellpadding="0"
                  cellspacing="0"
                  border="0"
                  style="
                    background-color:{self._VIOLET_LIGHT};
                    border-radius:12px;
                  "
                >
                  <tr>
                    <td
                      style="
                        padding:14px 20px;
                        font-family:Arial,Helvetica,sans-serif;
                      "
                    >

                      <p
                        style="
                          margin:0;
                          font-size:10px;
                          font-weight:700;
                          letter-spacing:1.2px;
                          color:{self._VIOLET};
                          text-transform:uppercase;
                        "
                      >
                        Pending Monthly Habits
                      </p>

                      <p
                        style="
                          margin:2px 0 0 0;
                          font-size:24px;
                          font-weight:700;
                          color:{self._INK};
                        "
                      >
                        {count}
                      </p>

                    </td>
                  </tr>
                </table>

              </td>
            </tr>

            <!-- Greeting -->
            <tr>
              <td
                style="
                  padding:24px 32px 4px 32px;
                  font-family:Arial,Helvetica,sans-serif;
                "
              >

                <p
                  style="
                    margin:0 0 4px 0;
                    font-size:16px;
                    font-weight:700;
                    color:{self._INK};
                  "
                >
                  Hi {safe_user_name} &#128075;
                </p>

                <p
                  style="
                    margin:0;
                    font-size:13px;
                    color:{self._MUTED};
                    line-height:1.5;
                  "
                >
                  Your monthly goals still have some progress left.
                  Keep going!
                </p>

              </td>
            </tr>

            <!-- Habit Cards -->
            <tr>
              <td style="padding-top:14px;">

                <table
                  role="presentation"
                  width="100%"
                  cellpadding="0"
                  cellspacing="0"
                  border="0"
                >
                  {habit_cards_html}
                </table>

              </td>
            </tr>

            <!-- CTA -->
            <tr>
              <td
                align="center"
                style="padding:8px 32px 30px 32px;"
              >

                <table
                  role="presentation"
                  cellpadding="0"
                  cellspacing="0"
                  border="0"
                >
                  <tr>
                    <td
                      style="
                        border-radius:10px;
                        background-color:{self._VIOLET};
                      "
                    >

                      <a
                        href="{safe_app_url}"
                        style="
                          display:inline-block;
                          padding:13px 28px;
                          font-family:Arial,Helvetica,sans-serif;
                          font-size:14px;
                          font-weight:700;
                          color:#ffffff;
                          text-decoration:none;
                          border-radius:10px;
                        "
                      >
                        Open HabitSphere &rarr;
                      </a>

                    </td>
                  </tr>
                </table>

              </td>
            </tr>

            <!-- Footer -->
            <tr>
              <td
                style="
                  padding:20px 32px 28px 32px;
                  border-top:1px solid {self._LINE};
                  font-family:Arial,Helvetica,sans-serif;
                  text-align:center;
                "
              >

                <p
                  style="
                    margin:0 0 10px 0;
                    font-size:12px;
                    color:{self._MUTED};
                    line-height:1.6;
                    font-style:italic;
                  "
                >
                  &ldquo;Small disciplines repeated with consistency
                  lead to great achievements.&rdquo;
                </p>

                <p
                  style="
                    margin:0;
                    font-size:12px;
                    color:{self._MUTED};
                  "
                >
                  Keep going,<br>
                  <strong style="color:{self._INK};">
                    HabitSphere
                  </strong>
                </p>

              </td>
            </tr>

          </table>

        </td>
      </tr>
    </table>

  </body>
</html>"""

    def _build_weekly_email(
        self,
        user_name: str,
        habits: list[dict[str, Any]],
        check_date: date,
    ) -> tuple[str, str, str]:
        """Returns subject, plain-text body, and HTML body for weekly reminders."""

        count = len(habits)

        subject = (
            f"HabitSphere Weekly Reminder: "
            f"{count} pending weekly habit"
            f"{'s' if count != 1 else ''}"
        )

        plain_text_body = self._build_weekly_plain_text_email(
            user_name,
            habits,
            check_date,
        )

        html_body = self._build_weekly_html_email(
            user_name,
            habits,
            check_date,
        )

        return subject, plain_text_body, html_body

    def _build_monthly_email(
        self,
        user_name: str,
        habits: list[dict[str, Any]],
        check_date: date,
    ) -> tuple[str, str, str]:
        """Returns subject, plain-text body, and HTML body for monthly reminders."""

        count = len(habits)

        subject = (
            f"HabitSphere Monthly Reminder: "
            f"{count} pending monthly habit"
            f"{'s' if count != 1 else ''}"
        )

        plain_text_body = self._build_monthly_plain_text_email(
            user_name,
            habits,
            check_date,
        )

        html_body = self._build_monthly_html_email(
            user_name,
            habits,
            check_date,
        )

        return subject, plain_text_body, html_body

    def _build_email(
        self, user_name: str, habits: list[dict[str, Any]], check_date: date
    ) -> tuple[str, str, str]:
        """Returns (subject, plain_text_body, html_body) for one user's reminder email."""
        count = len(habits)
        subject = f"HabitSphere Reminder: You have {count} pending daily habit{'s' if count != 1 else ''}"
        plain_text_body = self._build_plain_text_email(user_name, habits, check_date)
        html_body = self._build_html_email(user_name, habits, check_date)
        return subject, plain_text_body, html_body

    # ------------------------------------------------------------------
    # Reminder recording and orchestration (unchanged business logic)
    # ------------------------------------------------------------------

    def _record_successful_reminders(self, user_id: int, habits: list[dict[str, Any]], check_date: date) -> int:
        """Records reminder history after an email succeeds; never before delivery."""
        connection = cursor = None
        recorded = 0
        try:
            connection = self.database.connect()
            cursor = connection.cursor()
            for habit in habits:
                try:
                    cursor.execute(
                        """
                        INSERT INTO HABIT_REMINDERS
                            (habit_id, user_id, reminder_type, reminder_date)
                        VALUES (%s, %s, 'daily', %s)
                        """,
                        (habit["habit_id"], user_id, check_date),
                    )
                    recorded += 1
                    logger.info(
                        "Reminder record created: habit_id=%s user_id=%s check_date=%s",
                        habit["habit_id"],
                        user_id,
                        check_date,
                    )
                except Error as error:
                    if error.errno == 1062:
                        logger.warning(
                            "Duplicate reminder record skipped: habit_id=%s check_date=%s",
                            habit["habit_id"],
                            check_date,
                        )
                        continue
                    raise
            connection.commit()
            return recorded
        except Error as error:
            if connection:
                connection.rollback()
            logger.exception("Reminder history recording failed for user_id=%s", user_id)
            return 0
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()

    def _record_successful_weekly_reminders(
        self,
        user_id: int,
        habits: list[dict[str, Any]],
        check_date: date,
    ) -> int:
        """Records weekly reminder history only after email delivery succeeds."""

        connection = cursor = None
        recorded = 0

        try:
            connection = self.database.connect()
            cursor = connection.cursor()

            for habit in habits:
                try:
                    cursor.execute(
                        """
                        INSERT INTO HABIT_REMINDERS
                            (
                                habit_id,
                                user_id,
                                reminder_type,
                                reminder_date
                            )
                        VALUES
                            (%s, %s, 'weekly', %s)
                        """,
                        (
                            habit["habit_id"],
                            user_id,
                            check_date,
                        ),
                    )

                    recorded += 1

                    logger.info(
                        "Weekly reminder record created: "
                        "habit_id=%s user_id=%s check_date=%s",
                        habit["habit_id"],
                        user_id,
                        check_date,
                    )

                except Error as error:
                    if error.errno == 1062:
                        logger.warning(
                            "Duplicate weekly reminder record skipped: "
                            "habit_id=%s check_date=%s",
                            habit["habit_id"],
                            check_date,
                        )
                        continue

                    raise

            connection.commit()
            return recorded

        except Error:
            if connection:
                connection.rollback()

            logger.exception(
                "Weekly reminder history recording failed for user_id=%s",
                user_id,
            )

            return 0

        finally:
            if cursor:
                cursor.close()

            if connection and connection.is_connected():
                connection.close()

    def _record_successful_monthly_reminders(
        self,
        user_id: int,
        habits: list[dict[str, Any]],
        check_date: date,
    ) -> int:
        """Records monthly reminder history only after email delivery succeeds."""

        connection = cursor = None
        recorded = 0

        try:
            connection = self.database.connect()
            cursor = connection.cursor()

            for habit in habits:
                try:
                    cursor.execute(
                        """
                        INSERT INTO HABIT_REMINDERS
                            (
                                habit_id,
                                user_id,
                                reminder_type,
                                reminder_date
                            )
                        VALUES
                            (%s, %s, 'monthly', %s)
                        """,
                        (
                            habit["habit_id"],
                            user_id,
                            check_date,
                        ),
                    )

                    recorded += 1

                    logger.info(
                        "Monthly reminder record created: "
                        "habit_id=%s user_id=%s check_date=%s",
                        habit["habit_id"],
                        user_id,
                        check_date,
                    )

                except Error as error:
                    if error.errno == 1062:
                        logger.warning(
                            "Duplicate monthly reminder record skipped: "
                            "habit_id=%s check_date=%s",
                            habit["habit_id"],
                            check_date,
                        )
                        continue

                    raise

            connection.commit()
            return recorded

        except Error:
            if connection:
                connection.rollback()

            logger.exception(
                "Monthly reminder history recording failed for user_id=%s",
                user_id,
            )

            return 0

        finally:
            if cursor:
                cursor.close()

            if connection and connection.is_connected():
                connection.close()

    def check_pending_daily_habits(
        self, check_date: date | str, user_ids: list[int] | None = None
    ) -> dict[str, int | str]:
        """Manually sends consolidated reminders for one supplied calendar date."""
        selected_date = self._parse_check_date(check_date)
        logger.info("Daily reminder check started: check_date=%s", selected_date)
        pending_habits, examined, already_sent = self._find_pending_daily_habits(
            selected_date, user_ids
        )
        grouped_habits = self._group_by_user(pending_habits)

        logger.info(
            "Daily reminder check summary: check_date=%s examined=%s pending=%s users=%s already_sent=%s",
            selected_date,
            examined,
            len(pending_habits),
            len(grouped_habits),
            already_sent,
        )

        emails_sent = 0
        emails_failed = 0
        records_created = 0
        for user_id, user_habits in grouped_habits.items():
            recipient = str(user_habits[0]["email"] or "").strip()
            user_name = str(user_habits[0]["full_name"] or "there").strip() or "there"
            if not User.EMAIL_PATTERN.fullmatch(recipient):
                emails_failed += 1
                logger.warning("Reminder skipped because user_id=%s has an invalid email", user_id)
                continue

            subject, plain_text_body, html_body = self._build_email(user_name, user_habits, selected_date)
            logger.info(
                "Reminder email attempt: user_id=%s habit_count=%s check_date=%s",
                user_id,
                len(user_habits),
                selected_date,
            )
            if not self.email_sender.send(recipient, subject, plain_text_body, html_body):
                emails_failed += 1
                logger.error("Reminder email failed: user_id=%s check_date=%s", user_id, selected_date)
                continue

            emails_sent += 1
            logger.info("Reminder email sent: user_id=%s check_date=%s", user_id, selected_date)
            records_created += self._record_successful_reminders(user_id, user_habits, selected_date)

        return {
            "check_date": selected_date.isoformat(),
            "active_daily_habits_examined": examined,
            "pending_habits_found": len(pending_habits),
            "already_reminded_habits": already_sent,
            "users_requiring_reminders": len(grouped_habits),
            "emails_sent": emails_sent,
            "emails_failed": emails_failed,
            "reminder_records_created": records_created,
        }

    def check_pending_weekly_habits(
        self,
        check_date: date | str,
        user_ids: list[int] | None = None,
    ) -> dict[str, int | str]:
        """
        Sends consolidated weekly reminders for the supplied calendar date.

        This method is intentionally separate from the daily reminder flow.
        """

        selected_date = self._parse_check_date(check_date)

        logger.info(
            "Weekly reminder check started: check_date=%s",
            selected_date,
        )

        (
            pending_habits,
            examined,
            already_sent,
        ) = self._find_pending_weekly_habits(
            selected_date,
            user_ids,
        )

        grouped_habits = self._group_by_user(pending_habits)

        logger.info(
            "Weekly reminder check summary: "
            "check_date=%s examined=%s pending=%s users=%s already_sent=%s",
            selected_date,
            examined,
            len(pending_habits),
            len(grouped_habits),
            already_sent,
        )

        emails_sent = 0
        emails_failed = 0
        records_created = 0

        for user_id, user_habits in grouped_habits.items():

            recipient = str(
                user_habits[0]["email"] or ""
            ).strip()

            user_name = str(
                user_habits[0]["full_name"] or "there"
            ).strip() or "there"

            if not User.EMAIL_PATTERN.fullmatch(recipient):
                emails_failed += 1

                logger.warning(
                    "Weekly reminder skipped because user_id=%s "
                    "has an invalid email",
                    user_id,
                )

                continue

            (
                subject,
                plain_text_body,
                html_body,
            ) = self._build_weekly_email(
                user_name,
                user_habits,
                selected_date,
            )

            logger.info(
                "Weekly reminder email attempt: "
                "user_id=%s habit_count=%s check_date=%s",
                user_id,
                len(user_habits),
                selected_date,
            )

            if not self.email_sender.send(
                recipient,
                subject,
                plain_text_body,
                html_body,
            ):
                emails_failed += 1

                logger.error(
                    "Weekly reminder email failed: "
                    "user_id=%s check_date=%s",
                    user_id,
                    selected_date,
                )

                continue

            emails_sent += 1

            logger.info(
                "Weekly reminder email sent: "
                "user_id=%s check_date=%s",
                user_id,
                selected_date,
            )

            records_created += (
                self._record_successful_weekly_reminders(
                    user_id,
                    user_habits,
                    selected_date,
                )
            )

        return {
            "check_date": selected_date.isoformat(),
            "active_weekly_habits_examined": examined,
            "pending_weekly_habits_found": len(pending_habits),
            "already_reminded_weekly_habits": already_sent,
            "users_requiring_weekly_reminders": len(grouped_habits),
            "weekly_emails_sent": emails_sent,
            "weekly_emails_failed": emails_failed,
            "weekly_reminder_records_created": records_created,
        }

    def check_pending_monthly_habits(
        self,
        check_date: date | str,
        user_ids: list[int] | None = None,
    ) -> dict[str, int | str]:
        """
        Sends consolidated monthly reminders for the supplied calendar date.
        """

        selected_date = self._parse_check_date(check_date)

        logger.info(
            "Monthly reminder check started: check_date=%s",
            selected_date,
        )

        (
            pending_habits,
            examined,
            already_sent,
        ) = self._find_pending_monthly_habits(
            selected_date,
            user_ids,
        )

        grouped_habits = self._group_by_user(pending_habits)

        logger.info(
            "Monthly reminder check summary: "
            "check_date=%s examined=%s pending=%s users=%s already_sent=%s",
            selected_date,
            examined,
            len(pending_habits),
            len(grouped_habits),
            already_sent,
        )

        emails_sent = 0
        emails_failed = 0
        records_created = 0

        for user_id, user_habits in grouped_habits.items():

            recipient = str(
                user_habits[0]["email"] or ""
            ).strip()

            user_name = str(
                user_habits[0]["full_name"] or "there"
            ).strip() or "there"

            if not User.EMAIL_PATTERN.fullmatch(recipient):
                emails_failed += 1

                logger.warning(
                    "Monthly reminder skipped because user_id=%s "
                    "has an invalid email",
                    user_id,
                )

                continue

            (
                subject,
                plain_text_body,
                html_body,
            ) = self._build_monthly_email(
                user_name,
                user_habits,
                selected_date,
            )

            logger.info(
                "Monthly reminder email attempt: "
                "user_id=%s habit_count=%s check_date=%s",
                user_id,
                len(user_habits),
                selected_date,
            )

            if not self.email_sender.send(
                recipient,
                subject,
                plain_text_body,
                html_body,
            ):
                emails_failed += 1

                logger.error(
                    "Monthly reminder email failed: "
                    "user_id=%s check_date=%s",
                    user_id,
                    selected_date,
                )

                continue

            emails_sent += 1

            logger.info(
                "Monthly reminder email sent: "
                "user_id=%s check_date=%s",
                user_id,
                selected_date,
            )

            records_created += (
                self._record_successful_monthly_reminders(
                    user_id,
                    user_habits,
                    selected_date,
                )
            )

        return {
            "check_date": selected_date.isoformat(),
            "active_monthly_habits_examined": examined,
            "pending_monthly_habits_found": len(pending_habits),
            "already_reminded_monthly_habits": already_sent,
            "users_requiring_monthly_reminders": len(grouped_habits),
            "monthly_emails_sent": emails_sent,
            "monthly_emails_failed": emails_failed,
            "monthly_reminder_records_created": records_created,
        }