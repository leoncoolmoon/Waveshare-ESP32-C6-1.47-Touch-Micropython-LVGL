# /lib/hydra/sleep.py
from lib.hydra import loader
import machine
import time


"""进入深度睡眠，唤醒后恢复之前的程序"""

# 获取启动参数
args = loader.get_args()
print(f"Sleep启动参数: {args}")

# 如果有参数，第一个是sleep本身，第二个是要恢复的程序
if len(args) >= 2:
    resume_app = args[1]
    # 保存恢复程序到RTC（供唤醒后使用）
    loader.set_args(resume_app)
    print(f"唤醒后将启动: {resume_app}")
else:
    # 如果没有指定恢复程序，唤醒后启动Launcher
    loader.set_args("launcher")
    print("唤醒后将启动: Launcher")
wake_pin = Pin(0, Pin.IN, Pin.PULL_UP)

# 2. 设置唤醒条件
# 将引脚和唤醒电平传递给 wake_on_gpio
# esp32.WAKEUP_ALL_LOW: 所有指定的引脚都为低电平时唤醒
# esp32.WAKEUP_ANY_HIGH: 任意一个指定的引脚为高电平时唤醒
esp32.wake_on_gpio((wake_pin,), esp32.WAKEUP_ANY_HIGH)

# 等待一下让打印完成
time.sleep(0.1)

# 进入深度睡眠
print("💤 进入深度睡眠...")
machine.deepsleep()
# 程序在这里停止，直到唤醒

