# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


def transform_schedule(xlsx_path: Path) -> None:
    """将班表 Excel 转为按日期、小时展开的人力结构表"""
    if not xlsx_path.exists():
        print(f"错误: 文件不存在 — {xlsx_path}")
        sys.exit(1)

    try:
        df = pd.read_excel(xlsx_path)
    except Exception as e:
        print(f"错误: 读取 Excel 失败 — {e}")
        sys.exit(1)

    expected_cols = {"班组", "姓名", "入职日期"}
    missing = expected_cols - set(df.columns)
    if missing:
        print(f"错误: 缺少必要列 {missing}，当前列: {list(df.columns)}")
        sys.exit(1)

    df["序号"] = df.index + 1
    df = pd.melt(
        df,
        id_vars=["序号", "班组", "姓名", "入职日期"],
        var_name="日期",
        value_name="班次",
    ).reset_index(drop=True)

    df.sort_values(by=["序号", "日期"], inplace=True)

    df[[f"{x:02d}" for x in range(9, 21)]] = ""

    df["序号"] = df.index + 1
    df = pd.melt(
        df,
        id_vars=["序号", "班组", "姓名", "入职日期", "日期", "班次"],
        var_name="小时",
        value_name="人力数",
    )

    df.sort_values(by=["序号", "日期", "小时"], inplace=True)

    out_path = xlsx_path.with_stem(xlsx_path.stem + "_结果")
    df.to_excel(
        out_path,
        columns=df.columns.drop("序号"),
        index=False,
    )
    print(f"已生成: {out_path}  ({len(df)} 行)")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        xlsx = Path(sys.argv[1])
    else:
        xlsx = DATA_DIR / "班表.xlsx"
    transform_schedule(xlsx)
