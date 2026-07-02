"""
AmiVoice API 疎通確認スクリプト（公式サンプル準拠版）

使い方：
    1. AMIVOICE_APP_KEY に自分のAppKeyを貼り付ける
    2. python test_amivoice.py
    3. Enterキーを押して話しかける（最大10秒）
    4. 自動で録音終了→認識結果が表示される

終了：Ctrl+C
"""

import pyaudio
import struct
import threading
import time
import json
import websocket

# ─── 設定 ───────────────────────────────────────────
AMIVOICE_APP_KEY = "YOUR APP KEY"  # ← ここに自分のAppKeyを貼り付ける

AMIVOICE_WS_URL = "wss://acp-api.amivoice.com/v1/"
AMIVOICE_ENGINE = "-a-general"
AUDIO_FORMAT = "16K"              # 16kHz PCM

# 録音設定
SAMPLE_RATE = 16000
CHUNK = 1024
SILENCE_THRESHOLD = 200
SILENCE_DURATION = 1.5
MAX_RECORD_SECONDS = 10
AUDIO_BLOCK_SIZE = 16000          # 送信ブロックサイズ（0.5秒分）
# ────────────────────────────────────────────────────


def record_audio():
    pa = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK
    )
    print("🎤 録音開始... 話しかけてください")
    frames = []
    silent_chunks = 0
    silent_limit = int(SAMPLE_RATE / CHUNK * SILENCE_DURATION)
    max_chunks = int(SAMPLE_RATE / CHUNK * MAX_RECORD_SECONDS)
    has_voice = False

    for _ in range(max_chunks):
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)
        amplitude = max(abs(x) for x in struct.unpack(f"{CHUNK}h", data))
        if amplitude >= SILENCE_THRESHOLD:
            has_voice = True
            silent_chunks = 0
        else:
            if has_voice:
                silent_chunks += 1
        if has_voice and silent_chunks >= silent_limit:
            print("🔇 無音検出 → 録音終了")
            break

    stream.stop_stream()
    stream.close()
    pa.terminate()

    audio_bytes = b"".join(frames)
    duration = len(audio_bytes) / (SAMPLE_RATE * 2)
    print(f"   録音時間: {duration:.1f}秒 / 音声検出: {'あり' if has_voice else 'なし'}")
    return audio_bytes, has_voice


def recognize(audio_bytes):
    result_text = ""
    done_event = threading.Event()

    def on_open(ws):
        print("🔗 AmiVoice APIに接続しました")
        command = f"s {AUDIO_FORMAT} {AMIVOICE_ENGINE} authorization={AMIVOICE_APP_KEY}"
        print(f"   送信: s {AUDIO_FORMAT} {AMIVOICE_ENGINE} authorization=***")
        ws.send(command)

    def on_message(ws, message):
        nonlocal result_text
        event = message[0]
        content = message[2:].rstrip()
        print(f"   [受信] event={event} content={content[:80]}")

        if event == 's':
            if content != "":
                print(f"❌ sコマンドエラー: {content}")
                done_event.set()
                ws.close()
                return

            # sコマンド成功 → 別スレッドで音声データを送信
            def send_audio():
                offset = 0
                total = len(audio_bytes)
                while offset < total:
                    chunk = audio_bytes[offset:offset + AUDIO_BLOCK_SIZE]
                    ws.send(b'p' + chunk, opcode=websocket.ABNF.OPCODE_BINARY)
                    offset += AUDIO_BLOCK_SIZE
                    time.sleep(0.5)
                print("   音声送信完了 → eコマンド送信")
                ws.send('e')

            threading.Thread(target=send_audio).start()

        elif event == 'G':
            pass
        elif event == 'S':
            print(f"   発話開始: {content}ms")
        elif event == 'E':
            print(f"   発話終了: {content}ms")
        elif event == 'C':
            pass
        elif event == 'U':
            raw = json.loads(content) if content else {}
            text = raw.get("text", "")
            if text:
                print(f"   途中結果: {text}")
        elif event in ('A', 'R'):
            raw = json.loads(content) if content else {}
            result_text = raw.get("text", "")
            print(f"\n✅ 認識結果: {result_text}")
        elif event == 'e':
            print("   セッション終了")
            done_event.set()
            ws.close()
        elif event == 'p':
            if content:
                print(f"❌ pコマンドエラー: {content}")
                done_event.set()
                ws.close()

    def on_error(ws, error):
        print(f"\n❌ WebSocketエラー: {error}")
        done_event.set()

    def on_close(ws, close_status_code, close_msg):
        done_event.set()

    ws = websocket.WebSocketApp(
        AMIVOICE_WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    ws_thread = threading.Thread(target=ws.run_forever, kwargs={"ping_interval": 0})
    ws_thread.daemon = True
    ws_thread.start()

    done_event.wait(timeout=60)
    return result_text.strip()


def main():
    if AMIVOICE_APP_KEY == "YOUR_APP_KEY":
        print("❌ AMIVOICE_APP_KEY を設定してください")
        return

    print("=== AmiVoice API 疎通確認 ===\n")

    try:
        while True:
            input("Enterキーを押すと録音開始します（終了: Ctrl+C）\n")
            audio_bytes, has_voice = record_audio()

            if not has_voice:
                print("⚠️  音声が検出されませんでした。マイクを確認してください\n")
                continue

            result = recognize(audio_bytes)

            if result:
                print(f"\n📝 最終テキスト: 「{result}」\n")
            else:
                print("\n⚠️  認識結果が空でした\n")

            print("-" * 40 + "\n")

    except KeyboardInterrupt:
        print("\n終了します")


if __name__ == "__main__":
    main()
