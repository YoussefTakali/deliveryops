"""Automatisations: recorded/described repetitive tasks compiled into workflows of semantic steps.

The frontend/desktop runner executes a workflow (template rendering, foreach, when, on_error); the
backend stores workflows/runs/files, computes schedules, executes the *server* steps (steps.py), and
turns a description or a screen recording into a workflow with the LLM (compiler.py, recording.py).
"""
