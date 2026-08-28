"""
Configuration comparison service
Compares two device configurations and generates diff output
"""
import difflib
from typing import Dict, Any, List, Optional, Sequence, Tuple
import logging

from app.core.config import settings
from app.services.storage import StorageError, storage_service

logger = logging.getLogger(__name__)


def _format_range(start: int, stop: int) -> str:
    """
    Format one side of a unified diff hunk header

    Args:
        start: 0-based start index
        stop: 0-based stop index (exclusive)

    Returns:
        str: Range in unified diff notation
    """
    length = stop - start
    beginning = start + 1

    if length == 1:
        return str(beginning)

    if not length:
        beginning -= 1

    return f"{beginning},{length}"


class ConfigurationComparison:
    """Service for comparing device configurations"""

    # Above this, `include_content` returns nothing for that side rather than
    # a response no browser will render. Comfortably larger than any real
    # device configuration - a 4 MB running-config is around 100,000 lines -
    # and far below COMPARE_MAX_FILE_BYTES, which bounds what may be diffed at
    # all rather than what may be sent to a viewer.
    CONTENT_MAX_CHARS = 4 * 1024 * 1024

    @staticmethod
    def _build_matcher(
        lines1: Sequence[str], lines2: Sequence[str]
    ) -> difflib.SequenceMatcher:
        """
        Build a SequenceMatcher for configuration text

        Keeps difflib's autojunk heuristic on by default, which is what the
        stdlib helpers this service used to call did - it is the difference
        between a fast match and a several-times-slower one on configs full of
        repeated marker lines. COMPARE_ACCURATE_DIFF trades that CPU back for
        better hunk alignment, capped by line count so the cost stays bounded.

        Args:
            lines1: Lines of the first configuration
            lines2: Lines of the second configuration

        Returns:
            difflib.SequenceMatcher: Prepared matcher
        """
        autojunk = True

        if settings.COMPARE_ACCURATE_DIFF:
            longest = max(len(lines1), len(lines2))
            autojunk = longest > settings.COMPARE_ACCURATE_DIFF_MAX_LINES

        return difflib.SequenceMatcher(None, lines1, lines2, autojunk=autojunk)

    @staticmethod
    def _unified_from_opcodes(
        matcher: difflib.SequenceMatcher,
        lines1: Sequence[str],
        lines2: Sequence[str],
        fromfile: str,
        tofile: str,
        context_lines: int,
    ) -> str:
        """
        Render a unified diff from an already-computed matcher

        difflib.unified_diff() would build a second SequenceMatcher and repeat
        the whole match. get_grouped_opcodes() reuses this matcher's cached
        opcodes instead.

        Args:
            matcher: Matcher whose opcodes have already been computed
            lines1: Lines of the first configuration
            lines2: Lines of the second configuration
            fromfile: Label for the first configuration
            tofile: Label for the second configuration
            context_lines: Context lines around each hunk

        Returns:
            str: Unified diff text ('' when the inputs are identical)
        """
        chunks: List[str] = []

        for group in matcher.get_grouped_opcodes(context_lines):
            if not chunks:
                chunks.append(f"--- {fromfile}\n")
                chunks.append(f"+++ {tofile}\n")

            first, last = group[0], group[-1]
            chunks.append(
                f"@@ -{_format_range(first[1], last[2])} "
                f"+{_format_range(first[3], last[4])} @@\n"
            )

            for tag, i1, i2, j1, j2 in group:
                if tag == "equal":
                    chunks.extend(f" {line}" for line in lines1[i1:i2])
                    continue

                if tag in ("replace", "delete"):
                    chunks.extend(f"-{line}" for line in lines1[i1:i2])

                if tag in ("replace", "insert"):
                    chunks.extend(f"+{line}" for line in lines2[j1:j2])

        if not chunks:
            return ""

        # Configuration files often lack a trailing newline; without this the
        # last diff line would run into whatever follows it.
        return "".join(
            chunk if chunk.endswith("\n") else chunk + "\n" for chunk in chunks
        )

    @staticmethod
    def _structured_from_opcodes(
        opcodes: Sequence[Tuple[str, int, int, int, int]],
        lines1: Sequence[str],
        lines2: Sequence[str],
        max_blocks: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """
        Build the structured diff from opcodes the caller already has

        Args:
            opcodes: Opcodes from the shared matcher
            lines1: Lines of the first configuration
            lines2: Lines of the second configuration
            max_blocks: Stop after this many change blocks

        Returns:
            Tuple of (blocks, truncated)
        """
        if max_blocks is None:
            max_blocks = settings.COMPARE_MAX_STRUCTURED_BLOCKS

        structured: List[Dict[str, Any]] = []

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                continue

            if len(structured) >= max_blocks:
                # A response carrying every line of a wholesale rewrite is
                # neither useful to render nor cheap to serialise.
                return structured, True

            structured.append(
                {
                    "type": tag,  # 'replace', 'delete', 'insert'
                    "old_start": i1 + 1,  # 1-indexed line numbers
                    "old_end": i2,
                    "new_start": j1 + 1,
                    "new_end": j2,
                    "old_lines": [line.rstrip("\n") for line in lines1[i1:i2]],
                    "new_lines": [line.rstrip("\n") for line in lines2[j1:j2]],
                }
            )

        return structured, False

    @staticmethod
    def _statistics_from_opcodes(
        opcodes: Sequence[Tuple[str, int, int, int, int]]
    ) -> Dict[str, int]:
        """
        Derive change statistics from opcodes

        Counting from opcodes replaces a second pass that re-scanned every
        rendered diff line and inspected its leading character.

        Args:
            opcodes: Opcodes from the shared matcher

        Returns:
            dict: Change statistics
        """
        added = 0
        removed = 0
        sections = 0

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                continue

            sections += 1

            if tag in ("replace", "delete"):
                removed += i2 - i1

            if tag in ("replace", "insert"):
                added += j2 - j1

        return {
            "added_lines": added,
            "removed_lines": removed,
            "changed_sections": sections,
            "total_changes": added + removed,
        }

    @classmethod
    def compare_configs(
        cls,
        config1_path: str,
        config2_path: str,
        config1_label: Optional[str] = None,
        config2_label: Optional[str] = None,
        context_lines: int = 3,
        include_html: bool = False,
        include_content: bool = False,
    ) -> Dict[str, Any]:
        """
        Compare two configuration files and generate diff

        Args:
            config1_path: Path to first configuration file
            config2_path: Path to second configuration file
            config1_label: Label for first config (e.g., timestamp)
            config2_label: Label for second config
            context_lines: Number of context lines to show around changes
            include_html: Also render the side-by-side HTML diff. This is by
                far the most expensive part of a comparison (difflib compares
                every changed line character by character and emits a full
                HTML document), so it is only produced when asked for.
            include_content: Also return both configurations in full. A viewer
                that lets the reader turn context back on needs the lines the
                diff left out, and both files have already been read here.
                Withheld above CONTENT_MAX_BYTES, with `content_omitted` on
                each side saying so, because a 30 MB configuration is a
                response nobody can use.

        Returns:
            Dict containing diff results and statistics
        """
        label1 = config1_label or "Configuration 1"
        label2 = config2_label or "Configuration 2"

        try:
            max_bytes = settings.COMPARE_MAX_FILE_BYTES
            config1_lines = storage_service.read_lines(config1_path, max_bytes)
            config2_lines = storage_service.read_lines(config2_path, max_bytes)

            if not config1_lines or not config2_lines:
                raise ValueError("One or both configuration files are empty")

            # One matching pass drives the unified diff, the structured diff
            # and the statistics. The previous implementation built three
            # independent matchers over the same two files.
            matcher = cls._build_matcher(config1_lines, config2_lines)
            opcodes = matcher.get_opcodes()

            is_identical = all(tag == "equal" for tag, *_ in opcodes)

            unified = (
                ""
                if is_identical
                else cls._unified_from_opcodes(
                    matcher,
                    config1_lines,
                    config2_lines,
                    label1,
                    label2,
                    context_lines,
                )
            )

            structured_diff, truncated = cls._structured_from_opcodes(
                opcodes, config1_lines, config2_lines
            )

            statistics = cls._statistics_from_opcodes(opcodes)
            statistics["structured_diff_truncated"] = truncated

            result = {
                "is_identical": is_identical,
                "unified_diff": unified,
                "structured_diff": structured_diff,
                "statistics": statistics,
                "config1": {
                    "path": config1_path,
                    "label": config1_label,
                    "line_count": len(config1_lines),
                },
                "config2": {
                    "path": config2_path,
                    "label": config2_label,
                    "line_count": len(config2_lines),
                },
            }

            if include_content:
                for key, lines in (
                    ("config1", config1_lines),
                    ("config2", config2_lines),
                ):
                    # Measured before joining, so an oversized configuration is
                    # never built into a string only to be thrown away.
                    too_big = (
                        sum(len(line) for line in lines) > cls.CONTENT_MAX_CHARS
                    )
                    result[key]["content"] = None if too_big else "".join(lines)
                    result[key]["content_omitted"] = too_big

            if include_html:
                result["html_diff"] = difflib.HtmlDiff(wrapcolumn=80).make_file(
                    config1_lines,
                    config2_lines,
                    fromdesc=label1,
                    todesc=label2,
                    context=True,
                    numlines=context_lines,
                )

            return result

        except (StorageError, FileNotFoundError):
            raise
        except ValueError:
            raise
        except Exception as e:
            logger.exception("Error comparing configurations")
            raise ValueError(f"Error comparing configurations: {e}")

    @classmethod
    def get_change_summary(
        cls,
        config1_path: str,
        config2_path: str,
    ) -> Dict[str, Any]:
        """
        Get a quick summary of changes between two configs (without full diff)

        Args:
            config1_path: Path to first configuration
            config2_path: Path to second configuration

        Returns:
            Dict with change summary
        """
        try:
            # Hash the files first. When nothing changed - the common case for
            # a scheduled backup - this answers the question in one streamed
            # read per file and skips the diff entirely.
            if storage_service.calculate_file_checksum(
                config1_path
            ) == storage_service.calculate_file_checksum(config2_path):
                return {
                    "is_identical": True,
                    "has_changes": False,
                    "change_count": 0,
                    "similarity_ratio": 1.0,
                    "line_count_diff": 0,
                }

            max_bytes = settings.COMPARE_MAX_FILE_BYTES
            config1_lines = storage_service.read_lines(config1_path, max_bytes)
            config2_lines = storage_service.read_lines(config2_path, max_bytes)

            matcher = cls._build_matcher(config1_lines, config2_lines)

            # ratio() and get_opcodes() share the same cached matching blocks,
            # so this is a single pass rather than two.
            changes = sum(1 for tag, *_ in matcher.get_opcodes() if tag != "equal")

            return {
                "is_identical": False,
                "has_changes": True,
                "change_count": changes,
                "similarity_ratio": round(matcher.ratio(), 4),
                "line_count_diff": len(config2_lines) - len(config1_lines),
            }

        except (StorageError, FileNotFoundError):
            raise
        except Exception as e:
            logger.exception("Error generating change summary")
            raise ValueError(f"Error generating change summary: {e}")

    @classmethod
    def compare_config_texts(
        cls,
        config1_text: str,
        config2_text: str,
        config1_label: str = "Configuration 1",
        config2_label: str = "Configuration 2",
        context_lines: int = 3,
    ) -> Dict[str, Any]:
        """
        Compare two configuration texts directly (without file paths)

        Args:
            config1_text: First configuration text
            config2_text: Second configuration text
            config1_label: Label for first config
            config2_label: Label for second config
            context_lines: Number of context lines to show around changes

        Returns:
            Dict containing diff results
        """
        if config1_text == config2_text:
            return {
                "is_identical": True,
                "unified_diff": "",
                "structured_diff": [],
                "statistics": {
                    "added_lines": 0,
                    "removed_lines": 0,
                    "changed_sections": 0,
                    "total_changes": 0,
                    "structured_diff_truncated": False,
                },
            }

        config1_lines = config1_text.splitlines(keepends=True)
        config2_lines = config2_text.splitlines(keepends=True)

        matcher = cls._build_matcher(config1_lines, config2_lines)
        opcodes = matcher.get_opcodes()

        structured_diff, truncated = cls._structured_from_opcodes(
            opcodes, config1_lines, config2_lines
        )

        statistics = cls._statistics_from_opcodes(opcodes)
        statistics["structured_diff_truncated"] = truncated

        return {
            "is_identical": False,
            "unified_diff": cls._unified_from_opcodes(
                matcher,
                config1_lines,
                config2_lines,
                config1_label,
                config2_label,
                context_lines,
            ),
            "structured_diff": structured_diff,
            "statistics": statistics,
        }


# Singleton instance
config_comparison = ConfigurationComparison()
