# tts.py
import os
import tempfile
import pyttsx3

from typing import Optional


class TTS:
    @classmethod
    def gen_tts_audio(cls, text: Optional[str]) -> str:
        """
        Generate TTS audio offline using pyttsx3.

        Args:
            text: The text to convert to speech.

        Returns:
            Path to the generated WAV file.
        """
        if not text:
            raise ValueError("No text provided for TTS generation.")

        # Prepare temporary directory
        tmpdir = os.path.join(tempfile.gettempdir(), "ankipa")
        os.makedirs(tmpdir, exist_ok=True)
        tmp_path = os.path.join(tmpdir, "tts_output.wav")

        # Initialize TTS engine
        engine = pyttsx3.init()

        # Get voices and select English voice if available
        voices = list(engine.getProperty("voices") or [])
        voice_code = next((v.id for v in voices if "en" in v.id.lower()), voices[0].id)

        if voice_code:
            matched = False
            for voice in voices:
                if voice_code.lower() in voice.id.lower():
                    engine.setProperty("voice", voice.id)
                    matched = True
                    break
            if not matched:
                print(f"Voice '{voice_code}' not found. Using default voice.")

        # Save audio to file
        engine.save_to_file(text, tmp_path)
        engine.runAndWait()
        engine.stop()

        return tmp_path
