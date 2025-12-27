Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\DocSort"
WshShell.Run """" & "C:\DocSort\.venv\Scripts\python.exe" & """" & " -m docsort.tools.ocr_tray", 0
Set WshShell = Nothing
