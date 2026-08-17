"""
_touch.py

移植自 MicroHydra 的 gt911.py 触摸手势处理逻辑（Tap / Swipe 识别、
minisqrt 加速距离计算），底层包装的是 axs5106.AXS5106（LVGL indev）驱动，
而不是重新实现 AXS5106 的 I2C 寄存器读取——AXS5106 没有公开完整寄存器表，
硬猜地址/偏移量不会报错，只会读出乱码坐标，不如直接复用你已经验证过能跑
的 axs5106.py。

调用方式对齐 gt911.py 原来的用法：

    self.touch = _touch.Touch(i2c=self.i2c)
    self.get_touch_events = self.touch.get_touch_events
    self.get_current_points = self.touch.get_current_points

Touch() 对 i2c 参数做了兼容判断：
- 如果传进来的对象已经具备 get_state/_last_x（说明它已经是建好的
  axs5106.AXS5106 indev 实例，只是变量名叫 i2c），直接拿来用；
- 否则把它当作原始 i2c 总线/设备，内部按你原脚本的方式自己创建
  axs5106.AXS5106（reset_pin、touch_cal 校准数据）。

如果你的 self.i2c 既不是 indev，也不是能直接传给 axs5106.AXS5106() 的
设备对象（比如它其实是 machine.I2C 总线本身，还需要再包一层
I2C.Device(bus, axs5106.I2C_ADDR, axs5106.BITS)），那下面这版会在创建
indev 那步报错——把 self.i2c 具体的创建方式告诉我，我再精确适配。
"""

import utime as time
from collections import namedtuple
import lvgl as lv


TouchPoint = namedtuple("TouchPoint", ["id", "x", "y", "size"])

Tap = namedtuple("Tap", ['x', 'y', 'size', 'duration'])
Long_tap = namedtuple("Tap", ['x', 'y', 'size', 'duration'])
Swipe = namedtuple("Swipe", ['x0', 'y0', 'x1', 'y1', 'size', 'duration', 'distance', 'direction'])


@micropython.viper
def minisqrt(n: int) -> int:
    """
    32位整数快速开方，照搬自 wikipedia.org/wiki/Methods_of_computing_square_roots
    （和芯片无关，直接复用原 gt911.py 的实现）
    """
    x = n
    c = 0
    d = 1 << 28

    while d > n:
        d >>= 2

    while d != 0:
        if x >= c + d:
            x -= c + d
            c = (c >> 1) + d
        else:
            c >>= 1

        d >>= 2

    return c


class TouchEvent:
    """
    跟踪单次触摸从按下到抬起的完整过程，结束时判定是 Tap 还是 Swipe。
    逻辑和 gt911.py 完全一致，和具体芯片无关。
    """
    swipe_move_thresh = 30
    touch_time_thresh = 100

    def __init__(self, point=None):
        if point:
            self.alive = True
            _, start_x, start_y, start_size = point
        else:
            self.alive = False
            start_x = 0
            start_y = 0
            start_size = 0

        self.start_x = start_x
        self.start_y = start_y
        self.start_size = start_size
        self.start_time = time.ticks_ms()

        self.new_x = start_x
        self.new_y = start_y
        self.new_size = start_size

    def track(self, point):
        """触摸移动时更新最新坐标"""
        _, new_x, new_y, new_size = point
        self.new_x = new_x
        self.new_y = new_y
        self.new_size = new_size

    @micropython.viper
    def _point_dist(self):
        """计算从按下到抬起的移动距离"""
        x0 = int(self.start_x)
        y0 = int(self.start_y)
        x1 = int(self.new_x)
        y1 = int(self.new_y)

        x = x0 - x1
        y = y0 - y1

        return minisqrt((x * x) + (y * y))

    def _finish_tap(self, touch_time, touch_dist):
        if touch_time < TouchEvent.touch_time_thresh:
            return Tap(
                    (self.start_x + self.new_x) // 2,
                    (self.start_y + self.new_y) // 2,
                    (self.start_size + self.new_size) // 2,
                    touch_time,
                    )
        else:
            return Long_tap(
                        (self.start_x + self.new_x) // 2,
                        (self.start_y + self.new_y) // 2,
                        (self.start_size + self.new_size) // 2,
                        touch_time,
                        )
    @micropython.viper
    def _swipe_dir(self):
        x0 = int(self.start_x)
        y0 = int(self.start_y)
        x1 = int(self.new_x)
        y1 = int(self.new_y)

        x = x1 - x0
        y = y1 - y0

        if abs(x) > abs(y):
            # right or left
            if x > 0:
                return "RIGHT"
            else:
                return "LEFT"
        else:
            # up or down
            if y > 0:
                return "DOWN"
            else:
                return "UP"

    def _finish_swipe(self, touch_time, touch_dist):
        return Swipe(
            self.start_x,
            self.start_y,
            self.new_x,
            self.new_y,
            (self.start_size + self.new_size) // 2,
            touch_time,
            touch_dist,
            self._swipe_dir()
        )

    def finish(self):
        """结束这次触摸事件，返回 Tap 或 Swipe"""
        self.alive = False
        touch_time = time.ticks_diff(time.ticks_ms(), self.start_time)
        touch_dist = self._point_dist()
        if touch_dist < TouchEvent.swipe_move_thresh:
            return self._finish_tap(touch_time, touch_dist)
        return self._finish_swipe(touch_time, touch_dist)


class Touch:
    """
    对齐 gt911.py 里 Touch(i2c=...) 的调用方式，内部包装 axs5106.AXS5106
    （lv.indev）驱动，在上面提供 get_touch_events()/get_current_points()
    手势识别接口。

    只支持单点：axs5106 这条 lv.indev 路径本身只暴露单点坐标
    (get_state/_last_x/_last_y)，不像 GT911 能一次读出最多5个触点的
    原始寄存器数据，所以 tracker 只有1个槽位，固定用 id=0。
    """

    def __init__(self, i2c, reset_pin=20, touch_cal_name='touch_cal', swipe_move_thresh=20, touch_time_thresh=100, debug=False):
        self.debug = debug
        # 情况一：传进来的已经是建好的 axs5106 indev（有 get_state/_last_x），直接用
        if hasattr(i2c, 'get_state') and hasattr(i2c, '_last_x'):
            self.indev = i2c
        else:
            # 情况二：传进来的是原始 i2c 设备，按你原脚本的方式内部构建 axs5106 indev
            import axs5106
            from touch_cal_data import TouchCalData

            # 如果传进来的是裸 machine.I2C 总线（有 writeto_mem/readfrom_mem，
            # 这是 gt911.py 原本 Touch(i2c=machine.I2C(...)) 的用法，_keys.py
            # 里大概率也是照这个来的），需要先按你原脚本的方式包一层 I2C.Device，
            # axs5106.AXS5106 才认得。如果它已经是别的封装类型，这里会跳过，
            # 直接把 i2c 原样传给 axs5106.AXS5106——如果那样报错，说明 self.i2c
            # 的实际类型和这两种假设都不一样，需要贴出 _keys.py 里 self.i2c 的
            # 创建方式来精确适配。
            if hasattr(i2c, 'writeto_mem') and hasattr(i2c, 'readfrom_mem'):
                from i2c import I2C
                i2c = I2C.Device(i2c, axs5106.I2C_ADDR, axs5106.BITS)

            touch_cal = TouchCalData(touch_cal_name)
            self.indev = axs5106.AXS5106(i2c, debug=False,startup_rotation=lv.DISPLAY_ROTATION._90, reset_pin=reset_pin, touch_cal=touch_cal)

            if not self.indev.is_calibrated:
                touch_cal.mirrorX = True
                touch_cal.mirrorY = False
                touch_cal.alphaX = 1.0
                touch_cal.betaX = 0.0
                touch_cal.deltaX = 0.0
                touch_cal.alphaY = 0.0
                touch_cal.betaY = 1.0
                touch_cal.deltaY = 0.0
                touch_cal.save()

        TouchEvent.swipe_move_thresh = swipe_move_thresh
        TouchEvent.touch_time_thresh = touch_time_thresh
        self.tracker = [TouchEvent()]

        # 用于手动推进 LVGL tick（见 get_current_points 里的说明）
        self._last_task_time = time.ticks_ms()

    def get_current_points(self):
        """
        返回当前触摸点列表（0个或1个 TouchPoint）。
        size 字段这条路径上没有压力/尺寸数据，固定填 0。

        indev 底层是 lv.indev_t，get_state()/_last_x/_last_y 读到的其实是
        LVGL 内部定时器上一次刷新的缓存值，不会自己主动去读硬件。这个定时器
        要靠 lv.task_handler() 被反复调用才会触发真正的硬件读取。
        MicroHydra 的主循环不用 LVGL 渲染，不会调用 lv.task_handler()，所以
        这里自己负责推进一次 tick + task_handler，确保每次调用都能拿到真实
        的最新触摸状态，而不是永远卡在第一次读到的旧数据上。
        """
        now = time.ticks_ms()
        elapsed = time.ticks_diff(now, self._last_task_time)
        if elapsed > 0:
            lv.tick_inc(elapsed)
        self._last_task_time = now
        lv.task_handler()

        state = self.indev.get_state()
        '''
        if self.debug:
            print("touch raw state:", state, "PRESSED const:", self.indev.PRESSED,
                  "x:", self.indev._last_x, "y:", self.indev._last_y)
        '''
        if state == self.indev.PRESSED:
            x = self.indev._last_x
            y = self.indev._last_y
            return [TouchPoint(0, y, x, 0)]
        return []

    def get_touch_events(self):
        """
        返回本次调用产生的 Tap / Swipe 事件列表（通常0个或1个）。
        逻辑和 gt911.py 的 get_touch_events 完全一致，只是数据源换成了
        get_current_points() 里的 axs5106 单点读取。
        """
        current_points = self.get_current_points()
        active_ids = [point.id for point in current_points]
        tracker = self.tracker

        output = []

        for point in current_points:
            if tracker[point.id].alive:
                tracker[point.id].track(point)
            else:
                tracker[point.id].__init__(point)
                if self.debug:
                    print("touch: new press started at", point.x, point.y)

        for idx, event in enumerate(tracker):
            if event.alive and idx not in active_ids:
                finished = event.finish()
                if self.debug:
                    print("touch: finished event ->", finished)
                output.append(finished)
            if self.debug and output:    
                print(f"output={output}")
        return output