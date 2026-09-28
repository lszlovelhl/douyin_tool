' 抖音数据监控 - Windows 一键启动（隐藏控制台版）
' 双击此文件即可启动服务并打开浏览器，不会显示黑色命令行窗口

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 获取脚本所在目录
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' 切换到项目目录并运行批处理脚本（隐藏窗口）
WshShell.CurrentDirectory = scriptDir
WshShell.Run "cmd /c """ & scriptDir & "\start.bat""", 0, False
