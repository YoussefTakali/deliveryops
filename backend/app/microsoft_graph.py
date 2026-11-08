"""Microsoft Graph client.

The SPA acquires a delegated Graph access token via MSAL (the account signed into the PC) and
sends it to us; we relay it to Microsoft Graph. Graph validates the token and enforces the
consented scopes — so the backend needs no client secret for this to work, which keeps the local
"try it on my PC" test simple. (Production hardening would use the On-Behalf-Of flow with a
confidential client instead; the call sites here wouldn't change.)

Every source is fetched independently and degrades on its own: if a scope wasn't consented, or the
account type doesn't expose that endpoint, that one source comes back with an "error" and the rest
still populate. "Fetch all data from all Microsoft tools" realistically means "as much as this
account and its consented permissions allow", and the UI shows exactly what came back.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
_TIMEOUT = httpx.Timeout(20.0)
_EVENT_SELECT = "subject,start,end,location,organizer,isOnlineMeeting,onlineMeetingUrl,attendees,bodyPreview"


def _calendar_view(c: httpx.Client, *, days_back: int, days_ahead: int, top: int) -> dict:
    """Events in a time window via /me/calendarView (expands recurring meetings into instances, unlike
    /me/events). Times are UTC ISO. Ordered by start so "today" and "next up" are reliably included."""
    now = datetime.now(timezone.utc)
    return _get(c, "/me/calendarView", {
        "startDateTime": (now - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime": (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "$select": _EVENT_SELECT, "$orderby": "start/dateTime", "$top": top,
    })


class GraphError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _client(token: str) -> httpx.Client:
    return httpx.Client(base_url=GRAPH, headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT)


def me(token: str) -> dict:
    """Identity of the token's owner. Also serves as token validation: a bad/expired token 401s."""
    with _client(token) as c:
        r = c.get("/me", params={"$select": "id,displayName,userPrincipalName,mail,jobTitle,officeLocation,preferredLanguage"})
    if r.status_code == 401:
        raise GraphError(401, "Microsoft token invalid or expired")
    if r.status_code >= 400:
        raise GraphError(r.status_code, r.text[:300])
    return r.json()


def _get(c: httpx.Client, path: str, params: dict | None = None) -> dict:
    """One source. Returns {"items": [...], "count": n} or {"error": "...", "status": code}."""
    try:
        r = c.get(path, params=params)
    except httpx.HTTPError as exc:
        return {"error": f"réseau: {exc}", "status": 0}
    if r.status_code == 403:
        return {"error": "permission non accordée (consentement requis pour ce périmètre)", "status": 403}
    if r.status_code == 404:
        return {"error": "non disponible pour ce compte", "status": 404}
    if r.status_code >= 400:
        return {"error": r.json().get("error", {}).get("message", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200], "status": r.status_code}
    items = r.json().get("value", r.json())
    return {"items": items, "count": len(items) if isinstance(items, list) else 1}


def overview(token: str) -> dict:
    """Aggregate the signed-in user's data across Microsoft tools. Never raises for a single failed
    source — only a bad token (via `me`) fails the whole call."""
    profile = me(token)
    sources: dict[str, dict] = {}
    with _client(token) as c:
        # --- Outlook: calendar & mail ---
        # A window around "now" (last week → next month) via calendarView, so recent and upcoming
        # meetings — including ones created after a previous scan — are captured, not just the newest 15.
        sources["calendar"] = _summarize_events(_calendar_view(c, days_back=7, days_ahead=30, top=50))
        sources["mail"] = _summarize_mail(_get(c, "/me/messages", {
            "$top": 15, "$orderby": "receivedDateTime desc",
            "$select": "subject,from,receivedDateTime,bodyPreview,isRead,webLink",
        }))
        # --- OneDrive / SharePoint files ---
        sources["files"] = _summarize_files(_get(c, "/me/drive/recent", {"$top": 15}))
        sources["sites"] = _summarize_sites(_get(c, "/me/followedSites", None))
        # --- Teams: joined teams, chats ---
        sources["teams"] = _summarize_teams(_get(c, "/me/joinedTeams", {"$select": "id,displayName,description"}))
        sources["chats"] = _summarize_chats(_get(c, "/me/chats", {"$top": 15, "$orderby": "lastMessagePreview/createdDateTime desc", "$expand": "lastMessagePreview"}))
        # --- Planner / To Do tasks ---
        sources["tasks"] = _summarize_tasks(c)
        # --- Presence ---
        sources["presence"] = _summarize_presence(_get(c, "/me/presence", None))

    available = sum(1 for s in sources.values() if not s.get("error"))
    return {
        "profile": {
            "id": profile.get("id"),
            "name": profile.get("displayName"),
            "email": profile.get("mail") or profile.get("userPrincipalName"),
            "upn": profile.get("userPrincipalName"),
            "job_title": profile.get("jobTitle"),
            "office": profile.get("officeLocation"),
        },
        "sources": sources,
        "sources_available": available,
        "sources_total": len(sources),
    }


# ---------------------------------------------------------------- per-source shaping

def _passthrough_error(raw: dict) -> dict | None:
    return {"error": raw["error"], "status": raw.get("status")} if raw.get("error") else None


def _attendee_names(e: dict) -> list[str]:
    """Attendee display names (falling back to the email address). Keeping the actual names — not just
    a count — is what lets "do I have a meeting with X?" be answered."""
    out = []
    for a in e.get("attendees") or []:
        if not isinstance(a, dict):
            continue
        ea = a.get("emailAddress") or {}
        name = ea.get("name") or ea.get("address")
        if name:
            out.append(name)
    return out


def _summarize_events(raw: dict) -> dict:
    if err := _passthrough_error(raw):
        return err
    events = [{
        "subject": e.get("subject"),
        "start": (e.get("start") or {}).get("dateTime"),
        "end": (e.get("end") or {}).get("dateTime"),
        "location": (e.get("location") or {}).get("displayName"),
        "organizer": ((e.get("organizer") or {}).get("emailAddress") or {}).get("name"),
        "online": e.get("isOnlineMeeting"),
        "join_url": e.get("onlineMeetingUrl"),
        "attendees": _attendee_names(e),
        "attendee_count": len(e.get("attendees") or []),
        "preview": (e.get("bodyPreview") or "")[:160],
    } for e in raw["items"]]
    return {"count": len(events), "items": events}


def _summarize_mail(raw: dict) -> dict:
    if err := _passthrough_error(raw):
        return err
    msgs = [{
        "subject": m.get("subject"),
        "from": ((m.get("from") or {}).get("emailAddress") or {}).get("name"),
        "received": m.get("receivedDateTime"),
        "preview": (m.get("bodyPreview") or "")[:160],
        "unread": not m.get("isRead", True),
        "link": m.get("webLink"),
    } for m in raw["items"]]
    return {"count": len(msgs), "unread": sum(1 for m in msgs if m["unread"]), "items": msgs}


def _summarize_files(raw: dict) -> dict:
    if err := _passthrough_error(raw):
        return err
    files = [{
        "name": f.get("name"),
        "modified": f.get("lastModifiedDateTime"),
        "by": ((f.get("lastModifiedBy") or {}).get("user") or {}).get("displayName"),
        "url": f.get("webUrl"),
        "size": f.get("size"),
        "folder": "folder" in f,
    } for f in raw["items"]]
    return {"count": len(files), "items": files}


def _summarize_sites(raw: dict) -> dict:
    if err := _passthrough_error(raw):
        return err
    sites = [{"name": s.get("displayName") or s.get("name"), "url": s.get("webUrl")} for s in raw["items"]]
    return {"count": len(sites), "items": sites}


def _summarize_teams(raw: dict) -> dict:
    if err := _passthrough_error(raw):
        return err
    teams = [{"name": t.get("displayName"), "description": t.get("description")} for t in raw["items"]]
    return {"count": len(teams), "items": teams}


def _summarize_chats(raw: dict) -> dict:
    if err := _passthrough_error(raw):
        return err
    chats = []
    for ch in raw["items"]:
        preview = ch.get("lastMessagePreview") or {}
        body = (preview.get("body") or {}).get("content") or ""
        chats.append({
            "topic": ch.get("topic") or ch.get("chatType"),
            "type": ch.get("chatType"),
            "last_message": body[:160],
            "last_at": preview.get("createdDateTime"),
        })
    return {"count": len(chats), "items": chats}


def _summarize_tasks(c: httpx.Client) -> dict:
    lists_raw = _get(c, "/me/todo/lists", {"$top": 10})
    if err := _passthrough_error(lists_raw):
        return err
    out_lists = []
    total = 0
    for lst in lists_raw["items"][:5]:
        tasks_raw = _get(c, f"/me/todo/lists/{lst['id']}/tasks", {"$top": 10, "$filter": "status ne 'completed'"})
        items = [] if tasks_raw.get("error") else [{
            "title": t.get("title"),
            "due": ((t.get("dueDateTime") or {}) or {}).get("dateTime"),
            "importance": t.get("importance"),
        } for t in tasks_raw.get("items", [])]
        total += len(items)
        out_lists.append({"name": lst.get("displayName"), "tasks": items})
    return {"count": total, "lists": out_lists}


def _summarize_presence(raw: dict) -> dict:
    if err := _passthrough_error(raw):
        return err
    p = raw["items"] if isinstance(raw["items"], dict) else {}
    return {"availability": p.get("availability"), "activity": p.get("activity")}


def list_mail_bodies(token: str, *, days: int = 60, top: int = 150) -> list[dict]:
    """Recent mails WITH their full plain-text body, for the personal knowledge index. The overview
    only keeps a 160-char preview of the 15 newest messages, which is why a word buried in a body
    (or in an older mail) was unsearchable. Best-effort: returns [] on any Graph error."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        raw = list_messages(token, since_iso=since, with_body=True, max_pages=max(1, (top + 49) // 50))
    except GraphError as exc:
        log.info("list_mail_bodies failed: %s", exc.detail)
        return []
    out = []
    for m in raw[:top]:
        sender = (m.get("from") or {}).get("emailAddress") or {}
        body = ((m.get("body") or {}).get("content") or m.get("bodyPreview") or "").strip()
        out.append({
            "id": m.get("id"), "subject": m.get("subject") or "(sans objet)",
            "from": sender.get("name") or sender.get("address") or "?", "from_email": sender.get("address") or "",
            "received": m.get("receivedDateTime"), "body": body[:20000],
        })
    return out


# ---------------------------------------------------------------- live assistant context

_MAIL_WORDS = ("mail", "e-mail", "email", "courriel", "message", "boite", "boîte", "inbox", "reçu",
               "recu", "envoyé", "envoye", "expéditeur", "expediteur")
_CAL_WORDS = ("meeting", "réunion", "reunion", "agenda", "calendrier", "calendar", "rendez-vous", "rdv",
              "planning", "dispo", "aujourd", "demain", "hier", "semaine", "call", "visio", "teams",
              "invitation", "créneau", "creneau", "événement", "evenement", "event")


def live_context(token: str, question: str, *, top_events: int = 40, top_mail: int = 12) -> dict:
    """Fresh Microsoft data relevant to a free-form question, for the personal assistant's /ask. Always
    fetches a small calendar window (so "meeting today with X" works even if the last scan predates the
    meeting) and, when the question is about mail, recent messages too. Best-effort: any failure (no
    consent, bad token, network) returns empty context so /ask still answers from the indexed data."""
    q = (question or "").lower()
    blocks: list[str] = []
    sources: list[dict] = []
    try:
        with _client(token) as c:
            cal = _summarize_events(_calendar_view(c, days_back=1, days_ahead=14, top=top_events))
            if not cal.get("error") and cal.get("items"):
                lines = ["<agenda_temps_reel note=\"réunions de la dernière journée aux 14 prochains jours\">"]
                for e in cal["items"]:
                    who = ", ".join(e.get("attendees") or []) or "—"
                    lines.append(f"- {e.get('start') or '?'} → {e.get('end') or '?'} | « {e.get('subject') or 'réunion'} » | "
                                 f"organisateur: {e.get('organizer') or '?'} | participants: {who} | lieu: {e.get('location') or '—'}"
                                 + (f" | lien: {e['join_url']}" if e.get('join_url') else ""))
                lines.append("</agenda_temps_reel>")
                blocks.append("\n".join(lines))
                sources.append({"type": "microsoft", "ref": "live:calendar", "label": "[Agenda Microsoft — temps réel]",
                                "excerpt": f"{len(cal['items'])} réunion(s) sur la période"})
            if any(w in q for w in _MAIL_WORDS):
                since = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
                mail = _summarize_mail(_get(c, "/me/messages", {
                    "$top": top_mail, "$orderby": "receivedDateTime desc",
                    "$select": "subject,from,receivedDateTime,bodyPreview,isRead,webLink",
                }))
                if not mail.get("error") and mail.get("items"):
                    lines = ["<mails_temps_reel note=\"messages les plus récents\">"]
                    for m in mail["items"]:
                        lines.append(f"- {m.get('received') or '?'} | de {m.get('from') or '?'} | « {m.get('subject') or '(sans objet)'} » | {m.get('preview') or ''}")
                    lines.append("</mails_temps_reel>")
                    blocks.append("\n".join(lines))
                    sources.append({"type": "microsoft", "ref": "live:mail", "label": "[Boîte mail Microsoft — temps réel]",
                                    "excerpt": f"{len(mail['items'])} message(s) récent(s)"})
    except Exception as exc:  # noqa: BLE001 — never let a live lookup break /ask
        log.info("live_context failed: %s", exc)
        return {"text": "", "sources": []}
    return {"text": "\n\n".join(blocks), "sources": sources}


# ---------------------------------------------------------------- mail (automations)

def _raise_for(r: httpx.Response, what: str) -> None:
    if r.status_code < 400:
        return
    try:
        message = r.json().get("error", {}).get("message") or r.text[:300]
    except ValueError:
        message = r.text[:300]
    if r.status_code == 401:
        raise GraphError(401, "Jeton Microsoft invalide ou expiré : reconnectez votre compte Microsoft")
    if r.status_code == 403:
        raise GraphError(403, f"Permission Microsoft non accordée pour {what} ({message})")
    raise GraphError(r.status_code, f"{what} : {message}")


def list_messages(token: str, *, since_iso: str, has_attachment: bool = False, with_body: bool = False, max_pages: int = 4) -> list[dict]:
    """Recent inbox messages (newest first) received since `since_iso`, raw Graph shape. Filtering
    on sender/subject/body text is done by the caller: Graph can't `contains` on `from`, and
    `$search` can't be combined with `$filter`."""
    flt = f"receivedDateTime ge {since_iso}" + (" and hasAttachments eq true" if has_attachment else "")
    select = "id,subject,from,receivedDateTime,bodyPreview,hasAttachments" + (",body" if with_body else "")
    params: dict | None = {"$top": 50, "$orderby": "receivedDateTime desc", "$filter": flt, "$select": select}
    headers = {"Prefer": 'outlook.body-content-type="text"'} if with_body else {}
    out: list[dict] = []
    url = "/me/messages"
    with _client(token) as c:
        for _ in range(max_pages):
            try:
                r = c.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise GraphError(502, f"Microsoft Graph injoignable : {exc}") from exc
            _raise_for(r, "la lecture des mails (Mail.Read)")
            body = r.json()
            out.extend(body.get("value", []))
            url, params = body.get("@odata.nextLink"), None
            if not url:
                break
    return out


def list_attachments(token: str, message_id: str) -> list[dict]:
    with _client(token) as c:
        try:
            r = c.get(f"/me/messages/{message_id}/attachments", params={"$select": "id,name,size,contentType,isInline"})
        except httpx.HTTPError as exc:
            raise GraphError(502, f"Microsoft Graph injoignable : {exc}") from exc
    _raise_for(r, "la lecture des pièces jointes (Mail.Read)")
    return r.json().get("value", [])


def get_attachment(token: str, message_id: str, attachment_id: str) -> dict:
    """Full attachment incl. base64 `contentBytes` (file attachments only)."""
    with _client(token) as c:
        try:
            r = c.get(f"/me/messages/{message_id}/attachments/{attachment_id}")
        except httpx.HTTPError as exc:
            raise GraphError(502, f"Microsoft Graph injoignable : {exc}") from exc
    _raise_for(r, "la lecture des pièces jointes (Mail.Read)")
    return r.json()


def send_mail(token: str, message: dict) -> None:
    """POST /me/sendMail — needs the delegated Mail.Send scope."""
    with _client(token) as c:
        try:
            r = c.post("/me/sendMail", json={"message": message, "saveToSentItems": True})
        except httpx.HTTPError as exc:
            raise GraphError(502, f"Microsoft Graph injoignable : {exc}") from exc
    _raise_for(r, "l'envoi de mails (Mail.Send)")
