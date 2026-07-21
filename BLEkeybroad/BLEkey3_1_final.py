"""
ble_vkeyboard.py

Bluetooth virtual keyboard - touch to select keys and send via BLE HID

交互逻辑（改进版）：
1. 在屏幕右侧竖条（drag area）上垂直拖动 -> 选择行（4行，state=1）。
2. 手指移入左侧预览区（state=2）后：
   - 水平位置决定"列组内序号"（0-6，共7列，比原来14列宽了一倍，更容易点准）。
   - 每一行的14个键被拆成两组，分别常驻显示在：
       - 屏幕上半部分（top strip）：该行的前7个键
       - 屏幕下半部分顶端（bottom strip）：该行的后7个键
   - 手指在预览区内可以随意上下移动，代码持续跟踪"最近一段"垂直运动方向
     （每次 PRESSING 事件都会用当前点和上一个点的位移更新方向），
     并据此高亮 top 或 bottom 中对应列的那个键，给出实时反馈。
   - 抬起手指（RELEASED）时，直接使用抬起前最后一次记录到的方向作为最终判定
     （相当于"捕捉最后一段运动轨迹"），从 top 或 bottom 组里取出对应的键发送。

这样做的好处：
- 原来一行14个键挤在一条50px高的预览条里，最右侧的键触控宽度只有20px左右，
  很难点准；现在每组只有7个键，宽度翻倍，明显更好点。
- 用"方向"而不是"精确的垂直坐标"来判定上/下，用户不需要在很窄的目标区域里
  精确停留，只需要一个明确的"往上带一下"或"往下带一下"的手势，容错率更高。
"""

import lcd_bus
import machine
import jd9853
import axs5106
import lvgl as lv
from i2c import I2C
import utime as time
from myapp.hid_services import Keyboard

# ============================================================
# Keyboard arrays
# ============================================================
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

# FN 层：把原来重复的26个字母换成常用符号/组合键/导航键，提高利用率

KEYMAP_FN = [
    ['ESC', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6',
     'F7', 'F8', 'F9', 'F10', '_', '=', 'BSPC'],
    
    ['TAB', 'CAP', '!', '@', '#', '$', '%',
     '^', '&', '*', '(', ')', '[', ']'],
    
    ['FN', 'SHIFT', '~', 'AT', 'CT', '\\', '|',
     "'",  'Udo', 'Cut', 'Copy','Paste', 'PtS', 'ENT'],
    
    ['CTL', 'OPT', 'ALT', 'PGUP', 'UP', 'PGDN', 'INS',
     'DEL', 'HOME', 'END', 'LEFT', 'DOWN', 'RIGHT', 'SPC'],
]

# 每行按 7+7 拆成 top / bottom 两组
GROUP_SIZE = 7

# Modifier key codes (左侧修饰键；OPT 对应 GUI 键，Windows 上是 Win 键，
# 未来接 Mac 也可以复用这一位当 Command 键)
MODIFIER_CODES = {
    'CTL': 0x01,   # Left Control
    'SHIFT': 0x02, # Left Shift
    'ALT': 0x04,   # Left Alt
    'OPT': 0x08,   # Left GUI (Win/Command)
}

# 组合键：一次点击 = 同时按下 modifier + 目标键，再一起释放。
# target 可以是 HID_KEYCODES 里的键名，也可以是单个字符（走 _char_to_hid）。
COMBO_KEYS = {
    'AT': (0x04, 'TAB'),   # Alt + Tab：切换窗口
    'CT': (0x01, 'TAB'),   # Ctrl + Tab：切换标签页
    'Undo': (0x01, 'z'),   # Ctrl + Z
    'Cut': (0x01, 'x'),    # Ctrl + X
    'Copy': (0x01, 'c'),   # Ctrl + C
    'Paste': (0x01, 'v'),  # Ctrl + V
}

# 改成 False 可以整体退回纯文字缩写，不用图标
USE_SYMBOL_ICONS = True

def _resolve_symbol(name):
    """
    按符号名字（比如 'BACKSPACE'）去 lv 模块里找对应的图标字符串。
    不同版本的 lvgl python binding 暴露 SYMBOL 的方式不完全一样：
    有的是命名空间风格 lv.SYMBOL.BACKSPACE（和本文件里其它枚举比如
    lv.EVENT.PRESSED 是同一套风格），有的是打平的 lv.SYMBOL_BACKSPACE
    （对应 C 宏 LV_SYMBOL_BACKSPACE）。两种写法后缀是同一个名字，
    没必要各写一份完整字符串，这里按名字现拼两种路径试一下；
    都取不到就返回 None，调用方自动退回文字缩写。
    """
    ns = getattr(lv, 'SYMBOL', None)
    if ns is not None:
        sym = getattr(ns, name, None)
        if sym is not None:
            return sym
    return getattr(lv, 'SYMBOL_' + name, None)

# 每个键只在这一张表里维护一次：(文字缩写兜底, 可选的图标名字)。
# 图标名字取不到时（binding 没有这个符号 / USE_SYMBOL_ICONS=False）自动用文字缩写。
# 没有贴切图标的键（TAB/SHIFT/CTL/ALT/OPT/SPC/CAP/END/PGUP/PGDN/INS）
# 图标名字留 None，直接用文字。
# 两个不是百分百精确的取舍，可以按需要把图标名字改成 None 退回文字：
# - Undo 用"循环刷新箭头"图标近似代表撤销
# - PtSc(截屏) 用"图片"图标近似代表
#
# 注意：FN / AT / CT 这三个键的显示内容本来就等于键名本身（长度<=2），
# 不值得为它们单独占一个表项，由 _get_display_text 里的兜底分支直接处理。
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
    """把 KEY_DISPLAY 展开成最终 {key: 实际要显示的文本/图标字符串}"""
    result = {}
    for key, (text, symbol_name) in KEY_DISPLAY.items():
        symbol = _resolve_symbol(symbol_name) if (USE_SYMBOL_ICONS and symbol_name) else None
        result[key] = symbol if symbol is not None else text
    return result

DISPLAY_MAP = _build_display_map()

# ============================================================
# Helper functions
# ============================================================

def _get_display_text(key):
    """
    Get display text for a key（先查表拿图标/缩写；查不到时用零建表成本的
    规则直接兜底，不为这些键额外占字典空间）
    """
    if key in DISPLAY_MAP:
        return DISPLAY_MAP[key]
    # 键名本身长度<=2时，键名字面量就是想要的显示内容
    # （FN/AT/CT 这类组合键、F1~F9 都落在这里），直接返回，不用查表
    if len(key) <= 2:
        return key
    # 只有 F10（长度3）需要特殊处理成 'F0' 以对齐两字符宽度
    if key[0] == 'F' and key[1:].isdigit():
        return 'F0'
    return key[0]

def _is_function_key(key):
    """Check if key is a function key"""
    function_keys = {
        'BSPC', 'TAB', 'ENT', 'SHIFT', 'ESC', 'SPC',
        'CTL', 'ALT', 'OPT', 'FN', 'DEL', 'CAP',
        'UP', 'DOWN', 'LEFT', 'RIGHT',
        'HOME', 'END', 'PGUP', 'PGDN', 'INS', 'PtSc',
        'AT', 'CT', 'Undo', 'Cut', 'Copy', 'Paste',
        'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10'
    }
    return key in function_keys

def _pick_font(*names):
    """Try fonts in order, return first available"""
    for name in names:
        font = getattr(lv, name, None)
        if font is not None:
            return font
    return None

def _row_from_y(y):
    """Get row (0-3) from y coordinate (基于整块右侧拖动条的高度)"""
    h = 172
    if y < h * 0.25:
        return 0
    elif y < h * 0.5:
        return 1
    elif y < h * 0.75:
        return 2
    else:
        return 3

def _col_from_x(x, preview_x=5, preview_width=280, total_cols=GROUP_SIZE):
    """Get column (0..total_cols-1) from x coordinate within the preview area"""
    rel = x - preview_x
    if rel < 0:
        return 0
    col = int(rel * total_cols // preview_width)
    if col < 0:
        col = 0
    elif col > total_cols - 1:
        col = total_cols - 1
    return col

# ============================================================
# BLE Virtual Keyboard Class
# ============================================================

class BLEVKeyDrag:
    """Drag-based virtual keyboard with BLE output"""

    def __init__(self, scrn, locked_keys=None, keyboard_name="BLE Keyboard"):
        self.scrn = scrn
        self._locked_keys = locked_keys if locked_keys is not None else []

        self.screen_width = 320
        self.screen_height = 172

        self.drag_x = 290
        self.drag_width = 30

        # 预览区宽度（横向），x 起点
        self.preview_x = 5
        self.preview_width = 280

        # 上/下两条预览带的位置
        self.preview_top_y = 15
        self.preview_bottom_y = 95
        self.preview_strip_height = 45

        self.group_size = GROUP_SIZE
        self.cell_width = self.preview_width // self.group_size

        # 屏幕垂直中线，用于进入预览区那一刻的初始上/下判定
        self._vert_mid = self.screen_height // 2

        # 判定"最后一段滑动方向"时的抖动阈值（像素）
        self._dy_threshold = 2

        # State: 0=idle, 1=selecting row (right strip), 2=selecting key (preview area)
        self._state = 0
        self._row = 0
        self._col = -1          # 0..group_size-1，组内列号
        self._selected_row = 0
        self._selected_col = -1
        self._vert_bias = 'top'  # 'top' or 'bottom'，当前判定输出上半组还是下半组
        self._prev_touch_y = 0
        self._current_map = KEYMAP

        # BLE keyboard - initialize before UI to show status
        self.ble_keyboard = None
        self._ble_connected = False
        self._ble_initialized = False
        self._init_ble(keyboard_name)

        # UI fonts
        self._preview_font = _pick_font('font_montserrat_16', 'font_montserrat_12')
        self._badge_font = _pick_font('font_montserrat_12', 'font_montserrat_16')
        self._drag_font = _pick_font('font_montserrat_16', 'font_montserrat_12')
        self._big_font = _pick_font('font_montserrat_16', 'font_montserrat_12')

        self._build_ui()
        self._register_touch_events()
        self._update_lock_badges()
        self._update_connection_status()

    def _init_ble(self, name):
        """Initialize BLE keyboard"""
        try:
            self.ble_keyboard = Keyboard(name)
            self.ble_keyboard.set_state_change_callback(self._ble_state_callback)
            self.ble_keyboard.start()
            self._ble_initialized = True
            print("BLE Keyboard initialized as:", name)

            # Start advertising after a short delay
            time.sleep_ms(500)
            if self.ble_keyboard:
                self.ble_keyboard.start_advertising()
                print("BLE Keyboard advertising...")
        except Exception as e:
            print("BLE initialization error:", e)
            self._ble_initialized = False

    def _ble_state_callback(self):
        """Handle BLE state changes"""
        try:
            if not self.ble_keyboard:
                return

            state = self.ble_keyboard.get_state()
            if state == Keyboard.DEVICE_CONNECTED:
                self._ble_connected = True
                print("BLE Connected!")
            elif state == Keyboard.DEVICE_IDLE:
                self._ble_connected = False
                print("BLE Disconnected - re-advertising...")
                if self.ble_keyboard:
                    try:
                        self.ble_keyboard.start_advertising()
                    except:
                        pass
            elif state == Keyboard.DEVICE_ADVERTISING:
                self._ble_connected = False
                print("BLE Advertising...")
            self._update_connection_status()
        except Exception as e:
            print("BLE callback error:", e)

    def _update_connection_status(self):
        """Update connection status display"""
        try:
            if hasattr(self, '_status_label'):
                if self._ble_connected:
                    self._status_label.set_style_text_color(lv.color_hex(0x44FF88), 0)
                    self._status_label.set_text("BLE: Connected")
                elif self._ble_initialized:
                    self._status_label.set_style_text_color(lv.color_hex(0xFFAA44), 0)
                    self._status_label.set_text("BLE: Advertising...")
                else:
                    self._status_label.set_style_text_color(lv.color_hex(0xFF4444), 0)
                    self._status_label.set_text("BLE: Error")
        except:
            pass

    def _char_to_hid(self, char):
        """Convert character to HID key code - handles both cases"""
        # Letters - preserve case, SHIFT modifier will handle case
        if 'a' <= char <= 'z':
            return 0x04 + ord(char) - ord('a')
        elif 'A' <= char <= 'Z':
            return 0x04 + ord(char) - ord('A')
        # Numbers
        elif '1' <= char <= '9':
            return 0x1E + ord(char) - ord('1')
        elif char == '0':
            return 0x27
        # Symbols - return base key code, SHIFT will handle symbol
        elif char == ' ':
            return 0x2C
        elif char == '!':
            return 0x1E  # 1 key
        elif char == '@':
            return 0x1F  # 2 key
        elif char == '#':
            return 0x20  # 3 key
        elif char == '$':
            return 0x21  # 4 key
        elif char == '%':
            return 0x22  # 5 key
        elif char == '^':
            return 0x23  # 6 key
        elif char == '&':
            return 0x24  # 7 key
        elif char == '*':
            return 0x25  # 8 key
        elif char == '(':
            return 0x26  # 9 key
        elif char == ')':
            return 0x27  # 0 key
        elif char == '_':
            return 0x2D  # - key
        elif char == '+':
            return 0x2E  # = key
        elif char == '{':
            return 0x2F  # [ key
        elif char == '}':
            return 0x30  # ] key
        elif char == '|':
            return 0x31  # \ key
        elif char == ':':
            return 0x33  # ; key
        elif char == '"':
            return 0x34  # ' key
        elif char == '<':
            return 0x36  # , key
        elif char == '>':
            return 0x37  # . key
        elif char == '?':
            return 0x38  # / key
        elif char == '~':
            return 0x35  # ` key
        elif char == '`':
            return 0x35
        elif char == '-':
            return 0x2D
        elif char == '=':
            return 0x2E
        elif char == '[':
            return 0x2F
        elif char == ']':
            return 0x30
        elif char == '\\':
            return 0x31
        elif char == ';':
            return 0x33
        elif char == "'":
            return 0x34
        elif char == ',':
            return 0x36
        elif char == '.':
            return 0x37
        elif char == '/':
            return 0x38
        return None

    def _needs_shift(self, char):
        """Check if character needs SHIFT modifier"""
        if 'A' <= char <= 'Z':
            return True
        if char in '!@#$%^&*()_+{}|:"<>?~':
            return True
        return False

    def _send_hid_key(self, key, modifiers=0):
        """Send a key via BLE HID"""
        if not self._ble_connected or not self.ble_keyboard:
            print("BLE not connected, key not sent:", key)
            return False

        try:
            # Check if it's a character key
            if len(key) == 1:
                code = self._char_to_hid(key)
                if code is None:
                    return False

                # Add SHIFT if needed for uppercase/symbols
                final_modifiers = modifiers
                if self._needs_shift(key):
                    final_modifiers |= 0x02  # Left Shift

                # Send key press
                self._apply_modifiers(final_modifiers)
                self.ble_keyboard.set_keys(code)
                self.ble_keyboard.notify_hid_report()
                time.sleep_ms(10)

                # Release key
                self._apply_modifiers(0)
                self.ble_keyboard.set_keys()
                self.ble_keyboard.notify_hid_report()
                time.sleep_ms(10)
                return True

            # Check for special keys
            elif key in HID_KEYCODES:
                code = HID_KEYCODES[key]
                self._apply_modifiers(modifiers)
                self.ble_keyboard.set_keys(code)
                self.ble_keyboard.notify_hid_report()
                time.sleep_ms(10)
                self.ble_keyboard.set_keys()
                self._apply_modifiers(0)
                self.ble_keyboard.notify_hid_report()
                time.sleep_ms(10)
                return True

            # Check for function keys
            elif key in FUNCTION_KEYCODES:
                code = FUNCTION_KEYCODES[key]
                self._apply_modifiers(modifiers)
                self.ble_keyboard.set_keys(code)
                self.ble_keyboard.notify_hid_report()
                time.sleep_ms(10)
                self.ble_keyboard.set_keys()
                self._apply_modifiers(0)
                self.ble_keyboard.notify_hid_report()
                time.sleep_ms(10)
                return True
        except Exception as e:
            print("BLE send error:", e)
            return False

        return False

    def _send_single_key(self, key):
        """Send a single key press (for modifier keys when toggled off)"""
        if not self._ble_connected or not self.ble_keyboard:
            return False

        try:
            # Get the HID code for this modifier key
            if key in MODIFIER_CODES:
                code = MODIFIER_CODES[key]
                self.ble_keyboard.set_keys()
                self._apply_modifiers(code)
                self.ble_keyboard.notify_hid_report()
                time.sleep_ms(10)
                self._apply_modifiers(0)
                self.ble_keyboard.notify_hid_report()
                time.sleep_ms(10)
                return True
            return False
        except Exception as e:
            print("Send single key error:", e)
            return False

    def _apply_modifiers(self, mask):
        """
        把内部使用的单字节 bitmask(CTL=0x01, SHIFT=0x02, ALT=0x04, WIN=0x08)
        翻译成 hid_services.Keyboard.set_modifiers() 需要的具名参数。

        重要：这个库的 set_modifiers() 不接受一个组合好的整数，而是要求
        分别传 8 个具名的 0/1 参数(right_gui/right_alt/right_shift/
        right_control/left_gui/left_alt/left_shift/left_control)。
        之前代码里直接 set_modifiers(mask) 会把 mask 当成第一个位置参数
        (right_gui)，导致 right_gui << 7 溢出一个字节被截断成 0——
        这正是之前 SHIFT 怎么发都是全零 report 的根本原因。
        """
        self.ble_keyboard.set_modifiers(
            left_control=1 if mask & 0x01 else 0,
            left_shift=1 if mask & 0x02 else 0,
            left_alt=1 if mask & 0x04 else 0,
            left_gui=1 if mask & 0x08 else 0,
        )

    def _get_modifier_mask(self):
        """Get modifier mask from locked keys"""
        mask = 0
        for mod, code in MODIFIER_CODES.items():
            if mod in self._locked_keys:
                mask |= code
        return mask

    # ============================================================
    # UI building methods
    # ============================================================
    def _build_ui(self):
        """Build all UI elements"""

        self._preview_top_container = self._build_preview_strip(self.preview_top_y)
        self._preview_bottom_container = self._build_preview_strip(self.preview_bottom_y)

        self._cells_top, self._cell_bg_top = self._build_preview_cells(self._preview_top_container)
        self._cells_bottom, self._cell_bg_bottom = self._build_preview_cells(self._preview_bottom_container)

        # ---- Drag area ----
        self._drag_container = lv.obj(self.scrn)
        self._drag_container.set_pos(self.drag_x, 0)
        self._drag_container.set_size(self.drag_width, self.screen_height)
        self._drag_container.set_style_bg_color(lv.color_hex(0x2a2a4e), 0)
        self._drag_container.set_style_bg_opa(lv.OPA.COVER, 0)
        self._drag_container.set_style_border_width(1, 0)
        self._drag_container.set_style_border_color(lv.color_hex(0x555577), 0)
        self._drag_container.set_style_radius(2, 0)
        self._drag_container.set_style_pad_all(0, 0)

        # 4 slot labels
        self._slot_labels = []
        slot_height = self.screen_height // 4
        for i in range(4):
            label = lv.label(self._drag_container)
            label.set_pos(0, i * slot_height + slot_height // 2 - 8)
            label.set_size(self.drag_width, 16)
            label.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
            label.set_style_text_font(self._drag_font, 0)
            label.set_style_text_color(lv.color_hex(0x8888AA), 0)
            label.set_style_pad_all(0, 0)
            label.set_text(str(i + 1))
            self._slot_labels.append(label)

            if i < 3:
                line = lv.obj(self._drag_container)
                line.set_pos(2, (i + 1) * slot_height)
                line.set_size(self.drag_width - 4, 1)
                line.set_style_bg_color(lv.color_hex(0x444466), 0)
                line.set_style_bg_opa(lv.OPA.COVER, 0)
                line.set_style_border_width(0, 0)

        # Row indicator
        self._row_indicator = lv.obj(self._drag_container)
        self._row_indicator.set_pos(2, 0)
        self._row_indicator.set_size(self.drag_width - 4, slot_height - 2)
        self._row_indicator.set_style_bg_color(lv.color_hex(0x4488FF), 0)
        self._row_indicator.set_style_bg_opa(lv.OPA.TRANSP, 0)
        self._row_indicator.set_style_border_width(1, 0)
        self._row_indicator.set_style_border_color(lv.color_hex(0x66AAFF), 0)
        self._row_indicator.set_style_radius(2, 0)
        self._row_indicator.set_style_pad_all(0, 0)

        self._row_indicator_text = lv.label(self._row_indicator)
        self._row_indicator_text.set_pos(0, 0)
        self._row_indicator_text.set_size(self.drag_width - 4, slot_height - 2)
        self._row_indicator_text.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
        self._row_indicator_text.set_style_text_font(self._drag_font, 0)
        self._row_indicator_text.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
        self._row_indicator_text.set_style_pad_all(0, 0)
        self._row_indicator_text.set_text("")

        # Lock status
        self._lock_label = lv.label(self.scrn)
        self._lock_label.set_pos(5, self.screen_height - 14)
        self._lock_label.set_size(150, 12)
        self._lock_label.set_style_text_font(self._badge_font, 0)
        self._lock_label.set_style_text_color(lv.color_hex(0xFF6644), 0)
        self._lock_label.set_style_pad_all(0, 0)
        self._lock_label.set_text("")

        # Status label (now shows BLE status)
        self._status_label = lv.label(self.scrn)
        self._status_label.set_pos(155, self.screen_height - 14)
        self._status_label.set_size(160, 12)
        self._status_label.set_style_text_font(self._badge_font, 0)
        self._status_label.set_style_text_color(lv.color_hex(0x66AAFF), 0)
        self._status_label.set_style_text_align(lv.TEXT_ALIGN.RIGHT, 0)
        self._status_label.set_style_pad_all(0, 0)
        self._status_label.set_text("BLE: Initializing...")

        self._hide_preview()

    def _build_preview_strip(self, y):
        """创建一条预览带的容器（上半屏或下半屏顶端）"""
        container = lv.obj(self.scrn)
        container.set_pos(self.preview_x, y)
        container.set_size(self.preview_width, self.preview_strip_height)
        container.set_style_bg_color(lv.color_hex(0x1a1a2e), 0)
        container.set_style_bg_opa(lv.OPA.COVER, 0)
        container.set_style_border_width(1, 0)
        container.set_style_border_color(lv.color_hex(0x444466), 0)
        container.set_style_radius(2, 0)
        container.set_style_pad_all(0, 0)
        try:
            container.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        except:
            try:
                container.set_scrollbar_mode(lv.SCROLLBAR.OFF)
            except:
                pass
        return container

    def _build_preview_cells(self, container):
        """在预览带容器里创建 group_size 个单元格（背景+文字）"""
        cells = []
        cell_bg = []
        for i in range(self.group_size):
            bg = lv.obj(container)
            bg.set_pos(i * self.cell_width, 0)
            bg.set_size(self.cell_width, self.preview_strip_height)
            bg.set_style_bg_color(lv.color_hex(0x333355), 0)
            bg.set_style_bg_opa(lv.OPA.TRANSP, 0)
            bg.set_style_border_width(0, 0)
            bg.set_style_radius(0, 0)
            bg.set_style_pad_all(0, 0)
            cell_bg.append(bg)

            label = lv.label(container)
            label.set_pos(i * self.cell_width, 0)
            label.set_size(self.cell_width, self.preview_strip_height)
            label.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
            label.set_style_text_font(self._preview_font, 0)
            label.set_style_text_color(lv.color_hex(0xCCCCDD), 0)
            label.set_style_pad_all(0, 0)
            try:
                label.set_long_mode(lv.label.LONG_MODE.CLIP)
            except:
                try:
                    label.set_long_mode(lv.LABEL_LONG.CLIP)
                except:
                    pass
            label.set_text('')
            cells.append(label)
        return cells, cell_bg

    def _hide_preview(self):
        """Hide both preview strips"""
        for cell in self._cells_top + self._cells_bottom:
            cell.set_text('')
        for bg in self._cell_bg_top + self._cell_bg_bottom:
            bg.set_style_bg_opa(lv.OPA.TRANSP, 0)

    def _fill_strip(self, cells, keys):
        """把一组最多 group_size 个键的文字/颜色写进对应的 cells"""
        for i, cell in enumerate(cells):
            if i < len(keys):
                key = keys[i]
                cell.set_text(_get_display_text(key))
                if _is_function_key(key):
                    cell.set_style_text_color(lv.color_hex(0xFFA500), 0)
                else:
                    cell.set_style_text_color(lv.color_hex(0xCCCCDD), 0)
                cell.set_style_text_font(self._preview_font, 0)
            else:
                cell.set_text('')

    def _show_row_preview(self, row):
        """把选中行的14个键拆成上下两组，分别显示在两条预览带里"""
        if 'FN' in self._locked_keys:
            keymap = KEYMAP_FN
        else:
            keymap = KEYMAP

        self._current_map = keymap
        row_keys = keymap[row]

        top_keys = row_keys[:self.group_size]
        bottom_keys = row_keys[self.group_size:]

        self._fill_strip(self._cells_top, top_keys)
        self._fill_strip(self._cells_bottom, bottom_keys)

        for bg in self._cell_bg_top + self._cell_bg_bottom:
            bg.set_style_bg_opa(lv.OPA.TRANSP, 0)

    def _clear_strip_highlight(self, cells, keys):
        """把某一条预览带的字体/颜色恢复成未选中状态"""
        for i, cell in enumerate(cells):
            if i < len(keys):
                cell.set_style_text_font(self._preview_font, 0)
                if _is_function_key(keys[i]):
                    cell.set_style_text_color(lv.color_hex(0xFFA500), 0)
                else:
                    cell.set_style_text_color(lv.color_hex(0xCCCCDD), 0)

    def _mark_active_cell(self, bg, cell):
        """当前会被发送的键：实心蓝底 + 放大字体"""
        bg.set_style_bg_opa(lv.OPA._30, 0)
        bg.set_style_bg_color(lv.color_hex(0x4488FF), 0)
        bg.set_style_border_width(0, 0)
        cell.set_style_text_font(self._big_font, 0)
        cell.set_style_text_color(lv.color_hex(0xFFFFFF), 0)

    def _mark_candidate_cell(self, bg, cell):
        """另一侧的候选键：只描边、字体正常，提示"滑过去会切到这个" """
        bg.set_style_bg_opa(lv.OPA.TRANSP, 0)
        bg.set_style_border_width(2, 0)
        bg.set_style_border_color(lv.color_hex(0x4488FF), 0)
        cell.set_style_text_font(self._preview_font, 0)
        cell.set_style_text_color(lv.color_hex(0x99CCFF), 0)

    def _highlight_col(self, col, bias):
        """
        高亮当前列。手指一进入横向滑动阶段后，同一列在 top / bottom
        两条预览带里都会给出提示：
          - bias 指向的那一组 = 实心高亮（当前抬手会发送这个）
          - 另一组 = 描边提示（继续上/下滑就会切换成它）
        这样用户不用先滑一下才知道另一半在哪，两个候选一直可见。
        """
        row_keys = self._current_map[self._selected_row] if self._selected_row < 4 else []
        top_keys = row_keys[:self.group_size]
        bottom_keys = row_keys[self.group_size:]

        # 先把两条带的背景/边框都清空，再重新绘制
        for bg in self._cell_bg_top + self._cell_bg_bottom:
            bg.set_style_bg_opa(lv.OPA.TRANSP, 0)
            bg.set_style_border_width(0, 0)

        self._clear_strip_highlight(self._cells_top, top_keys)
        self._clear_strip_highlight(self._cells_bottom, bottom_keys)

        if col < 0 or col >= self.group_size:
            return

        top_active = bias == 'top'

        if col < len(top_keys):
            if top_active:
                self._mark_active_cell(self._cell_bg_top[col], self._cells_top[col])
            else:
                self._mark_candidate_cell(self._cell_bg_top[col], self._cells_top[col])

        if col < len(bottom_keys):
            if top_active:
                self._mark_candidate_cell(self._cell_bg_bottom[col], self._cells_bottom[col])
            else:
                self._mark_active_cell(self._cell_bg_bottom[col], self._cells_bottom[col])

        self._selected_col = col

    def _update_row_indicator(self, row):
        """Update row indicator position"""
        slot_height = self.screen_height // 4
        y_pos = row * slot_height
        self._row_indicator.set_pos(2, y_pos + 1)
        self._row_indicator.set_size(self.drag_width - 4, slot_height - 2)
        self._row_indicator.set_style_bg_opa(lv.OPA._30, 0)
        self._row_indicator_text.set_text(str(row + 1))

        for i, label in enumerate(self._slot_labels):
            if i == row:
                label.set_style_text_color(lv.color_hex(0x66AAFF), 0)
            else:
                label.set_style_text_color(lv.color_hex(0x8888AA), 0)

    def _hide_row_indicator(self):
        """Hide row indicator"""
        self._row_indicator.set_style_bg_opa(lv.OPA.TRANSP, 0)
        self._row_indicator_text.set_text("")
        for label in self._slot_labels:
            label.set_style_text_color(lv.color_hex(0x8888AA), 0)

    def _register_touch_events(self):
        """Register touch events"""
        self._touch_btn = lv.button(self.scrn)
        self._touch_btn.set_pos(0, 0)
        self._touch_btn.set_size(self.screen_width, self.screen_height)
        self._touch_btn.set_style_bg_opa(lv.OPA.TRANSP, 0)
        self._touch_btn.set_style_border_width(0, 0)
        self._touch_btn.set_style_pad_all(0, 0)
        self._touch_btn.add_event_cb(self._on_btn_event, lv.EVENT.ALL, None)

    def _on_btn_event(self, e):
        """Button event callback"""
        code = e.get_code()

        indev = lv.indev_active()
        if indev is None:
            return

        point = lv.point_t()
        indev.get_point(point)
        x = point.x
        y = point.y

        if code == lv.EVENT.PRESSED:
            self._handle_touch_start(x, y)
        elif code == lv.EVENT.PRESSING:
            self._handle_touch_move(x, y)
        elif code == lv.EVENT.RELEASED:
            self._handle_touch_release(x, y)

    def _handle_touch_start(self, x, y):
        """Touch start - begin row selection (仍然只能从右侧拖动条开始)"""
        if self._state == 0:
            if x >= self.drag_x:
                self._state = 1
                self._row = _row_from_y(y)
                self._selected_row = self._row
                self._selected_col = -1
                self._update_row_indicator(self._row)
                self._show_row_preview(self._row)

    def _handle_touch_move(self, x, y):
        """Touch move - update selection"""
        if self._state == 1:
            if x >= self.drag_x:
                new_row = _row_from_y(y)
                if new_row != self._row:
                    self._row = new_row
                    self._selected_row = self._row
                    self._update_row_indicator(self._row)
                    self._show_row_preview(self._row)
            else:
                # 进入预览区，开始列选择 + 上/下判定
                self._state = 2
                self._hide_row_indicator()
                col = _col_from_x(x, self.preview_x, self.preview_width, self.group_size)
                self._col = col
                # 初次进入时，用当前 y 相对屏幕中线的位置给一个合理的初始判定，
                # 后续手指的上下移动会随时覆盖这个判定
                self._vert_bias = 'top' if y < self._vert_mid else 'bottom'
                self._prev_touch_y = y
                self._highlight_col(col, self._vert_bias)

        elif self._state == 2:
            if x >= self.drag_x:
                # 手指回到右侧，退回行选择状态
                self._state = 1
                self._hide_preview()
                self._row = _row_from_y(y)
                self._selected_row = self._row
                self._selected_col = -1
                self._update_row_indicator(self._row)
                self._show_row_preview(self._row)
            else:
                col = _col_from_x(x, self.preview_x, self.preview_width, self.group_size)

                # 用当前点与上一个点的位移，持续更新"最近一段"的滑动方向。
                # 只有超过抖动阈值才更新，抬起前的最后一次更新即为最终判定。
                dy = y - self._prev_touch_y
                if abs(dy) > self._dy_threshold:
                    self._vert_bias = 'top' if dy < 0 else 'bottom'
                self._prev_touch_y = y

                if col != self._col or True:
                    # 每次移动都刷新高亮，保证上/下切换时有实时视觉反馈
                    self._col = col
                    self._highlight_col(col, self._vert_bias)

    def _handle_touch_release(self, x, y):
        """Touch release - output selected key via BLE"""
        if self._state == 2:
            if self._col >= 0 and self._selected_row < 4:
                row_keys = self._current_map[self._selected_row]
                idx = self._col if self._vert_bias == 'top' else self._col + self.group_size
                if idx < len(row_keys):
                    key = row_keys[idx]
                    self._send_key(key)

        self._hide_preview()
        self._hide_row_indicator()
        self._state = 0
        self._col = -1
        self._selected_col = -1

    def _send_key(self, key):
        """Send key via BLE HID"""
        _MOD_KEYS = ('CTL', 'SHIFT', 'ALT', 'OPT')

        # Toggle FN keymap
        if key == 'FN':
            if key in self._locked_keys:
                self._locked_keys.remove(key)
            else:
                self._locked_keys.append(key)
            self._update_lock_badges()
            return

        # Toggle modifiers (CTL, SHIFT, ALT, OPT)
        if key in _MOD_KEYS:
            if key in self._locked_keys:
                # Key is locked - unlock it AND send it as a single press
                self._locked_keys.remove(key)
                self._send_modifier_key(key)
            else:
                # Key is not locked - lock it
                self._locked_keys.append(key)
            self._update_lock_badges()
            return

        # 预定义组合键（Alt+Tab / Ctrl+Tab / Ctrl+Z 等），modifier 和目标键
        # 在同一份 report 里一起按下再一起释放
        if key in COMBO_KEYS:
            success = self._send_combo_key(key)
            if success:
                print("Sent combo:", key)
                # 组合键本身带了 modifier，视为"已经用掉一次"，
                # 同样触发已锁定修饰键的自动弹回
                self._auto_release_modifiers()
            else:
                print("Failed to send combo:", key)
            self._update_lock_badges()
            return

        # Get current modifiers
        modifiers = self._get_modifier_mask()

        # Send the key
        success = self._send_hid_key(key, modifiers)

        if success:
            print("Sent:", key, "with modifiers:", hex(modifiers))
            # 需求1：锁定的修饰键一旦配合其它键发送成功，就自动弹回（解锁），
            # 不需要用户再手动点一次去解锁
            self._auto_release_modifiers()
        else:
            print("Failed to send:", key)

        self._update_lock_badges()

    def _auto_release_modifiers(self):
        """
        锁定的 CTL/SHIFT/ALT/OPT 一旦配合某个键成功发送过一次，就自动解锁弹回，
        不需要用户再手动点一次去取消锁定。
        FN 不在这里处理：FN 是切换整层键盘布局的开关，不属于"和字符一起按一次
        就该弹回"的修饰键，所以维持手动切换。
        """
        changed = False
        for k in ('CTL', 'SHIFT', 'ALT', 'OPT'):
            if k in self._locked_keys:
                self._locked_keys.remove(k)
                changed = True
        return changed

    def _send_combo_key(self, key):
        """
        发送 COMBO_KEYS 里预定义的组合键：modifier 和目标键在同一份 HID report
        里一起按下，保持一小段时间后再一起释放（类似真实键盘同时按住两个键）。
        释放后会恢复成"当前仍锁定的修饰键"状态，而不是直接清零，
        这样如果用户本来就锁着别的修饰键，不会被组合键误清掉。
        """
        if key not in COMBO_KEYS:
            return False

        if not self._ble_connected or not self.ble_keyboard:
            print("BLE not connected, combo not sent:", key)
            return False

        extra_mod, target = COMBO_KEYS[key]

        if target in HID_KEYCODES:
            code = HID_KEYCODES[target]
        else:
            code = self._char_to_hid(target)

        if code is None:
            return False

        try:
            modifiers = self._get_modifier_mask() | extra_mod
            self._apply_modifiers(modifiers)
            self.ble_keyboard.set_keys(code)
            self.ble_keyboard.notify_hid_report()
            # Alt+Tab / Ctrl+Tab 这类切换窗口/标签页的组合键，保持时间
            # 比普通按键长一点更容易被系统识别为一次完整的切换动作
            time.sleep_ms(40)

            self.ble_keyboard.set_keys()
            # 释放组合键，但保留仍然锁定的修饰键（不是直接清零）
            self._apply_modifiers(self._get_modifier_mask())
            self.ble_keyboard.notify_hid_report()
            time.sleep_ms(10)
            return True
        except Exception as e:
            print("Combo send error:", e)
            return False

    def _send_modifier_key(self, key):
        """
        Send a modifier key as a single key press (for unlocking).

        重要：set_keys() 必须先调用，set_modifiers() 必须是 notify 前的
        最后一步。之前的写法是 set_modifiers(code) -> set_keys() -> notify，
        如果库里 set_keys() 会重置整份 report（含 modifier 位），
        modifier 就会在 notify 之前被冲掉，导致发出去的是全零 report——
        这正是日志里两条 "Notify with report: (0,0,0,0,0,0,0,0)" 的原因。
        """
        if key not in MODIFIER_CODES:
            return False

        if not self._ble_connected or not self.ble_keyboard:
            print("BLE not connected, cannot send modifier:", key)
            return False

        try:
            code = MODIFIER_CODES[key]

            # Press the modifier: 先清空按键槽位，最后再设置 modifiers，
            # 确保 modifiers 是 notify 前最后被写入的状态
            self.ble_keyboard.set_keys()
            self._apply_modifiers(code)
            self.ble_keyboard.notify_hid_report()
            # 单独的 modifier 按键（比如输入法切换）通常需要比普通按键
            # 稍长一点的保持时间才会被系统识别为一次独立按键，30ms 偏短
            time.sleep_ms(60)

            # Release the modifier：同样最后一步才清 modifiers
            self.ble_keyboard.set_keys()
            self._apply_modifiers(0)
            self.ble_keyboard.notify_hid_report()
            time.sleep_ms(20)

            print("Sent modifier key:", key)
            return True
        except Exception as e:
            print("Error sending modifier key:", e)
            return False

    def _update_lock_badges(self):
        """Update lock badges"""
        lock_chars = {
            'FN': 'FN', 'SHIFT': 'SF', 'CTL': 'CL', 'ALT': 'AL', 'OPT': 'OP'
        }
        locked = [lock_chars[k] for k in ('FN', 'SHIFT', 'CTL', 'ALT', 'OPT') if k in self._locked_keys]
        if locked:
            self._lock_label.set_text("Lock: " + " ".join(locked))
        else:
            self._lock_label.set_text("")

    def update(self):
        """Update per frame - check BLE state"""
        pass

# HID key codes for special keys
HID_KEYCODES = {
    'BSPC': 0x2A,    # Keyboard Backspace
    'TAB': 0x2B,     # Keyboard Tab
    'ENT': 0x28,     # Keyboard Return/Enter
    'ESC': 0x29,     # Keyboard Escape
    'SPC': 0x2C,     # Keyboard Space
    'DEL': 0x4C,     # Keyboard Delete Forward
    'UP': 0x52,      # Keyboard Up Arrow
    'DOWN': 0x51,    # Keyboard Down Arrow
    'LEFT': 0x50,    # Keyboard Left Arrow
    'RIGHT': 0x4F,   # Keyboard Right Arrow
    'CAP': 0x39,     # Keyboard Caps Lock
    'HOME': 0x4A,    # Keyboard Home
    'END': 0x4D,     # Keyboard End
    'PGUP': 0x4B,    # Keyboard Page Up
    'PGDN': 0x4E,    # Keyboard Page Down
    'INS': 0x49,     # Keyboard Insert
    'PtSc': 0x46,    # Keyboard PrintScreen
}

# Function key codes (F1-F10)
FUNCTION_KEYCODES = {
    'F1': 0x3A, 'F2': 0x3B, 'F3': 0x3C, 'F4': 0x3D,
    'F5': 0x3E, 'F6': 0x3F, 'F7': 0x40, 'F8': 0x41,
    'F9': 0x42, 'F10': 0x43,
}

# ============================================================
# Display initialization
# ============================================================

def init_display():
    """Initialize display"""
    _WIDTH = 172
    _HEIGHT = 320
    _BL = 23
    _RST = 22
    _DC = 15
    _MOSI = 2
    _SCK = 1
    _HOST = 1
    _LCD_CS = 14
    _LCD_FREQ = 40000000
    _OFFSET_X = 0
    _OFFSET_Y = 34

    spi_bus = machine.SPI.Bus(host=_HOST, mosi=_MOSI, sck=_SCK)
    display_bus = lcd_bus.SPIBus(spi_bus=spi_bus, freq=_LCD_FREQ, dc=_DC, cs=_LCD_CS)

    display = jd9853.JD9853(
        data_bus=display_bus,
        display_width=_WIDTH,
        display_height=_HEIGHT,
        backlight_pin=_BL,
        reset_pin=_RST,
        reset_state=jd9853.STATE_LOW,
        backlight_on_state=jd9853.STATE_PWM,
        color_space=lv.COLOR_FORMAT.RGB565,
        color_byte_order=jd9853.BYTE_ORDER_BGR,
        rgb565_byte_swap=True,
        offset_x=_OFFSET_X,
        offset_y=_OFFSET_Y,
    )

    original_table = jd9853.JD9853._ORIENTATION_TABLE
    new_table = list(original_table)
    new_table[0] = 0x00
    new_table[1] = 0x60
    new_table[2] = 0x82
    new_table[3] = 0xA0
    jd9853.JD9853._ORIENTATION_TABLE = tuple(new_table)

    display.set_power(True)
    display.init()
    display.set_rotation(lv.DISPLAY_ROTATION._90)
    display.set_backlight(30)

    return display

def init_touch():
    """Initialize touch"""
    from touch_cal_data import TouchCalData

    i2c_bus = I2C.Bus(host=0, sda=18, scl=19)
    touch_i2c = I2C.Device(i2c_bus, axs5106.I2C_ADDR, axs5106.BITS)
    touch_cal = TouchCalData('touch_cal')

    indev = axs5106.AXS5106(
        touch_i2c,
        debug=False,
        startup_rotation=lv.DISPLAY_ROTATION._90,
        reset_pin=20,
        touch_cal=touch_cal
    )
    return indev

# ============================================================
# Main function
# ============================================================

def main():
    """Main program"""
    lv.init()
    display = init_display()
    touch = init_touch()

    scr = lv.screen_active()
    scr.set_style_bg_color(lv.color_hex(0x0a0a1a), 0)
    scr.set_style_pad_all(0, 0)

    # Title
    title = lv.label(scr)
    title.set_pos(5, 3)
    title.set_size(280, 12)
    title.set_style_text_font(_pick_font('font_montserrat_12', 'font_montserrat_16'), 0)
    title.set_style_text_color(lv.color_hex(0x666688), 0)
    title.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
    title.set_style_pad_all(0, 0)
    title.set_text("BLE Virtual Keyboard - Drag to type")

    # Create BLE virtual keyboard
    locked_keys = []
    vkey = BLEVKeyDrag(scr, locked_keys=locked_keys, keyboard_name="BLE Virtual KB")

    # Main loop
    time_passed = 10
    while True:
        start_time = time.ticks_ms()
        lv.tick_inc(time_passed)
        lv.task_handler()
        vkey.update()
        time.sleep_ms(5)
        end_time = time.ticks_ms()
        time_passed = time.ticks_diff(end_time, start_time)
        if time_passed < 1:
            time_passed = 5

main()