@echo off
setlocal

rem Build the renamed ICR2 Animator launcher as a single-window executable.
rem Keep the script name in sync with the repository entry point.
pyinstaller --noconfirm --windowed --onefile icr2_animator.py --name "icr2_animator" --paths .
