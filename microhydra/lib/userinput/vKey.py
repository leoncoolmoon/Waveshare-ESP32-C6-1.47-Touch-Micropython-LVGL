"""
vKey.py

三层架构里的最上层：把 _touch.py 提供的原始触摸坐标，转换成键盘按键序列。
不碰 _touch.py / userinput.py 的现有接口——本模块只暴露一个 update() 方法，
输入是 touch.get_current_points() 的返回值，输出是一个 keylist（跟物理键盘
get_new_keys() 返回值同一种约定：['q']、['ENT']、['CTL', 'a'] 这样的字符串
列表），供 userinput.py 里的 get_new_keys() 直接拼接使用。
"""

import lvgl as lv

try:
    import time
    time.ticks_ms
    time.ticks_diff
except (ImportError, AttributeError):
    try:
        import utime as time
    except ImportError:
        time = None

try:
    import machine
except ImportError:
    machine = None


def _ticks_ms():
    if time is not None and hasattr(time, 'ticks_ms'):
        return time.ticks_ms()
    if time is not None:
        return int(time.time() * 1000)
    return 0


def _ticks_diff(a, b):
    if time is not None and hasattr(time, 'ticks_diff'):
        return time.ticks_diff(a, b)
    return a - b


KEYMAP = [
    ['`', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', '=', 'BSPC'],
    ['TAB', 'q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p', '[', ']', '\\'],
    ['FN', 'SHIFT', 'a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', ';', "'", 'ENT'],
    ['CTL', 'OPT', 'ALT', 'z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.', '/', 'SPC'],
]

KEYMAP_SHIFT = [
    ['~', '!', '@', '#', '$', '%', '^', '&', '*', '(', ')', '_', '+', 'BSPC'],
    ['TAB', 'Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P', '{', '}', '|'],
    ['FN', 'SHIFT', 'A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L', ':', '"', 'ENT'],
    ['CTL', 'OPT', 'ALT', 'Z', 'X', 'C', 'V', 'B', 'N', 'M', '<', '>', '?', 'SPC'],
]

KEYMAP_FN = [
    ['ESC', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', '_', '=', 'DEL'],
    ['TAB', 'q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p', '[', ']', '\\'],
    ['FN', 'SHIFT', 'a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'UP', "'", 'ENT'],
    ['CTL', 'OPT', 'ALT', 'z', 'x', 'c', 'v', 'b', 'n', 'm', 'LEFT', 'DOWN', 'RIGHT', 'SPC'],
]

for _name, _map in (('KEYMAP', KEYMAP), ('KEYMAP_SHIFT', KEYMAP_SHIFT), ('KEYMAP_FN', KEYMAP_FN)):
    if len(_map) != 4 or any(len(_row) != 14 for _row in _map):
        raise ValueError('%s 必须是 4 行 x 14 列' % _name)
del _name, _map


def _pick_font(*names):
    tried = []
    for name in names:
        tried.append(name)
        font = getattr(lv, name, None)
        if font is not None:
            return font
    raise AttributeError(
        '没有找到可用字体，试过: %s。' % ', '.join(tried)
    )


_CHARMAP_KEYS = ('FN', 'SHIFT')
_MOD_KEYS = ('CTL', 'ALT', 'OPT')
_LOCK_BADGE_CHAR = {'FN': 'F', 'SHIFT': 'S', 'CTL': 'C', 'ALT': 'A', 'OPT': 'O'}

_SWIPE_TO_KEY = {'RIGHT': 'LEFT', 'LEFT': 'RIGHT', 'UP': 'UP', 'DOWN': 'DOWN'}

_ST_IDLE = 0
_ST_ARMED = 1
_ST_TRACKING = 2
_ST_CANCELLED = 3
_ST_CANVAS = 4
_ST_ESC = 5


def _set_label_clip(label, letter_space=-4):
    try:
        label.set_long_mode(lv.label.LONG_MODE.CLIP)
    except:
        try:
            label.set_long_mode(lv.LABEL_LONG.CLIP)
        except:
            mode_enum = getattr(lv, 'LABEL_LONG_MODE', None) or getattr(lv, 'LABEL_LONG', None)
            if mode_enum:
                mode = getattr(mode_enum, 'CLIP', None) or getattr(mode_enum, 'WRAP', None)
                if mode:
                    try:
                        label.set_long_mode(mode)
                    except Exception:
                        pass
    try:
        label.set_style_text_letter_space(letter_space, 0)
    except Exception:
        pass


def _is_multichar_key(text):
    return len(text) > 1


def _get_display_text(key):
    special_map = {
        'BSPC': 'BS', 'TAB': 'TA', 'ENT': 'ET', 'SHIFT': 'SF', 'ESC': 'ES',
        'SPC': 'SP', 'CTL': 'CT', 'ALT': 'AT', 'OPT': 'OP', 'FN': 'FN',
        'DEL': 'DL', 'UP': 'Up', 'DOWN': 'Dn', 'LEFT': 'Lf', 'RIGHT': 'Ri',
        'CAPS': 'CP',
    }
    if key in special_map:
        return special_map[key]
    if key.startswith('F') and len(key) > 1 and key[1:].isdigit():
        num = int(key[1:])
        if num == 10:
            return 'F0'
        return key
    if _is_multichar_key(key):
        return key[0]
    return key


def _is_function_key(key):
    function_keys = {
        'BSPC', 'TAB', 'ENT', 'SHIFT', 'ESC', 'SPC',
        'CTL', 'ALT', 'OPT', 'FN', 'DEL',
        'UP', 'DOWN', 'LEFT', 'RIGHT',
        'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10'
    }
    return key in function_keys


class VKey:
    """手势识别 + 键盘映射 + LVGL 实时预览。逐帧调用 update()。"""

    def __init__(
            self,
            *,
            scrn,
            screen_width,
            screen_height,
            content_x,
            content_y,
            content_width,
            content_height,
            swipe_move_thresh=20,
            touch_time_thresh=200,
            preview_font=None,
            badge_font=None,
            row_preview_font=None,
            row_preview_small_font=None,
            debug=False,
            locked_keys=None,
            g0_pin=9):
        self.debug = debug
        self.scrn = scrn
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.content_x = content_x
        self.content_y = content_y
        self.content_width = content_width
        self.content_height = content_height
        self.swipe_move_thresh = swipe_move_thresh
        self.touch_time_thresh = touch_time_thresh
        self.left_margin_width = content_x
        self.right_margin_width = screen_width - content_x - content_width
        self.badge_zone_height = screen_height - content_height
        self.gap_y = 0 if content_y > 0 else content_height

        self._state = _ST_IDLE
        self._press_x = 0
        self._press_y = 0
        self._press_time = 0
        self._last_x = 0
        self._last_y = 0
        self._row = 0
        self._col = 0

        self._locked_keys = locked_keys if locked_keys is not None else []

        self.key_state = []
        self.key_state_record = []
        self.G0 = machine.Pin(g0_pin, machine.Pin.IN, machine.Pin.PULL_UP) if g0_pin is not None else None
        #print (self.G0.value())
        self._build_widgets(preview_font, badge_font, row_preview_font, row_preview_small_font)

    def _build_widgets(self, preview_font, badge_font, row_preview_font, row_preview_small_font):
        preview_font = preview_font or _pick_font('font_montserrat_16')
        badge_font = badge_font or _pick_font('font_montserrat_14')

        self._row_preview_font = row_preview_font or _pick_font('font_montserrat_12')

        self._preview_left = self._make_label(
            0, self.content_y, self.left_margin_width, self.content_height, preview_font)
        self._preview_right = self._make_label(
            self.content_x + self.content_width, self.content_y,
            self.right_margin_width, self.content_height, preview_font)

        self._badge_left = self._make_label(
            0, self.gap_y, self.left_margin_width, self.badge_zone_height, badge_font)
        self._badge_left.set_style_text_color(lv.color_hex(0xFF0000), 0)

        self._badge_right = self._make_label(
            self.content_x + self.content_width, self.gap_y,
            self.right_margin_width, self.badge_zone_height, badge_font)
        self._badge_right.set_style_text_color(lv.color_hex(0xFF0000), 0)

        self._row_preview_container = lv.obj(self.scrn)
        self._row_preview_container.set_pos(self.content_x, self.gap_y)
        self._row_preview_container.set_size(self.content_width, self.badge_zone_height)
        self._row_preview_container.set_style_bg_color(lv.color_hex(0x000000), 0)
        self._row_preview_container.set_style_bg_opa(lv.OPA.COVER, 0)
        self._row_preview_container.set_style_border_width(0, 0)

        self._row_preview_container.set_style_pad_all(0, 0)
        self._row_preview_container.set_style_pad_top(0, 0)
        self._row_preview_container.set_style_pad_bottom(0, 0)
        self._row_preview_container.set_style_pad_left(0, 0)
        self._row_preview_container.set_style_pad_right(0, 0)

        try:
            self._row_preview_container.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        except:
            try:
                self._row_preview_container.set_scrollbar_mode(lv.SCROLLBAR.OFF)
            except:
                pass
        self._row_cells = []
        cell_width = self.content_width // 14
        cell_height = self.badge_zone_height
        for i in range(14):
            cell = lv.label(self._row_preview_container)
            cell.set_pos(i * cell_width, 0)
            cell.set_size(cell_width, cell_height)
            cell.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)

            cell.set_style_pad_all(0, 0)
            cell.set_style_pad_top(0, 0)
            cell.set_style_pad_bottom(0, 0)
            cell.set_style_pad_left(0, 0)
            cell.set_style_pad_right(0, 0)

            try:
                cell.set_long_mode(lv.label.LONG_MODE.CLIP)
            except:
                try:
                    cell.set_long_mode(lv.LABEL_LONG.CLIP)
                except:
                    pass

            cell.set_style_text_letter_space(-4, 0)

            cell.set_style_text_font(self._row_preview_font, 0)
            cell.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
            cell.set_text('')
            self._row_cells.append(cell)

        self._highlight_cells = []
        for i in range(14):
            hl = lv.obj(self._row_preview_container)
            hl.set_pos(i * cell_width, 0)
            hl.set_size(cell_width, cell_height)
            hl.set_style_bg_color(lv.color_hex(0x4444FF), 0)
            hl.set_style_bg_opa(lv.OPA.TRANSP, 0)
            hl.set_style_border_width(0, 0)

            hl.set_style_pad_all(0, 0)
            hl.set_style_pad_top(0, 0)
            hl.set_style_pad_bottom(0, 0)
            hl.set_style_pad_left(0, 0)
            hl.set_style_pad_right(0, 0)

            self._highlight_cells.append(hl)

        self._update_preview_widgets()
        self._update_lock_badges()
        self._hide_row_preview()

    def _make_label(self, x, y, w, h, font):
        label = lv.label(self.scrn)
        label.set_pos(x, y)
        label.set_size(w, h)
        label.set_style_text_font(font, 0)
        label.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
        _set_label_clip(label)
        label.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
        label.set_text('')
        return label

        
    def update(self, points):
       # print (self.G0.value())
        output = self._update_impl(points)
        self.key_state = ['G0'] if self.G0.value() == 0 else output
        self.add2queue(self.key_state)
        return self.key_state

    def get_pressed_keys(self, *, force_fn=False, force_shift=False):  # noqa: ARG002
        
        #print(f"key_state_record={self.key_state_record}")
        #print (self.G0.value())
        
        now = _ticks_ms()
        while self.key_state_record and _ticks_diff(now, self.key_state_record[0]['timestamp']) > 300:
            self.key_state_record.pop(0)

        if self.key_state_record:
            event = self.key_state_record.pop(0)
            #print('vKey get_pressed_keys: delivering %r (queued %dms ago)' % (event['key'], _ticks_diff(now, event['timestamp'])))
            return list(event['key'])

        return []

    def _update_impl(self, points):
        point = points[0] if points else None

        if point is None:
            if self._state == _ST_IDLE:
                return []
            output = self._handle_release()
            self._state = _ST_IDLE
            return output

        x, y = point.x, point.y

        if self._state == _ST_IDLE:
            self._press_x, self._press_y = x, y
            self._press_time = _ticks_ms()
            self._last_x, self._last_y = x, y
            zone = self._zone_for(x, y)
            if zone == 'CANVAS':
                self._state = _ST_CANVAS
            elif zone == 'ESC':
                self._state = _ST_ESC
            else:
                self._state = _ST_ARMED
                self._row = self._row_from_y(y)
                self._show_row_preview()
            if self.debug:
                print('vKey press: raw=(%d,%d) zone=%s row=%s' % (
                    x, y, zone, self._row if zone == 'MARGIN' else '-'))
            return []

        self._last_x, self._last_y = x, y

        if self._state == _ST_ARMED:
            new_row = self._row_from_y(y)
            if new_row != self._row:
                self._row = new_row
                self._show_row_preview()
                if self.debug:
                    print('vKey armed: row changed to %d (y=%d)' % (self._row, y))

            if self._x_in_canvas(x):
                self._state = _ST_TRACKING
                self._col = self._col_from_x(x)
                self._update_preview_widgets()
                self._update_row_preview_highlight()
                if self.debug:
                    print('vKey armed->tracking: raw=(%d,%d) row=%d col=%d char=%r' % (
                        x, y, self._row, self._col, self._current_char()))
            return []

        if self._state == _ST_TRACKING:
            if not self._x_in_canvas(x):
                self._state = _ST_CANCELLED
                self._update_preview_widgets()
                self._hide_row_preview()
                if self.debug:
                    print('vKey tracking->cancelled: raw=(%d,%d)' % (x, y))
            else:
                self._col = self._col_from_x(x)
                self._update_preview_widgets()
                self._update_row_preview_highlight()
                if self.debug:
                    print('vKey tracking: raw=(%d,%d) row=%d col=%d char=%r' % (
                        x, y, self._row, self._col, self._current_char()))
            return []

        return []

    def _zone_for(self, x, y):
        if self._x_in_canvas(x):
            return 'CANVAS' if self.content_y <= y < self.content_y + self.content_height else 'ESC'
        return 'MARGIN'

    def _x_in_canvas(self, x):
        return self.content_x <= x < self.content_x + self.content_width

    def _row_from_y(self, y):
        h = self.screen_height
        if y < h / 6:
            return 0
        if y < h * 3 / 6:
            return 1
        if y < h * 5 / 6:
            return 2
        return 3

    def _col_from_x(self, x):
        rel = x - self.content_x
        col = int(rel * 14 // self.content_width)
        if col < 0:
            col = 0
        elif col > 13:
            col = 13
        return col

    def add2queue(self, output=None):
        if not output:
            return
        if len(self.key_state_record) >= 4:
            # 队列上限，避免长时间没人来取导致无限堆积；只保留最近的几条
            self.key_state_record = self.key_state_record[-3:]
        self.key_state_record.append({'key': list(output), 'timestamp': _ticks_ms()})

    def _handle_release(self):
        state = self._state
        self._update_preview_widgets(force_clear=True)
        self._hide_row_preview()

        if state == _ST_TRACKING:
            output = self._emit_selected_char()
            if self.debug:
                print('vKey release: state=TRACKING row=%d col=%d -> %r' % (
                    self._row, self._col, output))
            return output

        if state == _ST_CANVAS:
            dx = self._last_x - self._press_x
            dy = self._last_y - self._press_y
            held_ms = _ticks_diff(_ticks_ms(), self._press_time)
            if abs(dx) < self.swipe_move_thresh and abs(dy) < self.swipe_move_thresh:
                if held_ms >= self.touch_time_thresh:
                    output = ['ENT']
                else:
                    output = []
            else:
                direction = self._direction(dx, dy)
                output = [_SWIPE_TO_KEY[direction]]
            if self.debug:
                print('vKey release: state=CANVAS press=(%d,%d) last=(%d,%d) dx=%d dy=%d held=%dms -> %r' % (
                    self._press_x, self._press_y, self._last_x, self._last_y, dx, dy, held_ms, output))
            return output

        if state == _ST_ESC:
            dx = self._last_x - self._press_x
            dy = self._last_y - self._press_y
            output = []
            if abs(dx) < self.swipe_move_thresh and abs(dy) < self.swipe_move_thresh:
                output = ['ESC']
            if self.debug:
                print('vKey release: state=ESC dx=%d dy=%d -> %r' % (dx, dy, output))
            return output

        if self.debug:
            print('vKey release: state=%d -> no output' % state)
        return []

    @staticmethod
    def _direction(dx, dy):
        if abs(dx) > abs(dy):
            return 'RIGHT' if dx > 0 else 'LEFT'
        return 'DOWN' if dy > 0 else 'UP'

    def get_locked_keys(self):
        return [k for k in (_CHARMAP_KEYS + _MOD_KEYS) if k in self._locked_keys]

    def _current_rows(self):
        if 'SHIFT' in self._locked_keys:
            return KEYMAP_SHIFT
        if 'FN' in self._locked_keys:
            return KEYMAP_FN
        return KEYMAP

    def _current_char(self):
        return self._current_rows()[self._row][self._col]

    def _emit_selected_char(self):
        char = self._current_char()

        if char in _CHARMAP_KEYS:
            if char in self._locked_keys:
                self._locked_keys.remove(char)
            else:
                other = 'SHIFT' if char == 'FN' else 'FN'
                if other in self._locked_keys:
                    self._locked_keys.remove(other)
                self._locked_keys.append(char)
            self._update_lock_badges()
            return []

        if char in _MOD_KEYS:
            if char in self._locked_keys:
                self._locked_keys.remove(char)
            else:
                self._locked_keys.append(char)
            self._update_lock_badges()
            return []

        active_mods = [k for k in _MOD_KEYS if k in self._locked_keys]
        output = active_mods + [char]
        if active_mods:
            for k in active_mods:
                self._locked_keys.remove(k)
            self._update_lock_badges()
        return output

    def _show_row_preview(self):
        row_keys = self._current_rows()[self._row]

        for i, key in enumerate(row_keys):
            if i < len(self._row_cells):
                display_text = _get_display_text(key)
                self._row_cells[i].set_text(display_text)

                is_func = _is_function_key(key)

                if is_func:
                    self._row_cells[i].set_style_text_color(lv.color_hex(0xFFA500), 0)
                else:
                    self._row_cells[i].set_style_text_font(self._row_preview_font, 0)
                    self._row_cells[i].set_style_text_color(lv.color_hex(0xFFFFFF), 0)

        self._row_preview_container.set_style_bg_color(lv.color_hex(0x222222), 0)
        self._row_preview_container.set_style_bg_opa(lv.OPA.COVER, 0)

        self._clear_highlights()

    def _hide_row_preview(self):
        for cell in self._row_cells:
            cell.set_text('')

        self._row_preview_container.set_style_bg_color(lv.color_hex(0x000000), 0)
        self._row_preview_container.set_style_bg_opa(lv.OPA.COVER, 0)
        self._clear_highlights()

    def _update_row_preview_highlight(self):
        if self._state != _ST_TRACKING:
            return

        self._clear_highlights()

        if 0 <= self._col < len(self._highlight_cells):
            hl = self._highlight_cells[self._col]
            hl.set_style_bg_opa(lv.OPA._20, 0)
            hl.set_style_bg_color(lv.color_hex(0x0000FF), 0)
            hl.move_foreground()

    def _clear_highlights(self):
        for hl in self._highlight_cells:
            hl.set_style_bg_opa(lv.OPA.TRANSP, 0)

    def _update_preview_widgets(self, force_clear=False):
        if not force_clear and self._state == _ST_TRACKING:
            text = self._current_char()
        else:
            text = ''
        self._preview_left.set_text(text)
        self._preview_right.set_text(text)

    def _update_lock_badges(self):
        chars = [_LOCK_BADGE_CHAR[k] for k in (_CHARMAP_KEYS + _MOD_KEYS) if k in self._locked_keys]
        text = ''.join(chars)
        self._badge_left.set_text(text)
        self._badge_right.set_text(text)