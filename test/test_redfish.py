import unittest
from unittest.mock import patch, MagicMock, mock_open
import json
import io
import sys
import os
import argparse

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import redfish

# --- Mock Data ---
MOCK_THERMAL_DATA = {
    "Temperatures": [
        {"Name": "CPU Temp", "ReadingCelsius": 50, "Status": {"Health": "OK"}},
        {"Name": "System Temp", "ReadingCelsius": 30, "Status": {"Health": "OK"}}
    ],
    "Fans": [
        {"FanName": "FAN 1", "Reading": 4000, "ReadingUnits": "RPM", "Status": {"Health": "OK"}},
        {"FanName": "FAN 2", "Reading": 4100, "ReadingUnits": "RPM", "Status": {"Health": "OK"}}
    ]
}

MOCK_SYSTEM_DATA = {
    "Manufacturer": "TestCorp",
    "Model": "TestModel-123",
    "SerialNumber": "TESTSN12345",
    "PowerState": "On",
    "Status": {"Health": "OK"}
}

class TestRedfishScript(unittest.TestCase):

    def setUp(self):
        """Set up a fresh parser for each test."""
        self.parser = redfish.create_parser()

    # --- Argument Parsing Tests ---
    def test_parse_simple_action(self):
        args = self.parser.parse_args(['console-node1', 'status'])
        self.assertEqual(args.nodes, 'console-node1')
        self.assertEqual(args.action, 'status')

    def test_parse_multiple_nodes(self):
        args = self.parser.parse_args(['node1,node2', 'reboot'])
        self.assertEqual(args.nodes, 'node1,node2')

    def test_parse_sensors_all_filters(self):
        """Test parsing 'sensors' with all possible filters to prevent regression."""
        args = self.parser.parse_args(['node1', 'sensors', '--type', 'temperature', '--name', 'CPU', '--format', 'csv'])
        self.assertEqual(args.action, 'sensors')
        self.assertEqual(args.type, 'temperature')
        self.assertEqual(args.name, 'CPU')
        self.assertEqual(args.format, 'csv')

    def test_parse_inventory_with_format(self):
        args = self.parser.parse_args(['node1', 'inventory', '--resource', 'memory', '--format', 'json'])
        self.assertEqual(args.action, 'inventory')
        self.assertEqual(args.resource, 'memory')
        self.assertEqual(args.format, 'json')

    def test_invalid_action_fails(self):
        with self.assertRaises(SystemExit):
            with patch('sys.stderr', new_callable=io.StringIO):
                self.parser.parse_args(['node1', 'invalid_action'])

    def test_inventory_requires_resource(self):
        with self.assertRaises(SystemExit):
            with patch('sys.stderr', new_callable=io.StringIO):
                self.parser.parse_args(['node1', 'inventory'])

    # --- Formatting Tests ---
    def test_format_csv_sensors(self):
        result = redfish.format_as_csv(MOCK_THERMAL_DATA, 'sensors')
        self.assertIn('SensorType,Name,Reading,Units,Status', result)
        self.assertIn('Temperature,CPU Temp,50,C,OK', result)

    def test_format_human_readable_system(self):
        result = redfish.format_as_human_readable(MOCK_SYSTEM_DATA, 'system')
        self.assertIn("Manufacturer: TestCorp", result)
        self.assertIn("Model: TestModel-123", result)

    # --- Execution Logic Tests (with Mocking) ---
    @patch('redfish.get_redfish_credentials', return_value='dGVzdDpwYXNz')
    @patch('redfish.request.urlopen')
    def test_execute_status_action(self, mock_urlopen, mock_creds):
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = json.dumps(MOCK_SYSTEM_DATA).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with patch('sys.stdout', new_callable=io.StringIO) as mock_stdout:
            args = self.parser.parse_args(['console-node1', 'status'])
            redfish.execute_redfish_command('console-node1', args)
            self.assertIn("console-node1: Power=On, Health=OK", mock_stdout.getvalue())

    @patch('redfish.get_redfish_credentials', return_value='dGVzdDpwYXNz')
    @patch('redfish.request.urlopen')
    def test_build_reboot_request(self, mock_urlopen, mock_creds):
        args = self.parser.parse_args(['console-node1', 'reboot'])
        req, _ = redfish.build_request('console-node1', args)
        
        self.assertIsNotNone(req)
        self.assertEqual(req.full_url, 'https://10.10.1.11/redfish/v1/Systems/1/Actions/ComputerSystem.Reset')
        self.assertEqual(req.method, 'POST')
        self.assertEqual(json.loads(req.data), {"ResetType": "ForceRestart"})

    def test_get_node_ip(self):
        # This test relies on the CONSOLE_NODES data injected by Ansible
        self.assertEqual(redfish.get_node_ip('console-node1'), '10.10.1.11')
        self.assertIsNone(redfish.get_node_ip('nonexistent-node'))

if __name__ == '__main__':
    # This allows the test to be run directly
    unittest.main(verbosity=2)