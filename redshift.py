#!/usr/bin/env python3
import atexit
import ctypes
import json
import logging
import sys
import threading
import traceback
from pathlib import Path
from typing import List

import pystray
import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageDraw

if sys.platform == "darwin":
    import AppKit
    import Foundation
    import objc
else:
    AppKit = None
    Foundation = None
    objc = None


APP_NAME = "RedShift"
APP_VERSION = "1.0.0"
APP_BUNDLE_ID = "com.redshift.app"
SETTINGS_DIR = Path.home() / ".redshift"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"
LOG_FILE = SETTINGS_DIR / "redshift.log"
TABLE_SIZE = 256
WINDOWS_REAPPLY_SECONDS = 5.0
MACOS_REAPPLY_MS = 5000


if Foundation is not None:
    class MacMenuTarget(Foundation.NSObject):
        def initWithOwner_(self, owner: object) -> object:
            self = objc.super(MacMenuTarget, self).init()
            if self is None:
                return None
            self.owner = owner
            return self

        def sliderChanged_(self, sender: object) -> None:
            self.owner.set_intensity(int(round(sender.doubleValue())))

        def turnOff_(self, sender: object) -> None:
            self.owner.set_intensity(0)

        def quit_(self, sender: object) -> None:
            self.owner.quit_app()
else:
    MacMenuTarget = None


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def lerp(start: float, end: float, amount: float) -> float:
    return start + (end - start) * amount


def intensity_to_multipliers(value: int) -> tuple[float, float, float]:
    intensity = clamp(value / 100.0, 0.0, 1.0)
    red_multiplier = 1.0
    green_multiplier = 1.0 - intensity
    blue_multiplier = max(0.0, 1.0 - intensity * 1.5)
    return red_multiplier, green_multiplier, blue_multiplier


class RedShiftApp:
    def __init__(self) -> None:
        self.is_macos = sys.platform == "darwin"
        self.is_windows = sys.platform.startswith("win")
        if not (self.is_macos or self.is_windows):
            raise RuntimeError(f"{APP_NAME} supports macOS and Windows only.")

        self.intensity = self.load_intensity()
        self._quitting = False
        self._restored = False
        self._lock = threading.RLock()
        self._windows_timer: threading.Timer | None = None
        self._macos_timer: threading.Timer | None = None
        self._last_macos_display_count: int | None = None
        self._ns_app = None
        self._macos_status_item = None
        self._macos_menu = None
        self._macos_target = None
        self._macos_status_label = None
        self._macos_percent_label = None
        self._macos_slider = None
        self._macos_turn_off_item = None

        self.root = None
        if not self.is_macos:
            self.root = tk.Tk()
            self.root.title(APP_NAME)
            self.root.geometry("360x190")
            self.root.resizable(False, False)
            self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
            self.root.wm_attributes("-topmost", True)
            self.root.withdraw()

        self.status_var = tk.StringVar() if not self.is_macos else None
        self.percent_var = tk.StringVar() if not self.is_macos else None
        self.scale = None
        self.status_label = None
        self.percent_label = None
        self.swatch = None
        self.icon: pystray.Icon | None = None

        self._init_platform()
        if self.is_macos:
            self._build_macos_menu_bar()
        else:
            self._build_window()
        self.apply_filter(self.intensity, persist=False, update_ui=True)
        if not self.is_macos:
            self._start_tray_thread()
        self._schedule_windows_reapply()
        self._schedule_macos_reapply()
        atexit.register(self._restore_on_exit)

    def _init_platform(self) -> None:
        if self.is_macos:
            self._init_macos_gamma()
        elif self.is_windows:
            self._init_windows_gamma()

    def _configure_macos_app_mode(self) -> None:
        try:
            self._ns_app = AppKit.NSApplication.sharedApplication()
            self._ns_app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        except Exception:
            logging.exception("Failed to switch macOS app to accessory mode.")

    def _init_macos_gamma(self) -> None:
        self.cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        self.cg.CGGetActiveDisplayList.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self.cg.CGGetActiveDisplayList.restype = ctypes.c_int32
        self.cg.CGGetOnlineDisplayList.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self.cg.CGGetOnlineDisplayList.restype = ctypes.c_int32
        self.cg.CGSetDisplayTransferByTable.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
        ]
        self.cg.CGSetDisplayTransferByTable.restype = ctypes.c_int32
        self.cg.CGDisplayRestoreColorSyncSettings.argtypes = []
        self.cg.CGDisplayRestoreColorSyncSettings.restype = None

    def _init_windows_gamma(self) -> None:
        class DISPLAY_DEVICEW(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_uint32),
                ("DeviceName", ctypes.c_wchar * 32),
                ("DeviceString", ctypes.c_wchar * 128),
                ("StateFlags", ctypes.c_uint32),
                ("DeviceID", ctypes.c_wchar * 128),
                ("DeviceKey", ctypes.c_wchar * 128),
            ]

        self.DISPLAY_DEVICEW = DISPLAY_DEVICEW
        self.DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32

        self.user32.EnumDisplayDevicesW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.POINTER(DISPLAY_DEVICEW),
            ctypes.c_uint32,
        ]
        self.user32.EnumDisplayDevicesW.restype = ctypes.c_int
        self.gdi32.CreateDCW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
        ]
        self.gdi32.CreateDCW.restype = ctypes.c_void_p
        self.gdi32.SetDeviceGammaRamp.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gdi32.SetDeviceGammaRamp.restype = ctypes.c_int
        self.gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
        self.gdi32.DeleteDC.restype = ctypes.c_int

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint32),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", ctypes.c_uint32),
                ("szDevice", ctypes.c_wchar * 32),
            ]

        self.RECT = RECT
        self.MONITORINFOEXW = MONITORINFOEXW
        self.MONITORINFOF_PRIMARY = 0x00000001
        self.MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(RECT),
            ctypes.c_void_p,
        )
        self.user32.EnumDisplayMonitors.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            self.MONITORENUMPROC,
            ctypes.c_void_p,
        ]
        self.user32.EnumDisplayMonitors.restype = ctypes.c_int
        self.user32.GetMonitorInfoW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(MONITORINFOEXW),
        ]
        self.user32.GetMonitorInfoW.restype = ctypes.c_int

    def _build_window(self) -> None:
        style = ttk.Style(self.root)
        if "aqua" in style.theme_names():
            style.theme_use("aqua")

        outer = ttk.Frame(self.root, padding=(18, 16, 18, 14))
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X)

        self.swatch = tk.Canvas(header, width=28, height=28, highlightthickness=0, bd=0)
        self.swatch.pack(side=tk.LEFT)

        title_group = ttk.Frame(header)
        title_group.pack(side=tk.LEFT, padx=(10, 0), fill=tk.X, expand=True)

        title = ttk.Label(title_group, text=APP_NAME, font=("Helvetica Neue", 15, "bold"))
        title.pack(anchor=tk.W)

        subtitle = ttk.Label(title_group, text="Display warmth", foreground="#6e6e73")
        subtitle.pack(anchor=tk.W, pady=(1, 0))

        self.percent_label = ttk.Label(header, textvariable=self.percent_var, font=("Helvetica Neue", 14, "bold"))
        self.percent_label.pack(side=tk.RIGHT)

        self.scale = ttk.Scale(
            outer,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            length=314,
            command=self._on_slider_move,
        )
        self.scale.pack(fill=tk.X, pady=(18, 8))
        self.scale.set(self.intensity)

        footer = ttk.Frame(outer)
        footer.pack(fill=tk.X, pady=(8, 0))

        self.status_label = ttk.Label(footer, textvariable=self.status_var, foreground="#6e6e73")
        self.status_label.pack(side=tk.LEFT)

        turn_off_button = ttk.Button(footer, text="Turn Off", command=lambda: self.set_intensity(0))
        turn_off_button.pack(side=tk.RIGHT)

        self._update_window_ui()

    def _build_macos_menu_bar(self) -> None:
        self._configure_macos_app_mode()
        self._macos_target = MacMenuTarget.alloc().initWithOwner_(self)

        status_bar = AppKit.NSStatusBar.systemStatusBar()
        self._macos_status_item = status_bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        self._set_macos_status_icon()

        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        self._macos_menu = menu

        controls_item = AppKit.NSMenuItem.alloc().init()
        controls_item.setView_(self._build_macos_controls_view())
        menu.addItem_(controls_item)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        self._macos_turn_off_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Turn Off",
            "turnOff:",
            "",
        )
        self._macos_turn_off_item.setTarget_(self._macos_target)
        menu.addItem_(self._macos_turn_off_item)

        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit RedShift", "quit:", "q")
        quit_item.setTarget_(self._macos_target)
        menu.addItem_(quit_item)

        self._macos_status_item.setMenu_(menu)

    def _set_macos_status_icon(self) -> None:
        if self._macos_status_item is None:
            return
        button = self._macos_status_item.button()
        if button is None:
            return
        try:
            image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                "sun.max.fill",
                APP_NAME,
            )
            image.setTemplate_(True)
            button.setImage_(image)
            button.setImagePosition_(AppKit.NSImageOnly)
            button.setToolTip_(self._tray_status_text())
        except Exception:
            button.setTitle_(APP_NAME)

    def _build_macos_controls_view(self) -> object:
        width = 286
        height = 138
        view = AppKit.NSView.alloc().initWithFrame_(Foundation.NSMakeRect(0, 0, width, height))

        title = AppKit.NSTextField.labelWithString_(APP_NAME)
        title.setFrame_(Foundation.NSMakeRect(16, 102, 160, 22))
        title.setFont_(AppKit.NSFont.boldSystemFontOfSize_(15))
        view.addSubview_(title)

        subtitle = AppKit.NSTextField.labelWithString_("Display warmth")
        subtitle.setFrame_(Foundation.NSMakeRect(16, 82, 160, 18))
        subtitle.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        subtitle.setFont_(AppKit.NSFont.systemFontOfSize_(12))
        view.addSubview_(subtitle)

        self._macos_percent_label = AppKit.NSTextField.labelWithString_(self._format_intensity(self.intensity))
        self._macos_percent_label.setFrame_(Foundation.NSMakeRect(210, 100, 58, 24))
        self._macos_percent_label.setAlignment_(AppKit.NSTextAlignmentRight)
        self._macos_percent_label.setFont_(AppKit.NSFont.boldSystemFontOfSize_(15))
        view.addSubview_(self._macos_percent_label)

        self._macos_slider = AppKit.NSSlider.sliderWithValue_minValue_maxValue_target_action_(
            float(self.intensity),
            0.0,
            100.0,
            self._macos_target,
            "sliderChanged:",
        )
        self._macos_slider.setFrame_(Foundation.NSMakeRect(16, 48, 254, 24))
        view.addSubview_(self._macos_slider)

        self._macos_status_label = AppKit.NSTextField.labelWithString_(self._format_window_status(self.intensity))
        self._macos_status_label.setFrame_(Foundation.NSMakeRect(16, 18, 160, 18))
        self._macos_status_label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        self._macos_status_label.setFont_(AppKit.NSFont.systemFontOfSize_(12))
        view.addSubview_(self._macos_status_label)

        off_button = AppKit.NSButton.buttonWithTitle_target_action_("Turn Off", self._macos_target, "turnOff:")
        off_button.setFrame_(Foundation.NSMakeRect(184, 12, 86, 30))
        off_button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        view.addSubview_(off_button)

        return view

    def _start_tray_thread(self) -> None:
        thread = threading.Thread(target=self._run_tray_icon, name="redshift-tray", daemon=True)
        thread.start()

    def _run_tray_icon(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem(lambda item: self._tray_status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Controls...", self._menu_adjust_filter, default=True),
            pystray.MenuItem("Turn Off", self._menu_turn_off),
            pystray.MenuItem("Quit", self._menu_quit),
        )
        kwargs = {}
        if self.is_macos and self._ns_app is not None:
            kwargs["darwin_nsapplication"] = self._ns_app
        self.icon = pystray.Icon("redshift", self._generate_icon(self.intensity), APP_NAME, menu, **kwargs)
        self.icon.run()

    def _menu_adjust_filter(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.root.after(0, self.show_window)

    def _menu_turn_off(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.root.after(0, lambda: self.set_intensity(0))

    def _menu_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.root.after(0, self.quit_app)

    def _tray_status_text(self) -> str:
        return f"{APP_NAME}: {self._format_intensity(self.intensity)}"

    def _format_intensity(self, value: int) -> str:
        if value <= 0:
            return "OFF"
        if value >= 100:
            return "MAX"
        return f"{value}%"

    def _format_window_status(self, value: int) -> str:
        if value <= 0:
            return "OFF"
        if value >= 100:
            return "MAX (Red)"
        return f"{value}%"

    def _generate_icon(self, value: int) -> Image.Image:
        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        if value <= 50:
            mix = value / 50.0
            color = (
                int(lerp(220, 255, mix)),
                int(lerp(220, 145, mix)),
                int(lerp(220, 40, mix)),
            )
        else:
            mix = (value - 50) / 50.0
            color = (
                int(lerp(255, 220, mix)),
                int(lerp(145, 15, mix)),
                int(lerp(40, 15, mix)),
            )

        draw.ellipse((8, 8, 56, 56), fill=color)
        draw.ellipse((8, 8, 56, 56), outline=(35, 35, 35), width=2)
        return image

    def _slider_color(self, value: int) -> str:
        if value <= 50:
            mix = value / 50.0
            rgb = (
                int(lerp(110, 255, mix)),
                int(lerp(110, 140, mix)),
                int(lerp(110, 0, mix)),
            )
        else:
            mix = (value - 50) / 50.0
            rgb = (
                int(lerp(255, 220, mix)),
                int(lerp(140, 0, mix)),
                0,
            )
        return "#%02x%02x%02x" % rgb

    def _on_slider_move(self, raw_value: str) -> None:
        try:
            value = int(float(raw_value))
        except ValueError:
            return
        self.set_intensity(value)

    def set_intensity(self, value: int) -> None:
        value = int(clamp(value, 0, 100))
        if value == self.intensity:
            self._update_window_ui()
            self._update_tray_ui()
            return

        self.intensity = value
        self.apply_filter(value, persist=True, update_ui=True)

    def apply_filter(self, value: int, persist: bool = True, update_ui: bool = True) -> None:
        with self._lock:
            if value <= 0:
                self._restore_platform_gamma()
            else:
                red_multiplier, green_multiplier, blue_multiplier = intensity_to_multipliers(value)
                self._apply_platform_gamma(red_multiplier, green_multiplier, blue_multiplier, value)

            if persist:
                self.save_intensity(value)

            if update_ui:
                self._update_window_ui()
                self._update_tray_ui()

    def _apply_platform_gamma(
        self,
        red_multiplier: float,
        green_multiplier: float,
        blue_multiplier: float,
        value: int,
    ) -> None:
        if self.is_macos:
            self._apply_macos_gamma(red_multiplier, green_multiplier, blue_multiplier)
        elif self.is_windows:
            self._apply_windows_gamma(red_multiplier, green_multiplier, blue_multiplier, value)

    def _restore_platform_gamma(self) -> None:
        if self.is_macos:
            self.cg.CGDisplayRestoreColorSyncSettings()
        elif self.is_windows:
            self._restore_windows_gamma()

    def _get_macos_displays(self) -> List[int]:
        display_ids: list[int] = []
        seen: set[int] = set()

        def collect(get_display_list: object, label: str) -> None:
            max_displays = 64
            displays = (ctypes.c_uint32 * max_displays)()
            count = ctypes.c_uint32(0)
            error = get_display_list(max_displays, displays, ctypes.byref(count))
            if error != 0:
                logging.warning("Failed to enumerate %s macOS displays: CoreGraphics error %s", label, error)
                return
            for display_id in displays[: count.value]:
                display_id = int(display_id)
                if display_id not in seen:
                    seen.add(display_id)
                    display_ids.append(display_id)

        collect(self.cg.CGGetActiveDisplayList, "active")
        collect(self.cg.CGGetOnlineDisplayList, "online")
        self._last_macos_display_count = len(display_ids)
        return display_ids

    def _apply_macos_gamma(self, red_multiplier: float, green_multiplier: float, blue_multiplier: float) -> None:
        red_table = [i / 255.0 * red_multiplier for i in range(TABLE_SIZE)]
        green_table = [i / 255.0 * green_multiplier for i in range(TABLE_SIZE)]
        blue_table = [i / 255.0 * blue_multiplier for i in range(TABLE_SIZE)]

        red_array = (ctypes.c_float * TABLE_SIZE)(*red_table)
        green_array = (ctypes.c_float * TABLE_SIZE)(*green_table)
        blue_array = (ctypes.c_float * TABLE_SIZE)(*blue_table)

        displays = self._get_macos_displays()
        if not displays:
            logging.warning("No macOS displays found while applying gamma table.")

        for display_id in displays:
            error = self.cg.CGSetDisplayTransferByTable(
                ctypes.c_uint32(display_id),
                ctypes.c_uint32(TABLE_SIZE),
                red_array,
                green_array,
                blue_array,
            )
            if error != 0:
                logging.warning("Failed to apply gamma table to macOS display %s: CoreGraphics error %s", display_id, error)

    def _get_windows_display_names_from_adapters(self) -> List[str]:
        devices: List[str] = []
        index = 0
        while True:
            display_device = self.DISPLAY_DEVICEW()
            display_device.cb = ctypes.sizeof(self.DISPLAY_DEVICEW)
            if not self.user32.EnumDisplayDevicesW(None, index, ctypes.byref(display_device), 0):
                break
            if display_device.StateFlags & self.DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
                devices.append(display_device.DeviceName)
            index += 1
        return devices

    def _get_windows_display_names(self) -> List[str]:
        devices: list[tuple[bool, str]] = []
        seen: set[str] = set()

        @self.MONITORENUMPROC
        def enum_monitor_proc(
            hmonitor: ctypes.c_void_p,
            hdc_monitor: ctypes.c_void_p,
            rect: ctypes.POINTER(object),
            data: ctypes.c_void_p,
        ) -> int:
            del hdc_monitor, rect, data
            monitor_info = self.MONITORINFOEXW()
            monitor_info.cbSize = ctypes.sizeof(self.MONITORINFOEXW)
            if not self.user32.GetMonitorInfoW(hmonitor, ctypes.byref(monitor_info)):
                return 1
            device_name = monitor_info.szDevice
            if device_name and device_name not in seen:
                seen.add(device_name)
                devices.append((bool(monitor_info.dwFlags & self.MONITORINFOF_PRIMARY), device_name))
            return 1

        if not self.user32.EnumDisplayMonitors(None, None, enum_monitor_proc, None):
            logging.warning("EnumDisplayMonitors failed while enumerating Windows displays.")

        if devices:
            devices.sort(key=lambda item: not item[0])
            names = [device_name for _, device_name in devices]
            logging.info("Enumerated Windows monitors for gamma ramp: %s", ", ".join(names))
            return names

        names = self._get_windows_display_names_from_adapters()
        if names:
            logging.info("Falling back to Windows display adapter enumeration: %s", ", ".join(names))
        else:
            logging.warning("No Windows displays found while enumerating gamma ramp targets.")
        return names

    def _build_windows_ramp(self, multiplier: float, value: int) -> List[int]:
        values: List[int] = []
        min_multiplier = 10.0 / 255.0

        if value >= 100 and multiplier <= 0.0:
            return [0] * TABLE_SIZE

        effective_multiplier = multiplier
        if 0 < value < 100 and multiplier < min_multiplier:
            effective_multiplier = min_multiplier

        for index in range(TABLE_SIZE):
            level = index / 255.0
            word_value = int(clamp(level * effective_multiplier, 0.0, 1.0) * 65535.0)
            if 0 < value < 100 and word_value < 256:
                word_value = 256
            values.append(clamp(word_value, 0, 65535))

        if value >= 100 and multiplier <= 0.0:
            values[0] = 0
        return [int(v) for v in values]

    def _apply_windows_gamma(
        self,
        red_multiplier: float,
        green_multiplier: float,
        blue_multiplier: float,
        value: int,
    ) -> None:
        red_values = [int((i / 255.0) * 65535.0) for i in range(TABLE_SIZE)]
        green_values = self._build_windows_ramp(green_multiplier, value)
        blue_values = self._build_windows_ramp(blue_multiplier, value)

        ramp = (ctypes.c_ushort * (TABLE_SIZE * 3))()
        for index in range(TABLE_SIZE):
            ramp[index] = red_values[index]
            ramp[TABLE_SIZE + index] = green_values[index]
            ramp[TABLE_SIZE * 2 + index] = blue_values[index]

        for device_name in self._get_windows_display_names():
            hdc = self._create_windows_display_dc(device_name)
            if not hdc:
                logging.warning("Failed to create Windows display DC for %s.", device_name)
                continue
            try:
                result = self.gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp))
                if not result:
                    logging.warning("Failed to apply gamma ramp to Windows display %s.", device_name)
                    self._restore_windows_gamma_for_device(device_name)
            finally:
                self.gdi32.DeleteDC(hdc)

    def _restore_windows_gamma(self) -> None:
        for device_name in self._get_windows_display_names():
            self._restore_windows_gamma_for_device(device_name)

    def _restore_windows_gamma_for_device(self, device_name: str) -> None:
        ramp = (ctypes.c_ushort * (TABLE_SIZE * 3))()
        for index in range(TABLE_SIZE):
            value = min(65535, index * 256)
            ramp[index] = value
            ramp[TABLE_SIZE + index] = value
            ramp[TABLE_SIZE * 2 + index] = value

        hdc = self._create_windows_display_dc(device_name)
        if not hdc:
            logging.warning("Failed to create Windows display DC for restore on %s.", device_name)
            return
        try:
            result = self.gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp))
            if not result:
                logging.warning("Failed to restore gamma ramp for Windows display %s.", device_name)
        finally:
            self.gdi32.DeleteDC(hdc)

    def _create_windows_display_dc(self, device_name: str) -> int:
        for driver_name, output_name in (
            (device_name, None),
            ("DISPLAY", device_name),
            (device_name, device_name),
        ):
            hdc = self.gdi32.CreateDCW(driver_name, output_name, None, None)
            if hdc:
                return int(hdc)
        return 0

    def _schedule_windows_reapply(self) -> None:
        if not self.is_windows or self._quitting:
            return

        def _tick() -> None:
            if self._quitting:
                return
            if self.intensity > 0:
                try:
                    self.apply_filter(self.intensity, persist=False, update_ui=False)
                finally:
                    self._update_tray_ui()
            self._schedule_windows_reapply()

        self._windows_timer = threading.Timer(WINDOWS_REAPPLY_SECONDS, _tick)
        self._windows_timer.daemon = True
        self._windows_timer.start()

    def _cancel_windows_reapply(self) -> None:
        timer = self._windows_timer
        self._windows_timer = None
        if timer is not None:
            timer.cancel()

    def _schedule_macos_reapply(self) -> None:
        if not self.is_macos or self._quitting:
            return

        def _tick() -> None:
            if self._quitting:
                return
            if self.intensity > 0:
                self.apply_filter(self.intensity, persist=False, update_ui=False)
            self._schedule_macos_reapply()

        self._macos_timer = threading.Timer(MACOS_REAPPLY_MS / 1000.0, _tick)
        self._macos_timer.daemon = True
        self._macos_timer.start()

    def _cancel_macos_reapply(self) -> None:
        timer = self._macos_timer
        self._macos_timer = None
        if timer is not None:
            timer.cancel()

    def _update_window_ui(self) -> None:
        if self.is_macos:
            self._update_macos_menu_ui()
            return
        if self.scale is None:
            return
        color = self._slider_color(self.intensity)
        if int(float(self.scale.get())) != self.intensity:
            self.scale.set(self.intensity)
        if self.swatch is not None:
            self.swatch.delete("all")
            self.swatch.create_oval(2, 2, 26, 26, fill=color, outline="#d2d2d7")
        self.percent_var.set(self._format_intensity(self.intensity))
        self.status_var.set(self._format_window_status(self.intensity))

    def _update_macos_menu_ui(self) -> None:
        formatted = self._format_intensity(self.intensity)
        if self._macos_status_item is not None:
            button = self._macos_status_item.button()
            if button is not None:
                button.setToolTip_(self._tray_status_text())
        if self._macos_percent_label is not None:
            self._macos_percent_label.setStringValue_(formatted)
        if self._macos_status_label is not None:
            self._macos_status_label.setStringValue_(self._format_window_status(self.intensity))
        if self._macos_slider is not None and int(round(self._macos_slider.doubleValue())) != self.intensity:
            self._macos_slider.setDoubleValue_(float(self.intensity))
        if self._macos_turn_off_item is not None:
            self._macos_turn_off_item.setEnabled_(self.intensity > 0)

    def _update_tray_ui(self) -> None:
        if self.is_macos:
            self._update_macos_menu_ui()
            return
        if self.icon is None:
            return
        self.icon.icon = self._generate_icon(self.intensity)
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def show_window(self) -> None:
        if self.root is None:
            return
        self._position_window_near_menu_bar()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_window(self) -> None:
        if self.root is None:
            return
        self.root.withdraw()

    def _position_window_near_menu_bar(self) -> None:
        if self.root is None:
            return
        self.root.update_idletasks()
        width = 360
        height = 190
        screen_width = self.root.winfo_screenwidth()
        x = max(16, screen_width - width - 18)
        y = 34 if self.is_macos else 80
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def load_intensity(self) -> int:
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return int(clamp(int(data.get("intensity", 0)), 0, 100))
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            return 0

    def save_intensity(self, value: int) -> None:
        try:
            SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps({"intensity": value}), encoding="utf-8")
        except OSError:
            pass

    def _restore_on_exit(self) -> None:
        if self._restored:
            return
        self._restored = True
        try:
            self._restore_platform_gamma()
        except Exception:
            pass

    def quit_app(self) -> None:
        if self._quitting:
            return
        self._cancel_windows_reapply()
        self._cancel_macos_reapply()
        self._restore_on_exit()
        self._quitting = True

        icon = self.icon
        self.icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

        if self.is_macos:
            if self._macos_status_item is not None:
                try:
                    AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self._macos_status_item)
                except Exception:
                    pass
                self._macos_status_item = None
            AppKit.NSApp.terminate_(None)
            return

        if self.root is not None:
            try:
                self.root.quit()
                self.root.destroy()
            except tk.TclError:
                pass

    def run(self) -> None:
        if self.is_macos:
            AppKit.NSApp.run()
        elif self.root is not None:
            self.root.mainloop()


def configure_logging() -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.info("Starting %s %s", APP_NAME, APP_VERSION)


def main() -> None:
    configure_logging()
    app = RedShiftApp()
    app.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        error_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        try:
            logging.exception("RedShift failed to start")
        except Exception:
            pass
        if sys.platform != "darwin":
            try:
                fallback_root = tk.Tk()
                fallback_root.withdraw()
                messagebox.showerror(
                    APP_NAME,
                    f"{APP_NAME} failed to start.\n\nSee log file:\n{LOG_FILE}",
                )
                fallback_root.destroy()
            except Exception:
                pass
        else:
            try:
                AppKit.NSAlert.alloc().init()
            except Exception:
                pass
        raise SystemExit(error_text)
