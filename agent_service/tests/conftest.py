"""Pytest configuration for agent_service tests."""

import sys
from pathlib import Path

# Add parent directory to path so we can import agent_service modules
agent_service_dir = Path(__file__).parent.parent
if str(agent_service_dir) not in sys.path:
    sys.path.insert(0, str(agent_service_dir))
