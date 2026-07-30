"""Publish one standalone launcher EXE from the release directory."""

import argparse
import glob
import os
import subprocess
import sys
import time


GH_REPO = "yg1987/ComfyUI-Mie-Package-Launcher"


def parse_args():
    parser = argparse.ArgumentParser(description="ComfyUI launcher EXE release tool")
    parser.add_argument("--version", default=None, help="Release version, for example v1.0.17")
    parser.add_argument("--file", default=None, help="Standalone EXE to upload")
    parser.add_argument("--title", default=None, help="Release title")
    parser.add_argument("--notes", "--note", default=None, help="Release notes")
    parser.add_argument("--notes-file", default=None, help="Read release notes from a file")
    parser.add_argument("--latest", action="store_true", help="Mark the release as latest")
    parser.add_argument("--list", "-l", action="store_true", help="List release EXE files")
    parser.add_argument("--view", action="store_true", help="List GitHub releases")
    parser.add_argument("--delete", default=None, help="Delete the specified release version")
    parser.add_argument("--repo", default=GH_REPO, help=f"GitHub repository (default: {GH_REPO})")
    return parser.parse_args()


def get_project_dir():
    return os.path.dirname(os.path.abspath(__file__))


def list_exe_files():
    """Return only standalone EXEs directly under release/."""
    pattern = os.path.join(get_project_dir(), "release", "*.exe")
    return sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)


def extract_version_from_filename(filename):
    basename = os.path.basename(filename)
    if "_v" not in basename:
        return None
    try:
        return "v" + basename.split("_v", 1)[1].split("_", 1)[0]
    except (IndexError, ValueError):
        return None


def format_exe_list(files):
    lines = []
    for index, path in enumerate(files, 1):
        size_mb = os.path.getsize(path) / (1024 * 1024)
        modified = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
        version = extract_version_from_filename(path) or "unknown"
        lines.append(f"  [{index}] {os.path.basename(path)}")
        lines.append(f"      version: {version}  size: {size_mb:.1f} MB  modified: {modified}")
    return "\n".join(lines)


def pick_exe_interactive(files):
    if not files:
        raise SystemExit("No standalone EXE was found in release/. Run build.py first.")
    print("\n=== Select the standalone EXE to publish ===")
    print(format_exe_list(files))
    while True:
        choice = input("Number (Enter selects 1): ").strip()
        if not choice:
            return files[0]
        try:
            index = int(choice) - 1
        except ValueError:
            index = -1
        if 0 <= index < len(files):
            return files[index]
        print(f"Enter a number from 1 to {len(files)}.")


def run_gh(arguments, check=True):
    try:
        result = subprocess.run(arguments, capture_output=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        if check:
            raise SystemExit(f"Could not run gh: {exc}") from exc
        return None
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout or "gh failed").strip()
        raise SystemExit(message)
    return result


def normalize_version(version):
    return version if version.startswith("v") else f"v{version}"


def resolve_notes(args, version):
    if args.notes_file:
        try:
            with open(args.notes_file, "r", encoding="utf-8") as notes_file:
                return notes_file.read()
        except OSError as exc:
            raise SystemExit(f"Could not read release notes: {exc}") from exc
    return args.notes or f"Release {version}"


def do_list(_args):
    files = list_exe_files()
    print(format_exe_list(files) if files else "No standalone EXE found in release/.")
    return files


def do_view(args):
    result = run_gh(["gh", "release", "list", "--repo", args.repo])
    if result.stdout:
        print(result.stdout)


def do_delete(args):
    version = normalize_version(args.delete)
    run_gh(["gh", "release", "delete", version, "--repo", args.repo, "--yes"])


def do_upload(args):
    exe_path = os.path.abspath(args.file) if args.file else pick_exe_interactive(list_exe_files())
    if not os.path.isfile(exe_path) or not exe_path.lower().endswith(".exe"):
        raise SystemExit(f"Standalone EXE does not exist: {exe_path}")

    version = args.version or extract_version_from_filename(exe_path)
    if not version:
        raise SystemExit("Could not determine the version. Pass --version explicitly.")
    version = normalize_version(version)
    title = args.title or version
    notes = resolve_notes(args, version)

    existing = run_gh(["gh", "release", "view", version, "--repo", args.repo], check=False)
    if not existing or existing.returncode != 0:
        command = [
            "gh", "release", "create", version,
            "--title", title,
            "--notes", notes,
            "--repo", args.repo,
        ]
        if args.latest:
            command.append("--latest")
        run_gh(command)

    run_gh([
        "gh", "release", "upload", version, exe_path,
        "--repo", args.repo,
        "--clobber",
    ])
    print(f"Published one EXE: https://github.com/{args.repo}/releases/tag/{version}")


def main():
    args = parse_args()
    if args.list:
        do_list(args)
    elif args.view:
        do_view(args)
    elif args.delete:
        do_delete(args)
    else:
        do_upload(args)


if __name__ == "__main__":
    main()
