import re
import time
import wave
import threading
from typing import Optional

from aqt import mw
from aqt.sound import RecordDialog
from aqt.qt import Qt

from .stats import get_stat, log_assessment, update_stat, update_avg_stat, save_stats
from .templates.loader import load_template


# Regex to clean HTML and tags
_REMOVE_HTML_RE = re.compile(r"<[^<]+?>")
_REMOVE_TAG_RE = re.compile(r"\[[^\]]+\]")

# Load templates
_WORD_HTML: str = load_template("word.html")
_RESULT_HTML: str = load_template("result.html")
_RECOGNITION_ERROR_HTML: str = load_template("recognition_error.html")

class AnkiPA:
    REFTEXT: Optional[str] = None
    RECORDED: Optional[str] = None
    TTS_GEN: Optional[str] = None
    RESULT: Optional[dict] = None
    DIAG: Optional[RecordDialog] = None

    @classmethod
    def test_pronunciation(cls):
        """Extract text from current card and record user voice."""
        if mw.reviewer.card is None:
            return

        # Use first field by default
        field = mw.col.models.field_names(mw.reviewer.card.note().note_type())[0]
        cls.FIELD = field
        to_read = mw.reviewer.card.note()[field]

        # Clean HTML and tags
        to_read = re.sub(_REMOVE_HTML_RE, " ", to_read)
        to_read = re.sub(_REMOVE_TAG_RE, "", to_read).strip()

        cls.REFTEXT = to_read
        cls.DIAG = RecordDialog(mw, mw, cls.after_record)

    @classmethod
    def after_record(cls, recorded_voice: Optional[str]) -> None:
        """Handle recorded voice, run pronunciation assessment, update stats, and display results."""
        if not recorded_voice or not cls.REFTEXT:
            print("Error: No recorded voice or reference text available.")
            return

        cls.RECORDED = recorded_voice
        cls.RESULT = None

        # Run pronunciation assessment in thread
        try:
            from .pronunciation import pron_assess
        except Exception as e:
            print(f"Pronunciation import failed: {e}")
            cls.RESULT = {"error": "Local speech engine unavailable. Please restart Anki after installing dependencies."}
            mw.taskman.run_on_main(lambda: mw.reviewer.web.setHtml(_RECOGNITION_ERROR_HTML))
            return

        t = threading.Thread(
            target=pron_assess,
            args=(cls.REFTEXT, recorded_voice)
        )
        t.start()
        t.join()

        if cls.RESULT is None:
            msg = "Speech recognition failed. Local engine may be initializing; retry once."
            mw.taskman.run_on_main(lambda: mw.reviewer.web.setHtml(f"<b>{msg}</b>"))
            return

        if isinstance(cls.RESULT, dict) and cls.RESULT.get("error"):
            # Local recognition fallback screen
            mw.taskman.run_on_main(lambda: mw.reviewer.web.setHtml(_RECOGNITION_ERROR_HTML))

        if "NBest" not in cls.RESULT or not cls.RESULT["NBest"]:
            print("No pronunciation result:", cls.RESULT)
            return

        scores = cls.RESULT["NBest"][0]
        accuracy = scores.get("AccuracyScore", 0)
        fluency = scores.get("FluencyScore", 0)
        pronunciation = scores.get("PronScore", 0)

        # Update stats
        update_stat("assessments", 1)
        current_assessments = get_stat("assessments")
        
        update_avg_stat("avg_accuracy", accuracy, current_assessments)
        update_avg_stat("avg_fluency", fluency, current_assessments)
        update_avg_stat("avg_pronunciation", pronunciation, current_assessments)

        # Prepare result HTML
        html = _RESULT_HTML.replace("[ACCURACY]", str(int(accuracy)))
        html = html.replace("[FLUENCY]", str(int(fluency)))
        html = html.replace("[PRONUNCIATION]", str(int(pronunciation)))

        # Prepare word details
        words_list = scores.get("Words", [])
        if not isinstance(words_list, list):
            words_list = []

        errors = {"Mispronunciation": 0, "Omission": 0, "Insertion": 0}
        words_html = ""

        for word in words_list:
            syllables = ""
            if "Syllables" in word and isinstance(word["Syllables"], list):
                for i, syllable in enumerate(word["Syllables"]):
                    add = " &#x2022; " if i < (len(word["Syllables"]) - 1) else ""
                    syllables += (
                        f"<span style='color: black;'>{syllable.get('Syllable', '')}</span>"
                        f"<span style='color: white;'>{add}</span>"
                    )

            error = word.get("ErrorType", "None")
            words_html += (
                _WORD_HTML.replace("[WORD]", word.get("Word", ""))
                .replace("[SYLLABLES]", syllables)
                .replace("[ERROR]", error)
                .replace("[ERROR-INFO]", error if error != "None" else "Correct")
            )
            if error != "None" and error in errors:
                errors[error] += 1

        # Replace word list and error counts
        html = html.replace("[WORDLIST]", words_html)
        html = html.replace("[MISPRONUNCIATIONS]", str(errors["Mispronunciation"]))
        html = html.replace("[OMISSIONS]", str(errors["Omission"]))
        html = html.replace("[INSERTIONS]", str(errors["Insertion"]))

        # Log the assessment for later analysis
        try:
            with wave.open(recorded_voice, "rb") as wf:
                audio_length = wf.getnframes() / float(wf.getframerate())
        except Exception:
            audio_length = 0.0

        # Count only correctly recognized words (no errors)
        correct_words = sum(1 for word in words_list if word.get("ErrorType") == "None")
        
        # Update cumulative stats
        update_stat("pronunciation_time", audio_length)
        update_stat("words", correct_words)

        recognized_text = cls.RESULT.get("Transcript") or ""

        # Some card metadata for traceability
        note = mw.reviewer.card.note() if mw.reviewer.card else None
        card = mw.reviewer.card
        note_id = getattr(note, "id", -1)
        card_id = getattr(card, "id", -1)
        field_name = getattr(cls, "FIELD", "")
        deck_name = ""
        try:
            deck_name = mw.col.decks.name(card.did) if card else ""
        except Exception:
            deck_name = ""
        reps = getattr(card, "reps", 0)
        interval = getattr(card, "ivl", 0)

        # record an entry in stats.json so it is easy to
        # inspect progress over time.
        try:
            log_assessment({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "note_id": note_id,
                "card_id": card_id,
                "deck_name": deck_name,
                "field_name": field_name,
                "target_text": cls.REFTEXT,
                "recognized_text": recognized_text,
                "accuracy": accuracy,
                "fluency": fluency,
                "pronunciation_score": pronunciation,
                "audio_length": audio_length,
                "words_count": correct_words,
                "mispronunciations": errors["Mispronunciation"],
                "omissions": errors["Omission"],
                "insertions": errors["Insertion"],
                "reps": reps,
                "interval": interval,
            })

            save_stats()
        except Exception as e:
            print(f"Error logging assessment: {e}")

        # Clear RESULT to prevent reuse
        cls.RESULT = None

        # Show results using ResultsDialog
        from . import ResultsDialog
        widget = ResultsDialog(html, pronunciation)
        widget.setWindowModality(Qt.WindowModality.NonModal)
        widget.show()
