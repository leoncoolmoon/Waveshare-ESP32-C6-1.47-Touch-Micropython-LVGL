"""gb_alarm - Parse, store, and query Gadgetbridge/Bangle.js-style alarms.

Meant to live in `lib/` so any app can `from lib import gb_alarm` (or
`from lib.gb_alarm import ...`, depending on where you drop this file) and
share one alarm config across apps, instead of every app reinventing it.

Message format (from Gadgetbridge, see https://www.espruino.com/Gadgetbridge):
    GB({"t":"alarm", "d":[{"h":6,"m":30,"rep":127}]})
  - h, m: hour (0-23) / minute (0-59) the alarm should fire
  - rep: bitmask of which weekdays it repeats on
  - on: optional, 1/0 for enabled/disabled (defaults to enabled)

IMPORTANT / UNVERIFIED: which bit of `rep` corresponds to which weekday
isn't documented anywhere I could confirm. This module assumes
    bit 0 = Monday, bit 1 = Tuesday, ... bit 6 = Sunday
(so rep=127 = every day, rep=0b0111110 = weekdays only, etc). If alarms
come out firing on the wrong day, that assumption is the first thing to
check -- set a single-day alarm in Gadgetbridge, print the `rep` value you
actually receive, and adjust _WEEKDAY_BITS below to match.
"""

import re

try:
    import ujson as json
except ImportError:
    import json


_ALARM_FILE = "/alarms.json"

# index 0 = Monday .. index 6 = Sunday, matching civil_from_days()-style
# weekday numbering used elsewhere in this project (Monday=0). See the
# big warning in the module docstring -- verify this against real hardware.
_WEEKDAY_BITS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_alarm_message(alarm_list):
    """Turn the raw `d` list from a `{"t":"alarm","d":[...]}` message into
    a clean list of alarm dicts: {hour, minute, repeat_mask, enabled, days}.

    `days` is a human-readable list of weekday abbreviations the alarm
    repeats on, purely for display/debugging -- derived from repeat_mask
    using the (currently unverified) bit order above.
    """
    alarms = []
    for raw in alarm_list:
        try:
            hour = int(raw["h"])
            minute = int(raw["m"])
        except (KeyError, ValueError, TypeError) as e:
            print("[gb_alarm] skipping malformed alarm entry:", raw, e)
            continue
        repeat_mask = int(raw.get("rep", 0))
        enabled = bool(int(raw.get("on", 1)))
        days = [_WEEKDAY_BITS[i] for i in range(7) if repeat_mask & (1 << i)]
        alarms.append({
            "hour": hour,
            "minute": minute,
            "repeat_mask": repeat_mask,
            "enabled": enabled,
            "days": days,
        })
    return alarms


def save_alarms(alarms, path=_ALARM_FILE):
    """Persist the alarm list to flash so it survives a reboot."""
    try:
        with open(path, "w") as f:
            json.dump(alarms, f)
        return True
    except Exception as e:
        print("[gb_alarm] failed to save alarms:", e)
        return False


def load_alarms(path=_ALARM_FILE):
    """Load previously-saved alarms, or [] if none exist yet."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []


def weekday_matches(repeat_mask, weekday_mon0):
    """Check whether `repeat_mask` includes the given weekday.

    weekday_mon0: 0=Monday .. 6=Sunday (matches epoch_to_rtc_tuple()'s
    convention in the notification app, and _WEEKDAY_BITS above).
    """
    return bool(repeat_mask & (1 << weekday_mon0))


def next_alarm(alarms, now_hour, now_minute, now_weekday_mon0):
    """Find the soonest upcoming enabled alarm, given the current time.

    now_weekday_mon0: 0=Monday .. 6=Sunday.
    Returns (days_from_now, alarm_dict) for the soonest match, or None if
    there are no enabled alarms at all.

    This just picks the earliest (day_offset, hour, minute) match across
    the next 7 days -- it does not account for DST or timezone changes
    happening in between.
    """
    best = None
    for alarm in alarms:
        if not alarm.get("enabled", True):
            continue
        mask = alarm.get("repeat_mask", 0)
        # If repeat_mask is 0, treat it as a one-shot alarm for the very
        # next occurrence of that hour:minute (today if it hasn't passed
        # yet, otherwise tomorrow) -- rather than "never repeats, never
        # fires", which wouldn't be useful.
        candidate_days = range(7) if mask else range(2)
        for day_offset in candidate_days:
            weekday = (now_weekday_mon0 + day_offset) % 7
            if mask and not weekday_matches(mask, weekday):
                continue
            if day_offset == 0:
                fires_today = (alarm["hour"], alarm["minute"]) > (now_hour, now_minute)
                if mask and not fires_today:
                    continue
                if not mask and not fires_today:
                    continue  # one-shot: only "today" if still ahead of now
            key = (day_offset, alarm["hour"], alarm["minute"])
            if best is None or key < best[0]:
                best = (key, alarm)
            break  # found the soonest day_offset for this alarm, stop scanning it further
    if best is None:
        return None
    (day_offset, _, _), alarm = best
    return day_offset, alarm