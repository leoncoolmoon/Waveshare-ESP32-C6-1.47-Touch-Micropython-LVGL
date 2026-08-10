# bangle_utils.py -- 纯逻辑辅助函数，没有任何硬件依赖

_MAX_BITMAP_SIZE = (4096)  # 限制位图大小，避免内存问题

_MAX_NOTIF_BITMAP_TOTAL = (20480)  # 所有通知的位图总共最多 20KB

def _bitmap_bytes(field):
    if isinstance(field, dict) and "pixels" in field:
        try:
            return len(field["pixels"])
        except Exception:
            return 0
    return 0

def wrap_text(text, max_chars):
    lines = []
    for raw_line in text.split("\n"):
        while len(raw_line) > max_chars:
            # 尝试在空格处断行
            split_pos = raw_line[:max_chars].rfind(" ")
            if split_pos == -1:
                split_pos = max_chars
            lines.append(raw_line[:split_pos])
            raw_line = raw_line[split_pos:].lstrip()
        if raw_line:
            lines.append(raw_line)
    return lines

def truncate(text, max_len):
    text = text.replace("\n", " ")
    if len(text) > max_len:
        return text[:max_len - 1] + "…"
    return text

def civil_from_days(days_since_1970):
    z = days_since_1970 + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    y = y + 1 if m <= 2 else y
    return y, m, d

def epoch_to_rtc_tuple(epoch_seconds, tz_offset_hours=0):
    local_epoch = int(epoch_seconds + tz_offset_hours * 3600)
    days = local_epoch // 86400
    remainder = local_epoch % 86400
    hour = remainder // 3600
    minute = (remainder % 3600) // 60
    second = remainder % 60
    year, month, day = civil_from_days(days)
    weekday = (days + 3) % 7  # 1970-01-01 (day 0) was a Thursday -> index 3
    return (year, month, day, weekday, hour, minute, second, 0)

def decode_espruino_image_string(raw_bytes):
    if len(raw_bytes) < 4 or raw_bytes[0] != 0:
        return None

    # 检查位图大小，避免内存问题
    if len(raw_bytes) > _MAX_BITMAP_SIZE:
        print(f"[BLE] Bitmap too large ({len(raw_bytes)} bytes), skipping")
        return None

    width = raw_bytes[1]
    height = raw_bytes[2]
    bpp_byte = raw_bytes[3]
    bpp = bpp_byte & 0x7F
    has_transparent = bool(bpp_byte & 0x80)

    idx = 4
    transparent = None
    if has_transparent:
        transparent = raw_bytes[idx]
        idx += 1

    total_bits = width * height * bpp
    expected_pixel_bytes = (total_bits + 7) // 8
    if expected_pixel_bytes > _MAX_BITMAP_SIZE:
        print(f"[BLE] Bitmap pixel data too large ({expected_pixel_bytes} bytes), skipping")
        return None

    pixels = raw_bytes[idx:idx + expected_pixel_bytes]
    if len(pixels) != expected_pixel_bytes:
        print(
            f"[BLE] image pixel data size mismatch: got {len(pixels)}, "
            f"expected {expected_pixel_bytes} (w={width} h={height} bpp={bpp})"
        )

    return {
        "width": width,
        "height": height,
        "bpp": bpp,
        "transparent": transparent,
        "pixels": pixels,
    }

def extract_atob_fields(text):
    marker = 'atob("'
    bitmaps_b64 = {}
    out = []
    pos = 0
    text_len = len(text)

    while True:
        idx = text.find(marker, pos)
        if idx == -1:
            out.append(text[pos:])
            break

        colon = text.rfind(":", pos, idx)
        if colon == -1:
            out.append(text[pos:idx + len(marker)])
            pos = idx + len(marker)
            continue

        k = colon - 1
        while k >= 0 and (text[k].isalpha() or text[k].isdigit() or text[k] == "_"):
            k -= 1
        key = text[k + 1:colon]

        end_quote = text.find('"', idx + len(marker))
        if end_quote == -1 or text[end_quote:end_quote + 2] != '")':
            out.append(text[pos:])
            pos = text_len
            break

        out.append(text[pos:k + 1])
        b64 = text[idx + len(marker):end_quote]

        # 检查 base64 大小，避免解码超大位图
        if len(b64) > _MAX_BITMAP_SIZE:
            print(f"[BLE] Skipping oversized bitmap field '{key}' ({len(b64)} bytes)")
            bitmaps_b64[key] = None
        else:
            bitmaps_b64[key] = b64
        out.append(f'{key}:""')
        pos = end_quote + 2

    return "".join(out), bitmaps_b64

class GBBitmap:
    def __init__(self, width, height, bpp, pixels, palette):
        self.WIDTH = width
        self.HEIGHT = height
        self.BPP = bpp
        self.BITMAP = pixels
        self.PALETTE = palette


# 同样的道理，见 display_core.py 底部的说明。
__all__ = [
    '_MAX_BITMAP_SIZE', '_MAX_NOTIF_BITMAP_TOTAL', '_bitmap_bytes', 'wrap_text', 'truncate', 'civil_from_days',
    'epoch_to_rtc_tuple', 'decode_espruino_image_string', 'extract_atob_fields', 'GBBitmap',
]