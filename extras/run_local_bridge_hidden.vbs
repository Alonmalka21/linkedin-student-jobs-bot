' Silent launcher for the local bridge: wscript is a GUI host, so no console
' window is ever allocated (fixes the brief flash of a direct powershell task).
' Window style 0 = hidden; False = don't wait (task returns immediately).
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & dir & "\run_local_bridge.ps1""", 0, False
