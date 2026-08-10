# screen_manager.py -- 界面状态机 + 所有具体界面的绘制逻辑
from display_core import *
from bangle_utils import *
# tz_offset 是 ble_bridge.py 里一个会被改的模块级变量（手机同步时区的
# 时候用 global tz_offset 改掉）。如果写成 `from ble_bridge import
# tz_offset`，这里拿到的只是导入那一刻的快照，之后 ble_bridge 里改了，
# 这边不会跟着变——得用 `import ble_bridge` 然后每次都读
# `ble_bridge.tz_offset`，才能读到实时的值。
import ble_bridge
import plugin_manager

_CORE_VIEWS = (VIEW_CLOCK, VIEW_NOTIFICATIONS, VIEW_MUSIC, VIEW_STATUS)
_MAX_CHARS_PER_LINE = 17

class ScreenManager:

    def __init__(self):
        self.current_view = VIEW_CLOCK
        self.dirty = True
        self._last_view = None  # 记录上次视图，用于检测切换
        self.scroll_offset = 0
        self.max_scroll = 0

        # 通知历史 -- 每条: {app_name, title, body, is_bitmap, title_field, body_field, timestamp}
        self.notifications = []  # 最多保存20条
        self.max_notifications = 20

        # 当前音乐信息
        self.music_info = {
            "track": "",
            "artist": "",
            "album": "",
            "duration": 0,
            "track_num": 0,
            "track_count": 0,
            "playing": False,
            "position": 0,
            "shuffle": 0,
            "repeat": 0,
        }
        self.music_scroll_pos = 0
        self.music_scroll_timer = 0

        # 状态信息
        self.status = {
            "connected": False,
            "packet_count": 0,
            "last_event": "Waiting for connection...",
            "gps_active": False,
            "activity_active": False,
            "time_synced": False,
        }

        # 临时消息（显示几秒后消失）
        self.temp_message = None
        self.temp_message_timer = 0
        self.temp_message_big = False

        # 上次绘制的秒数（用于时钟刷新）
        self._last_clock_second = -1
        self._last_statusbar_minute = -1

        if RTC is not None:
            try:
                rtc_year = RTC.datetime()[0]
                if rtc_year > 2000:
                    self.status["time_synced"] = True
                    print(f"[RTC] Startup check: existing RTC year={rtc_year} > 2000, "
                          f"treating as already synced (will be refined by next setTime)")
                else:
                    print(f"[RTC] Startup check: RTC year={rtc_year}, waiting for setTime")
            except Exception as e:
                print("[RTC] Startup check failed to read RTC:", e)

    # ---- state mutators (called from BLE callbacks) --------------------

    def add_notification(self, app_name, title, body, is_bitmap=False, title_field=None, body_field=None, notif_id=None):
        entry = {
            "id": notif_id,
            "app_name": app_name,
            "title": title,
            "body": body,
            "is_bitmap": is_bitmap,
            "title_field": title_field,
            "body_field": body_field,
            "timestamp": self._get_local_epoch_seconds(),
        }
        self.notifications.insert(0, entry)
        if len(self.notifications) > self.max_notifications:
            self.notifications.pop()

        # 位图字节预算：条数够了不代表内存够了，这里再按总字节数淘汰
        # 一遍，防止一连串都是位图的通知把内存吃光。
        def _entry_bitmap_bytes(e):
            return _bitmap_bytes(e.get("title_field")) + _bitmap_bytes(e.get("body_field"))

        total = 0
        for e in self.notifications:
            total += _entry_bitmap_bytes(e)
        while total > _MAX_NOTIF_BITMAP_TOTAL and len(self.notifications) > 1:
            removed = self.notifications.pop()
            total -= _entry_bitmap_bytes(removed)
        self._fixup_scroll_after_removal()

        self.dirty = True

    def _fixup_scroll_after_removal(self):
        if self.scroll_offset > 0 and self.scroll_offset >= len(self.notifications):
            self.scroll_offset = max(0, len(self.notifications) - 1)
        self.dirty = True

    def dismiss_notification(self, index=0):
        if 0 <= index < len(self.notifications):
            self.notifications.pop(index)
            self._fixup_scroll_after_removal()
            return True
        return False

    def dismiss_notification_by_id(self, notif_id):
        for i, entry in enumerate(self.notifications):
            if entry.get("id") == notif_id:
                self.notifications.pop(i)
                self._fixup_scroll_after_removal()
                return True
        return False

    def update_music(self, data):
        self.music_info["track"] = data.get("track", "")
        self.music_info["artist"] = data.get("artist", "")
        self.music_info["album"] = data.get("album", "")
        self.music_info["duration"] = data.get("dur", 0)
        self.music_info["track_num"] = data.get("n", 0)
        self.music_info["track_count"] = data.get("c", 0)
        self.dirty = True

    def update_music_state(self, data):
        self.music_info["playing"] = (data.get("state") == "play")
        self.music_info["position"] = data.get("position", 0)
        self.music_info["shuffle"] = data.get("shuffle", 0)
        self.music_info["repeat"] = data.get("repeat", 0)
        self.dirty = True

    def set_status(self, key, value):
        self.status[key] = value
        self.dirty = True

    def show_temp_message(self, msg, duration=3, big=False):
        self.temp_message = msg
        self.temp_message_timer = time.time() + duration
        self.temp_message_big = big
        self.dirty = True

    def switch_view(self, view):
        # 核心界面（时钟/通知/音乐/状态）还是写死的四个；插件界面
        # （比如天气）动态问 plugin_manager 认不认识这个 id。
        if view in _CORE_VIEWS or plugin_manager.find_view(view) is not None:
            self.current_view = view
            self.scroll_offset = 0
            self.dirty = True
            # 强制重置上次视图，确保立即刷新
            self._last_view = None

    def scroll(self, direction):
        if direction > 0:
            self.scroll_offset = min(self.scroll_offset + 1, self.max_scroll)
        else:
            self.scroll_offset = max(self.scroll_offset - 1, 0)
        self.dirty = True

    # ---- drawing ---------------------------------------------------

    def draw(self):
        # 检测视图是否发生变化，强制刷新
        view_changed = (self._last_view != self.current_view)
        if view_changed:
            self.dirty = True
            self._last_view = self.current_view

        # 先检查临时消息是否过期。这一步必须放在提前 return 之前
        # （原来的 bug：这段判断写在 return 后面，一旦某次绘制把
        # dirty 变回 False，后面就再也不会执行到这里，temp_message
        # 永远清不掉 -> 时钟/通知/音乐界面全部卡在临时提示上不刷新）。
        if self.temp_message and time.time() > self.temp_message_timer:
            self.temp_message = None
            self._draw_temp_message_big_remove()
            self.dirty = True

        # The clock view needs to redraw once a second even with no other
        # state change, so it can't rely solely on self.dirty.
        needs_clock_tick = False
        if self.current_view == VIEW_CLOCK and not self.temp_message and self.status["time_synced"]:
            if RTC is not None:
                _, _, _, _, _, second, _, _ = RTC.datetime()
                if second != self._last_clock_second:
                    self._last_clock_second = second
                    needs_clock_tick = True

        # 非时钟界面：状态栏右上角的小时钟只需要按分钟刷新，比时钟视图
        # 的按秒刷新开销小很多。
        needs_statusbar_tick = False
        if self.current_view != VIEW_CLOCK and not self.temp_message and self.status["time_synced"]:
            local_dt = self._get_local_datetime_tuple()
            if local_dt is not None:
                minute = local_dt[4]
                if minute != self._last_statusbar_minute:
                    self._last_statusbar_minute = minute
                    needs_statusbar_tick = True

        if not self.dirty and not needs_clock_tick and not needs_statusbar_tick:
            return

        DISPLAY.fill(PALETTE[2])
        # NOTIF_STRIP 现在直接借用 DISPLAY 的行池子，DISPLAY.fill() 已经把
        # 所有行/矩形收回隐藏了，不用再单独 hide 一次。

        if self.current_view == VIEW_CLOCK and not self.temp_message:
            # Full-screen clock intentionally skips the status bar / bottom
            # indicator so the time gets maximum space.
            TOP_BAR.hide()
            BOTTOM_BAR.hide()
            self._draw_clock()
        else:
            CLOCK_DIGITS.hide()
            TOP_BAR.show()
            BOTTOM_BAR.show()
            self._draw_status_bar()
            if self.temp_message:
                self._draw_temp_message()
            elif self.current_view == VIEW_NOTIFICATIONS:
                self._draw_notifications()
            elif self.current_view == VIEW_MUSIC:
                self._draw_music()
            elif self.current_view == VIEW_STATUS:
                self._draw_status()
            elif not plugin_manager.draw_view(self.current_view):
                # 既不是核心界面，也没有插件认领这个 view id（比如插件
                # 加载失败了），兜底显示状态页，总比空白屏幕强。
                self._draw_status()
            self._draw_bottom_indicator()

        DISPLAY.show()
        self.dirty = False

    def _draw_status_bar(self):
        view_names = {
            VIEW_CLOCK: "Clock",
            VIEW_NOTIFICATIONS: "Notifications",
            VIEW_MUSIC: "Music",
            VIEW_STATUS: "Status",
        }
        plugin = plugin_manager.find_view(self.current_view)
        if plugin is not None:
            title = getattr(plugin, "TITLE", self.current_view)
        else:
            title = view_names.get(self.current_view, "Status")

        # 非时钟界面右上角显示小号当前时间，比包计数更实用；没同步过
        # 时间的话退回显示包计数，方便调试连接状态。
        local_dt = self._get_local_datetime_tuple()
        if local_dt is not None:
            _, _, _, hour, minute, _second, _weekday = local_dt
            right_text = f"{hour:02d}:{minute:02d}"
        elif self.status["packet_count"] > 0:
            right_text = f"#{self.status['packet_count']}"
        else:
            right_text = ""

        TOP_BAR.update(
            connected=self.status["connected"],
            title=title,
            right_text=right_text,
            bg_color=PALETTE[1],
            text_color=PALETTE[8],
            dim_color=PALETTE[6],
        )

    def _draw_bottom_indicator(self):
        BOTTOM_BAR.update(self.current_view, PALETTE[8], PALETTE[5], PALETTE[4], PALETTE[1])

    def _draw_temp_message(self):
        if self.temp_message_big:
            self._draw_temp_message_big()
            return
        lines = wrap_text(self.temp_message, _MAX_CHARS_PER_LINE)
        y = _CONTENT_Y_START + 10
        for line in lines[:_CONTENT_MAX_LINES]:
            x = (_MH_DISPLAY_WIDTH - len(line) * _CHAR_WIDTH) // 2
            DISPLAY.text(line, x, y, PALETTE[8])
            y += _LINE_HEIGHT
            
    def _pick_font(self, *names):
        for name in names:
            font = getattr(lv, name, None)
            if font is not None:
                return font
        return None
    
    def _draw_temp_message_big(self):
        if not self.temp_message:
            return
        
        # 分割并换行（适配大字体）
        max_chars = max(2, (_MH_DISPLAY_WIDTH - 24) // 16)
        lines = []
        for line in self.temp_message.split('\n'):
            if line.strip():
                lines.extend(wrap_text(line, max_chars))
            else:
                lines.append('')
        lines = lines[:3]
        
        if not lines:
            return
        
        # 选择大字体
        big_font = self._pick_font('font_montserrat_16', 'font_montserrat_12')
        if big_font is None:
            # 降级到小字体
            return self._draw_temp_message()
        
        # 硬编码字体高度（montserrat_16 高度约 16px，montserrat_12 约 12px）
        if big_font == getattr(lv, 'font_montserrat_16', None):
            font_height = 16
        elif big_font == getattr(lv, 'font_montserrat_12', None):
            font_height = 12
        else:
            font_height = 16  # 默认
        
        line_h = font_height + 4
        
        # 计算框
        box_w = _MH_DISPLAY_WIDTH - 16
        box_h = min(_CONTENT_HEIGHT - 8, len(lines) * line_h + 20)
        box_x = 8
        box_y = _CONTENT_Y_START + max(2, (_CONTENT_HEIGHT - box_h) // 2)
        
        
        if not hasattr(self, '_big_label') or self._big_label is None:
            self._big_label = lv.label(lv.screen_active())
            self._big_label.set_style_text_color(lv.color_hex(0xFFFFFF), 0)  # LVGL 9 推荐用 color_hex
            self._big_label.set_style_bg_opa(lv.OPA.TRANSP, 0)
            self._big_label.set_style_border_width(0, 0)
        
        full_text = '\n'.join(lines)
        self._big_label.set_text(full_text)
        self._big_label.set_style_text_font(big_font, 0)
        self._big_label.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
        self._big_label.set_width(box_w - 10)
        self._big_label.set_pos(box_x + 5, box_y + 5)
    
    def _draw_temp_message_big_remove(self):
        if not hasattr(self, '_big_label') or self._big_label is None:
            return
        self._big_label.delete()
        self._big_label = None
    # ---- notification cards: title / divider / body, boxed -------------

    def _draw_field(self, field, x, y, max_chars, color=None):
        if color is None:
            color = PALETTE[8]

        if isinstance(field, dict) and field.get("pixels"):
            # 前景色要用调用方传进来的 color，不能写死 -- 之前这里硬编码
            # 成 palette[8]，导致标题/正文的颜色区分对"位图形式"的通知
            # 文字（手机推送 emoji/特殊字符时经常整段渲染成图片而不是
            # 纯文本）完全不起作用，不管调用处传什么颜色都看不出变化。
            palette = [PALETTE[2], color]
            gb_bmp = GBBitmap(
                field["width"],
                field["height"],
                field["bpp"],
                field["pixels"],
                palette
            )
            key_idx = field.get("transparent")
            # 透明色处理：如果 transparent 为 0，使用背景色
            key_color = palette[key_idx] if key_idx is not None and key_idx < len(palette) else -1
            DISPLAY.bitmap(gb_bmp, x, y, key=key_color)
            return y + field["height"] + 4  # 位图下方增加间距

        if isinstance(field, str) and field:
            for line in wrap_text(field, max_chars):
                DISPLAY.text(line, x, y, color)
                y += _LINE_HEIGHT
            return y
        return y

    def _field_height(self, field, max_chars):
        if isinstance(field, dict) and field.get("pixels"):
            return field["height"] + 4  # 与 _draw_field 中的间距一致
        if isinstance(field, str) and field:
            return len(wrap_text(field, max_chars)) * _LINE_HEIGHT
        return 0

    def _format_notification_timestamp(self, ts):
        if ts is None or not self.status["time_synced"] or RTC is None:
            return ""
        now_seconds = self._get_local_epoch_seconds()
        if now_seconds is None:
            return ""
        try:
            now_tuple = time.localtime(now_seconds)
            ts_tuple = time.localtime(int(ts))
        except Exception:
            return ""
        same_day = (
            now_tuple[0] == ts_tuple[0]
            and now_tuple[1] == ts_tuple[1]
            and now_tuple[2] == ts_tuple[2]
        )
        if same_day:
            return f"{ts_tuple[3]:02d}:{ts_tuple[4]:02d}"
        return f"{ts_tuple[1]:02d}-{ts_tuple[2]:02d} {ts_tuple[3]:02d}:{ts_tuple[4]:02d}"

    def _draw_notification_card(self, title_field, body_field, y_start, y_limit, timestamp_str=""):
        box_x = _CARD_MARGIN_X
        box_width = _MH_DISPLAY_WIDTH - 2 * _CARD_MARGIN_X
        inner_x = box_x + _CARD_PADDING
        inner_chars = max(1, (box_width - 2 * _CARD_PADDING) // _CHAR_WIDTH)

        # 给右上角的时间戳标记预留宽度，只影响标题这一行的换行位置
        # （位图标题有自己的宽高，不走 max_chars 换行逻辑，不受影响）。
        ts_reserved_chars = (len(timestamp_str) + 1) if timestamp_str else 0
        title_chars = (
            max(1, inner_chars - ts_reserved_chars)
            if isinstance(title_field, str) else inner_chars
        )

        title_h = self._field_height(title_field, title_chars)
        body_h = self._field_height(body_field, inner_chars)
        title_strip_height = _CARD_PADDING + title_h + 2  # 到分隔线为止
        box_height = title_strip_height + 4 + body_h + _CARD_PADDING

        # 计算实际可用的高度
        available_height = y_limit - y_start
        if box_height > available_height:
            box_height = max(0, available_height)
        if title_strip_height > box_height:
            title_strip_height = box_height

        # 绘制卡片边框
        DISPLAY.rect(box_x, y_start, box_width, box_height, PALETTE[5])
        if box_height > 2:
            inner_h = box_height - 2
            # 标题区用状态栏同款的底色，正文区维持原来的卡片底色，两块
            # 背景分开填充，配合下面的分隔线，让标题/正文一眼就能分清楚。
            title_bg_h = max(0, min(title_strip_height, inner_h))
            if title_bg_h > 0:
                DISPLAY.rect(box_x + 1, y_start + 1, box_width - 2, title_bg_h, PALETTE[1], fill=True)
            body_bg_h = inner_h - title_bg_h
            if body_bg_h > 0:
                DISPLAY.rect(box_x + 1, y_start + 1 + title_bg_h, box_width - 2, body_bg_h, PALETTE[3], fill=True)
                # 保底方案：细笔画的文字对轻微色差不敏感，在某些屏幕
                # （比如色深有限的屏）上很难看出前景色差异。这里额外
                # 画一条实心色条标记正文区域，不依赖文字颜色，用大色块
                # 保证不管什么屏幕都能一眼看出标题/正文的分界。
                DISPLAY.rect(box_x + 1, y_start + 1 + title_bg_h, 3, body_bg_h, PALETTE[4], fill=True)

        y = y_start + _CARD_PADDING
        # 绘制标题 -- 用较亮的颜色，和正文区分开（不只靠下面那条分隔线）
        y = self._draw_field(title_field, inner_x, y, title_chars, color=PALETTE[8])

        # 右上角时间戳标记
        if timestamp_str:
            ts_x = box_x + box_width - _CARD_PADDING - len(timestamp_str) * _CHAR_WIDTH
            DISPLAY.text(timestamp_str, ts_x, y_start + _CARD_PADDING, PALETTE[6])

        # 分隔线
        y += 2
        if y < y_limit:
            divider_y = y
            line_start = inner_x
            line_end = inner_x + (box_width - 2 * _CARD_PADDING)
            DISPLAY.line(line_start, divider_y, line_end, divider_y, PALETTE[5])
        y += 4

        # 绘制正文 -- 用稍暗一档的颜色，和标题拉开视觉层次
        y = self._draw_field(body_field, inner_x, y, inner_chars, color=PALETTE[6])

        # 确保返回的 y 值不会超过 y_limit，并且至少比 y_start 大
        actual_y = min(y, y_limit)
        if actual_y <= y_start:
            actual_y = y_start + 1

        return actual_y + _CARD_GAP

    def _draw_notifications(self):
        if not self.notifications:
            text = "No notifications"
            x = (_MH_DISPLAY_WIDTH - len(text) * _CHAR_WIDTH) // 2
            DISPLAY.text(text, x, 20, PALETTE[5])
            return

        def extract_title(entry):
            if entry["is_bitmap"] and isinstance(entry["title_field"], dict):
                return entry["title_field"]
            title = entry["title_field"] if entry["is_bitmap"] else entry["title"]
            return f"{entry['app_name']}: {title}" if title else entry["app_name"]

        def extract_body(entry):
            return entry["body_field"] if entry["is_bitmap"] else entry["body"]

        shown, _next_y = NOTIF_STRIP.draw(
            self.notifications, self.scroll_offset, extract_title, extract_body,
            y_start=2, max_y=_CONTENT_HEIGHT,
            bg_color=PALETTE[2], title_color=PALETTE[8], body_color=PALETTE[6],
            divider_color=PALETTE[5],
        )

        # 每条完整通知占用池子里 2 行（标题+正文），能完整显示几条取决于
        # 这次实际借到了几行（池子大小可能因为内存紧张而缩水），不是
        # 写死的数字。
        full_shown = max(1, min(shown, len(DISPLAY._rows) // 2))
        self.max_scroll = max(0, len(self.notifications) - full_shown)

        if self.max_scroll > 0:
            scroll_text = f"{self.scroll_offset + 1}/{len(self.notifications)}"
            x = _MH_DISPLAY_WIDTH - len(scroll_text) * _CHAR_WIDTH - 4
            DISPLAY.text(scroll_text, x, 2, PALETTE[5])

    def _draw_music(self):
        info = self.music_info
        y = _CONTENT_Y_START + 2

        # 状态栏：播放/暂停
        status_icon = "▶" if info["playing"] else "⏸"
        status_text = f"{status_icon} Playing" if info["playing"] else f"{status_icon} Paused"
        DISPLAY.text(status_text, 4, y, PALETTE[8])
        y += _LINE_HEIGHT + 2

        DISPLAY.line(4, y, _MH_DISPLAY_WIDTH - 4, y, PALETTE[5])
        y += 4

        # 绘制音乐卡片 (artist/track)
        if info["track"] or info["artist"]:
            y_limit = _CONTENT_Y_START + _CONTENT_HEIGHT
            y = self._draw_notification_card(info["artist"] or "", info["track"] or "", y, y_limit)
            
            # 确保 y 不会超过内容区域
            if y >= y_limit:
                return  # 没有空间绘制进度条了

        # 检查是否有足够空间绘制进度条
        if y + 30 > _CONTENT_Y_START + _CONTENT_HEIGHT:
            return  # 空间不够，不绘制进度条

        if info["duration"] > 0 and info["position"] > 0:
            progress = info["position"] / info["duration"]
            bar_width = _MH_DISPLAY_WIDTH - 16
            bar_filled = int(bar_width * progress)

            pos_str = f"{info['position']//60:02d}:{info['position']%60:02d}"
            dur_str = f"{info['duration']//60:02d}:{info['duration']%60:02d}"
            DISPLAY.text(f"{pos_str}/{dur_str}", 4, y, PALETTE[6])
            y += _LINE_HEIGHT - 2

            DISPLAY.rect(4, y, bar_width, 4, PALETTE[5], fill=True)
            if bar_filled > 0:
                DISPLAY.rect(4, y, bar_filled, 4, PALETTE[4], fill=True)
            y += 8

            mode_str = ""
            if info["repeat"] == 1:
                mode_str += "🔁 "
            elif info["repeat"] == 2:
                mode_str += "🔂 "
            if info["shuffle"] == 1:
                mode_str += "🔀 "
            elif info["shuffle"] == 2:
                mode_str += "🔀1 "
            if mode_str:
                DISPLAY.text(mode_str, 4, y, PALETTE[6])
                
    def _draw_status(self):
        y = _CONTENT_Y_START + 2

        # ---- Settings 区（上半部分）----
        DISPLAY.text("⚙ Settings", 4, y, PALETTE[6])
        y += _LINE_HEIGHT + 2

        theme_label = "Inverted" if _THEME_INVERTED[0] else "Dark"
        row_h = _LINE_HEIGHT + 6
        DISPLAY.rect(4, y, _MH_DISPLAY_WIDTH - 8, row_h, PALETTE[3], fill=True)
        DISPLAY.text(f"Theme: {theme_label}", 8, y + 3, PALETTE[8])
        # 右边画一个简单的开关样式，纯装饰，提示这一行是可以点的
        sw_w, sw_h = 28, row_h - 8
        sw_x = _MH_DISPLAY_WIDTH - 8 - sw_w - 4
        sw_y = y + 4
        DISPLAY.rect(sw_x, sw_y, sw_w, sw_h, PALETTE[5], fill=False)
        knob_w = sw_w // 2
        knob_x = sw_x + (sw_w - knob_w) if _THEME_INVERTED[0] else sw_x
        DISPLAY.rect(knob_x, sw_y, knob_w, sw_h, PALETTE[4], fill=True)
        y += row_h + 4
        DISPLAY.text("(tap ENTER on this page to toggle)", 4, y, PALETTE[5])
        y += _LINE_HEIGHT + 4

        DISPLAY.line(4, y, _MH_DISPLAY_WIDTH - 4, y, PALETTE[5])
        y += 6

        # ---- Log 区（下半部分，原来那些状态行搬到这里）----
        DISPLAY.text("📜 Log", 4, y, PALETTE[6])
        y += _LINE_HEIGHT + 2

        status_lines = [
            f"Connection: {'✅ Connected' if self.status['connected'] else '❌ Disconnected'}",
            f"Packets: {self.status['packet_count']}",
            f"Last event: {self.status['last_event']}",
        ]

        if self.status["gps_active"]:
            status_lines.append("📍 GPS: Active")
        if self.status["activity_active"]:
            status_lines.append("🏃 Activity: Active")

        max_y = _CONTENT_Y_START + _CONTENT_HEIGHT
        for line in status_lines:
            if y + _LINE_HEIGHT > max_y:
                break
            DISPLAY.text(line, 4, y, PALETTE[8])
            y += _LINE_HEIGHT

    # ---- clock -----------------------------------------------------

    def _get_local_epoch_seconds(self):
        if RTC is None or not self.status["time_synced"]:
            return None
        y, m, d, wk, h, minute, s, _sub = RTC.datetime()
        utc_seconds = time.mktime((y, m, d, h, minute, s, wk, 0))
        return int(utc_seconds + (ble_bridge.tz_offset * 3600))

    def _get_local_datetime_tuple(self):
        local_seconds = self._get_local_epoch_seconds()
        if local_seconds is None:
            return None
        year, month, day, hour, minute, second, weekday, _ = time.localtime(local_seconds)
        return year, month, day, hour, minute, second, weekday

    def _draw_clock(self):
        local_dt = self._get_local_datetime_tuple()
        if local_dt is None:
            CLOCK_DIGITS.hide()
            msg = "Waiting for time sync..."
            x = (_MH_DISPLAY_WIDTH - len(msg) * _CHAR_WIDTH) // 2
            # DISPLAY 这块 canvas 现在只覆盖内容区（顶/底栏是独立控件），
            # 时钟界面把顶/底栏都隐藏了，但 canvas 本身位置/大小没变，
            # 所以这里要用 canvas 的本地坐标，减掉 _STATUS_BAR_HEIGHT
            # 的偏移，否则会画到 canvas 范围外面被裁掉。
            DISPLAY.text(msg, x, _MH_DISPLAY_HEIGHT // 2 - 4 - _STATUS_BAR_HEIGHT, PALETTE[6])
            return

        year, month, day, hour, minute, second, weekday = local_dt

        # 大号数字时钟：数字本体现在是 LVGL 控件拼出来的（见 ClockDigits），
        # 这里只需要算相对屏幕的尺寸，不用再自己画矩形了。
        digit_h = _MH_DISPLAY_HEIGHT * 45 // 100
        digit_w = digit_h * 5 // 10
        gap = max(4, digit_w * 25 // 100)
        colon_w = max(6, digit_w * 40 // 100)
        thick = max(2, digit_w // 14)
        total_w = digit_w * 4 + colon_w + gap * 3
        start_x = (_MH_DISPLAY_WIDTH - total_w) // 2
        start_y = max(4, _MH_DISPLAY_HEIGHT * 5 // 100) + 20  # 整体往下挪20px
        color = PALETTE[8]

        CLOCK_DIGITS.show_time(hour, minute, start_x, start_y, digit_w, digit_h, thick, gap, colon_w, color)

        # 底部日期和星期
        weekday_name = _WEEKDAY_NAMES[weekday] if 0 <= weekday < 7 else "?"
        date_str = f"{weekday_name}  {year:04d}-{month:02d}-{day:02d}"
        dy = start_y + digit_h + max(6, _MH_DISPLAY_HEIGHT * 6 // 100)
        dx = (_MH_DISPLAY_WIDTH - len(date_str) * _CHAR_WIDTH) // 2
        # 同上，转换成 DISPLAY canvas 的本地坐标
        DISPLAY.text(date_str, dx, dy - _STATUS_BAR_HEIGHT, PALETTE[6])

        # 如果秒数变化，标记需要刷新
        self._last_clock_second = second

