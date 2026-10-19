# 함초롬바탕(HCR Batang)으로 단독 상위 대리 U+D840의 (폭, tmHeight + tmInternalLeading)을 쌍마다 측정한다.
# HncBaseDraw는 D840~D87F를 한자로 분류해 한자 기본 글꼴로 측정하고 높이는 tmHeight + tmInternalLeading이다
# (FUN_1006ed20·HncDRGetCharLang, FUN_1005c030). 쌍 "x h": 글꼴 높이 +h(셀 높이), 폭 x/2(x가 홀수면 0). 출력 "x h:폭 높이".
param([string]$Pairs, [string]$Out)
Add-Type @"
using System; using System.IO; using System.Text; using System.Runtime.InteropServices;
public static class SurrogateBox {
  [DllImport("gdi32.dll", CharSet=CharSet.Unicode)] static extern IntPtr CreateFontW(int h, int w, int e, int o, int wt, uint i, uint u, uint s, uint cs, uint op, uint cp, uint q, uint pf, string face);
  [DllImport("gdi32.dll")] static extern IntPtr CreateCompatibleDC(IntPtr dc);
  [DllImport("gdi32.dll")] static extern IntPtr SelectObject(IntPtr dc, IntPtr o);
  [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr o);
  [DllImport("gdi32.dll", CharSet=CharSet.Unicode)] static extern int GetTextFaceW(IntPtr dc, int n, StringBuilder s);
  [StructLayout(LayoutKind.Sequential)] struct SZ { public int cx; public int cy; }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct TM { public int tmHeight, tmAscent, tmDescent, tmInternalLeading, tmExternalLeading, tmAveCharWidth, tmMaxCharWidth, tmWeight, tmOverhang, tmDigitizedAspectX, tmDigitizedAspectY; public char a, b, c, d; public byte i, u, s, p, cs; }
  [DllImport("gdi32.dll", CharSet=CharSet.Unicode)] static extern bool GetTextMetricsW(IntPtr dc, out TM tm);
  [DllImport("gdi32.dll", CharSet=CharSet.Unicode)] static extern bool GetTextExtentPoint32W(IntPtr dc, string s, int n, out SZ sz);
  public static void Run(string pairsPath, string outPath) {
    IntPtr dc = CreateCompatibleDC(IntPtr.Zero);
    var sb = new StringBuilder();
    foreach (var l in File.ReadAllLines(pairsPath)) {
      if (l.Trim().Length == 0) continue;
      var p = l.Trim().Split(' '); int x = int.Parse(p[0]), h = int.Parse(p[1]);
      IntPtr font = CreateFontW(h, x % 2 == 0 ? x / 2 : 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 0, 0, "HCR Batang");
      IntPtr old = SelectObject(dc, font);
      var got = new StringBuilder(64); GetTextFaceW(dc, 64, got);
      // 한국어 Windows에서는 지역화된 이름(함초롬바탕)을 돌려준다
      if (got.ToString() != "HCR Batang" && got.ToString() != "\uD568\uCD08\uB86C\uBC14\uD0D5") throw new Exception("GDI selected '" + got + "' instead of HCR Batang");
      TM tm; GetTextMetricsW(dc, out tm);
      SZ sz; GetTextExtentPoint32W(dc, "\uD840", 1, out sz);
      SelectObject(dc, old); DeleteObject(font);
      sb.Append(x).Append(' ').Append(h).Append(':').Append(sz.cx).Append(' ').Append(tm.tmHeight + tm.tmInternalLeading).Append('\n');
    }
    File.WriteAllText(outPath, sb.ToString());
  }
}
"@
$ErrorActionPreference = "Stop"
[SurrogateBox]::Run($Pairs, $Out)
