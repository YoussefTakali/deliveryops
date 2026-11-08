"""Teams notifications as Adaptive Cards (Teams Workflows / incoming webhook)."""

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


def _post_card(title: str, lines: list[str], button: str, url: str) -> None:
    if not settings.teams_webhook_url:
        return
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "wrap": True, "text": title},
            *({"type": "TextBlock", "wrap": True, "text": line} for line in lines[:8]),
        ],
        "actions": [{"type": "Action.OpenUrl", "title": button, "url": url}],
    }
    payload = {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}
    try:
        httpx.post(settings.teams_webhook_url, json=payload, timeout=5).raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("Teams notification failed: %s", exc)


def approval_request(run_id: str, perimeter_id: str, titles: list[str]) -> None:
    _post_card(f"DeliveryOps — {len(titles)} décision(s) à valider", [f"• {t}" for t in titles], "Ouvrir la file de validation", f"{settings.frontend_url}/runs/{run_id}")


def budget_alert(title: str, body: str) -> None:
    _post_card(f"💰 {title}", [body], "Ouvrir la gouvernance IA", f"{settings.frontend_url}/admin/governance")


def alert(perimeter, title: str, body: str, link: str) -> None:
    _post_card(f"⚠ {perimeter.client.name} / {perimeter.name} — {title}", body.splitlines(), "Voir dans DeliveryOps", f"{settings.frontend_url}{link}")
