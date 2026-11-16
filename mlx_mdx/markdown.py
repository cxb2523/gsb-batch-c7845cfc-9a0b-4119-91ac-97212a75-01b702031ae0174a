"""Markdown generation helpers backed by the MLX ReaderLM model."""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast
from urllib.parse import unquote, urlparse

from mlx_lm import batch_generate as batch_generate_text
from mlx_lm import load as load_model

from .models import ImageAsset, ImageOutcome, PageMetadata

logger = logging.getLogger("mlx_mdx")


@dataclass
class MarkdownRequest:
    """Payload used for batch Markdown generation."""

    metadata: PageMetadata
    content_html: str
    plain_text: str
    images: List[ImageAsset]


class ReaderLMMarkdownGenerator:
    """Thin wrapper around the MLX ReaderLM model for Markdown generation."""

    _ALLOW_PATTERNS = [
        "*.json",
        "model*.safetensors",
        "*.py",
        "tokenizer.model",
        "*.tiktoken",
        "tiktoken.model",
        "*.txt",
        "*.jsonl",
        "*.jinja",
    ]

    def __init__(self, model_id: str, max_tokens: int) -> None:
        self.model_id = model_id
        self.max_tokens = max_tokens
        self._model: Any = None
        self._tokenizer: Any = None
        self._model_load_seconds: float = 0.0
        self._model_load_reported = False
        self._resolved_load_target: Optional[str] = None

    def _resolve_env_override(self) -> Optional[Path]:
        override = os.getenv("MODEL_DIR")
        if not override:
            return None
        override_path = Path(override).expanduser()
        if override_path.exists():
            logger.debug("MODEL_DIR override detected at %s", override_path)
            return override_path
        logger.warning(
            "MODEL_DIR is set to %s but the path does not exist; falling back to %s",
            override_path,
            self.model_id,
        )
        return None

    def _resolve_snapshot_path(self, repo_id: str) -> Path:
        from huggingface_hub import snapshot_download

        try:
            local_path = Path(
                snapshot_download(
                    repo_id,
                    local_files_only=True,
                    allow_patterns=self._ALLOW_PATTERNS,
                )
            )
            logger.info(
                "Resolved cached model for %s at %s (local_files_only=True)",
                repo_id,
                local_path,
            )
            return local_path
        except Exception as err:  # noqa: BLE001 - propagate diagnostics
            logger.info(
                "Local cache for %s was not found (%s); attempting snapshot download.",
                repo_id,
                err,
            )
            local_path = Path(
                snapshot_download(repo_id, allow_patterns=self._ALLOW_PATTERNS)
            )
            logger.debug("Downloaded model %s to %s", repo_id, local_path)
            return local_path

    def _determine_load_target(self) -> str:
        if self._resolved_load_target:
            return self._resolved_load_target

        env_override = self._resolve_env_override()
        if env_override:
            self._resolved_load_target = str(env_override)
            return self._resolved_load_target

        candidate = Path(self.model_id).expanduser()
        if candidate.exists():
            logger.debug(
                "Model identifier %s resolves to local path %s",
                self.model_id,
                candidate,
            )
            self._resolved_load_target = str(candidate)
            return self._resolved_load_target

        snapshot_path = self._resolve_snapshot_path(self.model_id)
        self._resolved_load_target = str(snapshot_path)
        return self._resolved_load_target

    def _ensure_model(self) -> None:
        if self._model is None or self._tokenizer is None:
            load_target = self._determine_load_target()
            if load_target != self.model_id:
                logger.info(
                    "Loading model %s (resolved from %s)",
                    load_target,
                    self.model_id,
                )
            else:
                logger.info("Loading model %s", load_target)
            start = time.perf_counter()
            self._model, self._tokenizer = load_model(load_target)
            self._model_load_seconds = time.perf_counter() - start
            self._model_load_reported = False
            logger.debug(
                "Loaded model from %s in %.2fs",
                load_target,
                self._model_load_seconds,
            )

    def consume_model_load_seconds(self) -> float:
        """Return the model load time once per load event."""
        if not self._model_load_reported and self._model_load_seconds:
            self._model_load_reported = True
            return self._model_load_seconds
        return 0.0

    def _build_prompt_messages(
        self,
        metadata: PageMetadata,
        content_html: str,
        plain_text: str,
        images: List[ImageAsset],
    ) -> List[dict[str, str]]:
        system_prompt = (
            "You convert rendered HTML from web articles into precise Markdown suitable for offline reading. "
            "Return only the Markdown body without any YAML front matter. Preserve headings, lists, tables, "
            "inline emphasis, code blocks, and hyperlinks. Reference images only if they appear relevant. "
            "If you include images, use the provided local path values exactly. Avoid adding commentary about the task."
        )

        content_sections: List[str] = [
            f"Source URL: {metadata.source_url}",
        ]

        if metadata.title:
            content_sections.append(f"Page Title: {metadata.title}")
        if metadata.description:
            content_sections.append(f"Description: {metadata.description}")
        if metadata.byline:
            content_sections.append(f"Byline: {metadata.byline}")

        if content_html:
            content_sections.append("Main content HTML:\n" + content_html)
        if plain_text:
            content_sections.append("Extracted plain text:\n" + plain_text)

        if images:
            manifest_lines = [
                "Image manifest (use relative paths in Markdown if needed):"
            ]
            for idx, asset in enumerate(images, start=1):
                alt = asset.alt_text or "(no alt text provided)"
                manifest_lines.append(
                    f"{idx}. alt: {alt} | local_path: {asset.relative_path} | source: {asset.absolute_url}"
                )
            content_sections.append("\n".join(manifest_lines))
        else:
            content_sections.append("No downloadable images were captured.")

        content_sections.append(
            "Return only the Markdown body content for the article above."
        )

        user_content = "\n\n".join(content_sections)

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    def _build_prompt(
        self,
        metadata: PageMetadata,
        content_html: str,
        plain_text: str,
        images: List[ImageAsset],
    ) -> List[int]:
        """Render a tokenized chat prompt for the ReaderLM model."""
        messages = self._build_prompt_messages(
            metadata, content_html, plain_text, images
        )

        return self._tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True
        )

    def _generate_from_prompts(self, prompts: Sequence[List[int]]) -> List[str]:
        """Generate Markdown bodies from prepared token sequences."""
        logger.debug("Running batch generation for %d prompt(s)", len(prompts))
        prompt_batch = list(prompts)
        batch_result = batch_generate_text(
            self._model,
            self._tokenizer,
            # mlx_lm reports prompts as List[int], but supports batches of token lists.
            prompts=cast(Any, prompt_batch),
            max_tokens=self.max_tokens,
            verbose=False,
        )
        return [text.strip() for text in batch_result.texts]

    def generate_body(
        self,
        metadata: PageMetadata,
        content_html: str,
        plain_text: str,
        images: List[ImageAsset],
    ) -> str:
        """Produce Markdown body text from raw article content."""
        self._ensure_model()
        prompt = self._build_prompt(metadata, content_html, plain_text, images)
        logger.debug("Prompt token length: %d", len(prompt))
        return self._generate_from_prompts([prompt])[0]

    def generate_bodies(
        self,
        requests: Sequence[MarkdownRequest],
    ) -> List[str]:
        """Produce Markdown bodies for multiple requests sequentially."""
        if not requests:
            return []

        results: List[str] = []
        for idx, req in enumerate(requests, start=1):
            logger.debug("Processing request %d of %d", idx, len(requests))
            result = self.generate_body(
                req.metadata, req.content_html, req.plain_text, req.images
            )
            results.append(result)

        return results


_MARKDOWN_IMAGE_RE = re.compile(
    # Allow one level of balanced parentheses in the target, e.g. ``a_(1).png``.
    r"!\[(?P<alt>[^\]\n]*)\]\(\s*"
    r"(?P<target><[^>\n]*>|(?:[^()\s]|\([^()\s]*\))+)"
    r"(?P<title>\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)
_MARKDOWN_REF_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]\n]*)\]\[(?P<label>[^\]\n]*)\]"
)
_LINK_DEFINITION_RE = re.compile(
    r"^[ ]{0,3}\[(?P<label>[^\]]+)\]:[ \t]*(?P<target><[^>\n]*>|\S+)"
    r"(?P<rest>.*)$",
    re.MULTILINE,
)
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_HTML_ATTR_RE = re.compile(
    r"(?P<name>\bsrc|\balt)\s*=\s*(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_FENCE_OPEN_RE = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})")
# Inline code spans cannot cross a line break, so keep the body single-line.
# Otherwise a stray backtick in prose could swallow later image references.
_INLINE_CODE_RE = re.compile(r"(?P<ticks>`+)(?P<body>[^`\n]+?)(?P=ticks)")

_REMOTE_SCHEMES = {"http", "https", "ftp", "ftps", "data"}


def _normalise_reference(reference: str) -> str:
    """Strip angle brackets/whitespace and decode percent escapes for lookup."""
    cleaned = reference.strip()
    if cleaned.startswith("<") and cleaned.endswith(">"):
        cleaned = cleaned[1:-1].strip()
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def _candidate_references(reference: str) -> List[str]:
    normalised = _normalise_reference(reference)
    variants = [normalised]
    decoded = unquote(normalised)
    if decoded != normalised:
        variants.append(decoded)
    return variants


def _is_local_reference(reference: str) -> bool:
    """Return True when the reference points at the local filesystem."""
    parsed = urlparse(reference)
    if parsed.scheme in _REMOTE_SCHEMES:
        return False
    return True


class _ImageReferenceResolver:
    """Resolve image references against the images that were actually saved."""

    def __init__(
        self,
        assets: Sequence[ImageAsset],
        outcomes: Optional[Sequence[ImageOutcome]] = None,
        output_dir: Optional[Path] = None,
    ) -> None:
        self._saved: Dict[str, str] = {}
        self._saved_relative: set[str] = set()
        for asset in assets:
            self._saved_relative.add(_normalise_reference(asset.relative_path))
            for key in (
                asset.absolute_url,
                asset.original_src,
                asset.relative_path,
                asset.filename,
            ):
                if not key:
                    continue
                self._saved.setdefault(key, asset.relative_path)
                self._saved.setdefault(_normalise_reference(key), asset.relative_path)
                decoded = unquote(key)
                if decoded != key:
                    self._saved.setdefault(decoded, asset.relative_path)

        self._skipped: set[str] = set()
        if outcomes:
            for outcome in outcomes:
                if outcome.saved:
                    continue
                for key in (outcome.absolute_url, outcome.original_src):
                    if not key:
                        continue
                    self._skipped.add(key)
                    self._skipped.add(_normalise_reference(key))
                    self._skipped.add(unquote(key))
        self._output_dir = output_dir

    def resolve(self, reference: str) -> Optional[str]:
        """Return the saved relative path, or ``None`` when unsaved/unknown."""
        variants = _candidate_references(reference)
        for variant in variants:
            saved_path = self._saved.get(variant)
            if saved_path is not None:
                return saved_path if self._file_exists(saved_path) else None
        if any(variant in self._skipped for variant in variants):
            return None
        if not _is_local_reference(_normalise_reference(reference)):
            return None

        # Only references that resolve to an asset we actually stored are
        # accepted; anything else (including a stale file left by an earlier
        # run) is dropped so the Markdown never links to an unverified file.
        for variant in variants:
            if variant in self._saved_relative:
                return variant if self._file_exists(variant) else None
            absolute_from_output = self._resolve_under_output_dir(variant)
            if absolute_from_output is not None:
                return absolute_from_output
        return None

    def _file_exists(self, relative_path: str) -> bool:
        """Confirm the saved asset is still present on disk.

        Without an ``output_dir`` there is nothing to check against, so the
        recorded outcome is treated as the source of truth.
        """
        if self._output_dir is None:
            return True
        try:
            return (self._output_dir / relative_path).is_file()
        except OSError:  # pragma: no cover - defensive
            return False

    def refers_to_skipped_image(self, reference: str) -> bool:
        """Return True when the reference is a known, unsaved image candidate."""
        return any(
            variant in self._skipped
            for variant in _candidate_references(reference)
        )

    def _resolve_under_output_dir(self, reference: str) -> Optional[str]:
        """Map an absolute path inside the output directory to its asset."""
        if self._output_dir is None or not Path(reference).is_absolute():
            return None
        try:
            relative = Path(reference).resolve().relative_to(
                self._output_dir.resolve()
            )
        except (OSError, ValueError):
            return None
        candidate = _normalise_reference(str(relative).replace("\\", "/"))
        return candidate if candidate in self._saved_relative else None


def _split_fenced_blocks(markdown: str) -> List[Tuple[bool, str]]:
    """Split Markdown into ``(is_fenced, text)`` segments.

    Fenced code blocks are returned verbatim so their contents are never
    rewritten; everything else is eligible for image-link processing.
    """
    segments: List[Tuple[bool, str]] = []
    buffer: List[str] = []
    fence: Optional[str] = None
    for line in markdown.splitlines(keepends=True):
        match = _FENCE_OPEN_RE.match(line)
        if fence is None:
            if match:
                if buffer:
                    segments.append((False, "".join(buffer)))
                    buffer = []
                fence = match.group("fence")
                buffer.append(line)
            else:
                buffer.append(line)
            continue

        buffer.append(line)
        stripped = line.strip()
        if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
            segments.append((True, "".join(buffer)))
            buffer = []
            fence = None
    if buffer:
        segments.append((fence is not None, "".join(buffer)))
    return segments


def _split_inline_code(text: str) -> List[Tuple[bool, str]]:
    """Split text into ``(is_code_span, text)`` segments for inline code."""
    segments: List[Tuple[bool, str]] = []
    position = 0
    for match in _INLINE_CODE_RE.finditer(text):
        # Skip paired single backticks that are really empty spans.
        if match.start() > position:
            segments.append((False, text[position : match.start()]))
        segments.append((True, match.group(0)))
        position = match.end()
    if position < len(text):
        segments.append((False, text[position:]))
    return segments


def _transform_prose(markdown: str, transform) -> str:
    """Apply ``transform`` to Markdown outside fenced blocks/code spans."""
    output: List[str] = []
    for is_fenced, block in _split_fenced_blocks(markdown):
        if is_fenced:
            output.append(block)
            continue
        for is_code, chunk in _split_inline_code(block):
            output.append(chunk if is_code else transform(chunk))
    return "".join(output)


def _collect_link_definitions(markdown: str) -> Dict[str, str]:
    """Collect ``[label]: target`` definitions outside fenced blocks/code."""
    definitions: Dict[str, str] = {}

    def _collect(text: str) -> str:
        for match in _LINK_DEFINITION_RE.finditer(text):
            label = " ".join(match.group("label").split()).casefold()
            definitions[label] = match.group("target")
        return text

    _transform_prose(markdown, _collect)
    return definitions


def _replace_markdown_image(match: re.Match[str], resolver: "_ImageReferenceResolver") -> str:
    alt = match.group("alt")
    target = match.group("target")
    saved_path = resolver.resolve(target)
    if saved_path is None:
        return alt
    title = match.group("title") or ""
    return f"![{alt}]({saved_path}{title})"


def _replace_reference_image(
    match: re.Match[str],
    resolver: "_ImageReferenceResolver",
    definitions: Dict[str, str],
) -> str:
    alt = match.group("alt")
    label = match.group("label") or alt
    key = " ".join(label.split()).casefold()
    target = definitions.get(key)
    if target is None:
        return match.group(0)
    saved_path = resolver.resolve(target)
    if saved_path is None:
        return alt
    return f"![{alt}]({saved_path})"


def _rewrite_link_definitions(
    text: str,
    resolver: "_ImageReferenceResolver",
) -> str:
    """Rewrite ``[label]: target`` definitions to saved local paths."""
    return _LINK_DEFINITION_RE.sub(
        lambda match: _rewrite_one_definition(match, resolver), text
    )


def _rewrite_one_definition(
    match: re.Match[str],
    resolver: "_ImageReferenceResolver",
) -> str:
    target = match.group("target")
    saved_path = resolver.resolve(target)
    if saved_path is None:
        return match.group(0)
    return f"[{match.group('label')}]: {saved_path}{match.group('rest')}"


def _label_still_used(text_without_definitions: str, label: str) -> bool:
    """Return True when ``label`` is referenced by a link in the body."""
    pattern = re.compile(
        r"(?<!!)\[[^\]\n]*\]\[\s*" + re.escape(label) + r"\s*\]"
        r"|(?<!!)\[\s*" + re.escape(label) + r"\s*\](?![\[(:])",
        re.IGNORECASE,
    )
    return bool(pattern.search(text_without_definitions))


def _prune_dangling_definitions(
    text: str,
    resolver: "_ImageReferenceResolver",
) -> str:
    """Drop definitions that would reintroduce an unsaved image reference.

    A definition is removed only when its target is an image candidate that
    was skipped (or a local path that was never saved) *and* no ordinary link
    in the body still uses the label, so unrelated links stay intact.
    """
    body_without_definitions = _LINK_DEFINITION_RE.sub("", text)

    def _sub(match: re.Match[str]) -> str:
        target = match.group("target")
        if resolver.resolve(target) is not None:
            return _rewrite_one_definition(match, resolver)
        normalised = _normalise_reference(target)
        if not (
            resolver.refers_to_skipped_image(target)
            or _is_local_reference(normalised)
        ):
            return match.group(0)
        label = match.group("label")
        if _label_still_used(body_without_definitions, label):
            return match.group(0)
        return ""

    return _LINK_DEFINITION_RE.sub(_sub, text)


def _replace_html_image(match: re.Match[str], resolver: "_ImageReferenceResolver") -> str:
    tag = match.group(0)
    attributes = {
        attr.group("name").lower(): attr for attr in _HTML_ATTR_RE.finditer(tag)
    }
    src_attr = attributes.get("src")
    if src_attr is None:
        return tag
    saved_path = resolver.resolve(src_attr.group("value"))
    if saved_path is None:
        alt_attr = attributes.get("alt")
        return alt_attr.group("value") if alt_attr else ""
    start, end = src_attr.span("value")
    return tag[:start] + saved_path + tag[end:]


def replace_image_links(
    markdown: str,
    assets: Sequence[ImageAsset],
    *,
    outcomes: Optional[Sequence[ImageOutcome]] = None,
    output_dir: Optional[Path] = None,
) -> str:
    """Rewrite image references so only locally saved assets are linked.

    Saved images are rewritten to their local relative path. References to
    candidates that were skipped (or to local files that do not exist) are
    reduced to their alt text, so the delivered Markdown never points at a
    missing file. Fenced code blocks and inline code spans are left untouched.
    """
    if not markdown:
        return markdown

    resolver = _ImageReferenceResolver(assets, outcomes, output_dir)
    # Reference-style images may point at a definition that appears later in
    # the document, so collect every definition before rewriting anything.
    definitions = _collect_link_definitions(markdown)

    def _transform(text: str) -> str:
        rewritten = _rewrite_link_definitions(text, resolver)
        rewritten = _MARKDOWN_IMAGE_RE.sub(
            lambda match: _replace_markdown_image(match, resolver), rewritten
        )
        rewritten = _MARKDOWN_REF_IMAGE_RE.sub(
            lambda match: _replace_reference_image(match, resolver, definitions),
            rewritten,
        )
        rewritten = _HTML_IMAGE_RE.sub(
            lambda match: _replace_html_image(match, resolver), rewritten
        )
        return _prune_dangling_definitions(rewritten, resolver)

    return _transform_prose(markdown, _transform)


def strip_code_fence(markdown: str) -> str:
    """Remove a surrounding Markdown code fence if the model adds one."""
    text = markdown.strip()
    if not text.startswith("```"):
        return markdown.strip()

    lines = text.splitlines()
    fence_info = lines[0].strip()
    if not fence_info.startswith("```"):
        return text

    closing_index = None
    for idx in range(len(lines) - 1, 0, -1):
        if lines[idx].strip().startswith("```"):
            closing_index = idx
            break

    if closing_index is None:
        return text

    inner = "\n".join(lines[1:closing_index]).strip()
    return inner


def compose_markdown(metadata: PageMetadata, body: str, assets: List[ImageAsset]) -> str:
    """Generate final Markdown including front matter."""
    timestamp = (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    front_matter_lines = ["---"]
    if metadata.title:
        front_matter_lines.append(f"title: {metadata.title}")
    front_matter_lines.append(f"source_url: {metadata.source_url}")
    front_matter_lines.append(f"retrieved_at: {timestamp}")
    if metadata.description:
        front_matter_lines.append(f"description: {metadata.description}")
    if metadata.byline:
        front_matter_lines.append(f"byline: {metadata.byline}")
    if assets:
        image_files = [asset.relative_path for asset in assets]
        files_str = "[" + ", ".join(image_files) + "]"
        front_matter_lines.append(f"images: {files_str}")
    front_matter_lines.append("---\n")

    return "\n".join(front_matter_lines) + body.strip() + "\n"
