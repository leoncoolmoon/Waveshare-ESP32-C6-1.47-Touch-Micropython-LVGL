'''
LVGL 版圆形时钟 —— 用于 lvgl_micropython 自编译固件 (DISPLAY=jd9853)
'''
import lcd_bus
from micropython import const
import machine
from time import sleep
import jd9853
import lvgl as lv
import time
import math

lv.init()

# ---------------- 屏幕/引脚配置 ----------------
_WIDTH = 172
_HEIGHT = 320
_BL = 23
_RST = 22
_DC = 15

_MOSI = 2  # SDA
_MISO = 5
_SCK = 1   # SCL
_HOST = 1  # SPI2

_LCD_CS = 14
_LCD_FREQ = 2000000

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
display.set_backlight(100)

scrn = lv.screen_active()
scrn.set_style_bg_color(lv.color_hex(0x000000), 0)

# ---------------- 表盘几何参数 ----------------
CENTER_X = _WIDTH // 2       # 86
CENTER_Y = 110               # 表盘居中放在屏幕上半部分
RADIUS = min(CENTER_X, CENTER_Y) - 6   # 约80，留边距

wkd = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def polar(cx, cy, angle, length):
    """angle: 0 = 12点钟方向，顺时针为正"""
    return (cx + length * math.sin(angle), cy - length * math.cos(angle))


# ---------------- 画表盘刻度（静态，只画一次）----------------
print('Drawing ticks...')
for i in range(12):
    angle = i * (2 * math.pi / 12)
    x1, y1 = polar(CENTER_X, CENTER_Y, angle, RADIUS - 4)
    x2, y2 = polar(CENTER_X, CENTER_Y, angle, RADIUS - 12)
    tick = lv.line(scrn)
    tick.set_style_line_width(2, 0)
    tick.set_style_line_color(lv.color_hex(0xFFFFFF), 0)
    pts = [{"x": int(x1), "y": int(y1)}, {"x": int(x2), "y": int(y2)}]
    tick.set_points(pts, 2)

    hour_num = 12 if i == 0 else i
    lbl = lv.label(scrn)
    lbl.set_text(str(hour_num))
    lbl.set_style_text_color(lv.color_hex(0x00FF00), 0)
    lx, ly = polar(CENTER_X, CENTER_Y, angle, RADIUS - 26)
    lbl.set_pos(int(lx) - 5, int(ly) - 8)

# ---------------- 表盘外圈（使用 arc）----------------

# 移除 knob - 使用不同的方法

# 注释掉 clear_flag，因为 arc 没有这个方法
# ring.clear_flag(lv.obj.FLAG.CLICKABLE)

# ---------------- 表针（动态，每秒更新一次坐标）----------------
print('Creating hands...')
# 需要给每根线保留一个 points 数组的引用，LVGL 只存指针，Python 对象被回收会出问题
hour_pts = [{"x": CENTER_X, "y": CENTER_Y}, {"x": CENTER_X, "y": CENTER_Y}]
min_pts = [{"x": CENTER_X, "y": CENTER_Y}, {"x": CENTER_X, "y": CENTER_Y}]
sec_pts = [{"x": CENTER_X, "y": CENTER_Y}, {"x": CENTER_X, "y": CENTER_Y}]

hour_hand = lv.line(scrn)
hour_hand.set_points(hour_pts, 2)  # 立即设置初始点
hour_hand.set_style_line_width(4, 0)
hour_hand.set_style_line_color(lv.color_hex(0x4444FF), 0)
hour_hand.set_style_line_rounded(True, 0)

min_hand = lv.line(scrn)
min_hand.set_points(min_pts, 2)  # 立即设置初始点
min_hand.set_style_line_width(3, 0)
min_hand.set_style_line_color(lv.color_hex(0x00FF00), 0)
min_hand.set_style_line_rounded(True, 0)

sec_hand = lv.line(scrn)
sec_hand.set_points(sec_pts, 2)  # 立即设置初始点
sec_hand.set_style_line_width(1, 0)
sec_hand.set_style_line_color(lv.color_hex(0xFF0000), 0)
sec_hand.set_style_line_rounded(True, 0)

# ---------------- 数字时钟部分 ----------------
print('Creating labels...')
date_label = lv.label(scrn)
date_label.set_style_text_color(lv.color_hex(0xFF0000), 0)
date_label.set_pos(CENTER_X - 40, CENTER_Y + RADIUS + 30)

ampm_label = lv.label(scrn)
ampm_label.set_style_text_color(lv.color_hex(0xFF0000), 0)
ampm_label.set_pos(CENTER_X - 12, CENTER_Y - 8)

wkd_label = lv.label(scrn)
wkd_label.set_style_text_color(lv.color_hex(0x00FF00), 0)
wkd_label.set_pos(CENTER_X - 12, CENTER_Y + RADIUS + 55)

time_label = lv.label(scrn)
time_label.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
time_label.set_pos(CENTER_X - 30, CENTER_Y + RADIUS + 5)


def strD(n):
    return str(n) if n > 9 else "0" + str(n)


def update_clock(timer):
    nowT = list(time.localtime())
    hour, minute, second = nowT[3], nowT[4], nowT[5]

    # 表针角度
    a_hour = 2 * math.pi * (hour % 12) / 12 + 2 * math.pi * minute / (12 * 60)
    a_min = 2 * math.pi * minute / 60 + 2 * math.pi * second / (60 * 60)
    a_sec = 2 * math.pi * second / 60

    hx, hy = polar(CENTER_X, CENTER_Y, a_hour, RADIUS - 60)
    mx, my = polar(CENTER_X, CENTER_Y, a_min, RADIUS - 36)
    sx, sy = polar(CENTER_X, CENTER_Y, a_sec, RADIUS - 24)

    hour_pts[1]["x"], hour_pts[1]["y"] = int(hx), int(hy)
    min_pts[1]["x"], min_pts[1]["y"] = int(mx), int(my)
    sec_pts[1]["x"], sec_pts[1]["y"] = int(sx), int(sy)

    hour_hand.set_points(hour_pts, 2)
    min_hand.set_points(min_pts, 2)
    sec_hand.set_points(sec_pts, 2)

    # 数字时钟
    date_label.set_text(f"{nowT[0]}/{strD(nowT[1])}/{strD(nowT[2])}")
    ampm_label.set_text("AM" if hour < 12 else "PM")
    wkd_label.set_text(wkd[nowT[6]])
    time_label.set_text(f"{strD(hour)}:{strD(minute)}:{strD(second)}")


# 先立即更新一次显示
print('Initial update...')
update_clock(None)

# 每秒刷新一次
print('Creating timer...')
lv.timer_create(update_clock, 1000, None)

# ---------------- LVGL 主循环 ----------------
print('Clock started successfully!')
print('Press Ctrl-C to stop')

import utime as time
time_passed = 1000

while True:
    start_time = time.ticks_ms()
    time.sleep_ms(5)
    lv.tick_inc(time_passed)
    lv.task_handler()
    end_time = time.ticks_ms()
    time_passed = time.ticks_diff(end_time, start_time)