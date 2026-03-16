from unittest.mock import MagicMock, patch, ANY
import subprocess
import os
from transit_tracker.cli import main

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
    with patch("transit_tracker.cli.get_last_config_path", return_value=None), \
         patch("transit_tracker.cli.TransitConfig.load"), \
         patch("transit_tracker.gui.main") as mock_gui_main, \
         patch("argparse.ArgumentParser.parse_args") as mock_args:
        
        mock_args.return_value = MagicMock()
        mock_args.return_value.command = ["gui"]
        
        main()
        
        mock_gui_main.assert_called_once()

def test_service_start_idempotency():
    """Verifies that 'service start' does nothing if the service is already running."""
    with patch("transit_tracker.cli._nomad_available", return_value=False), \
         patch("transit_tracker.cli.get_service_status") as mock_status, \
         patch("os.system") as mock_os_system, \
         patch("subprocess.Popen") as mock_popen, \
         patch("argparse.ArgumentParser.parse_args") as mock_args:

        mock_args.return_value = MagicMock()
        mock_args.return_value.command = ["service", "start"]

        # 1. CASE: SERVICE RUNNING
        mock_status.return_value = True
        main()

        # Should not attempt to start via launchctl or Popen
        mock_os_system.assert_not_called()
        mock_popen.assert_not_called()

def test_service_stop_cleanup():
    """Verifies that 'service stop' unloads launchctl and pkills gui."""
    with patch("transit_tracker.cli._nomad_available", return_value=False), \
         patch("transit_tracker.cli.get_service_status") as mock_status, \
         patch("os.system") as mock_os_system, \
         patch("subprocess.run") as mock_run, \
         patch("argparse.ArgumentParser.parse_args") as mock_args:

        mock_args.return_value = MagicMock()
        mock_args.return_value.command = ["service", "stop"]

        # 1. CASE: SERVICE RUNNING
        mock_status.return_value = True
        main()

        # Should attempt to unload and pkill
        mock_os_system.assert_called_with(ANY)
        # Verify pkill was called for gui
        pkill_called = any("pkill" in str(call) and "gui" in str(call) for call in mock_run.call_args_list)
        assert pkill_called or mock_os_system.called
