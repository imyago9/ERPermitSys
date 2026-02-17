from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import threading
import time
from typing import Any, Mapping


def _load_scapture_module():
    module_path = Path(__file__).resolve().with_name("scapture.py")
    spec = importlib.util.spec_from_file_location("erpermitsys_space_scapture", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load scapture module at {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scapture = _load_scapture_module()


class SpaceBackend:
    def __init__(self, context) -> None:
        self._context = context
        context_settings_path = getattr(context, "settings_path", None)
        self._settings_path = (
            Path(context_settings_path)
            if isinstance(context_settings_path, (str, Path))
            else Path(context.data_dir) / "settings.json"
        )
        self._settings = self._load_settings()
        self._preferred_audio_device_id = str(self._settings.get("audioDeviceId") or "").strip()
        self._preferred_audio_device_name = str(self._settings.get("audioDeviceName") or "").strip()

        self._audio_devices = []
        self._device_names = []
        self._selected_index = -1
        self._audio_ready = False
        self._capture_ready = False
        self._audio_enabled = bool(self._settings.get("audioEnabled", False))
        self._background_enabled = bool(self._settings.get("backgroundEnabled", True))
        self._day_night_auto = bool(self._settings.get("dayNightAuto", True))
        self._last_message = ""

        self._anim_mode = str(self._settings.get("animMode") or "focus").strip().lower()
        if self._anim_mode not in ("focus", "hyper", "minimal"):
            self._anim_mode = "focus"
        self._note_grid_enabled = False
        self._note_grid_mode = "pitch-class"
        self._note_grid_layout = "auto"

        self._analyzer = scapture.AudioAnalyzer(bars=10)
        self._stop_event = threading.Event()
        self._thread = None
        self._latest_audio_payload: dict[str, Any] | None = None
        self._latest_audio_seq = 0
        self._audio_restart_attempt_at = 0.0

        self._reset_audio_smooth()

    def start(self, context=None) -> None:
        if context is not None:
            self._context = context
        self._refresh_audio_devices()
        if self._audio_enabled:
            ok, message = self._start_analyzer()
            self._audio_enabled = bool(ok)
            if ok:
                self._start_thread()
            else:
                self._send_audio_reset()
            self._emit_event("audio_status", {"message": message, "ok": ok})
        self._persist_ui_settings()
        self._emit_event("state", {"state": self.get_state()})

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._analyzer.stop()

    def handle_command(
        self,
        command: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = dict(payload or {})
        handlers = {
            "space.audio.enable": self.cmd_audio_enable,
            "space.audio.device.select": self.cmd_audio_device_select,
            "space.audio.peek": self.cmd_audio_peek,
            "space.anim.mode": self.cmd_anim_mode,
            "space.background.enable": self.cmd_background_enable,
            "space.daynight.auto": self.cmd_daynight_auto,
            "space.note.grid": self.cmd_note_grid,
            "space.location.set": self.cmd_location_set,
            "space.state.get": lambda _: {"ok": True, "state": self.get_state()},
        }
        handler = handlers.get((command or "").strip())
        if handler is None:
            return {"ok": False, "error": f"Unknown command: {command}"}
        result = handler(args)
        if result is None:
            return {"ok": True}
        if not isinstance(result, dict):
            return {"ok": True, "result": result}
        result.setdefault("ok", True)
        return result

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "state": self.get_state(),
            "audio_thread_active": bool(self._thread and self._thread.is_alive()),
            "audio_analyzer_active": self._analyzer.is_active(),
            "audio_seq": self._latest_audio_seq,
        }

    def get_state(self) -> dict[str, Any]:
        self._refresh_audio_devices()
        return {
            "nav": {
                "path": "/space",
                "views": [
                    {"path": "/space", "label": "Space", "parent": "/"},
                ],
            },
            "location": dict(self._settings),
            "audio": {
                "ready": self._audio_ready,
                "captureReady": self._capture_ready,
                "devices": list(self._device_names),
                "selected": self._selected_index,
                "enabled": self._audio_enabled,
                "message": self._last_message,
                "seq": self._latest_audio_seq,
                "analyzerActive": self._analyzer.is_active(),
            },
            "view": {
                "backgroundEnabled": self._background_enabled,
                "dayNightAuto": self._day_night_auto,
            },
            "anim": {"mode": self._anim_mode},
            "note": {
                "enabled": self._note_grid_enabled,
                "mode": self._note_grid_mode,
                "layout": self._note_grid_layout,
            },
        }

    def cmd_audio_enable(self, args: Mapping[str, Any]) -> dict[str, Any]:
        enabled = bool(args.get("enabled"))
        if enabled:
            ok, message = self._start_analyzer()
            self._audio_enabled = bool(ok)
            if ok:
                self._start_thread()
            else:
                self._send_audio_reset()
            self._persist_ui_settings()
            self._emit_event("audio_status", {"message": message, "ok": ok})
            self._emit_event("state", {"state": self.get_state()})
            return {"ok": ok, "message": message, "enabled": self._audio_enabled}
        self._audio_enabled = False
        self._stop_analyzer()
        self._last_message = "Audio off"
        self._send_audio_reset()
        self._persist_ui_settings()
        self._emit_event("audio_status", {"message": "Audio off", "ok": True})
        self._emit_event("state", {"state": self.get_state()})
        return {"ok": True, "message": "Audio off", "enabled": self._audio_enabled}

    def cmd_audio_device_select(self, args: Mapping[str, Any]) -> dict[str, Any]:
        try:
            index = int(args.get("index", -1))
        except Exception:
            index = -1
        self._selected_index = index
        ok = True
        message = "Device selected"
        self._remember_selected_audio_device()
        if self._audio_enabled:
            ok, message = self._start_analyzer()
            self._audio_enabled = bool(ok)
            if not ok:
                self._send_audio_reset()
            self._emit_event("audio_status", {"message": message, "ok": ok})
        else:
            self._last_message = message
        self._emit_event("state", {"state": self.get_state()})
        return {"ok": ok, "message": message, "enabled": self._audio_enabled}

    def cmd_anim_mode(self, args: Mapping[str, Any]) -> dict[str, Any]:
        mode = (args.get("mode") or "").strip().lower()
        if mode in ("focus", "hyper", "minimal"):
            self._anim_mode = mode
            self._persist_ui_settings()
        return {"ok": True, "mode": self._anim_mode}

    def cmd_background_enable(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._background_enabled = bool(args.get("enabled", True))
        self._persist_ui_settings()
        self._emit_event("state", {"state": self.get_state()})
        return {"ok": True, "enabled": self._background_enabled}

    def cmd_daynight_auto(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._day_night_auto = bool(args.get("enabled", True))
        self._persist_ui_settings()
        self._emit_event("state", {"state": self.get_state()})
        return {"ok": True, "enabled": self._day_night_auto}

    def cmd_audio_peek(self, args: Mapping[str, Any]) -> dict[str, Any]:
        since = -1
        try:
            since = int(args.get("since", -1))
        except Exception:
            since = -1
        seq = int(self._latest_audio_seq)
        payload = self._latest_audio_payload
        changed = seq != since and payload is not None
        return {
            "ok": True,
            "enabled": bool(self._audio_enabled),
            "seq": seq,
            "changed": bool(changed),
            "payload": dict(payload) if changed and isinstance(payload, dict) else None,
        }

    def cmd_note_grid(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._note_grid_enabled = bool(args.get("enabled"))
        mode = (args.get("mode") or "").strip()
        layout = (args.get("layout") or "").strip()
        if mode:
            self._note_grid_mode = mode
        if layout:
            self._note_grid_layout = layout
        return {
            "ok": True,
            "enabled": self._note_grid_enabled,
            "mode": self._note_grid_mode,
            "layout": self._note_grid_layout,
        }

    def cmd_location_set(self, args: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(args, dict):
            return {"ok": False, "message": "Invalid payload"}
        updated = False
        for key in ("zip", "timeZone"):
            if key in args and isinstance(args[key], str) and args[key].strip():
                self._settings[key] = args[key].strip()
                updated = True
        for key in ("lat", "lon"):
            if key in args:
                try:
                    value = float(args[key])
                except Exception:
                    continue
                self._settings[key] = value
                updated = True
        if updated:
            self._save_settings()
            self._emit_event("location", dict(self._settings))
        return {"ok": True, "location": dict(self._settings)}

    def _emit_event(self, event_type: str, payload: Mapping[str, Any] | None = None) -> None:
        context = self._context
        if context is None:
            return
        try:
            context.emit_event(event_type, dict(payload or {}))
        except Exception:
            return

    def _refresh_audio_devices(self):
        generic_messages = {
            "",
            "Install soundcard",
            "No audio output devices",
            "Install numpy to enable audio reactivity",
            "Ready",
        }
        soundcard_available = scapture.load_soundcard() is not None
        self._capture_ready = bool(scapture.capture_ready())
        if not soundcard_available:
            self._audio_ready = False
            self._audio_devices = []
            self._device_names = []
            self._selected_index = -1
            self._last_message = "Install soundcard"
            return
        speakers = scapture.list_speakers()
        if not speakers:
            self._audio_ready = False
            self._audio_devices = []
            self._device_names = []
            self._selected_index = -1
            self._last_message = "No audio output devices"
            return
        self._audio_ready = True
        self._audio_devices = speakers
        self._device_names = [scapture.speaker_name(s) for s in speakers]
        if self._selected_index < 0 or self._selected_index >= len(speakers):
            preferred = self._preferred_speaker_index(speakers)
            if preferred is None:
                preferred = scapture.find_speaker_index(speakers, "Artic")
            if preferred is None:
                preferred = scapture.default_speaker_index(speakers)
            self._selected_index = preferred or 0
        if self._capture_ready:
            if self._last_message in generic_messages:
                if self._audio_enabled and 0 <= self._selected_index < len(self._audio_devices):
                    self._last_message = scapture.speaker_name(self._audio_devices[self._selected_index])
                else:
                    self._last_message = "Ready"
        else:
            self._last_message = "Install numpy to enable audio reactivity"

    def _start_analyzer(self):
        self._refresh_audio_devices()
        if not self._audio_ready:
            return False, self._last_message
        if not self._capture_ready:
            return False, self._last_message
        if self._selected_index < 0 or self._selected_index >= len(self._audio_devices):
            self._last_message = "Select output device"
            return False, self._last_message
        speaker = self._audio_devices[self._selected_index]
        ok, message = self._analyzer.start(speaker)
        if ok:
            self._last_message = scapture.speaker_name(speaker)
            self._remember_selected_audio_device()
            return True, self._last_message
        self._last_message = message or "Unable to capture output"
        return False, self._last_message

    def _preferred_speaker_index(self, speakers) -> int | None:
        preferred_id = self._preferred_audio_device_id
        preferred_name = self._preferred_audio_device_name
        if preferred_id:
            for idx, speaker in enumerate(speakers):
                if str(getattr(speaker, "id", "") or "").strip() == preferred_id:
                    return idx
        if preferred_name:
            exact = preferred_name.casefold()
            for idx, speaker in enumerate(speakers):
                if scapture.speaker_name(speaker).casefold() == exact:
                    return idx
            for idx, speaker in enumerate(speakers):
                if exact in scapture.speaker_name(speaker).casefold():
                    return idx
        return None

    def _remember_selected_audio_device(self) -> None:
        if self._selected_index < 0 or self._selected_index >= len(self._audio_devices):
            return
        speaker = self._audio_devices[self._selected_index]
        self._preferred_audio_device_id = str(getattr(speaker, "id", "") or "").strip()
        self._preferred_audio_device_name = scapture.speaker_name(speaker)
        self._settings["audioDeviceId"] = self._preferred_audio_device_id
        self._settings["audioDeviceName"] = self._preferred_audio_device_name
        self._save_settings()

    def _persist_ui_settings(self) -> None:
        self._settings["audioEnabled"] = bool(self._audio_enabled)
        self._settings["backgroundEnabled"] = bool(self._background_enabled)
        self._settings["dayNightAuto"] = bool(self._day_night_auto)
        self._settings["animMode"] = self._anim_mode
        self._save_settings()

    def _stop_analyzer(self):
        self._analyzer.stop()
        self._reset_audio_smooth()

    def _start_thread(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._audio_loop, daemon=True)
        self._thread.start()

    def _audio_loop(self):
        while not self._stop_event.is_set():
            if not self._audio_enabled:
                time.sleep(0.2)
                continue
            if not self._analyzer.is_active():
                error_text = (self._analyzer.take_error() or "").strip()
                if error_text:
                    self._last_message = f"Capture error: {error_text}"
                    self._emit_event("audio_status", {"message": self._last_message, "ok": False})
                    self._emit_event("state", {"state": self.get_state()})
                now = time.monotonic()
                if now - self._audio_restart_attempt_at >= 1.0:
                    self._audio_restart_attempt_at = now
                    ok, message = self._start_analyzer()
                    if ok:
                        self._emit_event("audio_status", {"message": message, "ok": True})
                        self._emit_event("state", {"state": self.get_state()})
                time.sleep(0.2)
                continue
            self._audio_restart_attempt_at = 0.0
            try:
                levels = self._analyzer.get_levels()
                features = self._analyzer.get_features()
                if self._note_grid_enabled:
                    note_levels = self._analyzer.get_note_levels(self._note_grid_mode)
                else:
                    note_levels = []
                payload = self._compute_audio_payload(levels, features, note_levels)
                if payload:
                    self._publish_audio_payload(payload)
            except Exception as exc:
                self._last_message = f"Audio processing error: {exc}"
                self._emit_event("audio_status", {"message": self._last_message, "ok": False})
                self._emit_event("state", {"state": self.get_state()})
                time.sleep(0.2)
                continue
            time.sleep(0.016)

    def _send_audio_reset(self):
        payload = {
            "params": {
                "nodeSpeed": 1.0,
                "shooterRate": 1.0,
                "cometRate": 1.0,
                "dust": 1.0,
                "linkAlpha": 1.0,
                "camSpeed": 1.0,
                "nebula": 1.0,
                "starAlpha": 1.0,
                "linkDist": 1.0,
                "nodeCount": 120,
                "starCount": 900,
            },
            "palette": {
                "hue": 0,
                "mix": 0,
                "pulse": 0,
                "vivid": 0,
                "rest": 0,
                "spread": 0,
            },
            "bands": [],
            "energy": 0,
            "punch": 0,
            "complexity": 0,
            "flow": 0,
            "warmth": 0,
            "air": 0,
            "noteLevels": [],
        }
        self._publish_audio_payload(payload)

    def _publish_audio_payload(self, payload: Mapping[str, Any]) -> None:
        data = dict(payload or {})
        self._latest_audio_seq += 1
        self._latest_audio_payload = data
        self._emit_event("audio", data)

    def _reset_audio_smooth(self):
        self._audio_smooth = {}
        self._audio_params = {}
        self._audio_band_smooth = None
        self._audio_centroid = 0.5
        self._audio_key_vec = (1.0, 0.0)
        self._audio_last_update = time.monotonic()
        self._audio_prev_levels = None
        self._audio_prev_avg_raw = 0.0
        self._audio_pulse = 0.0
        self._audio_key_index = 0

    def _smooth_audio_ar(self, key, value, attack=0.25, release=0.12):
        current = self._audio_smooth.get(key, 0.0)
        alpha = attack if value > current else release
        updated = current + (value - current) * alpha
        self._audio_smooth[key] = updated
        return updated

    def _smooth_param(self, key, value, attack=0.22, release=0.14):
        current = self._audio_params.get(key, value)
        alpha = attack if value > current else release
        updated = current + (value - current) * alpha
        self._audio_params[key] = updated
        return updated

    def _smooth_bands(self, bands, attack=0.45, release=0.2):
        if self._audio_band_smooth is None or len(self._audio_band_smooth) != len(bands):
            self._audio_band_smooth = list(bands)
            return list(bands)
        smoothed = self._audio_band_smooth
        for i, value in enumerate(bands):
            current = smoothed[i]
            alpha = attack if value > current else release
            smoothed[i] = current + (value - current) * alpha
        return list(smoothed)

    def _compute_audio_payload(self, levels, features=None, note_levels=None):
        if not levels:
            return None
        mode = self._anim_mode
        if mode not in ("focus", "hyper", "minimal"):
            mode = "focus"
        features = features or {}
        bands = [max(0.0, min(float(v), 1.0)) for v in levels]
        bands = self._smooth_bands(bands)

        peak_raw = max(bands) if bands else 0.0
        total = sum(bands)
        avg_raw = total / len(bands) if bands else 0.0
        third = max(1, len(bands) // 3)
        low_raw = sum(bands[:third]) / third
        mid_raw = sum(bands[third:2 * third]) / third
        high_raw = sum(bands[2 * third:]) / max(1, len(bands) - 2 * third)

        avg = self._smooth_audio_ar("avg", avg_raw, 0.3, 0.12)
        low = self._smooth_audio_ar("low", low_raw, 0.32, 0.15)
        mid = self._smooth_audio_ar("mid", mid_raw, 0.3, 0.14)
        high = self._smooth_audio_ar("high", high_raw, 0.32, 0.15)
        peak = self._smooth_audio_ar("peak", peak_raw, 0.35, 0.18)

        now = time.monotonic()
        dt = max(0.0, now - getattr(self, "_audio_last_update", now))
        self._audio_last_update = now

        entropy = 0.0
        if total > 1e-6 and len(bands) > 1:
            inv = 1.0 / total
            ent_sum = 0.0
            for value in bands:
                p = value * inv
                if p > 1e-9:
                    ent_sum -= p * math.log(p)
            entropy = ent_sum / math.log(len(bands))
        entropy = max(0.0, min(entropy, 1.0))

        flux_spec = float(features.get("flux", 0.0) or 0.0)
        if self._audio_prev_levels is None:
            flux_levels = 0.0
        else:
            flux_sum = 0.0
            for value, prev in zip(bands, self._audio_prev_levels):
                if value > prev:
                    flux_sum += value - prev
            flux_levels = flux_sum / (total + 1e-6)
        self._audio_prev_levels = list(bands)
        flux = max(flux_spec, flux_levels)

        delta = abs(avg_raw - self._audio_prev_avg_raw)
        self._audio_prev_avg_raw = avg_raw
        stability = max(0.0, min(1.0, 1.0 - delta / 0.05))
        rest = max(0.0, min(1.0, (0.05 - avg_raw) / 0.05))

        def log_norm(value, lo, hi):
            if value <= 0.0 or hi <= lo:
                return 0.0
            return max(0.0, min(1.0, (math.log(value) - math.log(lo)) / (math.log(hi) - math.log(lo))))

        centroid_hz = float(features.get("centroid_hz", 0.0) or 0.0)
        bandwidth_hz = float(features.get("bandwidth_hz", 0.0) or 0.0)
        rolloff_hz = float(features.get("rolloff_hz", 0.0) or 0.0)
        flatness = max(0.0, min(1.0, float(features.get("flatness", 0.0) or 0.0)))
        crest = float(features.get("crest", 0.0) or 0.0)

        centroid_norm = log_norm(centroid_hz, 80.0, 12000.0)
        bandwidth_norm = log_norm(bandwidth_hz, 80.0, 9000.0)
        rolloff_norm = log_norm(rolloff_hz, 150.0, 15000.0)

        energy = self._smooth_audio_ar("energy", avg_raw, 0.3, 0.12)
        complexity = self._smooth_audio_ar("complexity", max(entropy, flatness), 0.28, 0.12)
        warmth = self._smooth_audio_ar("warmth", 1.0 - centroid_norm, 0.2, 0.1)
        air = self._smooth_audio_ar("air", max(centroid_norm, high), 0.22, 0.12)

        crest_norm = max(0.0, min(1.0, (crest - 1.0) / 6.0))
        flux_norm = max(0.0, min(1.0, flux * 2.2))
        punch_raw = max(flux_norm, crest_norm * 0.8)
        punch = self._smooth_audio_ar("punch", punch_raw, 0.4, 0.18)

        energy *= (1.0 - rest * 0.65)
        punch *= (1.0 - rest * 0.85)

        movement = max(0.0, min(1.0, energy * 0.75 + mid * 0.2 + punch * 0.35))
        density = max(0.0, min(1.0, energy * 0.6 + complexity * 0.45))

        tau_centroid = 6.0
        alpha_centroid = 1.0 - math.exp(-dt / tau_centroid) if dt > 0 else 0.0
        self._audio_centroid = self._audio_centroid + (centroid_norm - self._audio_centroid) * alpha_centroid

        tau_key = 14.0
        drift_scale = (0.2 + 0.8 * complexity) * (1.0 - rest * 0.9)
        alpha_key = (1.0 - math.exp(-dt / tau_key) if dt > 0 else 0.0) * drift_scale
        target_angle = (self._audio_centroid % 1.0) * math.tau
        target_vec = (math.cos(target_angle), math.sin(target_angle))
        prev_vec = self._audio_key_vec
        mix_x = prev_vec[0] + (target_vec[0] - prev_vec[0]) * alpha_key
        mix_y = prev_vec[1] + (target_vec[1] - prev_vec[1]) * alpha_key
        norm = math.hypot(mix_x, mix_y) or 1.0
        self._audio_key_vec = (mix_x / norm, mix_y / norm)

        def lerp(a, b, t):
            return a + (b - a) * t

        def mix_hue(a, b, t):
            delta = (b - a + 540.0) % 360.0 - 180.0
            return (a + delta * t) % 360.0

        if mode == "focus":
            node_speed = lerp(0.5, 1.25, movement)
            shooter_rate = lerp(0.25, 0.9, high)
            comet_rate = lerp(0.25, 0.85, mid)
            dust = lerp(0.7, 1.6, low)
            link_alpha = lerp(0.6, 1.1, energy)
            cam_speed = lerp(0.6, 1.35, movement)
            nebula = lerp(0.8, 1.9, low)
            star_alpha = lerp(0.9, 1.8, high)
            node_count = int(90 + density * 130)
            star_count = int(620 + density * 520)
            link_dist = lerp(0.9, 1.35, mid)
            pulse_scale = 0.25
        elif mode == "hyper":
            node_speed = lerp(0.8, 1.8, movement)
            shooter_rate = lerp(0.35, 1.25, high)
            comet_rate = lerp(0.4, 1.15, mid)
            dust = lerp(0.9, 2.1, low)
            link_alpha = lerp(0.85, 1.55, energy)
            cam_speed = lerp(0.9, 1.85, movement)
            nebula = lerp(1.0, 2.5, low)
            star_alpha = lerp(1.0, 2.1, high)
            node_count = int(120 + density * 140)
            star_count = int(760 + density * 640)
            link_dist = lerp(1.0, 1.55, mid)
            pulse_scale = 0.32
        else:
            node_speed = lerp(0.35, 0.9, movement)
            shooter_rate = lerp(0.12, 0.5, high)
            comet_rate = lerp(0.12, 0.45, mid)
            dust = lerp(0.45, 1.0, low)
            link_alpha = lerp(0.4, 0.85, energy)
            cam_speed = lerp(0.4, 0.95, movement)
            nebula = lerp(0.55, 1.2, low)
            star_alpha = lerp(0.6, 1.25, high)
            node_count = int(60 + density * 80)
            star_count = int(360 + density * 360)
            link_dist = lerp(0.65, 1.05, mid)
            pulse_scale = 0.18

        self._audio_pulse = max(punch, self._audio_pulse * 0.86)
        pulse = max(0.0, min(self._audio_pulse, 1.0))
        pulse = pulse * pulse_scale * (1.0 - rest * 0.9)

        node_speed += pulse * 0.35
        shooter_rate += pulse * 0.5
        comet_rate += pulse * 0.35
        cam_speed += pulse * 0.4
        link_alpha += pulse * 0.25

        node_speed = self._smooth_param("node_speed", max(0.1, min(node_speed, 2.5)))
        shooter_rate = self._smooth_param("shooter_rate", max(0.1, min(shooter_rate, 2.5)))
        comet_rate = self._smooth_param("comet_rate", max(0.1, min(comet_rate, 2.5)))
        dust = self._smooth_param("dust", max(0.1, min(dust, 2.5)))
        link_alpha = self._smooth_param("link_alpha", max(0.1, min(link_alpha, 2.0)))
        cam_speed = self._smooth_param("cam_speed", max(0.2, min(cam_speed, 2.5)))
        nebula = self._smooth_param("nebula", max(0.2, min(nebula, 3.0)))
        star_alpha = self._smooth_param("star_alpha", max(0.1, min(star_alpha, 2.5)))
        link_dist = self._smooth_param("link_dist", max(0.3, min(link_dist, 2.0)))
        node_count = int(self._smooth_param("node_count", max(10, min(node_count, 240)), 0.18, 0.08))
        star_count = int(self._smooth_param("star_count", max(100, min(star_count, 1200)), 0.18, 0.08))

        note_hues = (0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330)
        dominant_index = max(range(len(bands)), key=bands.__getitem__) if bands else 0
        dominant_level = bands[dominant_index] if bands else 0.0
        span = max(1, len(bands) - 1)
        target_index = int(round(dominant_index / span * 11)) % 12
        self._audio_key_index = target_index

        hue = note_hues[self._audio_key_index]
        hue_shift = (centroid_norm - 0.5) * 50.0 + (warmth - 0.5) * 18.0
        hue = (hue + hue_shift) % 360.0

        timbre_bias = (warmth - air) * 0.7 + (low - high) * 0.6
        timbre_bias = max(-1.0, min(timbre_bias, 1.0))
        mood_hue = mix_hue(210.0, 28.0, (timbre_bias + 1.0) * 0.5)
        mood_mix = 0.22 + abs(timbre_bias) * 0.4 + complexity * 0.25
        mood_mix = max(0.0, min(mood_mix, 0.85))
        hue = mix_hue(hue, mood_hue, mood_mix)

        if dominant_level < 0.035 and avg_raw < 0.012 and peak_raw < 0.08:
            hue = mix_hue(hue, 215.0, 0.7)

        mix = 0.08 + energy * 0.36 + complexity * 0.25 + punch * 0.1
        vivid = 0.15 + energy * 0.35 + air * 0.35 + complexity * 0.25
        mix *= 1.0 - 0.35 * stability
        vivid *= 1.0 - 0.3 * stability
        mix *= (1.0 - 0.75 * rest)
        vivid *= (1.0 - 0.85 * rest)
        mix = max(0.0, min(mix, 0.8))
        vivid = max(0.0, min(vivid, 1.0))

        spread = 8.0 + 18.0 * bandwidth_norm + 16.0 * complexity + 10.0 * air + 6.0 * rolloff_norm
        spread += 8.0 * air - 6.0 * warmth
        spread *= (1.0 - 0.3 * rest)
        spread = max(6.0, min(spread, 60.0))
        flow = max(0.0, min(1.0, 0.2 + movement * 0.9 + punch * 0.4))

        return {
            "params": {
                "nodeSpeed": node_speed,
                "shooterRate": shooter_rate,
                "cometRate": comet_rate,
                "dust": dust,
                "linkAlpha": link_alpha,
                "camSpeed": cam_speed,
                "nebula": nebula,
                "starAlpha": star_alpha,
                "linkDist": link_dist,
                "nodeCount": node_count,
                "starCount": star_count,
            },
            "palette": {
                "hue": hue,
                "mix": mix,
                "pulse": pulse,
                "vivid": vivid,
                "rest": rest,
                "spread": spread,
            },
            "bands": bands,
            "energy": energy,
            "punch": punch,
            "complexity": complexity,
            "flow": flow,
            "warmth": warmth,
            "air": air,
            "noteLevels": note_levels or [],
        }

    def _load_settings(self):
        defaults = {
            "zip": "00000",
            "lat": 0.0,
            "lon": 0.0,
            "timeZone": "UTC",
            "audioDeviceId": "",
            "audioDeviceName": "",
            "audioEnabled": False,
            "backgroundEnabled": True,
            "dayNightAuto": True,
            "animMode": "focus",
        }
        context = self._context
        if context is not None:
            loader = getattr(context, "load_settings", None)
            if callable(loader):
                try:
                    data = loader(defaults=defaults)
                except Exception:
                    data = None
                if isinstance(data, dict):
                    merged = dict(defaults)
                    merged.update({k: data[k] for k in data.keys() if k in defaults})
                    return merged
        if not self._settings_path.exists():
            return dict(defaults)
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return dict(defaults)
            merged = dict(defaults)
            merged.update({k: data[k] for k in data.keys() if k in defaults})
            return merged
        except Exception:
            return dict(defaults)

    def _save_settings(self):
        context = self._context
        if context is not None:
            saver = getattr(context, "save_settings", None)
            if callable(saver):
                try:
                    saver(self._settings)
                    return
                except Exception:
                    pass
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(
                json.dumps(self._settings, indent=2),
                encoding="utf-8",
            )
        except Exception:
            return


def create_backend(context):
    return SpaceBackend(context)
