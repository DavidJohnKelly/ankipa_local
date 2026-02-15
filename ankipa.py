from typing import Optional
from aqt.sound import RecordDialog
from aqt import mw
from aqt.qt import Qt
from .stats import update_stat, update_avg_stat, save_stats
from .templates.loader import load_template
import threading
import re


# Regex to clean HTML and tags
REMOVE_HTML_RE = re.compile(r"<[^<]+?>")
REMOVE_TAG_RE = re.compile(r"\[[^\]]+\]")

# Load templates
WORD_HTML: str = load_template("word.html")
RESULT_HTML: str = load_template("result.html")
NETWORK_ERROR_HTML: str = load_template("network_error.html")

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
        to_read = mw.reviewer.card.note()[field]

        # Clean HTML and tags
        to_read = re.sub(REMOVE_HTML_RE, " ", to_read)
        to_read = re.sub(REMOVE_TAG_RE, "", to_read).strip()

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
        from .pronunciation import pron_assess

        t = threading.Thread(
            target=pron_assess,
            args=(cls.REFTEXT, recorded_voice)
        )
        t.start()
        t.join()

        if cls.RESULT is None:
            # Network error template (used for offline fallback too)
            mw.taskman.run_on_main(lambda: mw.reviewer.web.eval(NETWORK_ERROR_HTML))
            return

        scores = cls.RESULT["NBest"][0]
        accuracy = scores.get("AccuracyScore", 0)
        fluency = scores.get("FluencyScore", 0)
        pronunciation = scores.get("PronScore", 0)

        # Update stats
        update_stat("assessments", 1)
        update_avg_stat("avg_accuracy", accuracy, 1)
        update_avg_stat("avg_fluency", fluency, 1)
        update_avg_stat("avg_pronunciation", pronunciation, 1)
        save_stats()

        # Prepare result HTML
        html = RESULT_HTML.replace("[ACCURACY]", str(int(accuracy)))
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
                    syllable_score = syllable.get("AccuracyScore", 0)
                    add = " &#x2022; " if i < (len(word["Syllables"]) - 1) else ""
                    syllables += (
                        f"<span style='color: black;'>{syllable.get('Syllable', '')}</span>"
                        f"<span style='color: white;'>{add}</span>"
                    )

            error = word.get("ErrorType", "None")
            words_html += (
                WORD_HTML.replace("[WORD]", word.get("Word", ""))
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

        # Clear RESULT to prevent reuse
        cls.RESULT = None

        # Show results using ResultsDialog
        from . import ResultsDialog
        widget = ResultsDialog(html, pronunciation)
        widget.setWindowModality(Qt.WindowModality.NonModal)
        widget.show()
