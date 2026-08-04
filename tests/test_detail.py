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
    print("detail OK")

if __name__ == "__main__": main()
