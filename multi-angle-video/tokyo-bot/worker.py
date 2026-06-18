#!/usr/bin/env python3
"""Local convenience entrypoint for render-worker mode.

The Railway WORKER service does NOT use this file — it runs the same image with the normal
start command (`python slack_bot.py`) plus env var RENDER_WORKER_MODE=1, and slack_bot.py
branches into the render-queue worker loop (_run_render_worker). This shim just lets you run
the worker locally: `python worker.py`.
"""
import os
import runpy

os.environ["RENDER_WORKER_MODE"] = "1"
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "slack_bot.py"),
               run_name="__main__")
