"""This is an automatically generated module that contains the MH config for this specific device.

`Device.vals` contains a dictionary of constants for this device.
`Device.feats` contains a tuple of features that this device has, with the final value being the device name.

Usage examples:
```
width = Device.display_width
height = Device.display_height

if 'touchscreen' in Device:
    get_touch()
```
"""

class Device:
    vals = {'name': 'CARDPUTER', 'mh_version': (2, 5, 0), 'batt_adc': 10, 'display_backlight': 23, 'display_baudrate': 40000000, 'display_cs': 14, 'display_dc': 15, 'display_height': 135, 'display_miso': None, 'display_mosi': 2, 'display_reset': 22, 'display_rotation': 1, 'display_sck': 1, 'display_spi_id': 1, 'display_width': 240, 'i2s_id': 1, 'i2s_sck': 41, 'i2s_sd': 42, 'i2s_ws': 43, 'sdcard_cs': 4, 'sdcard_miso': 3, 'sdcard_mosi': 2, 'sdcard_sck': 1, 'sdcard_slot': 2}
    feats = ('keyboard', 'display', 'i2s_speaker', 'pdm_microphone', 'ir_blaster', 'wifi', 'bluetooth', 'CARDPUTER')

    @staticmethod
    def __getattr__(name:str):
        return Device.vals[name]

    @staticmethod
    def __contains__(val:str):
        return val in Device.feats

Device = Device()
