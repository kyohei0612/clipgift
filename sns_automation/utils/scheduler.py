"""スケジュール解決

`schedule.yaml` を読み込み、現在時刻 / 指定モードに対応するカテゴリを返す。

GitHub Actions の cron は UTC で発火するので、ここでは「今がどのスケジュール枠か」
を判定するロジックを `--schedule-name` 引数 or 直近 cron で決める。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ScheduleSlot:
    name: str
    cron: str
    platforms: dict[str, str]  # {platform_name: category}


def load_schedule(yaml_path: Path | str) -> dict[str, ScheduleSlot]:
    """schedule.yaml をロード。`{name: ScheduleSlot}` で返す。"""
    import yaml

    raw = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    out: dict[str, ScheduleSlot] = {}
    for slot in raw.get("schedules") or []:
        out[slot["name"]] = ScheduleSlot(
            name=slot["name"],
            cron=slot["cron"],
            platforms=dict(slot.get("platforms") or {}),
        )
    return out


def resolve(
    schedules: dict[str, ScheduleSlot],
    name: str | None,
    fallback: str = "weekday_morning",
) -> ScheduleSlot:
    """指定スケジュール名で取得。未指定 / 不正なら fallback。"""
    if name and name in schedules:
        return schedules[name]
    if name:
        logger.warning("スケジュール %s が未定義 → fallback=%s", name, fallback)
    return schedules[fallback]
