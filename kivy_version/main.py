from __future__ import annotations

from pathlib import Path

from kivy.app import App
from kivy.lang import Builder
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout

from logic.label_extraction import LabelExtractor
from logic.rag import RAGEngine


KV = """
<MainLayout>:
    orientation: "vertical"
    spacing: "8dp"
    padding: "12dp"

    Label:
        text: "SuppSwap Offline (Kivy)"
        bold: True
        font_size: "20sp"
        size_hint_y: None
        height: self.texture_size[1] + dp(8)

    Label:
        text: root.status_text
        color: 0.0, 0.45, 0.2, 1 if "OK" in root.status_text else 0.55, 0.1, 0.1, 1
        size_hint_y: None
        height: self.texture_size[1] + dp(6)

    TextInput:
        id: image_path_input
        hint_text: "Image path on device (optional)"
        multiline: False
        size_hint_y: None
        height: "40dp"

    BoxLayout:
        size_hint_y: None
        height: "42dp"
        spacing: "8dp"

        Button:
            text: "Extract Label"
            on_release: root.extract_from_image(image_path_input.text)

        Button:
            text: "Analyze"
            on_release: root.run_analysis()

    TextInput:
        id: input_text
        hint_text: "Paste supplement label text or ingredient list"
        text: root.source_text
        multiline: True

    TextInput:
        id: question_input
        hint_text: "Ask nutrition question from local RAG context"
        multiline: False
        size_hint_y: None
        height: "40dp"

    Button:
        text: "Ask Local RAG"
        size_hint_y: None
        height: "42dp"
        on_release: root.ask_rag(question_input.text)

    Label:
        text: "Output"
        bold: True
        size_hint_y: None
        height: self.texture_size[1] + dp(6)

    ScrollView:
        Label:
            text: root.output_text
            text_size: self.width, None
            size_hint_y: None
            height: max(self.texture_size[1] + dp(8), root.height * 0.35)
            halign: "left"
            valign: "top"
"""


class MainLayout(BoxLayout):
    status_text = StringProperty("Offline services: initializing...")
    source_text = StringProperty("")
    output_text = StringProperty("Ready.")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_extracted_text = ""

    def _app(self) -> "SupplementApp":
        return App.get_running_app()  # type: ignore[return-value]

    def extract_from_image(self, image_path: str) -> None:
        p = str(image_path or "").strip()
        if not p:
            self.status_text = "Offline services: add an image path first"
            return
        text = self._app().extract_label_text(p)
        if not text:
            self.status_text = "Offline services: OCR failed"
            return
        self._last_extracted_text = text
        self.source_text = text
        self.output_text = text
        self.status_text = "Offline services: OCR OK"

    def run_analysis(self) -> None:
        source_text = str(self.ids.input_text.text or "").strip()
        if not source_text:
            source_text = self._last_extracted_text
        if not source_text:
            self.status_text = "Offline services: add text or extract image first"
            return
        report = self._app().analyze_label(source_text)
        self.output_text = report
        self.status_text = "Offline services: analysis OK"

    def ask_rag(self, question: str) -> None:
        q = str(question or "").strip()
        if not q:
            self.status_text = "Offline services: ask a question first"
            return
        answer = self._app().answer_rag(q)
        self.output_text = answer
        self.status_text = "Offline services: RAG OK"


class SupplementApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        app_root = Path(__file__).resolve().parent
        self.extractor = LabelExtractor(app_root=app_root)
        self.rag = RAGEngine(app_root=app_root)

    def build(self):
        Builder.load_string(KV)
        return MainLayout()

    def extract_label_text(self, image_path: str) -> str:
        try:
            return self.extractor.extract_text(image_path)
        except Exception as exc:
            return f"OCR error: {exc}"

    def analyze_label(self, source_text: str) -> str:
        return self.rag.build_label_analysis_report(source_text)

    def answer_rag(self, question: str) -> str:
        return self.rag.answer_question(question)


if __name__ == "__main__":
    SupplementApp().run()
