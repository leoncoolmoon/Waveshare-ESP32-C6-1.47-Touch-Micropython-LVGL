# plugins/calendar.py -- 月历视图插件。
#
# 参考版本里日历是用 lv.calendar（LVGL 原生控件）画的，但这个固件栈
# 里没有 lv，只有 DISPLAY.text/rect 这些基础绘图接口。好在算出"某个
# 月 1 号是星期几"、"这个月有几天"这些纯粹是整数运算（不需要 lv、也
# 不需要额外的日期库），bangle_utils.py 里已经有 days_from_civil() /
# weekday_from_days() / days_in_month() 这几个辅助函数了，这里直接
# 拿来手绘一个 7 列网格。
#
#   handle_event   接 Gadgetbridge 的 "calendar"/"calendar-" 事件，
#                  记一下哪天有事件（画网格时在日期下面点一个小点），
#                  同时保留原来"弹一条提示"的行为
#   handle_keys    UP/DOWN 翻月份（跟别的视图 UP/DOWN 滚动的默认行为
#                  冲突，所以只在当前正好是日历视图时才接管）
from display_core import *
from bangle_utils import days_from_civil, weekday_from_days, days_in_month
import ble_bridge

VIEW_ID = "Calendar"
ICON = "⏰C"
TITLE = "⏰ Calendar"

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_WEEKDAY_HEADER = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")


def init_state():
    return {
        "event_dates": {},       # (year, month, day) -> 当天事件数
        "event_id_to_date": {},  # 事件 id -> (year, month, day)，方便 calendar- 删除时定位
        "shown_year_month": None,  # UP/DOWN 翻页翻到了哪个月；None 表示还没翻过页，用今天所在的月
    }


def _get_current_date():
    """RTC 里存的已经是本地时间的年月日（main.py 的 on_time_sync() 用
    epoch_to_rtc_tuple() 换算成本地时间才写进去的），这里直接读前三个
    字段就是今天的本地日期，不需要再做任何时区换算。"""
    if RTC is not None:
        try:
            year, month, day, _wd, _h, _mi, _s, _sub = RTC.datetime()
            return year, month, day
        except Exception:
            pass
    return 2026, 1, 1


def _parse_event_date(timestamp):
    """Gadgetbridge 的日历事件时间戳是 UTC epoch（有的手机端发秒，有的
    发毫秒），换算成本地日期用来在网格上标点。"""
    if timestamp is None:
        return None
    try:
        ts = int(timestamp)
        if ts > 10000000000:  # 毫秒 -> 秒
            ts = ts // 1000
        local_ts = ts + int(ble_bridge.tz_offset * 3600)
        year, month, day, _h, _mi, _s, _wd, _yd = time.localtime(local_ts)
        return (year, month, day)
    except Exception:
        return None


def _record_event(state, event_id, date_tuple):
    if date_tuple is None:
        return
    old_date = state["event_id_to_date"].get(event_id)
    if old_date == date_tuple:
        return
    if old_date is not None:
        _decrement(state, old_date)
    state["event_dates"][date_tuple] = state["event_dates"].get(date_tuple, 0) + 1
    if event_id is not None:
        state["event_id_to_date"][event_id] = date_tuple


def _decrement(state, date_tuple):
    count = state["event_dates"].get(date_tuple, 0) - 1
    if count <= 0:
        state["event_dates"].pop(date_tuple, None)
    else:
        state["event_dates"][date_tuple] = count


def handle_event(t, data, state, screen):
    if t == "calendar":
        title = data.get("title")
        event_id = data.get("id")
        date_tuple = _parse_event_date(data.get("timestamp"))
        _record_event(state, event_id, date_tuple)
        screen.show_temp_message(f"📅 {title}" if title else "📅 Calendar event added", 2)
        return True

    if t == "calendar-":
        event_id = data.get("id")
        old_date = state["event_id_to_date"].pop(event_id, None)
        if old_date is not None:
            _decrement(state, old_date)
        screen.show_temp_message("Calendar event removed", 1)
        return True

    return False


def handle_keys(keys, state, screen):
    """UP/DOWN 翻上一月/下一月，只在当前正好停在日历视图时才接管这轮
    按键（plugin_manager.dispatch_keys 已经保证了这一点）。"""
    if "UP" in keys:
        delta = -1
    elif "DOWN" in keys:
        delta = 1
    else:
        return False

    year, month = state.get("shown_year_month") or _get_current_date()[:2]
    month += delta
    if month < 1:
        month = 12
        year -= 1
    elif month > 12:
        month = 1
        year += 1

    state["shown_year_month"] = (year, month)
    screen.show_temp_message(f"{_MONTH_NAMES[month - 1]} {year}", 1)
    return True


def draw(state):
    year, month = state.get("shown_year_month") or _get_current_date()[:2]
    today = _get_current_date()

    y = _CONTENT_Y_START + 2

    # 标题：月份 + 年份，居中
    title = f"{_MONTH_NAMES[month - 1]} {year}"
    tx = (_MH_DISPLAY_WIDTH - len(title) * _CHAR_WIDTH) // 2
    DISPLAY.text(title, tx, y, PALETTE[8])
    y += _LINE_HEIGHT + 2

    # 星期表头
    cell_w = _MH_DISPLAY_WIDTH // 7
    for i, wd in enumerate(_WEEKDAY_HEADER):
        cx = i * cell_w + (cell_w - len(wd) * _CHAR_WIDTH) // 2
        DISPLAY.text(wd, cx, y, PALETTE[6])
    y += _LINE_HEIGHT + 2

    # 日期网格：1 号是星期几，用 days_from_civil() + weekday_from_days()
    # 纯整数运算算出来，不用查表也不用 RTC 之外的任何东西。
    first_weekday = weekday_from_days(days_from_civil(year, month, 1))  # 0=Mon..6=Sun
    total_days = days_in_month(year, month)

    grid_y_limit = _CONTENT_Y_START + _CONTENT_HEIGHT
    rows_needed = -(-(first_weekday + total_days) // 7)  # 向上取整
    row_h = max(_LINE_HEIGHT + 2, (grid_y_limit - y) // max(1, rows_needed))

    day = 1
    col = first_weekday
    row = 0
    while day <= total_days and y + row * row_h < grid_y_limit:
        cx = col * cell_w
        cy = y + row * row_h
        day_str = str(day)
        date_tuple = (year, month, day)
        is_today = date_tuple == today
        has_event = date_tuple in state["event_dates"]

        if is_today:
            DISPLAY.rect(cx + 2, cy - 1, cell_w - 4, _LINE_HEIGHT, PALETTE[4], fill=True)
            text_color = _COLOR_BLACK
        else:
            text_color = PALETTE[8]

        tx = cx + (cell_w - len(day_str) * _CHAR_WIDTH) // 2
        DISPLAY.text(day_str, tx, cy, text_color)

        if has_event and not is_today:
            dot_x = cx + cell_w // 2 - 1
            dot_y = min(cy + _LINE_HEIGHT, grid_y_limit - 2)
            DISPLAY.rect(dot_x, dot_y, 2, 2, PALETTE[4], fill=True)

        col += 1
        if col > 6:
            col = 0
            row += 1
        day += 1
