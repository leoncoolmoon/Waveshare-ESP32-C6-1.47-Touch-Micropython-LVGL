# plugins/weather.py -- 天气界面插件
# 一个"带界面"的插件长这样：VIEW_ID/ICON/TITLE 三件套 + draw()，
# 外加两个可选的事件钩子：
#   handle_event          接 Gadgetbridge 专门发的 weather 事件
#   on_notification_guess 没有专门事件时，从普通通知文字里启发式猜
# DISPLAY/PALETTE/time 这些都从 display_core 通配导入拿，不用传参。
import re
from display_core import *
from bangle_utils import truncate

VIEW_ID = "weather"
ICON = "W"
TITLE = "Weather"

# 注意 MicroPython 的 re 模块不支持 re.IGNORECASE，匹配前手动转小写。
_TEMP_RE = re.compile(r"(-?\d{1,3})\s*(?:°|℃|℉|度)")
_HI_LO_RE = re.compile(
    r"(?:hi|high|最高|↑|h:)[:\s]{0,3}(-?\d{1,3}).{0,10}?(?:lo|low|最低|↓|l:)[:\s]{0,3}(-?\d{1,3})"
)
_HUMIDITY_RE = re.compile(r"(?:湿度|humidity)[:\s]{0,3}(\d{1,3})\s*%")
_WIND_RE = re.compile(r"(?:风速|wind)[:\s]{0,3}(\d{1,3})\s*(?:km/h|kph|mph)?")

_WEATHER_APP_HINTS = (
    "weather", "tianqi", "彩云", "墨迹", "accuweather", "carrot",
    "weathernews", "moji", "1weather", "windy", "yr.no",
)
_WEATHER_CONDITION_WORDS = (
    "sunny", "clear", "cloudy", "overcast", "rain", "shower", "storm",
    "thunder", "snow", "fog", "haze", "wind",
    "晴", "多云", "阴", "雨", "雷", "雪", "雾", "霾",
)


def init_state():
    return {
        "temp": "--", "hi": "--", "lo": "--", "humidity": "--",
        "wind": "--", "condition": "--", "uv": "--", "rain": "--",
        # "official" = 来自 Gadgetbridge 专门的 weather 事件
        # "notification" = 从普通通知里猜出来的备用数据
        # "none" = 还没有任何数据
        "source": "none",
        "updated_at": 0,
    }


def _apply(state, data, source):
    state["temp"] = data.get("temp", "--")
    state["hi"] = data.get("hi", "--")
    state["lo"] = data.get("lo", "--")
    state["humidity"] = data.get("hum", "--")
    state["wind"] = data.get("wind", "--")
    state["condition"] = data.get("txt", "--")
    state["uv"] = data.get("uv", "--")
    state["rain"] = data.get("rain", "--")
    state["source"] = source
    state["updated_at"] = time.time()


def _is_stale(state, max_age_seconds=1800):
    return (time.time() - state["updated_at"]) > max_age_seconds


def handle_event(t, data, state, screen):
    if t != "weather":
        return False
    _apply(state, data, source="official")
    screen.switch_view(VIEW_ID)
    screen.show_temp_message(f"Weather: {data.get('temp', '--')}°", 2)
    return True


def on_notification_guess(app_name, title, body, state, screen):
    # 已经有官方数据、而且还没过期的话，不用普通通知里的猜测数据覆盖它
    if state["source"] == "official" and not _is_stale(state):
        return

    app_name = app_name or ""
    text = " ".join(t for t in (title, body) if isinstance(t, str) and t)
    if not text:
        return

    text_lower = text.lower()
    temp_match = _TEMP_RE.search(text)
    if not temp_match:
        return

    looks_like_weather_app = any(hint in app_name.lower() for hint in _WEATHER_APP_HINTS)
    if not looks_like_weather_app:
        if not any(word in text_lower for word in _WEATHER_CONDITION_WORDS):
            return

    guess = {"temp": temp_match.group(1)}

    hi_lo = _HI_LO_RE.search(text_lower)
    if hi_lo:
        guess["hi"], guess["lo"] = hi_lo.group(1), hi_lo.group(2)

    hum = _HUMIDITY_RE.search(text_lower)
    if hum:
        guess["hum"] = hum.group(1)

    wind = _WIND_RE.search(text_lower)
    if wind:
        guess["wind"] = wind.group(1)

    condition_guess = title if isinstance(title, str) and title else text
    condition_guess = _TEMP_RE.sub("", condition_guess).strip(" ,.:：-")
    if condition_guess:
        guess["txt"] = truncate(condition_guess, 20)

    _apply(state, guess, source="notification")


def draw(state):
    w = state
    y = 2

    cond = w["condition"] if w["condition"] != "--" else "Unknown"
    DISPLAY.text(f"🌤 {cond}", 4, y, PALETTE[8])

    if w["source"] == "notification":
        tag = "guess"
        x = _MH_DISPLAY_WIDTH - len(tag) * _CHAR_WIDTH - 4
        DISPLAY.text(tag, x, y, PALETTE[5])

    y += _LINE_HEIGHT + 4

    temp_str = f"{w['temp']}°"
    if w["hi"] != "--" and w["lo"] != "--":
        temp_str += f"  (↑{w['hi']}° ↓{w['lo']}°)"
    DISPLAY.text(temp_str, 4, y, PALETTE[8])
    y += _LINE_HEIGHT + 2

    DISPLAY.line(4, y, _MH_DISPLAY_WIDTH - 4, y, PALETTE[5])
    y += 4

    col_width = _MH_DISPLAY_WIDTH // 2 - 8

    items = []
    if w["humidity"] != "--":
        items.append(("💧 Humidity", f"{w['humidity']}%"))
    if w["wind"] != "--":
        items.append(("💨 Wind", f"{w['wind']} km/h"))
    if w["uv"] != "--":
        items.append(("☀️ UV", w['uv']))
    if w["rain"] != "--":
        items.append(("🌧 Rain", f"{w['rain']}%"))

    row = 0
    for i in range(0, len(items), 2):
        x = 4
        if i < len(items):
            label, value = items[i]
            DISPLAY.text(f"{label}:", x, y + row * _LINE_HEIGHT, PALETTE[6])
            DISPLAY.text(value, x + 4, y + row * _LINE_HEIGHT + _LINE_HEIGHT // 2, PALETTE[8])
        if i + 1 < len(items):
            label, value = items[i + 1]
            x = col_width + 8
            DISPLAY.text(f"{label}:", x, y + row * _LINE_HEIGHT, PALETTE[6])
            DISPLAY.text(value, x + 4, y + row * _LINE_HEIGHT + _LINE_HEIGHT // 2, PALETTE[8])
        row += 1

    if not items:
        DISPLAY.text("No weather data", 4, y, PALETTE[5])
