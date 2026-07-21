# MicroPython Human Interface Device library - Touch Mouse
# 触摸屏鼠标：在画布上触摸控制鼠标移动，按钮控制点击
# 支持GPIO切换滚轮模式，支持轻击点击
#
# 本版本修复内容：
#   1. 垂直滚动过快：不再把像素位移直接当滚轮格数发送，改为累积+节流，
#      并将 set_wheel() 的实际取值限制在很小的范围内。
#   2. 水平滚动无效/被误判成垂直滚动：不再用连续的垂直滚轮模拟横向滚动，
#      改为调用 hid_services.py 中 Mouse 类新增的 set_pan()（真正的 AC Pan /
#      横向滚轮 HID 字段），需要配合修改后的 hid_services.py 使用。

import lcd_bus
from micropython import const
import machine
from time import sleep
import jd9853
import axs5106
import lvgl as lv
from i2c import I2C
import time
import gc
from myapp.hid_services import Mouse

# ============ GPIO配置 ============
MODE_SWITCH_PIN = 9  # 模式切换引脚，低电平触发切换

# ============ 显示初始化 ============
lv.init()

# display settings - 横屏模式 (宽度>高度)
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
_LCD_FREQ = 40000000
_TOUCH_FREQ = 2000000
_TOUCH_CS = 21

_OFFSET_X = 0
_OFFSET_Y = 34

print('初始化SPI总线...')
spi_bus = machine.SPI.Bus(
    host=_HOST,
    mosi=_MOSI,
    #miso=_MISO,
    sck=_SCK
)

print('初始化显示总线...')
display_bus = lcd_bus.SPIBus(
    spi_bus=spi_bus,
    freq=_LCD_FREQ,
    dc=_DC,
    cs=_LCD_CS,
)

print('初始化显示屏...')
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
    offset_y=_OFFSET_Y
)

display.set_power(True)
display.init()
#display.set_color_inversion(True)
original_table = jd9853.JD9853._ORIENTATION_TABLE
new_table = list(original_table)
new_table[0] = 0x00 
new_table[1] = 0x60
new_table[2] = 0x82# MV+MX 0x00/0x20/0x40/0x60/0x80/0xA0/0xC0/0xE0
new_table[3] = 0xA0 
jd9853.JD9853._ORIENTATION_TABLE = tuple(new_table)
display.set_rotation(lv.DISPLAY_ROTATION._90)  # 横屏旋转
display.set_backlight(20)

# ============ GPIO模式切换引脚初始化 ============
mode_switch_pin = machine.Pin(MODE_SWITCH_PIN, machine.Pin.IN, machine.Pin.PULL_UP)

# ============ 触摸初始化 ============
from touch_cal_data import TouchCalData

touch_cal = TouchCalData('touch_cal')

i2c_bus = I2C.Bus(host=0, sda=18, scl=19)
touch_i2c = I2C.Device(i2c_bus, axs5106.I2C_ADDR, axs5106.BITS)

indev = axs5106.AXS5106(touch_i2c, startup_rotation=lv.DISPLAY_ROTATION._90, 
                        reset_pin=20, touch_cal=touch_cal)

# 加载或设置校准参数
if not indev.is_calibrated:
    print("设置触摸校准参数...")
    touch_cal.mirrorX = True   # X轴镜像 - 控制左右反向
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

# ============ BLE鼠标类 ============
class BLESimpleMouse:
    def __init__(self):
        print("初始化BLE鼠标...")
        self.mouse = Mouse("Touch Mouse")
        self.mouse.set_state_change_callback(self.mouse_state_callback)
        self.mouse.start()
        
        # 鼠标状态
        self.connected = False
        
        print("BLE鼠标初始化完成")
    
    def mouse_state_callback(self):
        state = self.mouse.get_state()
        if state is Mouse.DEVICE_CONNECTED:
            self.connected = True
            print("BLE鼠标已连接!")
        elif state is Mouse.DEVICE_IDLE:
            self.connected = False
            print("BLE鼠标断开连接")
        elif state is Mouse.DEVICE_ADVERTISING:
            print("正在广播...")
    
    def advertise(self):
        if not self.connected:
            self.mouse.start_advertising()
    
    def move(self, dx, dy):
        """移动鼠标相对位移"""
        if self.connected:
            # 限制最大移动速度，防止跳帧
            max_move = 50
            dx = max(-max_move, min(max_move, dx))
            dy = max(-max_move, min(max_move, dy))
            
            self.mouse.set_axes(dx, dy)
            self.mouse.notify_hid_report()
    
    def scroll(self, dx, dy):
        """滚轮滚动 - 垂直用 set_wheel，水平用真正的 set_pan（AC Pan）"""
        if self.connected:
            # dx/dy 在这里已经是"格数"级别的小整数（由调用方做累积换算），
            # 这里再做一次保险性的限幅，避免异常输入导致滚动过快。
            dx = max(-5, min(5, int(dx)))
            dy = max(-5, min(5, int(dy)))
            
            if dx == 0 and dy == 0:
                return
            
            # 关键修复：notify_hid_report() 每次都会把完整状态(含X/Y)一起发出去。
            # 如果不清零，鼠标模式下最后一次移动残留的 X/Y 会被重复带出去，
            # 导致滚动的时候鼠标跟着"抖一下"。这里显式清零，只让滚轮字段生效。
            self.mouse.set_axes(0, 0)
            
            if dy != 0:
                self.mouse.set_wheel(dy)
            if dx != 0:
                self.mouse.set_pan(dx)
            
            self.mouse.notify_hid_report()
            time.sleep_ms(5)
            
            # 发送完立即复位，避免主机持续收到非零滚轮值
            self.mouse.set_wheel(0)
            self.mouse.set_pan(0)
            self.mouse.notify_hid_report()
    
    def click(self, button):
        """点击鼠标按钮: 1=左键, 2=右键, 3=中键"""
        if self.connected:
            # 同样清零X/Y，避免点击时把上一次移动的残留位移带出去
            self.mouse.set_axes(0, 0)
            if button == 1:
                self.mouse.set_buttons(b1=1)
            elif button == 2:
                self.mouse.set_buttons(b2=1)
            elif button == 3:
                self.mouse.set_buttons(b3=1)
            self.mouse.notify_hid_report()
            time.sleep_ms(50)
            self.mouse.set_buttons()
            self.mouse.notify_hid_report()
    
    def tap_click(self):
        """轻击 - 左键点击"""
        if self.connected:
            # 同样清零X/Y，避免轻击时把上一次移动的残留位移带出去
            self.mouse.set_axes(0, 0)
            self.mouse.set_buttons(b1=1)
            self.mouse.notify_hid_report()
            time.sleep_ms(30)
            self.mouse.set_buttons()
            self.mouse.notify_hid_report()
    
    def stop(self):
        self.mouse.stop()

# ============ 触摸鼠标界面 ============
class TouchMouseUI:
    def __init__(self, width, height, ble_mouse):
        self.width = height
        self.height = width
        self.ble_mouse = ble_mouse
        
        # 触摸状态
        self.touch_pressed = False
        self.last_x = 0
        self.last_y = 0
        self.sensitivity = 1.5  # 灵敏度系数
        
        # 轻击检测
        self.touch_start_time = 0
        self.touch_start_x = 0
        self.touch_start_y = 0
        self.is_tap = False
        self.tap_threshold = 20  # 移动距离阈值（像素）
        self.tap_time_threshold = 300  # 时间阈值（毫秒）
        
        # 滚轮模式
        self.scroll_mode = False
        
        # 滚动累积/节流参数（修复滚动过快 + 水平滚动问题的关键）
        self.scroll_accum_x = 0          # 水平累积像素
        self.scroll_accum_y = 0          # 垂直累积像素
        self.last_scroll_time = 0        # 上次真正发送滚轮事件的时间
        self.scroll_interval_ms = 40     # 两次滚轮事件之间的最小间隔
        self.scroll_px_per_notch = 8     # 累积多少像素算作一"格"滚动
        
        # GPIO状态
        self.last_pin_state = 1  # 上次引脚状态（高电平）
        
        # 获取当前活动屏幕
        self.scrn = lv.screen_active()
        
        # 禁用屏幕滚动
        self.scrn.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.scrn.set_scroll_dir(lv.DIR.NONE)
        
        # 计算相对尺寸
        self.margin = 5
        self.btn_height = int(self.height * 0.225)  # 高度占1/8
        self.btn_width = int(self.width * 0.31)     # 宽度约1/3 (减去间距)
        self.btn_spacing = int(self.width * 0.02)   # 间距占2%
        
        # 创建按钮区域
        self.create_buttons()
        
        # 创建触摸画布
        self.canvas_width = self.width - self.margin * 2
        self.canvas_height = self.height - self.btn_height - self.margin * 3
        self.create_canvas()
        
        # 创建状态指示器（左上角）
        self.create_status_indicator()
        
        # 创建模式指示器（右上角）
        self.create_mode_indicator()
        
        print(f"触摸鼠标UI已启动!")
        print(f"触摸区域: {self.canvas_width}x{self.canvas_height}")
        print(f"GPIO {MODE_SWITCH_PIN} 切换滚轮模式（低电平触发）")
        print("轻击画布执行左键点击")
        print("滚轮模式下：垂直滑动=垂直滚动，水平滑动=真正的水平滚动（AC Pan）")
    
    def create_buttons(self):
        """创建左中右键 - 水平排列，各占1/3"""
        total_btn_width = self.btn_width * 3 + self.btn_spacing * 2
        start_x = (self.width - total_btn_width) // 2
        
        # 左键 - 左侧1/3
        self.btn_left = lv.button(self.scrn)
        self.btn_left.set_size(self.btn_width, self.btn_height - self.margin)
        self.btn_left.set_pos(start_x, self.margin // 2)
        self.btn_left.set_style_bg_color(lv.color_hex(0x2d3436), 0)
        self.btn_left.set_style_radius(8, 0)
        label_left = lv.label(self.btn_left)
        label_left.set_text("L")
        label_left.center()
        self.btn_left.add_event_cb(self.left_click_cb, lv.EVENT.CLICKED, None)
        
        # 中键 - 中间1/3
        self.btn_middle = lv.button(self.scrn)
        self.btn_middle.set_size(self.btn_width, self.btn_height - self.margin)
        self.btn_middle.set_pos(start_x + self.btn_width + self.btn_spacing, self.margin // 2)
        self.btn_middle.set_style_bg_color(lv.color_hex(0x2d3436), 0)
        self.btn_middle.set_style_radius(8, 0)
        label_middle = lv.label(self.btn_middle)
        label_middle.set_text("M")
        label_middle.center()
        self.btn_middle.add_event_cb(self.middle_click_cb, lv.EVENT.CLICKED, None)
        
        # 右键 - 右侧1/3
        self.btn_right = lv.button(self.scrn)
        self.btn_right.set_size(self.btn_width, self.btn_height - self.margin)
        self.btn_right.set_pos(start_x + (self.btn_width + self.btn_spacing) * 2, self.margin // 2)
        self.btn_right.set_style_bg_color(lv.color_hex(0x2d3436), 0)
        self.btn_right.set_style_radius(8, 0)
        label_right = lv.label(self.btn_right)
        label_right.set_text("R")
        label_right.center()
        self.btn_right.add_event_cb(self.right_click_cb, lv.EVENT.CLICKED, None)
    
    def create_canvas(self):
        """创建触摸画布"""
        # 创建面板作为画布背景
        self.panel = lv.obj(self.scrn)
        self.panel.set_size(self.canvas_width, self.canvas_height)
        self.panel.set_pos(self.margin, self.btn_height + self.margin)
        self.panel.set_style_bg_color(lv.color_hex(0x1a1a2e), 0)
        self.panel.set_style_border_width(2, 0)
        self.panel.set_style_border_color(lv.color_hex(0x444444), 0)
        self.panel.set_style_radius(5, 0)
        self.panel.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.panel.set_scroll_dir(lv.DIR.NONE)
        
        # 创建画布
        self.canvas = lv.canvas(self.panel)
        self.canvas.set_size(self.canvas_width, self.canvas_height)
        self.canvas.set_pos(0, 0)
        self.canvas.set_style_bg_opa(lv.OPA._0, 0)
        self.canvas.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.canvas.set_scroll_dir(lv.DIR.NONE)
        
        # 创建画布缓冲区
        self.cbuf = bytearray(self.canvas_width * self.canvas_height * 2)
        self.canvas.set_buffer(self.cbuf, self.canvas_width, self.canvas_height, 
                               lv.COLOR_FORMAT.RGB565)
        self.canvas.fill_bg(lv.color_hex(0x0a0a1a), lv.OPA.COVER)
        
        # 画布中心绘制提示线
        self.draw_center_guide()
    
    def draw_center_guide(self):
        """绘制中心引导线"""
        cx = self.canvas_width // 2
        cy = self.canvas_height // 2
        color = lv.color_hex(0x00ff88)
        
        # 画十字线（淡一些）
        size = min(self.canvas_width, self.canvas_height) // 8
        for i in range(-size, size + 1):
            if i % 2 == 0:
                self.canvas.set_px(cx + i, cy, lv.color_hex(0x224433), lv.OPA.COVER)
                self.canvas.set_px(cx, cy + i, lv.color_hex(0x224433), lv.OPA.COVER)
        
        # 中心小点
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                self.canvas.set_px(cx + dx, cy + dy, color, lv.OPA.COVER)
        
        # 如果处于滚轮模式，显示滚轮图标
        if self.scroll_mode:
            self.draw_scroll_indicator()
        
        self.canvas.invalidate()
    
    def draw_scroll_indicator(self):
        """绘制滚轮模式指示图标"""
        cx = self.canvas_width // 2
        cy = self.canvas_height // 2
        color = lv.color_hex(0x4488ff)
        
        # 画一个简单的滚轮图标（圆环+箭头）
        radius = min(self.canvas_width, self.canvas_height) // 6
        for angle in range(0, 360, 10):
            import math
            rad = math.radians(angle)
            x = int(cx + radius * math.cos(rad))
            y = int(cy + radius * math.sin(rad))
            if 0 <= x < self.canvas_width and 0 <= y < self.canvas_height:
                self.canvas.set_px(x, y, color, lv.OPA.COVER)
        
        # 画上下箭头（垂直滚轮）
        arrow_size = min(self.canvas_width, self.canvas_height) // 12
        for i in range(-arrow_size, arrow_size + 1, 2):
            self.canvas.set_px(cx + i, cy - int(radius * 0.6), lv.color_hex(0x4488ff), lv.OPA.COVER)
            self.canvas.set_px(cx + i, cy + int(radius * 0.6), lv.color_hex(0x4488ff), lv.OPA.COVER)
        
        # 画左右箭头（水平滚轮指示）
        for i in range(-arrow_size, arrow_size + 1, 2):
            self.canvas.set_px(cx - int(radius * 0.6), cy + i, lv.color_hex(0x4488ff), lv.OPA.COVER)
            self.canvas.set_px(cx + int(radius * 0.6), cy + i, lv.color_hex(0x4488ff), lv.OPA.COVER)
    
    def create_status_indicator(self):
        """创建连接状态指示器（左上角）"""
        indicator_size = int(min(self.width, self.height) * 0.04)
        self.status_led = lv.obj(self.scrn)
        self.status_led.set_size(indicator_size, indicator_size)
        self.status_led.set_pos(self.margin + 2, self.margin)
        self.status_led.set_style_radius(indicator_size // 2, 0)
        self.status_led.set_style_bg_color(lv.color_hex(0xffffff), 0)  # 白色=未连接
        self.status_led.set_style_border_width(1, 0)
        self.status_led.set_style_border_color(lv.color_hex(0x888888), 0)
    
    def create_mode_indicator(self):
        """创建模式指示器（右上角）"""
        indicator_size = int(min(self.width, self.height) * 0.04)
        self.mode_led = lv.obj(self.scrn)
        self.mode_led.set_size(indicator_size, indicator_size)
        self.mode_led.set_pos(self.width - self.margin - indicator_size - 2, self.margin)
        self.mode_led.set_style_radius(indicator_size // 2, 0)
        self.mode_led.set_style_bg_color(lv.color_hex(0x888888), 0)  # 灰色=鼠标模式
        self.mode_led.set_style_border_width(1, 0)
        self.mode_led.set_style_border_color(lv.color_hex(0x888888), 0)
        
        # 模式标签
        self.mode_label = lv.label(self.scrn)
        self.mode_label.set_text("M")
        self.mode_label.set_pos(self.width - self.margin - indicator_size - 18, self.margin - 2)
        self.mode_label.set_style_text_color(lv.color_hex(0x888888), 0)
    
    def update_status(self, connected):
        """更新连接状态"""
        if connected:
            self.status_led.set_style_bg_color(lv.color_hex(0x00ff00), 0)  # 绿色=已连接
        else:
            self.status_led.set_style_bg_color(lv.color_hex(0xffffff), 0)  # 白色=未连接
    
    def update_mode(self, scroll_mode):
        """更新模式指示器"""
        if scroll_mode:
            self.mode_led.set_style_bg_color(lv.color_hex(0x4488ff), 0)  # 蓝色=滚轮模式
            self.mode_label.set_style_text_color(lv.color_hex(0x4488ff), 0)
            self.mode_label.set_text("S")
        else:
            self.mode_led.set_style_bg_color(lv.color_hex(0x888888), 0)  # 灰色=鼠标模式
            self.mode_label.set_style_text_color(lv.color_hex(0x888888), 0)
            self.mode_label.set_text("M")
    
    def toggle_scroll_mode(self):
        """切换滚轮模式"""
        self.scroll_mode = not self.scroll_mode
        self.update_mode(self.scroll_mode)
        # 切换模式时清空累积量，避免残留的位移在下次滚动时突然触发
        self.scroll_accum_x = 0
        self.scroll_accum_y = 0
        # 更新画布显示
        self.canvas.fill_bg(lv.color_hex(0x0a0a1a), lv.OPA.COVER)
        self.draw_center_guide()
        print(f"切换模式: {'滚轮' if self.scroll_mode else '鼠标'}")
    
    def check_gpio_switch(self):
        """检查GPIO引脚状态，检测下降沿触发切换"""
        current_state = mode_switch_pin.value()
        
        # 检测下降沿（从高到低变化）
        if self.last_pin_state == 1 and current_state == 0:
            print("GPIO触发模式切换")
            self.toggle_scroll_mode()
            # 防抖延迟
            time.sleep_ms(50)
        
        self.last_pin_state = current_state
    
    def left_click_cb(self, e):
        """左键点击"""
        self.ble_mouse.click(1)
    
    def middle_click_cb(self, e):
        """中键点击"""
        self.ble_mouse.click(3)
    
    def right_click_cb(self, e):
        """右键点击"""
        self.ble_mouse.click(2)
    
    def handle_scroll(self, move_dx, move_dy):
        """
        处理滚轮模式下的位移。
        采用"累积像素 -> 达到阈值才发送一格滚动 -> 限制发送频率"的策略，
        避免每次触摸采样(几乎每1ms一次)都直接发送滚轮事件导致滚动过快。
        同时明确按主方向二选一发送（垂直或水平），避免混合抖动。
        """
        self.scroll_accum_x += move_dx
        self.scroll_accum_y += move_dy
        
        now = time.ticks_ms()
        if time.ticks_diff(now, self.last_scroll_time) < self.scroll_interval_ms:
            return  # 还没到最小发送间隔，先只累积，不发送
        
        # 判断主方向：谁的累积量绝对值大就滚谁，避免同时触发垂直和水平
        if abs(self.scroll_accum_y) >= abs(self.scroll_accum_x):
            notches = int(self.scroll_accum_y / self.scroll_px_per_notch)
            if notches != 0:
                self.ble_mouse.scroll(0, notches)
                self.scroll_accum_x = 0
                self.scroll_accum_y = 0
                self.last_scroll_time = now
        else:
            notches = int(self.scroll_accum_x / self.scroll_px_per_notch)
            if notches != 0:
                self.ble_mouse.scroll(notches, 0)
                self.scroll_accum_x = 0
                self.scroll_accum_y = 0
                self.last_scroll_time = now
    
    def update_touch(self):
        """更新触摸状态 - 控制鼠标移动或滚轮，支持轻击"""
        state = indev.get_state()
        
        if state == indev.PRESSED:
            point = lv.point_t()
            indev.get_point(point)   # 拿到校准+旋转后的真实屏幕坐标
            x = point.x
            y = point.y

            
            # 转换到画布坐标 (横屏模式)
            # 注意：旋转后坐标需要重新映射
            canvas_x = x - self.margin
            canvas_y = y - self.btn_height - self.margin
            
            # 检查是否在画布区域内
            if 0 <= canvas_x < self.canvas_width and 0 <= canvas_y < self.canvas_height:
                if not self.touch_pressed:
                    # 首次按下 - 记录时间和位置用于轻击检测
                    self.touch_pressed = True
                    self.touch_start_time = time.ticks_ms()
                    self.touch_start_x = canvas_x
                    self.touch_start_y = canvas_y
                    self.last_x = canvas_x
                    self.last_y = canvas_y
                    self.is_tap = True  # 假设是轻击，后续根据移动距离判断
                    # 新按下时清空滚动累积，避免残留上一次触摸的量
                    self.scroll_accum_x = 0
                    self.scroll_accum_y = 0
                else:
                    # 计算位移
                    dx = canvas_x - self.last_x
                    dy = canvas_y - self.last_y
                    
                    # 检查移动距离，如果超过阈值则取消轻击
                    total_dx = canvas_x - self.touch_start_x
                    total_dy = canvas_y - self.touch_start_y
                    if abs(total_dx) > self.tap_threshold or abs(total_dy) > self.tap_threshold:
                        self.is_tap = False
                    
                    # 如果有位移，执行移动或滚动
                    if abs(dx) > 1 or abs(dy) > 1:
                        # 如果有移动，取消轻击
                        self.is_tap = False
                        
                        # 计算缩放后的位移
                        move_dx = dx * self.sensitivity
                        move_dy = dy * self.sensitivity
                        
                        if self.scroll_mode:
                            # 滚轮模式 - 累积+节流后再决定是否真正发送滚轮事件
                            self.handle_scroll(move_dx, move_dy)
                        else:
                            # 鼠标模式 - 移动鼠标
                            if abs(move_dx) > 1 or abs(move_dy) > 1:
                                self.ble_mouse.move(int(move_dx), int(move_dy))
                        
                        self.last_x = canvas_x
                        self.last_y = canvas_y
            else:
                self.touch_pressed = False
                self.is_tap = False
        else:
            # 触摸释放
            if self.touch_pressed:
                # 检查是否是轻击
                if self.is_tap:
                    # 检查时间是否在阈值内
                    elapsed = time.ticks_diff(time.ticks_ms(), self.touch_start_time)
                    if elapsed < self.tap_time_threshold:
                        # 执行轻击（左键点击）
                        print("轻击检测 - 左键点击")
                        self.ble_mouse.tap_click()
                    else:
                        print("轻击超时")
                
                self.touch_pressed = False
                self.is_tap = False
                # 松手时清空滚动累积
                self.scroll_accum_x = 0
                self.scroll_accum_y = 0
    
    def update_ble_status(self):
        """更新BLE连接状态"""
        self.update_status(self.ble_mouse.connected)
        
        # 如果未连接，尝试广播
        if not self.ble_mouse.connected:
            self.ble_mouse.advertise()


# ============ 主程序 ============

# 创建BLE鼠标实例
ble_mouse = BLESimpleMouse()

# 创建触摸鼠标UI
ui = TouchMouseUI(_WIDTH, _HEIGHT, ble_mouse)

# ============ 主循环 ============
time_passed = 1000
last_status_update = time.ticks_ms()
status_update_interval = 2000  # 每2秒更新一次状态
last_gpio_check = time.ticks_ms()
gpio_check_interval = 50  # 每50ms检查一次GPIO

print("\n=== 触摸鼠标已启动 ===")
print("使用方法:")
print("1. 在画布区域滑动控制鼠标移动")
print("2. 点击L/M/R执行点击操作")
print(f"3. GPIO {MODE_SWITCH_PIN} 切换滚轮模式（低电平触发）")
print("4. 轻击画布执行左键点击")
print("5. 左上角指示灯: 绿色=已连接, 白色=未连接")
print("6. 右上角指示灯: 蓝色=滚轮模式, 灰色=鼠标模式")
print("======================\n")

# 初始更新状态
ui.update_status(ble_mouse.connected)
ui.update_mode(False)

while True:
    start_time = time.ticks_ms()
    
    # 更新触摸
    ui.update_touch()
    
    # 定期检查GPIO
    if time.ticks_diff(time.ticks_ms(), last_gpio_check) > gpio_check_interval:
        ui.check_gpio_switch()
        last_gpio_check = time.ticks_ms()
    
    # 定期更新BLE状态
    if time.ticks_diff(time.ticks_ms(), last_status_update) > status_update_interval:
        ui.update_ble_status()
        last_status_update = time.ticks_ms()
    
    # LVGL处理
    time.sleep_ms(1)
    lv.tick_inc(time_passed)
    lv.task_handler()
    
    end_time = time.ticks_ms()
    time_passed = time.ticks_diff(end_time, start_time)

