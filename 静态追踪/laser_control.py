"""Test a single stationary GPIO17 light pulse, with optional camera measurement."""

from contextlib import contextmanager
from math import dist
from time import monotonic, sleep

SWITCH_GPIO = 17
PULSE_SECONDS = 0.25
MAX_LIGHT_SECONDS = 3.0


def capture_pulse(camera, trigger, light_seconds=PULSE_SECONDS):
    frames = []
    started_at = monotonic()
    ends_at = started_at + light_seconds
    spacing = (light_seconds - PULSE_SECONDS) / 4
    try:
        trigger.blink(on_time=light_seconds, off_time=0, n=1, background=True)
        for index in range(5):
            delay = started_at + 0.04 + index * spacing - monotonic()
            if delay > 0:
                sleep(delay)
            remaining = ends_at - monotonic()
            if remaining <= 0:
                break
            try:
                frames.append(camera.capture_array("main", wait=remaining))
            except TimeoutError:
                break
        remaining = ends_at - monotonic()
        if remaining > 0:
            sleep(remaining)
    finally:
        trigger.off()
    return frames


@contextmanager
def laser_switch():
    from gpiozero import DigitalOutputDevice
    from gpiozero.pins.lgpio import LGPIOFactory

    factory = LGPIOFactory()
    try:
        with DigitalOutputDevice(SWITCH_GPIO, active_high=True, initial_value=False,
                                 pin_factory=factory) as trigger:
            try:
                yield trigger
            finally:
                trigger.off()
    finally:
        factory.close()


def test_laser(marker_id, *, use_camera=True, light_seconds=PULSE_SECONDS):
    if not PULSE_SECONDS <= light_seconds <= MAX_LIGHT_SECONDS:
        raise RuntimeError(f"亮灯时间须在 {PULSE_SECONDS:g} 到 {MAX_LIGHT_SECONDS:g} 秒之间")
    countdown = f"底盘已停车，3 秒后对固定靶面点亮一次，持续 {light_seconds:g} 秒"
    with laser_switch() as trigger:
        if not use_camera:
            print(countdown, flush=True)
            sleep(3)
            trigger.blink(on_time=light_seconds, off_time=0, n=1, background=False)
            print("GPIO17 脉冲发送完成", flush=True)
            return

        from vision_tracking import Detector, capture_frame, open_camera, report_detection

        detector = Detector(marker_id)
        with open_camera() as camera:
            capture_frame(camera)
            print(countdown, flush=True)
            sleep(3)
            reference = capture_frame(camera)
            frames = capture_pulse(camera, trigger, light_seconds)

    marker, _ = detector.positions(reference)
    spots = []
    for frame in frames:
        observed_marker, spot = detector.positions(frame, reference=reference)
        if marker is None:
            marker = observed_marker
        if spot is not None:
            spots.append(spot)
    spot = max(spots, key=lambda point: sum(dist(point, other) <= 5 for other in spots), default=None)
    print(f"亮灯期间采集 {len(frames)} 帧，检出候选光点 {len(spots)} 帧，输出已关闭", flush=True)
    report_detection(marker, spot)
