"""MicroHydra Image Viewer with LVGL.

Version: 1.1 (fixed)

A full-featured image viewer for MicroHydra using LVGL for image decoding.
Supports BMP, JPG, and PNG formats.
Features zoom, pan, image info toggle, and directory navigation.
"""

import os
import time
import gc

from lib import display, userinput
from lib.hydra import config, loader

# Import LVGL
import lvgl as lv


def _init_decoder(*func_names):
    """Try each candidate init function name in order; return True on success."""
    for name in func_names:
        if hasattr(lv, name):
            try:
                getattr(lv, name)()
                print(f"Initialized decoder: {name}")
                return True
            except Exception as e:
                print(f"Found {name} but failed to init it: {e}")
    return False


# --- LVGL v8 / v9 compatibility shim ---
# v9 renamed several APIs this script relies on (img -> image, scr_act ->
# screen_active, set_zoom -> set_scale, etc). Resolve whichever name exists
# once, here, instead of hard-coding one version everywhere below.
_IMG_CLASS = getattr(lv, 'image', None) or lv.img
_SCR_ACT = getattr(lv, 'screen_active', None) or lv.scr_act
_DISP_GET_DEFAULT = getattr(lv, 'display_get_default', None) or getattr(lv, 'disp_get_default', None)
_IMG_HEADER_CLS = getattr(lv, 'image_header_t', None) or getattr(lv, 'img_header_t', None)
_IMG_DECODER_GET_INFO = getattr(lv, 'image_decoder_get_info', None) or getattr(lv, 'img_decoder_get_info', None)

if hasattr(lv, 'RESULT'):
    _RES_OK = lv.RESULT.OK
elif hasattr(lv, 'RES'):
    _RES_OK = lv.RES.OK
else:
    _RES_OK = None
print(f"_RES_OK = {_RES_OK}")

# Initialize all image decoders that this viewer needs.
# NOTE: each LVGL image decoder module (png, bmp, sjpg/tjpgd, gif, ...) usually
# needs its own explicit *_init() call even if the module was compiled into
# the firmware -- just being present is not enough.
if not _init_decoder('lodepng_init', 'png_init'):
    print("Warning: no PNG decoder available, .png files will fail to load")

if not _init_decoder('bmp_init'):
    print("Warning: no BMP decoder init found "
          "(some builds support BMP without an explicit init call)")

if not _init_decoder('tjpgd_init', 'libjpeg_turbo_init', 'jpg_init', 'sjpg_init'):
    print("Warning: no JPG decoder available, .jpg/.jpeg files will fail to load")


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ _CONSTANTS: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
_MH_DISPLAY_HEIGHT = const(135)
_MH_DISPLAY_WIDTH = const(240)
_DISPLAY_WIDTH_HALF = const(_MH_DISPLAY_WIDTH // 2)
_DISPLAY_HEIGHT_HALF = const(_MH_DISPLAY_HEIGHT // 2)

_CHAR_WIDTH = const(8)
_CHAR_HEIGHT = const(10)

# Supported image extensions
SUPPORTED_EXTENSIONS = ('.bmp', '.jpg', '.jpeg', '.png')

# Zoom levels (as multipliers)
ZOOM_LEVELS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0]

# If your LVGL port registers a filesystem driver under a drive letter
# (e.g. "S:" or "A:"), set it here. Leave as "" if img.set_src() already
# accepts plain posix-style paths on your board.
_FS_DRIVE_PREFIX = ""


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ GLOBAL_OBJECTS: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

# init object for accessing display
DISPLAY = display.Display()

# object for accessing microhydra config
CONFIG = config.Config()

# object for reading keypresses
INPUT = userinput.UserInput()
print ("HW done")
# LVGL objects
img_obj = None
info_objs = []  # List to store info overlay objects


# --------------------------------------------------------------------------------------------------
# -------------------------------------- function_definitions: -------------------------------------
# --------------------------------------------------------------------------------------------------

def _lvgl_path(filepath):
    """Prefix filepath with the configured LVGL fs drive letter, if any."""
    if _FS_DRIVE_PREFIX and not filepath.startswith(_FS_DRIVE_PREFIX):
        return _FS_DRIVE_PREFIX + filepath
    return filepath


def get_image_files(directory='/'):
    """Get list of image files in the current directory."""
    try:
        files = os.listdir(directory)
        image_files = []
        for f in files:
            f_lower = f.lower()
            if f_lower.endswith(SUPPORTED_EXTENSIONS):
                # Check if it's a file (not directory)
                try:
                    full_path = directory + '/' + f if directory != '/' else '/' + f
                    stat = os.stat(full_path)
                    if stat[0] & 0x4000:  # Check if directory
                        continue
                except Exception:
                    pass
                image_files.append(f)
        return sorted(image_files)
    except Exception as e:
        print(f"Error listing directory: {e}")
        return []


def get_directory_from_path(filepath):
    """Extract directory from a file path."""
    if '/' in filepath:
        return filepath.rsplit('/', 1)[0]
    else:
        return '/'


def get_filename_from_path(filepath):
    """Extract filename from a file path."""
    if '/' in filepath:
        return filepath.rsplit('/', 1)[1]
    else:
        return filepath


def load_image_with_lvgl(filepath):
    """Load an image using LVGL and return (img_obj, width, height)."""
    try:
        print(f"[img] load_image_with_lvgl: {filepath}")
        src = _lvgl_path(filepath)

        # Create image object
        print("[img] creating widget")
        img = _IMG_CLASS(_SCR_ACT())
        time.sleep_ms(10)

        # Set image source from file. NOTE: this is synchronous -- lv_image
        # decodes the header (and often the whole image) inside set_src()
        # itself, so no manual refresh/refr_now() call is needed here.
        # (A previous version called lv.refr_now(disp_get_default()) here;
        # on this board disp_get_default() returns None, and passing None
        # into that native call causes a hard Guru Meditation crash that
        # Python's try/except cannot catch. Removed.)
        print(f"[img] set_src: {src}")
        img.set_src(src)
        time.sleep_ms(10)  # give the UART time to flush the print above

        width = 0
        height = 0

        # Preferred: ask the widget for its own (decoded) content size
        try:
            width = img.get_self_width()
            height = img.get_self_height()
            print(f"[img] get_self_width/height -> {width}x{height}")
        except Exception as e:
            print(f"[img] get_self_width/height failed: {e}")

        # Fallback: ask the decoder directly for the image header,
        # in case the widget hasn't been laid out yet.
        if (width <= 0 or height <= 0) and _IMG_HEADER_CLS is not None and _IMG_DECODER_GET_INFO is not None:
            try:
                print("[img] falling back to image_decoder_get_info")
                header = _IMG_HEADER_CLS()
                res = _IMG_DECODER_GET_INFO(src, header)
                if _RES_OK is None or res == _RES_OK:
                    width = header.w
                    height = header.h
                print(f"[img] image_decoder_get_info -> {width}x{height}")
            except Exception as e:
                print(f"[img] image_decoder_get_info failed: {e}")

        # Last-resort default so the rest of the app doesn't divide by zero
        if width <= 0 or height <= 0:
            print(f"Warning: could not determine size of {filepath}, using default")
            width, height = 100, 100

        return img, width, height

    except Exception as e:
        print(f"Error loading image with LVGL: {e}")
        return None, 0, 0


def load_image(filepath):
    """Load an image using LVGL."""
    return load_image_with_lvgl(filepath)


def draw_image_lvgl(img, width, height, zoom, pan_x, pan_y):
    """Position and scale an LVGL image object."""
    if img is None or width <= 0 or height <= 0:
        return

    # Calculate scaled dimensions (for centering math only -- we do NOT
    # call set_size() with these, that would conflict with LVGL's own
    # zoom-based rendering)
    scaled_width = int(width * zoom)
    scaled_height = int(height * zoom)

    # Calculate top-left position, applying pan and centering
    x = (_MH_DISPLAY_WIDTH - scaled_width) // 2 + pan_x
    y = (_MH_DISPLAY_HEIGHT - scaled_height) // 2 + pan_y

    try:
        # Anchor scaling to the image's top-left corner so our x,y
        # positioning math (based on top-left corner) stays correct.
        try:
            img.set_pivot(0, 0)
        except Exception:
            pass

        # Apply zoom using LVGL's built-in fixed-point scale (256 == 100%)
        zoom_val = int(zoom * 256)
        if hasattr(img, 'set_zoom'):
            img.set_zoom(zoom_val)
        elif hasattr(img, 'set_scale'):
            img.set_scale(zoom_val)

        img.set_pos(x, y)

    except Exception as e:
        print(f"Error positioning LVGL image: {e}")


def clear_info_overlay():
    """Clear info overlay objects."""
    global info_objs
    for obj in info_objs:
        try:
            obj.delete()
        except Exception:
            pass
    info_objs = []


def create_info_overlay(filename, zoom, file_count, current_index, img_width, img_height):
    """Create info overlay using LVGL."""
    global info_objs

    clear_info_overlay()

    try:
        # Create background
        bg = lv.obj(_SCR_ACT())
        bg.set_size(_MH_DISPLAY_WIDTH, 30)
        bg.set_pos(0, 0)
        bg.set_style_bg_color(lv.color_hex(0x000000), 0)
        bg.set_style_bg_opa(180, 0)
        bg.set_style_radius(0, 0)
        info_objs.append(bg)

        # Info text
        info_text = f"{current_index+1}/{file_count} {filename}"
        label = lv.label(bg)
        label.set_text(info_text)
        label.set_pos(2, 2)
        label.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
        info_objs.append(label)

        # Zoom text
        zoom_text = f"{int(zoom*100)}%"
        zoom_label = lv.label(bg)
        zoom_label.set_text(zoom_text)
        zoom_label.set_pos(_MH_DISPLAY_WIDTH - len(zoom_text) * 7 - 2, 2)
        zoom_label.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
        info_objs.append(zoom_label)

        # Dimensions text
        dim_text = f"{img_width}x{img_height}"
        dim_label = lv.label(bg)
        dim_label.set_text(dim_text)
        dim_label.set_pos(_DISPLAY_WIDTH_HALF - len(dim_text) * 3, 2)
        dim_label.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
        info_objs.append(dim_label)

    except Exception as e:
        print(f"Error creating LVGL info overlay: {e}")
        clear_info_overlay()


def show_error_message(message, sub_message=""):
    """Display an error message."""
    # Clear screen
    _SCR_ACT().clean()

    # Create main label
    label = lv.label(_SCR_ACT())
    label.set_text(message)
    label.align(lv.ALIGN.CENTER, 0, -10)
    label.set_style_text_color(lv.color_hex(0xFFFFFF), 0)

    if sub_message:
        sub_label = lv.label(_SCR_ACT())
        sub_label.set_text(sub_message)
        sub_label.align(lv.ALIGN.CENTER, 0, 20)
        sub_label.set_style_text_color(lv.color_hex(0xCCCCCC), 0)


def wait_for_exit():
    """Wait for ESC key to exit."""
    while True:
        # Use get_new_keys() as specified
        keys = INPUT.get_new_keys()
        if 'ESC' in keys:
            return True
        time.sleep_ms(50)


def cleanup_image(img):
    """Clean up image object."""
    if img is not None:
        try:
            img.delete()
        except Exception:
            pass


# --------------------------------------------------------------------------------------------------
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ Main Loop: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
def main_loop():
    """Run the main loop of the program."""
    global img_obj, info_objs

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ INITIALIZATION: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    # Get command line arguments
    args = loader.get_args()
    start_file = args[0] if args else None
    print("opening file")
    # Default fallback image
    if not start_file:
        start_file = "/image/images.jpg"
        print(f"No file specified, using default: {start_file}")

    # Determine starting directory and file
    try:
        os.stat(start_file)
        current_dir = get_directory_from_path(start_file)
        start_filename = get_filename_from_path(start_file)
    except Exception:
        # If file doesn't exist, try to use as directory or fallback
        try:
            if start_file.endswith('/'):
                current_dir = start_file
            else:
                current_dir = start_file + '/' if not start_file.endswith('/') else start_file
            start_filename = None
        except Exception:
            try:
                current_dir = os.getcwd()
            except Exception:
                current_dir = '/'
            start_filename = None
            show_error_message("Invalid file path!", "Using current directory")
            time.sleep_ms(1000)

    # Normalize directory path
    if not current_dir.endswith('/'):
        current_dir += '/'

    # Get list of images in the directory
    image_files = get_image_files(current_dir)

    if not image_files:
        show_error_message("No images found!", "Press ESC to exit")
        wait_for_exit()
        return

    # Set starting index
    if start_filename and start_filename in image_files:
        current_index = image_files.index(start_filename)
    else:
        current_index = 0

    # State variables
    current_image = None
    img_width = 0
    img_height = 0
    filename = image_files[current_index]
    filepath = current_dir + filename

    zoom_index = 3  # Default to 1.0x
    zoom = ZOOM_LEVELS[zoom_index]
    pan_x = 0
    pan_y = 0

    show_info = True
    running = True

    # Load first image
    print(f"Loading: {filepath}")
    current_image, img_width, img_height = load_image(filepath)

    if current_image is None:
        show_error_message("Failed to load image!", "Press ESC to exit")
        wait_for_exit()
        return

    # Setup initial display
    draw_image_lvgl(current_image, img_width, img_height, zoom, pan_x, pan_y)

    # Show initial info
    if show_info:
        create_info_overlay(filename, zoom, len(image_files), current_index, img_width, img_height)
    print("looping")

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ MAIN LOOP: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    while running:
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ INPUT: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        # Get newly pressed keys (as specified)
        keys = INPUT.get_new_keys()

        for key in keys:
            # ESC - Exit
            if key == 'ESC':
                running = False
                break

            # ENTER - Toggle info
            elif key == 'ENT':
                show_info = not show_info
                if show_info:
                    create_info_overlay(filename, zoom, len(image_files), current_index, img_width, img_height)
                else:
                    clear_info_overlay()

            # + / - Zoom in/out
            elif key == 'KP_PLUS' or key == 'PLUS':
                if zoom_index < len(ZOOM_LEVELS) - 1:
                    zoom_index += 1
                    zoom = ZOOM_LEVELS[zoom_index]
                    pan_x = 0
                    pan_y = 0
                    if current_image:
                        draw_image_lvgl(current_image, img_width, img_height, zoom, pan_x, pan_y)
                        if show_info:
                            clear_info_overlay()
                            create_info_overlay(filename, zoom, len(image_files), current_index, img_width, img_height)

            elif key == 'KP_MINUS' or key == 'MINUS':
                if zoom_index > 0:
                    zoom_index -= 1
                    zoom = ZOOM_LEVELS[zoom_index]
                    pan_x = 0
                    pan_y = 0
                    if current_image:
                        draw_image_lvgl(current_image, img_width, img_height, zoom, pan_x, pan_y)
                        if show_info:
                            clear_info_overlay()
                            create_info_overlay(filename, zoom, len(image_files), current_index, img_width, img_height)

            # Arrow keys for panning
            elif key == 'LEFT':
                pan_x += 20
                if current_image:
                    draw_image_lvgl(current_image, img_width, img_height, zoom, pan_x, pan_y)

            elif key == 'RIGHT':
                pan_x -= 20
                if current_image:
                    draw_image_lvgl(current_image, img_width, img_height, zoom, pan_x, pan_y)

            elif key == 'UP':
                pan_y += 15
                if current_image:
                    draw_image_lvgl(current_image, img_width, img_height, zoom, pan_x, pan_y)

            elif key == 'DOWN':
                pan_y -= 15
                if current_image:
                    draw_image_lvgl(current_image, img_width, img_height, zoom, pan_x, pan_y)

            # PAGE UP - Previous image
            elif key == 'PAGEUP':
                if current_index > 0:
                    current_index -= 1
                    filename = image_files[current_index]
                    filepath = current_dir + filename
                    print(f"Loading: {filepath}")

                    # Clean up old image
                    cleanup_image(current_image)
                    current_image = None
                    clear_info_overlay()

                    # Load new image
                    current_image, img_width, img_height = load_image(filepath)

                    if current_image is not None:
                        pan_x = 0
                        pan_y = 0
                        zoom_index = 3
                        zoom = ZOOM_LEVELS[zoom_index]
                        draw_image_lvgl(current_image, img_width, img_height, zoom, pan_x, pan_y)
                        if show_info:
                            create_info_overlay(filename, zoom, len(image_files), current_index, img_width, img_height)
                    else:
                        current_index += 1
                        print("Failed to load image, skipping")
                    gc.collect()

            # PAGE DOWN - Next image
            elif key == 'PAGEDOWN':
                if current_index < len(image_files) - 1:
                    current_index += 1
                    filename = image_files[current_index]
                    filepath = current_dir + filename
                    print(f"Loading: {filepath}")

                    # Clean up old image
                    cleanup_image(current_image)
                    current_image = None
                    clear_info_overlay()

                    # Load new image
                    current_image, img_width, img_height = load_image(filepath)

                    if current_image is not None:
                        pan_x = 0
                        pan_y = 0
                        zoom_index = 3
                        zoom = ZOOM_LEVELS[zoom_index]
                        draw_image_lvgl(current_image, img_width, img_height, zoom, pan_x, pan_y)
                        if show_info:
                            create_info_overlay(filename, zoom, len(image_files), current_index, img_width, img_height)
                    else:
                        current_index -= 1
                        print("Failed to load image, skipping")
                    gc.collect()

        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ HOUSEKEEPING: ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        # Small delay to prevent CPU hogging
        time.sleep_ms(20)

    # Cleanup on exit
    cleanup_image(current_image)
    clear_info_overlay()


# Start the main loop
main_loop()