# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import io
import pandas as pd
import openpyxl
from collector import detail

def _xlsx(rows):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["", ""]); ws.append(["", ""])  # 两行空白
    for r in rows: ws.append(r)
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()

def main():
    df = pd.DataFrame({"渠道来源":["电话呼入呼入","在线客服呼入呼入","电话呼入"],
                       "处理组别":["转接一组","转接二组","转接一组"]})
    fcfg = {"channel_column":"渠道来源","channels":["电话呼入呼入","在线客服呼入呼入"],
            "group_column":"处理组别","groups":["转接一组","转接二组","贷后转接组"]}
    c = detail.count_groups(df, fcfg)
    assert c == {"转接一组":1,"转接二组":1,"贷后转接组":0}, c
    df2 = detail._parse_excel(_xlsx([["渠道来源","处理组别"],["电话呼入呼入","转接一组"]]))
    assert list(df2.columns) == ["渠道来源","处理组别"], list(df2.columns)
    assert len(df2) == 1, len(df2)

    # row_exclude: 建立坐席 == 接收坐席 的行被排除；仅统计不等的行
    df3 = pd.DataFrame({
        "建立坐席": ["杨国蓉","段雪楠","同一个人","甲"],
        "接收坐席": ["闫月玲","宋佳敏","同一个人","乙"],
        "接收组":   ["转接一组","转接一组","转接一组","转接二组"],
    })
    fcfg3 = {
        "group_column": "接收组",
        "groups": ["转接一组", "转接二组"],
        "row_exclude": {"eq_columns": ["建立坐席", "接收坐席"]},
    }
    c3 = detail.count_groups(df3, fcfg3)
    # 第3行建立==接收被排除；转接一组剩2行(第1、2行)，转接二组1行
    assert c3 == {"转接一组": 2, "转接二组": 1}, c3

    # 未配置 row_exclude 时行为不变（向后兼容）
    fcfg_noexc = {"group_column": "接收组", "groups": ["转接一组", "转接二组"]}
    c_noexc = detail.count_groups(df3, fcfg_noexc)
    assert c_noexc == {"转接一组": 3, "转接二组": 1}, c_noexc

    # exclude_groups：仅对列出的组排除等值行，其余组全保留
    # df5: 转接一组有1行等值(应排除)，常规工单处理组有1行等值(不排除，不在 exclude_groups)
    df5 = pd.DataFrame({
        "建立坐席": ["甲","乙","丙","丁"],
        "接收坐席": ["乙","乙","丙","戊"],   # 第2、3行建立==接收
        "接收组":   ["转接一组","转接一组","常规工单处理组","常规工单处理组"],
    })
    fcfg5 = {
        "group_column": "接收组",
        "groups": ["转接一组", "常规工单处理组"],
        "row_exclude": {
            "eq_columns": ["建立坐席", "接收坐席"],
            "exclude_groups": ["转接一组"],  # 仅转接一组排除，常规工单处理组保留全部
        },
    }
    c5 = detail.count_groups(df5, fcfg5)
    # 转接一组: 第1行不等(保留)+第2行等值但在exclude_groups内(排除) = 1
    # 常规工单处理组: 第3行等值但不在exclude_groups(保留)+第4行不等(保留) = 2
    assert c5 == {"转接一组": 1, "常规工单处理组": 2}, c5

    # 会话记录既有配置（channel + group，无 row_exclude）保持原语义
    df4 = pd.DataFrame({
        "渠道来源": ["电话呼入呼入","在线客服呼入呼入","电话呼入"],
        "处理组别": ["转接一组","转接二组","转接一组"],
    })
    fcfg4 = {"channel_column":"渠道来源","channels":["电话呼入呼入","在线客服呼入呼入"],
            "group_column":"处理组别","groups":["转接一组","转接二组","贷后转接组"]}
    assert detail.count_groups(df4, fcfg4) == {"转接一组":1,"转接二组":1,"贷后转接组":0}

    print("detail OK")

if __name__ == "__main__": main()
