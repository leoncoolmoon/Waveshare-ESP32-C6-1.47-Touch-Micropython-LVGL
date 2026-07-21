# mouse.py - 鼠标 / 滚轮模块
# 包含: BLE鼠标动作发送(MouseHID)、鼠标触控UI(MouseUI)
# UI文字全部使用英文

import lvgl as lv
import time

MARGIN = 5


def _pick_font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font is not None:
            return font
    return None


# ============ BLE 鼠标动作 ============
class MouseHID:
    """封装鼠标相关的BLE HID发送，依赖 main.py 中的 BLEState (ble.hid / ble.connected)"""

    def __init__(self, ble):
        self.ble = ble

    def move(self, dx, dy):
        if not self.ble.connected:
            return
        max_move = 50
        dx = max(-max_move, min(max_move, dx))
        dy = max(-max_move, min(max_move, dy))
        self.ble.hid.mouse_set_xy(dx, dy)
        self.ble.hid.mouse_send()

    def scroll(self, dx, dy):
        if not self.ble.connected:
            return
        dx = max(-5, min(5, int(dx)))
        dy = max(-5, min(5, int(dy)))
        if dx == 0 and dy == 0:
            return
        self.ble.hid.mouse_set_xy(0, 0)
        self.ble.hid.mouse_set_wheel(dy)
        self.ble.hid.mouse_set_pan(dx)
        self.ble.hid.mouse_send()
        time.sleep_ms(5)
        self.ble.hid.mouse_set_wheel(0)
        self.ble.hid.mouse_set_pan(0)
        self.ble.hid.mouse_send()

    def click(self, button):
        if not self.ble.connected:
            return
        self.ble.hid.mouse_set_xy(0, 0)
        if button == 1:
            self.ble.hid.mouse_set_buttons(left=1)
        elif button == 2:
            self.ble.hid.mouse_set_buttons(right=1)
        elif button == 3:
            self.ble.hid.mouse_set_buttons(middle=1)
        self.ble.hid.mouse_send()
        time.sleep_ms(50)
        self.ble.hid.mouse_set_buttons()
        self.ble.hid.mouse_send()

    def tap_click(self):
        if not self.ble.connected:
            return
        self.ble.hid.mouse_set_xy(0, 0)
        self.ble.hid.mouse_set_buttons(left=1)
        self.ble.hid.mouse_send()
        time.sleep_ms(30)
        self.ble.hid.mouse_set_buttons()
        self.ble.hid.mouse_send()


# ============ 鼠标 / 滚轮 触控UI ============
class MouseUI:
    """鼠标+滚轮模式UI。create时构建所有控件(挂在同一个容器下)，
    destroy时整体删除容器即可清除所有控件，不留边线残留。"""

    def __init__(self, scrn, mouse_hid, cfg, width, height, is_scroll=False):
        self.scrn = scrn
        self.hid = mouse_hid
        self.cfg = cfg
        self.width = width
        self.height = height
        self.is_scroll = is_scroll

        # 触摸状态
        self.touch_pressed = False
        self.last_x = 0
        self.last_y = 0
        self.touch_start_time = 0
        self.touch_start_x = 0
        self.touch_start_y = 0
        self.is_tap = False
        self.tap_threshold = 20
        self.tap_time_threshold = 300
        self.scroll_accum_x = 0
        self.scroll_accum_y = 0
        self.last_scroll_time = 0
        self.scroll_interval_ms = 40
        self.scroll_px_per_notch = 8

        self.margin = MARGIN
        self.btn_height = int(height * 0.225)
        self.btn_width = int(width * 0.31)
        self.btn_spacing = int(width * 0.02)

        self._label_font = _pick_font('font_montserrat_14', 'font_montserrat_12')

        # 根容器
        self.container = lv.obj(scrn)
        self.container.set_size(width, height)
        self.container.set_pos(0, 0)
        self.container.set_style_bg_opa(lv.OPA.TRANSP, 0)
        self.container.set_style_border_width(0, 0)
        self.container.set_style_pad_all(0, 0)
        self.container.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.container.set_scroll_dir(lv.DIR.NONE)

        self._create_buttons()
        self._create_canvas()
        self._create_mode_indicator()
        self.set_submode(is_scroll)

    # ---------- 控件创建 ----------
    def _create_buttons(self):
        total_btn_width = self.btn_width * 3 + self.btn_spacing * 2
        start_x = (self.width - total_btn_width) // 2

        self.btn_left = lv.button(self.container)
        self.btn_left.set_size(self.btn_width, self.btn_height - self.margin)
        self.btn_left.set_pos(start_x, self.margin // 2)
        self.btn_left.set_style_bg_color(lv.color_hex(0x2d3436), 0)
        self.btn_left.set_style_radius(8, 0)
        label_left = lv.label(self.btn_left)
        label_left.set_text("L")
        label_left.center()
        self.btn_left.add_event_cb(lambda e: self.hid.click(1), lv.EVENT.CLICKED, None)

        self.btn_middle = lv.button(self.container)
        self.btn_middle.set_size(self.btn_width, self.btn_height - self.margin)
        self.btn_middle.set_pos(start_x + self.btn_width + self.btn_spacing, self.margin // 2)
        self.btn_middle.set_style_bg_color(lv.color_hex(0x2d3436), 0)
        self.btn_middle.set_style_radius(8, 0)
        label_middle = lv.label(self.btn_middle)
        label_middle.set_text("M")
        label_middle.center()
        self.btn_middle.add_event_cb(lambda e: self.hid.click(3), lv.EVENT.CLICKED, None)

        self.btn_right = lv.button(self.container)
        self.btn_right.set_size(self.btn_width, self.btn_height - self.margin)
        self.btn_right.set_pos(start_x + (self.btn_width + self.btn_spacing) * 2, self.margin // 2)
        self.btn_right.set_style_bg_color(lv.color_hex(0x2d3436), 0)
        self.btn_right.set_style_radius(8, 0)
        label_right = lv.label(self.btn_right)
        label_right.set_text("R")
        label_right.center()
        self.btn_right.add_event_cb(lambda e: self.hid.click(2), lv.EVENT.CLICKED, None)

    def _create_canvas(self):
        self.canvas_width = self.width - self.margin * 2
        self.canvas_height = self.height - self.btn_height - self.margin * 3

        self.panel = lv.obj(self.container)
        self.panel.set_size(self.canvas_width, self.canvas_height)
        self.panel.set_pos(self.margin, self.btn_height + self.margin)
        self.panel.set_style_bg_color(lv.color_hex(0x1a1a2e), 0)
        self.panel.set_style_border_width(2, 0)
        self.panel.set_style_border_color(lv.color_hex(0x444444), 0)
        self.panel.set_style_radius(5, 0)
        self.panel.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.panel.set_scroll_dir(lv.DIR.NONE)

        self.canvas = lv.canvas(self.panel)
        self.canvas.set_size(self.canvas_width, self.canvas_height)
        self.canvas.set_pos(0, 0)
        self.canvas.set_style_bg_opa(lv.OPA._0, 0)
        self.canvas.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.canvas.set_scroll_dir(lv.DIR.NONE)

        self.cbuf = bytearray(self.canvas_width * self.canvas_height * 2)
        self.canvas.set_buffer(self.cbuf, self.canvas_width, self.canvas_height,
                                lv.COLOR_FORMAT.RGB565)
        self.canvas.fill_bg(lv.color_hex(0x0a0a1a), lv.OPA.COVER)

    def _create_mode_indicator(self):
        """左上角: 绿色/白色小圆点 + M/S 表示当前是鼠标还是滚轮子模式"""
        indicator_size = int(min(self.width, self.height) * 0.09)
        self.mode_dot = lv.obj(self.container)
        self.mode_dot.set_size(indicator_size, indicator_size)
        self.mode_dot.set_pos(self.margin, self.margin)
        self.mode_dot.set_style_radius(indicator_size // 2, 0)
        self.mode_dot.set_style_border_width(1, 0)
        self.mode_dot.set_style_border_color(lv.color_hex(0x888888), 0)

        self.mode_letter = lv.label(self.mode_dot)
        self.mode_letter.set_style_text_font(self._label_font, 0)
        self.mode_letter.center()

    # ---------- 子模式切换(鼠标<->滚轮，同一UI无需重建) ----------
    def set_submode(self, is_scroll):
        self.is_scroll = is_scroll
        self.scroll_accum_x = 0
        self.scroll_accum_y = 0
        if is_scroll:
            self.mode_dot.set_style_bg_color(lv.color_hex(0xffffff), 0)
            self.mode_letter.set_text("S")
            self.mode_letter.set_style_text_color(lv.color_hex(0x333333), 0)
        else:
            self.mode_dot.set_style_bg_color(lv.color_hex(0x00ff88), 0)
            self.mode_letter.set_text("M")
            self.mode_letter.set_style_text_color(lv.color_hex(0x003311), 0)

    # ---------- 触摸处理 ----------
    def handle_mouse_scroll(self, move_dx, move_dy):
        self.scroll_accum_x += move_dx
        self.scroll_accum_y += move_dy

        now = time.ticks_ms()
        if time.ticks_diff(now, self.last_scroll_time) < self.scroll_interval_ms:
            return

        scroll_scale = self.cfg.scroll_sensitivity
        if abs(self.scroll_accum_y) >= abs(self.scroll_accum_x):
            notches = int(self.scroll_accum_y * scroll_scale / self.scroll_px_per_notch)
            if notches != 0:
                self.hid.scroll(0, notches)
                self.scroll_accum_x = 0
                self.scroll_accum_y = 0
                self.last_scroll_time = now
        else:
            notches = int(self.scroll_accum_x * scroll_scale / self.scroll_px_per_notch)
            if notches != 0:
                self.hid.scroll(notches, 0)
                self.scroll_accum_x = 0
                self.scroll_accum_y = 0
                self.last_scroll_time = now

    def update_touch(self, indev):
        state = indev.get_state()

        if state == indev.PRESSED:
            point = lv.point_t()
            indev.get_point(point)
            x = point.x
            y = point.y

            canvas_x = x - self.margin
            canvas_y = y - self.btn_height - self.margin

            if 0 <= canvas_x < self.canvas_width and 0 <= canvas_y < self.canvas_height:
                if not self.touch_pressed:
                    self.touch_pressed = True
                    self.touch_start_time = time.ticks_ms()
                    self.touch_start_x = canvas_x
                    self.touch_start_y = canvas_y
                    self.last_x = canvas_x
                    self.last_y = canvas_y
                    self.is_tap = True
                    self.scroll_accum_x = 0
                    self.scroll_accum_y = 0
                else:
                    dx = canvas_x - self.last_x
                    dy = canvas_y - self.last_y

                    total_dx = canvas_x - self.touch_start_x
                    total_dy = canvas_y - self.touch_start_y
                    if abs(total_dx) > self.tap_threshold or abs(total_dy) > self.tap_threshold:
                        self.is_tap = False

                    if abs(dx) > 1 or abs(dy) > 1:
                        self.is_tap = False
                        sensitivity = self.cfg.mouse_sensitivity
                        move_dx = dx * sensitivity
                        move_dy = dy * sensitivity

                        if self.is_scroll:
                            self.handle_mouse_scroll(move_dx, move_dy)
                        else:
                            if abs(move_dx) > 1 or abs(move_dy) > 1:
                                self.hid.move(int(move_dx), int(move_dy))

                        self.last_x = canvas_x
                        self.last_y = canvas_y
            else:
                self.touch_pressed = False
                self.is_tap = False

        else:
            if self.touch_pressed:
                if self.is_tap:
                    elapsed = time.ticks_diff(time.ticks_ms(), self.touch_start_time)
                    if elapsed < self.tap_time_threshold:
                        self.hid.tap_click()

                self.touch_pressed = False
                self.is_tap = False
                self.scroll_accum_x = 0
                self.scroll_accum_y = 0

    # ---------- 生命周期 ----------
    def destroy(self):
        self.container.delete()
