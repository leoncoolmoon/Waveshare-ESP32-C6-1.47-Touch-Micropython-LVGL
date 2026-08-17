# screen_manager.py -- 屏幕状态 + 绘制。核心四个视图（时钟/通知/音乐/
# 状态）自己画；其它视图 id（天气之类）一律转给 plugin_manager 处理，
# 这样加新插件视图不用再回来改这个文件。
from display_core import *
from bangle_utils import wrap_text, GBBitmap
import plugin_manager

# 底栏/状态栏用的核心视图三元组：(view_id, 底栏短标签, 状态栏标题)。
# 插件视图通过 register_plugin_views() 追加在后面。
_CORE_VIEWS = [
    (VIEW_CLOCK, "⏲C", "⏲ Clock"),
    (VIEW_NOTIFICATIONS, "✉N", "✉ Notifications"),
    (VIEW_MUSIC, "♫M", "♫ Music"),
    (VIEW_STATUS, "⚙S", "⚙ Status"),
]


class ScreenManager:
    # 管理屏幕显示，支持多种视图切换（核心视图 + 插件视图）。

    def __init__(self):
        self.current_view = VIEW_CLOCK
        self.dirty = True
        self._last_view = None  # 记录上次视图，用于检测切换
        self.scroll_offset = 0
        self.max_scroll = 0

        # 视图列表 -- 默认只有核心四个，main.py 建好插件之后会调用
        # register_plugin_views() 把插件视图追加进来。
        self.view_items = list(_CORE_VIEWS)

        # screen.ble 由 main_loop() 挂上去（同一个 _BLE_BRIDGE 实例），
        # 插件靠它才能主动往手机发消息，比如 gps.py 回复 is_gps_active。
        self.ble = None

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

        # 状态信息 -- 插件（gps/activity 等）通过 set_status() 往这里写
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

        # 显示反色开关，通过 VIEW_STATUS 界面按 ENT 切换（见 toggle_invert()）
        self.inverted = False

        # 上次绘制的分钟数（用于时钟刷新，见 draw() 里的 needs_clock_tick）
        self._last_clock_minute = -1
        self._last_statusbar_minute = -1
        # 上次做"要不要因为分钟数变了而重绘"这个被动检查的时间戳
        # （ticks_ms），把这部分 RTC 轮询节流到最多 1 秒一次，见 draw()。
        self._last_tick_check_ms = 0

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

    # ---- view registry ---------------------------------------------

    def register_plugin_views(self, plugin_items):
        # plugin_items: [(view_id, icon, title), ...]，来自
        # plugin_manager.views() 里每个带 VIEW_ID 的插件。
        self.view_items = list(_CORE_VIEWS) + list(plugin_items)

    def all_view_ids(self):
        return [vid for vid, _icon, _title in self.view_items]

    # ---- state mutators (called from BLE callbacks / plugins) ----------

    def add_notification(self, app_name, title, body, is_bitmap=False, title_field=None, body_field=None, notif_id=None):
        # 添加通知到历史。
        #
        # 时间戳用 _get_local_epoch_seconds()（跟大时钟同一套 RTC 换算），
        # 不用 time.time() -- 这个固件下 time.time() 不一定跟着 RTC 走，
        # 两套基准混用会导致显示出来的时间戳不对。还没做过时间同步时存
        # None，_format_notification_timestamp() 会识别并跳过显示。
        #
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
        self.dirty = True

    def _fixup_scroll_after_removal(self):
        # 通知列表变短之后，收紧滚动位置，避免滚到空白区域。
        if self.scroll_offset > 0 and self.scroll_offset >= len(self.notifications):
            self.scroll_offset = max(0, len(self.notifications) - 1)
        self.dirty = True

    def dismiss_notification(self, index=0):
        # 按索引移除一条通知（本地乐观更新，不等手机蓝牙回传确认）。
        if 0 <= index < len(self.notifications):
            self.notifications.pop(index)
            self._fixup_scroll_after_removal()
            return True
        return False

    def dismiss_notification_by_id(self, notif_id):
        # 按 id 移除一条通知 -- 用于手机那边主动 dismiss（比如用户在手机
        # 上划掉了通知）时，把手表上对应的那条也同步移出去。
        for i, entry in enumerate(self.notifications):
            if entry.get("id") == notif_id:
                self.notifications.pop(i)
                self._fixup_scroll_after_removal()
                return True
        return False

    def update_music(self, data):
        # 更新音乐信息。
        self.music_info["track"] = data.get("track", "")
        self.music_info["artist"] = data.get("artist", "")
        self.music_info["album"] = data.get("album", "")
        self.music_info["duration"] = data.get("dur", 0)
        self.music_info["track_num"] = data.get("n", 0)
        self.music_info["track_count"] = data.get("c", 0)
        self.dirty = True

    def update_music_state(self, data):
        # 更新音乐播放状态。
        self.music_info["playing"] = (data.get("state") == "play")
        self.music_info["position"] = data.get("position", 0)
        self.music_info["shuffle"] = data.get("shuffle", 0)
        self.music_info["repeat"] = data.get("repeat", 0)
        self.dirty = True

    def set_status(self, key, value):
        # 更新状态信息。
        self.status[key] = value
        self.dirty = True

    def show_temp_message(self, msg, duration=3):
        # 显示临时消息。
        self.temp_message = msg
        self.temp_message_timer = time.time() + duration
        self.dirty = True

    def toggle_invert(self):
        # 反色显示：VIEW_STATUS 界面按 ENT 触发。
        #
        # 用的是硬件反色接口 `DISPLAY.display.set_color_inversion(bool)`
        # （ST7789 这类驱动芯片的 INVON/INVOFF 命令），这是在另一个版本
        # 里已经验证过能跑通的调用方式。调一次就能把控制器已经收到的
        # 整块画面颜色全部反过来，之后正常画（还是用原来的 PALETTE/
        # _COLOR_BLACK，不用改任何取色逻辑）就会自动呈现反色效果，不需要
        # 重新 draw()。
        #
        # 这跟 LVGL 版本里 toggle_theme() 那种"重建 PALETTE 数组、把每个
        # 颜色位取反、再重新应用到控件样式"的软件反色思路不是一回事 --
        # 软件反色是应用层面重新计算每个颜色值，硬件反色是控制器层面的
        # 操作，跟用不用 LVGL 无关。这里选硬件反色是因为它更简单可靠
        # （一次调用管住全屏），也不用像软件反色那样把 invert 状态传进
        # 每个插件的 draw() 里。
        #
        self.inverted = not self.inverted
        try:
            DISPLAY.display.set_color_inversion(self.inverted)
        except Exception as e:
            # 调用失败就把标志位改回去，避免"UI 状态说已经反色了，但
            # 屏幕其实没反色"这种不一致；打印出来方便确认这个固件下
            # DISPLAY.display.set_color_inversion 这条链路是否存在。
            self.inverted = not self.inverted
            print("[APP] DISPLAY.display.set_color_inversion() 调用失败:", e)
            self.show_temp_message("Invert not supported", 1)
            return
        self.show_temp_message("🔄 Inverted: ON" if self.inverted else "🔄 Inverted: OFF", 1)

    def switch_view(self, view):
        # 切换视图 -- view 可以是核心视图 id，也可以是任何已注册的
        # 插件 VIEW_ID。切走一个插件视图时会调 plugin_manager.hide_view()
        # 给插件一个清理机会（大多数插件不需要，只有创建了原生控件之类
        # 额外资源的插件才用得上）。
        if view not in self.all_view_ids():
            return
        if view != self.current_view:
            plugin_manager.hide_view(self.current_view)
        self.current_view = view
        self.scroll_offset = 0
        self.dirty = True
        # 强制重置上次视图，确保立即刷新
        self._last_view = None

    def scroll(self, direction):
        # 滚动内容（上下）。
        if direction > 0:
            self.scroll_offset = min(self.scroll_offset + 1, self.max_scroll)
        else:
            self.scroll_offset = max(self.scroll_offset - 1, 0)
        self.dirty = True

    # ---- drawing ---------------------------------------------------

    def draw(self):
        # 绘制屏幕。
        # 检测视图是否发生变化，强制刷新
        view_changed = (self._last_view != self.current_view)
        if view_changed:
            self.dirty = True
            self._last_view = self.current_view

        # 先检查临时消息是否过期。这一步必须放在提前 return 之前
        # （原来的 bug：这段判断写在 return 后面，一旦某次绘制把
        # dirty 变回 False，后面就再也不会执行到这里，temp_message
        # 永远清不掉 -> 界面全部卡在临时提示上不刷新）。
        if self.temp_message and time.time() > self.temp_message_timer:
            self.temp_message = None
            self.dirty = True

        # 时钟/状态栏这两个"被动检查"要不要重绘，都得读一次 RTC.datetime()
        # -- 这是真的硬件寄存器读取，不是纯内存操作，有实打实的开销。
        # 但这两个检查本质上只关心"分钟数变没变"，主循环轮询按键的频率
        # (main.py 里的 _LOOP_INTERVAL_MS) 完全没必要跟这个绑在一起 --
        # 每秒查一次绰绰有余，不会错过任何一次分钟跳变。这里单独把这部分
        # 检查节流到最多 1 秒一次，跟主循环的按键轮询频率解耦；已经是
        # True 的 self.dirty（用户按键、收到新通知等）完全不受这个节流
        # 影响，下一帧照样立刻重绘，不会变卡。
        needs_clock_tick = False
        needs_statusbar_tick = False
        now_ms = time.ticks_ms()
        if time.ticks_diff(now_ms, self._last_tick_check_ms) >= 1000:
            self._last_tick_check_ms = now_ms

            # 大号时钟只显示 HH:MM，不显示秒，所以真正需要重绘的频率是
            # "每分钟一次"，不是"每秒一次"。
            if self.current_view == VIEW_CLOCK and not self.temp_message and self.status["time_synced"]:
                if RTC is not None:
                    _, _, _, _, _, minute, _second, _ = RTC.datetime()
                    if minute != self._last_clock_minute:
                        self._last_clock_minute = minute
                        needs_clock_tick = True

            # 非时钟界面：状态栏右上角的小时钟只需要按分钟刷新。
            if self.current_view != VIEW_CLOCK and not self.temp_message and self.status["time_synced"]:
                local_dt = self._get_local_datetime_tuple()
                if local_dt is not None:
                    minute = local_dt[4]
                    if minute != self._last_statusbar_minute:
                        self._last_statusbar_minute = minute
                        needs_statusbar_tick = True

        if not self.dirty and not needs_clock_tick and not needs_statusbar_tick:
            return

        # 增量重绘路径：这一帧唯一的变化就是"状态栏右上角的分钟数跳了
        # 一下"，屏幕上其它内容（比如通知列表、日历网格这些画起来比较
        # 重的视图）完全没变，没必要跟着 DISPLAY.fill() 整屏清一遍、再
        # 把当前视图整个重画一遍 -- 只清+画那一小块时间文字的区域就够，
        # 省下的是这一帧的 CPU 绘制开销（具体到 SPI/DMA 传输量能不能
        # 跟着变小，取决于 jd9853_display.py 那边 invalidate_area 是否
        # 生效；就算那边不生效，这里省下来的重复绘制计算也是纯赚）。
        if not self.dirty and not needs_clock_tick and needs_statusbar_tick:
            self._draw_statusbar_clock_incremental()
            DISPLAY.show()
            return


        DISPLAY.fill(_COLOR_BLACK)

        if self.current_view == VIEW_CLOCK and not self.temp_message:
            # Full-screen clock intentionally skips the status bar / bottom
            # indicator so the time gets maximum space.
            self._draw_clock()
        else:
            self._draw_status_bar()
            if self.temp_message:
                self._draw_temp_message()
            elif self.current_view == VIEW_NOTIFICATIONS:
                self._draw_notifications()
            elif self.current_view == VIEW_MUSIC:
                self._draw_music()
            elif self.current_view == VIEW_STATUS:
                self._draw_status()
            else:
                # 不是核心视图 -> 一定是某个插件的 VIEW_ID，交给它自己画。
                if not plugin_manager.draw_view(self.current_view):
                    text = f"Unknown view: {self.current_view}"
                    x = (_MH_DISPLAY_WIDTH - len(text) * _CHAR_WIDTH) // 2
                    DISPLAY.text(text, x, _CONTENT_Y_START + 20, PALETTE[5])
            self._draw_bottom_indicator()

        DISPLAY.show()
        self.dirty = False

    def _draw_status_bar(self):
        # 绘制顶部状态栏。
        y = 0
        bg_color = PALETTE[1]
        text_color = PALETTE[8]

        DISPLAY.rect(0, y, _MH_DISPLAY_WIDTH, _STATUS_BAR_HEIGHT, bg_color, fill=True)

        status_icon = "🔗" if self.status["connected"] else "⛔"
        DISPLAY.text(status_icon, 2, y + 2, text_color)

        view_names = {vid: title for vid, _icon, title in self.view_items}
        view_name = view_names.get(self.current_view, "Status")
        x = 0
        DISPLAY.text(view_name, x, y + 2, text_color)

        # 非时钟界面右上角显示小号当前时间，比包计数更实用；没同步过
        # 时间的话退回显示包计数，方便调试连接状态。
        local_dt = self._get_local_datetime_tuple()
        if local_dt is not None:
            _, _, _, hour, minute, _second, _weekday = local_dt
            time_str = f"{hour:02d}:{minute:02d}"
            x = _MH_DISPLAY_WIDTH - len(time_str) * _CHAR_WIDTH - 2
            DISPLAY.text(time_str, x, y + 2, PALETTE[6])
        elif self.status["packet_count"] > 0:
            pkt_str = f"#{self.status['packet_count']}"
            x = _MH_DISPLAY_WIDTH - len(pkt_str) * _CHAR_WIDTH - 2
            DISPLAY.text(pkt_str, x, y + 2, PALETTE[5])

    def _draw_statusbar_clock_incremental(self):
        # 只更新状态栏右上角的 HH:MM 文字，不动屏幕其它任何部分。
        #
        # 只在 draw() 判断出"这一帧唯一的变化就是分钟数跳了一下"时才会
        # 被调用，调用前提（needs_statusbar_tick 的判断条件）已经保证了
        # self.status["time_synced"] 为 True，所以这里不用像
        # _draw_status_bar() 里那样再处理"还没同步时间，退回显示包计数"
        # 的分支 -- 直接假设 time_str 一定存在就行。
        #
        local_dt = self._get_local_datetime_tuple()
        if local_dt is None:
            return
        _, _, _, hour, minute, _second, _weekday = local_dt
        time_str = f"{hour:02d}:{minute:02d}"
        # HH:MM 固定 5 个字符宽，按这个固定宽度清一块矩形再写新文字，
        # 足够盖掉旧文字，不用先量旧文字多宽、也不用管旧文字是不是
        # 刚好也是 5 个字符（保证跟 _draw_status_bar() 里的定位一致）。
        w = 5 * _CHAR_WIDTH
        x = _MH_DISPLAY_WIDTH - w - 2
        DISPLAY.rect(x, 2, w, _LINE_HEIGHT, PALETTE[1], fill=True)
        DISPLAY.text(time_str, x, 2, PALETTE[6])

    def _draw_bottom_indicator(self):
        # 绘制底部指示器 -- 均分给当前注册的所有视图（核心 + 插件），
        # 视图数量是动态的，不再像早期版本那样写死 5 个固定坐标。
        y = _MH_DISPLAY_HEIGHT - _BOTTOM_INDICATOR_HEIGHT
        bg_color = PALETTE[1]

        DISPLAY.rect(0, y, _MH_DISPLAY_WIDTH, _BOTTOM_INDICATOR_HEIGHT, bg_color, fill=True)

        items = self.view_items
        if not items:
            return
        slot_width = _MH_DISPLAY_WIDTH // len(items)

        for i, (view, label, _title) in enumerate(items):
            x = i * slot_width + 4
            is_active = (view == self.current_view)
            color = PALETTE[8] if is_active else PALETTE[5]
            DISPLAY.text(label, x, y + 2, color)
            if is_active:
                DISPLAY.rect(x, y + 10, 12, 2, PALETTE[4], fill=True)

    def _draw_temp_message(self):
        # 绘制临时消息。
        lines = wrap_text(self.temp_message, _MAX_CHARS_PER_LINE)
        y = _CONTENT_Y_START + 10
        for line in lines[:_CONTENT_MAX_LINES]:
            x = (_MH_DISPLAY_WIDTH - len(line) * _CHAR_WIDTH) // 2
            DISPLAY.text(line, x, y, PALETTE[8])
            y += _LINE_HEIGHT

    # ---- notification cards: title / divider / body, boxed -------------

    def _draw_field(self, field, x, y, max_chars, color=None):
        # Draw a single field (plain string OR decoded-bitmap dict) at
        # (x, y), wrapping text to max_chars. Returns the y position right
        # after whatever got drawn.
        #
        # `color` lets callers visually distinguish e.g. a title from a body
        # (defaults to the bright PALETTE[8] used everywhere else).
        if color is None:
            color = PALETTE[8]

        if isinstance(field, dict) and field.get("pixels"):
            # 位图的"背景色"必须跟屏幕实际背景一致（现在统一是纯黑），
            # 不然位图周围会露出一圈跟屏幕背景对不上的颜色。
            palette = [_COLOR_BLACK, color]
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
        # Predict how tall _draw_field() will render this field, without
        # actually drawing it -- needed to size the card's border rect before
        # we've drawn anything inside it.
        if isinstance(field, dict) and field.get("pixels"):
            return field["height"] + 4  # 与 _draw_field 中的间距一致
        if isinstance(field, str) and field:
            return len(wrap_text(field, max_chars)) * _LINE_HEIGHT
        return 0

    def _format_notification_timestamp(self, ts):
        # 把通知时间戳格式化成卡片右上角的小标记：同一天只显示
        # HH:MM，跨天显示 MM-DD HH:MM。
        #
        # ts 是 add_notification() 用 _get_local_epoch_seconds() 存的值，
        # "现在"也必须用同一个方法取，不能用 time.localtime()（系统时钟）
        # 直接比较 -- 这个固件下系统时钟不一定跟 RTC 同步，两套基准一混
        # 算出来的时间戳就是错的。
        #
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
        # 绘制一张 标题/分隔线/内容 的卡片，外面带边框。返回卡片结束
        # 之后（含 gap）的 y 坐标。
        inner_x = _CARD_MARGIN_X + _CARD_PADDING
        box_x = _CARD_MARGIN_X
        box_width = _MH_DISPLAY_WIDTH - 2 * _CARD_MARGIN_X
        inner_chars = (box_width - 2 * _CARD_PADDING) // _CHAR_WIDTH

        y = y_start + _CARD_PADDING

        # 标题
        y = self._draw_field(title_field, inner_x, y, inner_chars, color=PALETTE[8])

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
        # 绘制通知列表：每条都是 标题/分隔线/内容 的卡片，外面有边框。
        if not self.notifications:
            text = "No notifications"
            x = (_MH_DISPLAY_WIDTH - len(text) * _CHAR_WIDTH) // 2
            DISPLAY.text(text, x, _CONTENT_Y_START + 20, PALETTE[5])
            return

        y_limit = _CONTENT_Y_START + _CONTENT_HEIGHT
        y = _CONTENT_Y_START + 2
        start_idx = self.scroll_offset
        shown = 0

        for i in range(start_idx, len(self.notifications)):
            if y >= y_limit:
                break
            entry = self.notifications[i]

            # 构建标题：应用名 + 标题
            if entry["is_bitmap"]:
                title_field = entry["title_field"]
                body_field = entry["body_field"]
                # 如果标题是位图，应用名作为文本前缀单独显示
                if isinstance(title_field, dict):
                    # 位图标题，在卡片内绘制位图
                    title_for_card = title_field
                else:
                    title_for_card = f"{entry['app_name']}: {title_field}" if title_field else entry['app_name']
            else:
                title_for_card = f"{entry['app_name']}: {entry['title']}" if entry['title'] else entry['app_name']
                body_field = entry["body"]

            y = self._draw_notification_card(
                title_for_card, body_field, y, y_limit,
                timestamp_str=self._format_notification_timestamp(entry["timestamp"]),
            )
            shown += 1

        self.max_scroll = max(0, len(self.notifications) - max(1, shown))

        if self.max_scroll > 0:
            scroll_text = f"{self.scroll_offset + 1}/{len(self.notifications)}"
            x = _MH_DISPLAY_WIDTH - len(scroll_text) * _CHAR_WIDTH - 4
            DISPLAY.text(scroll_text, x, _CONTENT_Y_START + 2, PALETTE[5])

    def _draw_music(self):
        # 绘制音乐信息（支持位图字段）。
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
        # 绘制状态信息。
        y = _CONTENT_Y_START + 2

        status_lines = [
            f"Connection: {'✅ Connected' if self.status['connected'] else '❌ Disconnected'}",
            f"Packets: {self.status['packet_count']}",
            f"Last event: {self.status['last_event']}",
        ]

        if self.status["gps_active"]:
            status_lines.append("📍 GPS: Active")
        if self.status["activity_active"]:
            status_lines.append("🏃 Activity: Active")

        for line in status_lines:
            DISPLAY.text(line, 4, y, PALETTE[8])
            y += _LINE_HEIGHT

    # ---- clock -----------------------------------------------------

    def _get_local_epoch_seconds(self):
        # 跟 _get_local_datetime_tuple() 用同一套 RTC 换算，返回一个可以
        # 互相比较大小、并且能喂给 time.localtime() 还原出年月日时分秒的
        # 本地"时间戳"。RTC 不可用或还没同步过时间时返回 None。
        #
        # 通知记录时间必须用这个，而不能用 time.time() -- 这个固件下
        # time.time() 不一定跟着 RTC.datetime() 走（大时钟本身也是绕开
        # time.time()，直接读 RTC 换算的），两套基准混用会导致算出来的
        # 时间戳对不上，显示出来的日期/时间是错的。
        #
        # NOTE: RTC 里存的已经是本地时间的年月日时分秒 -- main.py 里
        # on_time_sync() 是用 epoch_to_rtc_tuple(epoch, tz_offset) 写进去
        # 的，那个函数内部把 UTC epoch 加过一次 tz_offset*3600 之后才拆
        # 成年月日时分秒写入 RTC。所以这里读出来再用 time.mktime() 编码
        # 回一个整数，得到的已经是"本地时间对应的时间戳"，不需要再加一次
        # tz_offset -- 之前这里额外又加了一次 tz_offset*3600，等于把时区
        # 偏移应用了两次，显示出来的时钟/通知时间会比实际时间多偏出一个
        # tz_offset（比如 UTC+10 会显示成偏差 20 小时），这里改掉。
        #
        if RTC is None or not self.status["time_synced"]:
            return None
        y, m, d, wk, h, minute, s, _sub = RTC.datetime()
        local_epoch = time.mktime((y, m, d, h, minute, s, wk, 0))
        return int(local_epoch)

    def _get_local_datetime_tuple(self):
        # 返回 (year, month, day, hour, minute, second, weekday) 本地时间，
        # RTC 不可用或还没同步过时间时返回 None。
        #
        # 供 _draw_clock() 和状态栏右上角的小时钟共用，保证两处显示的时间
        # 永远一致。
        #
        local_seconds = self._get_local_epoch_seconds()
        if local_seconds is None:
            return None
        year, month, day, hour, minute, second, weekday, _ = time.localtime(local_seconds)
        return year, month, day, hour, minute, second, weekday

    def _draw_clock(self):
        # 全屏数字时钟：大号 HH:MM 居中，下方一行日期+星期。
        #
        # NOTE: DISPLAY.text() doesn't expose a scale/size parameter for the
        # built-in font, so "large" digits are hand-drawn as blocky 7-segment
        # shapes using DISPLAY.rect() rather than relying on any particular
        # font being available.
        #
        local_dt = self._get_local_datetime_tuple()
        if local_dt is None:
            msg = "Waiting for time sync..."
            x = (_MH_DISPLAY_WIDTH - len(msg) * _CHAR_WIDTH) // 2
            DISPLAY.text(msg, x, _MH_DISPLAY_HEIGHT // 2 - 4, PALETTE[6])
            return

        year, month, day, hour, minute, second, weekday = local_dt

        # 大号数字时钟
        time_str = f"{hour:02d}:{minute:02d}"
        digit_w, digit_h, gap = 28, 56, 8
        colon_w = 12
        total_w = digit_w * 4 + colon_w + gap * 3
        start_x = (_MH_DISPLAY_WIDTH - total_w) // 2
        start_y = 8
        color = PALETTE[8]

        x = start_x
        for ch in time_str:
            if ch == ":":
                self._draw_colon(x, start_y, colon_w, digit_h, color)
                x += colon_w + gap
            else:
                self._draw_big_digit(int(ch), x, start_y, digit_w, digit_h, color)
                x += digit_w + gap

        # 底部日期和星期
        weekday_name = _WEEKDAY_NAMES[weekday] if 0 <= weekday < 7 else "?"
        date_str = f"{weekday_name}  {year:04d}-{month:02d}-{day:02d}"
        dy = start_y + digit_h + 10
        dx = (_MH_DISPLAY_WIDTH - len(date_str) * _CHAR_WIDTH) // 2
        DISPLAY.text(date_str, dx, dy, PALETTE[6])

    def _draw_colon(self, x, y, w, h, color):
        dot = max(4, w // 2)
        cx = x + (w - dot) // 2
        DISPLAY.rect(cx, y + h // 3 - dot // 2, dot, dot, color, fill=True)
        DISPLAY.rect(cx, y + 2 * h // 3 - dot // 2, dot, dot, color, fill=True)

    # 7-segment layout, each digit 0-9 -> which of segments (a,b,c,d,e,f,g) are lit
    #   a
    # f   b
    #   g
    # e   c
    #   d
    _SEGMENTS = {
        0: "abcdef", 1: "bc", 2: "abged", 3: "abgcd", 4: "fgbc",
        5: "afgcd", 6: "afgedc", 7: "abc", 8: "abcdefg", 9: "abcfgd",
    }

    def _draw_big_digit(self, digit, x, y, w, h, color):
        thick = max(3, w // 6)
        seg_w = w - 2 * thick
        half_h = (h - 3 * thick) // 2
        lit = self._SEGMENTS.get(digit, "")

        # horizontal segments: a (top), g (middle), d (bottom)
        if "a" in lit:
            DISPLAY.rect(x + thick, y, seg_w, thick, color, fill=True)
        if "g" in lit:
            DISPLAY.rect(x + thick, y + thick + half_h, seg_w, thick, color, fill=True)
        if "d" in lit:
            DISPLAY.rect(x + thick, y + 2 * thick + 2 * half_h, seg_w, thick, color, fill=True)
        # vertical segments: f/b (top half), e/c (bottom half)
        if "f" in lit:
            DISPLAY.rect(x, y + thick, thick, half_h, color, fill=True)
        if "b" in lit:
            DISPLAY.rect(x + thick + seg_w, y + thick, thick, half_h, color, fill=True)
        if "e" in lit:
            DISPLAY.rect(x, y + 2 * thick + half_h, thick, half_h, color, fill=True)
        if "c" in lit:
            DISPLAY.rect(x + thick + seg_w, y + 2 * thick + half_h, thick, half_h, color, fill=True)