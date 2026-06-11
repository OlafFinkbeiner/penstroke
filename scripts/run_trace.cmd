@echo off
rem PDG job launcher for the trace stage. Jobs spawned by Houdini's
rem local scheduler inherit PYTHONPATH/PYTHONHOME pointing at
rem Houdini's own Python; the venv interpreter would import the wrong
rem stdlib (symptom: "SRE module mismatch"). Scrub before launching.
set PYTHONPATH=
set PYTHONHOME=
set PYTHONIOENCODING=utf-8
"%~dp0..\.venv\Scripts\python.exe" -m penstroke trace %1 %2 --charset %3 --name %4 --quiet
exit /b %ERRORLEVEL%
