"""This module is responsible for combining device-specific input modules into a single, unified API.

This module also adds some fancy extra features to that input,
such as key repetition, and global keyboard shortcuts.

!IMPORTANT NOTE!
    The API connecting _keys and userinput is almost certainly going to change!
    Do not use the _keys module directly!
"""
import time
from lib.hydra.config import Config
from lib.display import Display
from lib.hydra.utils import get_instance
import machine
from . import _keys
from . import _touch
from . import vKey



# Used for drawing locked keys to display:
_PADDING = const(3)
_FONT_WIDTH = const(8)
_FONT_HEIGHT = const(8)
_BOX_HEIGHT = const(_FONT_HEIGHT + (_PADDING * 2) + 1)
_RADIUS = const((_BOX_HEIGHT - 1) // 2)



# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ UserInput: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
class UserInput(_keys.Keys):

    def __init__(
        self,
        *,
        hold_ms=600,
        repeat_ms=80,
        use_sys_commands=True,
        allow_locking_keys=False,
        skip_hardware_init=False,  # 新增标志
        **kwargs):
        """Initialize the input drivers with the given settings."""
        self.config = get_instance(Config)

        # screen sleep / backlight timeout
        # sleep_after is in seconds; 0 (or falsy) disables auto-sleep.
        self.sleep_after = self.config['sleep_after']
        self._last_touch_ms = time.ticks_ms()
        self._backlight_on = True

        # key repetition / locking keys
        self.tracker = {}
        self.hold_ms = hold_ms
        self.repeat_delta = hold_ms - repeat_ms

        self.locking_keys = allow_locking_keys
        self.locked_keys = []

        # enable system commands
        self.use_sys_commands = use_sys_commands

        # setup locked key overlay:
        Display.overlay_callbacks.append(self._locked_keys_overlay)

        # init _keys.Keys
        #super().__init__(**kwargs)
        if not skip_hardware_init:
            try:
                super().__init__(**kwargs)
                self.hardware_available = True
            except Exception as e:
                print(f"Hardware initialization failed: {e}")
                print("Continuing in software-only mode...")
                self.key_state = []
                self.hardware_available = False
        else:
            # 跳过硬件初始化，只设置必要属性
            print("Continuing in software-only mode...")
            print("Hardware input initialization skipped")
            self.key_state = []
  
        # mh_if kb_light:
        # # keyboard backlight control!
        # self.set_backlight(self.config["kb_light"])
        # mh_end_if

        
        # setup touch control!
        import axs5106
        from i2c import I2C
        touch_i2c_bus = I2C.Bus(host=0, sda=18, scl=19)
        touch_i2c = I2C.Device(touch_i2c_bus, axs5106.I2C_ADDR, axs5106.BITS)
        self.touch = _touch.Touch(i2c=touch_i2c)#, debug=True)
        self.get_touch_events = self.touch.get_touch_events
        self.get_current_points = self.touch.get_current_points

            
        self.vkey = vKey.VKey(
        scrn=Display.instance.scrn,
        screen_width=320,
        screen_height=172,
        content_x=Display.instance.content_x,
        content_y=Display.instance.content_y,
        content_width=Display.instance.content_width,
        content_height=Display.instance.content_height,
        locked_keys=self.locked_keys,
    )

    def __new__(cls, **kwargs):  # noqa: ARG003, D102
        if not hasattr(cls, 'instance'):
          cls.instance = super().__new__(cls)
        return cls.instance



    @micropython.viper
    def _get_new_keys(self):  # noqa: ANN202
        """Viper component of get_new_keys."""
        # using viper for this part is probably not critical for speed.
        # but in my experience viper tends to be much faster any time
        # iteration is involved (also seems to use less ram).
        # and so when something like this can easily be viper-ized,
        # I tend to just do it.

        tracker = self.tracker
        time_now = int(time.ticks_ms())
        hold_ms = int(self.hold_ms)
        repeat_delta = int(self.repeat_delta)

        # Iterate over pressed keys, keeping keys not in the tracker.
        # And, check for device-specific keys that should always be "new".
        keylist = []
        for key in self.key_state:
            if key not in tracker \
            or key in _keys.ALWAYS_NEW_KEYS:
                keylist.append(key)  # noqa: PERF401

        # Test if tracked keys have been held enough to repeat.
        # If they have, we can repeat them and reset the repeat time.
        # Also, don't repeat modifier` keys.
        for key, key_time in tracker.items():
            if key not in _keys.MOD_KEYS \
            and int(time.ticks_diff(time_now, key_time)) >= hold_ms:
                keylist.append(key)
                tracker[key] = time_now - repeat_delta

        return keylist


    def get_new_keys(self) -> list:
        # Feeds the sleep/backlight timer regardless of which method the
        # main loop calls each tick (see _poll_touch_and_check_sleep docstring).
        touch_points = self._poll_touch_and_check_sleep()

        keylist = []
        if getattr(self, 'hardware_available', False):

            try:
                self.populate_tracker()
                if self.locking_keys:
                    self.handle_locking_keys()
                self.get_pressed_keys()
                keylist = self._get_new_keys()
                if self.use_sys_commands:
                    self.system_commands(keylist)
            except Exception as e:
                # MicroPython 的简单错误处理
                #print("no key input:", e)
                pass
        else:
            try:
                vkey_out = self.vkey.update(touch_points)
                if vkey_out:
                    keylist = vkey_out
            except Exception as e:
                print("no touch key input:", e)
                pass
            #print(f"key={keylist}")
        return keylist

    def _register_touch_activity(self):
        self._last_touch_ms = time.ticks_ms()
        if not self._backlight_on:
            Display.instance.set_backlight(True)
            self._backlight_on = True

    def _check_sleep(self):
        """Turn off the backlight once `sleep_after` seconds pass with no touch."""
        # Anything below 5s is treated as "feature disabled" (includes 0/None).
        if not self.sleep_after or self.sleep_after < 5:
            return

        elapsed_ms = time.ticks_diff(time.ticks_ms(), self._last_touch_ms)
        if self._backlight_on and elapsed_ms >= (self.sleep_after * 1000):
            Display.instance.set_backlight(False)
            self._backlight_on = False

    def _poll_touch_and_check_sleep(self):
        try:
            touch_points = self.get_current_points()
        except Exception:
            touch_points = None

        if touch_points:
            self._register_touch_activity()

        self._check_sleep()
        return touch_points



    def get_pressed_keys(self) -> list[str]:
        # Same reasoning as in get_new_keys(): some main loops call
        # get_pressed_keys() directly every tick instead of get_new_keys(),
        # so the sleep timer needs to be fed here too.
        touch_points = self._poll_touch_and_check_sleep()

        if not getattr(self, 'hardware_available', False):
            self.vkey.update(touch_points)
            self.key_state = self.vkey.get_pressed_keys(
                force_fn=('FN' in self.locked_keys),
                force_shift=('SHIFT' in self.locked_keys),
                )
            return self.key_state
        return super().get_pressed_keys(
            force_fn=('FN' in self.locked_keys),
            force_shift=('SHIFT' in self.locked_keys),
            )


    def get_mod_keys(self) -> list[str]:
        """Return modifier keys that are being held, or that are currently locked."""
        return [key for key in self.key_state + self.locked_keys if key in _keys.MOD_KEYS]


    def populate_tracker(self):
        """Move currently pressed keys to tracker."""
        # add new keys
        for key in self.key_state:
            if key not in self.tracker:

                # mod keys lock rather than repeat
                if self.locking_keys \
                and key in _keys.MOD_KEYS:
                    # True means key can be locked
                    self.tracker[key] = True
                else:
                    # Remember when key was pressed for key-repeat behavior
                    self.tracker[key] = time.ticks_ms()

        # remove keys that aren't being pressed from tracker
        # (mod keys are removed in handle_locking_keys)
        for key in self.tracker:
            if key not in self.key_state \
            and (self.locking_keys is False
            or key not in _keys.MOD_KEYS):
                self.tracker.pop(key)


    def handle_locking_keys(self):
        """Handle 'locking' behaviour of modifier keys."""
        tracker = self.tracker
        locked_keys = self.locked_keys

        # iterate over mod keys in tracker:
        for key in tracker:
            if key in _keys.MOD_KEYS:

                # pre-fetch for easier readability:
                tracker_val = tracker[key]
                in_locked_keys = key in locked_keys
                is_being_pressed = key in self.key_state

                # when mod key is pressed, val is True
                # becomes False when any other key is pressed at the same time
                # if not pressed and still True, then lock the mod key
                # remove locked mod key when pressed again.

                if tracker_val: # is True
                    if is_being_pressed:
                        # key is being pressed and val is True
                        if in_locked_keys:
                            # key already in locked keys, must have been pressed twice.
                            locked_keys.remove(key)
                            tracker[key] = False
                            # Redraw the locked keys overlay
                            Display.draw_overlays = True

                        elif len(self.key_state) > 1:
                            # multiple keys are being pressed together, dont lock this key
                            tracker[key] = False
                    else:
                        # key has just been released and should be locked
                        locked_keys.append(key)
                        tracker.pop(key)
                        # Redraw the locked keys overlay
                        Display.draw_overlays = True

                # tracker val is False
                elif not is_being_pressed:
                    # if not being pressed and not locking, then just remove it
                    tracker.pop(key)


    def system_commands(self, keylist: list):
        """Check for system commands in the keylist and apply to config."""
        if 'OPT' in self.key_state:
            self.ext_dir_keys(keylist)

            # system commands are bound to 'OPT': remove OPT and apply commands
            if 'OPT' in keylist:
                keylist.remove('OPT')

            for key, setting, val in (
                ('m', 'ui_sound', bool),
                ('UP', 'volume', 1),
                ('DOWN', 'volume', -1),
                ('LEFT', 'brightness', -1),
                ('RIGHT', 'brightness', 1)):
                if key in keylist:
                    keylist.remove(key)
                    if val is bool:
                        self.config[setting] = not self.config[setting]
                    else:
                        self.config[setting] = (self.config[setting] + val) % 11
                    if setting == 'brightness':
                        Display.instance.set_brightness(self.config['brightness'])

            if "q" in keylist:
                self.config.save()
                machine.RTC().memory("")
                machine.reset()

            # mh_if kb_light:
            # if "b" in keylist:
            #     self.config["kb_light"] = not self.config["kb_light"]
            #     self.set_backlight(self.config["kb_light"])
            #     keylist.remove('b')
            # mh_end_if


    def _locked_keys_overlay(self, display):
        """Draw currently locked keys to the display."""
        width = display.width

        for key_txt in self.locked_keys:
            box_width = (len(key_txt) * _FONT_WIDTH)
            x = width - box_width - _PADDING - _RADIUS
            key_idx = _keys.MOD_KEYS.index(key_txt)
            txt_clr = key_idx % 3
            bg_clr = (key_idx % 3) + 6
            ex_clr = 11 + key_idx

            # bg
            display.rect(x, 1, box_width, _BOX_HEIGHT, display.palette[bg_clr], fill=True)
            display.ellipse(x, _RADIUS + 1, _RADIUS, _RADIUS, display.palette[bg_clr], fill=True, m=6)
            display.ellipse(x + box_width, _RADIUS + 1, _RADIUS, _RADIUS, display.palette[bg_clr], fill=True, m=9)

            # outline
            display.hline(x, 1, box_width, display.palette[ex_clr])
            display.hline(x, _BOX_HEIGHT, box_width, display.palette[ex_clr])
            display.ellipse(x, _RADIUS + 1, _RADIUS, _RADIUS, display.palette[ex_clr], fill=False, m=6)
            display.ellipse(x + box_width, _RADIUS + 1, _RADIUS, _RADIUS, display.palette[ex_clr], fill=False, m=9)

            display.text(key_txt, x, _PADDING + 2, display.palette[txt_clr])
            width = x - _RADIUS - _PADDING