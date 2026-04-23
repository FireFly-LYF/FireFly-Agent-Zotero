"""
Entry point for running firefly as a module: python -m firefly
"""

from firefly.cli.commands import app

if __name__ == "__main__":
    app()
