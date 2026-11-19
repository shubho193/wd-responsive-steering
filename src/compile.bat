@echo off
rem Compile hand-edited src\xml\*.xml into the mod's .lib files.
rem usage: compile.bat "<Gibbed Tools folder>"
setlocal
if "%~1"=="" (echo usage: compile.bat "^<Gibbed Tools folder^>" & exit /b 1)
set "CONV=%~1\Gibbed.Disrupt.ConvertBinaryObject.exe"
set "OUT=%~dp0..\mod\responsive_steering\workspace\generated\databases\generic"
if not exist "%OUT%" mkdir "%OUT%"
for %%F in ("%~dp0xml\*.xml") do (
    echo %%~nF
    "%CONV%" -i "%%F" "%OUT%\%%~nF.lib" || (echo FAILED: %%~nxF & exit /b 1)
)
echo Done.
