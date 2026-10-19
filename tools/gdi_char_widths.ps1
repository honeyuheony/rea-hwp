# Windows GDI로 글자 전진 폭을 측정한다(build_eq_glyph_metrics.py가 부른다).
# 쌍 "x h": 글꼴 높이 -h 픽셀(em 높이), 폭 x/2 픽셀. x가 홀수면 폭 0(기본 비율). 출력은 쌍마다 "x h:폭 폭 ..." 한 줄.
param([string]$FontFile, [string]$FaceFile, [string]$Codes, [string]$Pairs, [string]$Out)
Add-Type @"
using System; using System.IO; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public static class GdiWidths {
  [DllImport("gdi32.dll", CharSet=CharSet.Unicode)] static extern IntPtr CreateFontW(int h, int w, int e, int o, int wt, uint i, uint u, uint s, uint cs, uint op, uint cp, uint q, uint pf, string face);
  [DllImport("gdi32.dll")] static extern IntPtr CreateCompatibleDC(IntPtr dc);
  [DllImport("gdi32.dll")] static extern IntPtr SelectObject(IntPtr dc, IntPtr o);
  [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr o);
  [DllImport("gdi32.dll", CharSet=CharSet.Unicode)] static extern int GetTextFaceW(IntPtr dc, int n, StringBuilder s);
  [DllImport("gdi32.dll", CharSet=CharSet.Unicode)] static extern bool GetCharWidth32W(IntPtr dc, uint first, uint last, int[] widths);
  [DllImport("gdi32.dll", CharSet=CharSet.Unicode)] public static extern int AddFontResourceExW(string file, uint flags, IntPtr reserved);
  public static void Run(string face, string codesPath, string pairsPath, string outPath) {
    var codes = new List<uint>();
    foreach (var l in File.ReadAllLines(codesPath)) if (l.Trim().Length > 0) codes.Add(Convert.ToUInt32(l.Trim(), 16));
    IntPtr dc = CreateCompatibleDC(IntPtr.Zero);
    var sb = new StringBuilder();
    foreach (var l in File.ReadAllLines(pairsPath)) {
      if (l.Trim().Length == 0) continue;
      var p = l.Trim().Split(' '); int x = int.Parse(p[0]), h = int.Parse(p[1]);
      IntPtr font = CreateFontW(-h, x % 2 == 0 ? x / 2 : 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 0, 0, face);
      IntPtr old = SelectObject(dc, font);
      var got = new StringBuilder(64); GetTextFaceW(dc, 64, got);
      if (!string.Equals(got.ToString(), face, StringComparison.OrdinalIgnoreCase)) throw new Exception("GDI selected '" + got + "' instead of '" + face + "'");
      var row = new int[codes.Count]; int i = 0;
      while (i < codes.Count) {
        int j = i; while (j + 1 < codes.Count && codes[j + 1] == codes[j] + 1) j++;
        var w = new int[j - i + 1];
        if (!GetCharWidth32W(dc, codes[i], codes[j], w)) throw new Exception("GetCharWidth32W failed");
        Array.Copy(w, 0, row, i, w.Length); i = j + 1;
      }
      SelectObject(dc, old); DeleteObject(font);
      sb.Append(x).Append(' ').Append(h).Append(':').Append(string.Join(" ", row)).Append('\n');
    }
    File.WriteAllText(outPath, sb.ToString());
  }
}
"@
$ErrorActionPreference = "Stop"
# 0x10 = FR_PRIVATE: 설치하지 않은 한글 글꼴(HBATANG.TTF)도 이 프로세스에서만 쓸 수 있게 올린다
if ([GdiWidths]::AddFontResourceExW($FontFile, 0x10, [IntPtr]::Zero) -eq 0) { throw "AddFontResourceExW failed: $FontFile" }
$face = [IO.File]::ReadAllText($FaceFile, [Text.Encoding]::UTF8).Trim()
[GdiWidths]::Run($face, $Codes, $Pairs, $Out)
