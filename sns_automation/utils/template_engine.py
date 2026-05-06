"""投稿テンプレート選択 + 変数置換

設計:
- カテゴリ内のテンプレを weight に基づきランダム選択
- {{var}} 形式の変数を置換
- 直近使用したテンプレ ID は履歴ファイルに記録 → 連続使用を避ける（簡易スパム判定回避）
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VAR_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@dataclass
class Template:
    id: str
    text: str
    weight: float = 1.0


def load_templates(yaml_path: Path | str) -> dict[str, dict[str, list[Template]]]:
    """templates.yaml をロードし、`{platform: {category: [Template]}}` の dict に変換。"""
    import yaml

    raw = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    out: dict[str, dict[str, list[Template]]] = {}
    for platform, categories in (raw.get("templates") or {}).items():
        out[platform] = {}
        for category, items in (categories or {}).items():
            out[platform][category] = [
                Template(id=item["id"], text=item["text"], weight=float(item.get("weight", 1.0)))
                for item in items
            ]
    return out


def select(
    templates: list[Template],
    history: list[str] | None = None,
    avoid_recent: int = 3,
    available_vars: set[str] | None = None,
) -> Template:
    """weight に従いランダム選択。

    - 直近 `avoid_recent` 件は除外
    - `available_vars` 指定時、テンプレが要求する変数が全て揃っているものだけを優先
      （揃ってるものがない場合は通常選択にフォールバック）
    """
    if not templates:
        raise ValueError("テンプレが空")

    recent = set((history or [])[-avoid_recent:])
    pool = [t for t in templates if t.id not in recent] or templates

    if available_vars is not None:
        satisfied = [t for t in pool if VAR_PATTERN.findall(t.text) <= [] or set(VAR_PATTERN.findall(t.text)).issubset(available_vars)]
        if satisfied:
            pool = satisfied
        else:
            logger.warning("変数を全て満たすテンプレなし → フォールバック選択")

    weights = [t.weight for t in pool]
    return random.choices(pool, weights=weights, k=1)[0]


def render(template: Template, variables: dict[str, Any]) -> str:
    """`{{var}}` を `variables` の値で置換。未定義変数はそのまま残る。"""
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables:
            return str(variables[key])
        logger.warning("テンプレ変数 %s が未定義（テンプレ id=%s）", key, template.id)
        return match.group(0)

    return VAR_PATTERN.sub(replace, template.text)


class HistoryStore:
    """テンプレ使用履歴の永続化（簡易 JSON ファイル）。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._data: dict[str, list[str]] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.warning("履歴ファイルが破損 → 空で初期化: %s", self.path)
                self._data = {}

    def get(self, key: str) -> list[str]:
        return list(self._data.get(key, []))

    def append(self, key: str, template_id: str, max_keep: int = 50) -> None:
        items = self._data.setdefault(key, [])
        items.append(template_id)
        del items[:-max_keep]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
