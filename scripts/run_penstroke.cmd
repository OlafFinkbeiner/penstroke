@echo off
rem Generic PDG job launcher: run any `penstroke` subcommand in the
rem project venv. Jobs spawned by Houdini's local scheduler inherit
rem PYTHONPATH/PYTHONHOME pointing at Houdini's own Python; the venv
rem interpreter would import the wrong stdlib (symptom: "SRE module
rem mismatch"). Scrub before launching. (run_trace.cmd is the
rem trace-stage sibling with its arguments baked.)
set PYTHONPATH=
set PYTHONHOME=
set PYTHONIOENCODING=utf-8
"%~dp0..\.venv\Scripts\python.exe" -m penstroke %*
exit /b %ERRORLEVEL%
