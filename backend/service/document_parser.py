"""Convert source documents to Markdown before chunking."""

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter


class DocumentParser(ABC):
    suffix: str

    @abstractmethod
    def to_markdown(self, source: Path) -> str:
        """Return the document's text as Markdown."""


class DoclingPdfParser(DocumentParser):
    suffix = ".pdf"

    def __init__(self) -> None:
        self._converter: DocumentConverter | None = None

    def to_markdown(self, source: Path) -> str:
        """Extract a born-digital PDF with Docling's layout model; OCR is off."""
        document = self._get_converter().convert(source).document
        markdown = document.export_to_markdown(
            escape_html=False,
            escape_underscores=False,
        )
        # Docling exports the title as a level-two heading. Restore it as the
        # level-one title so it supplies metadata instead of becoming a chunk.
        if not re.search(r"(?m)^#\s", markdown):
            markdown = re.sub(r"(?m)^##(?=\s)", "#", markdown, count=1)
        return markdown

    def _get_converter(self) -> "DocumentConverter":
        """Build the converter on first use; loading Docling's models is slow."""
        if self._converter is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            options = PdfPipelineOptions(do_ocr=False)
            self._converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=options),
                },
            )
        return self._converter
