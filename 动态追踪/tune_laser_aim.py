#!/usr/bin/env python3
"""激光瞄准参数调优工具 - 实时调整和测试参数"""

import argparse
import sys
from pathlib import Path

# 添加基本功能目录到路径
sys.path.insert(0, str(Path(__file__).parent / "基本功能"))


def show_current_params():
    """显示当前参数配置"""
    from laser_aiming import (
        YAW_THRESHOLD, PITCH_THRESHOLD, YAW_DURATION_MS,
        PITCH_GAIN, MAX_PITCH_STEP, FRAME_DELAY
    )

    print("\n" + "=" * 60)
    print("当前激光瞄准参数配置")
    print("=" * 60)
    print(f"YAW_THRESHOLD      = {YAW_THRESHOLD:6}  # 水平误差阈值（像素）")
    print(f"PITCH_THRESHOLD    = {PITCH_THRESHOLD:6}  # 垂直误差阈值（像素）")
    print(f"YAW_DURATION_MS    = {YAW_DURATION_MS:6}  # 底盘旋转持续时间（毫秒）")
    print(f"PITCH_GAIN         = {PITCH_GAIN:6.2f}  # 俯仰调整增益")
    print(f"MAX_PITCH_STEP     = {MAX_PITCH_STEP:6}  # 单次俯仰最大调整量（微秒）")
    print(f"FRAME_DELAY        = {FRAME_DELAY:6.2f}  # 帧间延迟（秒）")
    print("=" * 60 + "\n")


def update_params(args):
    """更新参数文件"""
    file_path = Path(__file__).parent / "基本功能" / "laser_aiming.py"

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    updates = []
    if args.yaw_threshold is not None:
        content = content.replace(
            f"YAW_THRESHOLD = ",
            f"YAW_THRESHOLD = {args.yaw_threshold}  # "
        ).replace(
            f"YAW_THRESHOLD = {args.yaw_threshold}  #       ",
            f"YAW_THRESHOLD = {args.yaw_threshold}       "
        )
        updates.append(f"YAW_THRESHOLD = {args.yaw_threshold}")

    if args.pitch_threshold is not None:
        content = content.replace(
            f"PITCH_THRESHOLD = ",
            f"PITCH_THRESHOLD = {args.pitch_threshold}  # "
        ).replace(
            f"PITCH_THRESHOLD = {args.pitch_threshold}  #     ",
            f"PITCH_THRESHOLD = {args.pitch_threshold}     "
        )
        updates.append(f"PITCH_THRESHOLD = {args.pitch_threshold}")

    if args.yaw_duration is not None:
        import re
        content = re.sub(
            r'YAW_DURATION_MS\s*=\s*\d+',
            f'YAW_DURATION_MS = {args.yaw_duration}',
            content
        )
        updates.append(f"YAW_DURATION_MS = {args.yaw_duration}")

    if args.pitch_gain is not None:
        import re
        content = re.sub(
            r'PITCH_GAIN\s*=\s*[\d.]+',
            f'PITCH_GAIN = {args.pitch_gain}',
            content
        )
        updates.append(f"PITCH_GAIN = {args.pitch_gain}")

    if args.max_pitch_step is not None:
        import re
        content = re.sub(
            r'MAX_PITCH_STEP\s*=\s*\d+',
            f'MAX_PITCH_STEP = {args.max_pitch_step}',
            content
        )
        updates.append(f"MAX_PITCH_STEP = {args.max_pitch_step}")

    if args.frame_delay is not None:
        import re
        content = re.sub(
            r'FRAME_DELAY\s*=\s*[\d.]+',
            f'FRAME_DELAY = {args.frame_delay}',
            content
        )
        updates.append(f"FRAME_DELAY = {args.frame_delay}")

    if updates:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        print("\n✓ 参数已更新:")
        for update in updates:
            print(f"  {update}")
        print("\n提示: 重新运行激光瞄准程序以应用新参数\n")
    else:
        print("\n未指定要更新的参数\n")


def suggest_params(issue):
    """根据问题给出参数调整建议"""
    suggestions = {
        "unstable": {
            "问题": "系统不稳定/抖动",
            "建议": [
                "增大 YAW_THRESHOLD (例如: 30-40)",
                "增大 PITCH_THRESHOLD (例如: 15-20)",
                "减小 PITCH_GAIN (例如: 0.10-0.12)",
                "增大 FRAME_DELAY (例如: 0.10-0.15)",
            ],
            "命令示例": "./tune_laser_aim.py --yaw-threshold 35 --pitch-threshold 18 --pitch-gain 0.10"
        },
        "slow": {
            "问题": "响应太慢",
            "建议": [
                "减小 YAW_THRESHOLD (例如: 15-18)",
                "减小 PITCH_THRESHOLD (例如: 8-10)",
                "增大 PITCH_GAIN (例如: 0.18-0.22)",
                "增大 MAX_PITCH_STEP (例如: 18-25)",
                "减小 FRAME_DELAY (例如: 0.05-0.06)",
            ],
            "命令示例": "./tune_laser_aim.py --yaw-threshold 15 --pitch-gain 0.20 --frame-delay 0.06"
        },
        "overshoot": {
            "问题": "调整过度/超调",
            "建议": [
                "减小 PITCH_GAIN (例如: 0.08-0.12)",
                "减小 MAX_PITCH_STEP (例如: 8-12)",
                "减小 YAW_DURATION_MS (例如: 80-90)",
            ],
            "命令示例": "./tune_laser_aim.py --pitch-gain 0.10 --max-pitch-step 10 --yaw-duration 85"
        },
        "rotation": {
            "问题": "底盘旋转不够或过度",
            "建议": [
                "调整 YAW_DURATION_MS (增大=转更多, 减小=转更少)",
                "配合 --speed 参数调整旋转速度",
            ],
            "命令示例": "./tune_laser_aim.py --yaw-duration 120  # 或 80"
        },
    }

    if issue not in suggestions:
        print(f"\n未知问题类型: {issue}")
        print(f"可用类型: {', '.join(suggestions.keys())}\n")
        return

    info = suggestions[issue]
    print("\n" + "=" * 60)
    print(f"问题: {info['问题']}")
    print("=" * 60)
    print("\n调整建议:")
    for i, suggestion in enumerate(info['建议'], 1):
        print(f"  {i}. {suggestion}")
    print(f"\n命令示例:")
    print(f"  {info['命令示例']}")
    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="激光瞄准参数调优工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  查看当前参数:
    %(prog)s --show

  调整参数:
    %(prog)s --yaw-threshold 30 --pitch-gain 0.12

  获取调优建议:
    %(prog)s --suggest unstable    # 系统抖动
    %(prog)s --suggest slow         # 响应慢
    %(prog)s --suggest overshoot    # 超调
    %(prog)s --suggest rotation     # 旋转问题
        """
    )

    parser.add_argument('--show', action='store_true',
                        help='显示当前参数配置')
    parser.add_argument('--suggest', choices=['unstable', 'slow', 'overshoot', 'rotation'],
                        help='根据问题类型给出调整建议')

    parser.add_argument('--yaw-threshold', type=int,
                        help='水平误差阈值（像素，建议范围: 10-50）')
    parser.add_argument('--pitch-threshold', type=int,
                        help='垂直误差阈值（像素，建议范围: 5-30）')
    parser.add_argument('--yaw-duration', type=int,
                        help='底盘旋转持续时间（毫秒，建议范围: 50-200）')
    parser.add_argument('--pitch-gain', type=float,
                        help='俯仰调整增益（建议范围: 0.05-0.30）')
    parser.add_argument('--max-pitch-step', type=int,
                        help='单次俯仰最大调整量（微秒，建议范围: 5-30）')
    parser.add_argument('--frame-delay', type=float,
                        help='帧间延迟（秒，建议范围: 0.05-0.20）')

    args = parser.parse_args()

    # 如果没有参数，显示帮助
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    # 显示当前参数
    if args.show:
        show_current_params()
        return

    # 显示建议
    if args.suggest:
        suggest_params(args.suggest)
        return

    # 更新参数
    update_params(args)
    show_current_params()


if __name__ == '__main__':
    main()
