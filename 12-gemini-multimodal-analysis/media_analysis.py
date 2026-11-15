# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Video and audio understanding with Gemini.

No Speech-to-Text API, no Video Intelligence API needed — Gemini handles both
modalities natively via Part.from_uri with the appropriate MIME type.

Video: sampled as frames + audio track. Longer clips = more input tokens.
Audio: transcription, summarization, diarization ("who said what"), and Q&A.

Use cases: media indexing, captioning, meeting transcription, content moderation.
"""

import os

from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = "gemini-2.5-flash"

# Public sample assets from Google Cloud.
VIDEO_URI = "gs://cloud-samples-data/generative-ai/video/pixel8.mp4"
AUDIO_URI = "gs://cloud-samples-data/generative-ai/audio/pixel.mp3"


def create_client():
    """Create and return a Vertex AI Gemini client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def analyze_video(client, video_uri=VIDEO_URI, max_output_tokens=512):
    """
    Describes the content of a video clip from a GCS URI.

    Video is sampled as frames + audio track. Cost scales with clip length.
    Tip: use timestamps in the prompt to focus on specific segments and
    reduce token consumption for long videos.

    Args:
        client: genai.Client instance.
        video_uri: GCS URI of the video (gs://bucket/object).
        max_output_tokens: Cap on response length.

    Returns:
        dict with keys: text, prompt_token_count
    """
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_uri(file_uri=video_uri, mime_type="video/mp4"),
            "Describe what happens in this video in 2-3 sentences. "
            "Mention the setting and the main subject.",
        ],
        config=types.GenerateContentConfig(max_output_tokens=max_output_tokens),
    )
    return {
        "text": response.text,
        "prompt_token_count": response.usage_metadata.prompt_token_count,
    }


def analyze_audio(client, audio_uri=AUDIO_URI, max_output_tokens=1024):
    """
    Transcribes and summarizes an audio clip from a GCS URI.

    Gemini can transcribe, summarize, diarize, or answer factual questions
    about audio content — all in a single call, no pre-processing pipeline.

    Args:
        client: genai.Client instance.
        audio_uri: GCS URI of the audio file (gs://bucket/object).
        max_output_tokens: Cap on response length (larger for transcription).

    Returns:
        dict with keys: text, prompt_token_count
    """
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_uri(file_uri=audio_uri, mime_type="audio/mpeg"),
            "Transcribe the first 30 seconds of this audio clip. "
            "Then give a one-line summary of the topic.",
        ],
        config=types.GenerateContentConfig(max_output_tokens=max_output_tokens),
    )
    return {
        "text": response.text,
        "prompt_token_count": response.usage_metadata.prompt_token_count,
    }


def run_media_analysis():
    """Entry point for the video and audio analysis demo."""
    client = create_client()

    print("=== Video Understanding ===")
    video_result = analyze_video(client)
    print(video_result["text"])
    print(f"(video input tokens: {video_result['prompt_token_count']})")

    print("\n=== Audio Transcription ===")
    audio_result = analyze_audio(client)
    print(audio_result["text"])
    print(f"(audio input tokens: {audio_result['prompt_token_count']})")

    print(
        "\nNote: both modalities use the same Part.from_uri pattern — only the "
        "mime_type changes. No separate Speech-to-Text or Video Intelligence APIs needed."
    )
