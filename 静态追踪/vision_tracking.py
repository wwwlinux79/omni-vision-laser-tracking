"""Locate an ArUco marker and a compact bright red spot in camera images."""

from contextlib import contextmanager
from threading import Event, Thread

import cv2
import numpy as np

WIDTH, HEIGHT = 640, 480


class Detector:
    def __init__(self, marker_id):
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        self.aruco = cv2.aruco.ArucoDetector(dictionary)
        self.marker_id = marker_id

    def positions(self, yuv, reference=None):
        corners, ids, _ = self.aruco.detectMarkers(yuv[:HEIGHT, :WIDTH])
        marker = None
        if ids is not None:
            for marker_id, points in zip(ids.ravel(), corners):
                if marker_id == self.marker_id:
                    marker = tuple(float(value) for value in points.reshape(4, 2).mean(axis=0))
                    break
        bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
        baseline = None if reference is None else cv2.cvtColor(reference, cv2.COLOR_YUV2BGR_I420)
        return marker, find_red_spot(bgr, baseline)


def find_red_spot(bgr, baseline=None):
    # Score compact highlights by red intensity above the other channels.
    blue, green, red = cv2.split(bgr)
    red_excess = cv2.subtract(red, cv2.max(blue, green))
    mask = (red >= 240) & (red_excess >= 80)  # 提高阈值：240和80（原220和65）
    strength = red_excess
    min_area, max_area, max_size = 5, 100, 20  # 收紧范围：5-100，最大20（原3-180-24）
    if baseline is not None:
        # A red pulse can look pale or purple on a cool background. Compare the
        # signed channel changes instead of testing the final pixel's hue.
        blue_gain, green_gain, red_gain = cv2.split(bgr.astype(np.int16) - baseline.astype(np.int16))
        red_advantage = red_gain - cv2.max(blue_gain, green_gain)
        mask = (red_gain >= 20) & (red_advantage >= 30)  # 提高阈值：20和30（原12和20）
        strength = red_gain
        min_area, max_area, max_size = 3, 800, 40  # 收紧最大范围（原2-1600-48）
    count, labels, stats, centers = cv2.connectedComponentsWithStats(mask.astype(np.uint8))
    best_score = 0
    spot = None
    for label in range(1, count):
        x, y, width, height, area = stats[label]
        if not min_area <= area <= max_area or max(width, height) > max_size:
            continue
        if max(width, height) > 3 * min(width, height):
            continue
        region = np.s_[y : y + height, x : x + width]
        score = float(strength[region][labels[region] == label].mean())
        if score > best_score:
            best_score = score
            spot = tuple(float(value) for value in centers[label])
    return spot


@contextmanager
def open_camera():
    from picamera2 import Picamera2

    with Picamera2() as camera:
        camera.configure(camera.create_video_configuration(
            main={"size": (WIDTH, HEIGHT), "format": "YUV420"}, queue=False
        ))
        camera.start()
        yield camera


def capture_frame(camera):
    try:
        return camera.capture_array("main", wait=2.0)
    except TimeoutError as error:
        raise RuntimeError("OV5647 取帧超时，请检查 CSI 摄像头连接") from error


def report_detection(marker, spot):
    offset = None
    if marker is not None and spot is not None:
        offset = tuple(round(target - light, 1) for target, light in zip(marker, spot))
    marker_px = tuple(round(value, 1) for value in marker) if marker else None
    spot_px = tuple(round(value, 1) for value in spot) if spot else None
    print(f"标记={marker_px} 光点={spot_px} 光点到标记误差={offset}", flush=True)


@contextmanager
def monitor_camera(marker_id):
    detector = Detector(marker_id)
    finished = Event()
    errors = []
    with open_camera() as camera:
        report_detection(*detector.positions(capture_frame(camera)))

        def monitor():
            try:
                while not finished.wait(0.15):
                    report_detection(*detector.positions(capture_frame(camera)))
            except (OSError, RuntimeError, cv2.error) as error:
                errors.append(error)

        worker = Thread(target=monitor)
        worker.start()
        try:
            yield
        finally:
            finished.set()
            worker.join()
        if errors:
            raise RuntimeError(f"联动摄像头取帧失败，{errors[0]}") from errors[0]
