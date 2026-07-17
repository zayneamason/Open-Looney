"""Luna Arcade Controller — Serial Bridge with configurable button mapping.

Reads structured button events from the Elegoo Mega 2560 and maps them to
keyboard presses, Luna commands, or profile switches based on button_config.json.

Also sends LCD commands back to the ISC15AMP4 display button.

Cross-platform: uses evdev/uinput on Linux (Pi), pynput on macOS.

Usage:
    python serial_bridge.py                          # auto-detect, default config
    python serial_bridge.py --port /dev/ttyACM0
    python serial_bridge.py --profile menu           # start in menu profile

Protocol (Mega → PC):
    BTN:<id>:<1|0>     button press/release
    READY              controller booted
    PROFILE:<name>     profile switch confirmed

Protocol (PC → Mega):
    LCD:text:<line1>|<line2>   update LCD text
    LCD:clear                   clear LCD
    LCD:icon:<name>             show built-in icon (fire, play, pause, luna, menu)
    LCD:invert:<0|1>            invert display
    PROFILE:<name>              request profile switch
"""

import sys
import json
import time
import glob
import logging
import argparse
import platform
from pathlib import Path
from typing import Optional

import serial

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "button_config.json"
IS_LINUX = platform.system() == "Linux"

# ── Keyboard backend ────────────────────────────────────────
# Linux (Pi): evdev + uinput — works headless, kiosk, Wayland, X11
# macOS: pynput — needs Accessibility permission

if IS_LINUX:
    import evdev
    from evdev import UInput, ecodes

    _EVDEV_KEY_MAP = {
        "space": ecodes.KEY_SPACE,
        "return": ecodes.KEY_ENTER,
        "enter": ecodes.KEY_ENTER,
        "left": ecodes.KEY_LEFT,
        "right": ecodes.KEY_RIGHT,
        "up": ecodes.KEY_UP,
        "down": ecodes.KEY_DOWN,
        "escape": ecodes.KEY_ESC,
        "esc": ecodes.KEY_ESC,
        "tab": ecodes.KEY_TAB,
        "shift": ecodes.KEY_LEFTSHIFT,
        "ctrl": ecodes.KEY_LEFTCTRL,
        "alt": ecodes.KEY_LEFTALT,
        "backspace": ecodes.KEY_BACKSPACE,
        "delete": ecodes.KEY_DELETE,
    }

    class KeyboardBackend:
        def __init__(self):
            self._ui = UInput(name="luna-arcade-controller")

        def press(self, key):
            self._ui.write(ecodes.EV_KEY, key, 1)
            self._ui.syn()

        def release(self, key):
            self._ui.write(ecodes.EV_KEY, key, 0)
            self._ui.syn()

        def close(self):
            self._ui.close()

        @staticmethod
        def resolve_key(name: str):
            key = _EVDEV_KEY_MAP.get(name)
            if key is not None:
                return key
            if len(name) == 1:
                attr = f"KEY_{name.upper()}"
                return getattr(ecodes, attr, None)
            return None

else:
    from pynput.keyboard import Controller as _PynputController, Key as _PynputKey

    _PYNPUT_KEY_MAP = {
        "space": _PynputKey.space,
        "return": _PynputKey.enter,
        "enter": _PynputKey.enter,
        "left": _PynputKey.left,
        "right": _PynputKey.right,
        "up": _PynputKey.up,
        "down": _PynputKey.down,
        "escape": _PynputKey.esc,
        "esc": _PynputKey.esc,
        "tab": _PynputKey.tab,
        "shift": _PynputKey.shift,
        "ctrl": _PynputKey.ctrl,
        "alt": _PynputKey.alt,
        "backspace": _PynputKey.backspace,
        "delete": _PynputKey.delete,
    }

    class KeyboardBackend:
        def __init__(self):
            self._ctrl = _PynputController()

        def press(self, key):
            self._ctrl.press(key)

        def release(self, key):
            self._ctrl.release(key)

        def close(self):
            pass

        @staticmethod
        def resolve_key(name: str):
            key = _PYNPUT_KEY_MAP.get(name)
            if key is not None:
                return key
            if len(name) == 1:
                return name
            return None


class ArcadeBridge:
    """Bridges Mega serial events to keyboard/Luna actions via config profiles."""

    def __init__(self, config_path: Path = CONFIG_PATH, port: str = None,
                 profile: str = None, luna_callback=None):
        self.config = self._load_config(config_path)
        self.config_path = config_path
        self.keyboard = KeyboardBackend()
        self.ser: Optional[serial.Serial] = None
        self.active_profile = profile or self.config.get("active_profile", "arcade")
        self.luna_callback = luna_callback  # async fn(command_str) for Luna commands
        self.port = port
        self._held_keys = set()
        self._running = False

    @staticmethod
    def _load_config(path: Path) -> dict:
        with open(path) as f:
            return json.load(f)

    def reload_config(self):
        """Hot-reload config without restarting."""
        self.config = self._load_config(self.config_path)
        logger.info("Config reloaded")

    @property
    def profile(self) -> dict:
        return self.config["profiles"].get(self.active_profile, {})

    @property
    def button_map(self) -> dict:
        return self.profile.get("buttons", {})

    def find_port(self) -> str:
        if self.port:
            return self.port
        override = self.config.get("serial", {}).get("port_override")
        if override:
            return override
        if IS_LINUX:
            candidates = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
        else:
            candidates = glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
        if not candidates:
            raise RuntimeError("No USB serial device found.")
        return candidates[0]

    def connect(self):
        port = self.find_port()
        baud = self.config.get("serial", {}).get("baud", 115200)
        logger.info(f"Connecting to {port} @ {baud}")
        self.ser = serial.Serial(port, baud, timeout=0.05)
        time.sleep(2)
        self.ser.reset_input_buffer()

        # Send startup LCD command
        on_connect = self.config.get("lcd", {}).get("on_connect", {})
        if "icon" in on_connect:
            self.lcd_icon(on_connect["icon"])
        elif "text" in on_connect:
            self.lcd_text(on_connect["text"])

        logger.info("Bridge connected")

    # ── LCD commands ─────────────────────────────────────────
    def lcd_text(self, text: str):
        if self.ser and self.ser.is_open:
            self.ser.write(f"LCD:text:{text}\n".encode())

    def lcd_clear(self):
        if self.ser and self.ser.is_open:
            self.ser.write(b"LCD:clear\n")

    def lcd_icon(self, name: str):
        if self.ser and self.ser.is_open:
            self.ser.write(f"LCD:icon:{name}\n".encode())

    def lcd_invert(self, on: bool):
        if self.ser and self.ser.is_open:
            self.ser.write(f"LCD:invert:{'1' if on else '0'}\n".encode())

    # ── Profile switching ────────────────────────────────────
    def switch_profile(self, name: str):
        if name not in self.config["profiles"]:
            logger.warning(f"Unknown profile: {name}")
            return
        self.active_profile = name
        logger.info(f"Switched to profile: {name}")

        # Update LCD
        idle = self.profile.get("lcd_idle", {})
        if "icon" in idle:
            self.lcd_icon(idle["icon"])
        elif "text" in idle:
            self.lcd_text(idle["text"])

        # Notify Mega
        if self.ser and self.ser.is_open:
            self.ser.write(f"PROFILE:{name}\n".encode())

    # ── Action dispatch ──────────────────────────────────────
    def _handle_action(self, action: str, pressed: bool, button_id: str):
        """Dispatch a button action string."""
        if action.startswith("key:"):
            key_name = action[4:]
            key = self.keyboard.resolve_key(key_name)
            if key is None:
                logger.warning(f"Unknown key: {key_name}")
                return

            if pressed:
                self.keyboard.press(key)
                self._held_keys.add(key)
            else:
                self.keyboard.release(key)
                self._held_keys.discard(key)

        elif action.startswith("profile:"):
            if pressed:
                self.switch_profile(action[8:])

        elif action.startswith("luna:"):
            if pressed and self.luna_callback:
                import asyncio
                cmd = action[5:]
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.luna_callback(cmd))
                except RuntimeError:
                    logger.info(f"Luna command (no event loop): {cmd}")

        # Show LCD label on press
        if pressed:
            btn_config = self.button_map.get(button_id, {})
            label = btn_config.get("lcd_label")
            if label:
                self.lcd_text(label)

    def _handle_line(self, line: str):
        """Parse one line from the Mega."""
        line = line.strip()
        if not line:
            return

        if line == "READY":
            logger.info("Controller ready")
            idle = self.profile.get("lcd_idle", {})
            if "icon" in idle:
                self.lcd_icon(idle["icon"])
            elif "text" in idle:
                self.lcd_text(idle["text"])
            return

        if line.startswith("BTN:"):
            parts = line.split(":")
            if len(parts) == 3:
                _, btn_id, state = parts
                pressed = (state == "1")
                btn_cfg = self.button_map.get(btn_id)
                if btn_cfg:
                    self._handle_action(btn_cfg["action"], pressed, btn_id)
                else:
                    logger.debug(f"Unmapped button: {btn_id}")
            return

        if line.startswith("PROFILE:"):
            logger.info(f"Mega confirmed profile: {line[8:]}")
            return

        if line.startswith("WARN:"):
            logger.warning(f"Controller: {line}")
            return

    # ── Main loop ────────────────────────────────────────────
    def run(self):
        """Blocking main loop — reads serial, dispatches actions."""
        self.connect()
        self._running = True
        backend = "evdev/uinput" if IS_LINUX else "pynput"
        print(f"Bridge active — profile: {self.active_profile}, keyboard: {backend}")
        print("Press Ctrl-C to quit.\n")

        try:
            while self._running:
                line = self.ser.readline()
                if line:
                    self._handle_line(line.decode(errors="replace"))
        except KeyboardInterrupt:
            print("\nShutting down.")
        finally:
            self.stop()

    def stop(self):
        self._running = False
        # Release held keys
        for key in list(self._held_keys):
            try:
                self.keyboard.release(key)
            except Exception:
                pass
        self._held_keys.clear()
        self.keyboard.close()
        if self.ser and self.ser.is_open:
            self.lcd_clear()
            self.ser.close()


def main():
    parser = argparse.ArgumentParser(description="Luna Arcade Controller Bridge")
    parser.add_argument("--port", help="Serial port (auto-detect if omitted)")
    parser.add_argument("--profile", default=None, help="Starting profile name")
    parser.add_argument("--config", default=None, help="Path to button_config.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    config_path = Path(args.config) if args.config else CONFIG_PATH
    bridge = ArcadeBridge(config_path=config_path, port=args.port, profile=args.profile)
    bridge.run()


if __name__ == "__main__":
    main()
