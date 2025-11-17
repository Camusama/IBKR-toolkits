#!/usr/bin/env python3
"""Package project files to ZIP

Creates a clean ZIP archive excluding:
- Files in .gitignore
- macOS system files (.DS_Store, __MACOSX, etc.)
- Python cache files
- Hidden files

Usage:
    python scripts/package_project.py [--output FILENAME]
"""

import os
import zipfile
import pathspec
from pathlib import Path
from datetime import datetime
import argparse
import sys


# macOS and Windows system files to exclude
SYSTEM_EXCLUDES = [
    '.DS_Store',
    '._.DS_Store',
    '__MACOSX',
    'Thumbs.db',
    'desktop.ini',
    '.Spotlight-V100',
    '.Trashes',
    '.fseventsd',
    '.TemporaryItems',
    '.DocumentRevisions-V100',
    '.AppleDouble',
    '.LSOverride',
    '.AppleDB',
    '.AppleDesktop',
    'Network Trash Folder',
    'Temporary Items',
    '.apdisk',
]

# Additional patterns to exclude
ADDITIONAL_EXCLUDES = [
    '**/__pycache__/**',
    '**/*.pyc',
    '**/*.pyo',
    '**/*.pyd',
    '**/.Python',
    '**/*.so',
    '**/*.egg-info/**',
    '**/.pytest_cache/**',
    '**/.mypy_cache/**',
    '**/.ruff_cache/**',
    '**/dist/**',
    '**/build/**',
]


def read_gitignore(project_root: Path) -> pathspec.PathSpec:
    """Read .gitignore and return PathSpec matcher

    Args:
        project_root: Project root directory

    Returns:
        PathSpec object for matching ignored files
    """
    gitignore_path = project_root / '.gitignore'

    patterns = []

    # Add .gitignore patterns
    if gitignore_path.exists():
        with open(gitignore_path, 'r', encoding='utf-8') as f:
            patterns.extend(f.read().splitlines())

    # Add system file patterns
    patterns.extend(SYSTEM_EXCLUDES)
    patterns.extend(ADDITIONAL_EXCLUDES)

    # Always exclude the zip files themselves
    patterns.extend(['*.zip', '**/*.zip'])

    return pathspec.PathSpec.from_lines('gitwildmatch', patterns)


def should_exclude(file_path: Path, project_root: Path, spec: pathspec.PathSpec) -> bool:
    """Check if file should be excluded

    Args:
        file_path: File path to check
        project_root: Project root directory
        spec: PathSpec object for matching

    Returns:
        True if file should be excluded
    """
    try:
        # Get relative path
        rel_path = file_path.relative_to(project_root)

        # Convert to string with forward slashes (for pathspec)
        rel_path_str = str(rel_path).replace(os.sep, '/')

        # Check if matches gitignore patterns
        if spec.match_file(rel_path_str):
            return True

        # Exclude hidden files (starting with .)
        if any(part.startswith('.') and part not in ['.env.example']
               for part in rel_path.parts):
            return True

        # Check filename against system excludes
        if file_path.name in SYSTEM_EXCLUDES:
            return True

        return False

    except ValueError:
        # File is not relative to project root
        return True


def create_zip_archive(project_root: Path, output_file: Path, verbose: bool = True):
    """Create ZIP archive of project files

    Args:
        project_root: Project root directory
        output_file: Output ZIP file path
        verbose: Print progress messages
    """
    # Read .gitignore patterns
    if verbose:
        print(f"Reading exclusion patterns from .gitignore...")
    spec = read_gitignore(project_root)

    # Create ZIP file
    if verbose:
        print(f"Creating ZIP archive: {output_file}")

    file_count = 0
    excluded_count = 0

    with zipfile.ZipFile(output_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Walk through all files
        for file_path in project_root.rglob('*'):
            # Skip directories
            if file_path.is_dir():
                continue

            # Check if should exclude
            if should_exclude(file_path, project_root, spec):
                excluded_count += 1
                if verbose and excluded_count <= 10:  # Show first 10 excluded files
                    rel_path = file_path.relative_to(project_root)
                    print(f"  [SKIP] {rel_path}")
                continue

            # Add file to ZIP
            rel_path = file_path.relative_to(project_root)

            # Use forward slashes in ZIP for cross-platform compatibility
            arcname = str(rel_path).replace(os.sep, '/')

            try:
                zipf.write(file_path, arcname)
                file_count += 1

                if verbose:
                    print(f"  [ADD]  {rel_path}")

            except Exception as e:
                print(f"  [ERROR] Failed to add {rel_path}: {e}")

    # Print summary
    file_size = output_file.stat().st_size / (1024 * 1024)  # MB

    print("\n" + "=" * 60)
    print("ZIP Archive Created Successfully!")
    print("=" * 60)
    print(f"Output file:     {output_file}")
    print(f"File size:       {file_size:.2f} MB")
    print(f"Files included:  {file_count}")
    print(f"Files excluded:  {excluded_count}")
    print("=" * 60)
    print("\nYou can safely share this ZIP file.")
    print("All sensitive files (.env, logs, data) are excluded.")
    print("=" * 60)


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Package project files to ZIP (excluding .gitignore and system files)"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output ZIP filename (default: ibkr-toolkit-YYYYMMDD.zip)"
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Quiet mode (minimal output)"
    )

    args = parser.parse_args()

    # Get project root
    project_root = Path(__file__).parent.parent

    # Generate default filename if not provided
    if args.output:
        output_file = Path(args.output)
        if not output_file.suffix:
            output_file = output_file.with_suffix('.zip')
    else:
        timestamp = datetime.now().strftime("%Y%m%d")
        output_file = project_root / f"ibkr-toolkit-{timestamp}.zip"

    # Make sure output is absolute path
    if not output_file.is_absolute():
        output_file = project_root / output_file

    # Check if pathspec is installed
    try:
        import pathspec
    except ImportError:
        print("Error: 'pathspec' package is required.")
        print("Install it with: pip install pathspec")
        return 1

    # Create archive
    try:
        create_zip_archive(project_root, output_file, verbose=not args.quiet)
        return 0
    except Exception as e:
        print(f"Error: Failed to create ZIP archive: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
