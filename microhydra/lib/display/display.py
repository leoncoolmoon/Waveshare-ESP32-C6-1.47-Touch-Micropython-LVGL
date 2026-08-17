"""This Module provides an easy to use Display object for creating graphics in MicroHydra."""
import machine
from .jd9853_display import JD9853Display

# ~~~~~ Magic constants (ESP32-C6-Touch-LCD-1.47 / jd9853): ~~~~~
_MH_DISPLAY_HEIGHT = const(320)
_MH_DISPLAY_WIDTH = const(172)
_MH_DISPLAY_ROTATION = const(1)
_MH_DISPLAY_OFFSET_X = const(0)
_MH_DISPLAY_OFFSET_Y = const(34)
_MH_DISPLAY_BACKLIGHT = const(23)

s_height = const(135)
s_width = const(240)
s_top = const(37)
class Display(JD9853Display):
    """Main graphics class for MicroHydra.
    Subclasses the device-specific display driver.
    """
    # Set to True to redraw all overlays next time show is called
    draw_overlays = False
    # A public list of overlay functions, to be called in order.
    overlay_callbacks = []

    def __new__(cls, **kwargs):  # noqa: ARG003, D102
        if not hasattr(cls, 'instance'):
            Display.instance = super().__new__(cls)
        return cls.instance

    def __init__(
            self,
            *,
            use_tiny_buf=False,
            **kwargs):
        """Initialize the Display."""
        if hasattr(self, 'fbuf'):
            print("WARNING: Display re-initialized.")
            return

        print(f"Display.__init__: use_tiny_buf={use_tiny_buf}")

        super().__init__(
            _MH_DISPLAY_WIDTH,
            _MH_DISPLAY_HEIGHT,
            rotation=_MH_DISPLAY_ROTATION,
            offset_x=_MH_DISPLAY_OFFSET_X,
            offset_y=_MH_DISPLAY_OFFSET_Y,
            backlight=machine.Pin(_MH_DISPLAY_BACKLIGHT, machine.Pin.OUT),
            use_tiny_buf=use_tiny_buf,
            content_width=s_width,
            content_height=s_height,
            content_y = s_top,
            **kwargs,
            )
        Display.draw_overlays = True
        print("Display.__init__: calling first show()")
        self.show()
        print("Display.__init__: done")

    @staticmethod
    def _init_pin(target_pin, *args) -> machine.Pin | None:
        """For __init__: return a pin if an integer is given, or return None."""
        if target_pin is None:
            return None
        return machine.Pin(target_pin, *args)

    def _draw_overlays(self):
        """Call each overlay callback in Display.overlay_callbacks."""
        for callback in Display.overlay_callbacks:
            callback(self)

    def show(self):
        """Write changes to display."""
        if Display.draw_overlays:
            self._draw_overlays()
            Display.draw_overlays = False
        super().show()
        
    def deinit(self):
        """Release memory and resources, pull up CS."""
        
        try:
            self.reset()
            
            # 拉高 CS
            if hasattr(self, 'cs') and self.cs is not None:
                self.cs.value(1)
                self.cs.init(machine.Pin.OUT, value=1)
                
            self.set_power(False)
            
        except Exception as e:
            print(f"Error: {e}")
