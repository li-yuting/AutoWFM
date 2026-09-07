from __future__ import annotations

import argparse
from pathlib import Path

from reader import read_schedule
from scheduler import SchedulerConfig, run_scheduler
from validators import validate_schedule
from writer import write_schedule


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoShift 排班工具")
    parser.add_argument("input", nargs="?", default="排班计划.xlsx", help="输入排班计划 xlsx")
    parser.add_argument("output", nargs="?", default="排班结果.xlsx", help="输出排班结果 xlsx")
    args = parser.parse_args()

    # 仅 preset_rest_days 与 SchedulerConfig 默认值不同(6->8)，其余取 dataclass 默认
    config = SchedulerConfig(preset_rest_days=8)

    input_path = Path(args.input)
    output_path = Path(args.output)
    schedule = read_schedule(input_path)
    run_scheduler(schedule, config)
    schedule.warnings.clear()
    validate_schedule(schedule, config)
    write_schedule(schedule, output_path)

    errors = sum(1 for warning in schedule.warnings if warning.severity == "ERROR")
    warns = sum(1 for warning in schedule.warnings if warning.severity == "WARN")
    infos = sum(1 for warning in schedule.warnings if warning.severity == "INFO")
    print(f"已输出: {output_path}")
    print(f"员工: {len(schedule.employees)}，日期: {len(schedule.dates)}")
    print(f"验证信息: ERROR {errors} / WARN {warns} / INFO {infos}")
    for warning in schedule.warnings[:20]:
        who = f" {warning.employee}" if warning.employee else ""
        date = f" {warning.date:%Y-%m-%d}" if hasattr(warning.date, "strftime") else ""
        print(f"[{warning.severity}] {warning.check_id}{who}{date}: {warning.message}")
    if len(schedule.warnings) > 20:
        print(f"... 其余 {len(schedule.warnings) - 20} 条请查看“统计”sheet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
