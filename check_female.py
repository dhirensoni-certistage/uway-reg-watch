import os, sys, json, base64, requests
from datetime import datetime, timezone, timedelta

EVENT = "a36311c9-758f-482d-9c97-8d327a396c16"
URL = f"https://app.vedaevents.ai/api/v1/events/{EVENT}/dashboard?section=transactions&days=30"
TICKET_NAME = "Female Player Registration"
COUNT_TICKETS = ["Male Player Registration", "Female Player Registration"]  # website footer count
LIMIT = 22000
THRESHOLDS = [21700, 21900, 21950, 21980, 22000]
SUMMARY_EVERY_MIN = 60   # har itne min me silent count update (0 = band)

TOKEN = os.environ["VEDA_TOKEN"]
CF = os.environ.get("VEDA_CF", "")
NTFY = os.environ["NTFY_URL"]
STATE = "veda_state.json"
STATUS = "status.json"
IST = timezone(timedelta(hours=5, minutes=30))


def _hdr(s):
    try:
        s.encode("latin-1")
        return s
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(s.encode("utf-8")).decode("ascii") + "?="


def notify(title, msg, priority="default", tags="bell"):
    requests.post(NTFY, data=msg.encode("utf-8"),
                  headers={"Title": _hdr(title), "Priority": priority, "Tags": tags}, timeout=15)


def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"alerted": [], "history": [], "err_alerted": False}


def save(s):
    json.dump(s, open(STATE, "w"), indent=2)


def update_status(**fields):
    try:
        st = json.load(open(STATUS))
    except Exception:
        st = {"girls_open": True,
              "message": "Girls Registrations Closed — Thank you for the overwhelming response!"}
    if any(st.get(k) != v for k, v in fields.items()):
        st.update(fields)
        json.dump(st, open(STATUS, "w"), indent=2, ensure_ascii=False)


def find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find(v, key)
            if r is not None:
                return r
    return None


def fetch_counts():
    h = {
        "authorization": f"Bearer {TOKEN}",
        "organizer_access_token": TOKEN,
        "x-event-id": EVENT,
        "accept": "application/json, text/plain, */*",
        "referer": f"https://app.vedaevents.ai/owner/home/host-event/event-overview/dashboard?eventId={EVENT}&tab=transaction",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    }
    cookies = {"cf_clearance": CF} if CF else {}
    r = requests.get(URL, headers=h, cookies=cookies, timeout=30)
    if r.status_code != 200:
        print(f"Veda response {r.status_code}: {r.text[:300]}")
    r.raise_for_status()
    rows = find(r.json(), "ticketWiseRegistration")
    if not rows:
        raise RuntimeError("ticketWiseRegistration response me nahi mila")
    counts = {row.get("ticket_name"): int(row.get("sold") or 0) for row in rows}
    if TICKET_NAME not in counts:
        raise RuntimeError(f"'{TICKET_NAME}' row nahi mili")
    return counts


def main():
    s = load()
    now = datetime.now(IST)

    try:
        counts = fetch_counts()
    except Exception as e:
        print("ERROR:", e)
        if not s.get("err_alerted"):
            notify("⚠️ Veda bot ERROR",
                   f"Count nahi mila: {e}\n\nToken expire hua? VEDA_TOKEN secret update karo.",
                   "high", "warning")
            s["err_alerted"] = True
            save(s)
        sys.exit(1)

    sold = counts[TICKET_NAME]
    registered = sum(counts.values())

    if s.get("err_alerted"):
        notify("✅ Veda bot wapas chalu", f"Female sold: {sold:,}", "default", "white_check_mark")
        s["err_alerted"] = False

    # speed estimate (last ~60 min)
    s["history"] = (s.get("history") or [])[-30:]
    s["history"].append({"t": now.isoformat(), "n": sold})
    eta = ""
    old = [p for p in s["history"] if (now - datetime.fromisoformat(p["t"])).total_seconds() <= 3600]
    if len(old) >= 2:
        hrs = (now - datetime.fromisoformat(old[0]["t"])).total_seconds() / 3600
        rate = (sold - old[0]["n"]) / hrs if hrs > 0 else 0
        if rate > 0:
            eta = f"\nSpeed ~{rate:.0f}/hr → 22,000 approx {(LIMIT - sold) / rate:.1f} hr me"

    left = LIMIT - sold
    print(f"{now:%d-%m %H:%M} Female sold={sold:,} left={left} registered={registered:,}{eta}")

    # website footer count
    update_status(registered=registered, female_sold=sold)

    # hourly silent summary
    if SUMMARY_EVERY_MIN:
        last = s.get("last_summary")
        due = (not last) or (now - datetime.fromisoformat(last)).total_seconds() >= SUMMARY_EVERY_MIN * 60
        if due:
            notify(f"Female {sold:,} / 22,000 — {left} bache",
                   f"Update {now:%d %b %H:%M}\nTotal registered (M+F): {registered:,}{eta}",
                   "min", "bar_chart")
            s["last_summary"] = now.isoformat()

    crossed = [t for t in THRESHOLDS if sold >= t and t not in s["alerted"]]
    if crossed:
        top = max(crossed)
        if top >= LIMIT:
            update_status(girls_open=False)
            notify("🚨 22,000 HO GAYE — ABHI BAND KARO",
                   f"Female sold: {sold:,}\n\nWebsite auto-closed ho gayi (status.json).\nAb Veda me Female ticket OFF karo!",
                   "urgent", "rotating_light")
        else:
            notify(f"Female {sold:,} — sirf {left} bache",
                   f"Threshold {top:,} cross ho gaya.{eta}\n\nWebsite + Veda band karne ki taiyari rakho.",
                   "high", "warning")
        s["alerted"] = sorted(set(s["alerted"] + crossed))

    save(s)


if __name__ == "__main__":
    main()