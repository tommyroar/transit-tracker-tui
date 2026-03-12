import sys
import pytest
from unittest.mock import patch, MagicMock
from transit_tracker.cli import main

def test_cli_main_ui_launch():
    """Verifies that running with no args (default 'ui') attempts to launch the GUI and TUI."""
    # Mock everything that main calls
    with patch("transit_tracker.cli.get_last_config_path", return_value=None), \
         patch("transit_tracker.cli.TransitConfig.load") as mock_load, \
         patch("transit_tracker.cli.start_gui_if_needed") as mock_gui, \
         patch("transit_tracker.cli.run_cli") as mock_tui, \
         patch("argparse.ArgumentParser.parse_args") as mock_args:
        
        # Setup mock args for 'ui'
        mock_args.return_value = MagicMock(command="ui")
        
        # Run main
        main()
        
        # Assertions
        mock_load.assert_called()
        mock_gui.assert_called_once()
        mock_tui.assert_called_once()

def test_cli_main_simulator_launch():
    """Verifies that 'simulator' command works without UnboundLocalError."""
    with patch("transit_tracker.cli.get_last_config_path", return_value=None), \
         patch("transit_tracker.cli.TransitConfig.load") as mock_load, \
         patch("transit_tracker.cli.start_gui_if_needed"), \
         patch("transit_tracker.cli.run_cli"), \
         patch("transit_tracker.simulator.run_simulator") as mock_sim, \
         patch("argparse.ArgumentParser.parse_args") as mock_args:
        
        mock_args.return_value = MagicMock(command="simulator")
        
        # This is where the UnboundLocalError was happening
        main()
        
        mock_sim.assert_called_once()

def test_cli_main_gui_command_skips_autolaunch():
    """Verifies that 'gui' command doesn't trigger start_gui_if_needed (avoiding recursion)."""
    with patch("transit_tracker.cli.get_last_config_path", return_value=None), \
         patch("transit_tracker.cli.TransitConfig.load"), \
         patch("transit_tracker.cli.start_gui_if_needed") as mock_gui, \
         patch("transit_tracker.gui.main") as mock_gui_main, \
         patch("argparse.ArgumentParser.parse_args") as mock_args:
        
        mock_args.return_value = MagicMock(command="gui")
        
        main()
        
        # Should NOT call auto-launch if we are manually starting gui
        mock_gui.assert_not_called()
        mock_gui_main.assert_called_once()
