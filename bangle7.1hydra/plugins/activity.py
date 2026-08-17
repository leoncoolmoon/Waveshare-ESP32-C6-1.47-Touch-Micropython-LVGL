# plugins/activity.py -- 心率/计步等活动状态同步
# 没有 VIEW_ID，纯粹的事件处理器，不出现在底栏/滑动列表里。
# 对应早期单文件版本里的 on_activity()/on_activity_fetch()。

def handle_event(t, data, state, screen):
    """处理来自 Gadgetbridge 的活动相关事件：
      - "act"：hrm/stp/int 三个字段（心率、计步、间隔）
      - "actfetch"：ts 字段（要抓取的活动记录起始时间戳）
      - "listRecs" / "fetchRec"：id 字段（记录 id）
    """
    if t == "act":
        hrm = data.get("hrm")
        steps = data.get("stp")
        screen.set_status("activity_active", hrm or steps)
        return True

    if t == "actfetch":
        screen.show_temp_message(f"Activity fetch: {data.get('ts', 0)}", 2)
        return True

    if t == "listRecs" or t == "fetchRec":
        screen.show_temp_message(f"Activity fetch: {data.get('id', '')}", 2)
        return True

    return False
