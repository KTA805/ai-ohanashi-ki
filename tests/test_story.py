"""
GPT-4o お話生成 + OpenAI TTS 読み上げ 確認スクリプト

使い方：
    1. OPENAI_API_KEY に自分のAPIキーを貼り付ける
    2. python test_story.py
    3. お話のリクエストを入力してEnter

終了：Ctrl+C
"""

import io
import pygame
from openai import OpenAI

# ─── 設定 ───────────────────────────────────────────
OPENAI_API_KEY = "YOUR API KEY"  # ← ここに貼り付ける

GPT_MODEL = "gpt-4o"
TTS_MODEL = "tts-1"
TTS_VOICE = "nova"   # 明るい女性の声
TTS_SPEED = 0.9      # 少しゆっくり
# ────────────────────────────────────────────────────

SYSTEM_PROMPT = """
あなたは子ども向けのへんてこなお話を作るAIです。

ルール：
- 5歳前後の子どもに向けた、やさしい言葉を使う
- 1回のお話は200〜250文字程度（読み上げ約30秒）
- 必ず1つ「へんてこな出来事」を入れる（例：カレーが空から降る、ぞうがロケットに乗る）
- 文末は「〜でした」「〜しました」など、お話らしい口調にする
- 最後は「おしまい」で締めくくる
"""

def generate_story(client, request: str) -> str:
    """GPT-4oでお話を生成する"""
    print(f"🤖 GPT-4oにお話を生成中...")
    response = client.chat.completions.create(
        model=GPT_MODEL,
        max_tokens=400,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"「{request}」のお話を作って"}
        ]
    )
    return response.choices[0].message.content


def speak(client, text: str):
    """OpenAI TTSで読み上げる"""
    print(f"🔊 読み上げ中...")
    response = client.audio.speech.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=text,
        speed=TTS_SPEED
    )
    audio_data = io.BytesIO(response.content)
    pygame.mixer.music.load(audio_data)
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        pygame.time.wait(100)


def main():
    if OPENAI_API_KEY == "YOUR_OPENAI_API_KEY":
        print("❌ OPENAI_API_KEY を設定してください")
        return

    pygame.mixer.init()
    client = OpenAI(api_key=OPENAI_API_KEY)

    print("=== GPT-4o お話生成 + TTS 確認 ===\n")

    try:
        while True:
            request = input("お話のリクエストを入力してください（例：ぞうさんとカレー）\n> ").strip()
            if not request:
                continue

            # お話生成
            story = generate_story(client, request)
            print(f"\n📖 生成されたお話:\n{story}\n")

            # 読み上げ
            speak(client, story)
            print("✅ 読み上げ完了\n")
            print("-" * 40 + "\n")

    except KeyboardInterrupt:
        print("\n終了します")
    finally:
        pygame.mixer.quit()


if __name__ == "__main__":
    main()
