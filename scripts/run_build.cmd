@echo off
rem PDG build-bundle launcher. The hfont rep builders need hou, so this
rem runs under HYTHON (Houdini's own python), NOT the project venv — and
rem therefore must NOT scrub PYTHONPATH the way run_trace.cmd /
rem run_penstroke.cmd do (hython relies on Houdini's paths for hou). The
rem local scheduler job inherits the Houdini session env ($HFS,
rem $PENSTROKE, PYTHONPATH); we just make sure penstroke is importable.
set PYTHONIOENCODING=utf-8
set PYTHONPATH=%PENSTROKE%\src;%PYTHONPATH%
"%HFS%\bin\hython.exe" -m penstroke build-bundles %*
exit /b %ERRORLEVEL%
