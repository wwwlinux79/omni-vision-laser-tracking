"""Motor outputs on the RP2040-Zero."""

MOTOR_PINS = ((0, 1), (2, 3), (4, 5))
MOTOR_HZ = 2000
MAX_STEP_MS = 5000


class Robot:
    def __init__(self):
        from machine import Pin, PWM

        self.Pin = Pin
        self.motors = []
        try:
            for pair in MOTOR_PINS:
                for gpio in pair:
                    pin = Pin(gpio, Pin.OUT, value=0)
                    self.motors.append(PWM(pin, freq=MOTOR_HZ, duty_u16=0))
        except BaseException:
            self.close()
            raise

    def stop(self):
        for pwm in self.motors:
            pwm.duty_u16(0)

    def step(self, wheels, duration_ms):
        """Apply signed wheel duties for one timed move."""
        from time import sleep_ms

        if len(wheels) != len(MOTOR_PINS) or any(abs(duty) > 65535 for duty in wheels):
            raise ValueError("Expected three signed PWM duties")
        if not 0 < duration_ms <= MAX_STEP_MS:
            raise ValueError("Motion duration outside the configured range")

        try:
            self.stop()
            for wheel, duty in enumerate(wheels):
                if duty:
                    channel = 2 * wheel + (duty < 0)
                    self.motors[channel].duty_u16(abs(duty))
            sleep_ms(duration_ms)
        finally:
            self.stop()

    def close(self):
        self.stop()
        for pwm in self.motors:
            pwm.deinit()
        for pair in MOTOR_PINS:
            for gpio in pair:
                self.Pin(gpio, self.Pin.OUT, value=0)
        self.motors.clear()
