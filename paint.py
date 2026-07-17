import lcd_bus
from micropython import const
import machine
from time import sleep
import jd9853
import axs5106
import lvgl as lv
from i2c import I2C

lv.init()

# display settings
_WIDTH = 172
_HEIGHT = 320
_BL = 23
_RST = 22
_DC = 15

_MOSI = 2 #SDA
_MISO = 5
_SCK = 1  # SCL
_HOST = 1  # SPI2

_LCD_CS = 14
_LCD_FREQ = 40000000  # 原来是 2000000(2MHz)，刷新一次约172x320画布要传~17万字节，
                       # 2MHz下光传输就要接近0.4秒，这基本是卡顿的主因。
                       # 先试40MHz，如果花屏/初始化失败再往下调（30M/20M/10M...）
_TOUCH_FREQ = 2000000
_TOUCH_CS = 21

_OFFSET_X = 34
_OFFSET_Y = 0

print('s1')
spi_bus = machine.SPI.Bus(
    host=_HOST,
    mosi=_MOSI,
    #miso=_MISO,
    sck=_SCK
)

print('s2')
display_bus = lcd_bus.SPIBus(
    spi_bus=spi_bus,
    freq=_LCD_FREQ,
    dc=_DC,
    cs=_LCD_CS,
)

print('s3')
display = jd9853.JD9853(
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
    offset_y=_OFFSET_Y
)

print('s4')

display.set_power(True)
display.init()
display.set_color_inversion(True)
display.set_rotation(lv.DISPLAY_ROTATION._0)
display.set_backlight(100)


# ============ 使用 TouchCalData 处理触摸校准 ============
from touch_cal_data import TouchCalData

# 创建校准数据对象
touch_cal = TouchCalData('touch_cal')

i2c_bus = I2C.Bus(host=0, sda=18, scl=19)
touch_i2c = I2C.Device(i2c_bus, axs5106.I2C_ADDR, axs5106.BITS)

# 创建触摸设备，传入校准数据
indev = axs5106.AXS5106(touch_i2c, startup_rotation=lv.DISPLAY_ROTATION._0, reset_pin=20, touch_cal=touch_cal)

# 如果未校准，设置镜像参数
if not indev.is_calibrated:
    print("设置触摸校准参数...")
    touch_cal.mirrorX = False   # X轴镜像
    touch_cal.mirrorY = False  # Y轴不镜像

    touch_cal.alphaX = 1.0
    touch_cal.betaX = 0.0
    touch_cal.deltaX = 0.0

    touch_cal.alphaY = 0.0
    touch_cal.betaY = 1.0
    touch_cal.deltaY = 0.0

    touch_cal.save()
    print("校准参数已保存")
else:
    print("使用已保存的校准参数")

# ============ 触摸画出功能 ============
import utime as time


class TouchPainter:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.is_drawing = False
        self.last_x = 0
        self.last_y = 0
        self.pen_color = lv.color_hex(0xFFFFFF)
        self.pen_size = 2
        self.color_index = 0
        self.colors = [0xFFFFFF, 0xFF0000, 0x00FF00, 0x0000FF, 0xFFFF00, 0xFF00FF, 0x00FFFF]

        # 获取当前活动屏幕
        self.scrn = lv.screen_active()

        # 禁用屏幕滚动
        self.scrn.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.scrn.set_scroll_dir(lv.DIR.NONE)

        # 计算画布尺寸
        self.margin = 2
        self.btn_height = 42
        self.canvas_width = width - self.margin * 2
        self.canvas_height = height - self.btn_height - self.margin * 2

        # 创建面板
        self.panel = lv.obj(self.scrn)
        self.panel.set_size(self.canvas_width, self.canvas_height)
        self.panel.set_pos(self.margin, self.btn_height + self.margin)
        self.panel.set_style_bg_color(lv.color_hex(0x1a1a2e), 0)
        self.panel.set_style_border_width(1, 0)
        self.panel.set_style_border_color(lv.color_hex(0x444444), 0)
        self.panel.set_style_radius(3, 0)
        self.panel.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.panel.set_scroll_dir(lv.DIR.NONE)

        # 创建画布
        self.canvas = lv.canvas(self.panel)
        self.canvas.set_size(self.canvas_width, self.canvas_height)
        self.canvas.set_pos(0, 0)
        self.canvas.set_style_bg_opa(lv.OPA._0, 0)
        self.canvas.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.canvas.set_scroll_dir(lv.DIR.NONE)

        # 创建画布缓冲区 - RGB565 (2 bytes per pixel)
        self.cbuf = bytearray(self.canvas_width * self.canvas_height * 2)
        self.canvas.set_buffer(self.cbuf, self.canvas_width, self.canvas_height, lv.COLOR_FORMAT.RGB565)
        self.canvas.fill_bg(lv.color_hex(0x000000), lv.OPA._0)

        # 预计算像素偏移量，提高绘制速度
        self.pixel_offset_cache = {}

        # 创建UI控制按钮
        self.create_ui()

        # 触摸状态
        self.touch_pressed = False

        # 绘制计数器（用于性能调试）
        self.draw_count = 0

        # 探测一次是否能用 LVGL 原生 layer 画线（C 层渲染，比逐像素 set_px 快很多）
        # 只在初始化时判断一次，主循环里不再做 try/except
        self._use_layer_draw = self._probe_layer_draw_api()
        if self._use_layer_draw:
            print("使用 LVGL 原生 layer 画线（更快）")
        else:
            print("回退到逐像素 Python 画线")

        print("触摸画板已启动！")
        print(f"画布尺寸: {self.canvas_width}x{self.canvas_height}")
        print("提示: 在画布区域触摸画画，顶部是控制按钮")

    def _probe_layer_draw_api(self):
        """只在初始化时探测一次 LVGL layer/draw_line API 是否可用"""
        try:
            layer = lv.layer_t()
            self.canvas.init_layer(layer)
            dsc = lv.draw_line_dsc_t()
            dsc.init()
            dsc.color = self.pen_color
            dsc.width = max(1, self.pen_size * 2)
            dsc.round_start = 1
            dsc.round_end = 1
            dsc.p1.x = 0
            dsc.p1.y = 0
            dsc.p2.x = 0
            dsc.p2.y = 0
            lv.draw_line(layer, dsc)
            self.canvas.finish_layer(layer)
            return True
        except Exception as e:
            print("layer draw 不可用:", e)
            return False

    def create_ui(self):
        """创建控制按钮"""
        # 清空按钮
        btn_clear = lv.button(self.scrn)
        btn_clear.set_size(50, 32)
        btn_clear.set_pos(10, 5)
        btn_clear.set_style_bg_color(lv.color_hex(0x2d3436), 0)
        btn_clear.set_style_radius(5, 0)
        label_clear = lv.label(btn_clear)
        label_clear.set_text("Clear")
        label_clear.center()
        btn_clear.add_event_cb(self.clear_cb, lv.EVENT.CLICKED, None)

        # 颜色按钮
        btn_color = lv.button(self.scrn)
        btn_color.set_size(50, 32)
        btn_color.set_pos(65, 5)
        btn_color.set_style_bg_color(lv.color_hex(0x2d3436), 0)
        btn_color.set_style_radius(5, 0)
        label_color = lv.label(btn_color)
        label_color.set_text("Color")
        label_color.center()
        btn_color.add_event_cb(self.color_cb, lv.EVENT.CLICKED, None)

        # 大小按钮
        btn_size = lv.button(self.scrn)
        btn_size.set_size(45, 32)
        btn_size.set_pos(120, 5)
        btn_size.set_style_bg_color(lv.color_hex(0x2d3436), 0)
        btn_size.set_style_radius(5, 0)
        label_size = lv.label(btn_size)
        label_size.set_text("Size")
        label_size.center()
        btn_size.add_event_cb(self.size_cb, lv.EVENT.CLICKED, None)

        # 颜色指示器
        self.color_indicator = lv.obj(self.scrn)
        indicator_size = max(12, self.pen_size * 2)
        self.color_indicator.set_size(indicator_size, indicator_size)
        self.color_indicator.set_pos(120, 9)
        self.color_indicator.set_style_bg_color(self.pen_color, 0)
        self.color_indicator.set_style_radius(indicator_size // 2, 0)
        self.color_indicator.set_style_border_width(2, 0)
        self.color_indicator.set_style_border_color(lv.color_hex(0xffffff), 0)

    def update_color_indicator(self):
        """更新颜色指示器的大小和颜色"""
        indicator_size = max(12, self.pen_size * 2)
        self.color_indicator.set_size(indicator_size, indicator_size)
        self.color_indicator.set_style_radius(indicator_size // 2, 0)
        self.color_indicator.set_style_bg_color(self.pen_color, 0)

    def clear_cb(self, e):
        """清空画布"""
        self.canvas.fill_bg(lv.color_hex(0x000000), lv.OPA._0)
        self.is_drawing = False
        self.draw_count = 0
        print("画布已清空")

    def color_cb(self, e):
        """切换颜色"""
        self.color_index = (self.color_index + 1) % len(self.colors)
        self.pen_color = lv.color_hex(self.colors[self.color_index])
        self.update_color_indicator()
        print("切换颜色:", hex(self.colors[self.color_index]))

    def size_cb(self, e):
        """调整画笔大小"""
        sizes = [1, 2, 3, 4, 5, 7, 10]
        current_idx = sizes.index(self.pen_size) if self.pen_size in sizes else 0
        self.pen_size = sizes[(current_idx + 1) % len(sizes)]
        # 清空像素偏移缓存（因为画笔大小变了）
        self.pixel_offset_cache = {}
        self.update_color_indicator()
        print("画笔大小:", self.pen_size)

    def get_pixel_offsets(self):
        """获取画笔的像素偏移量（缓存）"""
        if self.pen_size not in self.pixel_offset_cache:
            offsets = []
            for dx in range(-self.pen_size, self.pen_size + 1):
                for dy in range(-self.pen_size, self.pen_size + 1):
                    if dx * dx + dy * dy <= self.pen_size * self.pen_size:
                        offsets.append((dx, dy))
            self.pixel_offset_cache[self.pen_size] = offsets
        return self.pixel_offset_cache[self.pen_size]

    def draw_point(self, x, y):
        """画一个点（圆形笔触），用于 Python 回退路径"""
        if x < 0 or x >= self.canvas_width or y < 0 or y >= self.canvas_height:
            return
        offsets = self.get_pixel_offsets()
        color = self.pen_color
        w = self.canvas_width
        h = self.canvas_height
        set_px = self.canvas.set_px
        for dx, dy in offsets:
            px = x + dx
            py = y + dy
            if 0 <= px < w and 0 <= py < h:
                set_px(px, py, color, lv.OPA.COVER)

    def _draw_line_layer(self, x1, y1, x2, y2):
        """用 LVGL 原生 layer/draw_line 画一条粗线（C 层渲染，最快）"""
        layer = lv.layer_t()
        self.canvas.init_layer(layer)

        dsc = lv.draw_line_dsc_t()
        dsc.init()
        dsc.color = self.pen_color
        dsc.width = max(1, self.pen_size * 2)
        dsc.round_start = 1
        dsc.round_end = 1
        dsc.p1.x = x1
        dsc.p1.y = y1
        dsc.p2.x = x2
        dsc.p2.y = y2

        lv.draw_line(layer, dsc)
        self.canvas.finish_layer(layer)

    def _draw_line_python(self, x1, y1, x2, y2):
        """纯 Python 回退版本：Bresenham + 按步长盖圆章，减少大画笔时的重复绘制"""
        offsets = self.get_pixel_offsets()
        color = self.pen_color
        w = self.canvas_width
        h = self.canvas_height
        set_px = self.canvas.set_px

        # 画笔越大，盖章间隔越大（圆本身有重叠，不需要每像素都盖）
        stride = max(1, self.pen_size)
        step_counter = [0]

        def stamp(px, py):
            for dx, dy in offsets:
                fx = px + dx
                fy = py + dy
                if 0 <= fx < w and 0 <= fy < h:
                    set_px(fx, fy, color, lv.OPA.COVER)

        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy

        while True:
            if step_counter[0] % stride == 0:
                stamp(x1, y1)
            step_counter[0] += 1

            if x1 == x2 and y1 == y2:
                stamp(x2, y2)  # 保证终点一定被画上
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x1 += sx
            if e2 < dx:
                err += dx
                y1 += sy

    def draw_line(self, x1, y1, x2, y2):
        """画线：优先走 LVGL 原生 layer 画线，否则回退到 Python 版本"""
        if self._use_layer_draw:
            self._draw_line_layer(x1, y1, x2, y2)
        else:
            self._draw_line_python(x1, y1, x2, y2)

    def update_touch(self):
        """主循环中轮询触摸状态并绘制。

        indev 是标准 lv.indev_t 对象（dir(indev) 确认过 get_state/_last_x/
        _last_y 等都是直接可用的属性，没有 name mangling 问题，也不需要
        hasattr/try-except 兜底）。用 indev.PRESSED 常量比较，比硬编码
        state == 1 更稳妥。
        """
        state = indev.get_state()

        if state == indev.PRESSED:
            print("raw:", indev._last_x, indev._last_y)
            x = indev._last_x
            y = indev._last_y

            # X轴翻转
            canvas_x = self.canvas_width - 1 - (x - self.margin)
            canvas_y = y - self.btn_height - self.margin

            if 0 <= canvas_x < self.canvas_width and 0 <= canvas_y < self.canvas_height:
                if not self.is_drawing:
                    self.is_drawing = True
                    self.last_x = canvas_x
                    self.last_y = canvas_y
                    self.draw_line(canvas_x, canvas_y, canvas_x, canvas_y)
                    self.canvas.invalidate()
                else:
                    if abs(canvas_x - self.last_x) > 1 or abs(canvas_y - self.last_y) > 1:
                        self.draw_line(self.last_x, self.last_y, canvas_x, canvas_y)
                        self.last_x = canvas_x
                        self.last_y = canvas_y
                        self.draw_count += 1
                        # layer 画线开销小很多，可以每次都刷新，手感更跟手
                        self.canvas.invalidate()
            else:
                self.is_drawing = False
        else:
            if self.is_drawing:
                self.canvas.invalidate()
                self.is_drawing = False


# 创建画板实例
painter = TouchPainter(_WIDTH, _HEIGHT)

# ============ 主循环 ============
time_passed = 1000

print("开始主循环...")

while True:
    start_time = time.ticks_ms()

    # 轮询触摸状态并绘制
    painter.update_touch()

    # LVGL处理
    time.sleep_ms(1)
    lv.tick_inc(time_passed)
    lv.task_handler()

    end_time = time.ticks_ms()
    time_passed = time.ticks_diff(end_time, start_time)