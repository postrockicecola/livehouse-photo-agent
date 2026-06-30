"""Path resolving service for preview/raw/session locations."""
from __future__ import annotations

import json
from pathlib import Path


class PathResolver:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir.resolve()

    def _read_latest_session_ref(self) -> dict | None:
        try:
            from utils.runtime_paths import resolve_runtime_file

            runtime_ref = resolve_runtime_file(self.base_dir.parent.parent, "latest_session.json")
            if runtime_ref is None or not runtime_ref.is_file():
                return None
            with runtime_ref.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _session_and_raw_hint(self) -> tuple[Path, Path | None]:
        session_dir = self.base_dir.parent
        raw_hint: Path | None = None
        ref = self._read_latest_session_ref()
        if not ref:
            return session_dir, raw_hint
        try:
            previews_ref = Path(ref.get("previews_dir", "")).expanduser().resolve()
        except Exception:
            previews_ref = None
        if previews_ref != self.base_dir:
            return session_dir, raw_hint
        try:
            session_ref = Path(ref.get("session_dir", "")).expanduser().resolve()
            if session_ref.is_dir():
                session_dir = session_ref
        except Exception:
            pass
        raw_dir = ref.get("raw_dir", "")
        if raw_dir:
            try:
                raw_dir_path = Path(str(raw_dir)).expanduser().resolve()
                if raw_dir_path.is_dir():
                    raw_hint = raw_dir_path
            except Exception:
                pass
        return session_dir, raw_hint

    @staticmethod
    def _strip_resource_fork_name(image_name: str) -> str:
        base = Path(image_name).name
        if base.startswith("._"):
            return base[2:]
        return base

    @staticmethod
    def _iter_preview_roots(base_dir: Path):
        yield base_dir
        for folder in ("best", "keep", "trash", "AI_Best_90+", "AI_Keep_60-90", "AI_Trash_Below60"):
            yield base_dir / folder

    def resolve_preview(self, image_name: str) -> Path | None:
        name = Path(image_name).name
        for root in self._iter_preview_roots(self.base_dir):
            candidate = root / name
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _find_arw_for_stem(session_dir: Path, stem: str, raw_dir_hint: Path | None = None) -> Path | None:
        if not stem:
            return None
        roots: list[Path] = []
        if raw_dir_hint and raw_dir_hint.is_dir():
            roots.append(raw_dir_hint)
        for sub in ("RAW", "Raw", "raw"):
            d = session_dir / sub
            if d.is_dir() and d not in roots:
                roots.append(d)
        if session_dir not in roots:
            roots.append(session_dir)
        stem_l = stem.lower()
        for root in roots:
            for ext in (".ARW", ".arw"):
                cand = root / f"{stem}{ext}"
                if cand.is_file():
                    return cand
            try:
                for f in root.iterdir():
                    if not f.is_file():
                        continue
                    if f.suffix.lower() != ".arw":
                        continue
                    if f.stem.lower() == stem_l:
                        return f
            except OSError:
                continue
        return None

    def resolve_raw(self, image_name: str) -> Path | None:
        clean_name = self._strip_resource_fork_name(image_name)
        stem = Path(clean_name).stem
        session_dir, raw_hint = self._session_and_raw_hint()
        return self._find_arw_for_stem(session_dir, stem, raw_hint)

    def session_and_raw_hint(self) -> tuple[Path, Path | None]:
        return self._session_and_raw_hint()
