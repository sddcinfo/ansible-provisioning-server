import unittest
import requests
import os
import json
import tempfile
import subprocess

class TestProvisioningServerSudoLess(unittest.TestCase):
    BASE_URL = "http://localhost"
    TEST_MAC = "0a:0b:0c:0d:0e:0f"
    PRESERVED_MAC = "aa:bb:cc:dd:ee:ff" # An entry that should not be deleted
    
    def setUp(self):
        """Create a temporary state file for each test."""
        self.temp_file = tempfile.NamedTemporaryFile(delete=False, mode='w', prefix='test_state_', suffix='.json')
        self.temp_file_path = self.temp_file.name
        self.test_state_file_name = f"/var/www/html/sessions/{os.path.basename(self.temp_file_path)}"

        # Create a more complex initial state with multiple entries
        initial_state = {
            self.TEST_MAC: {"status": "DONE", "timestamp": "2025-01-01T12:00:00Z"},
            self.PRESERVED_MAC: {"status": "DONE", "timestamp": "2025-01-01T12:00:00Z"}
        }
        json.dump(initial_state, self.temp_file)
        self.temp_file.close()

        subprocess.run(["sudo", "mv", self.temp_file_path, self.test_state_file_name], check=True)
        subprocess.run(["sudo", "chmod", "666", self.test_state_file_name], check=True)

    def tearDown(self):
        """Clean up the temporary state file."""
        subprocess.run(["sudo", "rm", "-f", self.test_state_file_name], check=True)

    def _get_url(self, path, params=None):
        """Helper to construct URLs with the test state file parameter."""
        if params is None:
            params = {}
        params['test_state_file'] = os.path.basename(self.test_state_file_name)
        query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        return f"{self.BASE_URL}{path}?{query_string}"

    def test_status_page_loads(self):
        """Test that the status page loads correctly and shows test data."""
        url = self._get_url('/')
        response = requests.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("<title>Provisioning Status</title>", response.text)
        self.assertIn(self.TEST_MAC, response.text)
        self.assertIn(self.PRESERVED_MAC, response.text)

    def test_ipxe_boot_script(self):
        """Test iPXE boot script generation using the temp state file."""
        url = self._get_url('/', {'mac': self.TEST_MAC})
        response = requests.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("#!ipxe", response.text)

    def test_reprovision_workflow(self):
        """Test the full reprovisioning workflow."""
        url = self._get_url('/', {'action': 'reprovision', 'mac': self.TEST_MAC})
        response = requests.get(url, allow_redirects=True)
        self.assertEqual(response.status_code, 200)

        with open(self.test_state_file_name, 'r') as f:
            state_data = json.load(f)
        self.assertEqual(state_data[self.TEST_MAC]['status'], 'NEW')
        self.assertIn(self.PRESERVED_MAC, state_data) # Ensure other entries are untouched

    def test_delete_workflow(self):
        """Test that the delete action removes an entry and leaves others."""
        url = self._get_url('/', {'action': 'delete', 'mac': self.TEST_MAC})
        response = requests.get(url, allow_redirects=True)
        self.assertEqual(response.status_code, 200)

        with open(self.test_state_file_name, 'r') as f:
            state_data = json.load(f)
        
        self.assertNotIn(self.TEST_MAC, state_data, "Deleted MAC should not be in the state file")
        self.assertIn(self.PRESERVED_MAC, state_data, "Preserved MAC should still exist in the state file")

    def test_callback_updates_status(self):
        """Test the callback action using the temp state file."""
        url = self._get_url('/', {'action': 'callback', 'mac': self.TEST_MAC, 'status': 'FAILED'})
        response = requests.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("OK", response.text)

        with open(self.test_state_file_name, 'r') as f:
            state_data = json.load(f)
        self.assertEqual(state_data[self.TEST_MAC]['status'], 'FAILED')

    def test_invalid_mac_request(self):
        """Test that an invalid MAC address returns a 400 error."""
        url = self._get_url('/', {'mac': 'invalid-mac'})
        response = requests.get(url)
        self.assertEqual(response.status_code, 400)

if __name__ == '__main__':
    unittest.main()