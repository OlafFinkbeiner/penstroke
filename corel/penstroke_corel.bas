Attribute VB_Name = "PenstrokeRoundtrip"
' Penstroke <-> CorelDRAW round-trip macros (CSV format v2).
'
' PenstrokeImport:
'   Reads an edit CSV (written by `penstroke export-corel`) and builds
'   a fresh document: one page per glyph (all pages canvas-size, named
'   after the glyph), the original font OUTLINE as a locked pale-yellow
'   underlay (one combined shape per page, so counters are holes), and
'   every pen stroke as a smooth Bezier curve named "s01", "s02", ...
'   Strokes arrive FITTED: a straight stem is 2 nodes, letters 2-6
'   segments. Edit freely. The stroke ORDER for the animation is the
'   object NAME - rename to reorder, name new strokes "s05" etc.
'
' PenstrokeExportEdits:
'   Walks every page, samples every curve named "s*" and writes point
'   records back. Feed that CSV to `penstroke import-corel`.
'
' Install: Tools > Macros > Macro Editor, File > Import File... this
' .bas - then Tools > Macros > Run Macro.
'
' Coordinates: the CSV stores pixels with y pointing DOWN; Corel pages
' have y pointing UP, so both macros flip y. 1 pixel = 1 point.

Option Explicit

Private Const UNDERLAY_NAME As String = "underlay"
Private Const SAMPLE_STEP As Double = 3#   ' pt spacing when exporting curves


' ---------------------------------------------------------------------
Public Sub PenstrokeImport()
    Dim csvPath As String
    csvPath = GetFilePath("Select penstroke edit CSV")
    If Len(csvPath) = 0 Then Exit Sub

    Dim fnum As Integer: fnum = FreeFile
    Open csvPath For Input As #fnum

    Dim doc As Document
    Set doc = CreateDocument
    doc.Unit = cdrPoint

    Dim canvasW As Double, canvasH As Double
    Dim curPage As Integer: curPage = -1
    Dim pg As Page

    ' Stroke curve being assembled (one curve per stroke).
    Dim sCrv As Curve, sSp As SubPath
    Dim sKey As String: sKey = ""
    Dim sIdx As Long
    ' Underlay curve being assembled (ONE curve per page, one closed
    ' subpath per outline contour -> counters become holes).
    Dim uCrv As Curve, uSp As SubPath
    Dim uPolyIdx As Long: uPolyIdx = -1
    Dim haveU As Boolean: haveU = False

    Dim line As String
    Do While Not EOF(fnum)
        Line Input #fnum, line
        line = Trim$(line)
        If Len(line) = 0 Then GoTo NextLine
        Dim parts() As String
        parts = Split(line, ";")

        Select Case parts(0)
        Case "H"
            canvasW = Val(parts(4))
            canvasH = Val(parts(5))
            doc.MasterPage.SizeWidth = canvasW
            doc.MasterPage.SizeHeight = canvasH

        Case "G"
            FlushStroke doc, sCrv, sIdx: sKey = ""
            FlushUnderlay doc, uCrv, uSp, haveU: uPolyIdx = -1
            If curPage < 0 Then
                Set pg = doc.Pages(1)
            Else
                Set pg = doc.AddPages(1)
            End If
            curPage = CInt(parts(1))
            pg.SizeWidth = canvasW
            pg.SizeHeight = canvasH
            pg.Name = parts(3)
            pg.Activate

        Case "B"
            ' B;page;kind;idx;x0;y0;c1x;c1y;c2x;c2y;x1;y1
            Dim kind As String: kind = parts(2)
            Dim idx As Long: idx = CLng(parts(3))
            Dim x0 As Double, y0 As Double
            Dim c1x As Double, c1y As Double
            Dim c2x As Double, c2y As Double
            Dim x1 As Double, y1 As Double
            x0 = Val(parts(4)): y0 = canvasH - Val(parts(5))
            c1x = Val(parts(6)): c1y = canvasH - Val(parts(7))
            c2x = Val(parts(8)): c2y = canvasH - Val(parts(9))
            x1 = Val(parts(10)): y1 = canvasH - Val(parts(11))

            If kind = "S" Then
                Dim key As String
                key = parts(1) & "|" & parts(3)
                If key <> sKey Then
                    FlushStroke doc, sCrv, sIdx
                    Set sCrv = doc.CreateCurve
                    Set sSp = sCrv.CreateSubPath(x0, y0)
                    sKey = key
                    sIdx = idx
                End If
                sSp.AppendCurveSegment2 x1, y1, c1x, c1y, c2x, c2y
            Else   ' "U"
                If Not haveU Then
                    Set uCrv = doc.CreateCurve
                    haveU = True
                    uPolyIdx = -1
                End If
                If idx <> uPolyIdx Then
                    If uPolyIdx >= 0 Then uSp.Closed = True
                    Set uSp = uCrv.CreateSubPath(x0, y0)
                    uPolyIdx = idx
                End If
                uSp.AppendCurveSegment2 x1, y1, c1x, c1y, c2x, c2y
            End If
        End Select
NextLine:
    Loop
    FlushStroke doc, sCrv, sIdx
    FlushUnderlay doc, uCrv, uSp, haveU
    Close #fnum

    doc.Pages(1).Activate
    MsgBox "Penstroke import done: " & doc.Pages.Count & " glyph pages." _
           & vbCrLf & "Strokes are named s01, s02, ... (name = draw order)." _
           , vbInformation, "Penstroke"
End Sub


Private Sub FlushStroke(doc As Document, ByRef sCrv As Curve, sIdx As Long)
    If sCrv Is Nothing Then Exit Sub
    Dim sh As Shape
    Set sh = doc.ActiveLayer.CreateCurve(sCrv)
    sh.Name = "s" & Format$(sIdx + 1, "00")
    sh.Fill.ApplyNoFill
    sh.Outline.SetProperties 2#
    sh.Outline.Color.RGBAssign 30, 30, 30
    Set sCrv = Nothing
End Sub


Private Sub FlushUnderlay(doc As Document, ByRef uCrv As Curve, _
                          ByRef uSp As SubPath, ByRef haveU As Boolean)
    If Not haveU Then Exit Sub
    If Not uSp Is Nothing Then uSp.Closed = True
    Dim sh As Shape
    Set sh = doc.ActiveLayer.CreateCurve(uCrv)
    sh.Name = UNDERLAY_NAME
    sh.Fill.UniformColor.RGBAssign 253, 230, 138
    sh.Outline.SetNoOutline
    sh.OrderToBack
    sh.Locked = True
    Set uCrv = Nothing
    Set uSp = Nothing
    haveU = False
End Sub


' ---------------------------------------------------------------------
Public Sub PenstrokeExportEdits()
    Dim doc As Document
    Set doc = ActiveDocument
    If doc Is Nothing Then
        MsgBox "No document open.", vbExclamation, "Penstroke"
        Exit Sub
    End If
    doc.Unit = cdrPoint

    Dim csvPath As String
    csvPath = GetSavePath("Save edited CSV as")
    If Len(csvPath) = 0 Then Exit Sub

    Dim canvasW As Double, canvasH As Double
    canvasW = doc.Pages(1).SizeWidth
    canvasH = doc.Pages(1).SizeHeight

    Dim fnum As Integer: fnum = FreeFile
    Open csvPath For Output As #fnum
    ' Glyph identity travels via the page NAME (set at import); the
    ' char hex is not known to Corel, so we write 0000 and let the
    ' Python side resolve the char from the page name.
    Print #fnum, "H;penstroke-edit;2;" & doc.Title & ";" & _
                 Format$(canvasW, "0") & ";" & Format$(canvasH, "0") & ";0"

    Dim pg As Page, sh As Shape
    Dim pageIdx As Integer: pageIdx = 0
    For Each pg In doc.Pages
        Print #fnum, "G;" & pageIdx & ";0000;" & pg.Name
        For Each sh In pg.Shapes
            If LCase$(Left$(sh.Name, 1)) = "s" And IsNumeric(Mid$(sh.Name, 2)) Then
                Dim sIdx As Long
                sIdx = CLng(Mid$(sh.Name, 2)) - 1
                WriteShapeSamples fnum, sh, pageIdx, sIdx, canvasH
            End If
        Next sh
        pageIdx = pageIdx + 1
    Next pg
    Close #fnum
    MsgBox "Exported " & pageIdx & " pages to " & csvPath, vbInformation, "Penstroke"
End Sub


Private Sub WriteShapeSamples(fnum As Integer, sh As Shape, _
                              pageIdx As Integer, sIdx As Long, _
                              canvasH As Double)
    On Error Resume Next
    Dim crv As Curve
    Set crv = sh.Curve
    If crv Is Nothing Then Exit Sub

    Dim sp As SubPath
    For Each sp In crv.SubPaths
        Dim total As Double
        total = sp.Length
        If total <= 0 Then GoTo NextSp
        Dim n As Long
        n = CLng(total / SAMPLE_STEP)
        If n < 8 Then n = 8
        Dim i As Long
        For i = 0 To n
            Dim x As Double, y As Double
            ' GetPointPositionAt walks the subpath by absolute offset.
            sp.GetPointPositionAt x, y, total * i / n, cdrAbsoluteSegmentOffset
            Print #fnum, "S;" & pageIdx & ";" & sIdx & ";" & _
                         Format$(x, "0.00") & ";" & Format$(canvasH - y, "0.00")
        Next i
NextSp:
    Next sp
End Sub


' ---------------------------------------------------------------------
Private Function GetFilePath(title As String) As String
    On Error GoTo Fallback
    GetFilePath = CorelScriptTools.GetFileBox( _
        "CSV files (*.csv)|*.csv|All files (*.*)|*.*", title, 0)
    Exit Function
Fallback:
    GetFilePath = InputBox(title & " - full path to CSV:", "Penstroke")
End Function

Private Function GetSavePath(title As String) As String
    On Error GoTo Fallback
    GetSavePath = CorelScriptTools.GetFileBox( _
        "CSV files (*.csv)|*.csv|All files (*.*)|*.*", title, 1)
    Exit Function
Fallback:
    GetSavePath = InputBox(title & " - full path for CSV:", "Penstroke")
End Function
