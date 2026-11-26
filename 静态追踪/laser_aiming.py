"""激光常亮自动瞄准系统 - 实时调整云台让激光照射到标记中心"""

from time import sleep, monotonic

# 控制参数
YAW_THRESHOLD = 20       # 水平误差阈值（像素），超过此值才旋转底盘（降低：30→20，更精确）
PITCH_THRESHOLD = 8      # 垂直误差阈值（像素），超过此值才调整俯仰
YAW_DURATION_MS = 120    # 底盘旋转持续时间（毫秒）（增加：80→120，确保转动）
PITCH_GAIN = 0.12        # 俯仰调整增益（提高速度：0.05→0.12）
MAX_PITCH_STEP = 8       # 单次俯仰最大调整量（微秒）（提高速度：4→8）
FRAME_DELAY = 0.20       # 帧间延迟（秒）（增加：0.12→0.20，给底盘足够时间）

# 从 control.py 导入的常量（扩展云台范围）
PITCH_MIN_US = 700       # 允许云台抬得更高（原1000）
PITCH_MAX_US = 1500
PITCH_CENTER_US = 1300
PITCH_UP_SIGN = -1


def aim_step(board, set_pitch, marker, spot, center, pitch, speed):
    """
    执行一步瞄准调整（云台俯仰 + 底盘水平旋转）

    重要：激光和摄像头固定在云台上一起转动
    目标：调整云台俯仰和底盘旋转让标记移动到画面中心

    Args:
        board: Pico控制板连接（用于底盘旋转）
        set_pitch: 俯仰舵机控制函数
        marker: 标记中心位置 (x, y)
        spot: 激光光点位置 (x, y)
        center: 图像中心 (x, y)
        pitch: 当前俯仰脉宽
        speed: 底盘旋转速度

    Returns:
        新的俯仰脉宽值
    """
    # 需要标记位置
    if marker is None:
        return pitch

    # 目标：让标记移动到画面中心
    target_x = center[0]
    target_y = center[1]

    # 计算标记相对于画面中心的误差
    error_x = marker[0] - target_x  # 水平误差（标记相对中心）
    error_y = marker[1] - target_y  # 垂直误差（标记相对中心）

    # 调试输出
    print(f"[调试] 中心=({target_x:.1f},{target_y:.1f}) 标记=({marker[0]:.1f},{marker[1]:.1f}) 误差=({error_x:.1f},{error_y:.1f})", flush=True)

    # 水平调整：旋转底盘
    yaw = 0
    if board is not None and abs(error_x) > YAW_THRESHOLD:
        # error_x > 0: 标记在中心右侧，需要向右旋转（正方向）
        # error_x < 0: 标记在中心左侧，需要向左旋转（负方向）
        yaw = (speed * 0.85) if error_x > 0 else -(speed * 0.85)  # 提高旋转速度到85%（确保扭矩）
        duration_ms = max(120, min(YAW_DURATION_MS + round(abs(error_x) * 0.15), 150))  # 确保最小120ms
        from control import step
        step(board, (0, 0, yaw), duration_ms)
        print(f"[水平] 误差={error_x:.1f}px, yaw={yaw:.2f}, 持续{duration_ms}ms", flush=True)

    # 垂直调整：调整俯仰舵机
    new_pitch = pitch
    if abs(error_y) > PITCH_THRESHOLD:
        # error_y > 0: 标记在中心下方，需要向下调整云台（让标记向上移动到中心）
        # error_y < 0: 标记在中心上方，需要向上调整云台（让标记向下移动到中心）
        delta = max(-MAX_PITCH_STEP, min(MAX_PITCH_STEP,
                    round(-PITCH_UP_SIGN * error_y * PITCH_GAIN)))
        new_pitch = max(PITCH_MIN_US, min(PITCH_MAX_US, pitch + delta))
        print(f"[俯仰] 误差={error_y:.1f}px, delta={delta}, 新脉宽={new_pitch}", flush=True)

    if new_pitch != pitch:
        set_pitch(new_pitch)
        print(f"[执行] 俯仰调整: {pitch} → {new_pitch}", flush=True)

    return new_pitch


def run_laser_aiming(args, board, set_pitch):
    """
    激光常亮瞄准主循环

    Args:
        args: 命令行参数
        board: Pico控制板连接
        set_pitch: 俯仰舵机控制函数
    """
    from vision_tracking import HEIGHT, WIDTH, Detector, capture_frame, open_camera
    from laser_control import laser_switch

    detector = Detector(args.marker_id)
    center = (WIDTH / 2, HEIGHT / 2)
    pitch = args.pitch_us if args.pitch_us is not None else PITCH_CENTER_US

    print(f"激光瞄准系统启动（云台俯仰 + 底盘水平旋转），起始俯仰={pitch} µs", flush=True)
    print(f"识别标记ID={args.marker_id}，调整参数: 水平阈值={YAW_THRESHOLD}px 垂直阈值={PITCH_THRESHOLD}px", flush=True)

    with open_camera() as camera:
        # 预热摄像头
        capture_frame(camera)

        # 云台移动到起始位置
        set_pitch(pitch)
        sleep(0.5)

        print("3秒后开始瞄准...", flush=True)
        sleep(3)

        # 先捕获参考帧（激光关闭状态的背景）
        print("捕获背景参考帧（激光关闭）...", flush=True)
        reference = capture_frame(camera)

        with laser_switch() as trigger:
            # 激光常亮
            trigger.on()
            print("激光已开启（常亮模式）", flush=True)
            sleep(0.3)  # 等待激光稳定

            consecutive_detections = 0
            last_report_time = monotonic()
            last_spot = None  # 记录上一次的光点位置
            last_marker = None  # 记录上一次的标记位置
            marker_lost_count = 0  # 标记丢失计数

            print("开始实时瞄准，按 Ctrl+C 停止", flush=True)

            try:
                while True:
                    # 捕获当前帧
                    frame = capture_frame(camera)

                    # 检测标记和激光光点
                    marker, spot = detector.positions(frame, reference=reference)

                    # 标记丢失处理
                    if marker is None:
                        marker_lost_count += 1
                        if marker_lost_count <= 100 and last_marker is not None:
                            # 标记刚丢失，使用上次的位置继续（最多100帧，约15秒）
                            marker = last_marker
                            if marker_lost_count % 10 == 0:  # 每10帧报告一次
                                print(f"[警告] 标记丢失{marker_lost_count}帧，使用上次位置 y={marker[1]:.1f}", flush=True)
                        else:
                            if marker_lost_count == 101:
                                print(f"[警告] 标记持续丢失，停止调整", flush=True)
                    else:
                        # 检测到标记，重置计数
                        if marker_lost_count > 0:
                            print(f"[恢复] 重新检测到标记 y={marker[1]:.1f}", flush=True)
                        marker_lost_count = 0
                        last_marker = marker

                    # 光点位置连续性检查：如果光点突然跳得太远，可能是检测到另一个光点，忽略它
                    if marker is not None and spot is not None and last_spot is not None:
                        # 计算光点位置变化
                        spot_jump = abs(spot[1] - last_spot[1])
                        # 如果光点垂直跳动超过60像素，可能是误检测切换到另一个光点
                        if spot_jump > 60:
                            print(f"[过滤] 光点从y={last_spot[1]:.1f}跳到y={spot[1]:.1f}(跳动{spot_jump:.1f}px)，忽略", flush=True)
                            spot = last_spot  # 使用上次的光点位置

                    # 更新上次的光点位置（只在成功检测到时更新）
                    if spot is not None:
                        last_spot = spot

                    # 状态报告（每秒一次）
                    now = monotonic()
                    if now - last_report_time >= 1.0:
                        marker_px = tuple(round(v, 1) for v in marker) if marker else None
                        spot_px = tuple(round(v, 1) for v in spot) if spot else None
                        offset = None
                        if marker and spot:
                            offset = tuple(round(spot[i] - marker[i], 1) for i in range(2))
                        print(f"标记={marker_px} 光点={spot_px} 误差={offset} 俯仰={pitch}µs",
                              flush=True)
                        last_report_time = now

                    # 执行瞄准调整（只需要标记，不需要光点）
                    if marker is not None:
                        consecutive_detections += 1
                        # 连续检测到2帧后才开始调整，提高稳定性
                        if consecutive_detections >= 2:
                            pitch = aim_step(board, set_pitch, marker, spot, center,
                                           pitch, args.speed)
                    else:
                        consecutive_detections = 0

                    # 控制帧率
                    sleep(FRAME_DELAY)

            except KeyboardInterrupt:
                print("\n瞄准已停止", flush=True)
            finally:
                # 激光会在 laser_switch 上下文退出时自动关闭
                print("激光已关闭", flush=True)


def print_aiming_params():
    """打印当前瞄准参数配置"""
    print("=" * 50)
    print("激光瞄准参数配置:")
    print(f"  水平误差阈值: {YAW_THRESHOLD} 像素")
    print(f"  垂直误差阈值: {PITCH_THRESHOLD} 像素")
    print(f"  俯仰调整增益: {PITCH_GAIN}")
    print(f"  最大俯仰步长: {MAX_PITCH_STEP} 微秒")
    print(f"  底盘旋转时长: {YAW_DURATION_MS} 毫秒")
    print(f"  帧处理延迟: {FRAME_DELAY} 秒")
    print("=" * 50)
