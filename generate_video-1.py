#!/usr/bin/env python3
"""
مولّد فيديوهات قصيرة (Shorts / Reels / TikTok) - نسخة مجانية مفتوحة المصدر
==========================================================================

الفكرة:
  تعطيه ملف config.json فيه قائمة "مشاهد" (scene)، كل مشهد عنده:
    - text: النص اللي يُقرأ بصوت TTS ويظهر كترجمة
    - image_path (اختياري): مسار صورة محلية تستخدم لهذا المشهد
    - image_query (اختياري): كلمة بحث لجلب صورة مجانية من Pexels (لو ما فيه image_path)

  السكربت يسوي:
    1) يولّد صوت لكل مشهد عبر Piper TTS (محلي ومجاني بالكامل)
    2) يجيب صورة لكل مشهد (محلية أو من Pexels المجاني)
    3) يحرّك الصورة بتأثير Ken Burns (زوم/بان) بطول مدة الصوت
    4) يركب كل المشاهد فيديو واحد + الصوت
    5) يحرق ترجمة (subtitles) تلقائية فوق الفيديو
    6) يطلع فيديو نهائي جاهز بصيغة MP4 عمودي 1080x1920

المتطلبات (تثبتها على جهازك/سيرفرك، هذا السكربت لا يثبتها تلقائيًا):
  - ffmpeg و ffprobe (مجاني ومفتوح المصدر)
  - piper (محرك TTS محلي): https://github.com/rhasspy/piper
    + ملف صوت عربي أو إنجليزي .onnx (يتحمّل من نفس المشروع)
  - مكتبات بايثون: pip install moviepy pillow requests --break-system-packages

  اختياري (لجلب صور مجانية تلقائيًا):
  - مفتاح مجاني من https://www.pexels.com/api/  → يوضع في متغير البيئة PEXELS_API_KEY

الاستخدام:
  python generate_video.py --config config.example.json --output out.mp4
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import shutil

try:
    import requests
except ImportError:
    requests = None

def load_env_file(path=".env"):
    """
    يقرأ ملف .env بسيط (KEY=VALUE كل سطر) ويحطه في متغيرات البيئة
    لو المتغير مو موجود مسبقًا. ما يحتاج أي مكتبة خارجية.
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_env_file()


try:
    from moviepy.editor import (
        ImageClip,
        AudioFileClip,
        concatenate_videoclips,
        CompositeAudioClip,
    )
except ImportError:
    print("خطأ: ثبّت moviepy أولاً → pip install moviepy --break-system-packages")
    sys.exit(1)


# ---------- إعدادات عامة ----------
VIDEO_W, VIDEO_H = 1080, 1920   # مقاس عمودي يناسب تيك توك / ريلز / شورتس
FPS = 30


def run(cmd):
    """ينفّذ أمر نظام ويطبع خطأ واضح لو فشل"""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"فشل الأمر: {' '.join(cmd)}\n{result.stderr}")
        raise RuntimeError(result.stderr)
    return result


def generate_tts(text, voice_model, out_wav):
    """
    يولّد صوت من نص باستخدام Piper (محلي، مجاني).
    لازم يكون برنامج piper موجود في PATH، وملف الصوت (.onnx) محمّل عندك.
    """
    cmd = ["piper", "--model", voice_model, "--output_file", out_wav]
    proc = subprocess.run(cmd, input=text, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"فشل Piper TTS: {proc.stderr}")
    return out_wav


def get_audio_duration(wav_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        wav_path,
    ]
    out = run(cmd).stdout.strip()
    return float(out)


def fetch_pixabay_image(query, out_path):
    """
    يجيب صورة مجانية واحدة من Pixabay (مفتاح API يصدر فورًا بدون انتظار،
    بعكس Pexels اللي أوقفوا إصدار مفاتيح جديدة مؤقتًا).
    """
    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ما فيه PIXABAY_API_KEY في متغيرات البيئة. "
            "سجّل مجانًا في pixabay.com وخذ مفتاحك من pixabay.com/api/docs"
        )
    if requests is None:
        raise RuntimeError("ثبّت requests → pip install requests --break-system-packages")

    params = {
        "key": api_key,
        "q": query,
        "image_type": "photo",
        "orientation": "vertical",
        "per_page": 3,
        "safesearch": "true",
    }
    resp = requests.get("https://pixabay.com/api/", params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    hits = data.get("hits", [])
    if not hits:
        raise RuntimeError(f"ما لقيت صور لكلمة البحث: {query}")

    img_url = hits[0]["largeImageURL"]
    img_resp = requests.get(img_url, timeout=30)
    with open(out_path, "wb") as f:
        f.write(img_resp.content)
    return out_path


def fetch_pexels_image(query, out_path):
    """
    يجيب صورة مجانية واحدة من Pexels بناءً على كلمة بحث.
    ملاحظة: Pexels أوقفوا إصدار مفاتيح API جديدة مؤقتًا (منذ 30 سبتمبر 2026).
    لو عندك مفتاح قديم شغال، هذي الدالة تشتغل عادي؛ غير هذا استخدم Pixabay.
    """
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ما فيه PEXELS_API_KEY في متغيرات البيئة "
            "(ولاحظ إن Pexels متوقفين عن إصدار مفاتيح جديدة حاليًا)."
        )
    if requests is None:
        raise RuntimeError("ثبّت requests → pip install requests --break-system-packages")

    headers = {"Authorization": api_key}
    params = {"query": query, "orientation": "portrait", "per_page": 1}
    resp = requests.get(
        "https://api.pexels.com/v1/search", headers=headers, params=params, timeout=20
    )
    resp.raise_for_status()
    data = resp.json()
    photos = data.get("photos", [])
    if not photos:
        raise RuntimeError(f"ما لقيت صور لكلمة البحث: {query}")

    img_url = photos[0]["src"]["portrait"]
    img_resp = requests.get(img_url, timeout=30)
    with open(out_path, "wb") as f:
        f.write(img_resp.content)
    return out_path


def resolve_scene_image(scene, idx, work_dir):
    """يحدد صورة المشهد: محلية، أو من Pexels، أو لون خلفية بديل عند الفشل"""
    if scene.get("image_path"):
        return scene["image_path"]

    out_path = os.path.join(work_dir, f"scene_{idx}.jpg")
    if scene.get("image_query"):
        # نجرب Pixabay أولاً (مفتاحه يصدر فورًا)، ثم Pexels كخيار احتياطي
        if os.environ.get("PIXABAY_API_KEY"):
            try:
                return fetch_pixabay_image(scene["image_query"], out_path)
            except Exception as e:
                print(f"تحذير: فشل جلب صورة من Pixabay للمشهد {idx} ({e})")
        if os.environ.get("PEXELS_API_KEY"):
            try:
                return fetch_pexels_image(scene["image_query"], out_path)
            except Exception as e:
                print(f"تحذير: فشل جلب صورة من Pexels للمشهد {idx} ({e})")
        print(f"تحذير: ما قدرت أجلب صورة للمشهد {idx} — رح أستخدم خلفية بديلة.")

    # خلفية بديلة بسيطة لو ما فيه صورة
    from PIL import Image

    img = Image.new("RGB", (VIDEO_W, VIDEO_H), color=(20, 20, 30))
    img.save(out_path)
    return out_path


def ken_burns_clip(image_path, duration, zoom_in=True):
    """
    يحرّك صورة ثابتة بتأثير Ken Burns (زوم بطيء + حركة بسيطة)
    بدل فيديو AI مكلف — نفس الإحساس بصريًا بدون تكلفة توليد فيديو.
    """
    clip = ImageClip(image_path)

    # يكبّر الصورة أول شي عشان يصير فيها هامش حركة
    clip = clip.resize(height=int(VIDEO_H * 1.3))
    if clip.w < VIDEO_W:
        clip = clip.resize(width=int(VIDEO_W * 1.3))

    start_zoom, end_zoom = (1.0, 1.15) if zoom_in else (1.15, 1.0)

    def zoom_func(t):
        progress = t / duration if duration > 0 else 0
        return start_zoom + (end_zoom - start_zoom) * progress

    clip = clip.resize(lambda t: zoom_func(t))
    clip = clip.set_position("center").set_duration(duration)

    # نقصّه لمقاس الفيديو النهائي (crop للمنتصف)
    clip = clip.crop(
        x_center=clip.w / 2 if callable(clip.w) is False else None,
    ) if False else clip  # (احتياط: moviepy يعالج المقاس عبر set بعدين)

    return clip, duration


def build_srt(scenes, durations, out_srt):
    """يبني ملف ترجمة SRT بسيط بتوقيت يطابق مدة كل مشهد"""

    def fmt(t):
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t - int(t)) * 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    lines = []
    cursor = 0.0
    for i, (scene, dur) in enumerate(zip(scenes, durations), start=1):
        start = cursor
        end = cursor + dur
        lines.append(str(i))
        lines.append(f"{fmt(start)} --> {fmt(end)}")
        lines.append(scene["text"])
        lines.append("")
        cursor = end

    with open(out_srt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="مولّد فيديوهات قصيرة مجاني")
    parser.add_argument("--config", required=True, help="ملف config.json فيه المشاهد")
    parser.add_argument("--output", default="out_final.mp4", help="اسم الفيديو الناتج")
    parser.add_argument(
        "--voice-model", default=os.environ.get("PIPER_VOICE", "voice.onnx"),
        help="مسار ملف صوت Piper (.onnx)"
    )
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)

    scenes = config["scenes"]
    work_dir = tempfile.mkdtemp(prefix="video_agent_")
    print(f"مجلد العمل المؤقت: {work_dir}")

    video_clips = []
    audio_clips = []
    durations = []

    for idx, scene in enumerate(scenes):
        print(f"\n[مشهد {idx + 1}/{len(scenes)}] {scene['text'][:40]}...")

        wav_path = os.path.join(work_dir, f"scene_{idx}.wav")
        generate_tts(scene["text"], args.voice_model, wav_path)
        duration = get_audio_duration(wav_path)
        durations.append(duration)

        image_path = resolve_scene_image(scene, idx, work_dir)
        clip, _ = ken_burns_clip(image_path, duration, zoom_in=(idx % 2 == 0))

        # تقصيص للمقاس النهائي العمودي
        clip = clip.resize(height=VIDEO_H)
        clip = clip.crop(
            x1=max(0, (clip.w - VIDEO_W) / 2),
            width=VIDEO_W,
            height=VIDEO_H,
        )

        video_clips.append(clip)
        audio_clips.append(AudioFileClip(wav_path))

    print("\nتركيب الفيديو والصوت...")
    final_video = concatenate_videoclips(video_clips, method="compose")
    final_audio = CompositeAudioClip(
        [a.set_start(sum(durations[:i])) for i, a in enumerate(audio_clips)]
    )
    final_video = final_video.set_audio(final_audio).set_fps(FPS)

    temp_video_path = os.path.join(work_dir, "temp_no_subs.mp4")
    final_video.write_videofile(
        temp_video_path, codec="libx264", audio_codec="aac", fps=FPS, verbose=False, logger=None
    )

    # الترجمة
    srt_path = os.path.join(work_dir, "subs.srt")
    build_srt(scenes, durations, srt_path)

    print("حرق الترجمة على الفيديو النهائي...")
    style = (
        "FontName=Arial,FontSize=14,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=3,Outline=2,"
        "Alignment=2,MarginV=80"
    )
    run([
        "ffmpeg", "-y", "-i", temp_video_path,
        "-vf", f"subtitles={srt_path}:force_style='{style}'",
        "-c:a", "copy", args.output,
    ])

    shutil.rmtree(work_dir, ignore_errors=True)
    print(f"\n✅ تم! الفيديو النهائي: {args.output}")


if __name__ == "__main__":
    main()
