"""
openWakeWord 動作確認スクリプト（Windows対応）
"hey jarvis" を検出したらターミナルに通知する

使い方：
    venv\Scripts\activate
    python test_wakeword.py

終了：Ctrl+C
"""

import pyaudio
import numpy as np
import openwakeword
from openwakeword.model import Model
import time

# ─── 設定 ───────────────────────────────────────────
CHUNK = 1280          # openWakeWordは80ms=1280サンプル(16kHz)を期待
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
THRESHOLD = 0.5       # 検出スコアの閾値（0〜1）。下げると検出しやすくなる
# ────────────────────────────────────────────────────


def main():
    print("=== openWakeWord 動作確認 ===")
    print("モデルをロード中... （初回は自動ダウンロードが入ります）")

    owwModel = Model(
        wakeword_models=["hey_jarvis"],
        inference_framework="onnx"
    )

    print("マイクを初期化中...")
    pa = pyaudio.PyAudio()

    # 利用可能なマイクを表示
    print("\n--- 利用可能な入力デバイス ---")
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0:
            print(f"  [{i}] {info['name']}")
    print("------------------------------\n")

    # デフォルトのマイクで開く（複数マイクがある場合は input_device_index を指定）
    stream = pa.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        frames_per_buffer=CHUNK
        # input_device_index=1,  # マイクが認識されない場合はコメントアウトを外して番号を指定
    )

    print(f"待機中... 「hey jarvis」（ヘイ ジャービス）と話しかけてください")
    print(f"閾値: {THRESHOLD}  ／  終了: Ctrl+C\n")

    try:
        while True:
            audio_data = stream.read(CHUNK, exception_on_overflow=False)
            audio_array = np.frombuffer(audio_data, dtype=np.int16)

            prediction = owwModel.predict(audio_array)

            for model_name, score in prediction.items():
                if score > 0.1:
                    print(f"  スコア: {model_name} = {score:.3f}", end="\r")

                if score >= THRESHOLD:
                    print(f"\n✅ ウェイクワード検出！ [{model_name}] スコア: {score:.3f}")
                    print(f"   検出時刻: {time.strftime('%H:%M:%S')}")
                    print(f"待機中... 「hey jarvis」と話しかけてください\n")
                    time.sleep(2)

    except KeyboardInterrupt:
        print("\n\n終了します")
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


if __name__ == "__main__":
    main()
