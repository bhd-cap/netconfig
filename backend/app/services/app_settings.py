"""
Per-organization application settings

Covers the four planned settings areas: backup retention defaults, email
notification settings, backup schedule defaults, and maintenance windows.

The maintenance window logic is the part with real substance. A window like
22:00-02:00 wraps midnight, which means a naive start <= now <= end test is
wrong for exactly the windows people actually configure (overnight change
freezes). Windows are also evaluated in the organization's timezone, not the
server's, because "no backups during the Sunday night change window" means
Sunday night where the network is.
"""
import logging
import smtplib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from typing import Dict, List, Optional, Sequence, Tuple

try:  # Python 3.9+
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.administration import AppSettings
from app.utils.encryption import encryption_service

logger = logging.getLogger(__name__)

# Monday is 0, matching datetime.weekday().
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class SettingsError(RuntimeError):
    """Raised when a settings value is not acceptable"""


def get_or_create(db: Session, organization_id: int) -> AppSettings:
    """
    Fetch an organization's settings, creating defaults on first use

    Args:
        db: Database session
        organization_id: Organization

    Returns:
        AppSettings
    """
    settings = db.execute(
        select(AppSettings).where(AppSettings.organization_id == organization_id)
    ).scalar_one_or_none()

    if settings:
        return settings

    settings = AppSettings(organization_id=organization_id)
    db.add(settings)
    db.commit()

    logger.info(f"Created default settings for organization {organization_id}")
    return settings


# --------------------------------------------------------------------------
# Maintenance windows
# --------------------------------------------------------------------------


@dataclass
class MaintenanceWindow:
    """One recurring window during which activity is suppressed"""

    name: str
    days: List[int]  # 0 = Monday
    start: time
    end: time
    suppress_backups: bool = True
    suppress_notifications: bool = False

    @property
    def wraps_midnight(self) -> bool:
        """Whether the window runs past midnight into the next day"""
        return self.end <= self.start


def parse_window(raw: Dict) -> MaintenanceWindow:
    """
    Validate and parse one stored window

    Args:
        raw: Window dict as stored in JSONB

    Returns:
        MaintenanceWindow

    Raises:
        SettingsError: If the window is malformed
    """
    name = (raw.get("name") or "").strip() or "Maintenance"

    days = raw.get("days")
    if not isinstance(days, (list, tuple)) or not days:
        raise SettingsError(f"Window '{name}': at least one day is required")

    parsed_days = []
    for day in days:
        try:
            value = int(day)
        except (TypeError, ValueError):
            raise SettingsError(f"Window '{name}': '{day}' is not a day number")
        if not 0 <= value <= 6:
            raise SettingsError(
                f"Window '{name}': day {value} is out of range (0=Monday to 6=Sunday)"
            )
        parsed_days.append(value)

    def parse_time(value, field):
        if isinstance(value, time):
            return value
        try:
            hour, _, minute = str(value).partition(":")
            return time(int(hour), int(minute or 0))
        except (TypeError, ValueError):
            raise SettingsError(
                f"Window '{name}': {field} '{value}' is not a HH:MM time"
            )

    start = parse_time(raw.get("start"), "start")
    end = parse_time(raw.get("end"), "end")

    if start == end:
        raise SettingsError(
            f"Window '{name}': start and end are the same, which is a zero-length "
            f"window. Use 00:00 to 23:59 for a whole day."
        )

    return MaintenanceWindow(
        name=name,
        days=sorted(set(parsed_days)),
        start=start,
        end=end,
        suppress_backups=bool(raw.get("suppress_backups", True)),
        suppress_notifications=bool(raw.get("suppress_notifications", False)),
    )


def validate_windows(windows: Sequence[Dict]) -> List[Dict]:
    """
    Validate a list of windows and return them normalised for storage

    Args:
        windows: Raw window dicts

    Returns:
        Normalised dicts

    Raises:
        SettingsError: If any window is malformed
    """
    normalised = []

    for raw in windows or []:
        window = parse_window(raw)
        normalised.append(
            {
                "name": window.name,
                "days": window.days,
                "start": window.start.strftime("%H:%M"),
                "end": window.end.strftime("%H:%M"),
                "suppress_backups": window.suppress_backups,
                "suppress_notifications": window.suppress_notifications,
            }
        )

    return normalised


def resolve_timezone(name: Optional[str]):
    """
    Turn a timezone name into a tzinfo, falling back to UTC

    Args:
        name: IANA timezone name

    Returns:
        tzinfo
    """
    if not name or name.upper() == "UTC" or ZoneInfo is None:
        return timezone.utc

    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        logger.warning(f"Unknown timezone '{name}'; falling back to UTC")
        return timezone.utc


def window_contains(window: MaintenanceWindow, moment: datetime) -> bool:
    """
    Whether a local moment falls inside a window

    A window that wraps midnight (22:00-02:00) belongs to the day its start
    falls on, so 01:00 on Tuesday is inside a Monday 22:00-02:00 window. That
    is what people mean by "Monday night".

    Args:
        window: The window
        moment: A timezone-aware moment already converted to the window's zone

    Returns:
        bool
    """
    weekday = moment.weekday()
    current = moment.time()

    if not window.wraps_midnight:
        return weekday in window.days and window.start <= current < window.end

    # Wrapping window: either late on a configured day, or early on the day
    # after a configured day.
    if weekday in window.days and current >= window.start:
        return True

    previous_day = (weekday - 1) % 7
    return previous_day in window.days and current < window.end


def active_windows(
    settings: AppSettings, moment: Optional[datetime] = None
) -> List[MaintenanceWindow]:
    """
    Which of an organization's windows are open right now

    Args:
        settings: The organization's settings
        moment: Override the current time (aware or naive UTC)

    Returns:
        The open windows
    """
    tzinfo = resolve_timezone(settings.maintenance_timezone)

    if moment is None:
        moment = datetime.now(timezone.utc)
    elif moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    local = moment.astimezone(tzinfo)

    open_windows = []
    for raw in settings.maintenance_windows or []:
        try:
            window = parse_window(raw)
        except SettingsError:
            # A malformed window must not suppress everything, nor blow up a
            # scheduled run; skip it and carry on.
            logger.warning(f"Ignoring malformed maintenance window: {raw}")
            continue

        if window_contains(window, local):
            open_windows.append(window)

    return open_windows


def backups_suppressed(
    settings: AppSettings, moment: Optional[datetime] = None
) -> Tuple[bool, Optional[str]]:
    """
    Whether scheduled backups should be held right now

    Args:
        settings: The organization's settings
        moment: Override the current time

    Returns:
        (suppressed, name of the window responsible)
    """
    for window in active_windows(settings, moment):
        if window.suppress_backups:
            return True, window.name
    return False, None


def notifications_suppressed(
    settings: AppSettings, moment: Optional[datetime] = None
) -> Tuple[bool, Optional[str]]:
    """
    Whether notifications should be held right now

    Args:
        settings: The organization's settings
        moment: Override the current time

    Returns:
        (suppressed, name of the window responsible)
    """
    for window in active_windows(settings, moment):
        if window.suppress_notifications:
            return True, window.name
    return False, None


def next_window_start(
    settings: AppSettings, after: Optional[datetime] = None, horizon_days: int = 14
) -> Optional[datetime]:
    """
    When the next maintenance window opens

    Args:
        settings: The organization's settings
        after: Look forward from this moment
        horizon_days: How far ahead to look

    Returns:
        The next start as an aware datetime, or None if none is scheduled
    """
    tzinfo = resolve_timezone(settings.maintenance_timezone)
    after = (after or datetime.now(timezone.utc)).astimezone(tzinfo)

    windows = []
    for raw in settings.maintenance_windows or []:
        try:
            windows.append(parse_window(raw))
        except SettingsError:
            continue

    if not windows:
        return None

    candidates = []
    for offset in range(horizon_days + 1):
        day = (after + timedelta(days=offset)).date()
        for window in windows:
            if day.weekday() not in window.days:
                continue
            start = datetime.combine(day, window.start, tzinfo=tzinfo)
            if start > after:
                candidates.append(start)

    return min(candidates) if candidates else None


# --------------------------------------------------------------------------
# Email notifications
# --------------------------------------------------------------------------


def smtp_password(settings: AppSettings) -> Optional[str]:
    """Decrypt the stored SMTP password"""
    if not settings.smtp_password_encrypted:
        return None
    try:
        return encryption_service.decrypt(settings.smtp_password_encrypted)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Could not decrypt the SMTP password: {e}")
        return None


def send_email(
    settings: AppSettings,
    subject: str,
    body: str,
    recipients: Optional[Sequence[str]] = None,
    html_body: Optional[str] = None,
) -> Dict:
    """
    Send a notification email

    Never raises: a mail server problem must not fail the backup that
    triggered the notification.

    Args:
        settings: The organization's settings
        subject: Subject line
        body: Plain text body
        recipients: Override the configured recipient list
        html_body: Optional HTML alternative

    Returns:
        dict with success, message and the recipient count
    """
    to_addresses = list(recipients or settings.notify_recipients or [])

    if not settings.smtp_host:
        return {"success": False, "message": "No SMTP server is configured", "sent": 0}

    if not to_addresses:
        return {"success": False, "message": "No recipients are configured", "sent": 0}

    from_address = settings.smtp_from_address or settings.smtp_username
    if not from_address:
        return {
            "success": False,
            "message": "No from address is configured",
            "sent": 0,
        }

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_address
    message["To"] = ", ".join(to_addresses)
    message.set_content(body)

    if html_body:
        message.add_alternative(html_body, subtype="html")

    try:
        port = settings.smtp_port or 587

        # Port 465 is implicit TLS; everything else starts plain and upgrades.
        if port == 465:
            server = smtplib.SMTP_SSL(settings.smtp_host, port, timeout=30)
        else:
            server = smtplib.SMTP(settings.smtp_host, port, timeout=30)

        with server:
            server.ehlo()
            if port != 465 and settings.smtp_use_tls:
                server.starttls()
                server.ehlo()

            if settings.smtp_username:
                password = smtp_password(settings)
                if password:
                    server.login(settings.smtp_username, password)

            server.send_message(message)

    except Exception as e:  # noqa: BLE001 - notifications never break a backup
        logger.error(f"Could not send notification email: {e}")
        return {"success": False, "message": str(e), "sent": 0}

    logger.info(f"Sent '{subject}' to {len(to_addresses)} recipient(s)")
    return {
        "success": True,
        "message": f"Sent to {len(to_addresses)} recipient(s)",
        "sent": len(to_addresses),
    }


def send_test_email(settings: AppSettings, recipient: str) -> Dict:
    """
    Send a test message so an administrator can verify SMTP settings

    Args:
        settings: The organization's settings
        recipient: Where to send it

    Returns:
        dict with success and message
    """
    return send_email(
        settings,
        subject="NetConfig Backup: test message",
        body=(
            "This is a test message from NetConfig Backup.\n\n"
            "If you received it, the SMTP settings for your organization are "
            "working.\n"
        ),
        recipients=[recipient],
    )


def notify(
    db: Session,
    organization_id: int,
    event: str,
    subject: str,
    body: str,
) -> Dict:
    """
    Send a notification if the organization has asked for this event

    Args:
        db: Database session
        organization_id: Organization
        event: One of 'backup_failure', 'backup_success', 'config_change',
            'new_host'
        subject: Subject line
        body: Message body

    Returns:
        dict describing what happened
    """
    settings = get_or_create(db, organization_id)

    if not settings.notifications_enabled:
        return {"success": False, "message": "Notifications are disabled", "sent": 0}

    wanted = {
        "backup_failure": settings.notify_on_backup_failure,
        "backup_success": settings.notify_on_backup_success,
        "config_change": settings.notify_on_config_change,
        "new_host": settings.notify_on_new_host,
    }.get(event)

    if not wanted:
        return {
            "success": False,
            "message": f"Notifications for '{event}' are not enabled",
            "sent": 0,
        }

    suppressed, window = notifications_suppressed(settings)
    if suppressed:
        logger.info(f"Notification '{subject}' suppressed by window '{window}'")
        return {
            "success": False,
            "message": f"Suppressed by maintenance window '{window}'",
            "sent": 0,
        }

    return send_email(settings, subject, body)


# --------------------------------------------------------------------------
# Validation of the other settings groups
# --------------------------------------------------------------------------


def validate_retention(days: Optional[int], max_per_device: Optional[int]) -> None:
    """
    Check retention values are sane

    Raises:
        SettingsError
    """
    if days is not None and days < 1:
        raise SettingsError("Retention must be at least 1 day")
    if days is not None and days > 3650:
        raise SettingsError("Retention cannot exceed 10 years")
    if max_per_device is not None and max_per_device < 1:
        raise SettingsError("The per-device backup limit must be at least 1")


def validate_cron(expression: str) -> None:
    """
    Check a cron expression parses

    Raises:
        SettingsError
    """
    from croniter import croniter

    if not croniter.is_valid(expression):
        raise SettingsError(f"'{expression}' is not a valid cron expression")
