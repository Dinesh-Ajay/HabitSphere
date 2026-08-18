"""Background scheduling for HabitSphere's existing daily reminder service."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, time
from typing import Callable

from habit_tracker import logger
from reminder_service import ReminderService


@dataclass(frozen=True)
class ReminderSchedule:
    """Validated scheduler settings loaded from the environment."""

    enabled: bool
    hour: int
    minute: int
    interval_seconds: int

    @classmethod
    def from_environment(cls) -> "ReminderSchedule | None":
        enabled_value = os.getenv("REMINDER_ENABLED", "false").strip().lower()
        if enabled_value not in {"true", "false"}:
            logger.error("Reminder scheduler configuration is invalid: REMINDER_ENABLED")
            return None

        try:
            hour = int(os.getenv("REMINDER_HOUR", "20"))
            minute = int(os.getenv("REMINDER_MINUTE", "0"))
            interval_seconds = int(os.getenv("REMINDER_CHECK_INTERVAL_SECONDS", "300"))
        except ValueError:
            logger.error("Reminder scheduler configuration contains a non-numeric time or interval")
            return None

        if not 0 <= hour <= 23 or not 0 <= minute <= 59 or interval_seconds <= 0:
            logger.error("Reminder scheduler configuration has an invalid time or interval")
            return None
        return cls(enabled_value == "true", hour, minute, interval_seconds)


class ReminderScheduler:
    """Runs the existing reminder service on one daemon thread at a configured time."""

    def __init__(
        self,
        reminder_service: ReminderService | None = None,
        now_provider: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.reminder_service = reminder_service or ReminderService()
        self.now_provider = now_provider
        self._stop_event = threading.Event()
        self._start_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._schedule: ReminderSchedule | None = None

    def start(self) -> bool:
        """Starts one scheduler thread when reminder configuration enables it."""
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                logger.warning("Daily reminder scheduler start ignored because it is already running")
                return False

            schedule = ReminderSchedule.from_environment()
            if schedule is None:
                logger.error("Daily reminder scheduler disabled because configuration is invalid")
                return False
            if not schedule.enabled:
                logger.info("Daily reminder scheduler is disabled")
                return False

            self._schedule = schedule
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self.run,
                name="HabitSphereReminderScheduler",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "Daily reminder scheduler started: time=%02d:%02d interval_seconds=%s",
                schedule.hour,
                schedule.minute,
                schedule.interval_seconds,
            )
            return True

    def stop(self) -> None:
        """Signals the daemon thread to stop and waits briefly for a clean exit."""
        with self._start_lock:
            thread = self._thread
            self._stop_event.set()
        if thread and thread.is_alive():
            thread.join(timeout=5)
        logger.info("Daily reminder scheduler stopped")

    def should_run_reminder_check(self, current_time: datetime | None = None) -> bool:
        """Returns whether the configured earliest reminder time has been reached."""
        if self._schedule is None:
            return False
        now = current_time or self.now_provider()
        return now.time() >= time(self._schedule.hour, self._schedule.minute)

    def run_reminder_check(self) -> bool:
        """Delegates one eligible daily check to the existing reminder service."""
        now = self.now_provider()
        if not self.should_run_reminder_check(now):
            return False
        try:
            result = self.reminder_service.check_pending_daily_habits(now.date())
            logger.info(
                "Daily reminder scheduler check completed: date=%s users=%s emails_sent=%s",
                now.date(),
                result.get("users_requiring_reminders", 0),
                result.get("emails_sent", 0),
            )
            return True
        except Exception:
            logger.exception("Daily reminder scheduler check failed")
            return False

    def run(self) -> None:
        """Runs checks at the configured interval without blocking the HTTP server."""
        while not self._stop_event.is_set():
            self.run_reminder_check()
            interval = self._schedule.interval_seconds if self._schedule else 300
            self._stop_event.wait(interval)
