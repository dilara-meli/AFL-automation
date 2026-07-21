import re
import threading
import time

import lazy_loader as lazy

from AFL.automation.APIServer.Driver import Driver

serial = lazy.load("serial", require="AFL-automation[serial]")


class Stir_Plate(Driver):
    defaults = {}
    defaults["serial_port"] = "COM4"
    defaults["baudrate"] = 9600
    defaults["timeout"] = 0.5
    defaults["status_query_timeout"] = 0.5
    defaults["bytesize"] = 8
    defaults["parity"] = "N"
    defaults["stopbits"] = 1
    defaults["command_terminator"] = "\r"
    defaults["response_terminator"] = "\r"
    defaults["post_connect_delay"] = 1.0
    defaults["post_write_delay"] = 0.05
    defaults["poll_interval"] = 0.25
    defaults["speed_tolerance"] = 25.0
    defaults["speed_settle_timeout"] = 30.0
    defaults["default_rpm"] = 350
    defaults["default_power"] = 50

    def __init__(self, overrides=None):
        self.app = None
        Driver.__init__(
            self,
            name="Stir_Plate",
            defaults=self.gather_defaults(),
            overrides=overrides,
        )
        self.name = "Stir_Plate"
        self.connection = None
        self._serial_lock = threading.Lock()
        self.cached_rpm = None
        self.cached_power = None
        self.cached_power_state = "unknown"
        self.cached_stir_state = "unknown"
        self.cached_status = "unknown"
        self.cached_mode = "unknown"
        self.cached_response = None
        self._cancel_run_program = threading.Event()

    def _serial_kwargs(self):
        parity_map = {
            "N": serial.PARITY_NONE,
            "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD,
        }
        stopbit_map = {
            1: serial.STOPBITS_ONE,
            1.5: serial.STOPBITS_ONE_POINT_FIVE,
            2: serial.STOPBITS_TWO,
        }
        bytesize_map = {
            5: serial.FIVEBITS,
            6: serial.SIXBITS,
            7: serial.SEVENBITS,
            8: serial.EIGHTBITS,
        }
        return {
            "port": self.config["serial_port"],
            "baudrate": self.config["baudrate"],
            "bytesize": bytesize_map[self.config["bytesize"]],
            "parity": parity_map[str(self.config["parity"]).upper()],
            "stopbits": stopbit_map[self.config["stopbits"]],
            "timeout": self.config["timeout"],
        }

    def _ensure_connection(self):
        if self.connection is not None and self.connection.is_open:
            return self.connection

        self.connection = serial.Serial(**self._serial_kwargs())
        time.sleep(self.config["post_connect_delay"])
        return self.connection

    def close(self):
        with self._serial_lock:
            if self.connection is not None and self.connection.is_open:
                self.connection.close()
            self.connection = None
        return "closed"

    def _read_response(self, connection):
        response_terminator = self.config.get("response_terminator")
        if response_terminator:
            terminator = str(response_terminator).encode("utf-8")
            return connection.read_until(expected=terminator)
        return connection.readline()

    def _send_command(self, command, timeout=None, post_write_delay=None):
        with self._serial_lock:
            connection = self._ensure_connection()
            message = f"{command}{self.config['command_terminator']}"
            if post_write_delay is None:
                post_write_delay = self.config["post_write_delay"]

            connection.reset_input_buffer()
            connection.write(message.encode())
            connection.flush()
            time.sleep(post_write_delay)
            response = self._read_response(connection)

        if response:
            decoded = response.decode("utf-8", errors="ignore").strip()
            self.cached_response = decoded
            return decoded
        self.cached_response = None
        self._cancel_run_program = threading.Event()
        return None

    def _require_response(self, response, action):
        if response is None:
            raise RuntimeError(f"No response from stir plate during {action}")

    @staticmethod
    def _coerce_duration_part(value, field_name):
        if value is None:
            return 0.0
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return 0.0
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be numeric.") from exc

    def _extract_first_int(self, response):
        if response is None:
            return None
        match = re.search(r"(\d+)", str(response))
        if match is None:
            return None
        return int(match.group(1))

    def _update_mode_from_response(self, response):
        if response is None:
            return
        response_text = str(response).upper()
        if "_REM_" in response_text or response_text.endswith("_REM"):
            self.cached_mode = "remote"
        elif "_MAN_" in response_text or response_text.endswith("_MAN"):
            self.cached_mode = "manual"
        else:
            self.cached_mode = "unknown"

    @Driver.queued()
    def start_stir(self):
        if self.cached_power_state != "on":
            self.start()

        target_rpm = self.cached_rpm if isinstance(self.cached_rpm, int) and self.cached_rpm > 0 else int(self.config["default_rpm"])
        response = self.set_rpm(target_rpm)
        self.cached_power_state = "on"
        self.cached_stir_state = "running"
        self.cached_status = "running"
        return response

    @Driver.queued()
    def start(self):
        response = self._require_response(self._send_command("start"), "start")
        self.cached_power_state = "on"
        if self.cached_stir_state == "unknown":
            self.cached_stir_state = "stopped"
        self.cached_status = "on"
        return response

    @Driver.queued()
    def stop_stir(self):
        response = self.set_rpm(0)
        self.cached_stir_state = "stopped"
        self.cached_status = "stopped"
        return response

    @Driver.queued()
    def stop(self):
        response = self._require_response(self._send_command("stop"), "stop")
        self.cached_power_state = "off"
        self.cached_stir_state = "stopped"
        self.cached_status = "off"
        self.cached_rpm = 0
        return response

    @Driver.unqueued()
    def cancel_program(self):
        self._cancel_run_program.set()
        if self.cached_power_state == "off":
            return "already off"
        return self.stop()

    @Driver.queued()
    def set_rpm(self, rpm=350):
        response = self._require_response(self._send_command(f"setrpm_{int(rpm)}"), "set_rpm")
        self.cached_rpm = int(rpm)
        if self.cached_power_state == "off":
            self.cached_stir_state = "stopped"
            self.cached_status = "off"
        elif self.cached_rpm > 0:
            self.cached_power_state = "on"
            self.cached_stir_state = "running"
            self.cached_status = "running"
        else:
            if self.cached_power_state != "unknown":
                self.cached_power_state = "on"
            self.cached_stir_state = "stopped"
            self.cached_status = "on" if self.cached_power_state == "on" else "stopped"
        return response

    @Driver.unqueued()
    def get_rpm(self, timeout=None):
        response = self._send_command("sendrpm", timeout=timeout)
        rpm = self._extract_first_int(response)
        if rpm is not None:
            self.cached_rpm = rpm
            self.cached_stir_state = "running" if rpm > 0 else "stopped"
            if self.cached_power_state == "unknown" and rpm > 0:
                self.cached_power_state = "on"
        return rpm if rpm is not None else response

    @Driver.queued()
    def set_power(self, power=50):
        response = self._require_response(self._send_command(f"setpower_{int(power)}"), "set_power")
        self.cached_power = int(power)
        return response

    @Driver.queued()
    def reset_default(self):
        self.set_power(self.config["default_power"])
        return self.set_rpm(self.config["default_rpm"])

    @Driver.unqueued()
    def get_power(self, timeout=None):
        response = self._send_command("sendpower", timeout=timeout)
        power = self._extract_first_int(response)
        if power is not None:
            self.cached_power = power
        return power if power is not None else response

    @Driver.unqueued()
    def version(self, timeout=None):
        return self._send_command("sendversion", timeout=timeout)

    @Driver.unqueued()
    def read_status(self, timeout=None):
        response = self._send_command("sendstatus", timeout=timeout)
        if response is not None:
            self.cached_response = response
            self._update_mode_from_response(response)
            response_upper = str(response).upper()
            if "_OFF_" in response_upper:
                self.cached_power_state = "off"
                self.cached_stir_state = "stopped"
                self.cached_status = "off"
                self.cached_rpm = 0
            elif "_ON_" in response_upper:
                self.cached_power_state = "on"
                if self.cached_stir_state == "unknown":
                    self.cached_stir_state = "stopped"
                self.cached_status = "running" if self.cached_stir_state == "running" else "on"
            elif "START" in response_upper:
                self.cached_power_state = "on"
                self.cached_stir_state = "running"
                self.cached_status = "running"
            elif "STOP" in response_upper:
                self.cached_power_state = "on"
                self.cached_stir_state = "stopped"
                self.cached_status = "stopped"
        return response

    @Driver.unqueued()
    def test_connection(self, timeout=None):
        if timeout is None:
            timeout = self.config["status_query_timeout"]

        results = {}
        commands = {
            "status": self.read_status,
            "rpm": self.get_rpm,
            "power": self.get_power,
            "version": self.version,
        }
        for name, func in commands.items():
            try:
                results[name] = func(timeout=timeout)
            except Exception as exc:
                results[name] = f"ERROR: {exc}"
        return results

    def wait_for_speed(self, speed, tolerance=None, timeout=None):
        if tolerance is None:
            tolerance = self.config["speed_tolerance"]
        if timeout is None:
            timeout = self.config["speed_settle_timeout"]

        start_time = time.time()
        while (time.time() - start_time) < timeout:
            if self._cancel_run_program.is_set():
                self._cancel_run_program.clear()
                if self.cached_power_state != "off":
                    self.stop()
                return None
            readback = self.get_rpm()
            if isinstance(readback, int) and abs(readback - speed) <= tolerance:
                return readback
            time.sleep(self.config["poll_interval"])

        raise TimeoutError(f"Stir plate did not reach {speed} rpm within {timeout} s")

    @Driver.queued()
    def run_stir(self, rpm, hours=0, minutes=5, seconds=0, power=None, wait_for_speed=True):
        self._cancel_run_program.clear()
        if power not in (None, ""):
            self.set_power(power)
        self.start_stir()
        self.set_rpm(rpm)
        if wait_for_speed:
            speed_result = self.wait_for_speed(rpm)
            if speed_result is None and self.cached_power_state == "off":
                return "Cancelled"
        remaining_seconds = max(0.0, (
            self._coerce_duration_part(hours, "hours") * 3600
            + self._coerce_duration_part(minutes, "minutes") * 60
            + self._coerce_duration_part(seconds, "seconds")
        ))
        while remaining_seconds > 0:
            if self._cancel_run_program.is_set():
                self._cancel_run_program.clear()
                if self.cached_power_state != "off":
                    self.stop()
                return "Cancelled"
            sleep_interval = min(self.config["poll_interval"], remaining_seconds)
            time.sleep(sleep_interval)
            remaining_seconds -= sleep_interval
        self._cancel_run_program.clear()
        return self.stop()

    @Driver.unqueued()
    def status(self):
        timeout = self.config["status_query_timeout"]
        errors = []

        try:
            response = self.read_status(timeout=timeout)
        except Exception as exc:
            response = self.cached_response
            errors.append(f"status query failed: {exc}")

        status_lines = [
            f"power status: {self.cached_power_state}",
            f"mode: {self.cached_mode}",
            f"rpm: {self.cached_rpm}",
            f"power: {self.cached_power}",
        ]
        if errors:
            status_lines.append(f"errors: {'; '.join(errors)}")
        return status_lines


if __name__ == "__main__":
    from AFL.automation.shared.launcher import *









