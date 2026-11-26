#!/bin/bash
# 激光自动瞄准系统快速启动脚本

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}激光自动瞄准系统 - 快速启动${NC}"
echo -e "${GREEN}========================================${NC}"

# 检查pigpiod服务
echo -e "\n${YELLOW}[1/4] 检查pigpiod服务...${NC}"
if ! pgrep -x "pigpiod" > /dev/null; then
    echo -e "${YELLOW}pigpiod未运行，正在启动...${NC}"
    sudo pigpiod
    sleep 1
    if pgrep -x "pigpiod" > /dev/null; then
        echo -e "${GREEN}✓ pigpiod已启动${NC}"
    else
        echo -e "${RED}✗ pigpiod启动失败${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}✓ pigpiod正在运行${NC}"
fi

# 检查Pico连接
echo -e "\n${YELLOW}[2/4] 检查Pico连接...${NC}"
PICO_PORT=$(ls /dev/ttyACM* 2>/dev/null | head -1)
if [ -z "$PICO_PORT" ]; then
    echo -e "${RED}✗ 未找到Pico设备（/dev/ttyACM*）${NC}"
    echo -e "${YELLOW}请检查：${NC}"
    echo -e "  - Pico是否已连接USB"
    echo -e "  - USB线是否支持数据传输"
    echo -e "  - 执行 'ls /dev/ttyACM*' 查看设备"
    exit 1
else
    echo -e "${GREEN}✓ 找到Pico: $PICO_PORT${NC}"
fi

# 检查摄像头（仅警告，不阻止启动）
echo -e "\n${YELLOW}[3/4] 检查摄像头...${NC}"
if ! vcgencmd get_camera | grep -q "detected=1"; then
    echo -e "${YELLOW}⚠ vcgencmd未检测到摄像头（新系统使用libcamera，这是正常的）${NC}"
    echo -e "${YELLOW}  摄像头将在启动时自动检测${NC}"
else
    echo -e "${GREEN}✓ 摄像头已连接${NC}"
fi

# 进入工作目录（静态追踪文件夹）
echo -e "\n${YELLOW}[4/4] 准备启动...${NC}"
cd "$(dirname "$0")"

# 参数解析
MARKER_ID=0
PITCH_US=1050  
SPEED=0.6

while [[ $# -gt 0 ]]; do
    case $1 in
        --marker-id)
            MARKER_ID="$2"
            shift 2
            ;;
        --pitch-us)
            PITCH_US="$2"
            shift 2
            ;;
        --speed)
            SPEED="$2"
            shift 2
            ;;
        --help|-h)
            echo "用法: $0 [选项]"
            echo ""
            echo "选项:"
            echo "  --marker-id N    ArUco标记ID (默认: 0)"
            echo "  --pitch-us N     起始俯仰脉宽 (默认: 1500)"
            echo "  --speed N        底盘旋转速度 (默认: 0.6)"
            echo "  --help, -h       显示此帮助信息"
            echo ""
            echo "示例:"
            echo "  $0"
            echo "  $0 --marker-id 5 --pitch-us 1400"
            echo "  $0 --speed 0.8"
            exit 0
            ;;
        *)
            echo -e "${RED}未知参数: $1${NC}"
            echo "使用 --help 查看帮助"
            exit 1
            ;;
    esac
done

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}启动参数:${NC}"
echo -e "  Pico端口: ${YELLOW}$PICO_PORT${NC}"
echo -e "  标记ID: ${YELLOW}$MARKER_ID${NC}"
echo -e "  起始俯仰: ${YELLOW}$PITCH_US µs${NC}"
echo -e "  旋转速度: ${YELLOW}$SPEED${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}提示: 按 Ctrl+C 停止系统${NC}"
echo ""
sleep 2

# 启动激光瞄准系统
python control.py laser-aim \
    --port "$PICO_PORT" \
    --marker-id "$MARKER_ID" \
    --pitch-us "$PITCH_US" \
    --speed "$SPEED"
