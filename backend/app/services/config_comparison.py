"""
Configuration comparison service
Compares two device configurations and generates diff output
"""
import difflib
from typing import Dict, Any, List, Optional
from datetime import datetime
import logging

from app.services.storage import storage_service

logger = logging.getLogger(__name__)


class ConfigurationComparison:
    """Service for comparing device configurations"""

    @staticmethod
    def compare_configs(
        config1_path: str,
        config2_path: str,
        config1_label: Optional[str] = None,
        config2_label: Optional[str] = None,
        context_lines: int = 3,
    ) -> Dict[str, Any]:
        """
        Compare two configuration files and generate diff

        Args:
            config1_path: Path to first configuration file
            config2_path: Path to second configuration file
            config1_label: Label for first config (e.g., timestamp)
            config2_label: Label for second config
            context_lines: Number of context lines to show around changes

        Returns:
            Dict containing diff results and statistics
        """
        try:
            # Read both configuration files
            config1_text = storage_service.get_config(config1_path)
            config2_text = storage_service.get_config(config2_path)

            if not config1_text or not config2_text:
                raise ValueError("One or both configuration files are empty")

            # Split into lines for comparison
            config1_lines = config1_text.splitlines(keepends=True)
            config2_lines = config2_text.splitlines(keepends=True)

            # Generate unified diff
            diff_lines = list(
                difflib.unified_diff(
                    config1_lines,
                    config2_lines,
                    fromfile=config1_label or "Configuration 1",
                    tofile=config2_label or "Configuration 2",
                    n=context_lines,
                )
            )

            # Generate HTML diff for rich display
            html_diff = difflib.HtmlDiff(wrapcolumn=80)
            html_output = html_diff.make_file(
                config1_lines,
                config2_lines,
                fromdesc=config1_label or "Configuration 1",
                todesc=config2_label or "Configuration 2",
                context=True,
                numlines=context_lines,
            )

            # Calculate statistics
            added_lines = 0
            removed_lines = 0
            changed_sections = 0
            in_change_block = False

            for line in diff_lines:
                if line.startswith("+") and not line.startswith("+++"):
                    added_lines += 1
                    if not in_change_block:
                        changed_sections += 1
                        in_change_block = True
                elif line.startswith("-") and not line.startswith("---"):
                    removed_lines += 1
                    if not in_change_block:
                        changed_sections += 1
                        in_change_block = True
                elif line.startswith("@@"):
                    in_change_block = False

            # Check if configurations are identical
            is_identical = len(diff_lines) == 0 or (
                len(diff_lines) == 2 and all(
                    line.startswith(("---", "+++")) for line in diff_lines
                )
            )

            # Generate structured diff for programmatic consumption
            structured_diff = ConfigurationComparison._generate_structured_diff(
                config1_lines, config2_lines
            )

            return {
                "is_identical": is_identical,
                "unified_diff": "".join(diff_lines),
                "html_diff": html_output,
                "structured_diff": structured_diff,
                "statistics": {
                    "added_lines": added_lines,
                    "removed_lines": removed_lines,
                    "changed_sections": changed_sections,
                    "total_changes": added_lines + removed_lines,
                },
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

        except FileNotFoundError as e:
            logger.error(f"Configuration file not found: {e}")
            raise ValueError(f"Configuration file not found: {e}")
        except Exception as e:
            logger.exception("Error comparing configurations")
            raise ValueError(f"Error comparing configurations: {e}")

    @staticmethod
    def _generate_structured_diff(
        lines1: List[str], lines2: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Generate structured diff for easier frontend consumption

        Args:
            lines1: Lines from first configuration
            lines2: Lines from second configuration

        Returns:
            List of change blocks with line numbers and change types
        """
        structured = []
        matcher = difflib.SequenceMatcher(None, lines1, lines2)

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                # Skip equal sections in structured diff
                continue

            block = {
                "type": tag,  # 'replace', 'delete', 'insert'
                "old_start": i1 + 1,  # 1-indexed line numbers
                "old_end": i2,
                "new_start": j1 + 1,
                "new_end": j2,
                "old_lines": [line.rstrip() for line in lines1[i1:i2]],
                "new_lines": [line.rstrip() for line in lines2[j1:j2]],
            }

            structured.append(block)

        return structured

    @staticmethod
    def get_change_summary(
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
            # Read configurations
            config1_text = storage_service.get_config(config1_path)
            config2_text = storage_service.get_config(config2_path)

            # Quick comparison
            is_identical = config1_text == config2_text

            if is_identical:
                return {
                    "is_identical": True,
                    "has_changes": False,
                    "change_count": 0,
                }

            # Calculate basic statistics
            config1_lines = config1_text.splitlines()
            config2_lines = config2_text.splitlines()

            matcher = difflib.SequenceMatcher(None, config1_lines, config2_lines)
            ratio = matcher.ratio()

            # Count changes
            changes = 0
            for tag, _, _, _, _ in matcher.get_opcodes():
                if tag != "equal":
                    changes += 1

            return {
                "is_identical": False,
                "has_changes": True,
                "change_count": changes,
                "similarity_ratio": round(ratio, 4),
                "line_count_diff": len(config2_lines) - len(config1_lines),
            }

        except Exception as e:
            logger.exception("Error generating change summary")
            raise ValueError(f"Error generating change summary: {e}")

    @staticmethod
    def compare_config_texts(
        config1_text: str,
        config2_text: str,
        config1_label: str = "Configuration 1",
        config2_label: str = "Configuration 2",
    ) -> Dict[str, Any]:
        """
        Compare two configuration texts directly (without file paths)

        Args:
            config1_text: First configuration text
            config2_text: Second configuration text
            config1_label: Label for first config
            config2_label: Label for second config

        Returns:
            Dict containing diff results
        """
        # Split into lines
        config1_lines = config1_text.splitlines(keepends=True)
        config2_lines = config2_text.splitlines(keepends=True)

        # Generate unified diff
        diff_lines = list(
            difflib.unified_diff(
                config1_lines,
                config2_lines,
                fromfile=config1_label,
                tofile=config2_label,
                n=3,
            )
        )

        # Check if identical
        is_identical = config1_text == config2_text

        # Generate structured diff
        structured_diff = ConfigurationComparison._generate_structured_diff(
            config1_lines, config2_lines
        )

        return {
            "is_identical": is_identical,
            "unified_diff": "".join(diff_lines),
            "structured_diff": structured_diff,
        }


# Singleton instance
config_comparison = ConfigurationComparison()
