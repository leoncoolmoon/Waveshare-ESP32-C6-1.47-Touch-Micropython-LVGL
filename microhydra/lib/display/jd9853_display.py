"""jd9853 硬件层。

职责被压缩到最小：
    1. 初始化 SPI.Bus / lcd_bus / jd9853 / LVGL
    2. 提供一个 lv.canvas，把 DisplayCore 的 framebuffer 显示到屏幕
    3. show() 负责告诉 LVGL "buffer 内容变了，刷新"

关于旋转：
    这块面板 (172x320, jd9853) 在 lvgl_micropython 上，直接用
    display.set_rotation() 默认是不对的（会导致内容错位、颜色错乱），
    根因是 jd9853 驱动内部的 _ORIENTATION_TABLE (MADCTL 方向表) 没有
    为这块面板正确适配 90° 旋转对应的条目。
    解决方式：在创建 jd9853.JD9853 实例之前，运行时把
    jd9853.JD9853._ORIENTATION_TABLE 里 90° 对应的那一项(index 1)
    打补丁成 0x60 (MV+MX)，同时把 offset_x/offset_y 对调
    (原来给 rotation=0 用的是 offset_x=34, offset_y=0；
     rotation=1 时要变成 offset_x=0, offset_y=34)。
    这两点都已经在实机上验证过可以正常横屏显示。

关于内存架构（这是这版和上一版最大的区别，务必读一下）：
    ESP32-C6-Touch-LCD-1.47 这块板子没有 PSRAM，全片只有 512KB HP
    SRAM，还要跟 WiFi/BLE 协议栈、ESP-IDF、MicroPython 运行时共用。
    lcd_bus.SPIBus.allocate_framebuffer(..., MEMORY_INTERNAL |
    MEMORY_DMA) 申请的是"内部SRAM里支持DMA"这一小块专用内存池，跟
    MicroPython 的 GC 堆是完全独立的两个区域，而且这个池子本身就很
    小——实测申请整屏大小 (172*320*2 = 110080 字节) 会直接
    MemoryError，不管 GC 堆有多少空闲都没用。

    所以这一版的架构是"大画布 + 小DMA中转缓冲"：
        - self._canvas_buf：整屏大小的 RGB565 数据，存在普通 GC 堆里
          （不需要 DMA 能力，只是内容数据源），交给 lv.canvas 持有。
        - self._img_buf：只有几十行大小 (_DMA_BUF_ROWS 控制)，通过
          display_bus.allocate_framebuffer() 从 DMA 池申请，作为
          frame_buffer1 传给 jd9853.JD9853()，专门给驱动的 flush_cb
          做 SPI/DMA 中转用。
        - show() 只需要 invalidate canvas 上有变化的区域，
          lv.task_handler() 会自动通过这一小块 DMA buffer 分批把
          数据搬到屏幕上，不需要手写搬运循环。

    如果以后发现 _DMA_BUF_ROWS 这个默认值在实机上还是申请失败（比如
    WiFi 开着的时候可用 DMA 内存进一步变少），把这个常量调小即可，
    不需要改架构。

关于"内容区"和"物理屏幕"的解耦（content_width/content_height 等参数）：
    __init__ 新增了 content_width/content_height/content_x/content_y
    四个可选参数，把"面板物理分辨率"和"canvas 实际画多大"这两件事
    彻底分开：
        - width/height（原有的必传参数）：面板物理分辨率，只喂给
          jd9853.JD9853(display_width=..., display_height=...) 做硬件
          寻址，以及决定 DMA 中转缓冲(_img_buf)的大小——这两处都不受
          content_width/content_height 影响。
        - content_width/content_height：真正决定 _canvas_buf（以及
          通过它决定 DisplayCore.fbuf）分配多大内存。不传时默认等于
          整个物理屏幕，也就是旧版行为，完全向后兼容。
        - content_x/content_y：canvas 在屏幕上的摆放位置。不传时默认
          水平居中、垂直靠上对齐。
    这样传比屏幕小的 content_width/content_height 进来，就能省下
    canvas 那块内存；省下来的、canvas 覆盖不到的屏幕区域(见
    left_margin_width/right_margin_width 两个属性)，可以拿来挂载独立
    的 LVGL widget（比如手势虚拟键盘），直接建在 self.scrn 上，不需要
    经过、也不占用 _canvas_buf 的内存。
"""

import machine
import lcd_bus
import jd9853
import lvgl as lv
from array import array
from .displaycore import DisplayCore
from lib import hardware



# DMA 中转缓冲区的行数。172*20*2 ≈ 12.8KB(rotation=0时w=172)，
# 或 320*20*2 ≈ 12.8KB(rotation=1时w=320)，都远小于整屏的110KB，
# 大概率能在 ESP32C6 的 DMA 内存池里稳定申请到。
# 如果实机测试仍然 MemoryError，把这个数字调小（比如 10 或 5）再试。
_DMA_BUF_ROWS = const(40)#20



def _patch_orientation_table():
    """给 jd9853.JD9853._ORIENTATION_TABLE 打补丁，修正 90° 旋转的
    MADCTL 值。这是个类属性，只需要打一次补丁，重复调用也没有副作用
    （每次都是设成同样的绝对值，不会叠加）。
    
    """
    #print("打补丁")
    original_table = jd9853.JD9853._ORIENTATION_TABLE
    new_table = list(original_table)
    new_table[0] = 0x00 
    new_table[1] = 0x60
    new_table[2] = 0x82# MV+MX 0x00/0x20/0x40/0x60/0x80/0xA0/0xC0/0xE0
    new_table[3] = 0xA0
    jd9853.JD9853._ORIENTATION_TABLE = tuple(new_table)
    



class JD9853Display(DisplayCore):
    """DisplayCore + jd9853 硬件推送层。"""

    def __init__(
            self,
            width: int,
            height: int,
            *,
            rotation: int = 0,
            offset_x: int = 0,
            offset_y: int = 0,
            use_tiny_buf: bool = False,
            content_width: int | None = None,
            content_height: int | None = None,
            content_x: int | None = None,
            content_y: int | None = None,
            **kwargs):
        """
        content_width/content_height:
            内容区(canvas + DisplayCore.fbuf)的尺寸，单位是"旋转后的屏幕坐标"
            (跟 width x height 旋转后的最终物理分辨率同一套坐标系)。
            不传时默认等于整个屏幕(旧行为不变)。
            传比屏幕小的值时，_canvas_buf / DisplayCore.fbuf 只会按这个
            尺寸分配，省下的内存不会被这个类使用——上层(比如虚拟键盘的
            LVGL widget)可以直接挂在 self.scrn 上，用 canvas 之外的
            屏幕空间，不需要经过这块 canvas 内存。

            注意：display_width/display_height 传给 jd9853.JD9853(...)
            的仍然是面板的物理分辨率(width/height 参数本身)，不受这两个
            参数影响——面板寻址和内容区大小是两回事，缩小内容区不会、
            也不应该影响硬件层初始化。

        content_x/content_y:
            内容区在屏幕上的左上角坐标。不传时默认水平居中、垂直靠上
            对齐（贴合"内容区居中偏上，左右留白做手势键盘"的布局）。
        """

        self._use_tiny_buf = use_tiny_buf
        self._width = width
        self._height = height
        self._rotation = rotation
        self.utf8_font = open("/font/utf8_8x8.bin", "rb", buffering = 0) 
        # === 1. 初始化硬件 ===
        lv.init()
          
        self.spi_bus = hardware.get_spi_bus()
        self.display_bus = lcd_bus.SPIBus(spi_bus=self.spi_bus, freq=hardware.MH_DISPLAY_FREQ, dc=hardware.MH_DISPLAY_DC, cs=hardware.MH_DISPLAY_CS)
        
        # === 2. 根据 rotation 计算屏幕的物理(旋转后)尺寸 ===
        # rotation 为奇数(1/3)时，DisplayCore 内部会把 fbuf 的宽高对调
        # （见 DisplayCore.__init__ 里的 height/width 互换逻辑）。
        # screen_w/screen_h 是面板旋转后的物理分辨率，只用来:
        #   1) 分配 DMA 中转缓冲区(_img_buf)——这块 buffer 是 jd9853
        #      驱动内部整个面板共用的 flush 通道，跟 canvas 内容区
        #      多大无关，必须按物理全屏尺寸算。
        #   2) 在没有指定 content_width/content_height 时，作为内容区
        #      的默认尺寸(整屏)，保持旧行为不变。
        if rotation % 2 == 1:
            screen_w, screen_h = height, width
        else:
            screen_w, screen_h = width, height
        self._screen_w = screen_w
        self._screen_h = screen_h

        # === 2.1 内容区(canvas)尺寸与位置 ===
        # 不传 content_width/content_height 时默认整屏，行为与旧版一致。
        content_width = screen_w if content_width is None else content_width
        content_height = screen_h if content_height is None else content_height
        # 默认水平居中、垂直靠上对齐。
        content_x = (screen_w - content_width) // 2 if content_x is None else content_x
        content_y = 0 if content_y is None else content_y

        self.content_width = content_width
        self.content_height = content_height
        self.content_x = content_x
        self.content_y = content_y
        # 左右留白宽度，留给以后的手势虚拟键盘 widget 用。
        self.left_margin_width = content_x
        self.right_margin_width = screen_w - content_x - content_width

        # self._buf_w/self._buf_h 是 canvas 内容区的尺寸(不是整屏)，
        # 供 _convert_tiny_rows()/mirror_canvas_buffer_array() 等只
        # 操作 canvas 内容的方法使用。
        buf_w, buf_h = content_width, content_height
        self._buf_w = buf_w
        self._buf_h = buf_h

        # === 3. 分配 DMA 中转缓冲区（必须在 jd9853.JD9853(...) 之前）===
        # 只申请 _DMA_BUF_ROWS 行，而不是整屏——见文件头的架构说明。
        # display_bus 对每个实例最多允许 2 块通过 allocate_framebuffer
        # 分配的 frame buffer（超过会 MemoryError: "maximum of 2 frame
        # buffers allowed"）。如果创建 jd9853.JD9853(...) 时不传
        # frame_buffer1，驱动会自己偷偷申请一份占用这个名额，所以这里
        # 必须先分配好、显式传进去，避免它自己再申请一次。
        #
        # 对应地，deinit() 里必须调用 display_bus.free_framebuffer() 来
        # 释放，普通 del/delattr 是释放不了这块内存的（不受 gc 管理）。
        dma_rows = min(screen_h, _DMA_BUF_ROWS)
        self._img_buf = self.display_bus.allocate_framebuffer(
            screen_w * dma_rows * 2,
            lcd_bus.MEMORY_INTERNAL | lcd_bus.MEMORY_DMA,
        )

        self.display = jd9853.JD9853(
            data_bus=self.display_bus,
            display_width=width,
            display_height=height,
            frame_buffer1=self._img_buf,
            backlight_pin=hardware.MH_DISPLAY_BACKLIGHT,
            reset_pin=hardware.MH_DISPLAY_RESET,
            reset_state=jd9853.STATE_LOW,
            backlight_on_state=jd9853.STATE_PWM,
            color_space=lv.COLOR_FORMAT.RGB565,
            color_byte_order=jd9853.BYTE_ORDER_BGR,
            rgb565_byte_swap=True,
            offset_x=offset_x,
            offset_y=offset_y,
        )
        self.display.set_power(True)
        self.display.init()
        self.display.set_color_inversion(True)
        
        # 硬件旋转：方向表补丁 + 正确的 offset_x/offset_y（由调用方传入）
        # 都到位之后，这里才能正确生效。
        try:
            _patch_orientation_table()
            self.display.set_rotation(lv.DISPLAY_ROTATION._90)
        except Exception as e:
            print(f"[警告] set_rotation 失败: {e}")
            print("可用的 lv.DISPLAY_ROTATION 属性:", dir(lv.DISPLAY_ROTATION))

        #self.display.set_backlight(100)

        # === 关键：必须创建 TaskHandler 并保留引用 ===
        # lvgl_micropython 的显示驱动依赖 TaskHandler 来驱动
        # "渲染 -> flush -> 通过 SPI 写到面板" 这条链路。
        # 没有它，lv.task_handler() 即使调用"成功"，数据也不会真正被
        # 发送到硬件，屏幕会保持黑屏。
        import task_handler
        self._task_handler = task_handler.TaskHandler()

        self.scrn = lv.screen_active()
        self.scrn.set_style_bg_color(lv.color_hex(0x000000), 0)
        import gc
        gc.collect()
        print(f"Free memory: {gc.mem_free()}")
        

        _external_canvas_buf = kwargs.pop('reserved_bytearray', None)
        _needed_canvas_bytes = buf_w * buf_h * 2
        if _external_canvas_buf is not None:
            if len(_external_canvas_buf) < _needed_canvas_bytes:
                raise ValueError(
                    'reserved_bytearray 太小：内容区 %dx%d 需要至少 %d 字节，'
                    '实际传入 %d 字节' % (
                        buf_w, buf_h, _needed_canvas_bytes, len(_external_canvas_buf))
                )
            self._canvas_buf = _external_canvas_buf
        else:
            self._canvas_buf = bytearray(_needed_canvas_bytes)

        self.canvas = lv.canvas(self.scrn)
        self.canvas.set_buffer(self._canvas_buf, buf_w, buf_h, lv.COLOR_FORMAT.RGB565)
        self.canvas.set_pos(content_x, content_y)
        # canvas 之外的屏幕区域(左右留白，见 left_margin_width/
        # right_margin_width)保持 scrn 的黑色背景，留给以后挂载在
        # self.scrn 上的手势虚拟键盘 widget 使用，不占用这里的
        # _canvas_buf 内存。

        # use_tiny_buf=True 时，DisplayCore.fbuf 画在更小的 GS4（4bit/
        # 像素）缓冲区（_tiny_buf，普通堆）里，show() 时再逐行转换成
        # RGB565 写入 _canvas_buf；两块缓冲区不能共用。
        # use_tiny_buf=False 时，DisplayCore.fbuf 直接就是
        # _canvas_buf，不需要额外转换。
        if use_tiny_buf:
            tiny_size = (
                (buf_h * buf_w) // 2
                if (buf_w % 8 == 0)
                else (buf_h * (buf_w + 1)) // 2
            )
            self._tiny_buf = bytearray(tiny_size)
            core_reserved_buf = self._tiny_buf
        else:
            self._tiny_buf = None
            core_reserved_buf = self._canvas_buf

        # === 5. 初始化 DisplayCore ===
        # DisplayCore.__init__ 内部会按 rotation 对 width/height 做一次
        # swap 得到最终的 self.width/self.height（见 displaycore.py 里
        # "height if rotation%2==1 else width" 那段逻辑，不能改
        # displaycore.py，只能在这里反过来适配它）。
        # 之前整屏时传的是面板原始 width/height（172, 320），swap 后
        # 恰好得到屏幕物理尺寸 screen_w/screen_h（320, 172）。
        # 现在要让 swap 后的结果等于内容区尺寸 content_width/
        # content_height，所以这里要传"反过来的" content_height/
        # content_width，跟原来 width/height 是同一套约定。
        if rotation % 2 == 1:
            core_width, core_height = content_height, content_width
        else:
            core_width, core_height = content_width, content_height

        super().__init__(
            core_width, core_height,
            rotation=rotation,
            use_tiny_buf=use_tiny_buf,
            needs_swap=False,  # 由 jd9853 处理字节序
            reserved_bytearray=core_reserved_buf,
            **kwargs,
        )
        if self.backlight:
            br = self.config['brightness']
            print(f"brightness = {br}")
            self.set_brightness(br)
    
    def _ensure_display_mode(self):
        """切换到显示频率"""
        if hasattr(self, '_spi_controller'):
            try:
                self.display_bus.init(freq=hardware.MH_DISPLAY_FREQ)
                
                print(f"切换到显示频率: {hardware.MH_DISPLAY_FREQ}Hz")
            except Exception as e:
                print(f"切换显示频率失败: {e}")  
    # ------------------------------------------------------------------
    def _free_img_buf(self):
        """释放通过 display_bus.allocate_framebuffer() 分配的 DMA 中转
        缓冲区（_img_buf）。

        这块内存不在 MicroPython 的 gc 堆里，普通 `del self._img_buf`
        只是删掉了引用，不会把内存还给底层分配器——必须显式调用
        display_bus.free_framebuffer()。deinit() 流程要在 delattr
        之前调用这个方法。
        """
        buf = getattr(self, '_img_buf', None)
        if buf is not None:
            try:
                self.display_bus.free_framebuffer(buf)
                print("_img_buf 已通过 free_framebuffer 释放")
            except Exception as e:
                print(f"[警告] free_framebuffer 失败: {e}")
            self._img_buf = None

    def _teardown_canvas(self):
        """在释放 _canvas_buf 之前，必须先让 LVGL 自己正确销毁 canvas
        这个 widget（从 screen 的渲染树里摘除、释放 LVGL 内部为它分配
        的结构）。

        如果跳过这一步，直接把 Python 侧的 self.canvas /
        self._canvas_buf 引用删掉：canvas.set_buffer() 时 LVGL C 侧
        已经拿到了一份指向 _canvas_buf 内存的裸指针，且这个 canvas
        widget 本身还挂在 self.scrn 的渲染树上——单纯 delattr 并不会
        通知 LVGL "这个 widget 不用了、这块内存要被回收了"。一旦
        _canvas_buf 被 gc 实际回收，LVGL 下次碰到这个还存活的 canvas
        widget（渲染、遍历 screen 树、甚至只是 gc.collect() 触发的
        堆块合并检查）就是在访问一块结构已经变化的内存，会把堆的空闲
        链表写坏，表现为 heap_tlsf.c 的
        assert failed: remove_free_block (prev_free field can not be
        null) 崩溃——这是内存损坏后的连锁反应，不是内存不够。

        正确顺序：先 canvas.delete()（LVGL 自己解除对 _canvas_buf 的
        持有），再让 Python 侧回收 _canvas_buf。
        """
        canvas = getattr(self, 'canvas', None)
        if canvas is not None:
            try:
                canvas.delete()
                print("canvas widget 已通过 delete() 从 LVGL 渲染树摘除")
            except Exception as e:
                print(f"[警告] canvas.delete() 失败: {e}")
            self.canvas = None

    def reset(self):
        self.display.reset()
        
    def set_power(self, power_on: bool):
        self.display.set_power(power_on)
        '''
    def set_brightness(self, brightness: int):
        """0-10 映射到 0-100，走 jd9853 自己的背光接口。"""
        pct = max(0, min(100, brightness * 10))
        self.display.set_backlight(pct)
        '''
    def sleep_mode(self, enable: bool):
        """进入/退出睡眠模式。"""
        self.display.set_power(not enable)

    def _convert_tiny_rows(self, y_min: int, y_max: int):
        """把 [y_min, y_max) 范围内的 GS4 (_tiny_buf) 像素转换成 RGB565，
        写入 _canvas_buf。

        注意：不能用 self.palette[idx]，因为 Palette.__getitem__ 在
        use_tiny_buf=True 时会直接把索引原样返回（不查表），这里必须
        绕开它，直接读取 Palette 内部的 buf（16个 RGB565 颜色，小端，
        两字节一个）。
        """
        pbuf = self.palette.buf

        def palette_lookup(idx: int) -> int:
            off = idx * 2
            return pbuf[off] | (pbuf[off + 1] << 8)

        buf_w = self._buf_w
        canvas_buf = self._canvas_buf
        get_px = self.fbuf.pixel  # framebuf 自带的读像素接口，不用手动解析nibble

        for y in range(y_min, y_max):
            row_base = y * buf_w * 2
            for x in range(buf_w):
                idx = get_px(x, y)
                color = palette_lookup(idx)
                off = row_base + x * 2
                canvas_buf[off] = color & 0xFF
                canvas_buf[off + 1] = (color >> 8) & 0xFF

    def show(self):
        """把 DisplayCore 已经画好的 framebuffer 刷新到屏幕上。"""
        self._ensure_display_mode()
        y_min, y_max = self.reset_show_y()

        # 如果没有变化，仍然需要处理 LVGL 事件
        if y_max <= y_min:
            lv.task_handler()
            lv.tick_inc(5)
            return

        # tiny_buf 模式：先把变化的行从 GS4 转换成 RGB565，写进
        # _canvas_buf。非 tiny_buf 模式下 fbuf 本来就是 _canvas_buf，
        # 不需要转换这一步。
        if self._use_tiny_buf:
            self._convert_tiny_rows(y_min, y_max)

        # 只让 LVGL 重绘变化的这一块区域。lv.task_handler() 会自动
        # 通过 frame_buffer1（那一小块 DMA 中转缓冲）分批把这块区域
        # 的数据搬到屏幕上，不需要手写搬运循环。
        self.canvas.invalidate()

        # 处理 LVGL 事件（触发实际的渲染+flush）
        lv.task_handler()
        lv.tick_inc(5)