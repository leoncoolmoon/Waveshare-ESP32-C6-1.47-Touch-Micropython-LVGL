import machine

MH_SDCARD_FREQ_INIT = const(100_000)
MH_SDCARD_FREQ_REG = const(1_320_000)
MH_SPI_SLOT = const(1)
MH_SCK = const(1)
MH_MOSI = const(2)
MH_MISO = const(3)
MH_SDCARD_CS = const(4)
MH_DISPLAY_FREQ = const(40_000_000)
MH_DISPLAY_CS = const(14)
MH_DISPLAY_DC = const(15)
MH_DISPLAY_RESET = const(22)
MH_DISPLAY_BACKLIGHT = const(23)

# 定义全局唯一的共享总线，初始为 None
_shared_bus = None

def get_spi_bus():
    global _shared_bus
    if _shared_bus is None:
        print("[Hardware] 正在初始化全局唯一的 SPI(1) 总线...")
        # 只有在第一次被调用时，才会真正去触碰和初始化硬件引脚
        _shared_bus = machine.SPI.Bus(host=MH_SPI_SLOT, sck=MH_SCK, mosi=MH_MOSI, miso=MH_MISO)
    return _shared_bus