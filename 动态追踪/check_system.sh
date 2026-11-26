#!/bin/bash
# 系统健康检查脚本 - 在启动激光瞄准前运行

echo "========================================"
echo "激光瞄准系统 - 健康检查"
echo "========================================"
echo ""

PASS=0
FAIL=0

# 1. 检查pigpiod
echo -n "[1/7] 检查pigpiod服务... "
if pgrep -x "pigpiod" > /dev/null; then
    echo "✓ 通过"
    ((PASS++))
else
    echo "✗ 失败 - 运行: sudo pigpiod"
    ((FAIL++))
fi

# 2. 检查Pico
echo -n "[2/7] 检查Pico连接... "
if ls /dev/ttyACM* > /dev/null 2>&1; then
    PICO_PORT=$(ls /dev/ttyACM* | head -1)
    echo "✓ 通过 ($PICO_PORT)"
    ((PASS++))
else
    echo "✗ 失败 - 未找到设备"
    ((FAIL++))
fi

# 3. 检查摄像头
echo -n "[3/7] 检查CSI摄像头... "
if vcgencmd get_camera 2>/dev/null | grep -q "detected=1"; then
    echo "✓ 通过"
    ((PASS++))
else
    echo "✗ 失败 - 未检测到摄像头"
    ((FAIL++))
fi

# 4. 检查Python环境
echo -n "[4/7] 检查Python环境... "
if command -v python3 > /dev/null; then
    echo "✓ 通过 ($(python3 --version))"
    ((PASS++))
else
    echo "✗ 失败"
    ((FAIL++))
fi

# 5. 检查必需的Python包
echo -n "[5/7] 检查Python依赖... "
MISSING=""
for pkg in cv2 numpy picamera2 gpiozero pigpio; do
    if ! python3 -c "import $pkg" 2>/dev/null; then
        MISSING="$MISSING $pkg"
    fi
done
if [ -z "$MISSING" ]; then
    echo "✓ 通过"
    ((PASS++))
else
    echo "✗ 失败 - 缺少:$MISSING"
    ((FAIL++))
fi

# 6. 检查项目文件
echo -n "[6/7] 检查项目文件... "
REQUIRED_FILES=(
    "/home/pi4/project/基本功能/control.py"
    "/home/pi4/project/基本功能/laser_aiming.py"
    "/home/pi4/project/基本功能/laser_control.py"
    "/home/pi4/project/基本功能/vision_tracking.py"
    "/home/pi4/project/基本功能/pico_control.py"
)
ALL_EXIST=true
for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo "✗ 失败 - 缺少: $file"
        ALL_EXIST=false
        break
    fi
done
if $ALL_EXIST; then
    echo "✓ 通过"
    ((PASS++))
else
    ((FAIL++))
fi

# 7. 检查GPIO权限
echo -n "[7/7] 检查GPIO权限... "
if groups | grep -q "gpio\|dialout"; then
    echo "✓ 通过"
    ((PASS++))
else
    echo "⚠ 警告 - 可能需要添加用户到gpio/dialout组"
    ((PASS++))
fi

echo ""
echo "========================================"
echo "检查结果: $PASS 通过, $FAIL 失败"
echo "========================================"
echo ""

if [ $FAIL -eq 0 ]; then
    echo "✓ 系统状态良好，可以启动激光瞄准系统"
    echo ""
    echo "启动命令:"
    echo "  ./start_laser_aim.sh"
    echo ""
    exit 0
else
    echo "✗ 请先解决上述问题"
    echo ""
    exit 1
fi
