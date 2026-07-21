# keyboard.py - 键盘模块
# 包含: 键位数据、BLE按键发送(KeyboardHID)、触控键盘UI(KeyboardUI)
# UI文字全部使用英文

import lvgl as lv
import time

# ============ 键位表 ============
KEYMAP = [
    ['`', '1', '2', '3', '4', '5', '6',
     '7', '8', '9', '0', '-', '=', 'BSPC'],

    ['TAB', 'CAP', 'q', 'w', 'e', 'r', 't',
     'y', 'u', 'i', 'o', 'p', '[', ']'],

    ['FN', 'SHIFT', 'a', 's', 'd', 'f', 'g',
     'h', 'j', 'k', 'l', ';', "'", 'ENT'],

    ['CTL', 'OPT', 'ALT', 'z', 'x', 'c', 'v',
     'b', 'n', 'm', ',', '.', '/', 'SPC'],
]

KEYMAP_FN = [
    ['ESC', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6',
     'F7', 'F8', 'F9', 'F10', '_', '=', 'BSPC'],

    ['TAB', 'CAP', '!', '@', '#', '$', '%',
     '^', '&', '*', '(', ')', '[', ']'],

    ['FN', 'SHIFT', '~', 'AT', 'CT', '\\', '|',
     "'", 'Udo', 'Cut', 'Copy', 'Paste', 'PtS', 'ENT'],

    ['CTL', 'OPT', 'ALT', 'PGUP', 'UP', 'PGDN', 'INS',
     'DEL', 'HOME', 'END', 'LEFT', 'DOWN', 'RIGHT', 'SPC'],
]

GROUP_SIZE = 7

MODIFIER_CODES = {
    'CTL': 0x01,
    'SHIFT': 0x02,
    'ALT': 0x04,
    'OPT': 0x08,
}

COMBO_KEYS = {
    'AT': (0x04, 'TAB'),
    'CT': (0x01, 'TAB'),
    'Udo': (0x01, 'z'),
    'Cut': (0x01, 'x'),
    'Copy': (0x01, 'c'),
    'Paste': (0x01, 'v'),
}

USE_SYMBOL_ICONS = True


def _resolve_symbol(name):
    ns = getattr(lv, 'SYMBOL', None)
    if ns is not None:
        sym = getattr(ns, name, None)
        if sym is not None:
            return sym
    return getattr(lv, 'SYMBOL_' + name, None)


KEY_DISPLAY = {
    'BSPC':  ('BSP', 'BACKSPACE'),
    'TAB':   ('TAB', 'LOOP'),
    'ENT':   ('ENT', 'NEW_LINE'),
    'SHIFT': ('SFT', None),
    'ESC':   ('ESC', None),
    'SPC':   ('SPC', None),
    'CTL':   ('CTL', None),
    'ALT':   ('ALT', None),
    'OPT':   ('OPT', 'LIST'),
    'DEL':   ('DEL', 'CLOSE'),
    'UP':    ('Up', 'UP'),
    'DOWN':  ('Dn', 'DOWN'),
    'LEFT':  ('Lf', 'LEFT'),
    'RIGHT': ('Rt', 'RIGHT'),
    'CAP':   ('CAP', None),
    'Udo':   ('Udo', None),
    'Cut':   ('Cut', 'CUT'),
    'Copy':  ('Cpy', 'COPY'),
    'Paste': ('Pst', 'PASTE'),
    'HOME':  ('HM', 'PREV'),
    'END':   ('END', 'NEXT'),
    'PGUP':  ('PgU', None),
    'PGDN':  ('PgD', None),
    'INS':   ('INS', None),
    'PtS':   ('PtS', None),
    'AT':    ('A+T', None),
    'CT':    ('C+T', None),
}


def _build_display_map():
    result = {}
    for key, (text, symbol_name) in KEY_DISPLAY.items():
        symbol = _resolve_symbol(symbol_name) if (USE_SYMBOL_ICONS and symbol_name) else None
        result[key] = symbol if symbol is not None else text
    return result


DISPLAY_MAP = _build_display_map()


def _get_display_text(key):
    if key in DISPLAY_MAP:
        return DISPLAY_MAP[key]
    if len(key) <= 2:
        return key
    if key[0] == 'F' and key[1:].isdigit():
        return 'F0'
    return key[0]


_FUNCTION_KEYS = {
    'BSPC', 'TAB', 'ENT', 'SHIFT', 'ESC', 'SPC',
    'CTL', 'ALT', 'OPT', 'FN', 'DEL', 'CAP',
    'UP', 'DOWN', 'LEFT', 'RIGHT',
    'HOME', 'END', 'PGUP', 'PGDN', 'INS', 'PtSc',
    'AT', 'CT', 'Udo', 'Cut', 'Copy', 'Paste',
    'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10'
}


def _is_function_key(key):
    return key in _FUNCTION_KEYS


def _pick_font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font is not None:
            return font
    return None


def _row_from_y(y, h=172):
    if y < h * 0.25:
        return 0
    elif y < h * 0.5:
        return 1
    elif y < h * 0.75:
        return 2
    else:
        return 3


def _col_from_x(x, preview_x=5, preview_width=280, total_cols=GROUP_SIZE):
    rel = x - preview_x
    if rel < 0:
        return 0
    col = int(rel * total_cols // preview_width)
    if col < 0:
        col = 0
    elif col > total_cols - 1:
        col = total_cols - 1
    return col


# ============ HID Key Codes ============
HID_KEYCODES = {
    'BSPC': 0x2A,
    'TAB': 0x2B,
    'ENT': 0x28,
    'ESC': 0x29,
    'SPC': 0x2C,
    'DEL': 0x4C,
    'UP': 0x52,
    'DOWN': 0x51,
    'LEFT': 0x50,
    'RIGHT': 0x4F,
    'CAP': 0x39,
    'HOME': 0x4A,
    'END': 0x4D,
    'PGUP': 0x4B,
    'PGDN': 0x4E,
    'INS': 0x49,
    'PtSc': 0x46,
}

FUNCTION_KEYCODES = {
    'F1': 0x3A, 'F2': 0x3B, 'F3': 0x3C, 'F4': 0x3D,
    'F5': 0x3E, 'F6': 0x3F, 'F7': 0x40, 'F8': 0x41,
    'F9': 0x42, 'F10': 0x43,
}

_CHAR_TO_HID = {
    ' ': 0x2C, '!': 0x1E, '@': 0x1F, '#': 0x20, '$': 0x21, '%': 0x22,
    '^': 0x23, '&': 0x24, '*': 0x25, '(': 0x26, ')': 0x27, '_': 0x2D,
    '+': 0x2E, '{': 0x2F, '}': 0x30, '|': 0x31, ':': 0x33, '"': 0x34,
    '<': 0x36, '>': 0x37, '?': 0x38, '~': 0x35, '`': 0x35, '-': 0x2D,
    '=': 0x2E, '[': 0x2F, ']': 0x30, '\\': 0x31, ';': 0x33, "'": 0x34,
    ',': 0x36, '.': 0x37, '/': 0x38,
}

_SHIFT_SYMBOLS = set('!@#$%^&*()_+{}|:"<>?~')


def _char_to_hid(char):
    if 'a' <= char <= 'z':
        return 0x04 + ord(char) - ord('a')
    elif 'A' <= char <= 'Z':
        return 0x04 + ord(char) - ord('A')
    elif '1' <= char <= '9':
        return 0x1E + ord(char) - ord('1')
    elif char == '0':
        return 0x27
    return _CHAR_TO_HID.get(char)


def _needs_shift(char):
    if 'A' <= char <= 'Z':
        return True
    return char in _SHIFT_SYMBOLS


# ============ BLE 键盘动作 ============
class KeyboardHID:
    """封装键盘相关的BLE HID发送，依赖 main.py 中的 BLEState (ble.hid / ble.connected)"""

    def __init__(self, ble):
        self.ble = ble

    def _set_mods(self, modifiers):
        self.ble.hid.kb_set_modifiers(
            left_control=1 if modifiers & 0x01 else 0,
            left_shift=1 if modifiers & 0x02 else 0,
            left_alt=1 if modifiers & 0x04 else 0,
            left_gui=1 if modifiers & 0x08 else 0
        )

    def send_key(self, key, modifiers=0):
        """发送单个按键(字符或功能键)，modifiers为当前锁定的修饰键掩码"""
        if not self.ble.connected:
            return False

        if len(key) == 1:
            code = _char_to_hid(key)
            if code is None:
                return False
            final_modifiers = modifiers
            if _needs_shift(key):
                final_modifiers |= 0x02
            self._set_mods(final_modifiers)
            self.ble.hid.kb_set_keys(code, 0, 0, 0, 0, 0)
            self.ble.hid.kb_send()
            time.sleep_ms(10)
            self.ble.hid.kb_set_modifiers()
            self.ble.hid.kb_set_keys()
            self.ble.hid.kb_send()
            time.sleep_ms(10)
            return True

        code = HID_KEYCODES.get(key) or FUNCTION_KEYCODES.get(key)
        if code is None:
            return False

        self._set_mods(modifiers)
        self.ble.hid.kb_set_keys(code, 0, 0, 0, 0, 0)
        self.ble.hid.kb_send()
        time.sleep_ms(10)
        self.ble.hid.kb_set_keys()
        self.ble.hid.kb_set_modifiers()
        self.ble.hid.kb_send()
        time.sleep_ms(10)
        return True

    def send_single_modifier(self, key):
        """单独敲一下某个修饰键(用于取消锁定时通知主机松开)"""
        if not self.ble.connected:
            return False
        code = MODIFIER_CODES.get(key)
        if code is None:
            return False
        self.ble.hid.kb_set_keys()
        self._set_mods(code)
        self.ble.hid.kb_send()
        time.sleep_ms(10)
        self.ble.hid.kb_set_modifiers()
        self.ble.hid.kb_send()
        time.sleep_ms(10)
        return True

    def send_combo(self, key, modifiers=0):
        if key not in COMBO_KEYS or not self.ble.connected:
            return False

        extra_mod, target = COMBO_KEYS[key]
        code = HID_KEYCODES.get(target)
        if code is None:
            code = _char_to_hid(target)
        if code is None:
            return False

        final_modifiers = modifiers | extra_mod
        self._set_mods(final_modifiers)
        self.ble.hid.kb_set_keys(code, 0, 0, 0, 0, 0)
        self.ble.hid.kb_send()
        time.sleep_ms(40)
        self.ble.hid.kb_set_keys()
        self.ble.hid.kb_set_modifiers()
        self.ble.hid.kb_send()
        time.sleep_ms(10)
        return True


# ============ 触控键盘UI ============
class KeyboardUI:
    """键盘模式UI。create时构建所有控件(挂在同一个容器下)，
    destroy时整体删除容器即可清除所有控件，不留边线残留。"""

    def __init__(self, scrn, keyboard_hid, width, height):
        self.scrn = scrn
        self.hid = keyboard_hid
        self.width = width
        self.height = height

        self.kb_locked_keys = []
        self.kb_state = 0
        self.kb_row = 0
        self.kb_col = -1
        self.kb_selected_row = 0
        self.kb_selected_col = -1
        self.kb_vert_bias = 'top'
        self.kb_prev_touch_y = 0
        self.kb_current_map = KEYMAP
        self.kb_vert_mid = height // 2
        self.kb_dy_threshold = 2
        self.caps_lock_on = False

        self.drag_x = 290
        self.drag_width = 30
        self.preview_x = 5
        self.preview_width = 280
        self.preview_top_y = 15
        self.preview_bottom_y = 95
        self.preview_strip_height = 45
        self.cell_width = self.preview_width // GROUP_SIZE

        self._preview_font = _pick_font('font_montserrat_16', 'font_montserrat_12')
        self._badge_font = _pick_font('font_montserrat_12', 'font_montserrat_16')
        self._drag_font = _pick_font('font_montserrat_16', 'font_montserrat_12')
        self._big_font = _pick_font('font_montserrat_16', 'font_montserrat_12')

        # 根容器
        self.container = lv.obj(scrn)
        self.container.set_size(width, height)
        self.container.set_pos(0, 0)
        self.container.set_style_bg_color(lv.color_hex(0x0a0a1a), 0)
        self.container.set_style_bg_opa(lv.OPA.COVER, 0)
        self.container.set_style_border_width(0, 0)
        self.container.set_style_pad_all(0, 0)
        self.container.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.container.set_scroll_dir(lv.DIR.NONE)

        self._create_preview_strips()
        self._create_drag_area()
        self._create_status_labels()
        self._create_caps_indicator()

        self.hide_keyboard_preview()

    # ---------- 控件创建 ----------
    def _create_preview_strips(self):
        self.preview_top_container = lv.obj(self.container)
        self.preview_top_container.set_pos(self.preview_x, self.preview_top_y)
        self.preview_top_container.set_size(self.preview_width, self.preview_strip_height)
        self.preview_top_container.set_style_bg_color(lv.color_hex(0x1a1a2e), 0)
        self.preview_top_container.set_style_bg_opa(lv.OPA.COVER, 0)
        self.preview_top_container.set_style_border_width(1, 0)
        self.preview_top_container.set_style_border_color(lv.color_hex(0x444466), 0)
        self.preview_top_container.set_style_radius(2, 0)
        self.preview_top_container.set_style_pad_all(0, 0)
        self.preview_top_container.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

        self.preview_bottom_container = lv.obj(self.container)
        self.preview_bottom_container.set_pos(self.preview_x, self.preview_bottom_y)
        self.preview_bottom_container.set_size(self.preview_width, self.preview_strip_height)
        self.preview_bottom_container.set_style_bg_color(lv.color_hex(0x1a1a2e), 0)
        self.preview_bottom_container.set_style_bg_opa(lv.OPA.COVER, 0)
        self.preview_bottom_container.set_style_border_width(1, 0)
        self.preview_bottom_container.set_style_border_color(lv.color_hex(0x444466), 0)
        self.preview_bottom_container.set_style_radius(2, 0)
        self.preview_bottom_container.set_style_pad_all(0, 0)
        self.preview_bottom_container.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

        self.cells_top = []
        self.cell_bg_top = []
        self.cells_bottom = []
        self.cell_bg_bottom = []

        for i in range(GROUP_SIZE):
            bg = lv.obj(self.preview_top_container)
            bg.set_pos(i * self.cell_width, 0)
            bg.set_size(self.cell_width, self.preview_strip_height)
            bg.set_style_bg_color(lv.color_hex(0x333355), 0)
            bg.set_style_bg_opa(lv.OPA.TRANSP, 0)
            bg.set_style_border_width(0, 0)
            bg.set_style_radius(0, 0)
            bg.set_style_pad_all(0, 0)
            self.cell_bg_top.append(bg)

            label = lv.label(self.preview_top_container)
            label.set_pos(i * self.cell_width, 0)
            label.set_size(self.cell_width, self.preview_strip_height)
            label.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
            label.set_style_text_font(self._preview_font, 0)
            label.set_style_text_color(lv.color_hex(0xCCCCDD), 0)
            label.set_style_pad_all(0, 0)
            label.set_long_mode(lv.label.LONG_MODE.CLIP)
            label.set_text('')
            self.cells_top.append(label)

            bg2 = lv.obj(self.preview_bottom_container)
            bg2.set_pos(i * self.cell_width, 0)
            bg2.set_size(self.cell_width, self.preview_strip_height)
            bg2.set_style_bg_color(lv.color_hex(0x333355), 0)
            bg2.set_style_bg_opa(lv.OPA.TRANSP, 0)
            bg2.set_style_border_width(0, 0)
            bg2.set_style_radius(0, 0)
            bg2.set_style_pad_all(0, 0)
            self.cell_bg_bottom.append(bg2)

            label2 = lv.label(self.preview_bottom_container)
            label2.set_pos(i * self.cell_width, 0)
            label2.set_size(self.cell_width, self.preview_strip_height)
            label2.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
            label2.set_style_text_font(self._preview_font, 0)
            label2.set_style_text_color(lv.color_hex(0xCCCCDD), 0)
            label2.set_style_pad_all(0, 0)
            label2.set_long_mode(lv.label.LONG_MODE.CLIP)
            label2.set_text('')
            self.cells_bottom.append(label2)

    def _create_drag_area(self):
        self.drag_container = lv.obj(self.container)
        self.drag_container.set_pos(self.drag_x, 0)
        self.drag_container.set_size(self.drag_width, self.height)
        self.drag_container.set_style_bg_color(lv.color_hex(0x2a2a4e), 0)
        self.drag_container.set_style_bg_opa(lv.OPA.COVER, 0)
        self.drag_container.set_style_border_width(1, 0)
        self.drag_container.set_style_border_color(lv.color_hex(0x555577), 0)
        self.drag_container.set_style_radius(2, 0)
        self.drag_container.set_style_pad_all(0, 0)

        self.slot_labels = []
        slot_height = self.height // 4
        for i in range(4):
            label = lv.label(self.drag_container)
            label.set_pos(0, i * slot_height + slot_height // 2 - 8)
            label.set_size(self.drag_width, 16)
            label.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
            label.set_style_text_font(self._drag_font, 0)
            label.set_style_text_color(lv.color_hex(0x8888AA), 0)
            label.set_style_pad_all(0, 0)
            label.set_text(str(i + 1))
            self.slot_labels.append(label)

            if i < 3:
                line = lv.obj(self.drag_container)
                line.set_pos(2, (i + 1) * slot_height)
                line.set_size(self.drag_width - 4, 1)
                line.set_style_bg_color(lv.color_hex(0x444466), 0)
                line.set_style_bg_opa(lv.OPA.COVER, 0)
                line.set_style_border_width(0, 0)

        self.row_indicator = lv.obj(self.drag_container)
        self.row_indicator.set_pos(2, 0)
        self.row_indicator.set_size(self.drag_width - 4, slot_height - 2)
        self.row_indicator.set_style_bg_color(lv.color_hex(0x4488FF), 0)
        self.row_indicator.set_style_bg_opa(lv.OPA.TRANSP, 0)
        self.row_indicator.set_style_border_width(1, 0)
        self.row_indicator.set_style_border_color(lv.color_hex(0x66AAFF), 0)
        self.row_indicator.set_style_radius(2, 0)
        self.row_indicator.set_style_pad_all(0, 0)

        self.row_indicator_text = lv.label(self.row_indicator)
        self.row_indicator_text.set_pos(0, 0)
        self.row_indicator_text.set_size(self.drag_width - 4, slot_height - 2)
        self.row_indicator_text.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
        self.row_indicator_text.set_style_text_font(self._drag_font, 0)
        self.row_indicator_text.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
        self.row_indicator_text.set_style_pad_all(0, 0)
        self.row_indicator_text.set_text("")

    def _create_status_labels(self):
        self.kb_lock_label = lv.label(self.container)
        self.kb_lock_label.set_pos(5, self.height - 14)
        self.kb_lock_label.set_size(150, 12)
        self.kb_lock_label.set_style_text_font(self._badge_font, 0)
        self.kb_lock_label.set_style_text_color(lv.color_hex(0xFF6644), 0)
        self.kb_lock_label.set_style_pad_all(0, 0)
        self.kb_lock_label.set_text("")

        self.kb_status_label = lv.label(self.container)
        self.kb_status_label.set_pos(155, self.height - 14)
        self.kb_status_label.set_size(160, 12)
        self.kb_status_label.set_style_text_font(self._badge_font, 0)
        self.kb_status_label.set_style_text_color(lv.color_hex(0x66AAFF), 0)
        self.kb_status_label.set_style_text_align(lv.TEXT_ALIGN.RIGHT, 0)
        self.kb_status_label.set_style_pad_all(0, 0)
        self.kb_status_label.set_text("Drag right edge to pick a row")

    def _create_caps_indicator(self):
        """左上角: 红色/白色小圆点 + C 表示大写锁定状态"""
        indicator_size = int(min(self.width, self.height) * 0.09)
        self.caps_dot = lv.obj(self.container)
        self.caps_dot.set_size(indicator_size, indicator_size)
        self.caps_dot.set_pos(5, 5)
        self.caps_dot.set_style_radius(indicator_size // 2, 0)
        self.caps_dot.set_style_border_width(1, 0)
        self.caps_dot.set_style_border_color(lv.color_hex(0x888888), 0)

        self.caps_letter = lv.label(self.caps_dot)
        self.caps_letter.set_style_text_font(self._badge_font, 0)
        self.caps_letter.set_text("C")
        self.caps_letter.center()

        self._update_caps_indicator()

    def _update_caps_indicator(self):
        if self.caps_lock_on:
            self.caps_dot.set_style_bg_color(lv.color_hex(0xff3333), 0)
            self.caps_letter.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
        else:
            self.caps_dot.set_style_bg_color(lv.color_hex(0xffffff), 0)
            self.caps_letter.set_style_text_color(lv.color_hex(0x333333), 0)

    # ---------- 预览显示/隐藏(交互反馈，非模式切换) ----------
    def hide_keyboard_preview(self):
        for cell in self.cells_top + self.cells_bottom:
            cell.set_text('')
        for bg in self.cell_bg_top + self.cell_bg_bottom:
            bg.set_style_bg_opa(lv.OPA.TRANSP, 0)

    def kb_show_row_preview(self, row):
        keymap = KEYMAP_FN if 'FN' in self.kb_locked_keys else KEYMAP
        self.kb_current_map = keymap
        row_keys = keymap[row]
        top_keys = row_keys[:GROUP_SIZE]
        bottom_keys = row_keys[GROUP_SIZE:]

        for i, cell in enumerate(self.cells_top):
            if i < len(top_keys):
                key = top_keys[i]
                cell.set_text(_get_display_text(key))
                color = 0xFFA500 if _is_function_key(key) else 0xCCCCDD
                cell.set_style_text_color(lv.color_hex(color), 0)
                cell.set_style_text_font(self._preview_font, 0)
            else:
                cell.set_text('')

        for i, cell in enumerate(self.cells_bottom):
            if i < len(bottom_keys):
                key = bottom_keys[i]
                cell.set_text(_get_display_text(key))
                color = 0xFFA500 if _is_function_key(key) else 0xCCCCDD
                cell.set_style_text_color(lv.color_hex(color), 0)
                cell.set_style_text_font(self._preview_font, 0)
            else:
                cell.set_text('')

        for bg in self.cell_bg_top + self.cell_bg_bottom:
            bg.set_style_bg_opa(lv.OPA.TRANSP, 0)

    def kb_clear_strip_highlight(self, cells, keys):
        for i, cell in enumerate(cells):
            if i < len(keys):
                cell.set_style_text_font(self._preview_font, 0)
                color = 0xFFA500 if _is_function_key(keys[i]) else 0xCCCCDD
                cell.set_style_text_color(lv.color_hex(color), 0)

    def kb_mark_active_cell(self, bg, cell):
        bg.set_style_bg_opa(lv.OPA._30, 0)
        bg.set_style_bg_color(lv.color_hex(0x4488FF), 0)
        bg.set_style_border_width(0, 0)
        cell.set_style_text_font(self._big_font, 0)
        cell.set_style_text_color(lv.color_hex(0xFFFFFF), 0)

    def kb_mark_candidate_cell(self, bg, cell):
        bg.set_style_bg_opa(lv.OPA.TRANSP, 0)
        bg.set_style_border_width(2, 0)
        bg.set_style_border_color(lv.color_hex(0x4488FF), 0)
        cell.set_style_text_font(self._preview_font, 0)
        cell.set_style_text_color(lv.color_hex(0x99CCFF), 0)

    def kb_highlight_col(self, col, bias):
        row_keys = self.kb_current_map[self.kb_selected_row] if self.kb_selected_row < 4 else []
        top_keys = row_keys[:GROUP_SIZE]
        bottom_keys = row_keys[GROUP_SIZE:]

        for bg in self.cell_bg_top + self.cell_bg_bottom:
            bg.set_style_bg_opa(lv.OPA.TRANSP, 0)
            bg.set_style_border_width(0, 0)

        self.kb_clear_strip_highlight(self.cells_top, top_keys)
        self.kb_clear_strip_highlight(self.cells_bottom, bottom_keys)

        if col < 0 or col >= GROUP_SIZE:
            return

        top_active = bias == 'top'

        if col < len(top_keys):
            if top_active:
                self.kb_mark_active_cell(self.cell_bg_top[col], self.cells_top[col])
            else:
                self.kb_mark_candidate_cell(self.cell_bg_top[col], self.cells_top[col])

        if col < len(bottom_keys):
            if top_active:
                self.kb_mark_candidate_cell(self.cell_bg_bottom[col], self.cells_bottom[col])
            else:
                self.kb_mark_active_cell(self.cell_bg_bottom[col], self.cells_bottom[col])

        self.kb_selected_col = col

    def kb_update_row_indicator(self, row):
        slot_height = self.height // 4
        y_pos = row * slot_height
        self.row_indicator.set_pos(2, y_pos + 1)
        self.row_indicator.set_size(self.drag_width - 4, slot_height - 2)
        self.row_indicator.set_style_bg_opa(lv.OPA._30, 0)
        self.row_indicator_text.set_text(str(row + 1))

        for i, label in enumerate(self.slot_labels):
            color = 0x66AAFF if i == row else 0x8888AA
            label.set_style_text_color(lv.color_hex(color), 0)

    def kb_hide_row_indicator(self):
        self.row_indicator.set_style_bg_opa(lv.OPA.TRANSP, 0)
        self.row_indicator_text.set_text("")
        for label in self.slot_labels:
            label.set_style_text_color(lv.color_hex(0x8888AA), 0)

    # ---------- 锁定状态 & 按键发送 ----------
    def kb_update_lock_badges(self):
        lock_chars = {'FN': 'FN', 'SHIFT': 'SFT', 'CTL': 'CTL', 'ALT': 'ALT', 'OPT': 'OPT'}
        locked = [lock_chars[k] for k in ('FN', 'SHIFT', 'CTL', 'ALT', 'OPT') if k in self.kb_locked_keys]
        self.kb_lock_label.set_text("Lock: " + " ".join(locked) if locked else "")

    def kb_auto_release_modifiers(self):
        changed = False
        for k in ('CTL', 'SHIFT', 'ALT', 'OPT'):
            if k in self.kb_locked_keys:
                self.kb_locked_keys.remove(k)
                changed = True
        return changed

    def _get_modifier_mask(self):
        mask = 0
        for mod, code in MODIFIER_CODES.items():
            if mod in self.kb_locked_keys:
                mask |= code
        return mask

    def kb_send_key(self, key):
        _MOD_KEYS = ('CTL', 'SHIFT', 'ALT', 'OPT')

        if key == 'CAP':
            # 本地记录大写锁定状态用于显示(主机端真实状态取决于系统)
            self.caps_lock_on = not self.caps_lock_on
            self._update_caps_indicator()
            self.hid.send_key(key, self._get_modifier_mask())
            self.kb_auto_release_modifiers()
            self.kb_update_lock_badges()
            return

        if key == 'FN':
            if key in self.kb_locked_keys:
                self.kb_locked_keys.remove(key)
            else:
                self.kb_locked_keys.append(key)
            self.kb_update_lock_badges()
            return

        if key in _MOD_KEYS:
            if key in self.kb_locked_keys:
                self.kb_locked_keys.remove(key)
                self.hid.send_single_modifier(key)
            else:
                self.kb_locked_keys.append(key)
            self.kb_update_lock_badges()
            return

        if key in COMBO_KEYS:
            self.hid.send_combo(key, self._get_modifier_mask())
            self.kb_auto_release_modifiers()
            self.kb_update_lock_badges()
            return

        self.hid.send_key(key, self._get_modifier_mask())
        self.kb_auto_release_modifiers()
        self.kb_update_lock_badges()

    # ---------- 触摸处理 ----------
    def update_touch(self, indev):
        state = indev.get_state()

        if state == indev.PRESSED:
            point = lv.point_t()
            indev.get_point(point)
            x = point.x
            y = point.y

            if self.kb_state == 0:
                if x >= self.drag_x:
                    self.kb_state = 1
                    self.kb_row = _row_from_y(y, self.height)
                    self.kb_selected_row = self.kb_row
                    self.kb_selected_col = -1
                    self.kb_update_row_indicator(self.kb_row)
                    self.kb_show_row_preview(self.kb_row)

            elif self.kb_state == 1:
                if x >= self.drag_x:
                    new_row = _row_from_y(y, self.height)
                    if new_row != self.kb_row:
                        self.kb_row = new_row
                        self.kb_selected_row = self.kb_row
                        self.kb_update_row_indicator(self.kb_row)
                        self.kb_show_row_preview(self.kb_row)
                else:
                    self.kb_state = 2
                    self.kb_hide_row_indicator()
                    col = _col_from_x(x, self.preview_x, self.preview_width, GROUP_SIZE)
                    self.kb_col = col
                    self.kb_vert_bias = 'top' if y < self.kb_vert_mid else 'bottom'
                    self.kb_prev_touch_y = y
                    self.kb_highlight_col(col, self.kb_vert_bias)

            elif self.kb_state == 2:
                if x >= self.drag_x:
                    self.kb_state = 1
                    self.hide_keyboard_preview()
                    self.kb_row = _row_from_y(y, self.height)
                    self.kb_selected_row = self.kb_row
                    self.kb_selected_col = -1
                    self.kb_update_row_indicator(self.kb_row)
                    self.kb_show_row_preview(self.kb_row)
                else:
                    col = _col_from_x(x, self.preview_x, self.preview_width, GROUP_SIZE)
                    dy = y - self.kb_prev_touch_y
                    if abs(dy) > self.kb_dy_threshold:
                        self.kb_vert_bias = 'top' if dy < 0 else 'bottom'
                    self.kb_prev_touch_y = y
                    if col != self.kb_col:
                        self.kb_col = col
                        self.kb_highlight_col(col, self.kb_vert_bias)

        else:
            if self.kb_state == 2:
                if self.kb_col >= 0 and self.kb_selected_row < 4:
                    row_keys = self.kb_current_map[self.kb_selected_row]
                    idx = self.kb_col if self.kb_vert_bias == 'top' else self.kb_col + GROUP_SIZE
                    if idx < len(row_keys):
                        key = row_keys[idx]
                        self.kb_send_key(key)

            self.hide_keyboard_preview()
            self.kb_hide_row_indicator()
            self.kb_state = 0
            self.kb_col = -1
            self.kb_selected_col = -1

    # ---------- 生命周期 ----------
    def destroy(self):
        self.container.delete()
