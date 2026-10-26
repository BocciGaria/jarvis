"""「Hey Jarvis」の呼びかけを PC 内だけで検知する（openWakeWord 使用。音声は外部に送らない）。"""

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL_NAME = "hey_jarvis"
SAMPLE_RATE = 16000
CHUNK = 1280  # 80ms。openWakeWord が想定する単位
THRESHOLD = float(os.getenv("JARVIS_WAKEWORD_THRESHOLD", "0.5"))  # 上げると誤反応が減り、下げると反応しやすくなる
COOLDOWN_SEC = 2.0
QUIET_LEVEL = 10  # 音量（RMS）がこれ未満のまま続くと「マイクに音が届いていない」とみなす（静かな部屋でも通常は超える）
QUIET_SEC = 30
QUIET_WARNING = "マイクの音がほとんど届いていません。Windows の録音音量や、使っているマイクを確認してください"


def _model_files() -> dict[str, Path]:
    return {
        "wakeword": MODEL_DIR / "hey_jarvis_v0.1.onnx",
        "melspec": MODEL_DIR / "melspectrogram.onnx",
        "embedding": MODEL_DIR / "embedding_model.onnx",
    }


def ensure_models() -> dict[str, Path]:
    """初回だけ openWakeWord 公式リリースから「hey jarvis」モデルをダウンロードする。"""
    files = _model_files()
    if not all(p.exists() for p in files.values()):
        from openwakeword.utils import download_models

        print("「Hey Jarvis」検知モデルを初回ダウンロードしています…")
        download_models([MODEL_NAME], target_directory=str(MODEL_DIR))
    return files


class WakeWordListener:
    """マイクを常時聞き、「Hey Jarvis」を検知したら on_wake を呼ぶ。"""

    def __init__(self, on_wake: Callable[[float], None], on_warning: Callable[[str | None], None] = lambda _: None):
        self.on_wake = on_wake
        self.on_warning = on_warning  # マイクが無音に近いときに警告文、回復したら None で呼ばれる
        self.error: str | None = None
        self.warning: str | None = None
        self.device_name = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def start(self) -> None:
        if self.running:
            return
        if self._thread is not None:
            self._thread.join(timeout=2)  # 停止中のスレッドがマイクを手放すのを待つ
        self._stop.clear()
        self.error = None
        self._thread = threading.Thread(target=self._run, name="wakeword", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """検知を止めてマイクを解放する。"""
        self._stop.set()
        self._set_warning(None)

    def _set_warning(self, warning: str | None) -> None:
        if warning == self.warning:
            return
        self.warning = warning
        if warning:
            print(f"[注意] {warning}（マイク: {self.device_name}）")
        self.on_warning(warning)

    def _run(self) -> None:
        try:
            import numpy as np
            import sounddevice as sd
            from openwakeword.model import Model

            files = ensure_models()
            model = Model(
                wakeword_models=[str(files["wakeword"])],
                inference_framework="onnx",
                melspec_model_path=str(files["melspec"]),
                embedding_model_path=str(files["embedding"]),
            )
            self.device_name = sd.query_devices(kind="input")["name"]
            print(f"「Hey Jarvis」検知に使うマイク: {self.device_name}")
            last_wake = 0.0
            last_loud = time.monotonic()
            loud_run = 0
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK) as stream:
                while not self._stop.is_set():
                    audio, _ = stream.read(CHUNK)
                    samples = audio[:, 0]
                    now = time.monotonic()

                    # 一瞬のノイズ（クリック音など）は除外し、0.24 秒以上続く音だけを「音がある」とみなす
                    level = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                    loud_run = loud_run + 1 if level >= QUIET_LEVEL else 0
                    if loud_run >= 3:
                        last_loud = now
                        self._set_warning(None)
                    elif now - last_loud > QUIET_SEC:
                        self._set_warning(QUIET_WARNING)

                    score = max(model.predict(samples).values())
                    if score >= THRESHOLD and now - last_wake > COOLDOWN_SEC:
                        last_wake = now
                        model.reset()
                        self.on_wake(score)
        except Exception as e:  # マイクがない・モデル取得失敗など。GUI 本体は動かし続ける
            self.error = f"{type(e).__name__}: {e}"
            print(f"[Hey Jarvis 検知を開始できませんでした] {self.error}")
