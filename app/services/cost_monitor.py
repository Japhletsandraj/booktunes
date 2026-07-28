"""Free-tier quota monitoring.

Zero cost is the hard constraint, so the failure mode to prevent is silently
crossing a limit and either getting throttled or billed. This samples each
quota, records it to ``system_metrics``, and alerts once per day per metric
when usage crosses ``QUOTA_ALERT_THRESHOLD``.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.redis_client import cache_get, cache_set, get_daily_command_count
from app.models import Book, Recommendation, SystemMetric, User

logger = get_logger(__name__)


class CostMonitor:
    def __init__(self, session: AsyncSession):
        self.session = session

    # -- Samplers ---------------------------------------------------------

    async def database_size_mb(self) -> float:
        try:
            result = await self.session.execute(
                text("SELECT pg_database_size(current_database())")
            )
            return round((result.scalar() or 0) / (1024 * 1024), 2)
        except Exception as exc:
            logger.warning("Could not read database size: %s", exc)
            return 0.0

    async def table_sizes(self) -> dict[str, float]:
        """Per-table MB — tells you *what* to prune when space runs short."""
        try:
            rows = await self.session.execute(
                text(
                    "SELECT relname, pg_total_relation_size(relid) AS bytes "
                    "FROM pg_catalog.pg_statio_user_tables "
                    "ORDER BY bytes DESC LIMIT 15"
                )
            )
            return {
                name: round(size / (1024 * 1024), 2) for name, size in rows.all()
            }
        except Exception as exc:
            logger.debug("Table size query failed: %s", exc)
            return {}

    async def redis_commands_today(self) -> int:
        return await get_daily_command_count()

    async def cloudinary_usage_gb(self) -> float:
        """Query Cloudinary's usage endpoint.

        Only works when CLOUDINARY_URL is set; returns 0 otherwise. Covers are
        served from the source CDNs by default, so this is normally 0.
        """
        if not settings.CLOUDINARY_URL:
            return 0.0
        try:
            import cloudinary
            import cloudinary.api

            cloudinary.config(cloudinary_url=settings.CLOUDINARY_URL, secure=True)
            usage = cloudinary.api.usage()
            return round(usage.get("storage", {}).get("usage", 0) / (1024**3), 3)
        except Exception as exc:
            logger.debug("Cloudinary usage lookup failed: %s", exc)
            return 0.0

    async def row_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for label, model in (("users", User), ("books", Book),
                             ("recommendations", Recommendation)):
            try:
                counts[label] = int(
                    await self.session.scalar(select(func.count()).select_from(model)) or 0
                )
            except Exception:
                counts[label] = -1
        return counts

    # -- Reporting --------------------------------------------------------

    async def collect(self) -> dict[str, Any]:
        db_mb = await self.database_size_mb()
        redis_commands = await self.redis_commands_today()
        cloudinary_gb = await self.cloudinary_usage_gb()

        quotas = {
            "database_mb": {
                "used": db_mb,
                "limit": settings.MAX_DB_SIZE_MB,
                "ratio": round(db_mb / settings.MAX_DB_SIZE_MB, 4),
            },
            "redis_commands_today": {
                "used": redis_commands,
                "limit": settings.MAX_REDIS_COMMANDS_PER_DAY,
                "ratio": round(redis_commands / settings.MAX_REDIS_COMMANDS_PER_DAY, 4),
            },
            "cloudinary_gb": {
                "used": cloudinary_gb,
                "limit": settings.MAX_CLOUDINARY_STORAGE_GB,
                "ratio": round(cloudinary_gb / settings.MAX_CLOUDINARY_STORAGE_GB, 4),
            },
        }

        breaches = [
            name for name, q in quotas.items()
            if q["ratio"] >= settings.QUOTA_ALERT_THRESHOLD
        ]

        return {
            "quotas": quotas,
            "breaches": breaches,
            "tables": await self.table_sizes(),
            "rows": await self.row_counts(),
            "checked_at": datetime.now(UTC).isoformat(),
        }

    async def record(self, report: dict[str, Any]) -> None:
        for name, quota in report["quotas"].items():
            self.session.add(
                SystemMetric(
                    metric_name=f"quota.{name}",
                    value=quota["used"],
                    meta={"limit": quota["limit"], "ratio": quota["ratio"]},
                )
            )
        await self.session.commit()

    async def alert_if_needed(self, report: dict[str, Any]) -> list[str]:
        """Send at most one alert per metric per day."""
        sent: list[str] = []
        for name in report["breaches"]:
            dedup_key = f"alert:quota:{name}:{date.today().isoformat()}"
            if await cache_get(dedup_key):
                continue

            quota = report["quotas"][name]
            message = (
                f":warning: **Booktunes free-tier alert** — `{name}` at "
                f"{quota['ratio'] * 100:.1f}% "
                f"({quota['used']} / {quota['limit']}). "
                f"Environment: {settings.ENVIRONMENT}"
            )
            if await self._send_alert(message):
                sent.append(name)
                await cache_set(dedup_key, True, ttl=86_400)
        return sent

    async def _send_alert(self, message: str) -> bool:
        """Post to the configured Discord/Slack webhook.

        Set ALERT_WEBHOOK_URL to a free Discord webhook: Server Settings ->
        Integrations -> Webhooks -> New Webhook -> Copy Webhook URL.
        """
        if not settings.ALERT_WEBHOOK_URL:
            logger.warning("QUOTA ALERT (no webhook configured): %s", message)
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    settings.ALERT_WEBHOOK_URL, json={"content": message}
                )
                response.raise_for_status()
            logger.info("Quota alert dispatched")
            return True
        except Exception as exc:
            logger.error("Failed to send quota alert: %s", exc)
            return False

    # -- Cleanup ----------------------------------------------------------

    async def cleanup(self) -> dict[str, int]:
        """Prune the tables that grow without bound.

        Recommendations and metrics are both derived data — regenerating them
        is cheap, and letting them accumulate is the most likely way to hit the
        500MB ceiling.
        """
        removed: dict[str, int] = {}
        now = datetime.now(UTC)

        result = await self.session.execute(
            delete(Recommendation).where(Recommendation.expires_at < now)
        )
        removed["expired_recommendations"] = result.rowcount or 0

        result = await self.session.execute(
            delete(SystemMetric).where(
                SystemMetric.timestamp < now - timedelta(days=30)
            )
        )
        removed["old_metrics"] = result.rowcount or 0

        await self.session.commit()
        logger.info("Cleanup removed %s", removed)
        return removed


# --- Weekly usage email --------------------------------------------------
#
# Resend's free tier allows 100 emails/day, which is ample for a weekly digest.
# Get an API key at https://resend.com/api-keys and set RESEND_API_KEY plus
# ALERT_EMAIL_TO. The `from` address must be on a domain you've verified with
# Resend — the shared onboarding@resend.dev sender only delivers to the
# address that owns the account.

async def send_usage_email(report: dict[str, Any]) -> bool:
    if not (settings.RESEND_API_KEY and settings.ALERT_EMAIL_TO):
        logger.debug("Resend not configured — skipping usage email")
        return False

    lines = [
        f"<li><b>{name}</b>: {q['used']} / {q['limit']} ({q['ratio'] * 100:.1f}%)</li>"
        for name, q in report["quotas"].items()
    ]
    html = (
        f"<h2>Booktunes weekly usage</h2><ul>{''.join(lines)}</ul>"
        f"<p>Rows: {report['rows']}</p>"
        f"<p>Checked at {report['checked_at']}</p>"
    )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json={
                    "from": "Booktunes <onboarding@resend.dev>",
                    "to": [settings.ALERT_EMAIL_TO],
                    "subject": "Booktunes — weekly free-tier usage",
                    "html": html,
                },
            )
            response.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Usage email failed: %s", exc)
        return False
