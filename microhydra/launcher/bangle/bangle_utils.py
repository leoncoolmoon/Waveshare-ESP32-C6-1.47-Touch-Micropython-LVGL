# bangle_utils.py -- 纯逻辑辅助函数，没有任何硬件依赖，方便单独测试。
#
# 这两个常量故意不用 const(...)：它们被列在 __all__ 里，是给别的模块
# `from bangle_utils import *` 用的；MicroPython 的 const() 只保证同一
# 文件内的编译期内联替换，一旦编译器发现文件内部所有引用都已经被内联
# 成字面量，模块级的这条赋值就可能被当成死代码优化掉，运行时压根不会
# 有这个模块属性，外部 import 就会 AttributeError（拆多文件之前这个
# 问题不会暴露，因为原来只有同一个文件内部会用到它）。
_MAX_BITMAP_SIZE = 4096  # 限制位图大小，避免内存问题

_MAX_NOTIF_BITMAP_TOTAL = 20480  # 所有通知的位图总共最多 20KB（预留，暂未使用）


def _bitmap_bytes(field):
    if isinstance(field, dict) and "pixels" in field:
        try:
            return len(field["pixels"])
        except Exception:
            return 0
    return 0


def wrap_text(text, max_chars):
    """Wrap long text to the screen's max character width."""
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
    """Shorten text for preview."""
    text = text.replace("\n", " ")
    if len(text) > max_len:
        return text[:max_len - 1] + "…"
    return text


def civil_from_days(days_since_1970):
    """Convert day count to (year, month, day)."""
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


def days_from_civil(year, month, day):
    """Inverse of civil_from_days(): (year, month, day) -> days since
    1970-01-01. Same algorithm family (Howard Hinnant's civil_from_days),
    just run backwards. Used by the calendar plugin to figure out which
    weekday a given month's 1st falls on, without needing RTC/epoch math.
    """
    y = year - 1 if month <= 2 else year
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    m_adj = month + 9 if month <= 2 else month - 3
    doy = (153 * m_adj + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def weekday_from_days(days_since_1970):
    """0=Monday..6=Sunday, matching epoch_to_rtc_tuple()'s convention
    (1970-01-01 was a Thursday -> index 3)."""
    return (days_since_1970 + 3) % 7


def days_in_month(year, month):
    """Number of days in a given (year, month), leap years included."""
    if month == 2:
        is_leap = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
        return 29 if is_leap else 28
    return (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]


def epoch_to_rtc_tuple(epoch_seconds, tz_offset_hours=0):
    """Convert Unix epoch to an ESP32 machine.RTC()-style tuple:
    (year, month, day, weekday, hours, minutes, seconds, subseconds).

    weekday: 0=Monday..6=Sunday (confirmed against real Gadgetbridge alarm
    data -- a Monday-only alarm came back with rep=1, i.e. bit 0 = Monday).
    """
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
    """Parse decoded Espruino image string with size limits."""
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
    """
    提取 JSON 中的 atob("...") 字段
    输入: {"t":"notify","subject":atob("ACQWgQAAA..."),"body":atob("ACQWgQAAA...")}
    输出: ({"t":"notify","subject":"","body":""}, {'subject': 'ACQWgQAAA...', 'body': 'ACQWgQAAA...'})
    """
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

        # 找到 atob(" 前面的冒号
        colon = text.rfind(":", pos, idx)
        if colon == -1:
            # 没有冒号，跳过这个 atob
            out.append(text[pos:idx + len(marker)])
            pos = idx + len(marker)
            continue

        # 提取字段名：从冒号往前找，直到遇到 ", 但要从 " 里面提取
        # 例如: "subject":atob("...")
        # 先找到冒号前的双引号
        end_quote = text.rfind('"', pos, colon)
        if end_quote == -1:
            out.append(text[pos:idx + len(marker)])
            pos = idx + len(marker)
            continue
        
        # 再找到字段名开始的双引号
        start_quote = text.rfind('"', pos, end_quote - 1)
        if start_quote == -1:
            out.append(text[pos:idx + len(marker)])
            pos = idx + len(marker)
            continue
        
        # 字段名在 start_quote 和 end_quote 之间
        field_name = text[start_quote + 1:end_quote]
        
        # 找到 atob(" 的结束位置 ")
        end_atob = text.find('")', idx + len(marker))
        if end_atob == -1:
            # 没有找到结束，数据不完整
            out.append(text[pos:])
            pos = text_len
            break
        
        # 提取 base64 数据
        b64 = text[idx + len(marker):end_atob]
        
        # 保存解码后的数据
        if len(b64) > _MAX_BITMAP_SIZE:
            print(f"[BLE] Skipping oversized bitmap field '{field_name}' ({len(b64)} bytes)")
            bitmaps_b64[field_name] = None
        else:
            bitmaps_b64[field_name] = b64
        
        # 构建输出：保留从 pos 到 start_quote 的内容，然后用 "field_name":"" 替换 atob("...")
        out.append(text[pos:start_quote + 1])  # 保留到字段名的开始双引号
        out.append(f'{field_name}":"",')  # 注意这里：字段名 + ":""
        
        pos = end_atob + 2  # 跳过 ") 
        
        # 调试输出
        print(f"[BLE DEBUG] Extracted field: '{field_name}'")

    result = "".join(out)
    
    # 清理多余的逗号
    # 移除 ,, 双逗号
    while ',,' in result:
        result = result.replace(',,', ',')
    # 移除 {, 这种情况
    result = result.replace('{,', '{')
    # 移除 ,} 这种情况
    result = result.replace(',}', '}')
    
    return result, bitmaps_b64

class GBBitmap:
    def __init__(self, width, height, bpp, pixels, palette):
        self.WIDTH = width
        self.HEIGHT = height
        self.BPP = bpp
        self.BITMAP = pixels
        self.PALETTE = palette

__all__ = [
    '_MAX_BITMAP_SIZE', '_MAX_NOTIF_BITMAP_TOTAL', '_bitmap_bytes', 'wrap_text', 'truncate',
    'civil_from_days', 'days_from_civil', 'weekday_from_days', 'days_in_month',
    'epoch_to_rtc_tuple', 'decode_espruino_image_string',
    'extract_atob_fields', 'GBBitmap',
]
