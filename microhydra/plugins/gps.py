# plugins/gps.py -- GPS 定位数据同步 + 手机查询 GPS 开关状态
# 没有 VIEW_ID，纯粹的事件处理器，不出现在底栏/滑动列表里。
# 对应早期单文件版本里的 on_gps()/on_gps_power()。


def handle_event(t, data, state, screen):
    """处理来自 Gadgetbridge 的 GPS 相关事件：
      - "gps"：一次定位数据，lat/lon/... 就是原始字段名
      - "gps_power"：手机主动告诉手表 GPS 开关状态，status 字段
      - "is_gps_active"：手机在问"你现在 GPS 是不是开着的"，这条
        消息本身不带 status 字段，是一个独立的查询类型
    """
    if t == "gps":
        screen.set_status("gps_active", True)
        lat = data.get("lat")
        lon = data.get("lon")
        if lat is not None and lon is not None:
            screen.show_temp_message(f"📍 GPS: {lat:.4f}, {lon:.4f}", 2)
        return True

    if t == "gps_power":
        screen.set_status("gps_active", data.get("status", False))
        return True

    if t == "is_gps_active":
        # screen.ble 是 main_loop 里挂上去的 ble_bridge 实例，插件靠它
        # 才能主动往手机发东西，回一条跟设备端原始协议一致的消息。
        if screen.ble is not None:
            screen.ble.send_command({"t": "gps_power", "status": screen.status["gps_active"]})
        else:
            print("[GPS] screen.ble 不可用，无法回复 is_gps_active 查询")
        return True

    return False
