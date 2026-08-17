# settings.py - 设置模块
# 负责: 配置数据的读取/保存(掉电保存到 flash)、设置界面UI(背光/反色/鼠标灵敏度/滚轮灵敏度)
# UI文字全部使用英文(单片机未加载中文字体)

import lvgl as lv
import time

try:
    import ujson as json
except ImportError:
    import json

SETTINGS_FILE = "combo_settings.json"

# ============ 默认值 & 取值范围 ============
DEFAULTS = {
    "backlight": 20,          # 背光亮度 5~100
    "invert": False,          # 屏幕是否反色
    "mouse_sensitivity": 1.5, # 鼠标灵敏度 0.5~3.0
    "scroll_sensitivity": 1.0,# 滚轮灵敏度 0.2~3.0
    "os_mode": "win",         # 主机系统: "win"(Windows/Linux，剪贴板用Ctrl) / "mac"(macOS，剪贴板用Cmd)
    "swap_xy": False,         # 是否交换鼠标/滚轮的X轴和Y轴(改变握持方向时用)
    "invert_vscroll": False,  # 是否反转竖直滚动方向
    "invert_hscroll": False,  # 是否反转水平滚动方向
}

BACKLIGHT_MIN, BACKLIGHT_MAX = 5, 100
MOUSE_SENS_MIN, MOUSE_SENS_MAX = 0.5, 3.0
SCROLL_SENS_MIN, SCROLL_SENS_MAX = 0.2, 3.0


def _pick_font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font is not None:
            return font
    return None


# ============ 配置数据 ============
class Settings:
    def __init__(self):
        self.backlight = DEFAULTS["backlight"]
        self.invert = DEFAULTS["invert"]
        self.mouse_sensitivity = DEFAULTS["mouse_sensitivity"]
        self.scroll_sensitivity = DEFAULTS["scroll_sensitivity"]
        self.os_mode = DEFAULTS["os_mode"]
        self.swap_xy = DEFAULTS["swap_xy"]
        self.invert_vscroll = DEFAULTS["invert_vscroll"]
        self.invert_hscroll = DEFAULTS["invert_hscroll"]

    def load(self):
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
            self.backlight = data.get("backlight", DEFAULTS["backlight"])
            self.invert = data.get("invert", DEFAULTS["invert"])
            self.mouse_sensitivity = data.get("mouse_sensitivity", DEFAULTS["mouse_sensitivity"])
            self.scroll_sensitivity = data.get("scroll_sensitivity", DEFAULTS["scroll_sensitivity"])
            self.os_mode = data.get("os_mode", DEFAULTS["os_mode"])
            self.swap_xy = data.get("swap_xy", DEFAULTS["swap_xy"])
            self.invert_vscroll = data.get("invert_vscroll", DEFAULTS["invert_vscroll"])
            self.invert_hscroll = data.get("invert_hscroll", DEFAULTS["invert_hscroll"])
            print("设置已从", SETTINGS_FILE, "加载")
        except (OSError, ValueError):
            print("未找到已保存的设置，使用默认值")
        return self

    def save(self):
        data = {
            "backlight": self.backlight,
            "invert": self.invert,
            "mouse_sensitivity": self.mouse_sensitivity,
            "scroll_sensitivity": self.scroll_sensitivity,
            "os_mode": self.os_mode,
            "swap_xy": self.swap_xy,
            "invert_vscroll": self.invert_vscroll,
            "invert_hscroll": self.invert_hscroll,
        }
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(data, f)
        except OSError as e:
            print("保存设置失败:", e)


# ============ 应用到硬件 ============
def apply_backlight(display, value):
    value = max(BACKLIGHT_MIN, min(BACKLIGHT_MAX, int(value)))
    try:
        display.set_backlight(value)
    except Exception as e:
        print("设置背光失败:", e)
    return value


def apply_invert(display, invert):
    """尝试常见的几种反色API，兼容不同显示驱动"""
    for method_name in ("set_color_inversion", "invert_colors", "invert_display"):
        method = getattr(display, method_name, None)
        if method is not None:
            try:
                method(bool(invert))
                return
            except Exception as e:
                print(method_name, "调用失败:", e)
    print("警告: 未找到可用的反色接口")


SETTINGS_MARGIN = 10


# ============ 设置界面 ============
class SettingsUI:
    """设置模式UI。create时构建控件，destroy时整体删除，不留残留控件。
    内容超出屏幕高度时可竖直滚动。"""

    def __init__(self, scrn, display, cfg, width, height, on_disconnect=None, is_connected=None,
                 on_forget_hosts=None, on_set_auto_advertise=None, is_auto_advertise=None,
                 get_bonded_key_count=None):
        self.scrn = scrn
        self.display = display
        self.cfg = cfg
        self.width = width
        self.height = height
        self.margin = SETTINGS_MARGIN
        self.content_width = width - self.margin * 2
        self.on_disconnect = on_disconnect
        self.is_connected = is_connected
        self.on_forget_hosts = on_forget_hosts
        self.on_set_auto_advertise = on_set_auto_advertise
        self.is_auto_advertise = is_auto_advertise
        self.get_bonded_key_count = get_bonded_key_count
        self._forget_armed_at = 0

        self._title_font = _pick_font('font_montserrat_18', 'font_montserrat_16')
        self._label_font = _pick_font('font_montserrat_14', 'font_montserrat_12')

        # 根容器，销毁时一次性删除所有子控件，不留边线残留
        self.container = lv.obj(scrn)
        self.container.set_size(width, height)
        self.container.set_pos(0, 0)
        self.container.set_style_bg_color(lv.color_hex(0x0a0a1a), 0)
        self.container.set_style_bg_opa(lv.OPA.COVER, 0)
        self.container.set_style_border_width(0, 0)
        self.container.set_style_radius(0, 0)
        self.container.set_style_pad_all(self.margin, 0)
        # 内容超出高度时允许竖直滚动，并显示滚动条
        self.container.set_scrollbar_mode(lv.SCROLLBAR_MODE.AUTO)
        self.container.set_scroll_dir(lv.DIR.VER)
        self.container.set_style_bg_color(lv.color_hex(0x4488FF), lv.PART.SCROLLBAR)

        title = lv.label(self.container)
        title.set_text("Settings")
        title.set_style_text_font(self._title_font, 0)
        title.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
        title.set_pos(0, 0)

        row_h = 40
        y = 30

        y = self._build_slider_row(
            y, row_h, "Backlight",
            BACKLIGHT_MIN, BACKLIGHT_MAX, int(self.cfg.backlight),
            self._on_backlight_changed, value_suffix="%"
        )

        y = self._build_switch_row(
            y, row_h, "Invert Colors", self.cfg.invert, self._on_invert_changed
        )

        y = self._build_slider_row(
            y, row_h, "Mouse Sensitivity",
            int(MOUSE_SENS_MIN * 10), int(MOUSE_SENS_MAX * 10),
            int(self.cfg.mouse_sensitivity * 10),
            self._on_mouse_sens_changed, scale=10
        )

        y = self._build_slider_row(
            y, row_h, "Scroll Sensitivity",
            int(SCROLL_SENS_MIN * 10), int(SCROLL_SENS_MAX * 10),
            int(self.cfg.scroll_sensitivity * 10),
            self._on_scroll_sens_changed, scale=10
        )

        y = self._build_switch_row(
            y, row_h, "Invert Vertical Scroll", self.cfg.invert_vscroll, self._on_invert_vscroll_changed
        )

        y = self._build_switch_row(
            y, row_h, "Invert Horizontal Scroll", self.cfg.invert_hscroll, self._on_invert_hscroll_changed
        )

        y = self._build_switch_row(
            y, row_h, "Swap X/Y Axes", self.cfg.swap_xy, self._on_swap_xy_changed
        )

        y = self._build_switch_row(
            y, row_h, "macOS Shortcuts (Cmd)", self.cfg.os_mode == "mac", self._on_os_mode_changed
        )

        y = self._build_connection_row(y, row_h)
        y = self._build_auto_advertise_row(y, row_h)
        y = self._build_forget_hosts_row(y, row_h)

        # 底部留出一点空间，让滚动条到底时不贴边
        y += self.margin

    # ---------- 控件构建辅助 ----------
    def _build_slider_row(self, y, row_h, label_text, vmin, vmax, vinit, on_change, scale=1, value_suffix=""):
        label = lv.label(self.container)
        label.set_text(label_text)
        label.set_style_text_font(self._label_font, 0)
        label.set_style_text_color(lv.color_hex(0xCCCCDD), 0)
        label.set_pos(0, y)

        value_label = lv.label(self.container)
        value_label.set_style_text_font(self._label_font, 0)
        value_label.set_style_text_color(lv.color_hex(0x66AAFF), 0)
        value_label.set_pos(self.content_width - 60, y)
        value_label.set_size(60, 16)
        value_label.set_style_text_align(lv.TEXT_ALIGN.RIGHT, 0)

        def _fmt(v):
            if scale != 1:
                return "{:.1f}".format(v / scale)
            return "{}{}".format(v, value_suffix)

        value_label.set_text(_fmt(vinit))

        slider = lv.slider(self.container)
        slider.set_size(self.content_width - 4, 12)
        slider.set_pos(0, y + 18)
        slider.set_range(vmin, vmax)
        slider.set_value(vinit, lv.ANIM.OFF)
        slider.set_style_bg_color(lv.color_hex(0x4488FF), lv.PART.KNOB)
        slider.set_style_bg_color(lv.color_hex(0x4488FF), lv.PART.INDICATOR)
        slider.set_style_bg_color(lv.color_hex(0x33334d), lv.PART.MAIN)

        def _cb(e):
            v = slider.get_value()
            value_label.set_text(_fmt(v))
            on_change(v)

        slider.add_event_cb(_cb, lv.EVENT.VALUE_CHANGED, None)
        # 触摸释放时才落盘保存，减少flash写入次数
        slider.add_event_cb(lambda e: self.cfg.save(), lv.EVENT.RELEASED, None)

        return y + row_h

    def _build_switch_row(self, y, row_h, label_text, vinit, on_change):
        label = lv.label(self.container)
        label.set_text(label_text)
        label.set_style_text_font(self._label_font, 0)
        label.set_style_text_color(lv.color_hex(0xCCCCDD), 0)
        label.set_pos(0, y)

        sw = lv.switch(self.container)
        sw.set_pos(self.content_width - 50, y - 4)
        sw.set_style_bg_color(lv.color_hex(0x4488FF), lv.PART.INDICATOR | lv.STATE.CHECKED)
        if vinit:
            sw.add_state(lv.STATE.CHECKED)

        def _cb(e):
            checked = sw.has_state(lv.STATE.CHECKED)
            on_change(checked)
            self.cfg.save()

        sw.add_event_cb(_cb, lv.EVENT.VALUE_CHANGED, None)

        return y + row_h

    def _build_connection_row(self, y, row_h):
        sep = lv.obj(self.container)
        sep.set_size(self.content_width, 1)
        sep.set_pos(0, y)
        sep.set_style_bg_color(lv.color_hex(0x444466), 0)
        sep.set_style_bg_opa(lv.OPA.COVER, 0)
        sep.set_style_border_width(0, 0)
        y += 10

        self.conn_status_label = lv.label(self.container)
        self.conn_status_label.set_style_text_font(self._label_font, 0)
        self.conn_status_label.set_pos(0, y)
        self.conn_status_label.set_size(self.content_width, 16)

        self.disconnect_btn = lv.button(self.container)
        self.disconnect_btn.set_size(self.content_width, 34)
        self.disconnect_btn.set_pos(0, y + 24)
        self.disconnect_btn.set_style_bg_color(lv.color_hex(0x662222), 0)
        self.disconnect_btn.set_style_radius(6, 0)
        btn_label = lv.label(self.disconnect_btn)
        btn_label.set_text("Disconnect")
        btn_label.center()
        self.disconnect_btn.add_event_cb(self._on_disconnect_clicked, lv.EVENT.CLICKED, None)

        self._refresh_connection_row()

        return y + 24 + 34 + row_h - 40

    def _refresh_connection_row(self):
        connected = self.is_connected() if self.is_connected else False
        if connected:
            self.conn_status_label.set_text("Status: Connected")
            self.conn_status_label.set_style_text_color(lv.color_hex(0x4488FF), 0)
            self.disconnect_btn.remove_state(lv.STATE.DISABLED)
        else:
            self.conn_status_label.set_text("Status: Not connected")
            self.conn_status_label.set_style_text_color(lv.color_hex(0x8888AA), 0)
            self.disconnect_btn.add_state(lv.STATE.DISABLED)

        # Disconnect按钮可能顺带关闭了自动广播，这里把开关状态同步过来
        # (add_state/remove_state不会触发VALUE_CHANGED事件，不会造成死循环)
        if hasattr(self, "auto_adv_switch") and self.is_auto_advertise is not None:
            if self.is_auto_advertise():
                self.auto_adv_switch.add_state(lv.STATE.CHECKED)
            else:
                self.auto_adv_switch.remove_state(lv.STATE.CHECKED)

    def refresh_connection_status(self):
        """由主循环周期性调用，刷新连接状态显示(BLE可能在设置界面打开时断开/连接)"""
        if hasattr(self, "conn_status_label"):
            self._refresh_connection_row()

    def _on_disconnect_clicked(self, e):
        if self.on_disconnect is not None:
            self.on_disconnect()
        self._refresh_connection_row()

    def _build_auto_advertise_row(self, y, row_h):
        label = lv.label(self.container)
        label.set_text("Stay Connectable")
        label.set_style_text_font(self._label_font, 0)
        label.set_style_text_color(lv.color_hex(0xCCCCDD), 0)
        label.set_pos(0, y)

        self.auto_adv_switch = lv.switch(self.container)
        self.auto_adv_switch.set_pos(self.content_width - 50, y - 4)
        self.auto_adv_switch.set_style_bg_color(lv.color_hex(0x4488FF), lv.PART.INDICATOR | lv.STATE.CHECKED)
        initial = self.is_auto_advertise() if self.is_auto_advertise else True
        if initial:
            self.auto_adv_switch.add_state(lv.STATE.CHECKED)

        def _cb(e):
            checked = self.auto_adv_switch.has_state(lv.STATE.CHECKED)
            if self.on_set_auto_advertise is not None:
                self.on_set_auto_advertise(checked)

        self.auto_adv_switch.add_event_cb(_cb, lv.EVENT.VALUE_CHANGED, None)

        y += 18
        hint = lv.label(self.container)
        hint.set_style_text_font(self._label_font, 0)
        hint.set_style_text_color(lv.color_hex(0x666688), 0)
        hint.set_pos(0, y)
        hint.set_size(self.content_width, 14)
        hint.set_text("Off = don't auto reconnect after Disconnect")

        return y + row_h - 18

    def _build_forget_hosts_row(self, y, row_h):
        y += 6
        hint = lv.label(self.container)
        hint.set_style_text_font(self._label_font, 0)
        hint.set_style_text_color(lv.color_hex(0x8888AA), 0)
        hint.set_pos(0, y)
        hint.set_size(self.content_width, 16)
        count = self.get_bonded_key_count() if self.get_bonded_key_count else None
        if count is not None:
            hint.set_text("Forget all paired hosts ({} keys stored):".format(count))
        else:
            hint.set_text("Forget all paired hosts:")
        y += 20

        self.forget_btn = lv.button(self.container)
        self.forget_btn.set_size(self.content_width, 34)
        self.forget_btn.set_pos(0, y)
        self.forget_btn.set_style_bg_color(lv.color_hex(0x442222), 0)
        self.forget_btn.set_style_radius(6, 0)
        self.forget_btn_label = lv.label(self.forget_btn)
        self.forget_btn_label.set_text("Forget All Hosts")
        self.forget_btn_label.center()
        self.forget_btn.add_event_cb(self._on_forget_hosts_clicked, lv.EVENT.CLICKED, None)

        return y + row_h

    def _on_forget_hosts_clicked(self, e):
        now = time.ticks_ms()
        # 需要3秒内连续点两次才会真正执行，避免误触清空全部配对记录
        if self._forget_armed_at and time.ticks_diff(now, self._forget_armed_at) < 3000:
            self._forget_armed_at = 0
            self.forget_btn_label.set_text("Forget All Hosts")
            if self.on_forget_hosts is not None:
                self.on_forget_hosts()
            self._refresh_connection_row()
        else:
            self._forget_armed_at = now
            self.forget_btn_label.set_text("Tap again to confirm")

    # ---------- 回调 ----------
    def _on_backlight_changed(self, v):
        self.cfg.backlight = apply_backlight(self.display, v)

    def _on_invert_changed(self, checked):
        self.cfg.invert = checked
        apply_invert(self.display, checked)

    def _on_mouse_sens_changed(self, v):
        self.cfg.mouse_sensitivity = v / 10

    def _on_scroll_sens_changed(self, v):
        self.cfg.scroll_sensitivity = v / 10

    def _on_os_mode_changed(self, checked):
        self.cfg.os_mode = "mac" if checked else "win"

    def _on_swap_xy_changed(self, checked):
        self.cfg.swap_xy = checked

    def _on_invert_vscroll_changed(self, checked):
        self.cfg.invert_vscroll = checked

    def _on_invert_hscroll_changed(self, checked):
        self.cfg.invert_hscroll = checked

    # ---------- 生命周期 ----------
    def destroy(self):
        self.container.delete()