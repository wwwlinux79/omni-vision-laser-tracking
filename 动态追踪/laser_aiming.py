"""激光动态追踪系统 - 先前进再追踪（分开进行）"""

from time import sleep, monotonic

# 控制参数
YAW_THRESHOLD = 20       # 水平误差阈值（像素），超过此值才旋转底盘
PITCH_THRESHOLD = 8      # 垂直误差阈值（像素），超过此值才调整俯仰
FORWARD_SPEED = 0.8      # 前进速度
FORWARD_DURATION = 1.5   # 每次前进持续时间（秒）：0.5→1.5
YAW_DURATION_MS = 120    # 底盘旋转持续时间（毫秒）
PITCH_GAIN = 0.12        # 俯仰调整增益
MAX_PITCH_STEP = 8       # 单次俯仰最大调整量（微秒）
FRAME_DELAY = 0.20       # 帧间延迟（秒）

# 从 control.py 导入的常量（扩展云台范围）
PITCH_MIN_US = 700       # 允许云台抬得更高（原1000）
PITCH_MAX_US = 1500
PITCH_CENTER_US = 1300
PITCH_UP_SIGN = -1


def aim_step(board, set_pitch, marker, spot, center, pitch, speed):
    """
    执行一步瞄准调整（原地旋转 + 俯仰，和静态追踪一样）

    Args:
        board: Pico控制板连接
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
    error_x = marker[0] - target_x  # 水平误差
    error_y = marker[1] - target_y  # 垂直误差

    # 调试输出
    print(f"[追踪] 中心=({target_x:.1f},{target_y:.1f}) 标记=({marker[0]:.1f},{marker[1]:.1f}) 误差=({error_x:.1f},{error_y:.1f})", flush=True)

    # 水平调整：原地旋转底盘（和静态追踪一样）
    yaw = 0
    if board is not None and abs(error_x) > YAW_THRESHOLD:
        yaw = (speed * 0.80) if error_x > 0 else -(speed * 0.80)  # 提高到0.80，确保有足够扭矩
        duration_ms = max(115, min(YAW_DURATION_MS + round(abs(error_x) * 0.13), 145))
        from control import step
        step(board, (0, 0, yaw), duration_ms)  # 只旋转，不前进
        print(f"[水平] 误差={error_x:.1f}px, yaw={yaw:.2f}, 持续{duration_ms}ms", flush=True)

    # 垂直调整：调整俯仰舵机
    new_pitch = pitch
    if abs(error_y) > PITCH_THRESHOLD:
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

    print(f"激光动态追踪系统启动（底盘持续前进 + 视觉追踪），起始俯仰={pitch} µs", flush=True)
    print(f"识别标记ID={args.marker_id}，前进速度={FORWARD_SPEED} 水平阈值={YAW_THRESHOLD}px 垂直阈值={PITCH_THRESHOLD}px", flush=True)
    print(f"⚠️  底盘将持续前进，请确保前方有足够空间！", flush=True)

    with open_camera() as camera:
        # 预热摄像头
        capture_frame(camera)

        # 云台移动到起始位置
        set_pitch(pitch)
        sleep(0.5)

        print("3秒后开始瞄准...", flush=True)
        sleep(3)

        # 底盘前进测试
        print("底盘前进测试（speed=0.8）...", flush=True)
        if board is not None:
            from control import step
            # 前进1秒
            for i in range(5):
                step(board, (0, 0.8, 0), 200)
                sleep(0.2)
        print("开始追踪...", flush=True)

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

            print("先前进到目标位置，然后开始持续追踪", flush=True)

            # ========== 前进到目标位置（只执行一次）==========
            print(f"\n[前进] 前进{FORWARD_DURATION}秒到达目标位置...", flush=True)
            if board is not None:
                from control import step
                forward_steps = int(FORWARD_DURATION / 0.2)
                for i in range(forward_steps):
                    step(board, (0, FORWARD_SPEED, 0), 200)
                    sleep(0.2)

                # 停车！确保底盘完全停止
                board.exec("_basic_robot.stop()")
                sleep(0.3)  # 等待停稳

            print("[追踪] 开始持续追踪（和静态追踪一样）...", flush=True)

            try:
                while True:
                    # ========== 持续追踪（原地，和静态追踪一样）==========
                    # 捕获当前帧
                    frame = capture_frame(camera)

                    # 检测标记和激光光点
                    marker, spot = detector.positions(frame, reference=reference)

                    # 执行追踪调整
                    if marker is not None:
                        pitch = aim_step(board, set_pitch, marker, spot, center, pitch, args.speed)
                    else:
                        print("[追踪] 未检测到标记", flush=True)

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
