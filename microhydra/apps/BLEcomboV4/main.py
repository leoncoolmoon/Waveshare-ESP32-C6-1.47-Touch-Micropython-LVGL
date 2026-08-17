# main.py - 主框架
# 负责: 显示屏/触摸初始化、BLE HID设备管理、GPIO9模式循环切换、控件的创建与销毁调度、主循环
#
# 模式循环顺序: 鼠标 -> 滚轮 -> 键盘 -> 设置 -> 鼠标 ...
# 切换到某个功能时才创建该功能的控件，切换出时整体销毁，不留残留控件。

import lcd_bus
import machine
import jd9853
import axs5106
import lvgl as lv
from i2c import I2C
import time
import gc
from hid_services import ComboHID
import settings as settings_mod
import mouse as mouse_mod
import keyboard as keyboard_mod

# ============ GPIO配置 ============
MODE_SWITCH_PIN = 9  # 模式切换引脚，低电平触发切换

# ============ 模式定义 ============
MODE_MOUSE = 0
MODE_SCROLL = 1
MODE_KEYBOARD = 2
MODE_SETTINGS = 3
MODE_COUNT = 4
MOUSE_GROUP = (MODE_MOUSE, MODE_SCROLL)
MODE_NAMES = ['Mouse', 'Scroll', 'Keyboard', 'Settings']

MARGIN = 5

# ============ 显示初始化 ============
lv.init()

_WIDTH = 172
_HEIGHT = 320
_BL = 23
_RST = 22
_DC = 15

_MOSI = 2
_MISO = 5
_SCK = 1
_HOST = 1

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
original_table = jd9853.JD9853._ORIENTATION_TABLE
new_table = list(original_table)
new_table[0] = 0x00
new_table[1] = 0x60
new_table[2] = 0x82
new_table[3] = 0xA0
jd9853.JD9853._ORIENTATION_TABLE = tuple(new_table)
display.set_rotation(lv.DISPLAY_ROTATION._90)

# 屏幕旋转90度后的实际可用尺寸
UI_WIDTH = _HEIGHT   # 320
UI_HEIGHT = _WIDTH   # 172

# ============ 设置数据加载 & 应用 ============
cfg = settings_mod.Settings()
cfg.load()
settings_mod.apply_backlight(display, cfg.backlight)
settings_mod.apply_invert(display, cfg.invert)

# ============ GPIO模式切换引脚初始化 ============
mode_switch_pin = machine.Pin(MODE_SWITCH_PIN, machine.Pin.IN, machine.Pin.PULL_UP)

# ============ 触摸初始化 ============
from touch_cal_data import TouchCalData

touch_cal = TouchCalData('touch_cal')

i2c_bus = I2C.Bus(host=0, sda=18, scl=19)
touch_i2c = I2C.Device(i2c_bus, axs5106.I2C_ADDR, axs5106.BITS)

indev = axs5106.AXS5106(touch_i2c, startup_rotation=lv.DISPLAY_ROTATION._90,
                         reset_pin=20, touch_cal=touch_cal)

if not indev.is_calibrated:
    print("设置触摸校准参数...")
    touch_cal.mirrorX = True
    touch_cal.mirrorY = False
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


# ============ BLE 组合设备状态 ============
class BLEState:
    def __init__(self):
        print("初始化BLE组合设备...")
        self.hid = ComboHID("Touch Combo HID")
        self.hid.set_state_change_callback(self.state_callback)
        self.hid.start()
        self.connected = False
        self.caps_lock_on = False  # 由主机端真实LED状态驱动(见 _on_kb_output_report)
        self.auto_advertise = True  # 断开后是否自动重新广播、允许(之前配对过的)主机自动连回来
        print("BLE组合设备初始化完成")

    def state_callback(self):
        state = self.hid.get_state()
        if state is ComboHID.DEVICE_CONNECTED:
            self.connected = True
            print("BLE已连接!")
        elif state is ComboHID.DEVICE_IDLE:
            self.connected = False
            print("BLE断开连接")
        elif state is ComboHID.DEVICE_ADVERTISING:
            print("正在广播...")

    def advertise(self):
        if not self.connected and self.auto_advertise:
            self.hid.start_advertising()

    def set_auto_advertise(self, enabled):
        """打开/关闭"断开后自动重新广播"。关闭后设备会停止广播，
        已配对的主机也就没有机会自动连回来，直到重新打开这个开关。"""
        self.auto_advertise = enabled
        if enabled:
            if not self.connected:
                self.hid.start_advertising()
        else:
            try:
                self.hid.stop_advertising()
            except Exception as e:
                print("停止广播失败:", e)

    def disconnect(self):
        """主动断开当前BLE连接，使用 HumanInterfaceDevice 提供的 conn_handle + _ble.gap_disconnect()。
        断开后会顺带关闭自动广播(set_auto_advertise(False))，避免主机很快又自动连回来；
        想重新允许连接的话，去设置里把 "Stay Connectable" 开关打开即可。"""
        if self.connected:
            try:
                if self.hid.conn_handle is not None:
                    self.hid._ble.gap_disconnect(self.hid.conn_handle)
                    self.hid.conn_handle = None
                    self.connected = False
                    print("已断开BLE连接")
                else:
                    print("当前没有有效的连接句柄")
            except Exception as e:
                print("断开连接失败:", e)
        self.set_auto_advertise(False)

    def forget_all_hosts(self):
        """清除本机保存的全部配对密钥(bonding secrets)。hid_keystores.KeyStore
        提供了现成的 clear_secrets()，会清空内存里的secrets字典，JSONKeyStore的
        重载版本还会顺带把空列表写回 keys.json，所以这里直接精确调用它即可。

        清除后，之前配对过的主机最好也去系统蓝牙设置里把这个设备"删除/忽略"掉，
        双方各自清空才能干净地重新配对，否则可能出现密钥对不上、连接失败的情况。"""
        self.disconnect()
        store = getattr(self.hid, "secrets", None)
        if store is None:
            print("未找到 secrets 密钥库对象")
            return
        try:
            store.clear_secrets()
            print("已清除全部配对记录，所有主机需要重新配对")
        except Exception as e:
            print("清除配对记录失败:", e)

    def bonded_key_count(self):
        """返回当前保存的配对密钥条目数，仅用于在设置界面里给用户一个大致参考
        (hid_keystores里的key不带主机名字，没法列出"是哪几台设备")。"""
        store = getattr(self.hid, "secrets", None)
        secrets_dict = getattr(store, "secrets", None) if store is not None else None
        if isinstance(secrets_dict, dict):
            return len(secrets_dict)
        return 0


ble = BLEState()
mouse_hid = mouse_mod.MouseHID(ble)
keyboard_hid = keyboard_mod.KeyboardHID(ble)


# ============ 大写锁定: 从主机真实LED状态获取，而非本地猜测 ============
def _on_kb_output_report(report):
    """当主机切换Caps Lock(或Num/Scroll Lock)时，BLE会把键盘output report写回来。
    按HID报告描述符，这个output report实际上只有1个字节(5位LED状态+3位填充)，
    不是8字节 —— 需要先把 hid_services.py 里 ComboHID.ble_irq() 对应那行从
    struct.unpack("8B", report) 改成 report[0]，见对话里的说明。

    这里做了防御性处理，兼容"单字节int"和"长度>=1的bytes/tuple"两种传入形式，
    不管 hid_services.py 具体怎么传都能正常工作。
    LED位图: bit0=NumLock bit1=CapsLock bit2=ScrollLock bit3=Compose bit4=Kana"""
    if isinstance(report, int):
        led_byte = report
    else:
        try:
            led_byte = report[0]
        except (IndexError, TypeError):
            return

    caps_on = bool(led_byte & 0x02)
    ble.caps_lock_on = caps_on
    if active_keyboard_ui is not None:
        active_keyboard_ui.set_caps_lock(caps_on)


ble.hid.kb_set_output_callback(_on_kb_output_report)

# ============ 屏幕 ============
scrn = lv.screen_active()
scrn.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
scrn.set_scroll_dir(lv.DIR.NONE)
scrn.set_style_bg_color(lv.color_hex(0x0a0a1a), 0)


# ============ 右上角: BLE连接状态圆点(蓝色/白色)，常驻不随模式销毁 ============
def create_ble_indicator():
    indicator_size = int(min(UI_WIDTH, UI_HEIGHT) * 0.09)
    dot = lv.obj(scrn)
    dot.set_size(indicator_size, indicator_size)
    dot.set_pos(UI_WIDTH - MARGIN - indicator_size, MARGIN)
    dot.set_style_radius(indicator_size // 2, 0)
    dot.set_style_bg_color(lv.color_hex(0xffffff), 0)
    dot.set_style_border_width(1, 0)
    dot.set_style_border_color(lv.color_hex(0x888888), 0)
    dot.move_foreground()
    return dot


ble_dot = create_ble_indicator()


def update_ble_indicator():
    if ble.connected:
        ble_dot.set_style_bg_color(lv.color_hex(0x4488ff), 0)
    else:
        ble_dot.set_style_bg_color(lv.color_hex(0xffffff), 0)
    ble_dot.move_foreground()


update_ble_indicator()

# ============ 模式管理 ============
current_mode = None
active_mouse_ui = None
active_keyboard_ui = None
active_settings_ui = None


def _destroy_all():
    global active_mouse_ui, active_keyboard_ui, active_settings_ui
    if active_mouse_ui is not None:
        active_mouse_ui.destroy()
        active_mouse_ui = None
    if active_keyboard_ui is not None:
        active_keyboard_ui.destroy()
        active_keyboard_ui = None
    if active_settings_ui is not None:
        active_settings_ui.destroy()
        active_settings_ui = None
    gc.collect()


def enter_mode(mode):
    global current_mode, active_mouse_ui, active_keyboard_ui, active_settings_ui

    prev_in_mouse_group = current_mode in MOUSE_GROUP if current_mode is not None else False
    new_in_mouse_group = mode in MOUSE_GROUP

    if new_in_mouse_group and prev_in_mouse_group and active_mouse_ui is not None:
        # 鼠标 <-> 滚轮 之间切换，同一套控件，无需重建
        active_mouse_ui.set_submode(mode == MODE_SCROLL)
    else:
        _destroy_all()
        if new_in_mouse_group:
            active_mouse_ui = mouse_mod.MouseUI(
                scrn, mouse_hid, cfg, UI_WIDTH, UI_HEIGHT, is_scroll=(mode == MODE_SCROLL)
            )
        elif mode == MODE_KEYBOARD:
            active_keyboard_ui = keyboard_mod.KeyboardUI(
                scrn, keyboard_hid, cfg, UI_WIDTH, UI_HEIGHT,
                initial_caps_lock=ble.caps_lock_on
            )
        elif mode == MODE_SETTINGS:
            active_settings_ui = settings_mod.SettingsUI(
                scrn, display, cfg, UI_WIDTH, UI_HEIGHT,
                on_disconnect=ble.disconnect,
                is_connected=lambda: ble.connected,
                on_forget_hosts=ble.forget_all_hosts,
                on_set_auto_advertise=ble.set_auto_advertise,
                is_auto_advertise=lambda: ble.auto_advertise,
                get_bonded_key_count=ble.bonded_key_count
            )

    current_mode = mode
    # BLE指示点始终保持在最上层
    ble_dot.move_foreground()
    print("切换模式:", MODE_NAMES[mode])


def toggle_mode():
    next_mode = (current_mode + 1) % MODE_COUNT if current_mode is not None else MODE_MOUSE
    enter_mode(next_mode)


last_pin_state = 1


def check_gpio_switch():
    global last_pin_state
    current_state = mode_switch_pin.value()
    if last_pin_state == 1 and current_state == 0:
        gc.collect()
        print("GPIO触发模式切换")
        toggle_mode()
        time.sleep_ms(50)
    last_pin_state = current_state
    

def update_touch():
    if current_mode in MOUSE_GROUP and active_mouse_ui is not None:
        active_mouse_ui.update_touch(indev)
    elif current_mode == MODE_KEYBOARD and active_keyboard_ui is not None:
        active_keyboard_ui.update_touch(indev)
    # 设置模式使用原生LVGL控件事件(slider/switch)，无需手动轮询触摸


def update_ble_status():
    update_ble_indicator()
    if active_settings_ui is not None:
        active_settings_ui.refresh_connection_status()
    if not ble.connected:
        ble.advertise()


# ============ 主程序启动 ============
enter_mode(MODE_MOUSE)
update_ble_status()

# ============ 主循环 ============
time_passed = 1000
last_status_update = time.ticks_ms()
status_update_interval = 2000
last_gpio_check = time.ticks_ms()
gpio_check_interval = 50

print("\n=== 组合设备已启动 ===")
print("GPIO9 循环切换模式: Mouse -> Scroll -> Keyboard -> Settings")
print("使用方法:")
print("1. 鼠标/滚轮模式: 在画布滑动控制")
print("2. 键盘模式: 右侧拖动选择行，左侧选择键")
print("3. 点击L/M/R执行鼠标点击")
print("4. 轻击画布执行左键点击")
print("5. 设置模式: 调整背光/反色/鼠标灵敏度/滚轮灵敏度")
print("======================\n")

while True:
    start_time = time.ticks_ms()

    update_touch()

    if time.ticks_diff(time.ticks_ms(), last_gpio_check) > gpio_check_interval:
        check_gpio_switch()
        last_gpio_check = time.ticks_ms()

    if time.ticks_diff(time.ticks_ms(), last_status_update) > status_update_interval:
        update_ble_status()
        last_status_update = time.ticks_ms()

    time.sleep_ms(1)
    lv.tick_inc(time_passed)
    lv.task_handler()

    end_time = time.ticks_ms()
    time_passed = time.ticks_diff(end_time, start_time)