from __future__ import annotations

import argparse
from pathlib import Path

from reader import read_schedule
from scheduler import SchedulerConfig, run_scheduler
from validators import validate_schedule
from writer import write_schedule

# ========== 可调参数 ==========
PRESET_REST_DAYS = 8
MAX_CONSECUTIVE_WORK_NORMAL = 6
MAX_CONSECUTIVE_WORK_PHASE3 = 5
MAX_CONSECUTIVE_REST = 2
MIN_WORK_DAYS_BETWEEN_REST_BLOCKS = 3
MAX_HIGH_CONSECUTIVE = 2
BALANCE_THRESHOLD = 2
Z_MIN_CONSECUTIVE = 2
Z_MAX_CONSECUTIVE = 3
# ==============================


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoShift 排班工具")
    parser.add_argument("input", nargs="?", default="排班计划.xlsx", help="输入排班计划 xlsx")
    parser.add_argument("output", nargs="?", default="排班结果.xlsx", help="输出排班结果 xlsx")
    args = parser.parse_args()

    config = SchedulerConfig(
        preset_rest_days=PRESET_REST_DAYS,
        max_consecutive_work_normal=MAX_CONSECUTIVE_WORK_NORMAL,
        max_consecutive_work_phase3=MAX_CONSECUTIVE_WORK_PHASE3,
        max_consecutive_rest=MAX_CONSECUTIVE_REST,
        min_work_days_between_rest_blocks=MIN_WORK_DAYS_BETWEEN_REST_BLOCKS,
        max_high_consecutive=MAX_HIGH_CONSECUTIVE,
        balance_threshold=BALANCE_THRESHOLD,
        z_min_consecutive=Z_MIN_CONSECUTIVE,
        z_max_consecutive=Z_MAX_CONSECUTIVE,
    )

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
