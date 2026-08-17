"""MicroHydra Time Sync Tool.

A small test/demo app based on the MicroHydra app template,
adapted from the launcher's wifi + NTP time-syncing logic.

Shows connection/sync status and the current RTC time on screen,
and prints progress to the console (REPL) as it happens.

Controls:
    ENT - sync now (quick shortcut)
    G0  - open settings menu (edit wifi/timezone, set date/time, exit)
"""

import time
import machine
import network
import ntptime
from lib import display, userinput
from lib.hydra import config, loader, popup


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ _CONSTANTS: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
_MH_DISPLAY_HEIGHT = const(135)
_MH_DISPLAY_WIDTH = const(240)
_DISPLAY_WIDTH_HALF = const(_MH_DISPLAY_WIDTH // 2)

_CHAR_WIDTH = const(8)
_CHAR_WIDTH_HALF = const(_CHAR_WIDTH // 2)

_MAX_WIFI_ATTEMPTS = const(1000)
_MAX_NTP_ATTEMPTS = const(10)


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ GLOBAL_OBJECTS: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

DISPLAY = display.Display()
CONFIG = config.Config()
INPUT = userinput.UserInput()
RTC = machine.RTC()
overlay = popup.UIOverlay()

# wifi loves to give unknown runtime errors, just try it twice:
try:
    NIC = network.WLAN(network.STA_IF)
except RuntimeError as e:
    print(e)
    try:
        NIC = network.WLAN(network.STA_IF)
    except RuntimeError as e:
        NIC = None
        print("Wifi WLAN object couldn't be created. Gave this error:", e)


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ GLOBAL STATE: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SYNCING_CLOCK = False
SYNC_NTP_ATTEMPTS = 0
CONNECT_WIFI_ATTEMPTS = 0
IP_ADDRESS = ""
STATUS_TEXT = "ENT: sync   G0: menu"


# --------------------------------------------------------------------------------------------------
# -------------------------------------- function_definitions: -------------------------------------
# --------------------------------------------------------------------------------------------------

def apply_timezone_offset(timestamp_sec: int, tz_offset_hours: int) -> tuple:
    """
    Apply timezone offset to a UTC timestamp and return a normalized time tuple.
    
    Args:
        timestamp_sec: UTC timestamp in seconds since epoch
        tz_offset_hours: Timezone offset in hours (e.g., -5, 8)
    
    Returns:
        Tuple in RTC.datetime format: (year, month, day, weekday, hour, minute, second, subsec)
    """
    adjusted_ts = timestamp_sec + (tz_offset_hours * 3600)
    tm = time.localtime(adjusted_ts)
    # time.localtime returns: (year, month, day, hour, minute, second, weekday, yearday)
    # RTC.datetime expects: (year, month, day, weekday, hour, minute, second, subsec)
    return (tm[0], tm[1], tm[2], tm[6], tm[3], tm[4], tm[5], 0)


def set_rtc_from_utc(tz_offset_hours: int) -> bool:
    """
    Set the RTC to local time based on UTC time from NTP.
    Returns True if successful, False otherwise.
    """
    try:
        # Get current UTC timestamp
        utc_ts = time.time()
        # Apply timezone offset and get normalized local time
        local_time = apply_timezone_offset(utc_ts, tz_offset_hours)
        RTC.datetime(local_time)
        return True
    except Exception as e:
        print(f"Failed to set RTC with timezone: {e}")
        return False


def start_sync():
    """Kick off the wifi connection + clock sync process."""
    global SYNCING_CLOCK, SYNC_NTP_ATTEMPTS, CONNECT_WIFI_ATTEMPTS, STATUS_TEXT, IP_ADDRESS  # noqa: PLW0603

    if NIC is None:
        STATUS_TEXT = "No NIC available"
        print(STATUS_TEXT)
        return

    if CONFIG['wifi_ssid'] == '':
        STATUS_TEXT = "No wifi_ssid set"
        print("No wifi_ssid set in config. Open the menu (G0) to set one.")
        return

    SYNC_NTP_ATTEMPTS = 0
    CONNECT_WIFI_ATTEMPTS = 0
    IP_ADDRESS = ""
    SYNCING_CLOCK = True
    STATUS_TEXT = "Starting..."
    print(f"Starting wifi + clock sync (SSID: {CONFIG['wifi_ssid']})...")

    if not NIC.active():  # turn on wifi if it isn't already
        NIC.active(True)

    if not NIC.isconnected():  # try connecting
        try:
            NIC.connect(CONFIG['wifi_ssid'], CONFIG['wifi_pass'])
            print(f"Connecting to '{CONFIG['wifi_ssid']}'...")
        except OSError as e:
            print("Error while starting wifi connection:", e)


def try_sync_clock():
    """Try syncing the RTC using ntptime. Call this repeatedly from the main loop."""
    global SYNCING_CLOCK, SYNC_NTP_ATTEMPTS, CONNECT_WIFI_ATTEMPTS, IP_ADDRESS, STATUS_TEXT  # noqa: PLW0603

    if NIC.isconnected():
        # only update/print the IP once we actually have one:
        if not IP_ADDRESS:
            IP_ADDRESS = NIC.ifconfig()[0]
            print(f"Connected. IP address: {IP_ADDRESS}")

        STATUS_TEXT = f"IP: {IP_ADDRESS}\nSyncing NTP..."

        try:
            ntptime.settime()
        except OSError as e:
            SYNC_NTP_ATTEMPTS += 1
            print(f"NTP attempt {SYNC_NTP_ATTEMPTS} failed: {e}")

        # Check if NTP sync succeeded (RTC year should not be 2000)
        if RTC.datetime()[0] != 2000:
            # Apply timezone offset properly with normalization
            tz_offset = CONFIG["timezone"]
            if set_rtc_from_utc(tz_offset):
                NIC.disconnect()
                NIC.active(False)  # shut off wifi
                SYNCING_CLOCK = False
                STATUS_TEXT = f"Synced!\nIP: {IP_ADDRESS}"
                print(
                    f"RTC successfully synced to {RTC.datetime()} "
                    f"with {SYNC_NTP_ATTEMPTS} attempts."
                )
            else:
                STATUS_TEXT = "Timezone apply failed"
                print("Failed to apply timezone offset")

        elif SYNC_NTP_ATTEMPTS >= _MAX_NTP_ATTEMPTS:
            NIC.disconnect()
            NIC.active(False)  # shut off wifi
            SYNCING_CLOCK = False
            STATUS_TEXT = "NTP sync failed"
            print(f"Syncing RTC aborted after {SYNC_NTP_ATTEMPTS} NTP attempts")

    elif CONNECT_WIFI_ATTEMPTS >= _MAX_WIFI_ATTEMPTS:
        NIC.disconnect()
        NIC.active(False)  # shut off wifi
        SYNCING_CLOCK = False
        STATUS_TEXT = "Wifi connect failed"
        print(f"Connecting to wifi aborted after {CONNECT_WIFI_ATTEMPTS} loops")

    else:
        CONNECT_WIFI_ATTEMPTS += 1
        if CONNECT_WIFI_ATTEMPTS % 100 == 0:  # avoid spamming the console
            print(f"Still trying to connect wifi... ({CONNECT_WIFI_ATTEMPTS})")


def connect_wifi():
    """Connect to the configured wifi network (without running the full NTP sync)."""
    global STATUS_TEXT  # noqa: PLW0603

    if NIC is None:
        overlay.error("No wifi hardware available")
        return

    if CONFIG['wifi_ssid'] == '':
        overlay.error("No wifi_ssid set. Use 'Set Wifi SSID' first.")
        return

    if not NIC.active():  # connecting requires the radio to be on
        NIC.active(True)

    if NIC.isconnected():
        STATUS_TEXT = "Already connected"
        print("Already connected to wifi")
        return

    try:
        NIC.connect(CONFIG['wifi_ssid'], CONFIG['wifi_pass'])
        STATUS_TEXT = f"Connecting to\n{CONFIG['wifi_ssid']}..."
        print(f"Connecting to '{CONFIG['wifi_ssid']}'...")
    except OSError as e:
        STATUS_TEXT = "Connect failed"
        print("Error while connecting:", e)


def disconnect_wifi():
    """Disconnect from the current wifi network, but leave the radio on."""
    global STATUS_TEXT, SYNCING_CLOCK, IP_ADDRESS  # noqa: PLW0603

    if NIC is None or not NIC.isconnected():
        STATUS_TEXT = "Not connected"
        print("Not currently connected to wifi")
        return

    NIC.disconnect()
    SYNCING_CLOCK = False  # don't let an in-progress sync try to keep going
    IP_ADDRESS = ""
    STATUS_TEXT = "Disconnected"
    print("Disconnected from wifi")


def get_wifi_status_text() -> str:
    """Return a short human-readable string describing the current wifi state."""
    if NIC is None:
        return "Wifi: unavailable"
    if not NIC.active():
        return "Wifi: Off"
    if NIC.isconnected():
        ip = IP_ADDRESS or NIC.ifconfig()[0]
        return f"Wifi: On - {ip}"
    return "Wifi: On - not connected"


def toggle_wifi():
    """Turn the wifi radio on or off."""
    global SYNCING_CLOCK, STATUS_TEXT, IP_ADDRESS  # noqa: PLW0603

    if NIC is None:
        overlay.error("No wifi hardware available")
        return

    if NIC.active():
        NIC.disconnect()
        NIC.active(False)
        SYNCING_CLOCK = False
        IP_ADDRESS = ""
        STATUS_TEXT = "Wifi turned off"
        print("Wifi turned off")
    else:
        NIC.active(True)
        STATUS_TEXT = "Wifi turned on"
        print("Wifi turned on")


_MANUAL_ENTRY_OPTION = const("Manual entry...")


def scan_wifi_networks() -> list:
    """Scan for nearby wifi networks, returning a list of unique SSID strings.

    Sorted strongest signal first. Networks with blank/undecodable SSIDs
    (hidden networks) are skipped, since there'd be nothing to select.
    """
    if NIC is None:
        return []

    if not NIC.active():  # scanning requires the radio to be on
        NIC.active(True)

    try:
        scan_results = NIC.scan()
    except OSError as e:
        print("Wifi scan failed:", e)
        return []

    # scan_results entries look like: (ssid, bssid, channel, RSSI, authmode, hidden)
    strongest_rssi = {}
    for entry in scan_results:
        raw_ssid, _bssid, _channel, rssi = entry[0], entry[1], entry[2], entry[3]
        try:
            ssid = raw_ssid.decode()
        except UnicodeError:
            continue
        if not ssid:
            continue  # skip hidden/blank ssids, can't select them anyway

        # the same network's SSID can show up multiple times (multiple APs/channels);
        # just keep the strongest signal we saw for it:
        if ssid not in strongest_rssi or rssi > strongest_rssi[ssid]:
            strongest_rssi[ssid] = rssi

    return sorted(strongest_rssi, key=lambda s: strongest_rssi[s], reverse=True)


def set_wifi_ssid():
    """Let the user pick a nearby wifi network from a scan, or type one in manually."""
    old_ssid = CONFIG['wifi_ssid']

    overlay.draw_textbox("Scanning for networks...")
    DISPLAY.show()

    found_ssids = scan_wifi_networks()
    print(f"Found {len(found_ssids)} network(s): {found_ssids}")

    # always offer manual entry too, in case the network isn't found (hidden, out of range, etc):
    options = found_ssids + [_MANUAL_ENTRY_OPTION]
    chosen = overlay.popup_options(options, title="Select Wifi Network")

    if chosen is None:  # user cancelled (ESC/BSPC)
        return

    if chosen == _MANUAL_ENTRY_OPTION:
        new_ssid = overlay.text_entry(start_value=old_ssid, title="Wifi SSID:")
        if not new_ssid or new_ssid == old_ssid:
            return  # cancelled, or unchanged
    else:
        new_ssid = chosen

    CONFIG['wifi_ssid'] = new_ssid
    CONFIG.save()
    print(f"wifi_ssid set to '{new_ssid}'")

    # a new network likely needs its own password, so prompt for it right away:
    new_pass = overlay.text_entry(
        start_value=CONFIG['wifi_pass'],
        title=f"Password for '{new_ssid}':",
        )
    if new_pass != CONFIG['wifi_pass']:
        CONFIG['wifi_pass'] = new_pass
        CONFIG.save()
        print("wifi_pass updated")


def set_datetime_manually():
    """Prompt the user to manually enter a date and time, and apply it to the RTC."""
    year, month, day, _weekday, hour, minute, second, _sub = RTC.datetime()
    current_str = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

    new_str = overlay.text_entry(
        start_value=current_str,
        title="Date/Time (YYYY-MM-DD HH:MM:SS):",
        )

    # ESC cancels and returns current_str unchanged; DEL+ENT gives '':
    if not new_str or new_str == current_str:
        return

    try:
        date_part, time_part = new_str.strip().split(" ")
        y, m, d = (int(x) for x in date_part.split("-"))
        hh, mm, ss = (int(x) for x in time_part.split(":"))
    except (ValueError, IndexError):
        overlay.error("Couldn't parse date/time. Use YYYY-MM-DD HH:MM:SS")
        return

    RTC.datetime((y, m, d, 0, hh, mm, ss, 0))
    print(f"RTC manually set to {RTC.datetime()}")


def open_menu():
    """Open the settings menu for wifi/timezone/date-time."""
    global SYNCING_CLOCK  # noqa: PLW0603

    # pause any in-progress auto-sync while the user is editing settings:
    SYNCING_CLOCK = False

    wifi_toggle_label = "Turn Wifi Off" if (NIC and NIC.active()) else "Turn Wifi On"
    connect_toggle_label = "Disconnect" if (NIC and NIC.isconnected()) else "Connect"
    options = [
        "Sync Now",
        wifi_toggle_label,
        connect_toggle_label,
        "Set Wifi SSID",
        "Set Wifi Password",
        "Set Timezone",
        "Set Date/Time",
        "Exit to Launcher",
        ]
    option = overlay.popup_options(options, title="Time Sync Settings")

    if option == "Sync Now":
        start_sync()

    elif option in ("Turn Wifi On", "Turn Wifi Off"):
        toggle_wifi()

    elif option == "Connect":
        connect_wifi()

    elif option == "Disconnect":
        disconnect_wifi()

    elif option == "Set Wifi SSID":
        set_wifi_ssid()

    elif option == "Set Wifi Password":
        old_pass = CONFIG['wifi_pass']
        new_pass = overlay.text_entry(start_value=old_pass, title="Wifi Password:")
        if new_pass and new_pass != old_pass:
            CONFIG['wifi_pass'] = new_pass
            CONFIG.save()
            print("wifi_pass updated")

    elif option == "Set Timezone":
        old_tz = str(CONFIG['timezone'])
        new_tz = overlay.text_entry(
            start_value=old_tz,
            title="Timezone (hour offset, e.g. -5, 8):",
            )
        if new_tz and new_tz != old_tz:
            try:
                CONFIG['timezone'] = int(new_tz)
                CONFIG.save()
                print(f"timezone set to {CONFIG['timezone']}")
            except ValueError:
                overlay.error("Timezone must be a whole number")

    elif option == "Set Date/Time":
        set_datetime_manually()

    elif option == "Exit to Launcher":
        loader.launch_app("/launcher/launcher")

    # note: we deliberately do NOT reset STATUS_TEXT to a default message here -
    # whatever the handler above set (e.g. "Connecting to X...", "Disconnected")
    # should stay visible on screen until something else changes it.


# --------------------------------------------------------------------------------------------------
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Main Loop: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
def main_loop():
    """Run the main loop of the program.

    Runs forever (until program is closed, or exits to the launcher).
    """

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ INITIALIZATION: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    print("Time Sync Tool started.")
    print("ENT = sync now, G0 = settings menu")

    # kick off syncing automatically on start, like the launcher does on boot
    start_sync()

    while True:

        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ INPUT: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        keys = INPUT.get_new_keys()

        if keys:
            if "ENT" in keys and not SYNCING_CLOCK:
                start_sync()
            elif "G0" in keys:
                open_menu()
                # note: no manual "restore screen" call needed here -
                # the graphics section below does a full DISPLAY.fill() + redraw
                # unconditionally, every loop, so overlay leftovers vanish next frame.

        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ WIFI / NTP SYNC: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        if SYNCING_CLOCK:
            try_sync_clock()

        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ MAIN GRAPHICS: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        # clear framebuffer
        DISPLAY.fill(CONFIG.palette[2])

        # current RTC time
        year, month, day, _weekday, hour, minute, second, _sub = RTC.datetime()
        time_str = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

        DISPLAY.text(
            text=time_str,
            x=_DISPLAY_WIDTH_HALF - (len(time_str) * _CHAR_WIDTH_HALF),
            y=32,
            color=CONFIG.palette[8],
            )

        # status text (may be multiple lines, split on \n)
        for idx, line in enumerate(STATUS_TEXT.split("\n")):
            DISPLAY.text(
                text=line,
                x=_DISPLAY_WIDTH_HALF - (len(line) * _CHAR_WIDTH_HALF),
                y=56 + (idx * 16),
                color=CONFIG.palette[6],
                )

        # wifi radio status (on/off, connected/ip)
        wifi_status = get_wifi_status_text()
        DISPLAY.text(
            text=wifi_status,
            x=_DISPLAY_WIDTH_HALF - (len(wifi_status) * _CHAR_WIDTH_HALF),
            y=94,
            color=CONFIG.palette[9] if (NIC and NIC.active()) else CONFIG.palette[5],
            )

        # small footer with current wifi ssid/timezone config
        footer = f"SSID:{CONFIG['wifi_ssid']}  TZ:{CONFIG['timezone']}"
        DISPLAY.text(
            text=footer,
            x=_DISPLAY_WIDTH_HALF - (len(footer) * _CHAR_WIDTH_HALF),
            y=_MH_DISPLAY_HEIGHT - 12,
            color=CONFIG.palette[4],
            )

        # write framebuffer to display
        DISPLAY.show()

        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ HOUSEKEEPING: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        time.sleep_ms(10)


# start the main loop
main_loop()