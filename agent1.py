import json
import re
from datetime import datetime
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

try:  # cards need a recent uagents_core; fall back to plain text if missing
    from uagents_core.contrib.protocols.chat import MetadataContent
    HAS_CARDS = True
except Exception:
    HAS_CARDS = False

# Set to True to send interactive cards; False sends plain text only (safest for first test).
CARDS_ENABLED = False
HAS_CARDS = HAS_CARDS and CARDS_ENABLED

agent = Agent()
chat_proto = Protocol(spec=chat_protocol_spec)

CITIES = ["Pune", "Mumbai", "Delhi", "Noida", "Ghaziabad", "Meerut",
          "Bengaluru", "Bangalore", "Hyderabad", "Chennai", "Kolkata"]
WORDS = {"ek": 1, "one": 1, "do": 2, "two": 2, "teen": 3, "three": 3,
         "char": 4, "four": 4, "paanch": 5, "five": 5}
MIN_DAYS = 90
SAFETY = ("Demo data only. Bank staff confirm release and cross-matching. "
          "In a real emergency call 108 (India) or go to the nearest hospital.")

BANKS = [
    {"id": "A", "name": "Sanjeevani Blood Bank", "city": "Pune", "d": 2.1, "crossmatch": True, "replacement": False,
     "stock": {"A+": 11, "A-": 1, "B+": 9, "B-": 2, "O+": 12, "O-": 0, "AB+": 3, "AB-": 0}},
    {"id": "B", "name": "LifeLine Blood Centre", "city": "Pune", "d": 4.6, "crossmatch": True, "replacement": True,
     "stock": {"A+": 6, "A-": 0, "B+": 7, "B-": 1, "O+": 8, "O-": 1, "AB+": 2, "AB-": 1}},
    {"id": "C", "name": "City General Blood Bank", "city": "Pune", "d": 7.8, "crossmatch": True, "replacement": False,
     "stock": {"A+": 9, "A-": 2, "B+": 4, "B-": 0, "O+": 10, "O-": 3, "AB+": 1, "AB-": 0}},
    {"id": "D", "name": "Redleaf Blood Bank", "city": "Delhi", "d": 12.4, "crossmatch": False, "replacement": True,
     "stock": {"A+": 5, "A-": 1, "B+": 3, "B-": 2, "O+": 6, "O-": 2, "AB+": 0, "AB-": 0}},
]
DONORS = [
    {"id": "1", "group": "O-", "days": 40, "consent": True, "avail": True, "d": 1.4},
    {"id": "2", "group": "O-", "days": 140, "consent": True, "avail": True, "d": 3.1},
    {"id": "3", "group": "O-", "days": 200, "consent": False, "avail": True, "d": 2.2},
    {"id": "4", "group": "O-", "days": 160, "consent": True, "avail": False, "d": 4.0},
    {"id": "5", "group": "B+", "days": 120, "consent": True, "avail": True, "d": 2.8},
    {"id": "6", "group": "A-", "days": 95, "consent": True, "avail": True, "d": 5.2},
    {"id": "7", "group": "B-", "days": 300, "consent": True, "avail": True, "d": 3.6},
    {"id": "8", "group": "A+", "days": 30, "consent": True, "avail": True, "d": 2.0},
    {"id": "9", "group": "O+", "days": 110, "consent": True, "avail": True, "d": 6.5},
]

HELP = ("Namaste! I am RaktSetu, a blood-finding agent. Tell me the blood group, units, city and hospital, "
        "in English or Hinglish. Example: 'Urgent: need 2 units O- in Pune at Ruby Hall Hospital'.")


# ---------------------------------------------------------------- logic
def can_give(donor: str, recipient: str) -> bool:
    return (donor[-1] == "-" or recipient[-1] == "+") and (
        donor[:-1] == "O" or donor[:-1] == recipient[:-1] or recipient[:-1] == "AB")


def parse(text: str) -> dict:
    t = text.replace("\u2212", "-").replace("\u2013", "-")
    g = re.search(r"\b(ab|a|b|o)\s*(\+|-|pos(?:itive)?|neg(?:ative)?|ve\b)", t, re.I)
    group = None
    if g:
        group = g.group(1).upper() + ("+" if re.match(r"^(\+|p)", g.group(2), re.I) else "-")
    u = re.search(r"(\d+)\s*(?:units?|bottles?|bags?)", t, re.I) or \
        re.search(r"\b(ek|do|teen|char|paanch|one|two|three|four|five)\s*(?:units?|bottles?|bags?)", t, re.I)
    units = 1
    if u:
        units = int(u.group(1)) if u.group(1).isdigit() else WORDS[u.group(1).lower()]
    city = next((c for c in CITIES if re.search(r"\b" + c + r"\b", t, re.I)), None)
    if city == "Bangalore":
        city = "Bengaluru"
    hm = re.search(r"((?:[A-Z][A-Za-z.']*\s+){0,2}(?:Hospital|Clinic|AIIMS|Institute|Centre|Center|Medical))", t)
    hospital = hm.group(1).strip() if hm else None
    urgent = bool(re.search(r"urgent|emergency|asap|critical|now|jaldi", t, re.I))
    return {"group": group, "units": max(1, min(units, 10)), "city": city, "hospital": hospital, "urgent": urgent}


def bank_offers(group: str, units: int, city):
    """Each bank agent answers for itself; returns offers ranked best-first."""
    offers = []
    for b in BANKS:
        local = (not city) or city == b["city"]
        s = b["stock"].get(group, 0)
        eta = round(b["d"] / 22 * 60) + 10 + (0 if local else 240)
        notes = []
        if not local:
            notes.append("different city")
        if b["crossmatch"]:
            notes.append("cross-match needed")
        if b["replacement"] and units >= 2:
            notes.append("replacement donor policy")
        offers.append({"bank": b, "local": local, "stock": s, "can": min(s, units), "eta": eta, "notes": notes})
    ranked = [o for o in offers if o["can"] > 0]
    ranked.sort(key=lambda o: (not o["local"], not (o["can"] >= units), o["eta"], -o["stock"]))
    return ranked


def donor_fallback(need: int, group: str, req: str):
    lines, skipped = [], []
    for d in sorted([d for d in DONORS if can_give(d["group"], group)], key=lambda d: d["d"]):
        if not need:
            break
        if d["days"] < MIN_DAYS:
            skipped.append(f"donor {d['id']}: gave blood {d['days']} days ago, not yet eligible")
        elif not d["consent"]:
            skipped.append(f"donor {d['id']}: no consent to be contacted")
        elif not d["avail"]:
            skipped.append(f"donor {d['id']}: not available right now")
        else:
            d["avail"] = False
            need -= 1
            eta = round(d["d"] / 22 * 60) + 15
            lines.append(f"- Donor {d['id']} ({d['group']}) pinged, ~{d['d']} km, ETA ~{eta} min, ref DON-{req}-{d['id']}")
    return need, lines, skipped


def reserve(bank_id: str, group: str, units: int) -> str:
    bank = next((b for b in BANKS if b["id"] == bank_id), None)
    if not bank:
        return "I could not find that blood bank. Please send your request again."
    req = uuid4().hex[:4].upper()
    n = min(units, bank["stock"].get(group, 0))
    bank["stock"][group] -= n
    out = [f"Reservation {req}: {n} of {units} unit(s) of {group} held at {bank['name']} ({bank['city']}), "
           f"~{bank['d']} km, ref RES-{req}-{bank['id']}."]
    need = units - n
    if need > 0:
        out.append(f"Banks covered {n} of {units}. Falling back to eligible donors for {need}:")
        need, lines, skipped = donor_fallback(need, group, req)
        out += lines or ["- No eligible donor available right now."]
        if skipped:
            out += ["Donors skipped (safety rules):"] + ["- " + s for s in skipped[:3]]
        if need:
            out.append(f"Still searching for {need} unit(s). Please call 108.")
    out += ["", SAFETY]
    return "\n".join(out)


# ---------------------------------------------------------------- cards
def card_content(kind: str, payload: dict, terminal: bool = False):
    md = {"card_protocol_version": "1", "requires_card_interaction": "true",
          "card_kind": kind, "card_payload": json.dumps(payload)}
    if terminal:
        md["is_terminal"] = "true"
    return MetadataContent(type="metadata", metadata=md)


def carousel_for(p: dict, ranked: list) -> dict:
    items = []
    for o in ranked[:4]:
        b = o["bank"]
        badges = [{"label": "Same city" if o["local"] else "Other city", "variant": "info"}]
        badges.append({"label": "Covers all" if o["can"] >= p["units"] else "Partial", "variant": "info"})
        items.append({
            "id": b["id"],
            "title": b["name"],
            "subtitle": f"{b['city']} \u00b7 {b['d']} km \u00b7 ETA ~{o['eta']} min",
            "badges": badges,
            "secondary_text": f"{o['stock']} unit(s) {p['group']}",
            "primary_cta": {"label": "Select", "selection": {
                "action": "select_bank", "bank_id": b["id"], "blood_group": p["group"],
                "units": str(p["units"]), "city": p["city"] or "any"}},
        })
    return {"title": "Choose a blood bank",
            "subtitle": f"{p['units']} unit(s) of {p['group']}" + (f" in {p['city']}" if p["city"] else ""),
            "items": items}


def review_for(bank_id: str, group: str, units: int) -> dict:
    b = next(x for x in BANKS if x["id"] == bank_id)
    sel = {"bank_id": bank_id, "blood_group": group, "units": str(units)}
    return {"title": "Confirm reservation",
            "summary_rows": [
                {"label": "Blood bank", "value": b["name"]},
                {"label": "City", "value": b["city"]},
                {"label": "Blood group", "value": group},
                {"label": "Units", "value": str(units)},
                {"label": "Distance", "value": f"{b['d']} km"}],
            "approve_cta": {"label": "Confirm reservation", "primary": True,
                            "selection": {"action": "confirm", **sel}},
            "reject_cta": {"label": "Cancel", "selection": {"action": "cancel", **sel}}}


# ---------------------------------------------------------------- selections
def read_selection(text: str):
    """ASI:One returns a JSON string (direct @mention) or planner prose. Handle both."""
    try:
        d = json.loads(text)
        if isinstance(d, dict) and d.get("action"):
            return d
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict) and d.get("action"):
                return d
        except Exception:
            pass
    low = text.lower()
    action = ("select_bank" if "select_bank" in low else "confirm" if "confirm" in low
              else "cancel" if re.search(r"cancel|reject", low) else None)
    if not action:
        return None
    b = re.search(r"bank_id\W+([A-D])\b", text, re.I)
    g = re.search(r"blood_group\W+(AB|A|B|O)\s*([+-])", text, re.I)
    u = re.search(r"units\W+(\d+)", text, re.I)
    c = re.search(r"city\W+([A-Za-z]+)", text, re.I)
    return {"action": action,
            "bank_id": b.group(1).upper() if b else None,
            "blood_group": (g.group(1).upper() + g.group(2)) if g else None,
            "units": u.group(1) if u else "1",
            "city": c.group(1) if c else "any"}


def respond(text: str):
    """Returns (text_to_show, optional (card_kind, payload))."""
    sel = read_selection(text)
    if sel:
        action, bid, grp = sel["action"], sel.get("bank_id"), sel.get("blood_group")
        units = int(str(sel.get("units") or 1)) if str(sel.get("units") or "1").isdigit() else 1
        if action == "cancel":
            return "Cancelled. No blood was reserved. Send a new request any time.", None
        if action == "select_bank" and bid and grp:
            return ("Please review and confirm your reservation.", ("review", review_for(bid, grp, units)))
        if action == "confirm" and bid and grp:
            return reserve(bid, grp, units), None
        return "I could not read that selection. Please send your request again.", None

    p = parse(text)
    if not p["group"]:
        return "I could not find a blood group in your message. Please include one, like O- or B+. " + HELP, None
    ranked = bank_offers(p["group"], p["units"], p["city"])
    head = f"RaktSetu: {p['units']} unit(s) of {p['group']}" + (f" in {p['city']}" if p["city"] else "") \
        + (f" for {p['hospital']}" if p["hospital"] else "") + (" (URGENT)" if p["urgent"] else "")
    if not ranked:
        _, lines, skipped = donor_fallback(p["units"], p["group"], uuid4().hex[:4].upper())
        return "\n".join([head, "", "No bank has this group in stock right now. Trying eligible donors:"]
                         + (lines or ["- No eligible donor available. Please call 108."]) + ["", SAFETY]), None
    if not HAS_CARDS:  # plain-text fallback: auto-reserve the top bank
        return head + "\n\n" + reserve(ranked[0]["bank"]["id"], p["group"], p["units"]), None
    return (head + f"\n\nI asked {len(BANKS)} bank agents. {len(ranked)} can help. Pick one below.",
            ("carousel", carousel_for(p, ranked)))


# ---------------------------------------------------------------- chat protocol
def make_msg(text: str, card=None) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    if card and HAS_CARDS:
        content.append(card_content(card[0], card[1]))
    return ChatMessage(timestamp=datetime.utcnow(), msg_id=uuid4(), content=content)


@chat_proto.on_message(ChatMessage)
async def handle_message(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))
    text = " ".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
    text = re.sub(r"@\S+\s*", "", text).strip()  # drop the @agent mention ASI:One adds
    ctx.logger.info(f"Request from {sender}: {text}")
    try:
        shown, card = respond(text) if text else (HELP, None)
    except Exception as e:  # never crash the agent
        ctx.logger.error(f"error: {e}")
        shown, card = "Sorry, something went wrong. Please resend your request.", None
    await ctx.send(sender, make_msg(shown, card))


@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    ctx.logger.info(f"Ack from {sender} for {msg.acknowledged_msg_id}")


agent.include(chat_proto, publish_manifest=True)
