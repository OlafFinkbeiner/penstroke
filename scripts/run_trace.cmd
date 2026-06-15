@echo off
rem PDG job launcher for the trace stage. Jobs spawned by Houdini's
rem local scheduler inherit PYTHONPATH/PYTHONHOME pointing at
rem Houdini's own Python; the venv interpreter would import the wrong
rem stdlib (symptom: "SRE module mismatch"). Scrub before launching.
set PYTHONPATH=
set PYTHONHOME=
set PYTHONIOENCODING=utf-8
rem Forward ALL args verbatim with %* (NOT %1 %2 ...). cmd's positional
rem tokens split a quoted path on a comma, so variable-font filenames
rem like "Foo[wdth,wght].ttf" break --charset/--name; %* is the raw,
rem untokenized tail and keeps the comma inside the quotes intact. The
rem caller bakes the ttf, dir, --charset and --name into the command.
"%~dp0..\.venv\Scripts\python.exe" -m penstroke trace %* --quiet
exit /b %ERRORLEVEL%
