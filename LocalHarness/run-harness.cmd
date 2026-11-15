@echo off
REM ===========================================================================
REM run-harness.cmd -- one-command verification for the offline harness.
REM
REM Builds LocalHarness\*.vb with the .NET Framework compiler that ships with
REM Windows, runs every mail-metadata scenario in fixtures\scenarios.txt and
REM structurally validates Main.xaml.  Needs no UiPath Studio, no desktop
REM Outlook and no network access.
REM
REM Exit code 0 means the harness and the structural checks both passed.
REM ===========================================================================
setlocal
set REPO=%~dp0..
set VBC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\vbc.exe
if not exist "%VBC%" set VBC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\vbc.exe
if not exist "%VBC%" (
  echo ERROR: vbc.exe was not found. Install the .NET Framework 4.x.
  exit /b 2
)

set WORK=%REPO%\LocalHarness\build
if not exist "%WORK%" mkdir "%WORK%"

set PSH=%WINDIR%\System32\WindowsPowerShell\v1.0\powershell.exe
if not exist "%PSH%" (
  echo ERROR: powershell.exe was not found.
  exit /b 2
)

echo [1/3] Compiling the offline harness
"%VBC%" -nologo -out:"%WORK%\ScenarioRunner.exe" "%REPO%\LocalHarness\EmailArchiveFlow.vb" "%REPO%\LocalHarness\ScenarioRunner.vb"
if errorlevel 1 (
  echo ERROR: the harness did not compile.
  exit /b 1
)

echo [2/3] Running the mail-metadata scenarios
if exist "%WORK%\work" rmdir /s /q "%WORK%\work"
"%WORK%\ScenarioRunner.exe" "%REPO%\EmailTemplates" "%REPO%\LocalHarness\fixtures\scenarios.txt" "%WORK%\work"
if errorlevel 1 (
  echo ERROR: one or more scenarios failed.
  exit /b 1
)

echo [3/3] Validating Main.xaml structure and locked configuration
"%PSH%" -NoProfile -ExecutionPolicy Bypass -File "%REPO%\LocalHarness\validate-xaml.ps1"
if errorlevel 1 (
  echo ERROR: the XAML structural checks failed.
  exit /b 1
)

echo.
echo All checks passed.
echo Generated report bodies: %WORK%\work\_reports
exit /b 0
