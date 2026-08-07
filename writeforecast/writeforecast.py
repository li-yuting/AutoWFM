# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RESULT_DIR = DATA_DIR / "result"

# 蜘蛛读取的预估流入量总表（每次运行后追加本次结果并按 时间+线路 去重，保留最末一条）
TARGET_CSV = BASE_DIR.parent / "data" / "预估流入量.csv"


def transform_forecast(xlsx_path: Path) -> None:
    """将周度预测 Excel 转为按时间、线路展开的 CSV"""
    if not xlsx_path.exists():
        print(f"错误: 文件不存在 — {xlsx_path}")
        sys.exit(1)

    try:
        sheets = pd.read_excel(xlsx_path, sheet_name=None)
    except Exception as e:
        print(f"错误: 读取 Excel 失败 — {e}")
        sys.exit(1)

    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    all_frames = []  # 汇总各 sheet 结果，用于追加到预估流入量总表
    for sheet_name, sheet_df in sheets.items():
        if "时段" not in sheet_df.columns:
            print(f"警告: sheet '{sheet_name}' 缺少 '时段' 列，跳过")
            continue

        sheet_df = pd.melt(
            sheet_df,
            id_vars=["时段"],
            var_name="日期",
            value_name="时段预估量",
        ).reset_index(drop=True)

        sheet_df.sort_values(by=["日期", "时段"], inplace=True)

        sheet_df["时间"] = sheet_df["日期"] + " " + sheet_df["时段"]
        sheet_df["时间"] = pd.to_datetime(sheet_df["时间"], format="%Y-%m-%d %H:%M")

        sheet_df["线路"] = sheet_name

        sheet_df["累计预估量"] = sheet_df.groupby("日期")["时段预估量"].cumsum()

        sheet_df.drop(columns=["日期", "时段"], inplace=True)

        sheet_df["时间"] = sheet_df["时间"].dt.strftime("%Y-%m-%d %H:%M")

        out_path = RESULT_DIR / f"{sheet_name}.csv"
        sheet_df.to_csv(
            out_path,
            columns=["时间", "线路", "时段预估量", "累计预估量"],
            index=False,
        )
        print(f"已生成: {out_path}  ({len(sheet_df)} 行)")
        all_frames.append(sheet_df[["时间", "线路", "时段预估量", "累计预估量"]])

    if all_frames:
        append_to_forecast(pd.concat(all_frames, ignore_index=True))


def append_to_forecast(new_df: pd.DataFrame) -> None:
    """把本次结果追加到 everyday/data/预估流入量.csv 末尾，按 时间+线路 去重保留最末一条"""
    if TARGET_CSV.exists():
        old_df = pd.read_csv(TARGET_CSV)
        combined = pd.concat([old_df, new_df], ignore_index=True)
    else:
        combined = new_df
    # 同一 时间+线路 出现多次时保留最末一条（即本次新追加的）
    combined = combined.drop_duplicates(
        subset=["时间", "线路"], keep="last"
    ).reset_index(drop=True)
    # 统一为可空整型，避免浮点空值写出 "321.0" 污染格式
    combined["时段预估量"] = combined["时段预估量"].astype("Int64")
    combined["累计预估量"] = combined["累计预估量"].astype("Int64")
    TARGET_CSV.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(
        TARGET_CSV,
        columns=["时间", "线路", "时段预估量", "累计预估量"],
        index=False,
    )
    print(f"已追加并去重: {TARGET_CSV}  ({len(combined)} 行)")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        xlsx = Path(sys.argv[1])
    else:
        xlsx = DATA_DIR / "量级预估20260805-0810.xlsx"
    transform_forecast(xlsx)