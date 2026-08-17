"""This simple module configures and mounts an SDCard."""
#from .sdcard import _SDCard as _SDCard
import machine
import os
from lib import hardware



class SDCard:
    """SDCard control."""

    def __init__(self):
        """Initialize the SDCard."""
        try:
            spi_bus = hardware.get_spi_bus()
            self.sd = machine.SDCard( spi_bus=spi_bus, cs=hardware.MH_SDCARD_CS,  freq=hardware.MH_SDCARD_FREQ_REG)
        except Exception as e:
            print(f"SDcard initialization failed: {e}")
            print("Continuing...")

               

    def mount(self):
        """Mount the SDCard."""
        if "sd" in os.listdir("/"):
            return
        try:
            os.mount(self.sd, '/sd')
        except (OSError, NameError, AttributeError) as e:
            print(f"Could not mount SDCard: {e}")


    def deinit(self):
        """Unmount and deinit the SDCard."""
        os.umount('/sd')
        # mh_if not shared_sdcard_spi:
        #self.sd.deinit()
        # mh_end_if
