# display_core.py -- 屏幕/触摸硬件初始化 + LVGL 控件框架
from bangle_utils import GBBitmap

import lcd_bus
import machine
import gc
import jd9853
import axs5106
import lvgl as lv
from i2c import I2C
import utime as time

lv.init()

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
_display_drv = jd9853.JD9853(
    data_bus=display_bus,
    display_width=_WIDTH,
    display_height=_HEIGHT,
    backlight_pin=_BL,
    reset_pin=_RST,
    reset_state=jd9853.STATE_LOW,
    backlight_on_state=jd9853.STATE_HIGH,
    color_space=lv.COLOR_FORMAT.RGB565,
    color_byte_order=jd9853.BYTE_ORDER_BGR,
    rgb565_byte_swap=True,
    offset_x=_OFFSET_X,
    offset_y=_OFFSET_Y,
)
_display_drv.set_power(True)
_display_drv.init()

_orig_table = jd9853.JD9853._ORIENTATION_TABLE
_new_table = list(_orig_table)
_new_table[0] = 0x00
_new_table[1] = 0x60
_new_table[2] = 0x82
_new_table[3] = 0xA0
jd9853.JD9853._ORIENTATION_TABLE = tuple(_new_table)

_display_drv.set_rotation(lv.DISPLAY_ROTATION._90)
# 默认亮度 (百分比)
_DEFAULT_BRIGHTNESS = 70
_DIMMED_BRIGHTNESS = 20  # 变暗后的亮度百分比
_display_drv.set_backlight(_DEFAULT_BRIGHTNESS)

from touch_cal_data import TouchCalData

_i2c_bus = I2C.Bus(host=0, sda=18, scl=19)
_touch_i2c = I2C.Device(_i2c_bus, axs5106.I2C_ADDR, axs5106.BITS)
_touch_cal = TouchCalData('touch_cal')

_indev = axs5106.AXS5106(
    _touch_i2c,
    debug=False,
    startup_rotation=lv.DISPLAY_ROTATION._90,
    reset_pin=20,
    touch_cal=_touch_cal,
)

_SCREEN_W = (320)
_SCREEN_H = (172)

_MH_DISPLAY_HEIGHT = (_SCREEN_H)
_MH_DISPLAY_WIDTH = (_SCREEN_W)

_CHAR_WIDTH = (8)
_LINE_HEIGHT = (12)  # 稍微增加行高，提高可读性

_MAX_CHARS_PER_LINE = (_MH_DISPLAY_WIDTH // _CHAR_WIDTH)
_MAX_LINES = (_MH_DISPLAY_HEIGHT // _LINE_HEIGHT)

# 下面这些跟屏幕尺寸的比例算出来的，不再是写死的字面量，() 折不了
# 带变量的乘除表达式，所以就用普通模块级变量，访问性能差别可以忽略。
# 状态栏高度：约屏幕高的 12%
_STATUS_BAR_HEIGHT = max(14, _MH_DISPLAY_HEIGHT * 12 // 100)
# 底部指示器高度：约屏幕高的 8%
_BOTTOM_INDICATOR_HEIGHT = max(10, _MH_DISPLAY_HEIGHT * 8 // 100)
# 内容区域：DISPLAY 这块 canvas 现在只盖内容区（顶/底栏已经改成原生
# LVGL 控件，不用画布了），canvas 自己的坐标系从 0 开始，所以
# _CONTENT_Y_START 改成 0——canvas 在屏幕上的位置由下面创建 DISPLAY
# 时的 set_pos(0, _STATUS_BAR_HEIGHT) 决定，跟这里的坐标计算是两回事。
_CONTENT_Y_START = 0
_CONTENT_HEIGHT = _MH_DISPLAY_HEIGHT - _STATUS_BAR_HEIGHT - _BOTTOM_INDICATOR_HEIGHT
_CONTENT_MAX_LINES = _CONTENT_HEIGHT // _LINE_HEIGHT

# 通知卡片（标题/分隔线/内容/边框）布局
_CARD_MARGIN_X = max(2, _MH_DISPLAY_WIDTH * 1 // 100)
_CARD_PADDING = max(2, _MH_DISPLAY_WIDTH * 1 // 100)
_CARD_GAP = max(2, _MH_DISPLAY_HEIGHT * 2 // 100)

scr = lv.screen_active()
scr.set_style_bg_color(lv.color_hex(0x1a1a2e), 0)
scr.set_style_pad_all(0, 0)

scr.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

def _try_disable_scroll(obj):
    for getter in (
        lambda: lv.obj.FLAG.SCROLLABLE,
        lambda: lv.OBJ_FLAG.SCROLLABLE,
        lambda: lv.OBJ_FLAG_SCROLLABLE,
    ):
        try:
            flag = getter()
        except AttributeError:
            continue
        try:
            obj.remove_flag(flag)
            return True
        except Exception:
            continue
    return False

_SCROLL_DISABLED = _try_disable_scroll(scr)
if not _SCROLL_DISABLED:
    print("[scr] 没找到能用的 SCROLLABLE flag 路径，拖拽手势可能还是会被当成"
          "滚动吸收掉——如果时钟界面划动还是有问题，把 dir(lv.obj) 里带 "
          "FLAG 的属性名发过来")

def debug_outline_children(container=None, color=0xFF0000):
    container = container or scr
    n = container.get_child_cnt()
    for i in range(n):
        child = container.get_child(i)
        try:
            child.set_style_border_width(2, 0)
            child.set_style_border_color(lv.color_hex(color), 0)
            child.set_style_border_opa(255, 0)
        except Exception:
            pass
    print(f"[debug] 给 scr 下面 {n} 个直接子控件描红了边框")

PALETTE = [
    0x000000, 0x24243e, 0x1a1a2e, 0x2d2d4a, 0xe94560,
    0x555577, 0xaaaaee, 0x000000, 0xffffff,
]

# ---- 主题反色开关：Settings 里那个开关用的 ----
_PALETTE_BASE = list(PALETTE)
_THEME_INVERTED = [False]

def _invert_color(c):
    return (~c) & 0xFFFFFF

def toggle_theme():
    _THEME_INVERTED[0] = not _THEME_INVERTED[0]
    if _THEME_INVERTED[0]:
        PALETTE[:] = [_invert_color(c) for c in _PALETTE_BASE]
    else:
        PALETTE[:] = list(_PALETTE_BASE)
    scr.set_style_bg_color(_lc(PALETTE[2]), 0)

import framebuf

try:
    _UTF8_FONT = open("/font/utf8_8x8.bin", "rb")
except OSError:
    _UTF8_FONT = None

_ascii_glyph_buf = bytearray(8)
_ascii_glyph_fb = framebuf.FrameBuffer(_ascii_glyph_buf, 8, 8, framebuf.MONO_HLSB)

def _draw_glyph(canvas, ch, x, y, color, max_w, max_h, scale=1):
    ch_ord = ord(ch)
    if ch_ord < 128:
        _ascii_glyph_fb.fill(0)
        _ascii_glyph_fb.text(ch, 0, 0, 1)
        for row in range(8):
            for col in range(8):
                if not _ascii_glyph_fb.pixel(col, row):
                    continue
                _blit_block(canvas, x + col * scale, y + row * scale, scale, color, max_w, max_h)
        return
    if _UTF8_FONT is None:
        return
    _UTF8_FONT.seek(ch_ord * 8)
    data = _UTF8_FONT.read(8)
    if len(data) < 8:
        return
    for byte_i in range(8):
        row = 7 - byte_i  # 原逻辑里 byte0 是最底一行，这里翻回从上到下
        b = data[byte_i]
        for col in range(8):
            if not (b >> col) & 1:
                continue
            _blit_block(canvas, x + col * scale, y + row * scale, scale, color, max_w, max_h)


def _blit_block(canvas, x, y, scale, color, max_w, max_h):
    # scale=1 就是画单个像素；scale>1 把这一个点画成 scale x scale 的
    # 小方块——放大字体不用换字体文件，就是把现成的 8x8 点阵按比例描粗，
    # 不占额外内存（还是画在同一块 canvas 里），就是画的点变多了。
    if scale <= 1:
        if 0 <= x < max_w and 0 <= y < max_h:
            canvas.set_px(x, y, color, 255)
        return
    for dy in range(scale):
        py = y + dy
        if py < 0 or py >= max_h:
            continue
        for dx in range(scale):
            px = x + dx
            if px < 0 or px >= max_w:
                continue
            canvas.set_px(px, py, color, 255)

def _lc(v):
    return lv.color_hex(v)

def _alloc_buffers(tag, num, chunk_size):
    gc.collect()
    free_before = gc.mem_free()
    total = num * chunk_size
    try:
        big = bytearray(total)
        bufs = [memoryview(big)[i * chunk_size:(i + 1) * chunk_size] for i in range(num)]
        gc.collect()
        print(f"[{tag}] 整块申请成功: {num}x{chunk_size}B (合计{total}B), "
              f"mem_free {free_before} -> {gc.mem_free()}")
        return bufs, num
    except MemoryError:
        pass

    bufs = []
    for _ in range(num):
        try:
            bufs.append(bytearray(chunk_size))
        except MemoryError:
            break
    gc.collect()
    print(f"[{tag}] 整块申请失败，改成逐块申请: 拿到 {len(bufs)}/{num} 块 x {chunk_size}B, "
          f"mem_free {free_before} -> {gc.mem_free()}")
    return bufs, len(bufs)

class _RowSlot:

    def __init__(self, parent, w, h, buf=None):
        self.width = w
        self.height = h
        self.buf = buf if buf is not None else bytearray(w * h * 2)
        self.canvas = lv.canvas(parent)
        self.canvas.set_buffer(self.buf, w, h, lv.COLOR_FORMAT.RGB565)
        self.canvas.set_style_pad_all(0, 0)
        self.canvas.set_size(0, 0)  # 一开始都是隐藏状态

    def clear(self, color):
        c = _lc(color)
        try:
            self.canvas.fill_bg(c, 255)
        except AttributeError:
            for py in range(self.height):
                for px in range(self.width):
                    self.canvas.set_px(px, py, c, 255)

    def text(self, s, x, y, color, scale=1):
        if not s:
            return
        c = _lc(color)
        cx = x
        for ch in s:
            _draw_glyph(self.canvas, ch, cx, y, c, self.width, self.height, scale=scale)
            cx += 8 * scale

    def bitmap(self, bmp, x, y, key=-1):
        draw_width, draw_height, bpp = bmp.WIDTH, bmp.HEIGHT, bmp.BPP
        if draw_width <= 0 or draw_height <= 0 or bpp <= 0:
            return
        palette = bmp.PALETTE
        data = bmp.BITMAP
        data_len = len(data)
        bitmask = (1 << bpp) - 1
        w, h = self.width, self.height
        for py in range(draw_height):
            ty = y + py
            if ty < 0 or ty >= h:
                continue
            row_base = py * draw_width
            for px in range(draw_width):
                tx = x + px
                if tx < 0 or tx >= w:
                    continue
                bit_idx = (row_base + px) * bpp
                byte_idx = bit_idx // 8
                if byte_idx >= data_len:
                    continue
                bit_offset = bit_idx % 8
                bits_needed = bit_offset + bpp
                bytes_needed = 2 if bits_needed > 8 else 1
                chunk = data[byte_idx]
                if bytes_needed == 2:
                    nxt = data[byte_idx + 1] if byte_idx + 1 < data_len else 0
                    chunk = (chunk << 8) | nxt
                shift = (bytes_needed * 8) - bits_needed
                clr_idx = (chunk >> shift) & bitmask
                clr = palette[clr_idx] if clr_idx < len(palette) else 0
                if key is not None and key >= 0 and clr == key:
                    continue
                self.canvas.set_px(tx, ty, _lc(clr), 255)

    def pos(self, x, y):
        self.canvas.set_pos(x, y)

    def show(self):
        self.canvas.set_size(self.width, self.height)

    def hide(self):
        self.canvas.set_size(0, 0)

class _RectSlot:

    def __init__(self, parent):
        self.o = lv.obj(parent)
        self.o.set_style_pad_all(0, 0)
        self.o.set_style_radius(0, 0)
        self.o.set_size(0, 0)

    def show_fill(self, x, y, w, h, color):
        self.o.set_pos(x, y)
        self.o.set_size(max(1, w), max(1, h))
        self.o.set_style_bg_color(_lc(color), 0)
        self.o.set_style_bg_opa(255, 0)
        self.o.set_style_border_width(0, 0)

    def show_border(self, x, y, w, h, color):
        self.o.set_pos(x, y)
        self.o.set_size(max(1, w), max(1, h))
        self.o.set_style_bg_opa(0, 0)
        self.o.set_style_border_width(1, 0)
        self.o.set_style_border_color(_lc(color), 0)

    def hide(self):
        self.o.set_size(0, 0)

class LVDisplay:

    def __init__(self, parent, x, y, width, height, num_rows=12, num_rects=24):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self._row_h = _LINE_HEIGHT
        self._row_i = 0
        self._rect_i = 0
        self._bg = 0x000000

        row_bufs, got = _alloc_buffers("LVDisplay rows", num_rows, width * self._row_h * 2)
        if got < num_rows:
            print(f"[LVDisplay] 只申请到 {got}/{num_rows} 块行缓冲区，内存紧张，够用的界面会正常显示，内容比较多的界面可能会被裁掉一部分")

        self._rows = [_RowSlot(parent, width, self._row_h, buf=b) for b in row_bufs]

        rects = []
        for _ in range(num_rects):
            try:
                rects.append(_RectSlot(parent))
            except MemoryError:
                break
        if len(rects) < num_rects:
            print(f"[LVDisplay] 只申请到 {len(rects)}/{num_rects} 块矩形装饰，边框/分隔线可能会少画一些")
        self._rects = rects

    def _next_row(self):
        if self._row_i >= len(self._rows):
            return None
        r = self._rows[self._row_i]
        self._row_i += 1
        return r

    def _next_rect(self):
        if self._rect_i >= len(self._rects):
            return None
        r = self._rects[self._rect_i]
        self._rect_i += 1
        return r

    def fill(self, color):
        # 相当于"这一帧重新开始画" -- 把两个池子全部收回隐藏，游标归零
        self._bg = color
        for r in self._rows:
            r.hide()
        for r in self._rects:
            r.hide()
        self._row_i = 0
        self._rect_i = 0

    def rect(self, x, y, w, h, color, fill=False):
        if w <= 0 or h <= 0:
            return
        slot = self._next_rect()
        if slot is None:
            return
        if fill:
            slot.show_fill(self.x + x, self.y + y, w, h, color)
        else:
            slot.show_border(self.x + x, self.y + y, w, h, color)

    def text(self, s, x, y, color):
        if not s:
            return
        row = self._next_row()
        if row is None:
            return
        row.clear(self._bg)
        row.text(s, 0, 0, color)
        row.pos(self.x + x, self.y + y)
        row.show()

    def line(self, x1, y1, x2, y2, color):
        # 目前这个 app 只画横线/竖线，退化成一个很细的矩形，走 rect 池子
        if y1 == y2:
            self.rect(min(x1, x2), y1, abs(x2 - x1) + 1, 1, color, fill=True)
        elif x1 == x2:
            self.rect(x1, min(y1, y2), 1, abs(y2 - y1) + 1, color, fill=True)
        # 斜线目前用不到，先不处理

    def bitmap(self, bmp, x, y, key=-1):
        # 位图借一块"行"canvas 来画，高度只有一行（_LINE_HEIGHT），比这
        # 更高的位图会被裁掉上/下沿——目前手机推过来的图标都不大，暂时
        # 够用；真要支持更高的位图，可以专门再开一个更高的 slot 类型。
        row = self._next_row()
        if row is None:
            return
        row.clear(self._bg)
        row.bitmap(bmp, 0, 0, key=key)
        row.pos(self.x + x, self.y + y)
        row.show()

    def show(self):
        pass

    def set_brightness(self, level):
        # level 是旧接口的 0-10 刻度，换算成百分比
        pct = max(0, min(100, int(level) * 10))
        _display_drv.set_backlight(pct)

_TAP_MIN_MS = 200     # 按住不动超过这个时长才算一次 ENTER（防误触）
_HOLD_EXIT_MS = 1500  # 按住不动超过这个时长算长按，退出到 launcher

# ---- 背光超时控制（使用软件轮询） ----
_BACKLIGHT_TIMEOUT_MS = 8000   # 8秒无操作变暗
_BACKLIGHT_OFF_MS = 10000      # 再过2秒（总共10秒）关闭背光
_backlight_state = "full"  # "full", "dimmed", "off"
_last_touch_time = 0
_backlight_timer_running = False

def _check_backlight_timer():
    """在主循环中定期调用此函数检查背光状态"""
    global _last_touch_time, _backlight_state, _backlight_timer_running
    
    if not _backlight_timer_running:
        return
    
    now = time.ticks_ms()
    elapsed = time.ticks_diff(now, _last_touch_time)
    
    if _backlight_state == "full" and elapsed >= _BACKLIGHT_TIMEOUT_MS:
        # 变暗
        _display_drv.set_backlight(_DIMMED_BRIGHTNESS)
        _backlight_state = "dimmed"
        print("[背光] 变暗 (20%)")
    elif _backlight_state == "dimmed" and elapsed >= _BACKLIGHT_OFF_MS:
        # 关闭
        _display_drv.set_backlight(0)
        _backlight_state = "off"
        print("[背光] 已关闭")

def _reset_backlight_timer(debug=False):
    """重置背光计时器（触摸时调用）"""
    global _last_touch_time, _backlight_state, _backlight_timer_running
    
    # 如果背光已关闭或变暗，恢复到全亮
    if _backlight_state != "full":
        _display_drv.set_backlight(_DEFAULT_BRIGHTNESS)
        _backlight_state = "full"
        print("[背光] 恢复亮度")
    
    # 记录触摸时间
    _last_touch_time = time.ticks_ms()
    _backlight_timer_running = True
    if debug:
        print("[背光] 计时器重置")

# 触摸事件回调 - 用于重置背光计时器
def _on_touch_reset_backlight(e):
    # 只处理真正的触摸事件，避免 LVGL 内部事件触发
    code = e.get_code()
    if code in (lv.EVENT.PRESSED, lv.EVENT.RELEASED, lv.EVENT.GESTURE):
        _reset_backlight_timer(debug=False)  # debug=False 避免刷屏

class TouchInput:

    def __init__(self, target):
        self._keys = []
        self._press_start = 0
        self._gesture_seen = False
        
        # 只监听真正的触摸事件
        target.add_event_cb(self._on_event, lv.EVENT.PRESSED, None)
        target.add_event_cb(self._on_event, lv.EVENT.RELEASED, None)
        target.add_event_cb(self._on_event, lv.EVENT.GESTURE, None)
        
        # 背光重置也监听同样的事件
        target.add_event_cb(_on_touch_reset_backlight, lv.EVENT.PRESSED, None)
        target.add_event_cb(_on_touch_reset_backlight, lv.EVENT.RELEASED, None)
        target.add_event_cb(_on_touch_reset_backlight, lv.EVENT.GESTURE, None)

    def _on_event(self, e):
        code = e.get_code()
        if code == lv.EVENT.PRESSED:
            self._press_start = time.ticks_ms()
            self._gesture_seen = False
        elif code == lv.EVENT.GESTURE:
            self._gesture_seen = True
            indev = lv.indev_active()
            if indev is None:
                return
            d = indev.get_gesture_dir()
            if d == lv.DIR.LEFT:
                self._keys.append("RIGHT")
            elif d == lv.DIR.RIGHT:
                self._keys.append("LEFT")
            elif d == lv.DIR.TOP:
                self._keys.append("DOWN")
            elif d == lv.DIR.BOTTOM:
                self._keys.append("UP")
        elif code == lv.EVENT.RELEASED:
            if self._press_start and not self._gesture_seen:
                held = time.ticks_diff(time.ticks_ms(), self._press_start)
                if held >= _HOLD_EXIT_MS:
                    self._keys.append("G0")
                elif held >= _TAP_MIN_MS:
                    self._keys.append("ENTER")
            self._press_start = 0

    def get_new_keys(self):
        if not self._keys:
            return []
        keys, self._keys = self._keys, []
        return keys

DISPLAY = LVDisplay(scr, 0, _STATUS_BAR_HEIGHT, _MH_DISPLAY_WIDTH, _CONTENT_HEIGHT,
                     num_rows=6, num_rects=16)
time.sleep_ms(0)  # 让出一点时间片给BLE后台栈处理连接请求，下面同理

# 启动背光计时器（初始启动）
_reset_backlight_timer(debug=True)

class NotifStrip:
    # 通知列表专用的渲染逻辑——不再自己攒一份 buffer，改成直接借用
    # DISPLAY 那个通用池子里的行/矩形（DISPLAY._next_row() / DISPLAY.rect()）。
    # 之前这里自己另开一份 5 块 x 11KB 的池子，跟 DISPLAY 自己的 6 块池子
    # 是两份完全独立、同时占着的内存——但同一时刻只会显示一个界面，
    # 根本用不着两份都留着。合并成一份之后，通知列表跟其它界面（天气/
    # 状态/音乐/时钟）共用同一批常驻 canvas，总内存占用直接减半，而且
    # 也不用再纠结"哪个先申请"的问题了，只有一次分配。
    #
    # 短线（标题/正文之间）画在借来的那块 row canvas 自己最后一行像素
    # 里；长线（通知与通知之间，贯通全屏含左右留白）走 DISPLAY.rect()。

    def __init__(self, display):
        self.display = display

    def draw(self, notifications, scroll_offset, extract_title, extract_body,
             y_start, max_y, bg_color, title_color, body_color, divider_color):
        d = self.display
        n = len(notifications)
        if n == 0:
            return 0, y_start

        margin_x = 6
        content_w = max(1, _MH_DISPLAY_WIDTH - 2 * margin_x)
        max_chars = max(1, content_w // _CHAR_WIDTH)
        row_h = d._row_h
        y = y_start
        shown = 0
        i = scroll_offset

        while i < n and y + row_h <= max_y:
            entry = notifications[i]
            title_field = extract_title(entry)
            body_field = extract_body(entry)

            title_row = d._next_row()
            if title_row is None:
                break
            title_row.clear(bg_color)
            self._short_line(title_row, margin_x, content_w, divider_color)
            self._draw_one_field(title_row, title_field, title_color, margin_x, max_chars)
            title_row.pos(d.x, d.y + y)
            title_row.show()
            y += row_h

            has_body = y + row_h <= max_y
            if has_body:
                body_row = d._next_row()
                has_body = body_row is not None
                if has_body:
                    body_row.clear(bg_color)
                    self._draw_one_field(body_row, body_field, body_color, margin_x, max_chars)
                    body_row.pos(d.x, d.y + y)
                    body_row.show()
                    y += row_h

            shown += 1
            i += 1

            if has_body and i < n and y + 8 <= max_y:
                y += 3
                d.rect(0, y, _MH_DISPLAY_WIDTH, 1, divider_color, fill=True)
                y += 1 + 3

        return shown, y

    def _short_line(self, row, margin_x, content_w, color):
        c = _lc(color)
        line_y = row.height - 1
        for px in range(margin_x, margin_x + content_w):
            if px < row.width:
                row.canvas.set_px(px, line_y, c, 255)

    def _draw_one_field(self, row, field, color, margin_x, max_chars):
        if isinstance(field, dict) and field.get("pixels"):
            palette = [PALETTE[2], color]
            gb_bmp = GBBitmap(field["width"], field["height"], field["bpp"], field["pixels"], palette)
            key_idx = field.get("transparent")
            key_color = palette[key_idx] if key_idx is not None and key_idx < len(palette) else -1
            row.bitmap(gb_bmp, margin_x, 0, key=key_color)
            return
        if isinstance(field, str) and field:
            text = field if len(field) <= max_chars else field[:max(0, max_chars - 1)] + "…"
            row.text(text, margin_x, 2, color)

NOTIF_STRIP = NotifStrip(DISPLAY)
time.sleep_ms(0)

VIEW_CLOCK = "clock"
VIEW_NOTIFICATIONS = "notifications"
VIEW_MUSIC = "music"
VIEW_STATUS = "status"

class ClockDigits:

    _SEG_NAMES = ("a", "b", "c", "d", "e", "f", "g")
    _SEGMENTS = {
        0: "abcdef", 1: "bc", 2: "abged", 3: "abgcd", 4: "fgbc",
        5: "afgcd", 6: "afgedc", 7: "abc", 8: "abcdefg", 9: "abcfgd",
    }

    def __init__(self, parent):
        self.digit_objs = []
        for _ in range(4):
            segs = {}
            for name in self._SEG_NAMES:
                segs[name] = self._bare(lv.obj(parent))
            self.digit_objs.append(segs)
        self.colon_objs = [self._bare(lv.obj(parent)), self._bare(lv.obj(parent))]

    @staticmethod
    def _bare(o):
        o.set_style_pad_all(0, 0)
        o.set_style_border_width(0, 0)
        o.set_style_radius(0, 0)
        o.set_size(0, 0)
        return o

    def hide(self):
        for segs in self.digit_objs:
            for o in segs.values():
                o.set_size(0, 0)
        for o in self.colon_objs:
            o.set_size(0, 0)

    def _set_color(self, color):
        c = _lc(color)
        for segs in self.digit_objs:
            for o in segs.values():
                o.set_style_bg_color(c, 0)
                o.set_style_bg_opa(255, 0)
        for o in self.colon_objs:
            o.set_style_bg_color(c, 0)
            o.set_style_bg_opa(255, 0)

    def show_time(self, hour, minute, x, y, digit_w, digit_h, thick, gap, colon_w, color):
        self._set_color(color)
        digits = [hour // 10, hour % 10, minute // 10, minute % 10]
        seg_w = max(1, digit_w - 2 * thick)
        half_h = max(1, (digit_h - 3 * thick) // 2)
        cx = x
        for i, d in enumerate(digits):
            lit = self._SEGMENTS.get(d, "")
            geo = {
                "a": (cx + thick, y, seg_w, thick),
                "g": (cx + thick, y + thick + half_h, seg_w, thick),
                "d": (cx + thick, y + 2 * thick + 2 * half_h, seg_w, thick),
                "f": (cx, y + thick, thick, half_h),
                "b": (cx + thick + seg_w, y + thick, thick, half_h),
                "e": (cx, y + 2 * thick + half_h, thick, half_h),
                "c": (cx + thick + seg_w, y + 2 * thick + half_h, thick, half_h),
            }
            for name, o in self.digit_objs[i].items():
                if name in lit:
                    gx, gy, gw, gh = geo[name]
                    o.set_pos(gx, gy)
                    o.set_size(max(1, gw), max(1, gh))
                else:
                    o.set_size(0, 0)
            cx += digit_w + gap
            if i == 1:
                dot = max(2, colon_w // 2)
                cxc = cx + (colon_w - dot) // 2
                self.colon_objs[0].set_pos(cxc, y + digit_h // 3 - dot // 2)
                self.colon_objs[0].set_size(dot, dot)
                self.colon_objs[1].set_pos(cxc, y + 2 * digit_h // 3 - dot // 2)
                self.colon_objs[1].set_size(dot, dot)
                cx += colon_w + gap
        return cx  # 返回画完之后的 x，方便调用方知道整体宽度

CLOCK_DIGITS = ClockDigits(scr)
time.sleep_ms(0)

class TopBar:

    def __init__(self, parent, w, h):
        self.h = h
        self.bg = lv.obj(parent)
        self.bg.set_pos(0, 0)
        self.bg.set_size(w, h)
        self.bg.set_style_pad_all(0, 0)
        self.bg.set_style_border_width(0, 0)
        self.bg.set_style_radius(0, 0)

        dot_d = max(6, h // 2)
        self.dot = lv.obj(self.bg)
        self.dot.set_pos(4, (h - dot_d) // 2)
        self.dot.set_size(dot_d, dot_d)
        self.dot.set_style_radius(dot_d // 2, 0)
        self.dot.set_style_border_width(0, 0)
        self.dot.set_style_pad_all(0, 0)

        self.title = lv.label(self.bg)
        self.title.set_pos(4 + dot_d + 6, (h - 14) // 2)
        self.title.set_style_text_font(_BAR_FONT, 0)
        self.title.set_style_pad_all(0, 0)

        self.right = lv.label(self.bg)
        self.right.set_style_text_font(_BAR_FONT, 0)
        self.right.set_style_pad_all(0, 0)
        self.right.set_pos(w - 60, (h - 14) // 2)
        self.right.set_width(56)
        self.right.set_style_text_align(lv.TEXT_ALIGN.RIGHT, 0)

    def show(self):
        self.bg.set_size(_MH_DISPLAY_WIDTH, self.h)

    def hide(self):
        self.bg.set_size(0, 0)

    def update(self, connected, title, right_text, bg_color, text_color, dim_color):
        self.bg.set_style_bg_color(_lc(bg_color), 0)
        self.bg.set_style_bg_opa(255, 0)
        self.dot.set_style_bg_color(_lc(0x2ecc71 if connected else 0xe74c3c), 0)
        self.dot.set_style_bg_opa(255, 0)
        self.title.set_style_text_color(_lc(text_color), 0)
        self.title.set_text(title)
        self.right.set_style_text_color(_lc(dim_color), 0)
        self.right.set_text(right_text)

class BottomBar:
    # items 是 [(view_id, 单字符标签), ...]，核心界面先给4个，插件加载
    # 完之后 main.py 会调 rebuild() 把插件界面也加进来（这时候
    # plugin_manager 还没跑，没法一开始就知道最终有几个）。

    def __init__(self, parent, w, h, items):
        self.h = h
        self.bg = lv.obj(parent)
        self.bg.set_pos(0, _MH_DISPLAY_HEIGHT - h)
        self.bg.set_style_pad_all(0, 0)
        self.bg.set_style_border_width(0, 0)
        self.bg.set_style_radius(0, 0)
        self._items = []
        self.labels = []
        self.underlines = []
        self.rebuild(items)
        self.bg.set_size(w, h)  # rebuild() 不管 bg 自己的显示/隐藏

    def rebuild(self, items):
        for lbl in self.labels:
            try:
                lbl.delete()
            except Exception:
                pass
        for ul in self.underlines:
            try:
                ul.delete()
            except Exception:
                pass
        self._items = list(items)
        self.labels = []
        self.underlines = []

        w = _MH_DISPLAY_WIDTH
        n = max(1, len(self._items))
        slot_w = w // n
        for i, (_view, ch) in enumerate(self._items):
            lbl = lv.label(self.bg)
            lbl.set_style_text_font(_BAR_FONT, 0)
            lbl.set_style_pad_all(0, 0)
            lbl.set_text(ch)
            lbl.set_pos(i * slot_w + slot_w // 2 - 4, 1)
            self.labels.append(lbl)

            ul = lv.obj(self.bg)
            ul.set_style_pad_all(0, 0)
            ul.set_style_border_width(0, 0)
            ul.set_style_radius(0, 0)
            ul_w = max(6, slot_w // 3)
            ul.set_pos(i * slot_w + (slot_w - ul_w) // 2, self.h - 3)
            ul.set_size(ul_w, 2)
            self.underlines.append(ul)

    def show(self):
        self.bg.set_size(_MH_DISPLAY_WIDTH, self.h)

    def hide(self):
        self.bg.set_size(0, 0)

    def update(self, current_view, active_color, dim_color, accent_color, bg_color):
        self.bg.set_style_bg_color(_lc(bg_color), 0)
        for i, (view, _ch) in enumerate(self._items):
            active = (view == current_view)
            self.labels[i].set_style_text_color(_lc(active_color if active else dim_color), 0)
            self.underlines[i].set_style_bg_opa(255 if active else 0, 0)
            self.underlines[i].set_style_bg_color(_lc(accent_color), 0)

_BAR_FONT = getattr(lv, "font_montserrat_14", None) or lv.font_montserrat_12
TOP_BAR = TopBar(scr, _MH_DISPLAY_WIDTH, _STATUS_BAR_HEIGHT)
# 先只放核心的4个界面，插件加载完之后 main.py 会调
# BOTTOM_BAR.rebuild(...) 把插件界面也加进来（这时候还不知道有哪些
# 插件——插件要用 display_core 里的 DISPLAY/PALETTE，所以只能先建好
# display_core 这一层，插件才能 import 它，顺序上没法反过来）。
BOTTOM_BAR = BottomBar(scr, _MH_DISPLAY_WIDTH, _BOTTOM_INDICATOR_HEIGHT,
                        items=[(VIEW_CLOCK, "C"), (VIEW_NOTIFICATIONS, "N"),
                               (VIEW_MUSIC, "M"), (VIEW_STATUS, "S")])
time.sleep_ms(0)

_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_WEEKDAY_NAMES_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# 通知图标
ICON_NOTIFICATION = "📬"
ICON_MUSIC = "🎵"
ICON_WEATHER = "🌤"
ICON_STATUS = "📊"

INPUT = TouchInput(scr)
time.sleep_ms(0)
RTC = machine.RTC() if machine is not None else None


# __all__：MicroPython 的 from X import * 会把下划线开头的名字排除掉
# （跟标准 Python 行为一致），但这个文件里一大堆内部常量都是下划线
# 开头的（_MAX_CHARS_PER_LINE/_CONTENT_Y_START 这些），screen_manager.py
# /main.py/插件文件全靠 `from display_core import *` 拿到它们。之前
# 想当然以为 MicroPython 的 import * 不过滤下划线，没有实测验证，
# 结果这些名字全都没导出成功。显式列出 __all__ 就能绕开这条规则，
# 把想导出的名字（不管下划线不下划线）都摆明了。
__all__ = [
    'GBBitmap', 'lcd_bus', 'machine', 'gc', 'jd9853', 'axs5106',
    'lv', 'I2C', 'time', '_WIDTH', '_HEIGHT', '_BL',
    '_RST', '_DC', '_MOSI', '_SCK', '_HOST', '_LCD_CS',
    '_LCD_FREQ', '_OFFSET_X', '_OFFSET_Y', 'spi_bus', 'display_bus', '_display_drv',
    '_orig_table', '_new_table', 'TouchCalData', '_i2c_bus', '_touch_i2c', '_touch_cal',
    '_indev', '_SCREEN_W', '_SCREEN_H', '_MH_DISPLAY_HEIGHT', '_MH_DISPLAY_WIDTH', '_CHAR_WIDTH',
    '_LINE_HEIGHT', '_MAX_CHARS_PER_LINE', '_MAX_LINES', '_STATUS_BAR_HEIGHT', '_BOTTOM_INDICATOR_HEIGHT', '_CONTENT_Y_START',
    '_CONTENT_HEIGHT', '_CONTENT_MAX_LINES', '_CARD_MARGIN_X', '_CARD_PADDING', '_CARD_GAP', 'scr',
    '_try_disable_scroll', '_SCROLL_DISABLED', 'debug_outline_children', 'PALETTE', '_PALETTE_BASE', '_THEME_INVERTED',
    '_invert_color', 'toggle_theme', 'framebuf', '_UTF8_FONT', '_ascii_glyph_buf', '_ascii_glyph_fb',
    '_draw_glyph', '_blit_block', '_lc', '_alloc_buffers', '_RowSlot', '_RectSlot',
    'LVDisplay', '_TAP_MIN_MS', '_HOLD_EXIT_MS', 'TouchInput', 'DISPLAY', 'NotifStrip',
    'NOTIF_STRIP', 'VIEW_CLOCK', 'VIEW_NOTIFICATIONS', 'VIEW_MUSIC', 'VIEW_STATUS', 'ClockDigits',
    'CLOCK_DIGITS', 'TopBar', 'BottomBar', '_BAR_FONT', 'TOP_BAR', 'BOTTOM_BAR',
    '_WEEKDAY_NAMES', '_WEEKDAY_NAMES_SHORT', 'ICON_NOTIFICATION', 'ICON_MUSIC', 'ICON_WEATHER', 'ICON_STATUS',
    'INPUT', 'RTC',
    '_check_backlight_timer',
]