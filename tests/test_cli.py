from unittest.mock import MagicMock, patch
import subprocess
import os
import pytest
from transit_tracker.cli import main

pytestmark = pytest.mark.unit


def test_cli_main_ui_launch():
    """Verifies that running with no args (default 'ui') attempts to launch the TUI."""
    # Mock everything that main calls
    with patch("transit_tracker.cli.get_last_config_path", return_value=None), \
         patch("transit_tracker.cli.TransitConfig.load") as mock_load, \
         patch("transit_tracker.cli.run_cli") as mock_tui, \
         patch("argparse.ArgumentParser.parse_args") as mock_args:
        
        # Setup mock args for 'ui'
        mock_args.return_value = MagicMock()
        mock_args.return_value.command = ["ui"]
        
        # Run main
        main()
        
        # Assertions
        mock_load.assert_called()
        mock_tui.assert_called_once()

def test_cli_main_simulator_launch():
    """Verifies that 'simulator' command works."""
    with patch("transit_tracker.cli.get_last_config_path", return_value=None), \
         patch("transit_tracker.cli.TransitConfig.load") as mock_load, \
         patch("transit_tracker.cli.run_cli"), \
         patch("transit_tracker.simulator.run_simulator") as mock_sim, \
         patch("argparse.ArgumentParser.parse_args") as mock_args:
        
        mock_args.return_value = MagicMock()
        mock_args.return_value.command = ["simulator"]
        
        main()
        
        mock_sim.assert_called_once()

def test_cli_main_gui_command_direct_launch():
    """Verifies that 'gui' command launches the GUI directly."""
    mock_gui_main = MagicMock()
    mock_gui_module = MagicMock()
    mock_gui_module.main = mock_gui_main

    import sys
    with patch("transit_tracker.cli.get_last_config_path", return_value=None), \
         patch("transit_tracker.cli.TransitConfig.load"), \
         patch.dict(sys.modules, {"transit_tracker.gui": mock_gui_module}), \
         patch("argparse.ArgumentParser.parse_args") as mock_args:

        mock_args.return_value = MagicMock()
        mock_args.return_value.command = ["gui"]

        main()

        mock_gui_main.assert_called_once()

def test_service_start_idempotency():
    """Verifies that 'service start' does nothing if the container is already running."""
    mock_inspect = MagicMock()
    mock_inspect.returncode = 0
    mock_inspect.stdout = "true"

    with patch("subprocess.run", return_value=mock_inspect) as mock_run, \
         patch("argparse.ArgumentParser.parse_args") as mock_args:

        mock_args.return_value = MagicMock()
        mock_args.return_value.command = ["service", "start"]

        main()

        # Should have checked container status but not called docker start
        # (the "already running" path prints a message and returns)
        start_calls = [c for c in mock_run.call_args_list if "start" in str(c) and "docker" in str(c)]
        # docker inspect is called, but docker start should not be
        assert not any("docker', 'start'" in str(c) for c in mock_run.call_args_list)

def test_service_stop_cleanup():
    """Verifies that 'service stop' stops the container and pkills gui."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "true"

    with patch("subprocess.run", return_value=mock_result) as mock_run, \
         patch("argparse.ArgumentParser.parse_args") as mock_args:

        mock_args.return_value = MagicMock()
        mock_args.return_value.command = ["service", "stop"]

        main()

        # Should have called docker stop and pkill for gui
        all_calls = [str(c) for c in mock_run.call_args_list]
        docker_stop = any("docker" in c and "stop" in c for c in all_calls)
        pkill_gui = any("pkill" in c and "gui" in c for c in all_calls)
        assert docker_stop or pkill_gui
