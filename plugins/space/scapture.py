import threading
import time
import math
import sys

try:
    from PySide6.QtCore import QSize, Qt, QTimer
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QWidget
    _QT_AVAILABLE = True
except Exception:
    QSize = None
    Qt = None
    QTimer = None
    QColor = None
    QPainter = None
    QWidget = object
    _QT_AVAILABLE = False

try:
    import numpy as np
except Exception:
    np = None

_NUMPY_ORIGINAL_FROMSTRING = None


def _patch_numpy_fromstring():
    global _NUMPY_ORIGINAL_FROMSTRING
    if np is None:
        return
    try:
        major = int(np.__version__.split(".")[0])
    except Exception:
        return
    if major < 2:
        return

    original_fromstring = np.fromstring
    _NUMPY_ORIGINAL_FROMSTRING = original_fromstring

    def _fromstring_compat(data, dtype=float, sep=""):
        if sep == "":
            try:
                return np.frombuffer(data, dtype=dtype)
            except Exception:
                try:
                    return np.frombuffer(memoryview(data), dtype=dtype)
                except Exception:
                    pass
        return original_fromstring(data, dtype=dtype, sep=sep)

    np.fromstring = _fromstring_compat


_patch_numpy_fromstring()

_sc_module = None
_sc_error = None
_sc_numpy_patched = False


def _patch_module_numpy_fromstring(module):
    if module is None or np is None:
        return
    module_dict = getattr(module, "__dict__", None)
    if not isinstance(module_dict, dict):
        return
    for name, value in list(module_dict.items()):
        if value is _NUMPY_ORIGINAL_FROMSTRING:
            module_dict[name] = np.fromstring
            continue
        if name != "fromstring":
            continue
        module_name = str(getattr(value, "__module__", ""))
        func_name = str(getattr(value, "__name__", ""))
        if func_name == "fromstring" and module_name.startswith("numpy"):
            module_dict[name] = np.fromstring


def _patch_soundcard_numpy_fromstring(sc_module):
    global _sc_numpy_patched
    if _sc_numpy_patched or np is None or sc_module is None:
        return
    modules = [sc_module]
    base_name = str(getattr(sc_module, "__name__", "soundcard"))
    for suffix in ("mediafoundation", "pulseaudio", "coreaudio"):
        full_name = f"{base_name}.{suffix}"
        submodule = sys.modules.get(full_name)
        if submodule is None:
            try:
                submodule = __import__(full_name, fromlist=["*"])
            except Exception:
                submodule = None
        if submodule is not None:
            modules.append(submodule)
    for module in modules:
        _patch_module_numpy_fromstring(module)
    _sc_numpy_patched = True


def load_soundcard():
    global _sc_module, _sc_error
    if _sc_module is False:
        return None
    if _sc_module is None:
        try:
            import soundcard as sc
        except Exception as exc:
            _sc_error = exc
            _sc_module = False
            return None
        _sc_module = sc
        _patch_soundcard_numpy_fromstring(_sc_module)
    elif _sc_module is not None:
        _patch_soundcard_numpy_fromstring(_sc_module)
    return _sc_module


def speaker_name(speaker):
    return getattr(speaker, "name", None) or str(speaker)


def list_speakers():
    sc = load_soundcard()
    if sc is None:
        return []
    try:
        return sc.all_speakers()
    except Exception:
        return []


def default_speaker_index(speakers):
    sc = load_soundcard()
    if sc is None:
        return 0
    try:
        default_speaker = sc.default_speaker()
    except Exception:
        return 0
    if default_speaker is None:
        return 0

    default_id = getattr(default_speaker, "id", None)
    default_name = speaker_name(default_speaker)
    for index, speaker in enumerate(speakers):
        if default_id is not None and getattr(speaker, "id", None) == default_id:
            return index
        if speaker_name(speaker) == default_name:
            return index
    return 0


def find_speaker_index(speakers, name_substring):
    if not name_substring:
        return None
    needle = name_substring.lower()
    for index, speaker in enumerate(speakers):
        if needle in speaker_name(speaker).lower():
            return index
    return None


def capture_ready():
    return load_soundcard() is not None and np is not None


if _QT_AVAILABLE:
    class EqualizerWidget(QWidget):
        def __init__(self, bars=16, parent=None, padding=12, spacing=6, radius=3, background=None):
            super().__init__(parent)
            self.bars = bars
            self.levels = [0.0] * bars
            self.targets = [0.0] * bars
            self.padding = padding
            self.spacing = spacing
            self.radius = radius
            self.background = background or QColor(18, 24, 38)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(60)
            self.setMinimumHeight(160)

        def sizeHint(self):
            return QSize(480, 220)

        def set_levels(self, levels):
            if not levels:
                return
            for i in range(self.bars):
                value = levels[i] if i < len(levels) else 0.0
                self.targets[i] = max(0.0, min(value, 1.0))

        def tick(self):
            # Smoothly move levels toward targets from the audio analyzer.
            for i in range(self.bars):
                current = self.levels[i]
                target = self.targets[i]
                if target > current:
                    current += (target - current) * 0.4
                else:
                    current += (target - current) * 0.2
                self.levels[i] = max(0.0, min(current, 1.0))
            self.update()

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            rect = self.rect()
            painter.fillRect(rect, self.background)

            bar_rect = rect.adjusted(self.padding, self.padding, -self.padding, -self.padding)
            if self.bars <= 0 or bar_rect.width() <= 0 or bar_rect.height() <= 0:
                return

            total_spacing = self.spacing * (self.bars - 1)
            bar_width = max(2, (bar_rect.width() - total_spacing) // self.bars)
            total_width = bar_width * self.bars + total_spacing
            x = bar_rect.x() + (bar_rect.width() - total_width) // 2
            max_h = bar_rect.height()

            for level in self.levels:
                height = int(max_h * max(0.0, min(level, 1.0)))
                y = bar_rect.bottom() - height + 1
                color = QColor(70, 220, 130) if level < 0.7 else QColor(250, 200, 90)
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(x, y, bar_width, height, self.radius, self.radius)
                x += bar_width + self.spacing


    class TinyEqualizerWidget(EqualizerWidget):
        def __init__(self, bars=10, parent=None):
            super().__init__(
                bars=bars,
                parent=parent,
                padding=2,
                spacing=2,
                radius=2,
                background=QColor(0, 0, 0, 0),
            )
            self.setMinimumHeight(18)
            self.setMaximumHeight(26)
            self.setMinimumWidth(64)
else:
    class EqualizerWidget:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("PySide6 is required for EqualizerWidget.")


    class TinyEqualizerWidget(EqualizerWidget):
        pass


class AudioAnalyzer:
    def __init__(self, bars):
        self.bars = bars
        self.blocksize = 2048
        self._samplerate = 48000
        self._lock = threading.Lock()
        self._levels = [0.0] * bars
        self._peak = 1e-6
        self._band_slices = []
        self._window = None
        self._freqs = None
        self._bin_midi = None
        self._spectrum = None
        self._prev_spectrum = None
        self._note_peaks = {}
        self._features = {
            "rms": 0.0,
            "peak": 0.0,
            "crest": 0.0,
            "centroid_hz": 0.0,
            "bandwidth_hz": 0.0,
            "rolloff_hz": 0.0,
            "flatness": 0.0,
            "flux": 0.0,
        }
        self._last_update = 0.0
        self._stop_event = threading.Event()
        self._thread = None
        self._last_error = ""

    def is_active(self):
        return self._thread is not None and self._thread.is_alive()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None
        self._stop_event.clear()
        with self._lock:
            self._last_error = ""

    def start(self, speaker):
        self.stop()
        sc = load_soundcard()
        if sc is None or np is None:
            return False, "Install soundcard and numpy to enable output capture."
        if speaker is None:
            return False, "No output device selected."
        mic = None
        last_error = ""
        candidates = []
        seen = set()
        speaker_id = getattr(speaker, "id", None)
        speaker_name = getattr(speaker, "name", None)
        for candidate in (speaker, speaker_id, speaker_name, str(speaker)):
            if not candidate:
                continue
            key = str(candidate).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)

        for candidate in candidates:
            try:
                mic = sc.get_microphone(candidate, include_loopback=True)
            except Exception as exc:
                last_error = str(exc)
                mic = None
            if mic is not None:
                break

        if mic is None:
            if last_error:
                return False, f"Unable to open loopback for selected speaker: {last_error}"
            return False, "Unable to open loopback for selected speaker."

        samplerate = getattr(mic, "samplerate", None) or getattr(speaker, "samplerate", None) or 48000
        self._samplerate = int(samplerate)
        self._window = None
        self._band_slices = []
        self._freqs = None
        self._bin_midi = None
        self._spectrum = None
        self._prev_spectrum = None
        self._note_peaks = {}
        self._peak = 1e-6
        self._last_update = 0.0
        with self._lock:
            for key in self._features:
                self._features[key] = 0.0

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._capture_loop, args=(mic,), daemon=True)
        self._thread.start()
        return True, None

    def take_error(self):
        with self._lock:
            error = self._last_error
            self._last_error = ""
        return error

    def get_levels(self):
        with self._lock:
            levels = list(self._levels)
            last_update = self._last_update
        if time.monotonic() - last_update > 0.5:
            return [0.0] * self.bars
        return levels

    def get_features(self):
        with self._lock:
            return dict(self._features)

    def get_note_levels(self, mode):
        if np is None:
            return []
        with self._lock:
            spectrum = None if self._spectrum is None else self._spectrum.copy()
            bin_midi = None if self._bin_midi is None else self._bin_midi.copy()
            last_update = self._last_update
        if spectrum is None or bin_midi is None:
            return []
        if time.monotonic() - last_update > 0.5:
            return []

        if mode == "pitch-class":
            note_count = 12
            valid = bin_midi >= 0
            if not np.any(valid):
                return [0.0] * note_count
            idx = np.mod(bin_midi[valid], 12)
            values = np.bincount(idx, weights=spectrum[valid], minlength=note_count)
        else:
            if mode == "piano":
                midi_min, midi_max = 21, 108
            elif mode == "full":
                midi_min = int(math.floor(69 + 12 * math.log2(20.0 / 440.0)))
                midi_max = int(math.ceil(69 + 12 * math.log2(20000.0 / 440.0)))
            else:
                midi_min = int(math.floor(69 + 12 * math.log2(40.0 / 440.0)))
                midi_max = int(math.ceil(69 + 12 * math.log2(16000.0 / 440.0)))
            note_count = max(1, midi_max - midi_min + 1)
            valid = (bin_midi >= midi_min) & (bin_midi <= midi_max)
            if not np.any(valid):
                return [0.0] * note_count
            idx = bin_midi[valid] - midi_min
            values = np.bincount(idx, weights=spectrum[valid], minlength=note_count)

        peak = float(values.max()) if values.size else 0.0
        key = f"note_{mode}"
        with self._lock:
            prev_peak = self._note_peaks.get(key, 1e-6)
        peak = max(prev_peak * 0.97, peak)
        with self._lock:
            self._note_peaks[key] = peak
        if peak <= 1e-9:
            return [0.0] * note_count
        values = np.clip(values / peak, 0.0, 1.0)
        values = np.sqrt(values)
        return values.tolist()

    def _capture_loop(self, mic):
        try:
            with mic.recorder(samplerate=self._samplerate, blocksize=self.blocksize) as rec:
                while not self._stop_event.is_set():
                    data = rec.record(numframes=self.blocksize)
                    if data is None:
                        continue
                    if hasattr(data, "ndim") and data.ndim > 1:
                        samples = np.mean(data, axis=1)
                    else:
                        samples = data
                    self._process_samples(samples)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)

    def _build_bands(self, frames, samplerate):
        freqs = np.fft.rfftfreq(frames, 1.0 / samplerate)
        self._freqs = freqs
        try:
            with np.errstate(divide="ignore", invalid="ignore"):
                midi = 69 + 12 * np.log2(np.maximum(freqs, 1e-9) / 440.0)
            midi = np.where(freqs > 0.0, np.round(midi), -1.0).astype(int)
        except Exception:
            midi = np.full(freqs.shape, -1, dtype=int)
        self._bin_midi = midi
        high = min(samplerate / 2.0, 16000.0)
        low = 40.0
        if high <= low:
            edges = np.linspace(0.0, samplerate / 2.0, self.bars + 1)
        else:
            edges = np.geomspace(low, high, self.bars + 1)

        slices = []
        for i in range(self.bars):
            start = int(np.searchsorted(freqs, edges[i], side="left"))
            end = int(np.searchsorted(freqs, edges[i + 1], side="right"))
            if end <= start:
                end = min(start + 1, freqs.size)
            if start >= freqs.size:
                start = max(0, freqs.size - 1)
                end = freqs.size
            slices.append(slice(start, end))
        self._band_slices = slices

    def _process_samples(self, samples):
        if np is None or samples is None:
            return
        if hasattr(samples, "size"):
            if samples.size == 0:
                return
            length = samples.size
        else:
            try:
                length = len(samples)
            except Exception:
                return
            if length == 0:
                return

        if self._window is None or len(self._window) != length:
            self._window = np.hanning(length)
            self._build_bands(length, self._samplerate)

        samples = np.asarray(samples, dtype=np.float32)
        rms = float(np.sqrt(np.mean(samples * samples)))
        peak = float(np.max(np.abs(samples)))
        crest = peak / (rms + 1e-9)

        windowed = samples * self._window
        spectrum = np.abs(np.fft.rfft(windowed))
        if spectrum.size:
            spectrum[0] = 0.0
        mag = spectrum + 1e-12
        total = float(np.sum(mag))
        centroid = 0.0
        bandwidth = 0.0
        rolloff = 0.0
        flatness = 0.0
        flux = 0.0
        if self._freqs is not None and mag.size and total > 0.0:
            centroid = float(np.sum(self._freqs * mag) / total)
            bandwidth = float(np.sqrt(np.sum(((self._freqs - centroid) ** 2) * mag) / total))
            cumsum = np.cumsum(mag)
            roll_index = int(np.searchsorted(cumsum, total * 0.85))
            roll_index = min(max(roll_index, 0), mag.size - 1)
            rolloff = float(self._freqs[roll_index])
            flatness = float(np.exp(np.mean(np.log(mag))) / (np.mean(mag) + 1e-12))
        if self._prev_spectrum is not None and self._prev_spectrum.shape == spectrum.shape:
            rise = np.maximum(spectrum - self._prev_spectrum, 0.0)
            flux = float(np.sum(rise) / (total + 1e-9))
        self._prev_spectrum = spectrum

        band_values = []
        for band_slice in self._band_slices:
            if band_slice.start >= spectrum.size or band_slice.stop <= band_slice.start:
                band_values.append(0.0)
            else:
                band_values.append(float(np.mean(spectrum[band_slice])))

        peak = max(band_values) if band_values else 0.0
        self._peak = max(self._peak * 0.98, peak)
        if self._peak <= 1e-9:
            levels = [0.0] * self.bars
        else:
            levels = [min(value / self._peak, 1.0) for value in band_values]
            levels = [value ** 0.5 for value in levels]

        with self._lock:
            self._levels = levels
            self._last_update = time.monotonic()
            self._spectrum = spectrum
            self._features["rms"] = rms
            self._features["peak"] = peak
            self._features["crest"] = crest
            self._features["centroid_hz"] = centroid
            self._features["bandwidth_hz"] = bandwidth
            self._features["rolloff_hz"] = rolloff
            self._features["flatness"] = flatness
            self._features["flux"] = flux
