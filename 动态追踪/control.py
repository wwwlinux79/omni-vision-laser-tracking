"""Control the three-wheel robot from the Raspberry Pi."""

import argparse
import subprocess
import sys
from contextlib import ExitStack, contextmanager, suppress
from functools import partial
from math import copysign, cos, pi, radians, sin
from pathlib import Path
from time import monotonic, sleep

from mpremote.transport import TransportError, TransportExecError
from mpremote.transport_serial import SerialTransport
from serial.tools.list_ports import comports

from laser_control import MAX_LIGHT_SECONDS, PULSE_SECONDS, test_laser
from pico_control import MAX_STEP_MS

# Wheel position angles measured clockwise from the front of the chassis.
# Use the physical locations of motor channels 1, 2, and 3 in that order.
WHEEL_ANGLES_DEG = (0, 120, 240)
MOTOR_POLARITY = (1, -1, 1)
SERVO_GPIO = 18  # BCM18, physical pin 12 on the Raspberry Pi.
PITCH_CENTER_US = 1500
PITCH_MIN_US = 1000  # Verified upward endpoint on this gimbal.
PITCH_MAX_US = 1600
PITCH_UP_SIGN = -1  # A shorter pulse raises this camera.
MANUAL_PITCH_START_US = 1300
MANUAL_PITCH_MIN_US = 800
PITCH_MOVE_SECONDS = 5.0
TILT_MOVE_SECONDS = 8.0
PITCH_STEP_SECONDS = 0.02
DEFAULT_MOTION_SECONDS = 3.0
DEFAULT_MOTOR_SPEED = 0.60
TRANSLATION_SPEED = 0.95
TRANSLATION_START_FRACTION = 0.55
PICO_SOURCE = Path(__file__).with_name("pico_control.py")
MOVES = {
    "forward": (0, 1, 0),
    "backward": (0, -1, 0),
    "left": (-1, 0, 0),
    "right": (1, 0, 0),
    "cw": (0, 0, 1),
    "ccw": (0, 0, -1),
}
DRIVE_MODES = ("demo", "drive", *MOVES)
MOTION_TEST_STEPS = (
    ("forward", "前进"),
    ("backward", "后退"),
    ("left", "左横移"),
    ("right", "右横移"),
    ("cw", "顺时针自转"),
    ("ccw", "逆时针自转"),
)
MOTION_TEST_PAUSE_SECONDS = 0.5
FORWARD_LOCATE_SECONDS = 1.5


def wheel_duties(right, forward, clockwise, speed=1, min_duty=0):
    """Mix body-relative axes for tangential omni wheels spaced 120 degrees apart."""
    duties = []
    for angle, polarity in zip(WHEEL_ANGLES_DEG, MOTOR_POLARITY):
        theta = radians(angle)
        tangential = right * cos(theta) - forward * sin(theta)
        duties.append(polarity * (tangential + clockwise))
    scale = max(1, *(abs(duty) for duty in duties))
    pwm = []
    for duty in duties:
        fraction = duty / scale
        if round(65535 * fraction) == 0:
            pwm.append(0)
            continue
        magnitude = min_duty + (speed - min_duty) * abs(fraction)
        pwm.append(round(copysign(65535 * magnitude, fraction)))
    return tuple(pwm)


def pico_port(port):
    if port:
        return port
    matches = [item.device for item in comports() if item.vid == 0x2E8A]
    if len(matches) != 1:
        raise RuntimeError("需要找到一块 Pico。请连接 Pico 或用 --port 指定串口。")
    return matches[0]


@contextmanager
def connect_robot(port):
    board = SerialTransport(pico_port(port))
    board.serial.timeout = 1
    board.serial.write_timeout = 2
    try:
        board.enter_raw_repl(soft_reset=False)
        board.exec(PICO_SOURCE.read_bytes())
        board.exec("if '_basic_robot' in globals():\n    _basic_robot.close()\n_basic_robot = Robot()")
        try:
            yield board
        except BaseException:
            # Interrupt any active move before asking the Pico to clean up.
            with suppress(OSError, TransportError):
                board.enter_raw_repl(soft_reset=False)
                board.exec("_basic_robot.close()")
            raise
        else:
            board.exec("_basic_robot.close()")
    finally:
        try:
            if board.in_raw_repl:
                board.exit_raw_repl()
        finally:
            board.close()


@contextmanager
def connect_gimbal():
    import pigpio

    controller = pigpio.pi("localhost", show_errors=False)
    try:
        if not controller.connected:
            raise RuntimeError("无法连接本机的 pigpiod 舵机服务，请先启动 pigpiod")
        set_pitch = partial(controller.set_servo_pulsewidth, SERVO_GPIO)
        try:
            yield set_pitch
        finally:
            set_pitch(0)
    except pigpio.error as error:
        raise RuntimeError(f"树莓派 GPIO18 舵机控制失败，{error}") from error
    finally:
        controller.stop()


def motor_command(axes, milliseconds, speed, min_duty=0):
    wheels = wheel_duties(*axes, speed=speed, min_duty=min_duty)
    return f"_basic_robot.step({wheels!r}, {milliseconds!r})"


def drive_axes(args):
    if args.mode not in ("demo", "drive"):
        return MOVES.get(args.mode, (0, 0, 0))

    default_axes = MOVES["forward"] if args.mode == "demo" else (0, 0, 0)
    return tuple(
        default if axis is None else axis
        for default, axis in zip(default_axes, (args.vx, args.vy, args.yaw))
    )


def motion_power(axes, args):
    right, forward, clockwise = axes
    translating = bool(right or forward) and clockwise == 0
    speed = args.speed
    if speed is None:
        if translating:
            speed = TRANSLATION_SPEED
        else:
            speed = 0.80 if args.mode == "demo" else DEFAULT_MOTOR_SPEED
    min_duty = args.min_duty
    if min_duty is None:
        min_duty = speed * TRANSLATION_START_FRACTION if translating else 0
    return speed, min_duty


def step(board, axes, milliseconds, speed=1):
    board.exec(motor_command(axes, milliseconds, speed))


def sweep_pitch(set_pitch, start_us, target_us, seconds=PITCH_MOVE_SECONDS):
    steps = max(1, round(seconds / PITCH_STEP_SECONDS))
    started_at = monotonic()
    for index in range(1, steps + 1):
        fraction = (1 - cos(pi * index / steps)) / 2
        pulse_us = round(start_us + (target_us - start_us) * fraction)
        set_pitch(pulse_us)
        remaining = started_at + seconds * index / steps - monotonic()
        if remaining > 0:
            sleep(remaining)


def run_manual(board, args, set_pitch):
    if args.mode == "stop":
        board.exec("_basic_robot.stop()")
        print("底盘已停车", flush=True)
        return

    print("3 秒后启动", flush=True)
    sleep(3)
    pitch = args.pitch_us
    if args.mode in ("demo", "tilt") and pitch is None:
        pitch = MANUAL_PITCH_MIN_US

    milliseconds = round(args.seconds * 1000)
    if board is not None:
        command = motor_command(drive_axes(args), milliseconds, args.speed, args.min_duty)
    if pitch is not None:
        start_pitch = args.start_pitch_us if args.mode == "tilt" else MANUAL_PITCH_START_US
        sweep_seconds = TILT_MOVE_SECONDS if args.mode == "tilt" else PITCH_MOVE_SECONDS
        print(f"云台起始脉宽 {start_pitch} µs", flush=True)
        set_pitch(start_pitch)
        if pitch != start_pitch:
            sleep(0.3)
            print(f"云台用 {sweep_seconds:g} 秒缓动至 {pitch} µs", flush=True)
            if board is not None:
                board.exec_raw_no_follow(command)
            sweep_pitch(set_pitch, start_pitch, pitch, sweep_seconds)
            if board is not None:
                output, error = board.follow(timeout=MAX_STEP_MS / 1000 + 2)
                if error:
                    raise TransportExecError(output, error.decode(errors="replace"))
                print("动作完成，底盘已停车", flush=True)
                return
    if board is None:
        sleep(args.seconds)
        print("俯仰测试完成", flush=True)
    else:
        board.exec(command)
        print("动作完成，底盘已停车", flush=True)


def run_motion_test(board, args):
    milliseconds = round(args.seconds * 1000)
    print("3 秒后开始底盘顺序点测，每个动作结束后停车", flush=True)
    sleep(3)
    for mode, label in MOTION_TEST_STEPS:
        axes = MOVES[mode]
        speed, min_duty = motion_power(axes, args)
        print(f"{label}，{milliseconds} 毫秒", flush=True)
        board.exec(motor_command(axes, milliseconds, speed, min_duty))
        sleep(MOTION_TEST_PAUSE_SECONDS)
    board.exec("_basic_robot.stop()")
    print("顺序点测完成，底盘已停车", flush=True)


def run_forward_locate(args):
    """前进1.5秒，停车后启动激光追踪"""
    project = Path(__file__).resolve().parent.parent
    basic_control = project / "基本功能" / "control.py"
    static_aim = project / "静态追踪" / "start_laser_aim.sh"

    # 第一步：连续前进1.5秒（平滑）
    forward_cmd = [sys.executable, str(basic_control), "forward"]

    # 只在参数有值时添加
    if args.port:
        forward_cmd.extend(["--port", args.port])

    # 速度参数
    speed = args.speed if args.speed else 0.8
    forward_cmd.extend(["--speed", str(speed)])

    # 最小占空比（提高启动扭矩）
    min_duty = args.min_duty if args.min_duty else 0.55
    forward_cmd.extend(["--min-duty", str(min_duty)])

    # 前进时间
    forward_cmd.extend(["--seconds", str(FORWARD_LOCATE_SECONDS)])

    print(f"连续前进 {FORWARD_LOCATE_SECONDS} 秒（速度={speed}, 最小占空比={min_duty}）...", flush=True)
    subprocess.run(forward_cmd, check=True)

    # 第二步：快速启动激光追踪（完全复用静态追踪）
    aim_cmd = [str(static_aim)]

    if args.marker_id is not None:
        aim_cmd.extend(["--marker-id", str(args.marker_id)])

    if args.port:
        aim_cmd.extend(["--port", args.port])

    subprocess.run(aim_cmd, check=True)


def track_step(board, set_pitch, marker, center, pitch, speed):
    error_x = marker[0] - center[0]
    error_y = marker[1] - center[1]
    yaw = 0
    duration_ms = 120
    if board is not None and abs(error_x) > 24:
        yaw = speed if error_x > 0 else -speed
        duration_ms = min(180, 65 + round(abs(error_x) * 0.45))
    new_pitch = pitch
    if abs(error_y) > 16:
        delta = max(-10, min(10, round(-PITCH_UP_SIGN * error_y * 0.12)))
        new_pitch = max(PITCH_MIN_US, min(PITCH_MAX_US, pitch + delta))
    if new_pitch != pitch:
        set_pitch(new_pitch)
    if yaw:
        step(board, (0, 0, yaw), duration_ms)
    return new_pitch


def run_camera(args, board=None, set_pitch=None):
    from vision_tracking import HEIGHT, WIDTH, Detector, capture_frame, open_camera, report_detection

    detector = Detector(args.marker_id)
    center = (WIDTH / 2, HEIGHT / 2)
    pitch = args.pitch_us if args.pitch_us is not None else PITCH_CENTER_US
    consecutive_frames = 0
    with open_camera() as camera:
        capture_frame(camera)
        if set_pitch is not None:
            print("3 秒后开始跟踪", flush=True)
            sleep(3)
            if board is None:
                print(f"连续识别到标记后再调整云台，起始参考脉宽 {pitch} µs", flush=True)
            else:
                set_pitch(pitch)
                sleep(0.35)
        while True:
            marker, spot = detector.positions(capture_frame(camera))
            report_detection(marker, spot)

            # Each correction stops the wheels before the next frame is read.
            consecutive_frames = consecutive_frames + 1 if marker is not None else 0
            if set_pitch is not None and consecutive_frames >= 2:
                pitch = track_step(board, set_pitch, marker, center, pitch, args.speed)
                if board is None:
                    print(f"标记垂直误差={marker[1] - center[1]:+.1f} px 云台={pitch} µs", flush=True)
            sleep(0.12)


def main():
    parser = argparse.ArgumentParser(description="三轮全向底盘、俯仰舵机和 ArUco 视觉跟踪")
    parser.add_argument("mode", choices=(*DRIVE_MODES, "stop", "motion-test", "forward-locate", "tilt", "observe", "track",
                                         "tilt-track", "laser-test", "laser-aim"),
                        help="forward-locate 连续前进 1.5 秒后停车定位，motion-test 点测底盘，"
                             "tilt 手动俯仰，observe 读画面，track 跟踪标记，"
                             "tilt-track 停车俯仰跟踪，laser-test 点测开关和光点")
    parser.add_argument("--port", help="Pico 串口，例如 /dev/ttyACM0")
    parser.add_argument("--vx", type=float, help="demo 或 drive 的横移分量，向右为正，范围 -1 到 1")
    parser.add_argument("--vy", type=float, help="demo 或 drive 的前进分量，向前为正，范围 -1 到 1")
    parser.add_argument("--yaw", type=float, help="demo 或 drive 的自转分量，顺时针为正，范围 -1 到 1")
    parser.add_argument("--speed", type=float,
                        help=f"电机占空比上限比例，平移默认 {TRANSLATION_SPEED:g}，"
                             f"自转点测默认 {DEFAULT_MOTOR_SPEED:g}")
    parser.add_argument("--min-duty", type=float,
                        help=f"手动动作的车轮起转补偿，平移默认为占空比上限的 {TRANSLATION_START_FRACTION * 100:g}%%")
    parser.add_argument("--seconds", type=float,
                        help=f"底盘每个动作默认 {DEFAULT_MOTION_SECONDS:g} 秒，tilt 时为到位后保持时长（默认 1 秒）")
    parser.add_argument("--pitch-us", type=int,
                        help=f"舵机目标脉宽，tilt 和 demo 默认到 {MANUAL_PITCH_MIN_US} 微秒")
    parser.add_argument("--start-pitch-us", type=int,
                        help=f"tilt 的当前脉宽，默认 {MANUAL_PITCH_START_US} 微秒，只向脉宽较小的方向缓动")
    parser.add_argument("--marker-id", type=int, default=0, help="DICT_4X4_50 标记编号，默认 0")
    parser.add_argument("--camera", action="store_true", help="手动动作时同步显示摄像头读数")
    parser.add_argument("--no-camera", action="store_true", help="laser-test 时仅测试 GPIO17 亮灯")
    parser.add_argument("--light-seconds", type=float,
                        help=f"laser-test 单次脉冲时长，默认 {PULSE_SECONDS:g} 秒，最多 {MAX_LIGHT_SECONDS:g} 秒")
    args = parser.parse_args()
    if args.no_camera and args.mode != "laser-test":
        parser.error("--no-camera 用于 laser-test")
    if args.light_seconds is not None:
        if args.mode != "laser-test":
            parser.error("--light-seconds 用于 laser-test")
        if not PULSE_SECONDS <= args.light_seconds <= MAX_LIGHT_SECONDS:
            parser.error(f"--light-seconds 必须在 {PULSE_SECONDS:g} 到 {MAX_LIGHT_SECONDS:g} 之间")
    if args.mode == "forward-locate" and (args.seconds is not None or args.pitch_us is not None or args.camera):
        parser.error("forward-locate 固定前进 1.5 秒，停车后开启相机定位")
    if args.seconds is None:
        args.seconds = DEFAULT_MOTION_SECONDS if args.mode in (*DRIVE_MODES, "motion-test") else 1.0
    axes = (args.vx, args.vy, args.yaw)
    if any(axis is not None for axis in axes) and args.mode not in ("demo", "drive"):
        parser.error("--vx、--vy 和 --yaw 用于 demo 或 drive")
    if any(axis is not None and not -1 <= axis <= 1 for axis in axes):
        parser.error("--vx、--vy 和 --yaw 必须在 -1 到 1 之间")
    move = MOVES["forward"] if args.mode == "forward-locate" else drive_axes(args)
    speed_limit, min_duty = motion_power(move, args)
    if not 0 <= speed_limit <= 1:
        parser.error("--speed 必须在 0 到 1 之间")
    if args.min_duty is not None and not 0 <= args.min_duty <= speed_limit:
        parser.error(f"--min-duty 必须在 0 和 {speed_limit:g} 之间")
    if args.mode not in ("motion-test", "forward-locate"):
        args.speed, args.min_duty = speed_limit, min_duty
    if not 0.001 <= args.seconds <= MAX_STEP_MS / 1000:
        parser.error(f"--seconds 必须在 0.001 到 {MAX_STEP_MS / 1000} 之间")
    if args.pitch_us is not None and args.mode == "motion-test":
        parser.error("motion-test 只测试底盘。使用 tilt --pitch-us 测试云台")
    min_pitch_us = MANUAL_PITCH_MIN_US if args.mode in (*DRIVE_MODES, "tilt") else PITCH_MIN_US
    if args.pitch_us is not None and not min_pitch_us <= args.pitch_us <= PITCH_MAX_US:
        parser.error(f"--pitch-us 必须在 {min_pitch_us} 到 {PITCH_MAX_US} 之间")
    if args.mode == "tilt":
        if args.start_pitch_us is None:
            args.start_pitch_us = MANUAL_PITCH_START_US
        if not MANUAL_PITCH_MIN_US <= args.start_pitch_us <= PITCH_MAX_US:
            parser.error(f"--start-pitch-us 必须在 {MANUAL_PITCH_MIN_US} 到 {PITCH_MAX_US} 之间")
        if args.pitch_us is not None and args.pitch_us > args.start_pitch_us:
            parser.error(f"tilt 只向上移动。目标脉宽必须小于或等于起始脉宽 {args.start_pitch_us} 微秒")
    elif args.start_pitch_us is not None:
        parser.error("--start-pitch-us 用于 tilt")
    if not 0 <= args.marker_id < 50:
        parser.error("--marker-id 必须在 0 到 49 之间")

    try:
        if args.mode == "observe":
            run_camera(args)
        elif args.mode == "forward-locate":
            run_forward_locate(args)
        elif args.mode == "laser-test":
            light_seconds = PULSE_SECONDS if args.light_seconds is None else args.light_seconds
            with connect_robot(args.port) as board:
                board.exec("_basic_robot.stop()")
                test_laser(args.marker_id, use_camera=not args.no_camera, light_seconds=light_seconds)
        elif args.mode == "tilt-track":
            from laser_control import laser_switch

            with connect_robot(args.port) as board:
                board.exec("_basic_robot.stop()")
                with laser_switch(), connect_gimbal() as set_pitch:
                    run_camera(args, set_pitch=set_pitch)
        elif args.mode == "laser-aim":
            from laser_aiming import run_laser_aiming

            with connect_robot(args.port) as board:
                board.exec("_basic_robot.stop()")
                with connect_gimbal() as set_pitch:
                    run_laser_aiming(args, board, set_pitch)
        else:
            with ExitStack() as devices:
                if args.camera and args.mode not in ("track", "stop"):
                    from vision_tracking import monitor_camera

                    devices.enter_context(monitor_camera(args.marker_id))
                board = None if args.mode == "tilt" else devices.enter_context(connect_robot(args.port))
                needs_pitch = args.mode in ("demo", "tilt", "track") or (
                    args.mode != "stop" and args.pitch_us is not None
                )
                set_pitch = None
                if needs_pitch:
                    set_pitch = devices.enter_context(connect_gimbal())
                if args.mode == "track":
                    run_camera(args, board, set_pitch)
                elif args.mode == "motion-test":
                    run_motion_test(board, args)
                else:
                    run_manual(board, args, set_pitch)
    except KeyboardInterrupt:
        print("已结束", flush=True)
    except subprocess.CalledProcessError as error:
        parser.exit(1, f"{Path(error.cmd[1]).parent.name}的 {error.cmd[2]} 退出，状态码 {error.returncode}\n")
    except (OSError, RuntimeError, TransportError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
