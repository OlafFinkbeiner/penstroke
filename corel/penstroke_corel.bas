Attribute VB_Name = "PenstrokeRoundtrip"
' Penstroke <-> CorelDRAW round-trip macros.
'
' PenstrokeImport:
'   Reads an edit CSV (written by `penstroke export-corel`) and builds
'   a fresh document: one page per glyph (all pages canvas-size, named
'   after the glyph), the original letterform as a locked pale-yellow
'   underlay, and every pen stroke as a named curve "s01", "s02", ...
'   Edit freely: reshape nodes, delete strokes, draw new ones. The
'   stroke ORDER for the animation is the object NAME - rename to
'   reorder, name new strokes "s05" etc. Colors don't matter.
'
' PenstrokeExportEdits:
'   Walks every page, samples every curve named "s*" and writes the
'   same CSV format back. Feed that to `penstroke import-corel`.
'
' Install: Tools > Macros > Macro Editor, File > Import File... this
' .bas - or just Tools > Macros > Run Macro after opening it.
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
    Dim lyr As Layer
    Dim underLyr As Layer

    ' Geometry accumulators: we buffer points per (page, kind, index)
    ' and flush a curve whenever the key changes (records are written
    ' grouped, so a simple previous-key check suffices).
    Dim prevKey As String: prevKey = ""
    Dim bufX() As Double, bufY() As Double, bufN As Long
    ReDim bufX(0 To 100000): ReDim bufY(0 To 100000)
    bufN = 0
    Dim prevKind As String, prevIdx As Long, prevPage As Integer

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
            FlushBuffer doc, prevPage, prevKind, prevIdx, bufX, bufY, bufN, canvasH
            prevKey = ""
            Dim pageIdx As Integer: pageIdx = CInt(parts(1))
            If curPage < 0 Then
                Set pg = doc.Pages(1)
            Else
                Set pg = doc.AddPages(1)
            End If
            curPage = pageIdx
            pg.SizeWidth = canvasW
            pg.SizeHeight = canvasH
            pg.Name = parts(3)
            pg.Activate

        Case "U", "S"
            Dim key As String
            key = parts(0) & "|" & parts(1) & "|" & parts(2)
            If key <> prevKey And prevKey <> "" Then
                FlushBuffer doc, prevPage, prevKind, prevIdx, bufX, bufY, bufN, canvasH
            End If
            If key <> prevKey Then
                prevKey = key
                prevKind = parts(0)
                prevPage = CInt(parts(1))
                prevIdx = CLng(parts(2))
                bufN = 0
            End If
            bufX(bufN) = Val(parts(3))
            bufY(bufN) = Val(parts(4))
            bufN = bufN + 1
        End Select
NextLine:
    Loop
    FlushBuffer doc, prevPage, prevKind, prevIdx, bufX, bufY, bufN, canvasH
    Close #fnum

    doc.Pages(1).Activate
    MsgBox "Penstroke import done: " & doc.Pages.Count & " glyph pages." _
           & vbCrLf & "Strokes are named s01, s02, ... (name = draw order)." _
           , vbInformation, "Penstroke"
End Sub


Private Sub FlushBuffer(doc As Document, pageIdx As Integer, _
                        kind As String, idx As Long, _
                        bufX() As Double, bufY() As Double, _
                        ByRef bufN As Long, canvasH As Double)
    If bufN < 2 Then bufN = 0: Exit Sub

    Dim crv As Curve
    Set crv = doc.CreateCurve
    Dim sp As SubPath
    ' Flip y: CSV is y-down, Corel pages are y-up.
    Set sp = crv.CreateSubPath(bufX(0), canvasH - bufY(0))
    Dim i As Long
    For i = 1 To bufN - 1
        sp.AppendLineSegment bufX(i), canvasH - bufY(i)
    Next i

    Dim sh As Shape
    Set sh = doc.ActiveLayer.CreateCurve(crv)

    If kind = "U" Then
        sp.Closed = True
        sh.Name = UNDERLAY_NAME
        sh.Fill.UniformColor.RGBAssign 253, 230, 138
        sh.Outline.SetNoOutline
        sh.OrderToBack
        sh.Locked = True
    Else
        sh.Name = "s" & Format$(idx + 1, "00")
        sh.Fill.ApplyNoFill
        sh.Outline.SetProperties 2#
        sh.Outline.Color.RGBAssign 30, 30, 30
    End If
    bufN = 0
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
    ' Python side resolve the char from the safe name via its manifest.
    Print #fnum, "H;penstroke-edit;1;" & doc.Title & ";" & _
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
